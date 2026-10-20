#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流串联 —— 数据模型

设计要点
========
1) **唯一标识（UID）**：视频一进入工作流就分配一个贯穿全流程的 UID。
   优先用番号（``PatternMatcher`` 提取），其次用文件名主干，最后退回 UUID。
   V1→V2→V3 每个阶段的文件变化都记录在同一个 ``WorkflowItem`` 上，
   保证中间文件（转码成片、字幕、临时文件）不会串台。

2) **变量模板**：目标文件夹 / 输出文件名支持 ``{uid} {stem} {name} {ext}
   {src_dir} {cur_dir} {date} {step}`` 等变量，满足无人值守的动态命名。

3) **步骤可编排**：``WorkflowJob.steps`` 是有序列表，可在界面上增删、上下移动，
   从而支持「A→B→转码→C→字幕」与「A→B→字幕→转码打包软字幕→C」等不同路径。
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from pattern_matcher import PatternMatcher

try:  # 与主程序共用视频扩展名定义
    from config import VIDEO_EXTENSIONS
except Exception:  # pragma: no cover - 独立运行兜底
    VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts',
                        '.rmvb', '.rm', '.m4v', '.mpg', '.mpeg', '.3gp', '.webm'}

# ---------------------------------------------------------------- 步骤类型
STEP_MOVE = "move"
STEP_TRANSCODE = "transcode"
STEP_SUBTITLE = "subtitle"
STEP_RENAME = "rename"

STEP_LABELS = {
    STEP_MOVE: "移动到文件夹",
    STEP_TRANSCODE: "剪辑与重编码",
    STEP_SUBTITLE: "生成字幕",
    STEP_RENAME: "修改名称",
}
STEP_ORDER = (STEP_MOVE, STEP_TRANSCODE, STEP_SUBTITLE, STEP_RENAME)
STEP_ICONS = {STEP_MOVE: "➡", STEP_TRANSCODE: "✂", STEP_SUBTITLE: "📝",
              STEP_RENAME: "✏"}

# 转码：编码器下拉显示名（与 video_grid_tab.VideoTranscodePanel.ENCODER_OPTIONS 一致）
ENCODER_LABELS = {
    "auto": "自动（优先显卡加速）",
    "nvenc": "显卡 NVIDIA（NVENC）",
    "amf": "显卡 AMD（AMF）",
    "qsv": "显卡 Intel（QSV）",
    "cpu": "CPU（libx265 软编码）",
}
# 转码：字幕封装方式（与 video_grid_tab.VideoTranscodePanel.SUBTITLE_MODES 一致）
SUBTITLE_MODE_LABELS = {
    "none": "不添加字幕",
    "soft": "软字幕·封装（随转码）",
    "soft_fast": "软字幕·快速附加（不重编码）",
}
# 剪辑时间模式
TRIM_MODES = {
    "none": "不裁剪（整段输出）",
    "range": "统一时间段（起~止）",
    "head": "去掉片头 N 秒",
    "tail": "去掉片尾 N 秒",
    "custom": "使用每个视频单独设置的时间",
}

FINISH_NONE, FINISH_SLEEP, FINISH_SHUTDOWN = "none", "sleep", "shutdown"
FINISH_LABELS = {FINISH_NONE: "无操作", FINISH_SLEEP: "进入睡眠", FINISH_SHUTDOWN: "关机"}

DEFAULT_PARAMS: Dict[str, Dict[str, Any]] = {
    STEP_MOVE: {
        "target_dir": "",              # 支持 {uid} 等变量
        "with_companions": True,       # 同时移动同名字幕/图片等附属文件
        "companion_exts": ".srt,.ass,.ssa,.jpg,.jpeg,.png,.webp,.nfo,.json",
        "uid_subdir": False,           # 目标下再建一层 {uid} 子文件夹
    },
    STEP_TRANSCODE: {
        "trim_mode": "none",
        "start": 0.0,                  # 统一时间段：起点（秒）
        "end": 0.0,                    # 统一时间段：终点（秒，0=到片尾）
        "head": 0.0,                   # 去掉片头秒数
        "tail": 0.0,                   # 去掉片尾秒数
        "resolution": "1280x720",
        "aspect": "保持原比例",
        "bitrate": 2500,
        "encoder": ENCODER_LABELS["auto"],
        "save_mode": "source",         # source=输出到当前文件夹 / custom=指定文件夹
        "save_dir": "",
        "out_ext": "mp4",
        "keep_quality": False,         # 流拷贝剪辑：仅按起止时间裁剪，不改分辨率/码率/编码（无损）
        "keep_bitrate": False,         # 不修改码率：按每个视频自身的原片码率编码（读不到时回退 bitrate）
        "tc_merge": False,             # 拼接为一个视频：任务内多个视频按顺序合并成一个成片
        "auto_orient": True,           # 竖屏自动适配：按源视频屏幕方向自动调整目标分辨率
        "portrait_res": "自动(按源比例)",    # 竖屏源目标分辨率：自动(按源比例) / 720x1280 / 720x1080
        "subtitle_mode": "none",       # none / soft / soft_fast
        "del_src": False,              # 转码成功后删除转码源文件
        "del_srt": False,              # 软字幕封装成功后删除原始 .srt 文件
    },
    STEP_SUBTITLE: {
        "asr_engine": "kotoba-whisper",        # faster-whisper / kotoba-whisper / reazonspeech-nemo
        "model_size": "medium",                # tiny~large-v3（仅 faster-whisper）
        "translator_engine": "NLLB-200",       # NLLB-200 / Sakura-7B / Sakura-14B / Sakura-GalTransl-7B
        "device": "cuda",                      # cuda / cpu（不可用时自动降级）
        "compute_type": "int8",
        "bilingual": True,                     # 双语（日文+中文）
        "skip_existing": True,                  # 同名 srt 已存在则跳过
    },
    STEP_RENAME: {
        "name_template": "{stem}",             # 文件名模板（不含扩展名；可用 {uid}/{stem}/{ext} 等变量）
        "with_companions": True,               # 同时改名同名附属文件（字幕/封面等）
        "companion_exts": ".srt,.ass,.ssa,.jpg,.jpeg,.png,.webp,.nfo,.json",
    },
}


def default_params(step_type: str) -> Dict[str, Any]:
    """返回某类步骤的默认参数（深拷贝，避免多处共享同一字典）"""
    return dict(DEFAULT_PARAMS.get(step_type, {}))


# ---------------------------------------------------------------- 唯一标识
_MATCHER = PatternMatcher()
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')

_LEARNER = None


def _get_learner():
    """全局 UID 学习库（懒加载；不可用时退化为直接提取，不影响主流程）"""
    global _LEARNER
    if _LEARNER is None:
        try:
            from .uid_learner import UidLearner
            _LEARNER = UidLearner.load_default()
        except Exception:
            class _Noop:
                def suggest(self, path):
                    return "", ""

                def learn(self, stem, uid, auto=""):
                    return False
            _LEARNER = _Noop()
    return _LEARNER


def make_uid(path: str) -> str:
    """生成贯穿全流程的唯一标识

    0) 学习库建议（按可信度从高到低）：
       - 同名精确记忆（人工修正过的同名文件）直接采用；
       - 方法归纳：按人工修正里归纳出的"提取方法"（UID 字形结构 + 上下文定界符），
         对*同类型结构*的新文件名泛化提取（即使从未见过这个名字）；
       - 垃圾前后缀剥离后再用主匹配器提取；
    1) 其次取番号（如 SSIS-001、FC2-PPV-123456，用 PatternMatcher 提取）；
    2) 其次取清洗后的文件名主干；3) 最后退回 UUID 前 10 位。
    同一个视频在整个流程中 UID 恒定不变。
    """
    stem = Path(path).stem or Path(path).name
    try:
        learned, _src = _get_learner().suggest(path)
    except Exception:
        learned = ""
    if learned:
        return learned
    try:
        info = _MATCHER.extract_code(stem)
    except Exception:
        info = None
    if info:
        code = (info.get('canonical') or info.get('full') or '').strip()
        code = code.replace(' ', '')
        if code:
            return code.upper()
    cleaned = _ILLEGAL.sub('_', stem).strip().strip('.')
    return cleaned or uuid.uuid4().hex[:10]


def uid_with_normalized_suffix(uid: str, filename: str) -> str:
    """在 UID 基础上追加按规则归一化后的后缀（供 {uid_norm} 模板变量使用）

    规则与 file_organize_tab / PatternMatcher 保持一致：
      -u / -un / -Uncensored / -ai / -破解 / -无码  →  -Uncensored
      -c / -ch / -chinese                            →  -C
      -UC / -UNC                                     →  保留原样（大写）
    无特殊后缀时与 {uid} 相同。
    例：JUR-090-U.mp4，UID=JUR-090  →  JUR-090-Uncensored
    """
    try:
        suffixes = _MATCHER.detect_all_suffixes(filename or '')
    except Exception:
        suffixes = {'chinese_subtitle': None, 'uncensored': None}
    suffix = ''
    if suffixes:
        suffix = suffixes.get('uncensored') or suffixes.get('chinese_subtitle') or ''
    return f"{uid}{suffix}"


def learn_uid_correction(path: str, corrected_uid: str, auto_uid: str = "") -> bool:
    """记录一次人工修正，供后续识别学习（返回知识库是否变化）"""
    try:
        stem = Path(path).stem or Path(path).name
        return _get_learner().learn(stem, corrected_uid, auto_uid)
    except Exception:
        return False


def learner_stats() -> Dict[str, int]:
    """学习库规模（精确记忆条数 / 前缀模式数 / 后缀模式数 / 归纳模板数）"""
    try:
        return _get_learner().stats()
    except Exception:
        return {"mappings": 0, "junk_prefixes": 0, "junk_suffixes": 0, "uid_templates": 0}


def dedup_uids(uids: List[str], existing: Optional[Set[str]] = None) -> List[str]:
    """同一批任务里出现重复 UID 时追加 -2 / -3 序号，保证唯一。

    ``existing`` 为已存在条目的 uid 集合，用于追加导入时避免新条目
    与已有条目（不同文件但同名/同番号）撞号。
    """
    seen: Dict[str, int] = {}
    if existing:
        for u in existing:
            seen[u] = seen.get(u, 0) + 1
    out: List[str] = []
    for u in uids:
        n = seen.get(u, 0) + 1
        seen[u] = n
        out.append(u if n == 1 else f"{u}-{n}")
    return out


def safe_name(name: str) -> str:
    """去掉文件名中的非法字符"""
    return _ILLEGAL.sub('_', (name or '').strip()).strip(' .') or "untitled"


_VAR_RE = re.compile(r'\{(\w+)\}')


def render_template(tpl: str, item: 'WorkflowItem', **extra) -> str:
    """把路径/文件名模板里的 {变量} 替换成实际值（未知变量原样保留）"""
    if not tpl:
        return ""
    ctx = item.template_vars()
    if extra:
        ctx.update(extra)

    def _sub(m):
        return str(ctx.get(m.group(1), m.group(0)))

    return _VAR_RE.sub(_sub, str(tpl))


# ---------------------------------------------------------------- 数据类
@dataclass
class WorkflowStep:
    """流程中的一个步骤（可增删、可上下移动调整顺序）"""
    type: str = STEP_MOVE
    enabled: bool = True
    params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        merged = default_params(self.type)
        merged.update(self.params or {})
        if self.type == STEP_TRANSCODE:
            # 改名功能已移至“修改名称”步骤：清除旧版本配置残留的输出名模板，
            # 避免其与改名步骤重复执行，把名称追加两次（如 -C → -C-C）
            merged.pop('name_template', None)
        self.params = merged

    @property
    def label(self) -> str:
        return STEP_LABELS.get(self.type, self.type)

    def summary(self) -> str:
        """步骤参数的一句话摘要（显示在流程列表里）"""
        p = self.params
        if self.type == STEP_MOVE:
            d = (p.get('target_dir') or '').strip()
            extra = "（含同名附属文件）" if p.get('with_companions') else ""
            extra += " +{uid}子目录" if p.get('uid_subdir') else ""
            return f"移动到 {d or '（未设置）'}{extra}"
        if self.type == STEP_TRANSCODE:
            mode = TRIM_MODES.get(p.get('trim_mode'), p.get('trim_mode'))
            place = "当前文件夹" if p.get('save_mode') == 'source' else (p.get('save_dir') or '（未设置）')
            sub = SUBTITLE_MODE_LABELS.get(p.get('subtitle_mode'), '不添加字幕')
            if p.get('keep_quality'):
                return (f"{mode} · 流拷贝剪辑（原画质）→ {place}；字幕：{sub}")
            return (f"{mode} → {p.get('resolution')} {p.get('bitrate')}k → {place}；字幕：{sub}")
        if self.type == STEP_SUBTITLE:
            return (f"{p.get('asr_engine')}"
                    + (f"/{p.get('model_size')}" if p.get('asr_engine') == 'faster-whisper' else "")
                    + f" + {p.get('translator_engine')}；{p.get('device')}；"
                    + ("双语" if p.get('bilingual') else "仅中文"))
        if self.type == STEP_RENAME:
            tpl = p.get('name_template') or '{stem}'
            extra = "（含同名附属文件）" if p.get('with_companions') else ""
            return f"重命名为：{tpl}{extra}"
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "enabled": bool(self.enabled),
                "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowStep':
        return cls(type=data.get("type", STEP_MOVE),
                   enabled=bool(data.get("enabled", True)),
                   params=dict(data.get("params") or {}))


@dataclass
class WorkflowItem:
    """工作流中的一条视频（UID 恒定，current 随阶段推进而更新）"""
    uid: str = ""
    origin: str = ""          # V1：进入工作流时的原始路径
    current: str = ""         # Vn：当前阶段的文件路径
    status: str = "待处理"     # 待处理 / 进行中 / 已完成 / 失败 / 已跳过 / 已停止
    stage: str = ""           # 当前所处步骤摘要
    message: str = ""
    subtitle: str = ""        # 最近一次生成/匹配到的字幕文件
    # 剪辑时间（无人值守时为 None 表示沿用步骤的统一设置）
    trim_start: float = 0.0
    trim_end: float = 0.0
    use_custom_trim: bool = False
    # 探测到的媒体信息
    duration: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    video_kbps: int = 0        # 视频流数据速率（kbps，不含音频），与资源管理器“数据速率”口径一致
    # 阶段轨迹：每个元素的 {"step","from","to","time","ok"}
    history: List[Dict[str, Any]] = field(default_factory=list)

    # ---- 便捷属性 ----
    @property
    def path(self) -> str:
        return self.current or self.origin

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        return Path(self.path).stem

    def template_vars(self) -> Dict[str, str]:
        """模板可用变量"""
        p = Path(self.path)
        o = Path(self.origin) if self.origin else p
        return {
            "uid": self.uid,
            "uid_norm": uid_with_normalized_suffix(self.uid, p.name),
            "name": p.name,
            "stem": p.stem,
            "ext": p.suffix.lstrip('.'),
            "cur_dir": str(p.parent),
            "src_dir": str(o.parent),
            "src_stem": o.stem,
            "date": time.strftime("%Y%m%d"),
            "datetime": time.strftime("%Y%m%d_%H%M%S"),
            "stage": self.stage or "",
        }

    def render(self, tpl: str, **extra) -> str:
        return render_template(tpl, self, **extra)

    def record(self, step: str, src: str, dst: str, ok: bool = True, note: str = ""):
        """记录一次阶段变化（V1 → V2 → V3 的轨迹）"""
        self.history.append({
            "step": step,
            "from": src,
            "to": dst,
            "time": time.strftime("%H:%M:%S"),
            "ok": bool(ok),
            "note": note,
        })

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowItem':
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class WorkflowJob:
    """一条完整的工作流配置（可保存 / 读取 / 复用）"""
    name: str = "默认流程"
    source_dir: str = ""
    recursive: bool = True
    only_video: bool = True
    steps: List[WorkflowStep] = field(default_factory=list)
    finish_action: str = FINISH_NONE
    continue_on_error: bool = False

    def __post_init__(self):
        if not self.steps:
            self.steps = [WorkflowStep(STEP_MOVE), WorkflowStep(STEP_TRANSCODE)]
        self.steps = [s if isinstance(s, WorkflowStep) else WorkflowStep.from_dict(s)
                      for s in self.steps]

    def enabled_steps(self) -> List[WorkflowStep]:
        return [s for s in self.steps if s.enabled]

    def describe(self) -> str:
        """流程路径的文字描述，例如：移动到B → 剪辑转码 → 移动到C → 生成字幕"""
        return " → ".join(f"{STEP_ICONS.get(s.type, '')}{s.label}" for s in self.enabled_steps())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source_dir": self.source_dir,
            "recursive": self.recursive,
            "only_video": self.only_video,
            "steps": [s.to_dict() for s in self.steps],
            "finish_action": self.finish_action,
            "continue_on_error": self.continue_on_error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowJob':
        data = dict(data or {})
        steps = [WorkflowStep.from_dict(s) for s in data.get("steps") or []]
        data["steps"] = steps
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------- 任务清单
@dataclass
class WorkflowTask:
    """任务清单中的一项：一批视频 + 它专属的完整流程

    与 ``WorkflowJob`` 的区别：Job 只是“流程”；Task 把流程绑定到具体的视频文件，
    并附上每个视频的人工修正（UID / 剪辑时间），从而支持“多批视频各自用不同流程
    （不同的数据速率、字幕模型、目标文件夹……）”，再按清单顺序逐个处理。
    """

    name: str = ""
    file_paths: List[str] = field(default_factory=list)
    # origin(原始路径) -> {"uid","trim_start","trim_end","use_custom_trim"}
    overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    job: WorkflowJob = field(default_factory=WorkflowJob)

    def build_items(self) -> List['WorkflowItem']:
        """根据该任务的文件列表与人工修正，构造独立的 WorkflowItem

        返回的是新对象（不污染主清单），同一视频出现在多个任务里也互不串台。
        """
        items = build_items(self.file_paths)
        for it in items:
            ov = self.overrides.get(it.origin)
            if ov:
                u = (ov.get("uid") or "").strip()
                if u:
                    it.uid = u
                it.trim_start = float(ov.get("trim_start", 0.0) or 0.0)
                it.trim_end = float(ov.get("trim_end", 0.0) or 0.0)
                it.use_custom_trim = bool(ov.get("use_custom_trim", False))
        return items

    def summary(self) -> str:
        return f"{len(self.file_paths)} 个视频 · {self.job.describe() or '（空流程）'}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "file_paths": list(self.file_paths),
            "overrides": {k: dict(v) for k, v in (self.overrides or {}).items()},
            "job": self.job.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowTask':
        data = dict(data or {})
        job = data.get("job") or {}
        try:
            job = WorkflowJob.from_dict(job)
        except Exception:
            job = WorkflowJob()
        return cls(
            name=data.get("name", ""),
            file_paths=[str(p) for p in (data.get("file_paths") or [])],
            overrides={k: dict(v) for k, v in (data.get("overrides") or {}).items()},
            job=job,
        )


# ---------------------------------------------------------------- 扫描
def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def scan_videos(source_dir: str, recursive: bool = True, only_video: bool = True) -> List[str]:
    """扫描源文件夹里的视频文件（可按扩展名过滤）"""
    root = Path(source_dir)
    if not root.is_dir():
        return []
    it = root.rglob('*') if recursive else root.glob('*')
    files: List[str] = []
    for p in it:
        try:
            if not p.is_file():
                continue
            if p.name.startswith('.'):        # 跳过转码临时文件
                continue
            if only_video and not is_video(p.name):
                continue
            files.append(str(p))
        except OSError:
            continue
    return sorted(files, key=lambda x: str(x).lower())


def build_items(files: List[str]) -> List[WorkflowItem]:
    """按文件列表创建工作流条目（分配 UID 并保证唯一）"""
    uids = dedup_uids([make_uid(f) for f in files])
    items: List[WorkflowItem] = []
    for uid, f in zip(uids, files):
        items.append(WorkflowItem(uid=uid, origin=f, current=f, status="待处理"))
    return items
