#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流串联选项卡（多任务串联 / 无人值守）

把已有的「文件夹下移动」「视频剪辑转码」「视频转字幕」三个功能打通为
可编排、可调序、可预置参数的工作流：

    A 文件夹 → 移动到 B → 剪辑与重编码 → 移动到 C → 生成字幕
    A 文件夹 → 移动到 B → 生成字幕 → 剪辑转码并打包软字幕 → 移动到 C

每个视频从进入流程起就绑定一个贯穿全流程的唯一标识 UID（优先取番号），
界面与日志中始终按 UID 追踪，避免中间文件（V1/V2/V3）匹配错误。

独立运行：``python workflow_tab.py``
"""

from __future__ import annotations

import gc
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Any, Dict, List, Optional, Tuple

from toast import show_toast

try:
    from video_grid_tab import (ConfigManager, VideoTranscodePanel,
                                EmbeddedPlayer, TrimRangeBar, TimeEntry,
                                format_clock, parse_clock)
except Exception as e:      # pragma: no cover
    raise ImportError(f"无法载入 video_grid_tab 模块: {e}")

from workflow.models import (WorkflowJob, WorkflowStep, WorkflowItem, WorkflowTask,
                             STEP_MOVE, STEP_TRANSCODE, STEP_SUBTITLE, STEP_RENAME,
                             STEP_LABELS, STEP_ICONS, ENCODER_LABELS, VIDEO_EXTENSIONS,
                             SUBTITLE_MODE_LABELS, TRIM_MODES, FINISH_LABELS,
                             scan_videos, build_items, is_video, dedup_uids,
                             learn_uid_correction, learner_stats)
from workflow.executor import WorkflowExecutor

CONFIG_SECTION = "workflow_tab"

RESOLUTIONS = ["1280x720", "1920x1080", "2560x1440", "854x480", "保持原分辨率", "自定义分辨率"]
ASPECTS = ["保持原比例", "16:9", "4:3", "9:16", "1:1"]
# 分辨率设置四种模式（UI 顶层选择）
RES_MODES = ["预设固定分辨率", "自定义分辨率", "固定宽或高", "固定长边/短边"]
RES_PRESETS = ["1280x720", "1920x1080", "2560x1440", "854x480", "保持原分辨率"]
OUT_EXTS = ["mp4", "mkv", "mov"]
TRIM_PRECISIONS = {"0.01S": 0.01, "0.05S": 0.05, "0.1S": 0.1, "0.5S": 0.5, "1S": 1.0}

# 标准分辨率（横/竖成对）：清单“分辨率”列据此着色——
# 标准分辨率显示绿色，裁剪过的特殊分辨率（如 960x1264）显示红色，便于一眼区分。
STANDARD_RESOLUTIONS = {
    (1920, 1080), (1280, 720), (3840, 2160), (2560, 1440), (4096, 2160),
    (2048, 1080), (854, 480), (640, 360), (960, 540), (352, 288),
    (176, 144), (320, 240), (1920, 1200), (1366, 768), (1280, 1024),
    (1024, 768),
}

# “选中步骤的参数”区固定高度（px）：内容超出时用右侧滚动条查看，不撑大窗口
PARAM_AREA_H = 300
# 灰色提示文字的换行宽度：超宽时自动换行，避免把整行（乃至窗口）撑宽
HINT_WRAP = 330

import re as _re
_ILLEGAL_NAME = _re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _sanitize_filename(name: str) -> str:
    """清理 Windows 下非法的文件名字符（\\ / : * ? " < > | 及换行/制表）"""
    cleaned = _ILLEGAL_NAME.sub('_', name).strip()
    return cleaned or "file"



# 文件名/目标文件夹可用变量的用法说明（点“变量用法”按钮展开，平时隐藏）
VAR_HELP_TEXT = """可用变量（目标文件夹、修改名称步骤的文件名模板均支持）：

  {uid}      唯一标识，优先取番号（如 SSIS-001；无番号则用文件名派生，保证全程不变）
  {uid_norm} 番号 + 按规则归一化后的后缀
             例：JUR-090-U.mp4 → JUR-090-Uncensored
             规则：-u/-un/-ai/-破解 → -Uncensored；-c/-ch → -C；
                   -UC/-UNC 保留；无特殊后缀时与 {uid} 相同
  {stem}     当前文件主名（不含扩展名）
             例：SSIS-001.mp4 → SSIS-001
  {name}     当前文件完整文件名（含扩展名）
             例：SSIS-001.mp4 → SSIS-001.mp4
  {src_dir}  最初的源文件夹
             例：D:\\Downloads\\new
  {cur_dir}  当前所在文件夹（经过前面移动步骤之后的落点）
             例：E:\\Library\\SSIS-001
  {date}     今天日期（YYYYMMDD）
             例：20261006
  {ext}      扩展名（不含点）
             例：mp4

用法示例：
  文件名模板 {uid}_{date}           → 重命名为  SSIS-001_20261006.mp4
  文件名模板 {stem}_转码            → 重命名为  SSIS-001_转码.mp4
  目标文件夹     E:\\Library\\{uid}  → 移动/输出到  E:\\Library\\SSIS-001\\ 文件夹
  目标文件夹     E:\\Backup\\{date}  → 移动/输出到  E:\\Backup\\20261006\\ 文件夹

关于扩展名（重要）：
  · “修改名称”步骤：模板用 {stem}/{uid} 等“不含扩展名”的变量时，会自动在末尾追加
    当前文件的扩展名；模板已含扩展名（如 {name}）则直接沿用，不会出现重复扩展名。
    例：{stem}=SSIS-001 → 重命名为  SSIS-001.mp4（沿用原扩展名）
  · “剪辑与重编码”步骤：不含扩展名的模板会追加“容器”扩展名（上面“输出规格/编码·容器”
    里的容器，如 mp4）；已含扩展名则沿用。
    例：{stem}=SSIS-001 + 容器 mp4 → 输出  SSIS-001.mp4
  · 想“保留原始文件名”请用 {name}；想改名再用 {stem}。
"""


# ======================================================================
# 剪辑时间设置对话框：直接复用视频剪辑 Tab 的双滑块 / 时间框 / ±按钮
# ======================================================================
class TrimTimeDialog:
    """为单个视频设置剪辑起止时间

    复用 ``video_grid_tab`` 的 ``EmbeddedPlayer``（画面）``TrimRangeBar``（双滑块）
    与 ``TimeEntry``（掩码时间框），并沿用同样的“−/+"微调、"设为当前"、
    "移到最前/最后"行为，保证与剪辑 Tab 的操作手感完全一致。
    """

    def __init__(self, parent, video_path: str, start: float = 0.0, end: float = 0.0,
                 use_custom: bool = False, item=None):
        self.video_path = video_path
        self.item = item                        # 关联的 WorkflowItem（用于标记需剪辑/有字幕）
        self.result: Optional[tuple] = None      # (start, end, use_custom)
        self._active = 'start'
        self._loading = False

        self.top = tk.Toplevel(parent)
        self.top.title(f"设置剪辑时间 — {os.path.basename(video_path)}")
        self.top.transient(parent)
        self.top.geometry("760x640")
        # 居中显示在所属主窗口的正中（而非整个屏幕正中）
        self.top.update_idletasks()
        try:
            root = parent.winfo_toplevel()
            rw, rh = root.winfo_width(), root.winfo_height()
            rx, ry = root.winfo_rootx(), root.winfo_rooty()
            x = rx + max(0, (rw - 760) // 2)
            y = ry + max(0, (rh - 640) // 2)
            # 兜底：防止超出屏幕可视范围
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            x = max(0, min(x, sw - 760))
            y = max(0, min(y, sh - 640))
            self.top.geometry(f"760x640+{x}+{y}")
        except Exception:
            sw = self.top.winfo_screenwidth()
            sh = self.top.winfo_screenheight()
            self.top.geometry(f"760x640+{max(0, (sw - 760) // 2)}"
                              f"+{max(0, (sh - 640) // 2)}")
        try:
            self.top.grab_set()
        except Exception:
            pass

        # ---- 播放器 ----
        self.player = EmbeddedPlayer(self.top, on_time=self._on_time,
                                     on_state=self._on_state)
        self.player._canvas.configure(height=300)          # noqa: SLF001
        self.ok = self.player.load(video_path)
        self.duration = float(getattr(self.player, 'duration', 0.0) or 0.0)

        # ---- 双滑块 ----
        self.trim_bar = TrimRangeBar(self.top, on_select=self._on_slider_select,
                                     on_drag=self._on_slider_drag,
                                     on_seek=self._on_seek)
        self.trim_bar.pack(fill=tk.X, padx=8, pady=(6, 0))

        # ---- 秒级微调行（位于滑块下方、时间框上方，点击即定位画面） ----
        ctrl = ttk.Frame(self.top)
        ctrl.pack(fill=tk.X, padx=8, pady=(2, 0))
        ttk.Button(ctrl, text="« 5秒", width=7,
                   command=lambda: self._nudge_sec(-5)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="« 1秒", width=7,
                   command=lambda: self._nudge_sec(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="1秒 »", width=7,
                   command=lambda: self._nudge_sec(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="5秒 »", width=7,
                   command=lambda: self._nudge_sec(5)).pack(side=tk.LEFT, padx=2)
        ttk.Label(ctrl, text="（按当前选中的时间框微调，并同步画面）",
                  foreground="#777").pack(side=tk.LEFT, padx=(8, 0))
        # 播放时间显示（当前时间/总时间，格式与时间文本框一致）
        self.time_label_var = tk.StringVar(
            value=f"{format_clock(0.0)} / {format_clock(self.duration)}")
        ttk.Label(ctrl, textvariable=self.time_label_var, width=26,
                  anchor=tk.E, foreground="#333").pack(side=tk.RIGHT, padx=(8, 0))

        # ---- 时间行 ----
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.precision_var = tk.StringVar(value="0.1S")
        self._build_time_rows()

        # ---- 操作标记（仅当关联了视频条目时显示：标记该视频需剪辑 / 已有字幕）----
        if self.item is not None:
            markf = ttk.Frame(self.top)
            markf.pack(fill=tk.X, padx=8, pady=(6, 0))
            ttk.Label(markf, text="操作标记：", foreground="#555").pack(side=tk.LEFT)
            self._mark_trim_btn = ttk.Button(
                markf, text="✂ 需剪辑", width=12,
                command=lambda: self._dlg_toggle_mark("need_trim"))
            self._mark_trim_btn.pack(side=tk.LEFT, padx=3)
            self._mark_sub_btn = ttk.Button(
                markf, text="📝 有字幕", width=12,
                command=lambda: self._dlg_toggle_mark("has_subtitle"))
            self._mark_sub_btn.pack(side=tk.LEFT, padx=3)
            self._refresh_dlg_marks()

        # ---- 提示与按钮 ----
        ttk.Label(self.top, text="点击滑块选中（蓝/红高亮），再用 −/+ 微调；"
                                 "拖动滑块画面跟随；点轨道可定位播放位置。",
                  foreground="#555").pack(anchor=tk.W, padx=8, pady=(8, 0))

        bar = ttk.Frame(self.top)
        bar.pack(fill=tk.X, padx=8, pady=10)
        ttk.Button(bar, text="⏯ 播放/暂停", command=self._toggle_play).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="清除自定义时间", command=self._clear_custom).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="确定", command=self._ok, width=10).pack(side=tk.RIGHT, padx=3)
        ttk.Button(bar, text="取消", command=self._cancel, width=10).pack(side=tk.RIGHT, padx=3)

        # 初值
        s = max(0.0, float(start or 0.0))
        e = float(end or 0.0) or self.duration
        if e <= s:
            e = self.duration
        self.start_var.set(format_clock(s))
        self.end_var.set(format_clock(e))
        self._refresh_bar()

        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        # 空格键：播放/暂停。用 bind_all 在其它绑定之前拦截，返回 "break"
        # 可阻止空格落入时间框/按钮等（时间框本就不接受空格），无论焦点在哪个控件，
        # 只要本窗口处于激活状态即可控制播放。
        self.top.bind_all("<space>", self._on_space)
        self.top.bind_all("<KP_Space>", self._on_space)
        self.top.bind("<Destroy>", self._on_destroy)
        self.top.wait_window()

    def _on_space(self, event=None):
        """空格键：播放/暂停（窗口激活即可，焦点在视频/时间框/滑块均可）"""
        try:
            self._toggle_play()
        except Exception:
            pass
        return "break"

    def _on_destroy(self, event=None):
        """窗口销毁时解除全局空格绑定，避免影响其它界面

        注意：<Destroy> 会随子控件销毁而冒泡，必须确认事件源是本窗口本身，
        否则播放器画布等子控件销毁时会误解除绑定。
        """
        if event is not None and getattr(event, 'widget', None) is not self.top:
            return
        try:
            self.top.unbind_all("<space>")
            self.top.unbind_all("<KP_Space>")
        except Exception:
            pass

    # ---------------- UI ----------------
    def _build_time_rows(self):
        for which, text in (('start', "开始时间"), ('end', "结束时间")):
            row = ttk.Frame(self.top)
            row.pack(fill=tk.X, padx=8, pady=3)
            ttk.Label(row, text=text, width=9).pack(side=tk.LEFT)
            var = self.start_var if which == 'start' else self.end_var
            entry = TimeEntry(row, textvariable=var, width=13)
            entry.pack(side=tk.LEFT)
            entry.bind('<FocusIn>', lambda e, w=which: self._set_active(w))
            ttk.Button(row, text="−", width=3,
                       command=lambda w=which: self._nudge(w, -1)).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="+", width=3,
                       command=lambda w=which: self._nudge(w, 1)).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="设为当前", width=8,
                       command=lambda w=which: self._set_from_player(w)).pack(side=tk.LEFT, padx=6)
            pos = 'first' if which == 'start' else 'last'
            label = "移到最前" if which == 'start' else "移到最后"
            ttk.Button(row, text=label, width=8,
                       command=lambda w=which, p=pos: self._move_to(w, p)).pack(side=tk.LEFT, padx=2)
            if which == 'start':
                ttk.Label(row, text="微调步长:").pack(side=tk.RIGHT, padx=(0, 4))
                ttk.Combobox(row, textvariable=self.precision_var, width=6,
                             values=list(TRIM_PRECISIONS.keys()),
                             state="readonly").pack(side=tk.RIGHT)

    # ---------------- 交互 ----------------
    def _dlg_toggle_mark(self, attr: str):
        """切换当前视频的“需剪辑 / 有字幕”标记（直接改在关联 item 上）"""
        if self.item is None:
            return
        setattr(self.item, attr, not bool(getattr(self.item, attr)))
        self._refresh_dlg_marks()

    def _refresh_dlg_marks(self):
        """按钮文字带 ✓ 表示该标记已打上，便于一眼看清当前状态"""
        if self.item is None:
            return
        self._mark_trim_btn.configure(
            text="✓ 需剪辑" if self.item.need_trim else "✂ 需剪辑")
        self._mark_sub_btn.configure(
            text="✓ 有字幕" if self.item.has_subtitle else "📝 有字幕")

    def _set_active(self, which: str):
        self._active = which
        self.trim_bar.set_active(which)

    def _on_slider_select(self, which: str):
        self._set_active(which)

    def _on_slider_drag(self, which: str, t: float):
        self._set_active(which)
        self._set_time(which, t)
        try:
            self.player.seek(t)
        except Exception:
            pass

    def _on_seek(self, t: float):
        try:
            self.player.seek(t)
        except Exception:
            pass

    def _on_time(self, t: float):
        self.trim_bar.set_position(t)
        try:
            self.time_label_var.set(
                f"{format_clock(t)} / {format_clock(self.duration)}")
        except Exception:
            pass

    def _on_state(self, playing: bool):
        pass

    def _toggle_play(self):
        try:
            if self.player.playing:
                self.player.pause()
            else:
                self.player.play()
        except Exception:
            pass

    def _precision(self) -> float:
        return TRIM_PRECISIONS.get(self.precision_var.get(), 0.1)

    def _get_time(self, which: str) -> float:
        raw = (self.start_var if which == 'start' else self.end_var).get()
        v = parse_clock(raw)
        return 0.0 if v is None else float(v)

    def _set_time(self, which: str, t: float):
        t = max(0.0, min(float(t), self.duration or float(t)))
        (self.start_var if which == 'start' else self.end_var).set(format_clock(t))
        self._clamp()
        self._refresh_bar()

    def _nudge(self, which: str, direction: int):
        self._set_active(which)
        t = self._get_time(which) + direction * self._precision()
        self._set_time(which, t)
        # 让播放画面跟随（修复原 -/+ 按钮只改数值不定位画面的问题）
        try:
            self.player.seek(t)
        except Exception:
            pass

    def _nudge_sec(self, delta: float):
        """秒级微调（±1/±5 秒）：同步更新数值、滑块与播放画面"""
        which = self._active
        self._set_active(which)
        t = self._get_time(which) + delta
        self._set_time(which, t)
        try:
            self.player.seek(t)
        except Exception:
            pass

    def _set_from_player(self, which: str):
        self._set_active(which)
        self._set_time(which, float(getattr(self.player, 'current_time', 0.0) or 0.0))

    def _move_to(self, which: str, position: str):
        self._set_active(which)
        self._set_time(which, 0.0 if position == 'first' else self.duration)

    def _clamp(self):
        s, e = self._get_time('start'), self._get_time('end')
        if e < s:
            if self._active == 'start':
                self.start_var.set(format_clock(e))
            else:
                self.end_var.set(format_clock(s))

    def _refresh_bar(self):
        self.trim_bar.set_values(self.duration, self._get_time('start'),
                                 self._get_time('end'), self._active,
                                 getattr(self.player, 'current_time', 0.0))

    def _ok(self):
        self._clamp()
        self.result = (self._get_time('start'), self._get_time('end'), True)
        self._close()

    def _clear_custom(self):
        self.result = (0.0, 0.0, False)
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            # release() = 暂停 + 释放 cv2.VideoCapture 句柄；
            # 只调 stop() 会让视频文件一直被 Python 占用，
            # 导致资源管理器中无法移动/删除该文件
            self.player.release()
        except Exception:
            pass
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()


# ======================================================================
# 工作流选项卡
# ======================================================================
class WorkflowTab:
    CONFIG_SECTION = CONFIG_SECTION

    def __init__(self, parent_notebook, controller=None, config_manager=None):
        self.parent = parent_notebook
        self.controller = controller
        self.config_manager = config_manager or ConfigManager()

        self.items: List[WorkflowItem] = []
        self.job = WorkflowJob()
        self.tasks: List[WorkflowTask] = []      # 任务清单（多批视频各自不同流程）
        self.executor: Optional[WorkflowExecutor] = None
        self._items_by_uid: Dict[str, WorkflowItem] = {}

        self.frame = ttk.Frame(parent_notebook)
        self._build_ui()
        self._load_config()
        # 等主窗口就绪后再注册拖放（tkdnd 需要主窗口已创建）
        self.frame.after(300, self._enable_dnd)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self):
        # ---------- ① 视频来源（不再用“文件夹输入框”选择，改为按钮式） ----------
        src = ttk.LabelFrame(self.frame, text="① 视频来源（进入流程时自动分配唯一标识 UID；"
                                             "双击行设置剪辑时间，双击 UID 列修改 UID）",
                             padding=6)
        src.pack(fill=tk.X, padx=8, pady=(8, 4))
        # 取消原先“文件夹输入框 + 浏览 + 扫描”的文件域选择方式，
        # 改为点击按钮：①「添加文件」多文件选择；②「扫描文件夹」弹窗选目录
        ttk.Button(src, text="📂 添加文件（可多选）", width=18,
                   command=self._add_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(src, text="📁 扫描文件夹", width=12,
                   command=self._scan).pack(side=tk.LEFT, padx=2)
        self.recursive_var = tk.BooleanVar(value=True)
        self.only_video_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(src, text="含子文件夹", variable=self.recursive_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(src, text="仅视频文件", variable=self.only_video_var).pack(side=tk.LEFT, padx=4)

        # ---------- 主体左右分栏 ----------
        paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=4)
        # 左列上下分栏：上=视频清单，下=任务清单
        lv = ttk.PanedWindow(left, orient=tk.VERTICAL)
        lv.pack(fill=tk.BOTH, expand=True)
        top_f = ttk.Frame(lv)
        bot_f = ttk.Frame(lv)
        lv.add(top_f, weight=3)
        lv.add(bot_f, weight=1)      # 任务清单高度减半（原为 2），腾出空间给日志
        self._build_item_list(top_f)
        self._build_task_list(bot_f)

        right = ttk.Frame(paned)
        paned.add(right, weight=6)
        self._build_flow_area(right)

        # 初始分隔位置：让右侧参数区有足够宽度（约 55%），按钮不被遮挡
        self.frame.after_idle(lambda: self._set_sash(paned))

        # ---------- 日志 ----------
        logf = ttk.LabelFrame(self.frame, text="运行日志", padding=4)
        logf.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
        self.log_text = tk.Text(logf, height=14, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=sb.set)
        ttk.Button(self.frame, text="清空日志", width=10,
                   command=lambda: self.log_text.delete('1.0', tk.END)).pack(
            side=tk.RIGHT, padx=8, pady=(0, 8))

    @staticmethod
    def _set_sash(paned: ttk.PanedWindow):
        """把左右分栏的分隔线放到约 45% 处，保证右侧参数区完整可见"""
        try:
            total = paned.winfo_width()
            if total > 100:
                paned.sashpos(0, int(total * 0.45))
        except Exception:
            pass

    def _build_item_list(self, parent):
        lf = ttk.LabelFrame(parent, text="② 视频清单（UID 全程绑定；双击行设置剪辑时间，"
                                         "双击 UID 列修改 UID）", padding=4)
        lf.pack(fill=tk.BOTH, expand=True)

        # 操作按钮放在列表下方（先 pack 到底部占位，避免被列表挤到右侧）
        bar = ttk.Frame(lf)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Button(bar, text="☑ 选中所有视频", width=13, command=self._select_all_items).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="✂ 需剪辑", width=10, command=lambda: self._toggle_mark("need_trim")).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="📝 有字幕", width=10, command=lambda: self._toggle_mark("has_subtitle")).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="✂ 设置剪辑时间", width=15, command=self._edit_trim).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="✏ 修改 UID", width=12, command=self._edit_uid).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="清除自定义时间", width=15, command=self._clear_trim).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="移除选中", width=11, command=self._remove_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="⬇ 导出", width=9, command=self._export_marks).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="⬆ 导入", width=9, command=self._import_marks).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="清空", width=9, command=self._clear_items).pack(side=tk.LEFT, padx=2)

        # 列表 + 纵向滚动条
        listf = ttk.Frame(lf)
        listf.pack(fill=tk.BOTH, expand=True)
        # 列顺序：UID → 标记（图标列，置于“当前文件”前）→ 当前文件 → … → 状态
        cols = ("uid", "mark", "file", "dir", "trim", "rate", "res", "status")
        heads = ("UID", "标记", "当前文件", "所在文件夹", "剪辑时间", "数据速率", "分辨率", "状态")
        widths = (100, 70, 160, 150, 130, 95, 95, 85)
        self.tree = ttk.Treeview(listf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=tk.W, stretch=(c in ("file", "dir", "mark")))
        vsb = ttk.Scrollbar(listf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        # 分辨率着色图例：标准分辨率绿色，特殊/裁剪分辨率红色
        legend = ttk.Frame(lf)
        legend.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))
        ttk.Label(legend, text="标记：", anchor=tk.W).pack(side=tk.LEFT)
        ttk.Label(legend, text="✂ 需剪辑", foreground="#c62828").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(legend, text="📝 有字幕", foreground="#1565c0").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(legend, text="　分辨率着色：", anchor=tk.W).pack(side=tk.LEFT)
        ttk.Label(legend, text="■ 标准", foreground="#1b6b2f").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(legend, text="■ 特殊/裁剪", foreground="#c62828").pack(side=tk.LEFT)
        # 已加入任务清单的视频：高亮标记（浅绿底），避免重复选择
        #   仅用背景高亮，前景留给“分辨率”着色，二者互不干扰
        self.tree.tag_configure("added", background="#e3f2e1")
        # 分辨率着色：标准分辨率（如 1920x1080 / 1280x720）绿色，
        #            裁剪过的特殊分辨率（如 960x1264 / 1440x1080）红色
        self.tree.tag_configure("res_std", foreground="#1b6b2f")
        self.tree.tag_configure("res_special", foreground="#c62828")
        self.tree.bind("<Double-1>", self._on_item_double_click)
        # 右键菜单：剪辑时间 / 修改 UID / 打开文件夹 / 移除
        self.item_menu = tk.Menu(self.tree, tearoff=0)
        self.item_menu.add_command(label="✂ 设置剪辑时间", command=self._edit_trim)
        self.item_menu.add_command(label="✏ 修改 UID（人工修正后自动学习）",
                                   command=self._edit_uid)
        self.item_menu.add_separator()
        self.item_menu.add_command(label="📂 打开所在文件夹", command=self._open_folder)
        self.item_menu.add_command(label="移除选中", command=self._remove_selected)
        self.tree.bind("<Button-3>", self._show_item_menu)

    def _on_item_double_click(self, event):
        # 双击 UID 列 → 修改 UID；双击其它列 → 设置剪辑时间
        try:
            col = self.tree.identify_column(event.x)
        except Exception:
            col = ""
        if col == "#1":
            self._edit_uid()
        else:
            self._edit_trim()

    def _show_item_menu(self, event):
        """右键：若点击行不在选区内则改为选中该行，再弹出菜单"""
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
                self.tree.see(row)
            try:
                self.item_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.item_menu.grab_release()

    # ------------------------------------------------------------------
    # 任务清单
    # ------------------------------------------------------------------
    def _build_task_list(self, parent):
        lf = ttk.LabelFrame(parent, text="⑥ 任务清单（按任务顺序逐个执行；同一任务内多个视频共用一套流程）",
                             padding=4)
        lf.pack(fill=tk.BOTH, expand=True)

        # 操作按钮放在列表下方（注：「添加选中视频为任务」已移到⑤无人值守运行框）
        bar = ttk.Frame(lf)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Button(bar, text="↑ 上移", width=8,
                   command=lambda: self._move_task(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="↓ 下移", width=8,
                   command=lambda: self._move_task(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="✖ 删除", width=8,
                   command=self._remove_task).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="清空任务清单", width=12,
                   command=self._clear_tasks).pack(side=tk.LEFT, padx=2)

        listf = ttk.Frame(lf)
        listf.pack(fill=tk.BOTH, expand=True)
        cols = ("idx", "name", "count", "summary")
        heads = ("#", "任务名", "视频数", "流程摘要")
        widths = (28, 80, 52, 260)
        self.task_tree = ttk.Treeview(listf, columns=cols, show="headings",
                                      selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.task_tree.heading(c, text=h)
            self.task_tree.column(c, width=w, anchor=tk.W,
                                  stretch=(c == "summary"))
        vsb = ttk.Scrollbar(listf, orient=tk.VERTICAL, command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=vsb.set)
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.task_tree.bind("<Double-1>", self._on_task_double_click)

    def _refresh_task_tree(self):
        self.task_tree.delete(*self.task_tree.get_children())
        for i, t in enumerate(self.tasks, 1):
            self.task_tree.insert("", tk.END, iid=str(i - 1),
                                  values=(i, t.name, len(t.file_paths), t.summary()))

    def _cur_task_index(self) -> Optional[int]:
        sel = self.task_tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _add_task(self):
        """把当前选中的视频 + 右侧当前流程，作为一个新任务加入任务清单"""
        if self.executor and self.executor.is_alive():
            show_toast(self.frame, "工作流执行中，暂不能修改任务清单", "warning")
            return
        items = self._selected_items()
        if not items:
            show_toast(self.frame, "请先在上方视频清单选中视频（部分或全部）", "warning")
            return
        self._apply_params()      # 确保当前右侧参数已写回 self.job
        overrides = {}
        for it in items:
            overrides[it.origin] = {
                "uid": it.uid,
                "trim_start": it.trim_start,
                "trim_end": it.trim_end,
                "use_custom_trim": it.use_custom_trim,
            }
        job_copy = WorkflowJob.from_dict(self.job.to_dict())
        job_copy.finish_action = "none"     # 单个任务不触发关机/睡眠，统一在最后处理
        name = f"任务{len(self.tasks) + 1}"
        task = WorkflowTask(name=name,
                            file_paths=[it.origin for it in items],
                            overrides=overrides, job=job_copy)
        self.tasks.append(task)
        self._refresh_task_tree()
        self._refresh_tree()      # 同步高亮清单中已加入任务的视频
        self._save_config()
        self._log(f"[任务清单] 已添加 {name}：{len(items)} 个视频；"
                  f"流程：{job_copy.describe()}")
        show_toast(self.frame, f"已添加{name}（{len(items)} 个视频）", "success")

    def _move_task(self, delta: int):
        idx = self._cur_task_index()
        if idx is None:
            return
        new = idx + delta
        if not (0 <= new < len(self.tasks)):
            return
        self.tasks[idx], self.tasks[new] = self.tasks[new], self.tasks[idx]
        self._refresh_task_tree()
        self.task_tree.selection_set(str(new))

    def _remove_task(self):
        idx = self._cur_task_index()
        if idx is None:
            return
        self.tasks.pop(idx)
        self._refresh_task_tree()
        self._refresh_tree()      # 取消这些视频的高亮标记
        self._save_config()       # 同步配置文件（手动移除的任务不再保留）

    def _clear_tasks(self):
        self.tasks = []
        self._refresh_task_tree()
        self._refresh_tree()      # 取消全部视频的高亮标记
        self._save_config()       # 同步配置文件（清空任务清单后不再保留）

    def _on_task_double_click(self, event):
        """双击任务 → 把它的流程载回右侧参数区，便于检查/修改后重新添加"""
        idx = self._cur_task_index()
        if idx is None:
            return
        task = self.tasks[idx]
        self.job = WorkflowJob.from_dict(task.job.to_dict())
        self._refresh_step_list()
        self._log(f"[任务清单] 已载入任务「{task.name}」的流程到右侧"
                  f"（可修改后重新“添加选中视频为任务”）")

    def _build_flow_area(self, parent):
        # ---- 流程步骤 ----
        lf = ttk.LabelFrame(parent, text="③ 流程步骤（可增删、上下移动调整执行顺序）", padding=4)
        lf.pack(fill=tk.X, padx=(4, 0), pady=(0, 4))
        sf = ttk.Frame(lf)
        sf.pack(fill=tk.X, padx=2, pady=2)
        self.step_list = tk.Listbox(sf, height=6, font=("Microsoft YaHei UI", 9),
                                    exportselection=False)
        hsb = ttk.Scrollbar(sf, orient=tk.HORIZONTAL, command=self.step_list.xview)
        self.step_list.configure(xscrollcommand=hsb.set)
        self.step_list.pack(side=tk.TOP, fill=tk.X)
        hsb.pack(side=tk.TOP, fill=tk.X)
        self.step_list.bind("<<ListboxSelect>>", lambda e: self._on_select_step())

        bar = ttk.Frame(lf)
        bar.pack(fill=tk.X, pady=(2, 4))
        for t, label in ((STEP_MOVE, "➕ 移动"), (STEP_TRANSCODE, "➕ 剪辑转码"),
                         (STEP_SUBTITLE, "➕ 生成字幕"), (STEP_RENAME, "➕ 修改名称")):
            ttk.Button(bar, text=label, width=11,
                       command=lambda tt=t: self._add_step(tt)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="↑ 上移", width=7, command=lambda: self._move_step(-1)).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="↓ 下移", width=7, command=lambda: self._move_step(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="✖ 删除", width=7, command=self._remove_step).pack(side=tk.LEFT, padx=6)
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="启用该步骤", variable=self.enabled_var,
                        command=self._toggle_enabled).pack(side=tk.LEFT, padx=6)

        # ---- 参数区（固定高度 + 垂直滚动条：内容超出时滚动查看，不撑大窗口）----
        pf = ttk.LabelFrame(parent, text="④ 选中步骤的参数（无人值守前预先设好）", padding=6)
        pf.pack(fill=tk.X, padx=(4, 0), pady=(0, 4))
        _pc = ttk.Frame(pf)
        _pc.pack(fill=tk.X)
        self._param_canvas = tk.Canvas(_pc, highlightthickness=0, height=PARAM_AREA_H)
        self._param_vsb = ttk.Scrollbar(_pc, orient=tk.VERTICAL,
                                        command=self._param_canvas.yview)
        self._param_canvas.configure(yscrollcommand=self._param_vsb.set)
        self._param_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._param_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.param_host = ttk.Frame(self._param_canvas)
        self._param_win = self._param_canvas.create_window(
            (0, 0), window=self.param_host, anchor='nw')
        # 内容高度变化 → 更新滚动范围；画布宽度变化 → 参数区跟随变宽（各行才能 fill=X）
        self.param_host.bind(
            '<Configure>',
            lambda e: self._param_canvas.configure(
                scrollregion=self._param_canvas.bbox('all')))
        self._param_canvas.bind('<Configure>', self._on_param_canvas_configure)
        self._param_frames: Dict[str, ttk.Frame] = {}
        self._build_move_params()
        self._build_transcode_params()
        self._build_subtitle_params()
        self._build_rename_params()
        # 鼠标滚轮：递归绑定到画布与参数区内所有控件（只影响本区域，不动全局绑定）
        self._bind_wheel(self._param_canvas, self._param_canvas)
        self._bind_wheel(self.param_host, self._param_canvas)

        # ---- 变量提示行（字幕步骤不显示；点“变量用法”展开详细说明） ----
        hint_wrap = ttk.Frame(pf)
        self.var_hint_wrap = hint_wrap
        hint_row = ttk.Frame(hint_wrap)
        hint_row.pack(fill=tk.X)
        self.var_toggle_btn = ttk.Button(hint_row, text="变量用法 ▼", width=11,
                                         command=self._toggle_var_help)
        self.var_toggle_btn.pack(side=tk.LEFT)
        ttk.Label(hint_row, foreground="#555",
                  text="提示：目标文件夹与文件名支持变量 {uid} {stem} {name} "
                       "{src_dir} {cur_dir} {date} {ext}"
                  ).pack(side=tk.LEFT, padx=(4, 0))
        self.var_help = tk.Text(hint_wrap, height=13, wrap=tk.WORD,
                                font=("Consolas", 9), background="#fafafa",
                                relief=tk.FLAT)
        self.var_help.insert("1.0", VAR_HELP_TEXT)
        self.var_help.configure(state="disabled")   # 只读
        hint_wrap.pack(fill=tk.X, pady=(4, 0))      # 默认显示，字幕步骤时自动隐藏

        # ---- 运行 ----
        rf = ttk.LabelFrame(parent, text="⑤ 无人值守运行", padding=6)
        rf.pack(fill=tk.X, padx=(4, 0))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.pct_var = tk.StringVar(value="0%")
        pbar_row = ttk.Frame(rf)
        pbar_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Progressbar(pbar_row, variable=self.progress_var, maximum=100).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(pbar_row, textvariable=self.pct_var, width=6,
                  anchor=tk.E).pack(side=tk.LEFT, padx=(6, 0))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(rf, textvariable=self.status_var).pack(anchor=tk.W)

        # 并行线程数（多线程同时处理多个视频/任务）
        trow = ttk.Frame(rf)
        trow.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(trow, text="并行线程数:").pack(side=tk.LEFT)
        self.threads_var = tk.IntVar(value=1)
        ttk.Spinbox(trow, from_=1, to=8, increment=1, textvariable=self.threads_var,
                    width=6, command=self._on_threads_change).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(trow, text="（1=串行；>1 时多个视频/任务同时处理；"
                              "GPU 密集的“字幕”步骤并行可能爆显存，请酌情调小）",
                  foreground="#777").pack(side=tk.LEFT, padx=(6, 0))

        # 「添加选中视频为任务」：先设好上方流程，再把选中视频加入任务清单，最后点开始执行
        row3 = ttk.Frame(rf)
        row3.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(row3, text="➕ 添加选中视频为任务", width=20,
                   command=self._add_task).pack(side=tk.LEFT, padx=2)
        ttk.Label(row3, text="（设置好流程后点击，把选中视频加入下方任务清单；"
                              "已加入的视频在清单中高亮标记）",
                  foreground="#777").pack(side=tk.LEFT, padx=(6, 0))
        
        row = ttk.Frame(rf)
        row.pack(fill=tk.X, pady=(4, 0))
        self.start_btn = ttk.Button(row, text="▶ 开始执行工作流", width=18, command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        self.pause_btn = ttk.Button(row, text="⏸ 暂停", width=10, command=self._toggle_pause,
                                    state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=2)
        self.stop_btn = ttk.Button(row, text="⏹ 停止", width=10, command=self._stop,
                                   state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="💾 保存流程", width=12, command=self._save_config).pack(side=tk.LEFT, padx=8)
        self.continue_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="出错时继续下一个",
                        variable=self.continue_var).pack(side=tk.LEFT, padx=8)

        row2 = ttk.Frame(rf)
        row2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row2, text="全部完成后:").pack(side=tk.LEFT)
        self.finish_var = tk.StringVar(value="无操作")
        # 实时持有器：任务执行中可随时修改“完成后操作”，executor 在全部完成时
        # 才读取此最终值（而非启动时刻的快照）——运行中改为“无操作”即不触发关机/睡眠
        self._finish_action_live = {"value": "none"}
        combo = ttk.Combobox(row2, textvariable=self.finish_var, width=10,
                             state="readonly", values=list(FINISH_LABELS.values()))
        combo.pack(side=tk.LEFT, padx=(2, 0))
        combo.bind("<<ComboboxSelected>>",
                   lambda e: self._sync_finish_action())
        ttk.Label(row2, text="（无操作 / 进入睡眠 / 关机；运行中可随时修改，以完成前一刻为准）",
                  foreground="#777").pack(side=tk.LEFT, padx=(8, 0))



    # ---------- 参数面板：移动 ----------
    def _build_move_params(self):
        f = ttk.Frame(self.param_host)
        self._param_frames[STEP_MOVE] = f
        self.mv_target = tk.StringVar()
        self.mv_comp = tk.BooleanVar(value=True)
        self.mv_uidsub = tk.BooleanVar(value=False)
        r = ttk.Frame(f); r.pack(fill=tk.X, pady=2)
        ttk.Label(r, text="目标文件夹:", width=12).pack(side=tk.LEFT)
        ttk.Entry(r, textvariable=self.mv_target).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(r, text="浏览...", width=8,
                   command=lambda: self._pick_dir(self.mv_target)).pack(side=tk.LEFT)
        # 两个复选框各占一行：合并到一行会超出宽度，拆行后更清爽
        r2 = ttk.Frame(f); r2.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(r2, text="同时移动同名附属文件（字幕/封面等）",
                        variable=self.mv_comp).pack(side=tk.LEFT, padx=(12, 0))
        r3 = ttk.Frame(f); r3.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(r3, text="目标下再建 {uid} 子文件夹",
                        variable=self.mv_uidsub).pack(side=tk.LEFT, padx=(12, 0))
        for v in (self.mv_target, self.mv_comp, self.mv_uidsub):
            v.trace_add('write', lambda *a: self._apply_params())

    @staticmethod
    def _hint(parent, text: str):
        """灰色提示：自动换行，避免把整行撑宽"""
        ttk.Label(parent, text=text, foreground="#777", justify=tk.LEFT,
                  wraplength=HINT_WRAP).pack(anchor=tk.W, padx=(12, 0), pady=(0, 2))

    # ---------- 参数面板：转码 ----------
    def _build_transcode_params(self):
        f = ttk.Frame(self.param_host)
        self._param_frames[STEP_TRANSCODE] = f
        self.tc_trim = tk.StringVar(value="不裁剪（整段输出）")
        self.tc_start = tk.StringVar(value="00:00:00.00")
        self.tc_end = tk.StringVar(value="00:00:00.00")
        self.tc_head = tk.DoubleVar(value=0.0)
        self.tc_tail = tk.DoubleVar(value=0.0)
        self.tc_mode = tk.StringVar(value="预设固定分辨率")
        self.tc_preset = tk.StringVar(value="1280x720")
        self.tc_custom_w = tk.StringVar(value="自动")
        self.tc_custom_h = tk.StringVar(value="自动")
        self.tc_fixed_side = tk.StringVar(value="宽")
        self.tc_fixed_val = tk.StringVar(value="1280")
        self.tc_edge = tk.StringVar(value="长边")
        self.tc_edge_val = tk.StringVar(value="1280")
        self.tc_aspect = tk.StringVar(value="保持原比例")
        self.tc_bitrate = tk.IntVar(value=2500)
        self.tc_keep_br = tk.BooleanVar(value=False)   # 不修改码率：沿用原片码率
        self.tc_br_below_keep = tk.BooleanVar(value=False)  # 低于设置值不修改：原片码率低于设置值时保持原片
        self.tc_encoder = tk.StringVar(value=ENCODER_LABELS["auto"])
        self.tc_save_src = tk.BooleanVar(value=True)
        self.tc_save_dir = tk.StringVar()
        self.tc_ext = tk.StringVar(value="mp4")
        self.tc_sub = tk.StringVar(value="不添加字幕")
        self.tc_del_src = tk.BooleanVar(value=False)
        self.tc_del_srt = tk.BooleanVar(value=False)
        self.tc_keepq = tk.BooleanVar(value=False)   # 流拷贝剪辑：不改分辨率/码率
        self.tc_merge = tk.BooleanVar(value=False)   # 拼接为一个视频
        self.tc_auto_orient = tk.BooleanVar(value=True)      # 竖屏自动适配分辨率
        self.tc_portrait_res = tk.StringVar(value="自动(按源比例)")  # 竖屏目标分辨率

        def row(label, widget_pack):
            r = ttk.Frame(f); r.pack(fill=tk.X, pady=2)
            if label:
                ttk.Label(r, text=label, width=12).pack(side=tk.LEFT)
            widget_pack(r)
            return r

        row("剪辑时间:", lambda r: ttk.Combobox(
            r, textvariable=self.tc_trim, width=24, state="readonly",
            values=list(TRIM_MODES.values())).pack(side=tk.LEFT))

        self._tc_time_row = row("起止时间:", lambda r: (
            ttk.Label(r, text="起").pack(side=tk.LEFT, padx=(0, 2)),
            ttk.Entry(r, textvariable=self.tc_start, width=12).pack(side=tk.LEFT),
            ttk.Label(r, text="止").pack(side=tk.LEFT, padx=(10, 2)),
            ttk.Entry(r, textvariable=self.tc_end, width=12).pack(side=tk.LEFT)))
        self._hint(f, "（仅“统一时间段”剪辑模式生效）")

        row("掐头去尾:", lambda r: (
            ttk.Label(r, text="去片头(s)").pack(side=tk.LEFT, padx=(0, 2)),
            ttk.Entry(r, textvariable=self.tc_head, width=7).pack(side=tk.LEFT),
            ttk.Label(r, text="去片尾(s)").pack(side=tk.LEFT, padx=(10, 2)),
            ttk.Entry(r, textvariable=self.tc_tail, width=7).pack(side=tk.LEFT)))

        # ---- 分辨率设置：四种模式，按所选模式动态显示对应子行 ----
        self._tc_mode_row = row("分辨率模式:", lambda r: (
            ttk.Combobox(r, textvariable=self.tc_mode, width=14, state="readonly",
                         values=RES_MODES).pack(side=tk.LEFT)))
        # 子行容器：同一时刻只显示当前模式对应的那一行
        self._tc_res_sub = ttk.Frame(f)
        self._tc_res_sub.pack(fill=tk.X, pady=2)

        def _sub(build):
            rf = ttk.Frame(self._tc_res_sub)
            build(rf)
            rf.pack(fill=tk.X, pady=2)
            return rf

        # ① 预设固定分辨率（含竖屏自动适配）
        self._tc_preset_row = _sub(lambda r: (
            ttk.Label(r, text="分辨率").pack(side=tk.LEFT, padx=(12, 2)),
            ttk.Combobox(r, textvariable=self.tc_preset, width=12, state="readonly",
                         values=RES_PRESETS).pack(side=tk.LEFT)))
        # ② 自定义宽高（宽或高可填“自动”，另一边按原片比例算）
        self._tc_custom_row = _sub(lambda r: (
            ttk.Label(r, text="宽").pack(side=tk.LEFT, padx=(12, 2)),
            ttk.Entry(r, textvariable=self.tc_custom_w, width=7).pack(side=tk.LEFT),
            ttk.Label(r, text="高").pack(side=tk.LEFT, padx=(8, 2)),
            ttk.Entry(r, textvariable=self.tc_custom_h, width=7).pack(side=tk.LEFT),
            ttk.Label(r, text="（宽或高填“自动”则按原片比例算另一边，如 高=1280 宽=自动）")
                .pack(side=tk.LEFT, padx=(8, 0))))
        # ③ 固定宽或高（另一边按原片宽高比自动匹配）
        self._tc_fixed_row = _sub(lambda r: (
            ttk.Combobox(r, textvariable=self.tc_fixed_side, width=6, state="readonly",
                         values=["宽", "高"]).pack(side=tk.LEFT, padx=(12, 0)),
            ttk.Label(r, text="=").pack(side=tk.LEFT, padx=2),
            ttk.Entry(r, textvariable=self.tc_fixed_val, width=7).pack(side=tk.LEFT),
            ttk.Label(r, text="（另一边按原片宽高比自动匹配）").pack(side=tk.LEFT, padx=(8, 0))))
        # ④ 固定长边/短边（另一边自动；长/短边随原片横竖自动取宽或高，已含竖屏适配）
        self._tc_edge_row = _sub(lambda r: (
            ttk.Combobox(r, textvariable=self.tc_edge, width=6, state="readonly",
                         values=["长边", "短边"]).pack(side=tk.LEFT, padx=(12, 0)),
            ttk.Label(r, text="=").pack(side=tk.LEFT, padx=2),
            ttk.Entry(r, textvariable=self.tc_edge_val, width=7).pack(side=tk.LEFT),
            ttk.Label(r, text="（另一边自动匹配；长/短边随原片方向自动取宽或高，已含竖屏适配）")
                .pack(side=tk.LEFT, padx=(8, 0))))
        self._tc_aspect_row = row("比例:", lambda r: (
            ttk.Combobox(r, textvariable=self.tc_aspect, width=10, state="readonly",
                         values=ASPECTS).pack(side=tk.LEFT)))
        self.tc_mode.trace_add('write', lambda *a: self._update_res_state())
        self._hint(f, "四种模式：①预设固定分辨率（含竖屏自动适配）②自定义宽高"
                      "③固定宽或高、另一边自动 ④固定长边/短边、另一边自动（自动含竖屏适配）。"
                      "原片不足目标尺寸处自动补黑边，画面不变形。")
        self._tc_br_row = row("", self._build_br_row)
        self._hint(f, "勾选“不修改码率”后，每个视频按自身原片码率编码"
                      "（改分辨率/比例、起止剪辑、添加字幕时都保持原码率），"
                      "无需因各视频码率不同而逐个调整；读不到原片码率时回退为上方数值。\n"
                      "勾选“低于设置值不修改”后：原片码率高于设置值时降到设置值（控制体积/统一规格），"
                      "原片码率低于设置值时保持原片码率（避免低码率视频被强行拉高、体积虚增）。"
                      "二者同时勾选时以“不修改码率”优先。")

        # 竖屏自动适配：横屏/竖屏混合时，竖屏源自动改用竖屏目标分辨率
        # （“固定长边/短边”模式已随原片方向自动适配，该项自动置灰、无需勾选）
        self._tc_orient_row = row("", lambda r: (
            ttk.Checkbutton(r, text="竖屏自动适配分辨率",
                            variable=self.tc_auto_orient).pack(side=tk.LEFT, padx=(0, 0)),
            ttk.Label(r, text="竖屏目标").pack(side=tk.LEFT, padx=(10, 2)),
            ttk.Combobox(r, textvariable=self.tc_portrait_res, width=10,
                         values=["自动(按源比例)", "720x1280", "720x1080"]).pack(side=tk.LEFT)))
        self._hint(f, "横屏和竖屏混在一起时勾选“竖屏自动适配分辨率”：横屏源按设定分辨率输出；"
                      "竖屏源自动改用“竖屏目标”分辨率。都按原比例缩放，不足处补黑边，不变形。"
                      "“固定长边/短边”模式已随原片方向自动适配，该选项自动置灰。实际输出尺寸见运行日志。")

        row("编码/容器:", lambda r: (
            ttk.Label(r, text="编码器").pack(side=tk.LEFT, padx=(0, 2)),
            ttk.Combobox(r, textvariable=self.tc_encoder, width=18, state="readonly",
                         values=list(ENCODER_LABELS.values())).pack(side=tk.LEFT),
            ttk.Label(r, text="容器").pack(side=tk.LEFT, padx=(10, 2)),
            ttk.Combobox(r, textvariable=self.tc_ext, width=6, state="readonly",
                         values=OUT_EXTS).pack(side=tk.LEFT)))

        # 流拷贝剪辑：仅按起止时间裁剪，视频/音频流原样拷贝，不改分辨率/码率/编码（无损）
        rkq = ttk.Frame(f); rkq.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(rkq, text="保留原画质（流拷贝剪辑，不改分辨率/码率）",
                        variable=self.tc_keepq).pack(side=tk.LEFT, padx=(12, 0))
        self._hint(f, "勾选后仅按起止时间/掐头去尾裁剪，画面质量与原片一致（裁剪精度为关键帧级，"
                      "非逐帧精确）")

        # 拼接为一个视频：同一任务内的多个视频按清单顺序合并成一个成片
        rm = ttk.Frame(f); rm.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(rm, text="🔗 拼接为一个视频（本任务多个视频按顺序合并）",
                        variable=self.tc_merge).pack(side=tk.LEFT, padx=(12, 0))
        self._hint(f, "勾选后，同一任务里的所有视频按顺序拼接成一个成片，输出名为“第一个视频名_合并.mp4”："
                      "每个视频仍保留各自设置的剪辑时间（双击视频行设置/掐头去尾），全部按上方统一参数重新"
                      "编码后无损拼接，（双击视频行设置/掐头去尾），全部按上方统一参数重新编码后无损拼接，"
                      "各视频的 srt 字幕会按拼接顺序整体对齐后封装进成片。")

        r = ttk.Frame(f); r.pack(fill=tk.X, pady=2)
        ttk.Label(r, text="输出位置:", width=12).pack(side=tk.LEFT)
        ttk.Radiobutton(r, text="当前文件所在文件夹", variable=self.tc_save_src,
                        value=True).pack(side=tk.LEFT)
        ttk.Radiobutton(r, text="指定文件夹:", variable=self.tc_save_src,
                        value=False).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(r, textvariable=self.tc_save_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(r, text="浏览...", width=8,
                   command=lambda: self._pick_dir(self.tc_save_dir)).pack(side=tk.LEFT)

        row("字幕:", lambda r: (
            ttk.Label(r, text="字幕").pack(side=tk.LEFT, padx=(0, 2)),
            ttk.Combobox(r, textvariable=self.tc_sub, width=24, state="readonly",
                         values=list(SUBTITLE_MODE_LABELS.values())).pack(side=tk.LEFT)))

        # 删除类选项单独一行（与字幕下拉合并会超宽）
        rd = ttk.Frame(f); rd.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(rd, text="转码后删除源文件",
                        variable=self.tc_del_src).pack(side=tk.LEFT, padx=(12, 0))
        self._tc_subfile_row = ttk.Checkbutton(
            rd, text="封装后删除原始字幕文件（.srt）", variable=self.tc_del_srt)
        self._tc_subfile_row.pack(side=tk.LEFT, padx=(10, 0))
        self._hint(f, "（“封装后删除原始字幕文件”仅软字幕模式生效）")

        for v in (self.tc_trim, self.tc_start, self.tc_end, self.tc_head, self.tc_tail,
                  self.tc_mode, self.tc_preset, self.tc_aspect,
                  self.tc_custom_w, self.tc_custom_h,
                  self.tc_fixed_side, self.tc_fixed_val, self.tc_edge, self.tc_edge_val,
                  self.tc_bitrate, self.tc_keep_br,
                  self.tc_encoder, self.tc_save_src, self.tc_save_dir, self.tc_ext,
                  self.tc_sub, self.tc_del_src, self.tc_del_srt, self.tc_keepq,
                  self.tc_merge, self.tc_auto_orient, self.tc_portrait_res):
            v.trace_add('write', lambda *a: self._apply_params())
        self._update_res_state()

        # 联动：剪辑模式变化 → 起止时间可用性；字幕方式/原画质变化 → 输出规格可用性
        self.tc_trim.trace_add('write', lambda *a: self._update_param_states())
        self.tc_sub.trace_add('write', lambda *a: self._update_param_states())
        self.tc_keepq.trace_add('write', lambda *a: self._update_param_states())
        self.tc_keep_br.trace_add('write', lambda *a: self._update_param_states())
        self.tc_br_below_keep.trace_add('write', lambda *a: self._update_param_states())
        self._update_param_states()

    def _build_br_row(self, r):
        """码率行：数值框 + “不修改码率” + “低于设置值不修改”两个复选框。"""
        ttk.Label(r, text="码率(kbps)", width=12).pack(side=tk.LEFT)
        self._tc_bitrate_spin_pack(r)
        ttk.Checkbutton(r, text="不修改码率（沿用原片码率）",
                        variable=self.tc_keep_br).pack(side=tk.LEFT, padx=(10, 0))
        self._tc_br_below_chk = ttk.Checkbutton(
            r, text="低于设置值不修改", variable=self.tc_br_below_keep)
        self._tc_br_below_chk.pack(side=tk.LEFT, padx=(10, 0))
        return r

    def _tc_bitrate_spin_pack(self, r):
        """码率输入框（保留引用，便于“不修改码率”时禁用）"""
        self._tc_bitrate_spin = ttk.Spinbox(r, from_=100, to=50000, increment=100,
                                            width=8, textvariable=self.tc_bitrate)
        self._tc_bitrate_spin.pack(side=tk.LEFT)
        return self._tc_bitrate_spin

    # ---------- 参数面板：修改名称 ----------
    def _build_rename_params(self):
        f = ttk.Frame(self.param_host)
        self._param_frames[STEP_RENAME] = f
        self.rn_name = tk.StringVar(value="{stem}")
        self.rn_comp = tk.BooleanVar(value=True)

        def row(label, widget_pack):
            r = ttk.Frame(f); r.pack(fill=tk.X, pady=2)
            if label:
                ttk.Label(r, text=label, width=12).pack(side=tk.LEFT)
            widget_pack(r)
            return r

        # 输入框自适应拉宽，变量提示放到下一行并自动换行，避免把整行撑宽
        row("文件名模板:", lambda r: (
            ttk.Entry(r, textvariable=self.rn_name).pack(
                side=tk.LEFT, fill=tk.X, expand=True)))
        self._hint(f, "可用 {uid} {stem} {src_stem} {ext} 等变量，见上方“变量用法”")

        # 测试按钮：按当前选中的视频清单预览重命名结果（不改写任何文件）
        ttk.Button(f, text="🧪 测试重命名预览", width=18,
                   command=self._preview_rename).pack(anchor=tk.W, padx=12, pady=(4, 0))

        ttk.Checkbutton(f, text="同时改名同名附属文件（字幕/封面等）",
                        variable=self.rn_comp).pack(anchor=tk.W, padx=12, pady=(2, 0))

        self._hint(f, "在原所在文件夹就地改名，不影响“移动到文件夹”设定的目标位置；"
                      "若模板渲染后名称不变则跳过")

        for v in (self.rn_name, self.rn_comp):
            v.trace_add('write', lambda *a: self._apply_params())

    # ---------- 参数面板：字幕 ----------
    def _build_subtitle_params(self):
        f = ttk.Frame(self.param_host)
        self._param_frames[STEP_SUBTITLE] = f
        self.st_asr = tk.StringVar(value="kotoba-whisper")
        self.st_size = tk.StringVar(value="medium")
        self.st_trans = tk.StringVar(value="NLLB-200")
        self.st_device = tk.StringVar(value="cuda")
        self.st_bilingual = tk.BooleanVar(value=True)
        self.st_skip = tk.BooleanVar(value=True)

        try:
            from subtitle.config import ASR_ENGINES, TRANSLATOR_ENGINES, WHISPER_MODELS
            asr_values = list(ASR_ENGINES.keys())
            trans_values = list(TRANSLATOR_ENGINES.keys())
            size_values = list(WHISPER_MODELS.keys())
        except Exception:
            asr_values = ["faster-whisper", "kotoba-whisper", "reazonspeech-nemo"]
            trans_values = ["NLLB-200", "Sakura-7B", "Sakura-14B", "Sakura-GalTransl-7B"]
            size_values = ["tiny", "base", "small", "medium", "large-v3"]

        r = ttk.Frame(f); r.pack(fill=tk.X, pady=2)
        ttk.Label(r, text="语音大模型:", width=12).pack(side=tk.LEFT)
        ttk.Combobox(r, textvariable=self.st_asr, width=18, state="readonly",
                     values=asr_values).pack(side=tk.LEFT)
        ttk.Label(r, text="模型大小").pack(side=tk.LEFT, padx=(8, 2))
        self.st_size_combo = ttk.Combobox(r, textvariable=self.st_size, width=10,
                                          state="readonly", values=size_values)
        self.st_size_combo.pack(side=tk.LEFT)
        self._hint(f, "（模型大小仅 faster-whisper 可选；kotoba / nemo 为固定模型）")

        r2 = ttk.Frame(f); r2.pack(fill=tk.X, pady=2)
        ttk.Label(r2, text="翻译模型:", width=12).pack(side=tk.LEFT)
        ttk.Combobox(r2, textvariable=self.st_trans, width=18, state="readonly",
                     values=trans_values).pack(side=tk.LEFT)
        ttk.Label(r2, text="设备").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Combobox(r2, textvariable=self.st_device, width=8, state="readonly",
                     values=["cuda", "cpu"]).pack(side=tk.LEFT)

        # 两个复选框单独一行：与上面的下拉合并会超出宽度
        r3 = ttk.Frame(f); r3.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(r3, text="双语字幕（日文+中文）",
                        variable=self.st_bilingual).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Checkbutton(r3, text="已存在 srt 则跳过",
                        variable=self.st_skip).pack(side=tk.LEFT, padx=(10, 0))

        for v in (self.st_asr, self.st_size, self.st_trans, self.st_device,
                  self.st_bilingual, self.st_skip):
            v.trace_add('write', lambda *a: self._apply_params())

        # 联动：只有 faster-whisper 支持选择模型大小（kotoba/nemo 为固定模型）
        self.st_asr.trace_add('write', lambda *a: self._update_asr_size_state())
        self._update_asr_size_state()

    # ---------- 参数联动 ----------
    def _update_res_state(self):
        """分辨率模式切换：显示当前模式对应的子行；比例行随规格可用；
        “竖屏自动适配”在“固定长边/短边”模式下自动置灰（该模式已随原片方向适配）。
        """
        mode = self.tc_mode.get()
        spec_on = (self.tc_sub.get() != SUBTITLE_MODE_LABELS.get('soft_fast')
                   and not self.tc_keepq.get())
        submap = {
            "预设固定分辨率": getattr(self, '_tc_preset_row', None),
            "自定义分辨率": getattr(self, '_tc_custom_row', None),
            "固定宽或高": getattr(self, '_tc_fixed_row', None),
            "固定长边/短边": getattr(self, '_tc_edge_row', None),
        }
        for m, rw in submap.items():
            if rw is None:
                continue
            if spec_on and m == mode:
                rw.pack(fill=tk.X, pady=2, in_=self._tc_res_sub)
            else:
                rw.pack_forget()
        # 比例行：始终随“输出规格”可用性
        self._set_row_state(getattr(self, '_tc_aspect_row', None), spec_on)
        # 竖屏自动适配：固定长边/短边模式自动合并，无需勾选 → 置灰
        orient_on = spec_on and mode != "固定长边/短边"
        self._set_row_state(getattr(self, '_tc_orient_row', None), orient_on)

    @staticmethod
    def _parse_custom_dim(s) -> Optional[int]:
        """解析自定义分辨率宽/高输入框：返回正整数；空或“自动”等视为自动（返回 None）。"""
        s = (s or '').strip().lower()
        if s in ('auto', '自动', 'a', ''):
            return None
        try:
            v = int(s)
        except ValueError:
            return None
        return v if v > 0 else None

    def _update_param_states(self):
        """参数联动：
        - 剪辑时间选“统一时间段”以外的模式 → 起止时间行禁用（不裁剪/掐头去尾用不到）
        - 字幕选“软字幕·快速附加（不重编码）” → 输出规格行禁用（不改画质）
        - 勾选“保留原画质（流拷贝剪辑）” → 规格/码率行禁用（不重编码，设置无意义）
        - 勾选“不修改码率” → 仅禁用码率数值框（沿用原片码率）
        """
        if not hasattr(self, '_tc_time_row'):
            return
        time_on = (self.tc_trim.get() == TRIM_MODES.get('range'))
        self._set_row_state(self._tc_time_row, time_on)
        # 输出规格（分辨率/比例/码率）在两种情况下禁用：
        #  - 字幕选“软字幕·快速附加（不重编码）”
        #  - 勾选“保留原画质（流拷贝剪辑）”：该模式只裁剪、不重编码，规格设置无意义
        spec_on = (self.tc_sub.get() != SUBTITLE_MODE_LABELS.get('soft_fast')
                   and not self.tc_keepq.get())
        self._set_row_state(getattr(self, '_tc_br_row', None), spec_on)
        # 分辨率模式相关（子行显示 / 比例 / 竖屏适配）统一在 _update_res_state 处理
        self._update_res_state()
        # 勾选“不修改码率”时，码率数值框无意义 → 置灰（仍保留界面上一次的值作回退）
        spin = getattr(self, '_tc_bitrate_spin', None)
        if spin is not None:
            try:
                spin.configure(state='disabled'
                               if (self.tc_keep_br.get() or not spec_on) else 'normal')
            except Exception:
                pass
        # “低于设置值不修改”与“不修改码率”互斥（后者优先）；流拷贝时不重编码也禁用
        below_chk = getattr(self, '_tc_br_below_chk', None)
        if below_chk is not None:
            try:
                below_chk.configure(
                    state='disabled' if (self.tc_keep_br.get() or not spec_on) else 'normal')
            except Exception:
                pass
        srt_on = (self.tc_sub.get() != SUBTITLE_MODE_LABELS.get('none'))
        try:
            self._tc_subfile_row.configure(state='normal' if srt_on else 'disabled')
        except Exception:
            pass

    @staticmethod
    def _set_row_state(row: Optional[ttk.Frame], enabled: bool):
        if row is None:
            return
        for w in row.winfo_children():
            cls = w.winfo_class()
            try:
                if cls == 'TCombobox':
                    w.configure(state='readonly' if enabled else 'disabled')
                elif cls in ('TEntry', 'TSpinbox', 'TCheckbutton'):
                    w.configure(state='normal' if enabled else 'disabled')
            except Exception:
                pass

    def _update_asr_size_state(self):
        """只有 faster-whisper 有 tiny~large-v3 多种大小；kotoba/nemo 为固定模型"""
        combo = getattr(self, 'st_size_combo', None)
        if combo is None:
            return
        try:
            combo.configure(state='readonly' if self.st_asr.get() == 'faster-whisper'
                            else 'disabled')
        except Exception:
            pass

    # ---------- 变量用法说明 ----------
    def _toggle_var_help(self):
        if self.var_help.winfo_ismapped():
            self.var_help.pack_forget()
            self.var_toggle_btn.configure(text="变量用法 ▼")
        else:
            self.var_help.pack(fill=tk.X, pady=(4, 0))
            self.var_toggle_btn.configure(text="收起说明 ▲")

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------
    def _load_config(self):
        data = self.config_manager.get_section(CONFIG_SECTION, default={}) or {}
        self.recursive_var.set(bool(data.get("recursive", True)))
        self.only_video_var.set(bool(data.get("only_video", True)))
        job = data.get("job")
        if job:
            try:
                self.job = WorkflowJob.from_dict(job)
            except Exception:
                self.job = WorkflowJob()
        self.finish_var.set(FINISH_LABELS.get(data.get("finish_action", "none"), "无操作"))
        self._sync_finish_action()   # 同步实时持有器，保证运行期间修改下拉框生效
        self.continue_var.set(bool(data.get("continue_on_error", False)))
        try:
            self.threads_var.set(int(data.get("parallel_threads", 1)))
        except Exception:
            self.threads_var.set(1)
        # 任务清单（向后兼容：旧配置无 tasks 字段时为空白）
        self.tasks = []
        for td in (data.get("tasks") or []):
            try:
                self.tasks.append(WorkflowTask.from_dict(td))
            except Exception:
                pass
        self._refresh_task_tree()
        self._refresh_step_list()

    def _on_threads_change(self):
        try:
            v = int(self.threads_var.get())
            if v < 1:
                self.threads_var.set(1)
        except Exception:
            self.threads_var.set(1)
        self._save_config()

    def _save_config(self):
        self.job.finish_action = self._finish_key()
        self.job.continue_on_error = bool(self.continue_var.get())
        data = {
            "recursive": bool(self.recursive_var.get()),
            "only_video": bool(self.only_video_var.get()),
            "job": self.job.to_dict(),
            "finish_action": self.job.finish_action,
            "continue_on_error": self.job.continue_on_error,
            "parallel_threads": int(self.threads_var.get()),
            "tasks": [t.to_dict() for t in self.tasks],
        }
        self.config_manager.set_section(CONFIG_SECTION, data, auto_save=True)
        self._log("[配置] 工作流已保存")

    def _finish_key(self) -> str:
        label = self.finish_var.get()
        for k, v in FINISH_LABELS.items():
            if v == label:
                return k
        return "none"

    # ---------- 参数区滚动 ----------
    def _on_param_canvas_configure(self, event):
        """画布宽度变化：让参数区跟随变宽，并刷新滚动范围"""
        try:
            self._param_canvas.itemconfigure(self._param_win, width=event.width)
            self._param_canvas.configure(
                scrollregion=self._param_canvas.bbox('all'))
        except Exception:
            pass

    def _on_param_mousewheel(self, event):
        """参数区内鼠标滚轮：滚动内容（部分系统/触控板只发 Button-4/5）"""
        try:
            delta = getattr(event, 'delta', 0) or 0
            if delta:
                self._param_canvas.yview_scroll(int(-delta / 120), 'units')
            elif getattr(event, 'num', None) == 4:
                self._param_canvas.yview_scroll(-3, 'units')
            elif getattr(event, 'num', None) == 5:
                self._param_canvas.yview_scroll(3, 'units')
        except Exception:
            pass

    def _bind_wheel(self, widget, canvas, _depth: int = 0):
        """递归给控件绑定滚轮事件（只影响参数区，不使用全局 bind_all）"""
        if widget is None or _depth > 8:
            return
        try:
            widget.bind('<MouseWheel>', self._on_param_mousewheel, add='+')
            widget.bind('<Button-4>', self._on_param_mousewheel, add='+')
            widget.bind('<Button-5>', self._on_param_mousewheel, add='+')
            for child in widget.winfo_children():
                self._bind_wheel(child, canvas, _depth + 1)
        except Exception:
            pass

    def _sync_finish_action(self):
        """把当前“完成后操作”下拉框的值同步进实时持有器（运行中也生效）"""
        if getattr(self, "_finish_action_live", None) is None:
            self._finish_action_live = {"value": "none"}
        self._finish_action_live["value"] = self._finish_key()

    # ------------------------------------------------------------------
    # 视频清单
    # ------------------------------------------------------------------
    def _pick_dir(self, var: tk.StringVar):
        d = filedialog.askdirectory(title="选择文件夹")
        if d:
            var.set(d)

    def _scan(self):
        d = filedialog.askdirectory(title="选择要扫描的文件夹")
        if not d:
            return
        files = scan_videos(d, self.recursive_var.get(), self.only_video_var.get())
        if not files:
            show_toast(self.frame, "未扫描到任何视频文件", "warning")
            self._log("[扫描] 未找到视频文件")
            return
        self._set_items(files)
        self._log(f"[扫描] {d} → 找到 {len(files)} 个视频")
        show_toast(self.frame, f"扫描到 {len(files)} 个视频", "success")

    def _add_files(self):
        paths = filedialog.askopenfilenames(title="选择视频文件")
        if paths:
            self._set_items(list(paths), append=True)
            self._log(f"[添加] 新增 {len(paths)} 个文件")

    def _set_items(self, files: List[str], append: bool = False):
        # 统一为 Windows 反斜杠路径（拖放/对话框可能传入正斜杠）
        files = [os.path.normpath(str(f)) for f in files]
        new_items = build_items(files)
        if append:
            exists = {i.origin for i in self.items}
            new_items = [i for i in new_items if i.origin not in exists]
            # 追加导入时，新条目可能与已有条目同名/同番号而撞号，
            # 需基于已有 uid 重新去重，避免列表 iid 重复导致插入崩溃
            existing_uids = {i.uid for i in self.items}
            for it, uid in zip(new_items,
                               dedup_uids([i.uid for i in new_items], existing=existing_uids)):
                it.uid = uid
            self.items.extend(new_items)
        else:
            self.items = new_items
        self._refresh_tree()
        self._probe_rates(new_items)    # 后台探测数据速率（不阻塞 UI）

    def _refresh_tree(self):
        # 已加入任意任务的视频来源集合：用于高亮标记，防止重复添加
        added = self._added_origins()
        self.tree.delete(*self.tree.get_children())
        for it in self.items:
            if not it.uid:
                continue
            trim = (f"{format_clock(it.trim_start)}~{format_clock(it.trim_end)}"
                    if it.use_custom_trim else "（沿用步骤设置）")
            rate = f"{it.video_kbps} kbps" if it.video_kbps else "—"
            res = f"{it.width}x{it.height}" if it.width and it.height else "—"
            is_added = os.path.normpath(it.origin) in added
            # 状态列：运行期实时状态（翻译 3/20、剪辑 45%、移动中…）优先显示，
            # 无实时状态时才显示“已添加”标记（此前“已添加”会覆盖实时进度）
            live = (it.status or "").strip()
            status = live or ("已添加" if is_added else "")
            # 标记列图标：✂ 需剪辑、📝 有字幕（可同时出现）
            marks = []
            if it.need_trim:
                marks.append("✂")
            if it.has_subtitle:
                marks.append("📝")
            mark = " ".join(marks)
            # 分辨率着色：标准分辨率（含横/竖两种朝向）绿色，
            #            特殊/裁剪过的分辨率（如 960x1264）红色，未知（未探测）默认
            if it.width and it.height:
                is_std = ((it.width, it.height) in STANDARD_RESOLUTIONS
                          or (it.height, it.width) in STANDARD_RESOLUTIONS)
                res_tag = "res_std" if is_std else "res_special"
            else:
                res_tag = ""
            tags = tuple(t for t in (("added" if is_added else ""), res_tag) if t)
            self.tree.insert("", tk.END, iid=it.uid, values=(
                it.uid, mark, os.path.basename(it.path),
                os.path.normpath(os.path.dirname(it.path)),
                trim, rate, res, status),
                tags=tags)

    def _added_origins(self) -> set:
        """所有任务里出现过的视频来源（origin）集合，用于清单高亮"""
        s = set()
        for t in self.tasks:
            for p in getattr(t, "file_paths", []) or []:
                s.add(os.path.normpath(str(p)))
        return s

    def _selected_items(self) -> List[WorkflowItem]:
        uid_map = {i.uid: i for i in self.items}
        return [uid_map[uid] for uid in self.tree.selection() if uid in uid_map]

    def _select_all_items(self):
        """选中视频清单中所有视频行"""
        self.tree.selection_set(self.tree.get_children())

    def _remove_selected(self):
        sel = set(self.tree.selection())
        if not sel:
            return
        self.items = [i for i in self.items if i.uid not in sel]
        self._refresh_tree()

    def _clear_items(self):
        self.items = []
        self._refresh_tree()

    # ---------- 操作标记（需剪辑 / 有字幕） ----------
    def _toggle_mark(self, mark: str):
        """对选中的视频批量切换“需剪辑 / 有字幕”标记。

        语义：若选中项全部已带该标记 → 全部取消；否则全部打上（一键“添加或删除”）。
        这些标记仅用于提醒操作者接下来的操作，不影响实际转码/改名等步骤。
        """
        if self.executor and self.executor.is_alive():
            show_toast(self.frame, "工作流执行中，暂不能修改标记", "warning")
            return
        items = self._selected_items()
        if not items:
            show_toast(self.frame, "请先在视频清单中选中视频", "info")
            return
        attr = "need_trim" if mark == "need_trim" else "has_subtitle"
        label = "需剪辑" if mark == "need_trim" else "有字幕"
        all_on = all(bool(getattr(it, attr)) for it in items)
        new_val = not all_on          # 全开→关；否则→开
        for it in items:
            setattr(it, attr, new_val)
        self._refresh_tree()
        self._log(f"[{label}] 已{'标记' if new_val else '取消标记'} {len(items)} 个视频")
        show_toast(self.frame, f"已{'标记' if new_val else '取消'} {len(items)} 个“{label}”",
                   "success")

    # ---------- 清单设置导出 / 导入（标记 + 剪辑时间，跨会话持久化） ----------
    def _item_fingerprint(self, it) -> str:
        """文件的快速指纹（不读文件内容，仅取 名称+大小+修改时间），用于导入时匹配。

        比 md5 快得多（避免对多 GB 视频整文件哈希），足以在“重新加入同一文件”时
        准确匹配；同时导出时仍记录原始 path，优先按路径精确匹配，指纹作为兜底。
        """
        name = os.path.basename(it.path)
        size = mtime = 0
        try:
            st = os.stat(it.path)
            size, mtime = st.st_size, st.st_mtime
        except Exception:
            pass
        return f"{name}|{size}|{mtime}"

    def _export_marks(self):
        """将当前清单所有视频的标记（需剪辑/有字幕）与剪辑起止时间导出为 JSON。

        每条记录带 path + 快速指纹（名称|大小|修改时间），以便下次重新加入文件后
        按“路径→指纹→名称”顺序精准匹配并恢复设置，不会因文件名重复而搞错。
        """
        if not self.items:
            show_toast(self.frame, "清单为空，无可导出项", "warning")
            return
        path = filedialog.asksaveasfilename(
            title="导出视频清单设置（标记 + 剪辑时间）",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        items_data = []
        for it in self.items:
            fp = self._item_fingerprint(it)
            items_data.append({
                "uid": it.uid,
                "path": it.path,
                "name": os.path.basename(it.path),
                "size": int(fp.split("|")[1]),
                "mtime": float(fp.split("|")[2]),
                "fingerprint": fp,
                "need_trim": bool(it.need_trim),
                "has_subtitle": bool(it.has_subtitle),
                "trim_start": float(it.trim_start),
                "trim_end": float(it.trim_end),
                "use_custom_trim": bool(it.use_custom_trim),
            })
        data = {
            "app": "视频文件管理工具",
            "feature": "视频清单设置",
            "version": 1,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(items_data),
            "match_note": "导入时按 路径→指纹(名称|大小|修改时间)→名称 顺序匹配",
            "items": items_data,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"[导出] 已导出 {len(items_data)} 个视频设置 → {path}")
            show_toast(self.frame, f"已导出 {len(items_data)} 项设置", "success")
        except Exception as e:
            show_toast(self.frame, f"导出失败: {e}", "error")

    def _import_marks(self):
        """从 JSON 恢复标记与剪辑时间：仅对“已重新加入”且能匹配的视频生效。

        匹配优先级：① 路径完全一致；② 指纹（名称|大小|修改时间）一致；③ 文件名一致。
        未匹配到的导出项（对应文件未重新加入）会提示数量，避免静默丢失。
        """
        if not self.items:
            show_toast(self.frame, "请先重新加入视频文件，再导入设置", "warning")
            return
        path = filedialog.askopenfilename(
            title="导入视频清单设置（标记 + 剪辑时间）",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            show_toast(self.frame, f"读取失败: {e}", "error")
            return
        recs = data.get("items") or []
        if not recs:
            show_toast(self.frame, "文件中没有可导出的视频记录", "warning")
            return

        by_path: Dict[str, dict] = {}
        by_fp: Dict[str, dict] = {}
        by_name: Dict[str, dict] = {}
        for r in recs:
            rp = r.get("path")
            if rp:
                by_path.setdefault(os.path.normpath(rp), r)
            fp = r.get("fingerprint") or \
                f"{r.get('name')}|{r.get('size')}|{r.get('mtime')}"
            by_fp.setdefault(fp, r)
            by_name.setdefault(r.get("name"), r)

        matched = unmatched = 0
        for it in self.items:
            norm = os.path.normpath(it.path)
            r = (by_path.get(norm)
                 or by_fp.get(self._item_fingerprint(it))
                 or by_name.get(os.path.basename(it.path)))
            if not r:
                unmatched += 1
                continue
            it.need_trim = bool(r.get("need_trim", False))
            it.has_subtitle = bool(r.get("has_subtitle", False))
            it.trim_start = float(r.get("trim_start", 0.0) or 0.0)
            it.trim_end = float(r.get("trim_end", 0.0) or 0.0)
            it.use_custom_trim = bool(r.get("use_custom_trim", False))
            matched += 1
        self._refresh_tree()
        self._log(f"[导入] 已匹配 {matched} 项，未匹配 {unmatched} 项"
                  f"（未匹配多为文件尚未重新加入）")
        show_toast(self.frame, f"已导入 {matched} 项（{unmatched} 项未匹配）", "info")

    # ---------- 数据速率探测（视频流码率，不含音频） ----------
    def _resolve_ffprobe(self):
        if getattr(self, '_ffprobe', None):
            return self._ffprobe
        import shutil
        fp = shutil.which('ffprobe')
        if not fp:
            try:
                from subtitle.system_utils import find_ffmpeg
                ok, ff, _ = find_ffmpeg(None)
                if ok and ff and os.path.isfile(os.path.join(os.path.dirname(ff), 'ffprobe.exe')):
                    fp = os.path.join(os.path.dirname(ff), 'ffprobe.exe')
            except Exception:
                pass
        self._ffprobe = fp
        return fp

    def _probe_video_kbps(self, path: str) -> int:
        """用 ffprobe 读取视频流码率（kbps，不含音频），与资源管理器“数据速率”口径一致。
        读取不到时回退为「文件大小/时长 − 128k 音频」估算值。"""
        fp = self._resolve_ffprobe()
        if not fp or not os.path.isfile(path):
            return 0
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            res = subprocess.run(
                [fp, '-v', 'error', '-show_entries', 'stream=codec_type,bit_rate',
                 '-of', 'csv=p=0', path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=30, creationflags=flags)
            total = 0
            for line in (res.stdout or '').splitlines():
                parts = [x.strip() for x in line.split(',')]
                if len(parts) >= 2 and parts[0] == 'video' and parts[-1].isdigit():
                    total += int(parts[-1])
            if total > 0:
                return max(1, round(total / 1000.0))
            # 回退：按文件大小与时长估算
            size = os.path.getsize(path)
            res2 = subprocess.run(
                [fp, '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'csv=p=0', path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=30, creationflags=flags)
            try:
                d = float((res2.stdout or '').strip().splitlines()[0] or 0)
            except Exception:
                d = 0
            if d > 0:
                est = size * 8 / d - 128000
                return max(1, round(est / 1000.0)) if est > 0 else 0
            return 0
        except Exception:
            return 0

    def _probe_video_dims(self, path: str) -> Tuple[int, int]:
        """用 ffprobe 读取视频的帧宽度/帧高度（帧宽度x帧高度），与资源管理器属性一致。"""
        fp = self._resolve_ffprobe()
        if not fp or not os.path.isfile(path):
            return (0, 0)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            res = subprocess.run(
                [fp, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=30, creationflags=flags)
            parts = (res.stdout or '').strip().split(',')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return (0, 0)

    def _probe_rates(self, items: List[WorkflowItem]):
        """后台线程逐个探测数据速率与分辨率（帧宽度x帧高度），完成后刷新列表。"""
        fp = self._resolve_ffprobe()
        if not fp:
            return
        todo = [it for it in items
                if not getattr(it, 'video_kbps', 0)
                or not (getattr(it, 'width', 0) and getattr(it, 'height', 0))]
        if not todo:
            return

        def _worker():
            changed = False
            for it in todo:
                if not getattr(it, 'video_kbps', 0):
                    k = self._probe_video_kbps(it.path)
                    if k:
                        it.video_kbps = k
                        changed = True
                if not (getattr(it, 'width', 0) and getattr(it, 'height', 0)):
                    w, h = self._probe_video_dims(it.path)
                    if w and h:
                        it.width, it.height = w, h
                        changed = True
            if changed:
                try:
                    self.frame.after(0, self._refresh_tree)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _open_folder(self):
        """在资源管理器中打开选中视频所在的文件夹（尽量定位到该文件）。

        任务完成后原文件可能已被删除/改名（如“删除原文件并把新文件改名”），
        因此该菜单只要求文件夹存在即可进入：文件存在则高亮定位，
        文件不在了就直接打开其所在文件夹（当前文件与最初源文件都试）。
        """
        items = self._selected_items()
        if not items:
            show_toast(self.frame, "请先在视频清单中选中一个视频", "info")
            return
        it = items[0]
        # 候选：当前文件 → 最初源文件（去重保序；流程可能删除/改名了当前文件）
        for cand in dict.fromkeys([it.path, getattr(it, 'origin', '')]):
            if not cand:
                continue
            p = Path(cand)
            folder = p.parent
            if p.is_file():
                try:
                    # explorer /select, 会在文件夹里高亮该文件
                    subprocess.Popen(['explorer', '/select,', os.path.normpath(cand)],
                                     shell=False)
                    self._log(f"[打开] 资源管理器：{folder}")
                    return
                except Exception:
                    pass      # 定位失败降级为直接打开文件夹
            if folder.is_dir():
                try:
                    os.startfile(str(folder))
                    self._log(f"[打开] 文件夹（原文件已不在，仅进入文件夹）：{folder}")
                    return
                except Exception as e:
                    show_toast(self.frame, f"无法打开文件夹: {e}", "error")
                    return
        show_toast(self.frame, f"文件与其所在文件夹均不存在: {it.path}", "error")

    def _edit_trim(self):
        items = self._selected_items()
        if not items:
            show_toast(self.frame, "请先在视频清单中选中一个视频", "info")
            return
        it = items[0]
        if not os.path.isfile(it.path):
            show_toast(self.frame, f"文件不存在: {it.path}", "error")
            return
        dlg = TrimTimeDialog(self.frame, it.path, it.trim_start, it.trim_end,
                             it.use_custom_trim, item=it)
        if dlg.result:
            s, e, custom = dlg.result
            it.trim_start, it.trim_end, it.use_custom_trim = s, e, custom
        # 不论是否改了剪辑时间，对话框内的“需剪辑/有字幕”标记都可能被改动，统一刷新
        self._refresh_tree()
        if dlg.result:
            self._log(f"[剪辑时间] {it.uid}: {format_clock(s)} ~ {format_clock(e)}"
                      f"（仅对该视频生效）")

    def _clear_trim(self):
        for it in self._selected_items():
            it.use_custom_trim = False
            it.trim_start = it.trim_end = 0.0
        self._refresh_tree()

    def _edit_uid(self):
        """人工修正选中视频的 UID，并写入学习库（后续识别自动改进）"""
        if self.executor and self.executor.is_alive():
            show_toast(self.frame, "工作流执行中，暂不能修改 UID", "warning")
            return
        items = self._selected_items()
        if len(items) != 1:
            show_toast(self.frame, "请先选中一个视频（一次修改一个 UID）", "info")
            return
        it = items[0]
        from tkinter import simpledialog
        new = simpledialog.askstring(
            "修改 UID",
            f"文件：{os.path.basename(it.origin)}\n"
            f"当前 UID：{it.uid}\n\n"
            f"输入正确的番号 / UID（修正后会自动学习，\n"
            f"以后遇到同类文件名识别更准）：",
            initialvalue=it.uid, parent=self.frame)
        if new is None:
            return
        new = re.sub(r'[\\/:*?"<>|\r\n\t]+', '', new).strip().strip('.').upper()
        if not new:
            show_toast(self.frame, "UID 不能为空", "warning")
            return
        if new == it.uid:
            return
        # 与其它条目撞名时追加 -2 / -3，保证唯一
        others = {i.uid for i in self.items if i is not it}
        base, n = new, 1
        while new in others:
            n += 1
            new = f"{base}-{n}"
        old_uid = it.uid
        it.uid = new
        changed = learn_uid_correction(it.origin, new, old_uid)
        self._refresh_tree()
        self._log(f"[UID] {old_uid} → {new}")
        if changed:
            st = learner_stats()
            self._log(f"[学习] 已记录 “{os.path.splitext(os.path.basename(it.origin))[0]}”"
                      f" → {new}（累计精确记忆 {st['mappings']} 条、"
                      f"前缀模式 {st['junk_prefixes']} 个、后缀模式 {st['junk_suffixes']} 个），"
                      f"后续扫描/拖入自动套用")

    # ---------- 拖放：从资源管理器拖入文件/文件夹 ----------
    def _enable_dnd(self):
        """启用鼠标拖放（依赖 tkinterdnd2）；不可用时降级为“扫描/添加文件”"""
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            root = self.tree.winfo_toplevel()
            TkinterDnD._require(root)          # 为已有主窗口加载 tkdnd
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind('<<Drop>>', self._on_drop)
            self.dnd_enabled = True
        except Exception as e:
            self.dnd_enabled = False
            self._log(f"[提示] 未启用拖放（{e}），可用“扫描”或“添加文件”按钮加入视频")

    def _on_drop(self, event):
        """处理从资源管理器拖入的多个文件/文件夹（文件夹自动递归收集视频）"""
        try:
            raw = self.tree.tk.splitlist(event.data)
        except Exception:
            raw = str(event.data).split()
        files: List[str] = []
        skipped = 0
        for p in raw:
            p = str(p).strip()
            try:
                if os.path.isdir(p):
                    files.extend(scan_videos(p, recursive=True, only_video=True))
                elif os.path.isfile(p):
                    if is_video(p):
                        files.append(p)
                    else:
                        skipped += 1
            except OSError:
                continue
        if files:
            self._set_items(files, append=True)
            msg = f"[拖入] 新增 {len(files)} 个视频"
            if skipped:
                msg += f"（跳过 {skipped} 个非视频文件）"
            self._log(msg)
            show_toast(self.frame, f"已加入 {len(files)} 个视频", "success")
        else:
            show_toast(self.frame, "拖入内容中没有视频文件", "warning")
        return event.action

    # ------------------------------------------------------------------
    # 流程步骤
    # ------------------------------------------------------------------
    def _refresh_step_list(self, keep_index: Optional[int] = None):
        idx = keep_index if keep_index is not None else self._cur_step_index()
        self.step_list.delete(0, tk.END)
        for i, s in enumerate(self.job.steps, 1):
            mark = "✔" if s.enabled else "⤸"
            self.step_list.insert(tk.END, f"{i}. {mark} {STEP_ICONS.get(s.type, '')}"
                                          f"{s.label}  —  {s.summary()}")
        if not self.job.steps:
            self._show_params(None)
            return
        idx = max(0, min(idx, len(self.job.steps) - 1))
        self.step_list.selection_clear(0, tk.END)
        self.step_list.selection_set(idx)
        self.step_list.see(idx)
        self._show_params(self.job.steps[idx])

    def _refresh_step_summary(self):
        """仅刷新步骤列表的文字摘要，不重建/重载右侧参数面板。

        供参数输入（trace 触发 _apply_params）时调用：若走 _refresh_step_list
        会顺带 _load_params 重新 set 变量，正在编辑的 Entry 选区/光标被清空，
        导致「选中文字后输入却无法替换」。本方法只更新 Listbox 文本与选中项。
        """
        try:
            idx = self._cur_step_index()
            self.step_list.delete(0, tk.END)
            for i, s in enumerate(self.job.steps, 1):
                mark = "✔" if s.enabled else "⤸"
                self.step_list.insert(tk.END, f"{i}. {mark} {STEP_ICONS.get(s.type, '')}"
                                              f"{s.label}  —  {s.summary()}")
            idx = max(0, min(idx, len(self.job.steps) - 1))
            self.step_list.selection_clear(0, tk.END)
            self.step_list.selection_set(idx)
            self.step_list.see(idx)
        except Exception:
            pass

    def _cur_step_index(self) -> int:
        sel = self.step_list.curselection()
        return int(sel[0]) if sel else 0

    def _on_select_step(self):
        sel = self.step_list.curselection()
        if not sel:
            return
        self._show_params(self.job.steps[int(sel[0])])

    def _show_params(self, step: Optional[WorkflowStep]):
        for t, f in self._param_frames.items():
            f.pack_forget()
        if step is None:
            self._current_step = None
            self.var_hint_wrap.pack_forget()
            return
        self._current_step = step
        self.enabled_var.set(bool(step.enabled))
        f = self._param_frames.get(step.type)
        if f:
            f.pack(fill=tk.X)
        # 切换步骤后把参数区滚回顶部，并刷新滚动范围（内容高度可能不同）
        try:
            self._param_canvas.yview_moveto(0.0)
            self._param_canvas.configure(
                scrollregion=self._param_canvas.bbox('all'))
        except Exception:
            pass
        # 字幕步骤没有“输出文件夹/文件名模板”，不显示变量提示
        if step.type == STEP_SUBTITLE:
            self.var_hint_wrap.pack_forget()
        else:
            self.var_hint_wrap.pack(fill=tk.X, pady=(4, 0))
        self._load_params(step)

    def _preview_rename(self):
        """按当前“修改名称”步骤的文件名模板，预览选中视频的重命名结果（不真正改名）。

        预览逻辑与执行时 run_rename 完全一致：模板渲染 → 扩展名处理（模板含视频扩展名
        则沿用，否则补当前扩展名）→ 非法字符净化。结果为“原名 → 新名”，未变化的标注“不变”。
        """
        items = self._selected_items()
        if not items:
            show_toast(self.frame, "请先在左侧视频清单中选择要预览的视频", "warning")
            return
        tpl = self.rn_name.get().strip() or '{stem}'
        lines = []
        unchanged = 0
        for it in items:
            cur = os.path.basename(it.path)
            rendered = it.render(tpl).strip() or Path(it.path).stem
            _, cur_ext = os.path.splitext(cur)
            if os.path.splitext(rendered)[1].lower() in VIDEO_EXTENSIONS:
                new = rendered
            else:
                new = rendered + cur_ext
            new = _sanitize_filename(new)
            if os.path.normcase(new) == os.path.normcase(cur):
                unchanged += 1
                lines.append(f"{cur}  →  {new}   （不变）")
            else:
                lines.append(f"{cur}  →  {new}")
        title = f"重命名预览（按当前选中 {len(items)} 个视频，含 {unchanged} 个不变）"
        self._show_preview_dialog(title, "\n".join(lines))

    def _show_preview_dialog(self, title: str, text: str):
        """只读的多行预览弹窗（用于“测试重命名预览”等）"""
        top = tk.Toplevel(self.frame)
        top.title(title)
        try:
            top.transient(self.frame.winfo_toplevel())
        except Exception:
            pass
        top.geometry("560x380")
        txt = tk.Text(top, wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt.insert("1.0", text)
        txt.configure(state=tk.DISABLED)
        ttk.Button(top, text="关闭", command=top.destroy).pack(pady=(0, 8))
        try:
            top.grab_set()
        except Exception:
            pass

    def _load_params(self, step: WorkflowStep):
        self._loading = True
        p = step.params
        try:
            if step.type == STEP_MOVE:
                self.mv_target.set(p.get('target_dir', ''))
                self.mv_comp.set(bool(p.get('with_companions', True)))
                self.mv_uidsub.set(bool(p.get('uid_subdir', False)))
            elif step.type == STEP_TRANSCODE:
                self.tc_trim.set(TRIM_MODES.get(p.get('trim_mode', 'none'), "不裁剪（整段输出）"))
                self.tc_start.set(format_clock(float(p.get('start') or 0)))
                self.tc_end.set(format_clock(float(p.get('end') or 0)))
                self.tc_head.set(float(p.get('head') or 0))
                self.tc_tail.set(float(p.get('tail') or 0))
                res = p.get('resolution', '1280x720')
                rl = (res or '').lower().strip()
                if res in RES_PRESETS:
                    self.tc_mode.set("预设固定分辨率")
                    self.tc_preset.set(res)
                elif rl.startswith("long:") or rl.startswith("short:"):
                    self.tc_mode.set("固定长边/短边")
                    self.tc_edge.set("长边" if rl.startswith("long:") else "短边")
                    self.tc_edge_val.set(rl.split(":", 1)[1].strip())
                elif "auto" in rl:
                    # “auto x H” / “W x auto” → 固定宽或高
                    self.tc_mode.set("固定宽或高")
                    parts = rl.split('x')
                    if len(parts) == 2:
                        pw, ph = parts[0].strip(), parts[1].strip()
                        if pw == 'auto':
                            self.tc_fixed_side.set("高")
                            self.tc_fixed_val.set(ph)
                        else:
                            self.tc_fixed_side.set("宽")
                            self.tc_fixed_val.set(pw)
                    else:
                        self.tc_fixed_side.set("宽")
                        self.tc_fixed_val.set("1280")
                else:
                    parts = rl.split('x')
                    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        self.tc_mode.set("自定义分辨率")
                        self.tc_custom_w.set(parts[0].strip())
                        self.tc_custom_h.set(parts[1].strip())
                    else:
                        self.tc_mode.set("预设固定分辨率")
                        self.tc_preset.set("1280x720")
                self.tc_aspect.set(p.get('aspect', '保持原比例'))
                self.tc_bitrate.set(int(p.get('bitrate') or 2500))
                self.tc_keep_br.set(bool(p.get('keep_bitrate', False)))
                self.tc_br_below_keep.set(bool(p.get('bitrate_below_keep', False)))
                self.tc_encoder.set(p.get('encoder') or ENCODER_LABELS['auto'])
                self.tc_save_src.set(p.get('save_mode', 'source') == 'source')
                self.tc_save_dir.set(p.get('save_dir', ''))
                self.tc_ext.set(p.get('out_ext', 'mp4'))
                self.tc_sub.set(SUBTITLE_MODE_LABELS.get(p.get('subtitle_mode', 'none'),
                                                         '不添加字幕'))
                self.tc_del_src.set(bool(p.get('del_src', False)))
                self.tc_del_srt.set(bool(p.get('del_srt', False)))
                self.tc_keepq.set(bool(p.get('keep_quality', False)))
                self.tc_merge.set(bool(p.get('tc_merge', False)))
                self.tc_auto_orient.set(bool(p.get('auto_orient', True)))
                self.tc_portrait_res.set(p.get('portrait_res') or '自动(按源比例)')
            elif step.type == STEP_SUBTITLE:
                self.st_asr.set(p.get('asr_engine', 'kotoba-whisper'))
                self.st_size.set(p.get('model_size', 'medium'))
                self.st_trans.set(p.get('translator_engine', 'NLLB-200'))
                self.st_device.set(p.get('device', 'cuda'))
                self.st_bilingual.set(bool(p.get('bilingual', True)))
                self.st_skip.set(bool(p.get('skip_existing', True)))
            elif step.type == STEP_RENAME:
                self.rn_name.set(p.get('name_template', '{stem}'))
                self.rn_comp.set(bool(p.get('with_companions', True)))
        finally:
            self._loading = False
        # 载入后按当前参数刷新控件启用状态
        self._update_param_states()
        self._update_asr_size_state()

    def _apply_params(self):
        """把界面上的参数写回当前步骤（供无人值守运行使用）"""
        if getattr(self, '_loading', False):
            return
        step = getattr(self, '_current_step', None)
        if step is None:
            return
        p = step.params
        if step.type == STEP_MOVE:
            p['target_dir'] = self.mv_target.get().strip()
            p['with_companions'] = bool(self.mv_comp.get())
            p['uid_subdir'] = bool(self.mv_uidsub.get())
        elif step.type == STEP_TRANSCODE:
            for k, v in TRIM_MODES.items():
                if v == self.tc_trim.get():
                    p['trim_mode'] = k
                    break
            p['start'] = parse_clock(self.tc_start.get()) or 0.0
            p['end'] = parse_clock(self.tc_end.get()) or 0.0
            try:
                p['head'] = float(self.tc_head.get())
            except Exception:
                p['head'] = 0.0
            try:
                p['tail'] = float(self.tc_tail.get())
            except Exception:
                p['tail'] = 0.0
            # 四种分辨率模式 → 统一序列化为 resolution 字符串（转码时由
            # _resolve_target_size 解析）：
            #   预设固定/保持原分辨率 → "WxH" / "保持原分辨率"
            #   自定义             → "WxH" / "auto x H" / "W x auto"
            #   固定宽或高        → "auto x H" / "W x auto"
            #   固定长边/短边     → "long:N" / "short:N"
            mode = self.tc_mode.get()
            if mode == "预设固定分辨率":
                p['resolution'] = self.tc_preset.get()
            elif mode == "自定义分辨率":
                cw = self._parse_custom_dim(self.tc_custom_w.get())
                ch = self._parse_custom_dim(self.tc_custom_h.get())
                if cw and ch:
                    p['resolution'] = f"{cw}x{ch}"
                elif ch and not cw:
                    p['resolution'] = f"auto x {ch}"
                elif cw and not ch:
                    p['resolution'] = f"{cw} x auto"
                else:
                    # 宽高都为“自动”：无明确目标，回退 1280x720 避免空值导致转码异常
                    p['resolution'] = "1280x720"
            elif mode == "固定宽或高":
                side = self.tc_fixed_side.get()
                val = self._parse_custom_dim(self.tc_fixed_val.get())
                p['resolution'] = (f"auto x {val}" if side == "高" else f"{val} x auto") if val \
                    else "1280x720"
            elif mode == "固定长边/短边":
                edge = self.tc_edge.get()
                val = self._parse_custom_dim(self.tc_edge_val.get())
                p['resolution'] = (f"long:{val}" if edge == "长边" else f"short:{val}") if val \
                    else "1280x720"
            else:
                p['resolution'] = "1280x720"
            p['aspect'] = self.tc_aspect.get()
            try:
                p['bitrate'] = int(self.tc_bitrate.get())
            except Exception:
                pass
            # 不修改码率：沿用每个视频自身的原片码率（读不到时回退上面的数值）
            p['keep_bitrate'] = bool(self.tc_keep_br.get())
            # 低于设置值不修改：原片码率低于设置值时保持原片，高于时降到设置值
            p['bitrate_below_keep'] = bool(self.tc_br_below_keep.get())
            p['encoder'] = self.tc_encoder.get()
            p['save_mode'] = 'source' if self.tc_save_src.get() else 'custom'
            p['save_dir'] = self.tc_save_dir.get().strip()
            p['out_ext'] = self.tc_ext.get()
            for k, v in SUBTITLE_MODE_LABELS.items():
                if v == self.tc_sub.get():
                    p['subtitle_mode'] = k
                    break
            p['del_src'] = bool(self.tc_del_src.get())
            p['del_srt'] = bool(self.tc_del_srt.get())
            p['keep_quality'] = bool(self.tc_keepq.get())
            p['tc_merge'] = bool(self.tc_merge.get())
            p['auto_orient'] = bool(self.tc_auto_orient.get())
            p['portrait_res'] = self.tc_portrait_res.get().strip() or '自动(按源比例)'
        elif step.type == STEP_SUBTITLE:
            p['asr_engine'] = self.st_asr.get()
            p['model_size'] = self.st_size.get()
            p['translator_engine'] = self.st_trans.get()
            p['device'] = self.st_device.get()
            p['bilingual'] = bool(self.st_bilingual.get())
            p['skip_existing'] = bool(self.st_skip.get())
        elif step.type == STEP_RENAME:
            p['name_template'] = self.rn_name.get().strip() or '{stem}'
            p['with_companions'] = bool(self.rn_comp.get())

        # 仅刷新步骤列表里的摘要文字（不调用 _refresh_step_list，
        # 否则它会 _show_params → _load_params 重新 set 变量，
        # 导致正在编辑的文本框选区/光标被重置，无法用输入替换选中文字）
        self._refresh_step_summary()

    def _add_step(self, step_type: str):
        self.job.steps.append(WorkflowStep(step_type))
        self._refresh_step_list(keep_index=len(self.job.steps) - 1)
        self._log(f"[流程] 已添加步骤：{STEP_LABELS[step_type]}")

    def _remove_step(self):
        idx = self._cur_step_index()
        if not (0 <= idx < len(self.job.steps)):
            return
        self.job.steps.pop(idx)
        self._refresh_step_list(keep_index=min(idx, max(0, len(self.job.steps) - 1)))

    def _move_step(self, delta: int):
        idx = self._cur_step_index()
        new = idx + delta
        if not (0 <= new < len(self.job.steps)):
            return
        self.job.steps[idx], self.job.steps[new] = self.job.steps[new], self.job.steps[idx]
        self._refresh_step_list(keep_index=new)

    def _toggle_enabled(self):
        step = getattr(self, '_current_step', None)
        if step is None:
            return
        step.enabled = bool(self.enabled_var.get())
        self._refresh_step_list(keep_index=self._cur_step_index())

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------
    def _start(self):
        if self.executor and self.executor.is_alive():
            show_toast(self.frame, "工作流正在执行中", "warning")
            return
        self._apply_params()      # 确保当前右侧参数已写回 self.job

        # ---- 构造任务列表 ----
        if self.tasks:
            prepared = []
            for t in self.tasks:
                job_copy = WorkflowJob.from_dict(t.job.to_dict())
                job_copy.finish_action = "none"   # 单个任务不触发关机/睡眠
                items = t.build_items()
                if not items:
                    self._log(f"[任务清单] 任务「{t.name}」没有有效视频，跳过")
                    continue
                prepared.append((job_copy, items))
            if not prepared:
                show_toast(self.frame, "任务清单中没有可处理的视频", "warning")
                return
            # 任务清单模式：逐个任务校验“移动步骤目标文件夹”
            for job_copy, _ in prepared:
                for s in job_copy.enabled_steps():
                    if s.type == STEP_MOVE and not (s.params.get('target_dir') or '').strip():
                        show_toast(self.frame, f"任务「{job_copy.name}」的“移动到文件夹”"
                                   f"步骤未设置目标文件夹", "warning")
                        return
            overall_finish = self._finish_key()
            self._sync_finish_action()   # 以启动时刻的值初始化持有器（运行中仍可改）
            self._log(f"[启动] 多任务模式：{len(prepared)} 个任务将按顺序执行")
            # 记录本次实际参与运行的任务，供 _on_finish 精确移除（保留未执行任务）
            self._executed_tasks = list(self.tasks)
        else:
            # 兼容旧用法：全部视频 + 当前流程（无任务清单时）
            if not self.items:
                show_toast(self.frame, "视频清单为空，请先扫描源文件夹", "warning")
                return
            steps = self.job.enabled_steps()
            if not steps:
                show_toast(self.frame, "流程中没有启用的步骤", "warning")
                return
            for s in steps:
                if s.type == STEP_MOVE and not (s.params.get('target_dir') or '').strip():
                    show_toast(self.frame, "有“移动到文件夹”步骤未设置目标文件夹", "warning")
                    return
            self.job.finish_action = self._finish_key()
            prepared = [(WorkflowJob.from_dict(self.job.to_dict()), list(self.items))]
            overall_finish = self.job.finish_action
            self._executed_tasks = []     # 旧用法无任务清单，无可清除的任务

        self._save_config()
        for _, its in prepared:
            for it in its:
                it.status = "待处理"
                it.message = ""
        self._refresh_tree()

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.NORMAL, text="⏸ 暂停")
        self.progress_var.set(0.0)
        self.status_var.set("开始执行…")

        ffmpeg_path = ""
        try:
            ffmpeg_path = self._guess_ffmpeg()
        except Exception:
            pass

        self.executor = WorkflowExecutor(
            self.job, [],
            tasks=prepared,
            overall_finish_action=overall_finish,
            resolve_finish_action=lambda: self._finish_action_live.get("value", "none"),
            max_workers=int(self.threads_var.get()),
            on_log=lambda m: self.frame.after(0, self._log, m),
            on_item=lambda it: self.frame.after(0, self._on_item_update, it),
            on_progress=lambda p, t="": self.frame.after(0, self._set_progress, p, t),
            on_finish=lambda stat: self.frame.after(0, self._on_finish, stat),
            ffmpeg_path=ffmpeg_path,
        )
        self.executor.start()

    def _on_item_update(self, item: WorkflowItem):
        """任务里的条目是独立副本；把状态同步回主视频清单中相同来源的视频，便于查看"""
        for it in self.items:
            if it.origin == item.origin:
                it.status = item.status
                it.message = item.message
        self._refresh_tree()

    def _stop(self):
        if self.executor:
            self.executor.stop()
            self.stop_btn.config(state=tk.DISABLED)

    def _toggle_pause(self):
        """暂停/继续切换：暂停当前任务（冻结转码/剪辑子进程或字幕逐条翻译），再次点击恢复"""
        ex = self.executor
        if ex is None or not ex.is_alive():
            show_toast(self.frame, "当前没有正在执行的任务", "warning")
            return
        if ex.paused:
            ex.resume()
            self.pause_btn.config(text="⏸ 暂停")
            self.status_var.set("已恢复执行")
            self._log("[暂停] 用户已恢复执行")
        else:
            ex.pause()
            self.pause_btn.config(text="▶ 继续")
            self.status_var.set("已暂停（当前步骤冻结，点击“继续”恢复）")
            self._log("[暂停] 用户已暂停执行")

    def _guess_ffmpeg(self) -> str:
        """优先复用字幕/转码模块里已配置好的 ffmpeg 路径"""
        try:
            from subtitle.system_utils import find_ffmpeg
            ok, path, _ = find_ffmpeg(None)
            if ok and path:
                return path
        except Exception:
            pass
        import shutil
        return shutil.which('ffmpeg') or ""

    def _set_progress(self, pct: float, text: str = ""):
        try:
            v = max(0.0, min(100.0, float(pct)))
            self.progress_var.set(v)
            self.pct_var.set(f"{int(round(v))}%")
            if text:
                self.status_var.set(text)
        except Exception:
            pass

    def _on_finish(self, stat: Dict[str, Any]):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.DISABLED, text="⏸ 暂停")
        # ---- 任务完成后释放资源，避免视频文件被 Python 占用 ----
        # 移除“本次已执行的任务”：已完成的任务从清单清除，未执行的（如本次未纳入运行）
        # 任务保留；并把结果写回配置文件，避免下次打开软件时重复显示已完成的任务。
        ran = getattr(self, '_executed_tasks', None)
        if ran is not None:
            exe = set(id(t) for t in ran)
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if id(t) not in exe]
            self._executed_tasks = None
            if len(self.tasks) != before:
                self._refresh_task_tree()
                self._refresh_tree()      # 取消视频清单中“已添加”高亮
                self._save_config()       # 持久化：已执行任务从配置文件移除
                self._log("[清理] 已移除本次已执行的任务，并同步更新配置文件")
        self.executor = None
        # 2) 强制垃圾回收：探测/播放器等 C 层对象（cv2.VideoCapture、打开的
        #    文件句柄）若处于引用环中不会立即释放，gc.collect() 确保句柄
        #    立刻归还系统，资源管理器中可正常移动/删除这些视频文件
        gc.collect()
        self._log("[清理] 已清空任务清单并释放视频文件占用")
        summary = (f"工作流结束：共 {stat.get('total', 0)}，完成 {stat.get('done', 0)}，"
                   f"失败 {stat.get('failed', 0)}，停止 {stat.get('stopped', 0)}")
        self.status_var.set(summary)
        level = 'success' if not stat.get('failed') else 'warning'
        show_toast(self.frame, summary, level)
        self._log(f"[结束] {summary}")
        self._refresh_tree()

    # ------------------------------------------------------------------
    # 日志（本地显示 + 同步写入主窗口的操作日志 Tab）
    # ------------------------------------------------------------------
    def _log(self, msg: str):
        text = str(msg)

        def _append():
            try:
                self.log_text.insert(tk.END, text + "\n")
                self.log_text.see(tk.END)
            except Exception:
                pass

        try:
            self.frame.after(0, _append)
        except Exception:
            print(text)

        # 同步到主窗口的日志 Tab（controller.log → LogTab）
        ctrl = self.controller
        if ctrl is not None and hasattr(ctrl, 'log'):
            try:
                ctrl.log(f"[工作流] {text}")
            except Exception:
                pass

    def log(self, message: str):
        """供主程序/其它选项卡调用"""
        self._log(message)

    def get_frame(self) -> ttk.Frame:
        return self.frame

    def cleanup(self):
        if self.executor and self.executor.is_alive():
            self.executor.stop()


# ----------------------------------------------------------------------
def main():
    root = tk.Tk()
    root.title("工作流串联（多任务串联 · 无人值守）")
    root.geometry("1500x950")
    tab = WorkflowTab(root)
    tab.frame.pack(fill=tk.BOTH, expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
