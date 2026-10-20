#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 文件移动选项卡 - 支持独立运行（日志输出到控制台/窗口）和嵌入主窗体两种模式
"""
文件移动工具

【一、文件夹整理】
  a. 把操作文件夹下的子文件夹里的文件（默认仅子级 1 层，可勾选“包含所有子文件夹”递归）
     上移到操作文件夹本身；
  b. 把操作文件夹里的视频文件移入与该视频同名的子文件夹中（abc.mp4 -> abc\\abc.mp4）。

【二、文件移动到目标文件夹（拖放 + 任务队列）】
  只需设定目标文件夹；从 Windows 资源管理器选中文件“或整个文件夹”后拖到本窗口的文件列表
  即可加入清单（也可用“添加文件”“添加文件夹”或 Ctrl+V 粘贴路径）。
  - 拖入文件夹时：勾选“拖入文件夹时整体加入”则把整个文件夹作为一条记录加入清单，
    移动时把整个文件夹剪切到目标目录；不勾选则仍按旧方式打散为其中的文件；
  - “创建移动任务”：把当前文件清单 + 目标文件夹记录为一个任务，可继续设置下一个任务；
  - “移动”：把当前清单作为任务并立即开始移动；
  - 任务清单中的任务按顺序逐个执行（A 完成再执行 B，再执行 C），避免同时移动占用过多
    系统 / 硬盘资源；执行中可“暂停”“继续”“停止”；
  - 停止时已完成移动的文件保留在目标文件夹，未移动与移动中的不再继续（支持断点续传）；
  - 全部任务完成后可设置本机“关机”或“进入睡眠”；完成后不再弹出任何对话框，结果只在
    日志中显示，以免阻塞自动关机 / 睡眠。

路径规范：无论本地或网络路径，界面与配置统一使用反斜杠“\\”分隔。

使用方式：
- 独立运行：python file_move_tab.py
- 嵌入主窗体：from file_move_tab import FileMoveTab; FileMoveTab(notebook, controller)
"""

import os
import shutil
import json
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Dict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.scrolledtext as scrolledtext

# 导入通用配置路径（独立运行时使用软件的通用配置文件）
try:
    from config import get_config_path
except ImportError:
    def get_config_path() -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "file_mover_config.json")


VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts',
                    '.rmvb', '.rm', '.m4v', '.mpg', '.mpeg', '.3gp', '.webm'}

# 字幕扩展名（勾选“处理字幕文件”时，同名校验会补上这些兄弟字幕）
SUBTITLE_EXTENSIONS = {'.srt', '.ass', '.ssa', '.sub', '.idx', '.smi', '.vtt'}

CONFIG_SECTION = "file_move_tab"

FINISH_NONE, FINISH_SHUTDOWN, FINISH_SLEEP = "none", "shutdown", "sleep"
FINISH_LABELS = {"none": "无操作", "shutdown": "关机", "sleep": "进入睡眠"}

# 移动时的最大并发线程数（同盘 rename 接近瞬时，主要加速跨盘复制；可按需调整）
CONCURRENT_MOVES = 4

# 任务状态持久化文件名（记录每个文件的完成状态，用于崩溃后断点续传）
TASK_STATE_FILE = "file_move_tasks.json"


def bs(path: str) -> str:
    """统一把路径分隔符规范为反斜杠（本地 / 局域网均适用）"""
    return (path or "").replace('/', '\\').strip()


def is_video(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS


def is_subtitle(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in SUBTITLE_EXTENSIONS


def unique_dst(dst: Path) -> Path:
    """目标已存在时自动加序号，避免覆盖"""
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while True:
        cand = dst.parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


class StandaloneController:
    """独立运行时的控制器 - 配置存于本地文件"""

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file if config_file else get_config_path()
        self.config = self._load_config()

    def _load_config(self) -> dict:
        default = {CONFIG_SECTION: {
            "directory": "", "mode": "flatten", "all_subfolders": False,
            "only_video": True, "proc_sub": False, "proc_other": False,
            "rename_folder": False,
            "dry_run": True,
            "folder_b": "", "finish_action": FINISH_NONE, "folder_whole": True,
            "folder_b_history": [],
        }}
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    default[CONFIG_SECTION].update(loaded.get(CONFIG_SECTION, {}))
        except Exception as e:
            print(f"加载配置失败: {e}")
        return default

    def save_config(self, config: dict) -> bool:
        try:
            self.config.update(config)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

    def get_config(self) -> dict:
        return self.config.copy()

    def log(self, message: str):
        print(message)


class FileMoveTab:
    """文件移动选项卡（文件夹整理 + A→B 选择性移动）"""

    def __init__(self, parent, controller, show_log_panel: bool = False):
        self.parent = parent
        self.controller = controller
        self.show_log_panel = show_log_panel

        self.tab_config = {}
        if hasattr(controller, 'get_config'):
            self.tab_config = (controller.get_config() or {}).get(CONFIG_SECTION, {})

        # 运行状态（两区块共用一个运行标志，避免并发冲突）
        self._running = False
        self._stop = False

        # 区块二：待移动文件清单（可从资源管理器拖入）
        self._b_rows: List[dict] = []           # 文件明细（含完整路径，供排序与移动）
        self._tree_sort = {"col": "name", "reverse": False}  # 列表当前排序状态
        self.dnd_enabled = False                # 是否支持鼠标拖放

        # 移动任务队列（按顺序逐个执行，避免同时移动占用过多系统/硬盘资源）
        self._tasks: List[dict] = []
        self._task_seq = 0
        self._task_thread: Optional[threading.Thread] = None
        self._pause = threading.Event()         # 置位=运行，清零=暂停
        self._cur_task: Optional[dict] = None   # 当前正在执行的任务

        # 目标文件夹历史（最多 6 条）：点“保存设置”或以该路径创建过移动任务才记录
        self._folder_b_history: List[str] = []

        self.frame = ttk.Frame(parent)
        self._build_ui(self.frame)
        self._load_config()
        self._restore_tasks()      # 启动恢复：载入上次未完成（含崩溃中断）的任务

    # ============================ 界面构建 ============================
    def _build_ui(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        vbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # 内层宽度跟随画布（窗口）宽度，使各区块能横向铺满主窗口
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮滚动
        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            return "break"
        canvas.bind("<MouseWheel>", _on_wheel)

        self._build_section_a(inner)
        self._build_section_b(inner)
        self._build_log(inner)

    def _build_section_a(self, parent):
        f = ttk.LabelFrame(parent, text="一、文件夹整理")
        f.pack(fill=tk.X, padx=10, pady=6)

        # 目录
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="操作文件夹:").pack(side=tk.LEFT, padx=(0, 5))
        self.dir_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row, text="浏览...", command=self._browse_a_dir).pack(side=tk.LEFT)

        # 模式
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(row, text="整理模式:").pack(side=tk.LEFT, padx=(0, 5))
        self.mode_var = tk.StringVar(value="flatten")
        ttk.Radiobutton(row, text="a. 子文件夹文件上移到当前目录", variable=self.mode_var,
                        value="flatten").pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(row, text="b. 视频移入同名子文件夹", variable=self.mode_var,
                        value="vsub").pack(side=tk.LEFT, padx=4)

        # 选项：子文件夹范围 + 以文件夹名改名
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(row, text="子文件夹范围:").pack(side=tk.LEFT, padx=(0, 4))
        self.sub_scope_var = tk.StringVar(value="sub")
        ttk.Radiobutton(row, text="次级子文件夹", variable=self.sub_scope_var,
                        value="sub").pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(row, text="递归所有级", variable=self.sub_scope_var,
                        value="all").pack(side=tk.LEFT, padx=4)
        self.rename_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="以文件夹名改名",
                        variable=self.rename_var).pack(side=tk.LEFT, padx=4)

        # 选项：要处理的文件类型
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=(0, 2))
        self.proc_video_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="处理视频文件",
                        variable=self.proc_video_var).pack(side=tk.LEFT, padx=4)
        self.proc_sub_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="处理字幕文件",
                        variable=self.proc_sub_var).pack(side=tk.LEFT, padx=4)
        self.proc_other_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="其它所有文件",
                        variable=self.proc_other_var).pack(side=tk.LEFT, padx=4)
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="仅演示（不实际移动）",
                        variable=self.dry_run_var).pack(side=tk.LEFT, padx=4)

        # 按钮 + 进度
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=4)
        self.a_start_btn = ttk.Button(row, text="开始整理", command=self._start_a, width=14)
        self.a_start_btn.pack(side=tk.LEFT, padx=4)
        self.a_stop_btn = ttk.Button(row, text="停止", command=lambda: self._request_stop(),
                                     width=10, state=tk.DISABLED)
        self.a_stop_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="保存设置", command=self._save_config, width=12).pack(side=tk.LEFT, padx=4)

        self.a_progress = ttk.Progressbar(f, orient=tk.HORIZONTAL, mode='determinate')
        self.a_progress.pack(fill=tk.X, padx=8, pady=(2, 0))
        self.a_status = ttk.Label(f, text="准备就绪")
        self.a_status.pack(anchor=tk.W, padx=8, pady=(0, 4))

    def _build_section_b(self, parent):
        f = ttk.LabelFrame(parent, text="二、文件移动到目标文件夹（支持拖放 / 任务队列）")
        f.pack(fill=tk.X, padx=10, pady=6)

        # 目标文件夹（右侧“▼”按钮弹出历史路径选择层；输入框保持可自由输入/粘贴）
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="目标文件夹:").pack(side=tk.LEFT, padx=(0, 5))
        self.b_dir_var = tk.StringVar()
        self.b_dir_entry = ttk.Entry(row, textvariable=self.b_dir_var)
        self.b_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.b_hist_btn = ttk.Button(row, text="▼", width=3,
                                     command=self._show_folder_history)
        self.b_hist_btn.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(row, text="浏览...", command=self._browse_b).pack(side=tk.LEFT, padx=2)

        # 文件列表（拖放目标，四列显示，点击表头排序）
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(row, text="待移动文件/文件夹（从资源管理器拖入，点击表头按该列排序）:").pack(side=tk.LEFT)
        ttk.Button(row, text="清空", command=self._b_clear_files, width=6).pack(side=tk.RIGHT, padx=2)
        ttk.Button(row, text="移除选中", command=self._b_remove_sel, width=8).pack(side=tk.RIGHT, padx=2)
        ttk.Button(row, text="添加文件夹", command=self._b_add_folder, width=10).pack(side=tk.RIGHT, padx=2)
        ttk.Button(row, text="添加文件", command=self._b_add_files, width=8).pack(side=tk.RIGHT, padx=2)

        # 拖入文件夹的处理方式
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=(0, 2))
        self.folder_whole_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="拖入文件夹时整体加入清单（移动时把整个文件夹剪切到目标目录）",
                        variable=self.folder_whole_var).pack(side=tk.LEFT, padx=4)

        list_frame = ttk.Frame(f)
        list_frame.pack(fill=tk.X, padx=8, pady=2)
        cols = ("name", "type", "size", "ctime")
        self.file_tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                                      selectmode=tk.EXTENDED, height=12)
        headers = {"name": "文件名", "type": "文件类型", "size": "大小", "ctime": "创建日期"}
        widths = {"name": 380, "type": 110, "size": 110, "ctime": 170}
        for c in cols:
            self.file_tree.heading(c, text=headers[c],
                                   command=lambda col=c: self._sort_tree(col))
            self.file_tree.column(c, width=widths[c], anchor=tk.W if c == "name" else tk.CENTER,
                                  stretch=(c == "name"))
        self.file_tree.tag_configure("dir", foreground="#0B5FA5")   # 文件夹用蓝色区分
        ybar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=ybar.set)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_tree.bind('<Control-v>', lambda e: self._paste_paths())
        self._enable_dnd()   # 启用从资源管理器拖入文件

        # 完成后操作
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(row, text="全部任务完成后:").pack(side=tk.LEFT, padx=(0, 5))
        self.finish_var = tk.StringVar(value=FINISH_LABELS[FINISH_NONE])
        cb = ttk.Combobox(row, textvariable=self.finish_var, width=12, state="readonly")
        cb['values'] = (FINISH_LABELS[FINISH_NONE], FINISH_LABELS[FINISH_SHUTDOWN],
                        FINISH_LABELS[FINISH_SLEEP])
        cb.current(0)
        cb.pack(side=tk.LEFT)
        dnd_tip = "已启用拖放" if self.dnd_enabled else "未启用拖放（可用“添加文件”或 Ctrl+V）"
        ttk.Label(row, text=f"（{dnd_tip}）", foreground="#666666").pack(side=tk.LEFT, padx=8)

        # 按钮
        row = ttk.Frame(f)
        row.pack(fill=tk.X, padx=8, pady=4)
        self.b_task_btn = ttk.Button(row, text="创建移动任务", command=self._create_task, width=14)
        self.b_task_btn.pack(side=tk.LEFT, padx=4)
        self.b_start_btn = ttk.Button(row, text="移动", command=self._start_b, width=10)
        self.b_start_btn.pack(side=tk.LEFT, padx=4)
        self.b_pause_btn = ttk.Button(row, text="暂停", command=self._pause_b, width=10, state=tk.DISABLED)
        self.b_pause_btn.pack(side=tk.LEFT, padx=4)
        self.b_resume_btn = ttk.Button(row, text="继续", command=self._resume_b, width=10, state=tk.DISABLED)
        self.b_resume_btn.pack(side=tk.LEFT, padx=4)
        self.b_stop_btn = ttk.Button(row, text="停止", command=lambda: self._request_stop(),
                                     width=10, state=tk.DISABLED)
        self.b_stop_btn.pack(side=tk.LEFT, padx=4)

        self.b_progress = ttk.Progressbar(f, orient=tk.HORIZONTAL, mode='determinate')
        self.b_progress.pack(fill=tk.X, padx=8, pady=(2, 0))
        self.b_status = ttk.Label(f, text="准备就绪")
        self.b_status.pack(anchor=tk.W, padx=8, pady=(0, 4))

        self._build_task_list(f)

    def _build_task_list(self, parent):
        tf = ttk.LabelFrame(parent, text="任务清单（按顺序逐个执行）")
        tf.pack(fill=tk.X, padx=8, pady=4)

        # 左右可拖动分隔：左侧任务列表，右侧按钮竖排（不横向挤占列表宽度）
        pane = ttk.Panedwindow(tf, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 左侧：任务列表（宽度可通过拖动分隔条调整）
        list_side = ttk.Frame(pane)
        pane.add(list_side, weight=4)
        tcols = ("idx", "count", "target", "status")
        self.task_tree = ttk.Treeview(list_side, columns=tcols, show="headings",
                                      selectmode=tk.EXTENDED, height=5)
        thead = {"idx": "任务", "count": "进度", "target": "目标文件夹", "status": "状态"}
        twidth = {"idx": 60, "count": 90, "target": 420, "status": 150}
        for c in tcols:
            self.task_tree.heading(c, text=thead[c])
            self.task_tree.column(c, width=twidth[c],
                                  anchor=tk.CENTER if c in ("idx", "count") else tk.W,
                                  stretch=(c == "target"))
        tbar = ttk.Scrollbar(list_side, orient=tk.VERTICAL, command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=tbar.set)
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=2)
        tbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 右侧：按钮上下排列
        btn_side = ttk.Frame(pane, padding=(6, 0))
        pane.add(btn_side, weight=0)
        self.b_run_sel_btn = ttk.Button(btn_side, text="执行选中", command=self._run_selected,
                                        width=10)
        self.b_run_sel_btn.pack(fill=tk.X, pady=(6, 2))
        self.b_run_all_btn = ttk.Button(btn_side, text="执行全部", command=self._run_all,
                                        width=10)
        self.b_run_all_btn.pack(fill=tk.X, pady=2)
        ttk.Button(btn_side, text="删除任务", command=self._delete_task,
                   width=10).pack(fill=tk.X, pady=2)
        ttk.Button(btn_side, text="清空任务", command=self._clear_tasks,
                   width=10).pack(fill=tk.X, pady=2)

    def _build_log(self, parent):
        f = ttk.LabelFrame(parent, text="操作日志")
        f.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self.log_text = scrolledtext.ScrolledText(f, wrap=tk.WORD, height=8, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ============================ 辅助方法 ============================
    def _ui(self, func, *args):
        """把 UI 更新投递到主线程，避免子线程直接操作控件"""
        if hasattr(self, 'frame'):
            self.frame.after(0, lambda: func(*args))

    def _log(self, msg: str):
        self._ui(self._append_log, msg)

    def _append_log(self, msg: str):
        try:
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
        except Exception:
            pass

    def _browse_a_dir(self):
        d = filedialog.askdirectory(title="选择操作文件夹")
        if d:
            self.dir_var.set(bs(d))

    def _browse_b(self):
        d = filedialog.askdirectory(title="选择目标文件夹")
        if d:
            self.b_dir_var.set(bs(d))

    # ---------- 目标文件夹历史（最多 6 条） ----------
    def _remember_folder_b(self, path: str):
        """记录目标文件夹到历史（去重置顶，最多 6 条）。
        仅在点“保存设置”或以该路径创建过移动任务时调用。"""
        path = bs(str(path or '')).strip()
        if not path:
            return
        hist = [p for p in self._folder_b_history if p.lower() != path.lower()]
        hist.insert(0, path)
        self._folder_b_history = hist[:6]

    def _show_folder_history(self):
        """点击“▼”按钮弹出历史路径下拉菜单（与“演员 ▼”同款样式），
        选择后即可切换；没有历史时不弹出"""
        hist = list(getattr(self, '_folder_b_history', []) or [])
        if not hist:
            return
        root = self.frame.winfo_toplevel()
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="历史目标文件夹（点击切换）：", state=tk.DISABLED)
        for path in hist:
            menu.add_command(label=path,
                             command=lambda p=path: self._pick_folder_history(p))

        btn = getattr(self, 'b_hist_btn', None)
        try:
            menu.update_idletasks()
            h = menu.winfo_reqheight()
        except Exception:
            h = 0
        try:
            if btn is not None and btn.winfo_exists():
                pos_x = btn.winfo_rootx()
                pos_y = btn.winfo_rooty() + btn.winfo_height()   # 默认弹在按钮下方
                if pos_y + h > root.winfo_screenheight():        # 下方放不下则弹在上方
                    pos_y = max(0, btn.winfo_rooty() - h)
            else:
                pos_x = self.frame.winfo_rootx() + 60
                pos_y = self.frame.winfo_rooty() + 100
        except Exception:
            pos_x, pos_y = 100, 100
        try:
            menu.tk_popup(pos_x, pos_y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _pick_folder_history(self, path: str):
        """选中历史路径：填入输入框、置顶历史并落盘"""
        self._remember_folder_b(path)
        self.b_dir_var.set(path)
        self._persist_config()
        self._log(f"[目标文件夹] 已切换为历史路径：{path}")

    # ============================ 区块一：文件夹整理 ============================
    def _start_a(self):
        root = self.dir_var.get().strip()
        if not root:
            messagebox.showwarning("警告", "请选择操作文件夹")
            return
        if not os.path.isdir(root):
            messagebox.showwarning("警告", f"无法访问文件夹：{root}")
            return
        if not self.dry_run_var.get():
            if not messagebox.askyesno("确认", "您选择了实际移动文件，确定继续吗？"):
                return

        self._begin_run()
        self.a_status.configure(text="正在准备...")
        self.a_progress['value'] = 0
        threading.Thread(target=self._run_a, args=(bs(root),), daemon=True).start()

    def _run_a(self, root: str):
        root = Path(root)
        dry = self.dry_run_var.get()
        mode = self.mode_var.get()
        proc_video = self.proc_video_var.get()
        proc_sub = self.proc_sub_var.get()
        proc_other = self.proc_other_var.get()
        moved = errors = 0

        # 收集待处理文件（源文件 -> 目标文件夹）
        if mode == "flatten":
            recursive = (self.sub_scope_var.get() == "all")
            srcs = self._collect_flatten(root, recursive)
            rename = self.rename_var.get()
            tasks = []
            for p in srcs:
                # 勾选“以文件夹名改名”时，移出的文件以其原所在文件夹名重命名（保留原扩展名）
                new_name = f"{p.parent.name}{p.suffix}" if rename else p.name
                tasks.append((p, root, new_name))
            self._log(f"\n=== 模式a：子文件夹文件上移到 {bs(str(root))} ===")
            self._log(f"子文件夹范围: {'递归所有级' if recursive else '仅次级'}"
                      f"  处理视频: {proc_video}  处理字幕: {proc_sub}"
                      f"  其它所有: {proc_other}  以文件夹名改名: {rename}"
                      f"  {'[演示]' if dry else '[执行]'}")
        else:
            tasks = [(p, root / p.stem, p.name) for p in root.iterdir()
                     if p.is_file() and self._accept_file(p.name)]
            self._log(f"\n=== 模式b：视频移入同名子文件夹 ===")
            self._log(f"目录: {bs(str(root))}  处理视频: {proc_video}  处理字幕: {proc_sub}"
                      f"  其它所有: {proc_other}  {'[演示]' if dry else '[执行]'}")

        total = len(tasks)
        self._log(f"待处理文件: {total}")
        if total == 0:
            self._ui(self._a_finish, 0, 0)
            return

        for i, (src, dst_dir, new_name) in enumerate(tasks, 1):
            if self._stop:
                self._log("操作被停止")
                break
            try:
                if mode == "vsub" and not dst_dir.exists():
                    if not dry:
                        dst_dir.mkdir(parents=True, exist_ok=True)
                target = unique_dst(dst_dir / new_name)
                self._log(f"{'[演示] ' if dry else '[移动] '}{bs(str(src))} -> {bs(str(target))}")
                if not dry:
                    shutil.move(str(src), str(target))
                moved += 1
            except Exception as e:
                self._log(f"移动失败 {src.name}: {e}")
                errors += 1
            self._ui(self._a_progress, int(i / total * 100), f"[{i}/{total}] {src.name}")

        self._ui(self._a_finish, moved, errors)

    def _accept_file(self, name: str) -> bool:
        """区块一筛选：是否处理该文件。
        “处理视频文件”“处理字幕文件”“其它所有文件”三个勾选项
        分别独立控制视频、字幕、其余类型文件是否处理。"""
        if is_video(name):
            return self.proc_video_var.get()
        if is_subtitle(name):
            return self.proc_sub_var.get()
        # 视频与字幕以外的其它所有文件
        return self.proc_other_var.get()

    def _expand_with_subtitles(self, files) -> List[str]:
        """区块二（A→B 选择性移动）：若勾选“处理字幕文件”，为每个视频文件
        补上同目录、同名的字幕文件（若存在且未在清单中），使视频与字幕一起移动。
        文件夹项（整体移动）内部的字幕会随文件夹一起移动，无需单独配对。"""
        files = [bs(str(f)) for f in files]
        if not self.proc_sub_var.get():
            return files
        out: List[str] = []
        seen = set()
        for f in files:
            if f.lower() not in seen:
                seen.add(f.lower())
                out.append(f)
            if os.path.isfile(f) and is_video(os.path.basename(f)):
                base = os.path.splitext(f)[0]
                for ext in SUBTITLE_EXTENSIONS:
                    sib = base + ext
                    if os.path.isfile(sib) and sib.lower() not in seen:
                        seen.add(sib.lower())
                        out.append(sib)
        return out

    def _collect_flatten(self, root: Path, recursive: bool) -> List[Path]:
        """收集需上移的文件：
        - 默认仅“子级”文件夹（1 层）里的文件；
        - recursive=True 时遍历所有子文件夹（不含根目录本身的文件）。
        是否处理视频/字幕由 _accept_file 依据勾选项决定。
        """
        files: List[Path] = []
        if recursive:
            for p in root.rglob('*'):
                if p.is_file() and p.parent != root and self._accept_file(p.name):
                    files.append(p)
        else:
            try:
                subs = [d for d in root.iterdir() if d.is_dir()]
            except (PermissionError, OSError):
                return files
            for sub in subs:
                try:
                    for f in sub.iterdir():
                        if f.is_file() and self._accept_file(f.name):
                            files.append(f)
                except (PermissionError, OSError):
                    continue
        return files

    def _a_progress(self, pct: int, text: str):
        self.a_progress['value'] = pct
        self.a_status.configure(text=text)

    def _a_finish(self, moved: int, errors: int):
        self.a_status.configure(text=f"完成：已移动 {moved}，错误 {errors}")
        self._log(f"整理完成：移动 {moved} 个，错误 {errors} 个")
        self._end_run()

    # ============================ 区块二：A→B 选择性移动 ============================
    # ---------- 拖放 / 文件清单 ----------
    def _enable_dnd(self):
        """启用鼠标拖放（依赖 tkinterdnd2）；不可用时回退到“添加文件”/Ctrl+V"""
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            root = self.file_tree.winfo_toplevel()
            TkinterDnD._require(root)          # 为已有主窗口加载 tkdnd
            self.file_tree.drop_target_register(DND_FILES)
            self.file_tree.dnd_bind('<<Drop>>', self._on_drop)
            self.dnd_enabled = True
        except Exception as e:
            self.dnd_enabled = False
            self._log(f"[提示] 未启用鼠标拖放（{e}），可用“添加文件”按钮或 Ctrl+V 粘贴文件路径")

    def _on_drop(self, event):
        """处理从资源管理器拖入的文件"""
        try:
            paths = self.file_tree.tk.splitlist(event.data)
        except Exception:
            paths = str(event.data).split()
        n = self._add_paths([bs(str(p)) for p in paths])
        self._log(f"[拖入] 新增 {n} 项" if n else "[拖入] 没有有效的文件/文件夹被加入")
        return event.action

    def _paste_paths(self):
        """Ctrl+V 粘贴文件路径（拖放不可用时的备用方式）"""
        try:
            data = self.file_tree.clipboard_get()
        except Exception:
            return
        paths = [l.strip().strip('"').strip("'") for l in data.splitlines() if l.strip()]
        n = self._add_paths(paths)
        if n:
            self._log(f"[粘贴] 新增 {n} 项")

    def _b_add_files(self):
        files = filedialog.askopenfilenames(title="选择要移动的文件")
        if files:
            n = self._add_paths(list(files))
            if n:
                self._log(f"[添加] 新增 {n} 个文件")

    def _b_add_folder(self):
        """把整个文件夹加入清单（移动时整个文件夹剪切到目标目录）"""
        d = filedialog.askdirectory(title="选择要整体移入的文件夹")
        if d:
            n = self._add_paths([bs(d)])
            if n:
                self._log(f"[添加] 新增文件夹：{bs(d)}")

    def _add_paths(self, paths) -> int:
        """把路径加入清单（自动去重）
        拖入文件夹时：勾选“整体加入”则整条加入，否则展开为其中的文件。
        """
        added = 0
        for p in paths:
            p = bs(str(p or '')).strip()
            if not p:
                continue
            if os.path.isdir(p):
                if getattr(self, 'folder_whole_var', None) and self.folder_whole_var.get():
                    added += self._add_one(p)           # 整个文件夹作为一项
                else:                                   # 旧行为：展开其中的文件
                    try:
                        for sub in os.scandir(p):
                            if sub.is_file():
                                added += self._add_one(sub.path)
                    except (PermissionError, OSError):
                        continue
                continue
            added += self._add_one(p)
        if added:
            self._apply_sort()
        return added

    def _add_one(self, path: str) -> int:
        """加入单个文件或文件夹；返回是否新增（1/0）。文件夹大小后台计算"""
        path = bs(path)
        is_dir = os.path.isdir(path)
        if not is_dir and not os.path.isfile(path):
            return 0
        if any(r["path"].lower() == path.lower() for r in self._b_rows):
            return 0
        try:
            st = os.stat(path)
            size, ctime = st.st_size, st.st_ctime
        except OSError:
            size, ctime = 0, 0.0
        if is_dir:
            name = os.path.basename(path.rstrip('\\')) or path
            row = {"path": path, "name": name, "type": "文件夹",
                   "size_raw": -1, "ctime_raw": ctime, "is_dir": True}
        else:
            ext = os.path.splitext(path)[1].lstrip('.').upper()
            row = {"path": path, "name": os.path.basename(path), "type": ext or "文件",
                   "size_raw": size, "ctime_raw": ctime, "is_dir": False}
        self._b_rows.append(row)
        if is_dir:
            threading.Thread(target=self._calc_dir_size, args=(path,), daemon=True).start()
        return 1

    def _calc_dir_size(self, path: str):
        """后台统计文件夹总大小，避免拖入大文件夹时界面卡顿"""
        total = 0
        try:
            for cur, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(cur, f))
                    except OSError:
                        pass
        except OSError:
            pass
        for r in self._b_rows:
            if r["path"].lower() == path.lower():
                r["size_raw"] = total
                break
        self._ui(self._populate_tree)

    def _b_remove_sel(self):
        idx = {int(i) for i in self.file_tree.selection()}
        if not idx:
            messagebox.showwarning("警告", "请先在列表中选择要移除的文件")
            return
        self._b_rows = [r for i, r in enumerate(self._b_rows) if i not in idx]
        self._populate_tree()

    def _b_clear_files(self):
        self._b_rows = []
        self._populate_tree()

    def _sort_tree(self, col: str):
        """点击表头：同列切换升/降序，异列改为该列升序"""
        if self._tree_sort["col"] == col:
            self._tree_sort["reverse"] = not self._tree_sort["reverse"]
        else:
            self._tree_sort = {"col": col, "reverse": False}
        self._apply_sort()

    def _apply_sort(self):
        keys = {
            "name": lambda r: r["name"].lower(),
            "type": lambda r: r["type"].lower(),
            "size": lambda r: r["size_raw"],
            "ctime": lambda r: r["ctime_raw"],
        }
        col = self._tree_sort["col"]
        self._b_rows.sort(key=keys.get(col, keys["name"]),
                          reverse=self._tree_sort["reverse"])
        self._populate_tree()

    def _populate_tree(self):
        self.file_tree.delete(*self.file_tree.get_children())
        for i, r in enumerate(self._b_rows):
            self.file_tree.insert("", tk.END, iid=str(i), tags=("dir",) if r.get("is_dir") else (),
                                  values=(
                r["name"], r["type"], self._fmt_size(r["size_raw"]),
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ctime_raw"]))))
        # 表头加排序方向指示
        headers = {"name": "文件名", "type": "文件类型", "size": "大小", "ctime": "创建日期"}
        for c, text in headers.items():
            if c == self._tree_sort["col"]:
                text += " ▼" if self._tree_sort["reverse"] else " ▲"
            self.file_tree.heading(c, text=text)

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n is None or n < 0:          # 文件夹大小后台计算中
            return "统计中..."
        size = float(n)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{n} B"

    # ---------- 任务管理 ----------
    # 任务数据结构：{id, files:[源路径...], target, status, done_map:{源路径:True}}
    #   done_map 记录“已成功移动”的源项（文件或文件夹），用于断点续传与崩溃恢复
    def _task_total(self, task: dict) -> int:
        return len(task.get("files", []))

    def _task_done(self, task: dict) -> int:
        return len(task.get("done_map", {}))

    def _new_task(self, files, target: str) -> dict:
        self._task_seq += 1
        task = {"id": self._task_seq, "files": list(files), "target": bs(target),
                "status": "等待中", "done_map": {}}
        self._tasks.append(task)
        self._refresh_task_tree()
        self._save_tasks()
        return task

    def _create_task(self):
        """把当前文件清单 + 目标文件夹记录为一个任务，之后可继续设置下一个任务"""
        files = [r["path"] for r in self._b_rows]
        files = self._expand_with_subtitles(files)
        target = self.b_dir_var.get().strip()
        if not files:
            messagebox.showwarning("警告", "请先把要移动的文件/文件夹拖入文件列表（或用“添加文件”“添加文件夹”）")
            return
        if not target:
            messagebox.showwarning("警告", "请设置目标文件夹")
            return
        task = self._new_task(files, target)
        self._remember_folder_b(target)   # 以该路径创建过任务，记入历史
        self._persist_config()            # 立即落盘，软件关闭后历史仍在
        self._log(f"[任务] 已创建 #{task['id']}：{len(files)} 项 -> {bs(target)}")
        self.b_status.configure(text=f"已创建任务 #{task['id']}，可继续添加下一个任务")
        self._b_clear_files()   # 清空清单，便于继续设置下一个任务

    def _start_b(self):
        """立即移动：把当前清单作为一个任务并马上开始执行"""
        files = [r["path"] for r in self._b_rows]
        files = self._expand_with_subtitles(files)
        target = self.b_dir_var.get().strip()
        if not files:
            messagebox.showwarning("警告", "请先把要移动的文件/文件夹拖入文件列表（或用“添加文件”“添加文件夹”）")
            return
        if not target:
            messagebox.showwarning("警告", "请设置目标文件夹")
            return
        if not messagebox.askyesno("确认", f"确定将列表中 {len(files)} 项移动到：\n{target} ？"):
            return
        task = self._new_task(files, target)
        self._remember_folder_b(target)   # 以该路径创建过任务，记入历史
        self._persist_config()            # 立即落盘，软件关闭后历史仍在
        self._b_clear_files()
        self._run_queue([task["id"]])

    def _run_selected(self):
        ids = [int(i) for i in self.task_tree.selection()]
        if not ids:
            messagebox.showwarning("警告", "请先在任务清单中选择要执行的任务")
            return
        self._run_queue(ids)

    def _run_all(self):
        if not self._tasks:
            messagebox.showwarning("警告", "任务清单为空")
            return
        self._run_queue([t["id"] for t in self._tasks])

    def _delete_task(self):
        if self._running:
            messagebox.showwarning("警告", "任务执行中，无法删除")
            return
        ids = {int(i) for i in self.task_tree.selection()}
        if not ids:
            messagebox.showwarning("警告", "请先在任务清单中选择要删除的任务")
            return
        self._tasks = [t for t in self._tasks if t["id"] not in ids]
        self._refresh_task_tree()
        self._save_tasks()

    def _clear_tasks(self):
        if self._running:
            messagebox.showwarning("警告", "任务执行中，无法清空")
            return
        self._tasks = []
        self._refresh_task_tree()
        self._save_tasks()

    def _refresh_task_tree(self):
        self.task_tree.delete(*self.task_tree.get_children())
        for t in self._tasks:
            done, total = self._task_done(t), self._task_total(t)
            self.task_tree.insert("", tk.END, iid=str(t["id"]), values=(
                f"#{t['id']}", f"{done}/{total}", t["target"], t["status"]))

    # ---------- 任务状态持久化与崩溃恢复 ----------
    def _state_path(self) -> str:
        """任务状态文件：与主配置同目录（优先用控制器配置路径，否则本文件目录）"""
        base = os.path.dirname(os.path.abspath(__file__))
        try:
            cp = get_config_path() if 'get_config_path' in globals() else None
            if cp:
                base = os.path.dirname(os.path.abspath(cp))
        except Exception:
            pass
        return os.path.join(base, TASK_STATE_FILE)

    def _save_tasks(self):
        """把任务（含每个文件的完成状态）落盘，供崩溃后断点续传"""
        try:
            data = {"tasks": []}
            for t in self._tasks:
                data["tasks"].append({
                    "id": t["id"], "files": t["files"], "target": t["target"],
                    "status": t.get("status", "等待中"),
                    "done_map": list(t.get("done_map", {}).keys()),
                })
            with open(self._state_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            self._log(f"[警告] 任务状态保存失败（不影响本次移动）: {e}")

    def _restore_tasks(self):
        """启动恢复：载入上次未完成的任务（含崩溃中断），重建任务清单与进度。
        已完成的项在 done_map 中会被自动跳过，未完成的项保留源路径等待继续。"""
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            tasks = data.get("tasks", [])
            if not tasks:
                return
            restored = 0
            for t in tasks:
                files = [bs(p) for p in t.get("files", []) if str(p).strip()]
                if not files:
                    continue
                self._task_seq = max(self._task_seq, int(t.get("id", 0)))
                done_set = {bs(p) for p in t.get("done_map", [])}
                status = t.get("status", "等待中")
                # 崩溃时处于“进行中/已停止”的任务标记为可继续
                if status in ("进行中", "已停止"):
                    status = "可继续"
                task = {"id": t["id"], "files": files, "target": bs(t.get("target", "")),
                        "status": status,
                        "done_map": {p: True for p in done_set if p in set(files)}}
                self._tasks.append(task)
                restored += 1
            if restored:
                self._refresh_task_tree()
                self._log(f"[恢复] 已载入上次 {restored} 个任务（含未完成），"
                          f"可在任务清单中点击“执行选中/执行全部”继续移动")
        except Exception as e:
            self._log(f"[警告] 任务状态恢复失败: {e}")

    def _run_queue(self, task_ids):
        """按任务顺序逐个执行（A 完成再执行 B...），每个任务内多文件并发移动"""
        if self._running:
            messagebox.showwarning("警告", "已有任务正在执行，请先停止或等待完成")
            return
        self._begin_run()
        self._pause.set()          # 初始为运行状态
        self.b_status.configure(text="开始执行任务...")
        self.b_progress['value'] = 0
        self._task_thread = threading.Thread(
            target=self._worker, args=(list(task_ids),), daemon=True)
        self._task_thread.start()

    # ---------- 移动执行（多线程并发 + 安全移动 + 断点续传） ----------
    def _worker(self, task_ids):
        """按任务顺序逐个执行；每个任务内把待移动项拆分为多份，用线程池并发移动。
        每完成一项即写入 done_map 并持久化，因此暂停/停止或程序崩溃后再次打开，
        未完成的项会被自动跳过已完成的、继续移动剩余项。
        """
        tasks = [t for t in self._tasks if t["id"] in set(task_ids)]
        for task in tasks:
            if self._stop:
                break
            self._cur_task = task
            total = self._task_total(task)
            task["status"] = "进行中"
            self._ui(self._refresh_task_tree)
            self._log(f"\n=== 开始任务 #{task['id']}：{total} 项 -> {task['target']} ===")

            tgt = Path(task["target"])
            if not tgt.exists():
                try:
                    tgt.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    self._log(f"目标文件夹不可用: {e}")
                    task["status"] = "失败"
                    self._ui(self._refresh_task_tree)
                    continue

            lock = threading.Lock()
            sem = threading.Semaphore(CONCURRENT_MOVES)
            pending = [p for p in task["files"]
                       if p not in task.setdefault("done_map", {})]

            def _on_done(_fut):
                sem.release()

            with ThreadPoolExecutor(max_workers=CONCURRENT_MOVES) as ex:
                futures = []
                for src in pending:
                    if self._stop:
                        break
                    # 暂停：等待恢复后再提交新项（已提交的可继续跑完）
                    while not self._pause.is_set():
                        if self._stop:
                            break
                        self._pause.wait(timeout=0.2)
                    if self._stop:
                        break
                    sem.acquire()
                    fut = ex.submit(self._move_item, task, src, str(tgt), lock)
                    fut.add_done_callback(_on_done)
                    futures.append(fut)
                for fut in futures:
                    try:
                        fut.result()
                    except Exception:
                        pass

            done = self._task_done(task)
            if self._stop:
                task["status"] = f"已停止（{done}/{total}）"
                self._log(f"任务 #{task['id']} 已停止：已移动 {done}/{total}，"
                          f"未移动项保留在源路径，可稍后继续")
            else:
                task["status"] = "已完成" if done == total else "部分完成"
                self._log(f"任务 #{task['id']} 结束：已移动 {done}/{total} 项")
            self._save_tasks()

        self._cur_task = None
        self._ui(self._refresh_task_tree)
        self._ui(self._b_finish, not self._stop)

        # 全部执行完毕（非停止）且本批任务均已成功完成时，自动清空任务清单
        if not self._stop:
            run_set = set(task_ids)
            finished = [t for t in self._tasks
                        if t["id"] in run_set
                        and self._task_done(t) == self._task_total(t)]
            if finished:
                self._tasks = [t for t in self._tasks if t not in finished]
                self._save_tasks()   # 同步落盘，重启后不再恢复已清空的清单
                self._ui(self._refresh_task_tree)
                if not self._tasks:
                    self._log("[任务] 全部任务完成，已自动清空任务清单")
                else:
                    self._log(f"[任务] 已自动移除 {len(finished)} 个已完成任务，"
                              f"剩余 {len(self._tasks)} 个未完成/未执行")

    def _move_item(self, task: dict, src: str, tgt: str, lock: threading.Lock) -> bool:
        """移动单条源项（文件或文件夹），成功后写入 done_map 并持久化/更新进度。"""
        src = bs(src)
        if not os.path.exists(src):
            self._log(f"[跳过] 源不存在: {bs(src)}")
            with lock:
                task.setdefault("done_map", {})[src] = True   # 不存在也标记，避免反复重试
                self._persist_progress(task)
            return True
        name = os.path.basename(os.path.normpath(src.rstrip('/\\'))) or src
        try:
            if os.path.isdir(src):
                self._merge_move_folder(src, tgt)
                self._log(f"[移动文件夹] {bs(src)} -> 合并到 {bs(os.path.join(tgt, name))}")
            else:
                dest = self._safe_move_file(src, tgt)
                self._log(f"[移动] {bs(src)} -> {bs(dest)}")
        except Exception as e:
            self._log(f"移动失败 {name}: {e}")
            return False
        with lock:
            task.setdefault("done_map", {})[src] = True
            self._persist_progress(task)
        return True

    def _persist_progress(self, task: dict):
        """更新进度条/列表并把任务状态落盘（断点续传依据）"""
        done = self._task_done(task)
        total = self._task_total(task)
        self._ui(self._b_progress, int(done / total * 100),
                 f"任务#{task['id']} [{done}/{total}]")
        self._ui(self._refresh_task_tree)
        self._save_tasks()

    def _safe_move_file(self, src: str, dst_parent: str) -> str:
        """安全移动单个文件，杜绝中断导致的文件损坏：
        - 同盘：os.rename 原子操作，不会产生半截文件；
        - 跨盘：先复制为 .part，校验大小一致后原子替换目标，再删源；
          中途崩溃则残留 .part 会在下次重试时清理，绝不覆盖/损坏目标。
        目标重名时加数字后缀，不覆盖已有文件。
        """
        name = os.path.basename(os.path.normpath(src.rstrip('/\\'))) or src
        dst = os.path.join(dst_parent, name)
        if os.path.exists(dst):
            dst = str(unique_dst(Path(dst)))
        # 同盘：直接原子 rename（最快、最安全）
        try:
            st_src = os.stat(src)
            st_parent = os.stat(dst_parent)
            if st_src.st_dev == st_parent.st_dev:
                shutil.move(src, dst)
                return dst
        except OSError:
            pass
        # 跨盘：.part 拷贝 -> 校验 -> 原子替换 -> 删源
        part = dst + ".part"
        try:
            if os.path.exists(part):
                os.remove(part)
            shutil.copy2(src, part)
            if os.path.getsize(part) != os.path.getsize(src):
                raise IOError("拷贝大小不一致，可能已损坏")
            os.replace(part, dst)        # 目标所在盘内原子替换
            os.remove(src)
        except Exception:
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
            raise
        return dst

    def _merge_move_folder(self, src_dir: str, dst_parent: str):
        """把源文件夹整体移入 dst_parent：若目标已存在同名文件夹，则合并其内部文件
        （递归）；仅当同名文件/子项冲突时才给新项加数字后缀，绝不覆盖已有内容。
        文件移动走 _safe_move_file，跨盘中断也不会损坏。
        """
        name = os.path.basename(os.path.normpath(src_dir.rstrip('/\\'))) or src_dir
        dst_dir = os.path.join(dst_parent, name)
        if not os.path.isdir(dst_dir):
            # 目标无同名文件夹：创建后逐文件安全移入（跨盘也安全，便于断点续传）
            if os.path.exists(dst_dir):
                dst_dir = str(unique_dst(Path(dst_dir)))
            try:
                os.makedirs(dst_dir, exist_ok=True)
            except OSError as e:
                self._log(f"创建目标文件夹失败 {name}: {e}")
                return
        # 逐项合并（目标已存在同名文件夹则合并内部文件；否则直接移入）
        try:
            for entry in os.scandir(src_dir):
                s = entry.path
                if entry.is_dir():
                    self._merge_move_folder(s, dst_dir)     # 递归合并子文件夹
                else:
                    if entry.name.endswith('.part'):        # 清理上次中断残留的临时文件
                        try:
                            os.remove(s)
                        except OSError:
                            pass
                        continue
                    try:
                        self._safe_move_file(s, dst_dir)
                    except Exception as e:
                        self._log(f"移动失败 {entry.name}: {e}")
        except OSError as e:
            self._log(f"合并文件夹出错 {name}: {e}")
        finally:
            # 源文件夹已搬空则删除，避免残留空目录
            try:
                if os.path.isdir(src_dir) and not os.listdir(src_dir):
                    os.rmdir(src_dir)
            except OSError:
                pass

    def _b_progress(self, pct: int, text: str):
        self.b_progress['value'] = pct
        self.b_status.configure(text=text)

    def _b_finish(self, success: bool):
        if success:
            self.b_status.configure(text="全部任务完成")
            self._log("[完成] 全部任务已完成")
        else:
            self.b_status.configure(text="已停止（已移动的文件保留在目标文件夹）")
            self._log("[停止] 任务已停止，已移动的文件保留在目标文件夹")
        self._end_run()
        if success:
            action = {v: k for k, v in FINISH_LABELS.items()}.get(
                self.finish_var.get(), FINISH_NONE)
            if action != FINISH_NONE:
                self._do_finish(action)

    def _pause_b(self):
        if self._running and self._task_thread and self._task_thread.is_alive():
            self._pause.clear()
            self.b_status.configure(text="已暂停（断点续传）")
            self.b_pause_btn.configure(state=tk.DISABLED)
            self.b_resume_btn.configure(state=tk.NORMAL)

    def _resume_b(self):
        if self._running:
            self._pause.set()
            self.b_status.configure(text="继续移动...")
            self.b_pause_btn.configure(state=tk.NORMAL)
            self.b_resume_btn.configure(state=tk.DISABLED)

    def _do_finish(self, action: str):
        """完成后操作：不弹窗（避免阻塞自动关机/睡眠），结果仅写入日志和状态栏"""
        label = FINISH_LABELS.get(action, action)
        self._log(f"[完成后操作] 移动已完成，即将{label}")
        self.b_status.configure(text=f"移动完成，即将{label}")
        try:
            if action == FINISH_SHUTDOWN:
                os.system("shutdown /s /f /t 30")
            elif action == FINISH_SLEEP:
                from system_power import enter_sleep
                if not enter_sleep():
                    self._log("[完成后操作] 发起睡眠失败（可能被系统/驱动阻止）。"
                             "可检查：①快速启动是否开启；②网卡/鼠标/键盘的“允许此设备唤醒"
                             "计算机”；③计划任务唤醒定时器（powercfg /waketimers，需管理员）")
        except Exception as e:
            self._log(f"[完成后操作] 执行{label}失败: {e}")

    # ============================ 运行控制 ============================
    def _begin_run(self):
        self._running = True
        self._stop = False
        self.a_start_btn.configure(state=tk.DISABLED)
        self.a_stop_btn.configure(state=tk.NORMAL)
        self.b_start_btn.configure(state=tk.DISABLED)
        self.b_task_btn.configure(state=tk.DISABLED)
        self.b_run_sel_btn.configure(state=tk.DISABLED)
        self.b_run_all_btn.configure(state=tk.DISABLED)
        self.b_stop_btn.configure(state=tk.NORMAL)
        self.b_pause_btn.configure(state=tk.NORMAL)
        self.b_resume_btn.configure(state=tk.DISABLED)

    def _end_run(self):
        self._running = False
        self._stop = False
        self.a_start_btn.configure(state=tk.NORMAL)
        self.a_stop_btn.configure(state=tk.DISABLED)
        self.b_start_btn.configure(state=tk.NORMAL)
        self.b_task_btn.configure(state=tk.NORMAL)
        self.b_run_sel_btn.configure(state=tk.NORMAL)
        self.b_run_all_btn.configure(state=tk.NORMAL)
        self.b_stop_btn.configure(state=tk.DISABLED)
        self.b_pause_btn.configure(state=tk.DISABLED)
        self.b_resume_btn.configure(state=tk.DISABLED)

    def _request_stop(self):
        if self._running:
            self._stop = True
            self._pause.set()      # 解除暂停等待，让线程尽快退出
            self._log("正在停止（已移动的文件保留在目标文件夹）...")

    # ============================ 配置 ============================
    def _load_config(self):
        cfg = self.tab_config
        self.dir_var.set(bs(cfg.get("directory", "")))
        self.mode_var.set(cfg.get("mode", "flatten"))
        # 子文件夹范围：旧键 all_subfolders(True=递归) 兼容；新键用 sub_scope
        self.sub_scope_var.set("all" if cfg.get("all_subfolders", False) else "sub")
        self.rename_var.set(cfg.get("rename_folder", False))
        # 兼容旧配置键 only_video；新键为 proc_video / proc_sub
        self.proc_video_var.set(cfg.get("proc_video", cfg.get("only_video", True)))
        self.proc_sub_var.set(cfg.get("proc_sub", False))
        self.proc_other_var.set(cfg.get("proc_other", False))
        self.dry_run_var.set(cfg.get("dry_run", True))
        self.b_dir_var.set(bs(cfg.get("folder_b", "")))
        self.folder_whole_var.set(cfg.get("folder_whole", True))
        self.finish_var.set(FINISH_LABELS.get(cfg.get("finish_action", FINISH_NONE),
                                              FINISH_LABELS[FINISH_NONE]))
        try:
            hist = cfg.get("folder_b_history", []) or []
            self._folder_b_history = [bs(str(p)) for p in hist if str(p).strip()][:6]
        except Exception:
            self._folder_b_history = []

    def _persist_config(self):
        """把当前配置（含目标文件夹历史）写入磁盘，供下次启动恢复"""
        config = {CONFIG_SECTION: {
            "directory": bs(self.dir_var.get()),
            "mode": self.mode_var.get(),
            "all_subfolders": (self.sub_scope_var.get() == "all"),
            "rename_folder": self.rename_var.get(),
            "proc_video": self.proc_video_var.get(),
            "proc_sub": self.proc_sub_var.get(),
            "proc_other": self.proc_other_var.get(),
            "dry_run": self.dry_run_var.get(),
            "folder_b": bs(self.b_dir_var.get()),
            "folder_whole": self.folder_whole_var.get(),
            "folder_b_history": list(self._folder_b_history),
            "finish_action": {v: k for k, v in FINISH_LABELS.items()}.get(
                self.finish_var.get(), FINISH_NONE),
        }}
        if hasattr(self.controller, 'save_config'):
            if self.controller.save_config(config):
                return True
            self._log("[配置] 保存设置失败")
            return False
        self._log("[配置] 当前控制器不支持保存配置")
        return False

    def _save_config(self):
        # 点“保存设置”时把当前目标文件夹记入历史（非空才记录）并落盘
        self._remember_folder_b(self.b_dir_var.get())
        if self._persist_config():
            self._log("[配置] 设置已保存")


# ============================ 独立运行入口 ============================
def main():
    root = tk.Tk()
    root.title("文件移动工具")
    root.geometry("760x720")
    root.minsize(620, 560)

    controller = StandaloneController()
    tab = FileMoveTab(root, controller, show_log_panel=False)
    tab.frame.pack(fill=tk.BOTH, expand=True)

    status = ttk.Label(root, text="就绪 - 日志输出到下方文本框", relief=tk.SUNKEN, anchor=tk.W)
    status.pack(side=tk.BOTTOM, fill=tk.X)

    def on_closing():
        if tab._running:
            if messagebox.askokcancel("确认", "操作正在进行中，确定要退出吗？"):
                tab._request_stop()
                root.after(400, root.destroy)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
