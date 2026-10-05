#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字幕生成主界面面板（可嵌入主程序选项卡，也可独立运行）

由原「日剧语音转中文字幕工具 v3.4.0」的 MainWindow 改造而来：
  * 从 tk.Tk 主窗口改为 ttk.Frame 面板，可被主程序的 Notebook 直接 add；
  * 所有任务状态提示改为右下角浮出提示（Toast），不再弹 messagebox；
  * 支持 GPU/CPU 切换，CUDA 不可用时自动切 CPU；单个视频在 GPU 上失败时自动改用 CPU 重试；
  * 任务列表支持拖放（含文件夹递归多级子目录）、多选（Ctrl/Shift）、移除选中、只处理选中、一键清空；
  * 任务完成后可选：无操作 / 计算机进入睡眠 / 关闭计算机。
"""
import os
import sys
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from datetime import datetime

from .config import (
    STATUS_PENDING, STATUS_EXTRACTING, STATUS_LOADING_MODEL,
    STATUS_RECOGNIZING, STATUS_TRANSLATING, STATUS_GENERATING,
    STATUS_COMPLETED, STATUS_ERROR, STATUS_CANCELLED,
    STATUS_COLORS, WHISPER_MODELS, ASR_ENGINES, TRANSLATOR_ENGINES,
    ENGINE_RECOMMENDATION, AFTER_FINISH_ACTIONS, GPU_ERROR_KEYWORDS,
    load_config, save_config,
)
from .ui_utils import sc, fit_and_center
from .system_utils import (
    find_ffmpeg, check_cuda_available, check_cublas, check_cudnn,
    resolve_model_path, resolve_translator_path, resolve_sakura_path,
    resolve_kotoba_path, resolve_nemo_path,
    check_model_path_valid, check_translator_path_valid,
    check_sakura_path_valid, check_kotoba_path_valid, check_nemo_path_valid,
    release_gpu_memory,
)
from .core import TaskItem, WorkerThread
from .dialogs import SettingsDialog, DependencyDialog

try:
    from subtitle import __version__ as _VERSION
except Exception:
    _VERSION = "3.4.1"


class SubtitlePanel:
    """字幕生成面板

    parent     : 父容器（主程序的 Notebook 或独立窗口）
    controller : 主程序控制器（提供 log() 方法），为 None 时表示独立运行
    """

    VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
                        ".m4v", ".mpg", ".mpeg", ".ts", ".rmvb", ".rm"}
    ROW_BG = "#f5f5f5"            # 任务行默认底色
    ROW_BG_SELECTED = "#cde4ff"   # 任务行选中底色

    def __init__(self, parent, controller=None, show_log_panel=True):
        self.parent = parent
        self.controller = controller
        self.is_standalone = controller is None
        self.show_log_panel = show_log_panel

        self.generator = None
        self.processing = False
        self.stopped_by_user = False
        self.config = load_config()

        self.tasks = []
        self.task_widgets = {}
        self.selected_ids = set()      # 当前选中的任务 id(task) 集合
        self._sel_anchor = None        # shift 范围选择的锚点任务
        self._process_only_ids = None  # “只处理选中”过滤集合（None=处理全部）
        self.workers = []
        self._session_id = 0           # 递增的 session ID，用于隔离新旧处理线程
        self._cpu_retry_ids = set()    # 已用 CPU 重试过的任务
        self._dnd_available = False
        self._dnd_registered = False
        self._toast_fallback = None

        self.frame = ttk.Frame(parent)
        self._build_ui()

        # 拖放需在窗口拿到句柄之后注册
        self.frame.after(400, self._setup_drag_drop)

        # 环境检测（FFmpeg / CUDA / 模型）放到后台线程，避免拖慢主程序启动
        threading.Thread(target=self._check_initial_state, daemon=True).start()

    # ============================================================
    # 基础工具：主线程调度 / 日志 / 右下角浮窗
    # ============================================================

    def _ui(self, func, *args):
        """把 UI 更新投递到主线程，避免子线程直接操作控件"""
        try:
            self.frame.after(0, lambda: func(*args))
        except Exception:
            pass

    def _toplevel(self):
        """获取当前窗口（Toplevel / Tk），用于对话框的 parent"""
        try:
            return self.frame.winfo_toplevel()
        except Exception:
            return None

    def _log(self, message):
        """写日志：面板内日志区 + 主程序日志选项卡"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = "[" + timestamp + "] " + str(message)

        def _write():
            try:
                text = getattr(self, "log_text", None)
                if text is not None and text.winfo_exists():
                    text.config(state="normal")
                    text.insert("end", line + "\n")
                    text.see("end")
                    text.config(state="disabled")
            except Exception:
                pass

        self._ui(_write)
        if self.controller is not None and hasattr(self.controller, "log"):
            try:
                self.controller.log("[字幕] " + line)
            except Exception:
                pass

    def show_toast(self, message, level="info", duration=None):
        """右下角浮出提示（与项目其它选项卡一致的样式），失败时降级为日志"""
        def _run():
            ok = False
            try:
                from toast import show_toast as _show_toast
                ok = _show_toast(self.frame, message, level, duration)
            except Exception:
                ok = False
            if not ok:
                self._log("[" + level + "] " + message)

        self._ui(_run)

    # ============================================================
    # 界面构建
    # ============================================================

    def _build_ui(self):
        main_frame = ttk.Frame(self.frame, padding="8")
        main_frame.pack(fill="both", expand=True)

        # ---------- 标题行 + 常用功能按钮 ----------
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(title_frame, text="日语视频转中文字幕",
                  font=("Microsoft YaHei", 16, "bold")).pack(side="left")
        ttk.Label(title_frame, text="v" + str(_VERSION), foreground="#999",
                  font=("Microsoft YaHei", 10)).pack(side="left", padx=6, pady=(4, 0))

        for text, cmd in (("设置", self._show_settings),
                          ("依赖检查", self._show_deps),
                          ("使用帮助", self._show_help),
                          ("关于", self._show_about)):
            ttk.Button(title_frame, text=text, command=cmd).pack(side="right", padx=2)

        # ---------- 第一行：任务操作 + 设备 + 双语 + 完成后动作 ----------
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.pack(fill="x", pady=3)

        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.pack(side="left")
        ttk.Button(btn_frame, text="+ 添加视频", command=self._add_videos).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="移除选中", command=self._remove_selected).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="清空列表", command=self._clear_tasks).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="▶ 转换选中", command=self._start_selected).pack(side="left", padx=2)

        # 拖入文件夹时是否递归扫描子文件夹
        self.recursive_var = tk.BooleanVar(value=bool(self.config.get("scan_subfolders", True)))
        ttk.Checkbutton(btn_frame, text="包含子文件夹", variable=self.recursive_var,
                        command=self._on_recursive_toggle).pack(side="left", padx=(10, 0))

        settings_frame = ttk.Frame(ctrl_frame)
        settings_frame.pack(side="left", padx=10)
        ttk.Label(settings_frame, text="设备:").pack(side="left", padx=(8, 0))
        saved_device = self.config.get("device")
        if saved_device in ("cpu", "cuda"):
            default_device = saved_device
        else:
            default_device = "cuda" if check_cuda_available()[0] else "cpu"
        self.device_var = tk.StringVar(value=default_device)
        ttk.Radiobutton(settings_frame, text="CPU", variable=self.device_var, value="cpu",
                        command=self._on_device_change).pack(side="left", padx=2)
        ttk.Radiobutton(settings_frame, text="GPU", variable=self.device_var, value="cuda",
                        command=self._on_device_change).pack(side="left", padx=2)

        self.bilingual_var = tk.BooleanVar(value=bool(self.config.get("bilingual", True)))
        ttk.Checkbutton(settings_frame, text="双语（日文+中文）", variable=self.bilingual_var,
                        command=self._on_bilingual_change).pack(side="left", padx=8)

        # 任务完成后操作
        finish_frame = ttk.Frame(ctrl_frame)
        finish_frame.pack(side="right", padx=(6, 4))
        ttk.Label(finish_frame, text="完成后:").pack(side="left")
        self.after_finish_var = tk.StringVar(value=self.config.get("after_finish_action", "none"))
        finish_combo = ttk.Combobox(finish_frame, textvariable=self.after_finish_var,
                                    values=list(AFTER_FINISH_ACTIONS.keys()),
                                    state="readonly", width=12)
        finish_combo.pack(side="left", padx=2)
        self.after_finish_desc_var = tk.StringVar(
            value=AFTER_FINISH_ACTIONS.get(self.after_finish_var.get(), ""))
        ttk.Label(finish_frame, textvariable=self.after_finish_desc_var,
                  foreground="#666", font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

        def _update_finish_desc(event=None):
            action = self.after_finish_var.get()
            self.after_finish_desc_var.set(AFTER_FINISH_ACTIONS.get(action, action))
            self.config["after_finish_action"] = action
            save_config(self.config)

        finish_combo.bind("<<ComboboxSelected>>", _update_finish_desc)

        action_frame = ttk.Frame(ctrl_frame)
        action_frame.pack(side="right")
        self.btn_start_all = ttk.Button(action_frame, text="▶ 开始全部",
                                        command=lambda: self._start_all(False))
        self.btn_start_all.pack(side="left", padx=2)
        self.btn_stop_all = ttk.Button(action_frame, text="⏹ 停止全部",
                                       command=self._stop_all, state="disabled")
        self.btn_stop_all.pack(side="left", padx=2)

        # ---------- 第二行：语音引擎 + 翻译模型选择 ----------
        engine_frame = ttk.Frame(main_frame)
        engine_frame.pack(fill="x", pady=3)

        ttk.Label(engine_frame, text="语音模型:").pack(side="left")
        self.asr_engine_var = tk.StringVar(value=self.config.get("asr_engine", "kotoba-whisper"))
        asr_combo = ttk.Combobox(engine_frame, textvariable=self.asr_engine_var,
                                 values=list(ASR_ENGINES.keys()),
                                 state="readonly", width=18)
        asr_combo.pack(side="left", padx=4)
        asr_combo.bind("<<ComboboxSelected>>", self._on_engine_change)

        ttk.Label(engine_frame, text="模型大小:").pack(side="left", padx=(12, 0))
        self.model_var = tk.StringVar(value=self.config.get("whisper_model", "medium"))
        self.model_combo = ttk.Combobox(engine_frame, textvariable=self.model_var,
                                        values=list(WHISPER_MODELS.keys()),
                                        state="readonly", width=10)
        self.model_combo.pack(side="left", padx=4)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)

        ttk.Label(engine_frame, text="翻译模型:").pack(side="left", padx=(12, 0))
        self.translator_engine_var = tk.StringVar(
            value=self.config.get("translator_engine", "NLLB-200"))
        trans_combo = ttk.Combobox(engine_frame, textvariable=self.translator_engine_var,
                                   values=list(TRANSLATOR_ENGINES.keys()),
                                   state="readonly", width=18)
        trans_combo.pack(side="left", padx=4)
        trans_combo.bind("<<ComboboxSelected>>", self._on_engine_change)

        # ---------- 第三行：模型介绍与推荐 ----------
        hint_frame = ttk.LabelFrame(main_frame, text="模型介绍与使用建议", padding="5")
        hint_frame.pack(fill="x", pady=(0, 4))
        self.asr_hint_var = tk.StringVar(value="")
        self.trans_hint_var = tk.StringVar(value="")
        self.recommend_var = tk.StringVar(value="💡 " + ENGINE_RECOMMENDATION)
        ttk.Label(hint_frame, textvariable=self.asr_hint_var, foreground="#2c5f8a",
                  wraplength=sc(1050), justify="left").pack(anchor="w")
        ttk.Label(hint_frame, textvariable=self.trans_hint_var, foreground="#2c5f8a",
                  wraplength=sc(1050), justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Label(hint_frame, textvariable=self.recommend_var, foreground="#e67e22",
                  wraplength=sc(1050), justify="left").pack(anchor="w", pady=(3, 0))
        self._update_engine_hint()

        # ---------- 运行状态 ----------
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill="x", pady=2)
        self.global_status_var = tk.StringVar(value="就绪 | 可将视频或文件夹拖入下方列表")
        ttk.Label(status_frame, textvariable=self.global_status_var,
                  font=("Microsoft YaHei", 10)).pack(anchor="w")

        # ---------- 任务列表 ----------
        list_frame = ttk.LabelFrame(main_frame, text="任务列表（可直接拖入视频文件或文件夹）",
                                    padding="5")
        list_frame.pack(fill="both", expand=True, pady=3)
        self.list_frame = list_frame

        self.col_widths = {
            "status": sc(92),
            "progress": sc(90),
            "name": sc(300),
            "detail": sc(180),
            "time": sc(76),
            "action": sc(96),
        }

        header_frame = ttk.Frame(list_frame)
        header_frame.pack(fill="x", pady=(0, 2))
        for key, text in (("status", "状态"), ("progress", "进度"), ("name", "文件名"),
                          ("detail", "当前操作"), ("time", "耗时"), ("action", "操作")):
            cell = tk.Frame(header_frame, width=self.col_widths[key], height=sc(22))
            cell.pack_propagate(False)
            cell.pack(side="left", padx=2)
            ttk.Label(cell, text=text, font=("Microsoft YaHei", 10, "bold")).pack(side="left")
        # 表头区域滚轮也能滚动列表
        self._bind_mousewheel(header_frame)
        ttk.Separator(list_frame, orient="horizontal").pack(fill="x")

        canvas = tk.Canvas(list_frame, highlightthickness=0, bd=0)
        self.task_canvas = canvas
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.tasks_container = ttk.Frame(canvas)
        self.tasks_container.bind("<Configure>", self._sync_scrollregion)
        self._canvas_window = canvas.create_window((0, 0), window=self.tasks_container,
                                                   anchor="nw", width=sc(860))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_canvas_resize(event):
            try:
                canvas.itemconfig(self._canvas_window, width=event.width)
            except Exception:
                pass
            # 可视区尺寸变化后重新同步滚动区域
            self._sync_scrollregion()

        canvas.bind("<Configure>", _on_canvas_resize)

        # 鼠标滚轮：仅在内容超过可视区时生效，列表内任意位置均可滚动
        canvas.bind("<MouseWheel>", self._on_list_mousewheel)
        self.tasks_container.bind("<MouseWheel>", self._on_list_mousewheel)

        self._drop_widgets = (list_frame, canvas, self.tasks_container, header_frame)

        # ---------- 日志区 ----------
        if self.show_log_panel:
            log_frame = ttk.LabelFrame(main_frame, text="处理日志", padding="5")
            log_frame.pack(fill="x", pady=3)
            self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD,
                                                      font=("Consolas", 10), height=6)
            self.log_text.pack(fill="both", expand=True)
            self.log_text.config(state="disabled")
        else:
            self.log_text = None

        try:
            style = ttk.Style()
            style.configure("Accent.TButton", font=("Microsoft YaHei", 11, "bold"))
        except Exception:
            pass

    # ============================================================
    # 拖放
    # ============================================================

    def _setup_drag_drop(self):
        """注册拖放目标（仅任务列表区域，避免影响主程序其它选项卡）。"""
        if self._dnd_registered or self._dnd_available:
            return
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
        except Exception as e:
            self._dnd_available = False
            self._log("拖放功能: 未启用（" + str(e) + "）| pip install tkinterdnd2 后可用")
            return

        root = self._toplevel()
        required = False
        for name in ("_require", "require"):
            fn = getattr(TkinterDnD, name, None)
            if callable(fn):
                try:
                    fn(root)
                    required = True
                    break
                except Exception:
                    continue
        if not required:
            self._log("拖放功能: tkdnd 初始化失败")
            return

        try:
            for widget in self._drop_widgets:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            self._dnd_available = True
            self._dnd_registered = True
            self._log("拖放功能: 已启用 | 可直接把视频文件或文件夹拖入任务列表")
        except Exception as e:
            self._dnd_available = False
            self._log("拖放功能: 初始化失败 | " + str(e))

    def _on_drag_enter(self, event):
        try:
            self.list_frame.config(text="松开鼠标，把视频/文件夹添加到任务列表")
            self.task_canvas.config(bg="#d6eaff")
        except Exception:
            pass
        return event.action

    def _on_drag_leave(self, event=None):
        try:
            self.list_frame.config(text="任务列表（可直接拖入视频文件或文件夹）")
            self.task_canvas.config(bg="SystemButtonFace")
        except Exception:
            pass
        if event is not None:
            return event.action

    def _on_drop(self, event):
        """拖放添加：支持多个视频文件，或文件夹（可递归所有子文件夹）。"""
        try:
            dropped = self.frame.tk.splitlist(event.data)
        except Exception:
            dropped = [event.data]

        videos = []
        skipped = 0
        for path in dropped:
            path = str(path).strip().strip("{}").strip()
            if not path:
                continue
            if os.path.isdir(path):
                if self.recursive_var.get():
                    for dirpath, _dirnames, filenames in os.walk(path):
                        for name in sorted(filenames):
                            if os.path.splitext(name)[1].lower() in self.VIDEO_EXTENSIONS:
                                videos.append(os.path.join(dirpath, name))
                else:
                    try:
                        for name in sorted(os.listdir(path)):
                            full = os.path.join(path, name)
                            if os.path.isfile(full) and \
                                    os.path.splitext(name)[1].lower() in self.VIDEO_EXTENSIONS:
                                videos.append(full)
                    except Exception:
                        pass
            elif os.path.splitext(path)[1].lower() in self.VIDEO_EXTENSIONS:
                videos.append(path)
            else:
                skipped += 1

        added = 0
        for v in videos:
            if any(t.video_path == v for t in self.tasks):
                continue
            self._add_task(v)
            added += 1

        if added:
            self._log("拖放添加: " + str(added) + " 个视频")
            self.show_toast("已添加 " + str(added) + " 个视频", "info")
        if skipped:
            self._log("拖放忽略: " + str(skipped) + " 个非视频文件")
        if not added and not skipped:
            self._log("拖放: 未找到可添加的视频文件")
            self.show_toast("未找到可添加的视频文件", "warning")
        self._on_drag_leave()
        return event.action

    # ============================================================
    # 引擎 / 模型选择
    # ============================================================

    def _on_model_change(self, event=None):
        model = self.model_var.get()
        info = WHISPER_MODELS.get(model, {})
        self.config["whisper_model"] = model
        save_config(self.config)
        self._update_engine_hint()
        self._log("切换语音模型大小: " + model + " (" + info.get('size', '?') + ")")

    def _on_engine_change(self, event=None):
        self.config["asr_engine"] = self.asr_engine_var.get()
        self.config["translator_engine"] = self.translator_engine_var.get()
        save_config(self.config)
        self._update_engine_hint()
        self._log("语音模型: " + self.asr_engine_var.get() +
                  " | 翻译模型: " + self.translator_engine_var.get())

    def _on_device_change(self):
        self.config["device"] = self.device_var.get()
        save_config(self.config)
        self._log("处理设备: " + ("GPU (CUDA)" if self.device_var.get() == "cuda" else "CPU"))

    def _on_bilingual_change(self):
        self.config["bilingual"] = bool(self.bilingual_var.get())
        save_config(self.config)

    def _on_recursive_toggle(self):
        self.config["scan_subfolders"] = bool(self.recursive_var.get())
        save_config(self.config)
        mode = "递归扫描所有子文件夹" if self.recursive_var.get() else "仅当前文件夹"
        self._log("文件夹拖放扫描方式: " + mode)

    def _update_engine_hint(self):
        asr = self.asr_engine_var.get()
        trans = self.translator_engine_var.get()

        asr_hint = "▸ 语音模型「" + asr + "」: " + ASR_ENGINES.get(asr, "")
        if asr == "faster-whisper":
            model = self.model_var.get()
            info = WHISPER_MODELS.get(model, {})
            asr_hint += (" | 当前大小「" + model + "」: 体积 " + info.get("size", "?") +
                         "，速度" + info.get("speed", "?") +
                         "，精度" + info.get("accuracy", "?") +
                         "，显存需求 " + info.get("vram", "?"))
        self.asr_hint_var.set(asr_hint)
        self.trans_hint_var.set("▸ 翻译模型「" + trans + "」: " + TRANSLATOR_ENGINES.get(trans, ""))

        # 模型大小选择仅对 faster-whisper 有效
        try:
            self.model_combo.config(state="readonly" if asr == "faster-whisper" else "disabled")
        except Exception:
            pass

    # ============================================================
    # 任务列表操作
    # ============================================================

    def _sync_scrollregion(self, event=None):
        """按内容高度同步滚动区域。

        内容不超过可视区时把滚动区域收敛为可视区大小，从根本上禁止滚动
        （否则画布默认允许在空区域滚动，导致列表内容被上下拖动）。
        """
        canvas = getattr(self, "task_canvas", None)
        if canvas is None:
            return
        try:
            bbox = canvas.bbox("all")
            if not bbox:
                return
            content_h = bbox[3] - bbox[1]
            view_h = canvas.winfo_height()
            width = bbox[2] - bbox[0]
            if content_h <= view_h:
                canvas.configure(scrollregion=(0, 0, width, view_h))
                canvas.yview_moveto(0)
            else:
                canvas.configure(scrollregion=bbox)
        except Exception:
            pass

    def _on_list_mousewheel(self, event):
        """任务列表滚轮处理：内容未超出可视区时不滚动。"""
        canvas = getattr(self, "task_canvas", None)
        if canvas is None:
            return None
        try:
            bbox = canvas.bbox("all")
            view_h = canvas.winfo_height()
            if not bbox or (bbox[3] - bbox[1]) <= view_h:
                return "break"
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"
        except Exception:
            return None

    def _bind_mousewheel(self, widget):
        """递归给控件绑定滚轮事件。

        使得鼠标落在行内的表格子控件（标签、按钮、进度条等）上时，
        仍能滚动任务列表，而不是滚轮失效。
        """
        try:
            widget.bind("<MouseWheel>", self._on_list_mousewheel)
            for child in widget.winfo_children():
                self._bind_mousewheel(child)
        except Exception:
            pass

    def _add_videos(self):
        paths = filedialog.askopenfilenames(
            title="选择视频文件（可多选）",
            filetypes=[("视频文件", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm"),
                       ("所有文件", "*.*")])
        added = 0
        for path in paths:
            if not any(t.video_path == path for t in self.tasks):
                self._add_task(path)
                added += 1
        if added:
            self.show_toast("已添加 " + str(added) + " 个视频", "info")

    def _add_task(self, video_path):
        task = TaskItem(video_path)
        self.tasks.append(task)
        self._create_task_widget(task)
        self._update_global_status()

    def _create_task_widget(self, task):
        frame = tk.Frame(self.tasks_container, bg=self.ROW_BG)
        frame.pack(fill="x", pady=1)

        def cell(key):
            c = tk.Frame(frame, width=self.col_widths[key], height=sc(24), bg=self.ROW_BG)
            c.pack_propagate(False)
            c.pack(side="left", padx=2)
            return c

        status_label = tk.Label(cell("status"), text=task.status, bg=self.ROW_BG,
                                fg=STATUS_COLORS.get(task.status, "#000"), anchor="w",
                                font=("Microsoft YaHei", 9))
        status_label.pack(side="left")

        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(cell("progress"), variable=progress_var, maximum=100,
                                       length=self.col_widths["progress"] - sc(10),
                                       mode="determinate")
        progress_bar.pack(side="left")

        name_label = tk.Label(cell("name"), text=task.video_name, anchor="w",
                              bg=self.ROW_BG, font=("Microsoft YaHei", 9))
        name_label.pack(side="left", fill="x", expand=True)

        detail_label = tk.Label(cell("detail"), text="", fg="#666", anchor="w",
                                bg=self.ROW_BG, font=("Microsoft YaHei", 9))
        detail_label.pack(side="left", fill="x", expand=True)

        time_label = tk.Label(cell("time"), text="", anchor="w", bg=self.ROW_BG,
                              font=("Microsoft YaHei", 9))
        time_label.pack(side="left")

        btn_remove = tk.Button(cell("action"), text="移除", relief="flat", bg=self.ROW_BG,
                               fg="#c0392b", font=("Microsoft YaHei", 9), cursor="hand2",
                               command=lambda t=task: self._remove_single(t))
        btn_remove.pack(side="left", padx=4)

        sep = ttk.Separator(self.tasks_container, orient="horizontal")
        sep.pack(fill="x", pady=1)

        clickables = [frame, status_label, name_label, detail_label, time_label] + \
                     [w for w in frame.winfo_children()]
        for w in set(clickables):
            w.bind("<Button-1>", lambda e, t=task: self._on_task_click(e, t))
            w.bind("<Button-3>", lambda e, t=task: self._on_task_right_click(e, t))

        # 让鼠标落在该行任意控件（含表格子、按钮）上时也能滚动任务列表
        self._bind_mousewheel(frame)
        self._bind_mousewheel(sep)

        self.task_widgets[id(task)] = {
            "frame": frame,
            "sep": sep,
            "cells": list(frame.winfo_children()),
            "labels": [status_label, name_label, detail_label, time_label],
            "status_label": status_label,
            "progress_var": progress_var,
            "progress_bar": progress_bar,
            "name_label": name_label,
            "detail_label": detail_label,
            "time_label": time_label,
        }

    def _update_task_ui(self, task):
        widgets = self.task_widgets.get(id(task))
        if not widgets:
            return

        def update():
            try:
                widgets["status_label"].config(
                    text=task.status, fg=STATUS_COLORS.get(task.status, "#000"))
                widgets["progress_var"].set(task.progress)
                widgets["detail_label"].config(text=task.status_detail[:22])
                widgets["time_label"].config(text=task.elapsed)
            except Exception:
                pass

        self._ui(update)

    def _on_task_click(self, event, task):
        ctrl = bool(event.state & 0x4)   # Control
        shift = bool(event.state & 0x1)  # Shift
        tid = id(task)

        if shift and self._sel_anchor is not None and self._sel_anchor in self.tasks:
            i0 = self.tasks.index(self._sel_anchor)
            i1 = self.tasks.index(task)
            lo, hi = min(i0, i1), max(i0, i1)
            if not ctrl:
                self.selected_ids.clear()
            for t in self.tasks[lo:hi + 1]:
                self.selected_ids.add(id(t))
        elif ctrl:
            if tid in self.selected_ids:
                self.selected_ids.discard(tid)
            else:
                self.selected_ids.add(tid)
                self._sel_anchor = task
        else:
            self.selected_ids = {tid}
            self._sel_anchor = task

        self._refresh_row_highlights()
        return "break"

    def _on_task_right_click(self, event, task):
        if id(task) not in self.selected_ids:
            self.selected_ids = {id(task)}
            self._sel_anchor = task
            self._refresh_row_highlights()

        menu = tk.Menu(self._toplevel(), tearoff=0, font=("Microsoft YaHei", 10))
        n = len(self.selected_ids)
        menu.add_command(label="只处理选中的 " + str(n) + " 个任务", command=self._start_selected)
        menu.add_command(label="移除选中的 " + str(n) + " 个任务", command=self._remove_selected)
        menu.add_separator()
        menu.add_command(label="清空列表", command=self._clear_tasks)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _start_selected(self):
        if not self.selected_ids:
            self.show_toast("请先点击选择要处理的视频（Ctrl/Shift 可多选）", "warning")
            return
        self._start_all(only_selected=True)

    def _refresh_row_highlights(self):
        for t in self.tasks:
            widgets = self.task_widgets.get(id(t))
            if not widgets:
                continue
            bg = self.ROW_BG_SELECTED if id(t) in self.selected_ids else self.ROW_BG
            try:
                widgets["frame"].config(bg=bg)
                for c in widgets["cells"]:
                    try:
                        c.config(bg=bg)
                    except Exception:
                        pass
                for lbl in widgets["labels"]:
                    lbl.config(bg=bg)
            except Exception:
                pass

    WORKING_STATUS = (STATUS_EXTRACTING, STATUS_LOADING_MODEL, STATUS_RECOGNIZING,
                      STATUS_TRANSLATING, STATUS_GENERATING)

    def _remove_single(self, task):
        self.selected_ids = {id(task)}
        self._remove_selected()

    def _remove_selected(self):
        """从列表中移除选中的任务（正在处理中的任务不能移除）。"""
        if not self.selected_ids:
            self.show_toast("请先点击选择要移除的视频（Ctrl/Shift 可多选）", "warning")
            return

        removable = [t for t in self.tasks if id(t) in self.selected_ids]
        busy = [t for t in removable if t.status in self.WORKING_STATUS]
        removed = 0
        for t in removable:
            if t.status in self.WORKING_STATUS:
                continue
            widgets = self.task_widgets.pop(id(t), None)
            if widgets:
                try:
                    widgets["frame"].destroy()
                    if widgets.get("sep"):
                        widgets["sep"].destroy()
                except Exception:
                    pass
            if t in self.tasks:
                self.tasks.remove(t)
            self.selected_ids.discard(id(t))
            self._cpu_retry_ids.discard(id(t))
            removed += 1

        if self._sel_anchor is not None and self._sel_anchor not in self.tasks:
            self._sel_anchor = None
        if removed:
            self._log("已移除 " + str(removed) + " 个任务")
            self.show_toast("已移除 " + str(removed) + " 个任务", "info")
        if busy:
            self.show_toast(str(len(busy)) + " 个正在处理中的任务已跳过", "warning")
        self._refresh_row_highlights()
        self._update_global_status()
        self._ui(self._sync_scrollregion)

    def _clear_tasks(self):
        if self.processing:
            # 不弹窗确认：先停止全部任务，再清空
            self._log("正在处理中，先停止全部任务后再清空列表")
            self._stop_all()
        if not self.tasks:
            return
        for widgets in self.task_widgets.values():
            try:
                widgets["frame"].destroy()
                if widgets.get("sep"):
                    widgets["sep"].destroy()
            except Exception:
                pass
        count = len(self.tasks)
        self.tasks.clear()
        self.task_widgets.clear()
        self.selected_ids.clear()
        self._sel_anchor = None
        self._cpu_retry_ids.clear()
        try:
            self.task_canvas.yview_moveto(0)
            self.task_canvas.xview_moveto(0)
        except Exception:
            pass
        self._log("已清空列表（" + str(count) + " 个任务）")
        self.show_toast("已清空列表（" + str(count) + " 个任务）", "info")
        self._update_global_status()
        self._ui(self._sync_scrollregion)

    def _update_global_status(self):
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.status == STATUS_COMPLETED)
        pending = sum(1 for t in self.tasks if t.status == STATUS_PENDING)
        processing = sum(1 for t in self.tasks if t.status in self.WORKING_STATUS)
        error = sum(1 for t in self.tasks if t.status == STATUS_ERROR)
        status_text = ("总计: " + str(total) + " | 等待: " + str(pending) +
                       " | 处理中: " + str(processing) + " | 完成: " + str(completed) +
                       " | 出错: " + str(error) +
                       (" | 已选: " + str(len(self.selected_ids)) if self.selected_ids else ""))
        self.global_status_var.set(status_text)

    # ============================================================
    # 批量处理
    # ============================================================

    def _start_all(self, only_selected=False):
        if not self.tasks:
            self.show_toast("请先添加视频文件（可拖入视频或文件夹）", "warning")
            return
        if self.processing:
            self.show_toast("正在处理中，请先停止后再开始", "warning")
            return

        if only_selected:
            self._process_only_ids = set(self.selected_ids)
        else:
            self._process_only_ids = None

        ffmpeg_found, _, _ = find_ffmpeg(self.config)
        if not ffmpeg_found:
            self.show_toast("未找到 FFmpeg，请在【设置】中指定 ffmpeg.exe 路径", "error")
            self._log("FFmpeg 未找到，处理中止")
            return

        model = self.model_var.get()
        asr_engine = self.asr_engine_var.get()
        translator_engine = self.translator_engine_var.get()

        model_path = None
        kotoba_path = None
        nemo_path = None
        if asr_engine == "faster-whisper":
            model_path = resolve_model_path(model, self.config)
            if not model_path:
                self.show_toast("未找到 faster-whisper 模型，请在【设置】中指定模型路径", "error")
                return
            valid, msg = check_model_path_valid(model_path)
            if not valid:
                self.show_toast("语音模型路径无效: " + msg, "error")
                return
        elif asr_engine == "kotoba-whisper":
            kotoba_path = resolve_kotoba_path(self.config)
            if not kotoba_path:
                self.show_toast("未找到 kotoba-whisper 模型，请在【设置】中指定模型目录", "error")
                return
            valid, msg = check_kotoba_path_valid(kotoba_path)
            if not valid:
                self.show_toast("语音模型路径无效: " + msg, "error")
                return
        else:
            nemo_path = resolve_nemo_path(self.config)
            if not nemo_path:
                self.show_toast("未找到 ReazonSpeech NeMo 模型（.nemo），请在【设置】中指定", "error")
                return
            valid, msg = check_nemo_path_valid(nemo_path)
            if not valid:
                self.show_toast("语音模型路径无效: " + msg, "error")
                return

        translator_path = None
        sakura_path = None
        if translator_engine.startswith("Sakura"):
            sakura_path = resolve_sakura_path(self.config, engine=translator_engine)
            if not sakura_path:
                self.show_toast("未找到 " + translator_engine + " 翻译模型（.gguf），请在【设置】中指定", "error")
                return
            valid, msg = check_sakura_path_valid(sakura_path)
            if not valid:
                self.show_toast("翻译模型路径无效: " + msg, "error")
                return
        else:
            translator_path = resolve_translator_path(self.config)
            if not translator_path:
                self.show_toast("未找到 NLLB-200 翻译模型，请在【设置】中指定模型目录", "error")
                return
            valid, msg = check_translator_path_valid(translator_path)
            if not valid:
                self.show_toast("翻译模型路径无效: " + msg, "error")
                return

        # GPU 不可用时自动切换到 CPU（不弹窗打断无人值守流程）
        device = self.device_var.get()
        if device == "cuda":
            cuda_ok, cuda_msg = check_cuda_available()
            cublas_ok, cublas_msg = check_cublas()
            cudnn_ok, cudnn_msg = check_cudnn()
            if not (cuda_ok and cublas_ok and cudnn_ok):
                device = "cpu"
                self.device_var.set("cpu")
                self.config["device"] = "cpu"
                save_config(self.config)
                self._log("CUDA/GPU 不可用，已自动切换到 CPU 模式 | " +
                          cuda_msg + " | " + cublas_msg + " | " + cudnn_msg)
                self.show_toast("GPU 加速不可用，已自动切换为 CPU 处理", "warning")

        self._session_id += 1
        session_id = self._session_id
        self.processing = True
        self.stopped_by_user = False
        self._cpu_retry_ids = set()
        self._session_start_time = time.time()
        self._session_task_count = 0
        self.btn_start_all.config(state="disabled")
        self.btn_stop_all.config(state="normal")

        for task in self.tasks:
            if task.status in (STATUS_PENDING, STATUS_ERROR, STATUS_CANCELLED):
                task.set_status(STATUS_PENDING)
                task.progress = 0
                task.error_msg = ""
                self._update_task_ui(task)

        if self._process_only_ids is not None:
            self._log("开始处理选中的 " + str(len(self._process_only_ids)) +
                      " 个视频（共 " + str(len(self.tasks)) + " 个）")
        else:
            self._log("开始批量处理，共 " + str(len(self.tasks)) + " 个视频")
        self._log("语音模型: " + asr_engine +
                  (" | 大小: " + model if model_path else "") +
                  " | 设备: " + device)
        self._log("翻译模型: " + translator_engine + " | 字幕: " +
                  ("双语（日文+中文）" if self.bilingual_var.get() else "仅中文"))

        engine_opts = {
            "asr_engine": asr_engine,
            "translator_engine": translator_engine,
            "kotoba_path": kotoba_path,
            "nemo_path": nemo_path,
            "sakura_path": sakura_path,
        }
        threading.Thread(
            target=self._process_queue,
            args=(model_path, device, translator_path, engine_opts, session_id),
            daemon=True).start()

    def _run_worker(self, task, model_path, device, compute_type,
                    translator_path, engine_opts, bilingual):
        """运行单个任务的工作线程并等待结束。"""
        worker = WorkerThread(
            task=task,
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            bilingual=bilingual,
            on_progress=self._update_task_ui,
            on_status=self._update_task_ui,
            on_complete=self._on_task_complete,
            translator_path=translator_path,
            asr_engine=engine_opts["asr_engine"],
            translator_engine=engine_opts["translator_engine"],
            kotoba_path=engine_opts["kotoba_path"],
            nemo_path=engine_opts["nemo_path"],
            sakura_path=engine_opts["sakura_path"],
        )
        self.workers.append(worker)
        worker.start()
        worker.join()

    def _is_gpu_error(self, task):
        msg = ((task.error_msg or "") + " " + (task.status_detail or "")).lower()
        return any(k in msg for k in GPU_ERROR_KEYWORDS)

    def _process_queue(self, model_path, device, translator_path, engine_opts, session_id):
        self._active_session_id = session_id
        bilingual = self.bilingual_var.get()

        for task in self.tasks:
            if not self.processing or self._active_session_id != session_id:
                break
            if task.status != STATUS_PENDING:
                continue
            if self._process_only_ids is not None and id(task) not in self._process_only_ids:
                continue

            task.set_status(STATUS_EXTRACTING, "准备处理...")
            self._update_task_ui(task)
            self._update_global_status()
            self._log("开始处理: " + task.video_name)
            self._session_task_count += 1

            compute_type = "float16" if device == "cuda" else "int8"
            self._run_worker(task, model_path, device, compute_type,
                             translator_path, engine_opts, bilingual)

            # GPU 上失败（CUDA/显存/不支持的格式等）→ 自动改用 CPU 重试一次
            if (task.status == STATUS_ERROR and device == "cuda"
                    and id(task) not in self._cpu_retry_ids and self._is_gpu_error(task)):
                self._cpu_retry_ids.add(id(task))
                self._log("GPU 处理失败，自动改用 CPU 重试: " + task.video_name +
                          " | " + (task.error_msg or "")[:120])
                self.show_toast("GPU 处理失败，已自动改用 CPU 重试: " + task.video_name, "warning")
                task.set_status(STATUS_PENDING)
                task.progress = 0
                task.error_msg = ""
                task.start_time = None
                self._update_task_ui(task)
                self._run_worker(task, model_path, "cpu", "int8",
                                 translator_path, engine_opts, bilingual)

            if task.status == STATUS_COMPLETED:
                self._log("完成: " + task.video_name + " | 耗时: " + task.elapsed)
            elif task.status == STATUS_ERROR:
                self._log("出错: " + task.video_name + " | " + (task.error_msg or "")[:120])

            self._update_task_ui(task)
            self._update_global_status()

        self._ui(self._on_all_complete, session_id)

    def _on_task_complete(self, task):
        self._update_task_ui(task)
        self._update_global_status()

    def _on_all_complete(self, session_id):
        if session_id != self._session_id:
            return

        self.processing = False
        self.btn_start_all.config(state="normal")
        self.btn_stop_all.config(state="disabled")
        self.workers.clear()

        # 全部任务结束：在后台线程释放显存/GPU 资源，避免阻塞 UI
        threading.Thread(target=self._release_gpu_and_log, daemon=True).start()

        completed = sum(1 for t in self.tasks if t.status == STATUS_COMPLETED)
        error = sum(1 for t in self.tasks if t.status == STATUS_ERROR)
        cancelled = sum(1 for t in self.tasks if t.status == STATUS_CANCELLED)

        elapsed = time.time() - getattr(self, "_session_start_time", time.time())
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        time_str = (str(hours) + " 小时 " if hours else "") + str(minutes) + " 分 " + str(seconds) + " 秒"
        self._log("本次共处理 " + str(getattr(self, "_session_task_count", 0)) +
                  " 个视频，总耗时 " + time_str)

        if self.stopped_by_user:
            self._log("批量处理已停止 | 完成: " + str(completed) +
                      " | 出错: " + str(error) + " | 已取消: " + str(cancelled))
            self.show_toast("已停止处理", "info")
            self._update_global_status()
            self.stopped_by_user = False
            return

        self._log("批量处理完成！完成: " + str(completed) + " | 出错: " + str(error))
        self.show_toast("批量处理完成！\n完成: " + str(completed) + " | 出错: " + str(error),
                        "success" if not error else "warning", 5000)

        action = self.config.get("after_finish_action", "none")
        if action in ("sleep", "shutdown"):
            self._log("无人值守模式：即将自动执行 [" + AFTER_FINISH_ACTIONS.get(action, action) + "]")
            self.show_toast("即将执行: " + AFTER_FINISH_ACTIONS.get(action, action), "warning", 6000)
        self._execute_after_finish_action()

    def _release_gpu_and_log(self):
        try:
            release_gpu_memory()
            self._ui(self._log, "已释放模型与 GPU 显存资源")
        except Exception as e:
            self._ui(self._log, "释放 GPU 资源时出错: " + str(e))

    def _execute_after_finish_action(self):
        """根据用户设置执行睡眠或关机（无人值守，直接执行，不弹确认）。"""
        action = self.config.get("after_finish_action", "none")
        if action == "none":
            return
        if action == "sleep":
            self._log("正在执行：计算机进入睡眠...")
            try:
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"])
            except Exception as e:
                self._log("睡眠失败: " + str(e))
        elif action == "shutdown":
            self._log("正在执行：关闭计算机（60秒后关机）...")
            try:
                subprocess.run(["shutdown", "/s", "/t", "60"])
            except Exception as e:
                self._log("关机失败: " + str(e))

    def _stop_all(self):
        self._session_id += 1
        self.processing = False
        self.stopped_by_user = True

        for worker in self.workers:
            try:
                if worker.generator:
                    worker.generator.kill_audio_proc()
                worker.cancel()
            except Exception:
                pass

        for task in self.tasks:
            if task.status in (STATUS_PENDING,) + self.WORKING_STATUS:
                task.set_status(STATUS_CANCELLED, "已停止")
                task.progress = 0
                self._update_task_ui(task)
        self._update_global_status()

        self.btn_start_all.config(state="normal")
        self.btn_stop_all.config(state="disabled")
        threading.Thread(target=self._release_gpu_and_log, daemon=True).start()
        self._log("已停止所有任务")

    # ============================================================
    # 环境检测 / 对话框
    # ============================================================

    def _check_initial_state(self):
        found, path, version = find_ffmpeg(self.config)
        if found:
            self._log("FFmpeg: 已就绪 | " + str(path))
        else:
            self._log("FFmpeg: 未找到 | 请在【设置】中指定路径")

        cuda_ok, cuda_msg = check_cuda_available()
        self._log("CUDA: " + ("可用 | " + cuda_msg if cuda_ok else "不可用 | " + cuda_msg))

        trans_path = resolve_translator_path(self.config)
        if trans_path:
            valid, msg = check_translator_path_valid(trans_path)
            self._log("翻译模型(NLLB-200): " + ("已就绪" if valid else "无效 | " + msg))
        else:
            self._log("翻译模型(NLLB-200): 未找到 | 请在【设置】中指定")

        for engine, key in (("Sakura-14B", "Sakura-14B"), ("Sakura-7B", "Sakura-7B"),
                            ("Sakura-GalTransl-7B", "GalTransl-7B")):
            p = resolve_sakura_path(self.config, engine=engine)
            self._log("翻译模型(" + key + "): " + ("已就绪" if p else "未找到 | 请在【设置】中指定 .gguf 文件"))

        kotoba_path = resolve_kotoba_path(self.config)
        self._log("语音模型(kotoba-whisper): " + ("已就绪" if kotoba_path else "未找到"))

        nemo_path = resolve_nemo_path(self.config)
        self._log("语音模型(ReazonSpeech-NeMo): " + ("已就绪" if nemo_path else "未找到"))

        self._ui(self._update_engine_hint)

    def _show_settings(self):
        parent = self._toplevel()
        dialog = SettingsDialog(parent, on_saved=self._on_settings_saved)
        try:
            parent.wait_window(dialog)
        except Exception:
            pass

    def _on_settings_saved(self, ok):
        if ok:
            self.config = load_config()
            self.show_toast("设置已保存", "success")
            self._log("设置已保存，已重新载入配置")
            threading.Thread(target=self._check_initial_state, daemon=True).start()
        else:
            self.show_toast("设置保存失败", "error")

    def _show_deps(self):
        DependencyDialog(self._toplevel())

    def _show_help(self):
        help_text = (
            "使用说明\n\n"
            "语音模型（三选一）:\n"
            "  faster-whisper   - " + ASR_ENGINES["faster-whisper"] + "\n"
            "      tiny 39MB 最快/精度一般/显存~1GB\n"
            "      base 74MB 快/精度较好/显存~1GB\n"
            "      small 244MB 中等/精度良好/显存~2GB\n"
            "      medium 769MB 较慢/精度很好/显存~5GB\n"
            "      large-v3 1.5GB 最慢/精度最佳/显存~10GB\n"
            "  kotoba-whisper   - " + ASR_ENGINES["kotoba-whisper"] + "\n"
            "  reazonspeech-nemo - " + ASR_ENGINES["reazonspeech-nemo"] + "\n\n"
            "翻译模型（四选一）:\n"
            "  NLLB-200   - " + TRANSLATOR_ENGINES["NLLB-200"] + "\n"
            "  Sakura-7B  - " + TRANSLATOR_ENGINES["Sakura-7B"] + "\n"
            "  Sakura-14B - " + TRANSLATOR_ENGINES["Sakura-14B"] + "\n"
            "  Sakura-GalTransl-7B - " + TRANSLATOR_ENGINES["Sakura-GalTransl-7B"] + "\n\n"
            "组合建议:\n  " + ENGINE_RECOMMENDATION + "\n\n"
            "任务列表:\n"
            "  1. 把视频文件 / 文件夹直接拖入列表（勾选“包含子文件夹”可递归多级子目录）\n"
            "     也可用【+ 添加视频】按钮选择文件\n"
            "  2. Ctrl / Shift 点击可多选；右键菜单可“只处理选中”或“移除选中”\n"
            "  3. 【清空列表】一键清空（处理中会自动先停止）\n\n"
            "处理选项:\n"
            "  设备: GPU（CUDA）或 CPU；CUDA 不可用时会自动切到 CPU；\n"
            "        单个视频在 GPU 上失败（显存不足/格式不支持等）会自动改用 CPU 重试。\n"
            "  双语: 勾选后字幕为“日文 + 中文”两行，否则只输出中文。\n"
            "  完成后: 无操作 / 计算机进入睡眠 / 关闭计算机（60 秒后）。\n\n"
            "路径设置:\n"
            "  模型文件不随程序拷贝，请在【设置】中指定“模型根目录”\n"
            "  （其下包含 语音模型 / 翻译模型），也可对每个模型单独指定路径。\n\n"
            "提示: 本模块的所有状态提示均为右下角浮出提示，不会弹出对话框打断无人值守处理。"
        )
        parent = self._toplevel()
        help_win = tk.Toplevel(parent)
        help_win.title("字幕生成 - 使用说明")
        text = scrolledtext.ScrolledText(help_win, wrap=tk.WORD, font=("Microsoft YaHei", 11))
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", help_text)
        text.config(state="disabled")
        fit_and_center(help_win, parent, 760, 640)

    def _show_about(self):
        self.show_toast(
            "日语视频转中文字幕 v" + str(_VERSION) + "\n"
            "语音: faster-whisper / kotoba-whisper / ReazonSpeech-NeMo\n"
            "翻译: NLLB-200 / Sakura-7B / Sakura-14B / Sakura-GalTransl-7B\n"
            "支持: 拖放递归添加、GPU/CPU 自动切换、双语字幕、完成后睡眠/关机",
            "info", 6000)

    # ============================================================
    # 清理
    # ============================================================

    def cleanup(self):
        """关闭窗口时释放资源（由主程序 _on_close 调用）"""
        try:
            self.processing = False
            for worker in self.workers:
                try:
                    worker.cancel()
                except Exception:
                    pass
            release_gpu_memory()
        except Exception:
            pass
