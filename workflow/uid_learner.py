#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UID 学习库 —— 用人工修正结果持续改进番号识别（样例归纳式学习）

工作方式（在线归纳学习，无神经网络 / 无联网依赖，符合桌面应用实际）：
1) **精确记忆**：人工把某个文件名的 UID 改成 X 后，下次遇到同名文件直接用 X；
2) **模式归纳**：每次修正都会对比"原文件名 − 正确番号"，归纳出垃圾前缀
   （如 ``489155.com@``、``hhd800.com@``）与垃圾后缀（如 ``-U``、``-Uncensored``），
   以后扫描到带同类前后缀的文件会先剥离再提取，识别更准；
3) **方法归纳（泛化）**：这是"学会提取方法"的核心 —— 每次修正都会定位正确 UID
   在文件名中的位置，把 UID 的**字形结构**归纳为一个正则 token（如 ``ABN-129`` →
   ``[A-Za-z]{3}[-_.\s]?\d{3}``），并把 UID 前后的上下文定界符学成灵活模板。
   以后遇到*同类型结构*的新文件名（即使从没见过这个名字），只要文件名内含有
   相同字形 + 定界结构的 token，即自动按学会的方法提取出 UID。
   同一类结构被修正得越多（support 越高），该提取方法越优先。

   这相当于"少量样例驱动的归纳式程序合成"：从人工给出的 (输入, 正确输出) 配对中
   反向归纳出可复用的特征提取规则，并随修正累积不断增强——而非仅记忆个别样例。

全部知识持久化到 ``uid_learn.json``，重启后依然生效。修正越多，识别越准。
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pattern_matcher import PatternMatcher

_MATCHER = PatternMatcher()
_LOCK = threading.Lock()

# 学习库文件位置：应用根目录（workflow/ 的上一级）
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "uid_learn.json"

MAX_MAPPINGS = 5000      # 精确记忆上限（超出淘汰最早记录）
MAX_PATTERNS = 200       # 前后缀模式上限
MAX_TEMPLATES = 300      # 归纳模板上限（超出淘汰最低 support）

# UID 邻接的定界符（用于上下文模板的灵活匹配）
_DELIM_RE = r'[-_./\\ @()\[\]{}]'
_DELIMS = set("-_./\\ @()[]{}")


# ---------------------------------------------------------------------------
# 字形 → 正则：把"正确 UID"归纳成可泛化的 token 正则
# ---------------------------------------------------------------------------
def _uid_token_re(uid: str) -> str:
    """把正确 UID 归纳为一个正则 token（保留字形结构、同类数量弹性）。

    例：``ABN-129``   →  ``[A-Za-z]{1,6}[-_.\s]?\d{1,7}``
        ``SSIS-001``  →  ``[A-Za-z]{1,6}[-_.\s]?\d{1,7}``（同一条规则即可泛化命中）
        ``FC2-PPV-123456`` → ``[A-Za-z]{1,6}\d{1,7}[-_.\s]?[A-Za-z]{1,6}[-_.\s]?\d{1,7}``
        ``abc123``    →  ``[A-Za-z]{1,6}\d{1,7}``
    连续同类的字符合并成弹性区间，使"字母-数字"同结构的其它番号（SSIS / IPX / HND …）
    也能被同一条规则命中——这正是"学会提取方法"的泛化能力。
    """
    runs: List[List] = []
    for ch in uid:
        if ch.isalpha():
            cls = 'L'
        elif ch.isdigit():
            cls = 'D'
        elif ch in _DELIMS:
            cls = 'X'
        else:
            cls = 'O'
        if runs and runs[-1][0] == cls:
            runs[-1][1] += ch
        else:
            runs.append([cls, ch])
    parts = []
    for cls, chunk in runs:
        if cls == 'L':
            parts.append("[A-Za-z]{1,6}")      # 字母数弹性（SSIS/ABN 同结构）
        elif cls == 'D':
            parts.append("\\d{1,7}")           # 数字数弹性
        elif cls == 'X':
            parts.append("[-_.\s]?")           # 定界符可有可无、单字符
        else:
            parts.append(re.escape(chunk))
    return "".join(parts)


def _looks_like_uid(candidate: str) -> bool:
    """校验模板提取出的候选是否像一个番号（避免把中文/杂质当 UID）"""
    c = (candidate or "").strip()
    if not c or not (2 <= len(c) <= 20):
        return False
    if re.search(r"[一-鿿぀-ヿ]", c):           # 含 CJK / 假名
        return False
    if re.search(r"[^A-Za-z0-9\-_.\s]", c):     # 含怪字符
        return False
    has_letter = any(ch.isalpha() for ch in c)
    has_digit = any(ch.isdigit() for ch in c)
    # 绝大多数番号含数字；纯字母要求长度≥4，否则太易误匹配
    return has_digit or (has_letter and len(re.sub(r"[-_.\s]", "", c)) >= 4)


def _context_re(pre: str, post: str) -> Tuple[str, str]:
    """把 UID 前后的上下文归纳成灵活正则。

    - 末尾/开头的定界符（空格、_、- 等）保留为字面锚点；
    - 定界符之外的可变文字（如演员名、站点水印）退化为 ``.*?``。
    返回 (left_re, right_re)，与 token 正则拼接成完整模板。
    """
    left = ""
    if pre:
        m = re.search(_DELIM_RE + r"*$", pre)     # 末尾的定界符串
        if m and m.start() == 0:
            left = re.escape(pre)                  # 整段都是定界符（如 "["）
        elif m and m.start() > 0:
            left = ".*?" + re.escape(pre[m.start():])
        else:
            left = ".*?"
    right = ""
    if post:
        m = re.match(_DELIM_RE + r"+", post)       # 开头的定界符串
        if m and m.end() == len(post):
            right = re.escape(post)
        elif m and m.end() > 0:
            right = re.escape(post[:m.end()]) + ".*?"
        else:
            right = ".*?"
    return left, right


class UidLearner:
    """番号识别的持久化学习库"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.mappings: Dict[str, str] = {}       # 文件名主干(小写) -> 修正后的 UID
        self.junk_prefixes: Dict[str, int] = {}  # 垃圾前缀(小写) -> 出现次数
        self.junk_suffixes: Dict[str, int] = {}  # 垃圾后缀(小写) -> 出现次数
        # 归纳模板：regex 字符串 -> support 计数（出现越多越可信）
        self.uid_templates: Dict[str, int] = {}

    # ---------- 持久化 ----------
    def load(self) -> "UidLearner":
        try:
            import json
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.mappings = dict(data.get("mappings") or {})
                self.junk_prefixes = dict(data.get("junk_prefixes") or {})
                self.junk_suffixes = dict(data.get("junk_suffixes") or {})
                self.uid_templates = {k: int(v) for k, v in
                                      (data.get("uid_templates") or {}).items()}
        except Exception:
            pass
        return self

    def save(self):
        try:
            import json
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({
                "mappings": self.mappings,
                "junk_prefixes": self.junk_prefixes,
                "junk_suffixes": self.junk_suffixes,
                "uid_templates": self.uid_templates,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def load_default(cls) -> "UidLearner":
        return cls().load()

    # ---------- 学习 ----------
    def learn(self, stem: str, corrected_uid: str, auto_uid: str = "") -> bool:
        """记录一次人工修正；返回知识库是否发生变化

        stem          : 最初源文件的文件名主干（UID 的推导来源）
        corrected_uid : 人工修正后的正确 UID
        auto_uid      : 修正前的自动 UID（仅用于判断修正是否有效）
        """
        stem = (stem or "").strip()
        uid = (corrected_uid or "").strip()
        if not stem or not uid or uid == stem:
            return False
        # 规范化存储键：去掉扩展名（调用方多已传 stem，这里再兜底一次，
        # 同时兼容旧版把全名当键写入的 uid_learn.json）
        key = Path(stem).stem.lower() or stem.lower()
        with _LOCK:
            changed = False
            if self.mappings.get(key) != uid:
                self.mappings[key] = uid
                changed = True
                # 超上限时淘汰最早的记录（dict 保持插入序）
                while len(self.mappings) > MAX_MAPPINGS:
                    self.mappings.pop(next(iter(self.mappings)))

            # ---- 归纳垃圾前缀 / 后缀：在文件名主干里定位修正后的番号 ----
            located = _locate(stem, uid)
            if located:
                start, end = located
                prefix = stem[:start].strip()
                suffix = stem[end:].strip()
                if prefix and len(prefix) <= 40:
                    self.junk_prefixes[prefix.lower()] = \
                        self.junk_prefixes.get(prefix.lower(), 0) + 1
                    changed = True
                if suffix and len(suffix) <= 16:
                    self.junk_suffixes[suffix.lower()] = \
                        self.junk_suffixes.get(suffix.lower(), 0) + 1
                    changed = True
                # 各保留出现次数最多的前若干个
                self.junk_prefixes = dict(
                    sorted(self.junk_prefixes.items(), key=lambda kv: -kv[1])[:MAX_PATTERNS])
                self.junk_suffixes = dict(
                    sorted(self.junk_suffixes.items(), key=lambda kv: -kv[1])[:MAX_PATTERNS])

                # ---- 方法归纳：把"提取位置 + 字形结构"学成可泛化模板 ----
                changed |= self._learn_template(stem, start, end, uid)

            if changed:
                self.save()
            return changed

    def _learn_template(self, stem: str, start: int, end: int,
                        uid: str) -> bool:
        """把 (文件名, 正确UID) 归纳为一个正则模板，供同类型新文件名复用"""
        token_re = _uid_token_re(uid)
        if not token_re:
            return False
        # 仅在 UID 周围有合法边界时才归纳（避免从形如 "abc123" 这种整串即 UID
        # 的样例学到过度宽松的模板；这类本就由 base matcher 直接命中）
        left, right = _context_re(stem[:start], stem[end:])
        template = (f"{left}(?<![A-Za-z0-9])({token_re})(?![A-Za-z0-9]){right}")
        try:
            re.compile(template, re.I)
        except Exception:
            return False
        sig = template
        self.uid_templates[sig] = self.uid_templates.get(sig, 0) + 1
        # 超过上限淘汰最低 support
        if len(self.uid_templates) > MAX_TEMPLATES:
            drop = sorted(self.uid_templates, key=lambda k: self.uid_templates[k])[0]
            self.uid_templates.pop(drop, None)
        return True

    # ---------- 推断 ----------
    def suggest(self, path: str) -> Tuple[str, str]:
        """按学习知识给出 UID 建议

        返回 (uid, source)；source 为：
          ``learned``            —— 同名精确命中（最高可信度）；
          ``learned_template``   —— 按归纳出的提取方法，从同类型文件名命中（泛化）；
          ``learned_pattern``    —— 按归纳的前后缀剥离后提取成功；
          ``''``                 —— 无建议（交由主匹配器兜底）。
        """
        try:
            stem = Path(path).stem or Path(path).name
        except Exception:
            return "", ""
        # 先去扩展名的键，再回退带扩展名的全名（兼容旧版 uid_learn.json）
        name_low = Path(path).name.strip().lower()
        key = stem.strip().lower()
        with _LOCK:
            # 1) 精确记忆优先
            hit = self.mappings.get(key) or self.mappings.get(name_low)
            if hit:
                return hit, "learned"
            # 2) 方法归纳：按 support 从高到低尝试可泛化模板
            for sig in sorted(self.uid_templates, key=lambda k: -self.uid_templates[k]):
                try:
                    m = re.search(sig, stem, re.I)
                except Exception:
                    continue
                if m:
                    cand = (m.group(1) or "").strip()
                    if _looks_like_uid(cand):
                        return cand.upper(), "learned_template"
            # 3) 垃圾前后缀剥离 + 主匹配器
            s = stem.strip()
            changed = False
            prefs = sorted(self.junk_prefixes, key=lambda k: -self.junk_prefixes[k])
            sufs = sorted(self.junk_suffixes, key=lambda k: -self.junk_suffixes[k])
            for _ in range(3):
                sl = s.lower()
                hit = next((p for p in prefs if p and sl.startswith(p) and len(p) < len(s)), None)
                if hit:
                    s = s[len(hit):]
                    changed = True
                    continue
                hit = next((x for x in sufs if x and sl.endswith(x) and len(x) < len(s)), None)
                if hit:
                    s = s[:-len(hit)]
                    changed = True
                    continue
                break
            if changed:
                try:
                    info = _MATCHER.extract_code(s)
                except Exception:
                    info = None
                if info:
                    code = (info.get('canonical') or info.get('full') or '').strip()
                    if code:
                        return code.upper(), "learned_pattern"
        return "", ""

    def stats(self) -> Dict[str, int]:
        return {"mappings": len(self.mappings),
                "junk_prefixes": len(self.junk_prefixes),
                "junk_suffixes": len(self.junk_suffixes),
                "uid_templates": len(self.uid_templates)}


def _locate(stem: str, uid: str) -> Optional[Tuple[int, int]]:
    """在 stem 中定位 uid（允许字符间夹杂空格/_-/. 等分隔符），返回 (start, end)"""
    chars = [c for c in uid if not c.isspace()]
    if not chars:
        return None
    flex = r'[\s_\-.]{0,2}'.join(re.escape(c) for c in chars)
    m = re.search(flex, stem, re.I)
    return (m.start(), m.end()) if m else None
