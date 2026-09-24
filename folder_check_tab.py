#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 文件夹检查 / 文件比对 合并模块
#
# 本模块将两个功能相似的模块（原 video_check_tab.py 与 file_compare_tab.py）合并为统一的
# 标签页，提供两种工作模式：
#   1. 单文件夹检查（mode="check"）：遍历一个文件夹（可选递归），检查文件夹名与内部
#      视频文件名中的番号/编号是否匹配，并检测缺失的视频 / 封面 / 字幕文件。
#   2. 双文件夹比对（mode="compare"）：对比"原视频文件夹(A)"与"已转码文件夹(B)"，
#      检查转码完成情况（完全匹配 / 近似匹配 / 未匹配）。
#
# 两种模式涉及番号或编号识别时，统一使用 pattern_matcher 模块进行匹配识别。
#
# 双模式运行：
#   1. 作为模块被主程序调用：from folder_check_tab import FolderCheckTab
#      （调用方负责将 tab.frame 添加到 Notebook）
#   2. 独立运行：直接执行本文件，会创建一个简单的测试窗口

import os
import json
import re
import csv
import threading
import queue
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime

# ============ 番号匹配器导入 ============
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    PatternMatcher = None
    print("[FolderCheckTab] 警告: 无法导入 pattern_matcher，番号匹配功能将不可用")

# ============ 配置管理器导入（与 file_compare_tab 保持兼容的单例） ============
try:
    from file_organize_tab import ConfigManager
except ImportError:
    class ConfigManager:
        """统一配置管理器 - 单例模式（兼容版，独立运行时使用）"""
        _instance = None
        _lock = threading.Lock()

        def __new__(cls, config_path: Optional[str] = None):
            if cls._instance is None:
                with cls._lock:
                    if cls._instance is None:
                        cls._instance = super().__new__(cls)
                        cls._instance._initialized = False
            return cls._instance

        def __init__(self, config_path: Optional[str] = None):
            if self._initialized:
                return
            self._initialized = True
            self.config_path = config_path or self._get_default_config_path()
            self._config: Dict = {}
            self._load_config()

        def _get_default_config_path(self) -> str:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_config.json")

        def _load_config(self):
            try:
                if os.path.exists(self.config_path):
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        self._config = json.load(f)
                else:
                    self._config = {}
            except Exception as e:
                print(f"加载配置失败: {e}")
                self._config = {}

        def save_config(self, show_error: bool = False) -> bool:
            try:
                config_dir = os.path.dirname(self.config_path)
                if config_dir and not os.path.exists(config_dir):
                    os.makedirs(config_dir)
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(self._config, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                if show_error:
                    messagebox.showerror("错误", f"保存配置失败: {e}")
                return False

        def get_section(self, section: str, default: Optional[Dict] = None) -> Dict:
            return self._config.get(section, default or {})

        def set_section(self, section: str, data: Dict, auto_save: bool = True):
            self._config[section] = data
            if auto_save:
                self.save_config()


class FolderCheckTab:
    """文件夹检查 / 文件比对 合并标签页

    提供两种模式：
      - "check"    : 单文件夹检查
      - "compare"  : 双文件夹比对（转码前后）
    """

    CONFIG_SECTION = "folder_check"

    # ============ 文件类型定义（取两个原模块的并集，保证兼容） ============
    VIDEO_EXTENSIONS: Set[str] = {
        '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.m4v',
        '.mpeg', '.mpg', '.ts', '.rmvb', '.rm', '.m2ts'
    }
    IMAGE_EXTENSIONS: Set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif'}
    SUBTITLE_EXTENSIONS: Set[str] = {'.srt'}

    # 需要跳过的文件夹名标记
    SKIP_FOLDER_MARKERS = ['(个人)', '(不好看)']

    # 自带字幕的视频文件名后缀标记（不区分大小写检查）
    BUILTIN_SUBTITLE_SUFFIXES = ['-C', '-c', '-UNC', 'UC']

    def __init__(self, parent_notebook, controller=None):
        """
        初始化标签页

        Args:
            parent_notebook: 父级 Notebook 容器
            controller: 主控制器对象（FileMoverGUI 实例），可为 None（独立运行时）
        """
        self.parent = parent_notebook
        self.controller = controller

        # 初始化番号匹配器
        if PatternMatcher is not None:
            self.matcher = PatternMatcher()
        else:
            self.matcher = None

        # 获取配置管理器
        if controller and hasattr(controller, 'config_manager'):
            self.config_manager = controller.config_manager
        else:
            self.config_manager = ConfigManager()

        # 当前模式
        self.mode_var = tk.StringVar(value="check")

        # 创建主框架
        self.frame = ttk.Frame(parent_notebook)

        # ---------- 单文件夹检查模式的数据 ----------
        self.check_results: List[Dict] = []
        self.sort_reverse = False
        self.sort_column = None

        # ---------- 双文件夹比对模式的数据 ----------
        self.compare_results: List[Dict] = []
        self.matched_files: List[Dict] = []
        self.unmatched_files: List[Path] = []
        self.source_folder: Optional[Path] = None
        self.target_folder: Optional[Path] = None
        self.is_running = False
        self.operation_thread = None
        self.progress_queue = queue.Queue()

        self._init_ui()
        self._load_config()

        # 启动进度检查（供比对模式的后台线程使用）
        self._check_progress()

    # =====================================================================
    # 通用工具
    # =====================================================================
    def get_frame(self):
        """获取选项卡框架（供调用方添加到 Notebook）"""
        return self.frame

    def log(self, message: str):
        """通过 controller 记录日志到日志选项卡，无 controller 时打印"""
        if self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

    # =====================================================================
    # UI 初始化
    # =====================================================================
    def _init_ui(self):
        """初始化用户界面"""
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(5, weight=1)  # 结果区域可扩展

        # ========== 0. 模式选择区域 ==========
        mode_frame = ttk.LabelFrame(self.frame, text="模式选择", padding=10)
        mode_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))

        ttk.Radiobutton(
            mode_frame, text="单文件夹检查（视频命名/缺失检查）",
            variable=self.mode_var, value="check", command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=(0, 30))
        ttk.Radiobutton(
            mode_frame, text="双文件夹比对（转码前后对比）",
            variable=self.mode_var, value="compare", command=self._on_mode_change
        ).pack(side=tk.LEFT)

        # ========== 1. 单文件夹检查 - 配置区域 ==========
        self.check_config_frame = ttk.LabelFrame(self.frame, text="单文件夹检查配置", padding=10)
        self._init_check_config_ui(self.check_config_frame)
        self.check_config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        # ========== 2. 双文件夹比对 - 配置区域（初始隐藏） ==========
        self.compare_config_frame = ttk.LabelFrame(self.frame, text="双文件夹比对配置", padding=10)
        self._init_compare_config_ui(self.compare_config_frame)
        self.compare_config_frame.grid_remove()

        # ========== 3. 操作按钮区域 ==========
        btn_frame = ttk.Frame(self.frame)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)

        self.save_btn = ttk.Button(btn_frame, text="💾 保存配置", command=self._save_config)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.run_btn = ttk.Button(btn_frame, text="▶ 开始执行", command=self._run)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = ttk.Button(btn_frame, text="⏹ 停止", command=self._stop_compare,
                                   width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.export_btn = ttk.Button(btn_frame, text="📄 导出结果", command=self._export_results,
                                     state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=(0, 10))

        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(btn_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        # ========== 4. 统计信息区域 ==========
        stats_frame = ttk.LabelFrame(self.frame, text="统计信息", padding=5)
        stats_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        self.stats_label = ttk.Label(stats_frame, text="就绪")
        self.stats_label.pack(side=tk.LEFT)

        # ========== 5. 结果区域（内部 Notebook 区分两种模式的展示） ==========
        self.result_notebook = ttk.Notebook(self.frame)
        self.result_notebook.grid(row=5, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.result_notebook.rowconfigure(0, weight=1)
        self.result_notebook.columnconfigure(0, weight=1)

        # --- 模式一：检查结果 ---
        self.check_result_frame = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.check_result_frame, text="检查结果")
        self._init_check_result_ui(self.check_result_frame)

        # --- 模式二：比对结果（内部再分两个子页） ---
        self.compare_result_frame = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.compare_result_frame, text="比对结果")
        self._init_compare_result_ui(self.compare_result_frame)

        self.check_result_tab = self.result_notebook.tabs()[0]
        self.compare_result_tab = self.result_notebook.tabs()[1]

    # ---------------------------------------------------------------------
    # 单文件夹检查 - 配置 UI
    # ---------------------------------------------------------------------
    def _init_check_config_ui(self, parent: ttk.LabelFrame):
        parent.columnconfigure(1, weight=1)

        # 功能说明
        desc_frame = ttk.Frame(parent)
        desc_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        desc_frame.columnconfigure(0, weight=1)
        description = (
            "遍历指定文件夹（可选包含子文件夹），检查文件夹名称与内部视频文件名中的番号/编号"
            "是否匹配，\n检测文件夹中是否缺失视频文件、封面图片或字幕文件。"
            "可设置排除文件夹，跳过不需要检查的目录。"
        )
        self.desc_text = tk.Text(desc_frame, height=3, wrap=tk.WORD, bg='#f0f0f0', relief=tk.FLAT)
        self.desc_text.grid(row=0, column=0, sticky="ew")
        self.desc_text.insert('1.0', description)
        self.desc_text.config(state=tk.DISABLED)

        # 基础文件夹
        ttk.Label(parent, text="基础文件夹:").grid(row=1, column=0, sticky="w", padx=(0, 5))
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(parent, textvariable=self.folder_var)
        self.folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5))
        ttk.Button(parent, text="浏览...", command=self._browse_folder).grid(row=1, column=2, sticky="e")

        tip_label = ttk.Label(parent, text="支持本地路径 (C:\\...) 或网络共享 (\\server\\share)",
                              foreground="gray", font=('Microsoft YaHei', 8))
        tip_label.grid(row=2, column=1, sticky="w", pady=(2, 0))

        # 选项
        options_frame = ttk.Frame(parent)
        options_frame.grid(row=3, column=1, sticky="w", pady=(5, 0))
        self.include_subfolders_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="包含子文件夹 (递归遍历所有子目录)",
                        variable=self.include_subfolders_var).pack(side=tk.LEFT, padx=(0, 20))
        self.check_subtitle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="检查是否有字幕文件",
                        variable=self.check_subtitle_var).pack(side=tk.LEFT)

        # 排除文件夹
        exclude_frame = ttk.LabelFrame(parent, text="排除文件夹 (这些文件夹及其子目录将被跳过)", padding=5)
        exclude_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        exclude_frame.columnconfigure(0, weight=1)

        self.excluded_listbox = tk.Listbox(exclude_frame, height=4, selectmode=tk.EXTENDED)
        self.excluded_listbox.grid(row=0, column=0, sticky="ew", rowspan=3)
        exclude_sb = ttk.Scrollbar(exclude_frame, orient="vertical", command=self.excluded_listbox.yview)
        self.excluded_listbox.config(yscrollcommand=exclude_sb.set)
        exclude_sb.grid(row=0, column=1, sticky="ns", rowspan=3)

        exclude_btn_frame = ttk.Frame(exclude_frame)
        exclude_btn_frame.grid(row=0, column=2, sticky="ns", padx=(5, 0))
        ttk.Button(exclude_btn_frame, text="➕ 添加", command=self._add_excluded_folder, width=12).pack(side=tk.TOP, pady=(0, 2))
        ttk.Button(exclude_btn_frame, text="➖ 移除", command=self._remove_excluded_folder, width=12).pack(side=tk.TOP, pady=2)
        ttk.Button(exclude_btn_frame, text="🗑️ 清空", command=self._clear_all_excluded, width=12).pack(side=tk.TOP, pady=2)

        ttk.Label(exclude_frame, text="提示：设置新的基础文件夹时，排除列表将自动清空",
                  foreground="gray", font=('Microsoft YaHei', 8)).grid(row=3, column=0, sticky="w", pady=(2, 0))

    # ---------------------------------------------------------------------
    # 单文件夹检查 - 结果 UI
    # ---------------------------------------------------------------------
    def _init_check_result_ui(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        columns = ("folder_path", "file_name", "folder_code", "file_code", "issue_type", "detail")
        self.check_tree = ttk.Treeview(parent, columns=columns, show='headings', selectmode='browse')

        column_config = {
            "folder_path": ("文件夹路径", 300),
            "file_name": ("文件名", 200),
            "folder_code": ("文件夹番号", 100),
            "file_code": ("文件番号", 100),
            "issue_type": ("问题类型", 100),
            "detail": ("详细说明", 250)
        }
        for col, (text, width) in column_config.items():
            self.check_tree.heading(col, text=text, command=lambda c=col: self._sort_tree(c))
            self.check_tree.column(col, width=width, minwidth=50, stretch=False, anchor='w')

        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.check_tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=self.check_tree.xview)
        self.check_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.check_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.context_menu = tk.Menu(self.check_tree, tearoff=0)
        self.context_menu.add_command(label="复制路径", command=self._copy_path)
        self.context_menu.add_command(label="复制文件名", command=self._copy_filename)
        self.context_menu.add_command(label="复制文件夹番号", command=self._copy_folder_code)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="在资源管理器中打开", command=self._open_in_explorer)
        self.check_tree.bind("<Button-3>", self._show_context_menu)

        # 标签颜色
        self.check_tree.tag_configure('mismatch', background='#ffcccc')
        self.check_tree.tag_configure('warning', background='#ffffcc')
        self.check_tree.tag_configure('missing', background='#ccddff')
        self.check_tree.tag_configure('error', background='#ffaaaa')

    # ---------------------------------------------------------------------
    # 双文件夹比对 - 配置 UI
    # ---------------------------------------------------------------------
    def _init_compare_config_ui(self, parent: ttk.LabelFrame):
        parent.columnconfigure(1, weight=1)

        info_text = (
            "本功能用于对比原视频文件夹(A)和已转码文件夹(B)，检查转码完成情况。\n"
            "• 完全匹配：A、B 中存在同名文件（含不同格式），标记为已完成\n"
            "• 近似匹配：文件名前缀相同，后跟 ~1、-1 等数字后缀，标记为已完成但提示不一致\n"
            "• 未匹配：A 中文件在 B 中找不到对应文件，标记为待转码"
        )
        ttk.Label(parent, text=info_text, justify=tk.LEFT, wraplength=1100).grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        # 原视频文件夹（A）
        ttk.Label(parent, text="原视频文件夹(A):", width=15).grid(row=1, column=0, sticky="w", padx=5)
        self.source_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.source_var).grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(parent, text="浏览...", command=self._browse_source_folder).grid(row=1, column=2, sticky="e")

        # 已转码文件夹（B）
        ttk.Label(parent, text="已转码文件夹(B):", width=15).grid(row=2, column=0, sticky="w", padx=5)
        self.target_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.target_var).grid(row=2, column=1, sticky="ew", padx=5)
        ttk.Button(parent, text="浏览...", command=self._browse_target_folder).grid(row=2, column=2, sticky="e")

        # 一键选择按钮
        sel_frame = ttk.Frame(parent)
        sel_frame.grid(row=3, column=0, columnspan=3, sticky="w", padx=5, pady=5)
        ttk.Button(sel_frame, text="一键选择已转码文件", command=self._select_matched_files, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(sel_frame, text="一键选择未转码文件", command=self._select_unmatched_files, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(sel_frame, text="清空对比结果", command=self._clear_compare_results, width=15).pack(side=tk.LEFT, padx=5)

    # ---------------------------------------------------------------------
    # 双文件夹比对 - 结果 UI
    # ---------------------------------------------------------------------
    def _init_compare_result_ui(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        inner = ttk.Notebook(parent)
        inner.grid(row=0, column=0, sticky="nsew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        # Tab 1: 匹配结果
        matched_frame = ttk.Frame(inner)
        inner.add(matched_frame, text="匹配结果")
        matched_cols = ('source_file', 'target_file', 'status', 'note')
        self.matched_tree = ttk.Treeview(matched_frame, columns=matched_cols, show='headings')
        self.matched_tree.heading('source_file', text='原视频文件名(A)')
        self.matched_tree.heading('target_file', text='已转码文件名(B)')
        self.matched_tree.heading('status', text='状态')
        self.matched_tree.heading('note', text='备注')
        self.matched_tree.column('source_file', width=350)
        self.matched_tree.column('target_file', width=350)
        self.matched_tree.column('status', width=100, anchor=tk.CENTER)
        self.matched_tree.column('note', width=200)
        matched_scroll = ttk.Scrollbar(matched_frame, orient=tk.VERTICAL, command=self.matched_tree.yview)
        self.matched_tree.configure(yscrollcommand=matched_scroll.set)
        matched_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.matched_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.matched_tree.tag_configure('perfect', foreground='green')
        self.matched_tree.tag_configure('similar', foreground='orange')

        # Tab 2: 未匹配文件
        unmatched_frame = ttk.Frame(inner)
        inner.add(unmatched_frame, text="未转码文件")
        unmatched_cols = ('filename', 'filepath')
        self.unmatched_tree = ttk.Treeview(unmatched_frame, columns=unmatched_cols, show='headings')
        self.unmatched_tree.heading('filename', text='文件名')
        self.unmatched_tree.heading('filepath', text='完整路径')
        self.unmatched_tree.column('filename', width=400)
        self.unmatched_tree.column('filepath', width=600)
        unmatched_scroll = ttk.Scrollbar(unmatched_frame, orient=tk.VERTICAL, command=self.unmatched_tree.yview)
        self.unmatched_tree.configure(yscrollcommand=unmatched_scroll.set)
        unmatched_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.unmatched_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.unmatched_tree.tag_configure('unmatched', foreground='red')

        self.progress_label = ttk.Label(parent, text="准备就绪")
        self.progress_label.grid(row=1, column=0, sticky="w", padx=5, pady=(2, 0))

    # =====================================================================
    # 模式切换
    # =====================================================================
    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "check":
            self.compare_config_frame.grid_remove()
            self.check_config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
            self.result_notebook.select(self.check_result_tab)
            self.stop_btn.config(state=tk.DISABLED)
            self.stats_label.config(text="就绪")
        else:
            self.check_config_frame.grid_remove()
            self.compare_config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
            self.result_notebook.select(self.compare_result_tab)
            self.stats_label.config(text="A文件夹: 0个文件 | B文件夹: 0个文件 | 已匹配: 0 | 文件名不一致: 0 | 未匹配: 0")
        # 切换模式时重置进度条
        self.progress_var.set(0)
        self.export_btn.config(state=tk.DISABLED)

    # =====================================================================
    # 配置加载 / 保存
    # =====================================================================
    def _load_config(self):
        """从 app_config.json 加载两类模式的配置"""
        section = self.config_manager.get_section(self.CONFIG_SECTION, {})

        # 单文件夹检查配置
        self.folder_var.set(section.get("check_folder", ""))
        self.include_subfolders_var.set(section.get("check_include_subfolders", False))
        self.check_subtitle_var.set(section.get("check_check_subtitle", False))

        excluded_folders = section.get("check_excluded_folders", [])
        self.excluded_listbox.delete(0, tk.END)
        base_folder = section.get("check_folder", "")
        for path in excluded_folders:
            if os.path.exists(path):
                if base_folder and os.path.normpath(path) == os.path.normpath(base_folder):
                    continue
                self.excluded_listbox.insert(tk.END, path)

        # 双文件夹比对配置
        source_path = section.get("compare_source_folder", "")
        target_path = section.get("compare_target_folder", "")
        self.source_var.set(source_path)
        self.target_var.set(target_path)
        if source_path:
            self.source_folder = Path(source_path)
        if target_path:
            self.target_folder = Path(target_path)

    def _save_config(self):
        """保存两类模式的配置到 app_config.json"""
        config = {
            "check_folder": self.folder_var.get(),
            "check_include_subfolders": self.include_subfolders_var.get(),
            "check_check_subtitle": self.check_subtitle_var.get(),
            "check_excluded_folders": list(self.excluded_listbox.get(0, tk.END)),
            "compare_source_folder": self.source_var.get(),
            "compare_target_folder": self.target_var.get(),
        }
        self.config_manager.set_section(self.CONFIG_SECTION, config, auto_save=True)
        self.log("[文件夹检查] 配置已保存")
        messagebox.showinfo("成功", "配置已保存")

    # =====================================================================
    # 统一入口
    # =====================================================================
    def _run(self):
        """根据当前模式执行对应逻辑"""
        if self.mode_var.get() == "check":
            self._run_check()
        else:
            self._start_compare()

    # =====================================================================
    # 单文件夹检查模式 - 逻辑（移植自 video_check_tab.py）
    # =====================================================================
    def _browse_folder(self):
        folder = filedialog.askdirectory(title="选择要检查的文件夹")
        if folder:
            self.folder_var.set(folder)
            self.excluded_listbox.delete(0, tk.END)
            self.log("[单文件夹检查] 基础文件夹已更改，排除列表已自动清空")

    def _add_excluded_folder(self):
        initial_dir = self.folder_var.get().strip()
        if initial_dir and os.path.isdir(initial_dir):
            folder = filedialog.askdirectory(title="选择要排除的文件夹", initialdir=initial_dir)
        else:
            folder = filedialog.askdirectory(title="选择要排除的文件夹")
        if not folder:
            return
        folder_normalized = os.path.normpath(folder)
        existing = list(self.excluded_listbox.get(0, tk.END))
        if folder_normalized in existing:
            messagebox.showinfo("提示", "该文件夹已在排除列表中")
            return
        base_folder = self.folder_var.get().strip()
        if base_folder:
            base_normalized = os.path.normpath(base_folder)
            if folder_normalized == base_normalized:
                messagebox.showwarning("警告", "不能排除基础文件夹本身")
                return
            if not folder_normalized.startswith(base_normalized + os.sep):
                if not messagebox.askyesno("确认",
                    f"选择的文件夹:\n{folder_normalized}\n\n不在基础文件夹内:\n{base_normalized}\n\n确定要添加吗？"):
                    return
        self.excluded_listbox.insert(tk.END, folder_normalized)

    def _remove_excluded_folder(self):
        selected = self.excluded_listbox.curselection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要移除的文件夹")
            return
        for index in reversed(selected):
            self.excluded_listbox.delete(index)

    def _clear_all_excluded(self):
        if self.excluded_listbox.size() == 0:
            return
        if messagebox.askyesno("确认", "确定要清空所有排除的文件夹吗？"):
            self.excluded_listbox.delete(0, tk.END)

    def _validate_path(self, path: str) -> bool:
        if not path:
            return False
        try:
            return os.path.exists(path)
        except Exception:
            return False

    def _get_all_folders(self, base_path: str, include_subfolders: bool) -> List[str]:
        folders = []
        base_path = os.path.normpath(base_path)
        try:
            exclude_tuple = self.excluded_listbox.get(0, tk.END)
            excluded_paths = [os.path.normpath(p) for p in exclude_tuple if p] if exclude_tuple else []
        except Exception as e:
            self.log(f"[单文件夹检查] 获取排除列表失败: {e}")
            excluded_paths = []

        for excl in excluded_paths:
            if base_path == excl or base_path.startswith(excl + os.sep):
                return []

        if not include_subfolders:
            if os.path.isdir(base_path):
                folders.append(base_path)
        else:
            try:
                for root, dirs, files in os.walk(base_path):
                    root_normalized = os.path.normpath(root)
                    is_excluded = False
                    for excl in excluded_paths:
                        if root_normalized == excl or root_normalized.startswith(excl + os.sep):
                            is_excluded = True
                            break
                    if is_excluded:
                        dirs[:] = []
                        continue
                    dirs[:] = [d for d in dirs if d and not d.startswith('.') and not d.startswith('$')]
                    folders.append(root)
            except Exception as e:
                self.log(f"[单文件夹检查] 遍历文件夹失败: {e}")
        return folders

    def _is_skip_folder(self, folder_name: str) -> bool:
        for marker in self.SKIP_FOLDER_MARKERS:
            if marker in folder_name:
                return True
        return False

    def _has_builtin_subtitle(self, video_filename: str) -> bool:
        name_no_ext = os.path.splitext(video_filename)[0]
        upper_name = name_no_ext.upper()
        for suffix in self.BUILTIN_SUBTITLE_SUFFIXES:
            if upper_name.endswith(suffix.upper()):
                return True
        return False

    def _check_folder(self, folder_path: str) -> List[Dict]:
        """检查单个文件夹，返回问题列表（使用 pattern_matcher 识别番号/编号）"""
        issues = []
        folder_name = os.path.basename(folder_path)

        if self._is_skip_folder(folder_name):
            self.log(f"[单文件夹检查] 跳过标记文件夹: {folder_path}")
            return []

        if not os.path.isdir(folder_path):
            return [{
                "folder_path": folder_path, "file_name": "", "folder_code": "未识别",
                "file_code": "", "issue_type": "访问错误", "detail": "路径不存在或不是文件夹"
            }]

        all_items = os.listdir(folder_path)
        if all_items is None:
            all_items = []
        visible_items = [item for item in all_items
                         if not item.startswith('.') and not item.startswith('$')]
        file_count = 0
        subdir_count = 0
        for item in visible_items:
            item_path = os.path.join(folder_path, item)
            try:
                if os.path.isfile(item_path):
                    file_count += 1
                elif os.path.isdir(item_path):
                    subdir_count += 1
            except (OSError, PermissionError):
                continue

        # 分类文件夹：无任何文件，且至少有一个子文件夹
        if file_count == 0 and subdir_count >= 1:
            self.log(f"[单文件夹检查] 跳过分类文件夹: {folder_path}")
            return []

        # 获取文件夹番号（使用 pattern_matcher）
        if self.matcher is not None:
            folder_code_info = self.matcher.extract_code(folder_name)
            folder_code = folder_code_info['full'] if folder_code_info else None
            folder_code_display = folder_code_info['canonical'] if folder_code_info else "未识别"
        else:
            folder_code = None
            folder_code_display = "未识别"

        try:
            files = os.listdir(folder_path)
            if files is None:
                files = []
        except PermissionError:
            return [{
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "访问错误", "detail": "无法访问该文件夹（权限不足）"
            }]
        except Exception as e:
            return [{
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "访问错误", "detail": f"读取文件夹失败: {str(e)}"
            }]

        video_files, image_files, subtitle_files = [], [], []
        for file in files:
            if file is None or file.startswith('.'):
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in self.VIDEO_EXTENSIONS:
                video_files.append(file)
            elif ext in self.IMAGE_EXTENSIONS:
                image_files.append(file)
            elif ext in self.SUBTITLE_EXTENSIONS:
                subtitle_files.append(file)

        if not video_files:
            issues.append({
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "缺失视频",
                "detail": f"文件夹中未找到视频文件（支持格式: {', '.join(self.VIDEO_EXTENSIONS)}）"
            })

        if not image_files:
            issues.append({
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "缺失图片",
                "detail": f"文件夹中未找到封面图片（支持格式: {', '.join(self.IMAGE_EXTENSIONS)}）"
            })

        if self.check_subtitle_var.get():
            videos_need_subtitle_check, builtin_subtitle_videos = [], []
            for video_file in video_files:
                if self._has_builtin_subtitle(video_file):
                    builtin_subtitle_videos.append(video_file)
                else:
                    videos_need_subtitle_check.append(video_file)
            if videos_need_subtitle_check and not subtitle_files:
                skipped_info = ""
                if builtin_subtitle_videos:
                    skipped_names = ", ".join(builtin_subtitle_videos)
                    skipped_info = f"；以下视频因文件名含字幕标记已跳过检查: {skipped_names}"
                issues.append({
                    "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                    "file_code": "", "issue_type": "缺失字幕",
                    "detail": f"文件夹中有 {len(videos_need_subtitle_check)} 个视频需要字幕，但未找到字幕文件（.srt）{skipped_info}"
                })

        for video_file in video_files:
            file_name_no_ext = os.path.splitext(video_file)[0]
            file_code_info = self.matcher.extract_code(file_name_no_ext) if self.matcher else None

            if not file_code_info:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": folder_code_display, "file_code": "未识别",
                    "issue_type": "命名不规范", "detail": "视频文件名未能识别出番号/编号"
                })
                continue

            file_code = file_code_info['full']
            file_code_display = file_code_info['canonical']

            if not folder_code:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": "未识别", "file_code": file_code_display,
                    "issue_type": "命名不匹配",
                    "detail": f"文件夹名未识别番号，但视频文件识别为 {file_code_display}"
                })
                continue

            if self.matcher is not None:
                match_level = self.matcher.is_strict_match(file_code_info, folder_code_info)
            else:
                match_level = 0

            if match_level == 0:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": folder_code_display, "file_code": file_code_display,
                    "issue_type": "番号不匹配",
                    "detail": f"文件夹番号 {folder_code_display} 与文件番号 {file_code_display} 不匹配"
                })
            elif match_level == 1:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": folder_code_display, "file_code": file_code_display,
                    "issue_type": "疑似不匹配",
                    "detail": f"文件夹番号 {folder_code_display} 与文件番号 {file_code_display} 部分匹配但不完全一致"
                })
        return issues

    def _run_check(self):
        base_folder = self.folder_var.get().strip()
        if not base_folder:
            messagebox.showwarning("提示", "请先选择要检查的文件夹")
            return
        if not self._validate_path(base_folder):
            messagebox.showerror("错误", f"路径不存在或无法访问:\n{base_folder}")
            return

        excluded_paths = list(self.excluded_listbox.get(0, tk.END))
        invalid_excluded = [p for p in excluded_paths if not os.path.exists(p)]
        if invalid_excluded:
            if messagebox.askyesno("路径不存在", f"以下排除路径不存在，是否从列表中移除？\n\n" + "\n".join(invalid_excluded)):
                for path in invalid_excluded:
                    idx = list(self.excluded_listbox.get(0, tk.END)).index(path)
                    self.excluded_listbox.delete(idx)
            else:
                return

        for item in self.check_tree.get_children():
            self.check_tree.delete(item)
        self.check_results.clear()
        self.export_btn.config(state=tk.DISABLED)
        self.sort_column = None

        self.run_btn.config(state=tk.DISABLED, text="检查中...")
        self.save_btn.config(state=tk.DISABLED)
        self.stats_label.config(text="正在扫描文件夹...")
        self.frame.update()

        try:
            include_sub = self.include_subfolders_var.get()
            folders = self._get_all_folders(base_folder, include_sub)
            total = len(folders)
            if total == 0:
                messagebox.showinfo("提示", "未找到需要检查的文件夹")
                self.stats_label.config(text="就绪")
                return

            all_issues = []
            for idx, folder in enumerate(folders, 1):
                progress = (idx / total) * 100
                self.progress_var.set(progress)
                self.stats_label.config(text=f"正在检查: {idx}/{total} - {os.path.basename(folder)}")
                self.frame.update()
                all_issues.extend(self._check_folder(folder))

            self.check_results = all_issues
            self._display_check_results(all_issues)

            issue_count = len(all_issues)
            folder_with_issues = len(set(i['folder_path'] for i in all_issues))
            excluded_count = len(excluded_paths)
            self.stats_label.config(
                text=f"检查完成: 共 {total} 个文件夹（已排除 {excluded_count} 个），发现 {issue_count} 个问题（涉及 {folder_with_issues} 个文件夹）"
            )
            if all_issues:
                self.export_btn.config(state=tk.NORMAL)
            else:
                messagebox.showinfo("检查完成", "恭喜！未发现命名不匹配或文件缺失的问题。")
        except Exception as e:
            messagebox.showerror("错误", f"检查过程中发生错误:\n{str(e)}")
            self.stats_label.config(text=f"检查失败: {str(e)}")
        finally:
            self.run_btn.config(state=tk.NORMAL, text="▶ 开始执行")
            self.save_btn.config(state=tk.NORMAL)
            self.progress_var.set(0)

    def _sort_tree(self, col: str):
        data = [(self.check_tree.set(child, col), child) for child in self.check_tree.get_children('')]
        try:
            float(data[0][0])
            data.sort(key=lambda x: float(x[0]), reverse=self.sort_reverse)
        except (ValueError, IndexError):
            data.sort(key=lambda x: x[0].lower(), reverse=self.sort_reverse)
        for index, (_, item) in enumerate(data):
            self.check_tree.move(item, '', index)
        self.sort_reverse = not self.sort_reverse
        self.sort_column = col
        for column in self.check_tree["columns"]:
            text = self.check_tree.heading(column, "text").replace(" ▲", "").replace(" ▼", "")
            if column == col:
                text += " ▼" if self.sort_reverse else " ▲"
            self.check_tree.heading(column, text=text)

    def _display_check_results(self, results: List[Dict]):
        priority = {"番号不匹配": 1, "疑似不匹配": 2, "命名不规范": 3,
                   "缺失视频": 4, "缺失图片": 5, "缺失字幕": 6, "访问错误": 7}
        sorted_results = sorted(results, key=lambda x: priority.get(x['issue_type'], 99))
        for issue in sorted_results:
            tag = ''
            if issue['issue_type'] in ['番号不匹配']:
                tag = 'mismatch'
            elif issue['issue_type'] in ['疑似不匹配', '命名不规范']:
                tag = 'warning'
            elif issue['issue_type'] in ['缺失视频', '缺失图片', '缺失字幕']:
                tag = 'missing'
            elif issue['issue_type'] == '访问错误':
                tag = 'error'
            self.check_tree.insert('', tk.END, values=(
                issue['folder_path'], issue['file_name'], issue['folder_code'],
                issue['file_code'], issue['issue_type'], issue['detail']
            ), tags=(tag,))

    # =====================================================================
    # 单文件夹检查模式 - 导出 / 右键菜单
    # =====================================================================
    def _export_check_csv(self):
        if not self.check_results:
            return False
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialfile=f"视频检查结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not file_path:
            return False
        try:
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['文件夹路径', '文件名', '文件夹番号', '文件番号', '问题类型', '详细说明'])
                for issue in self.check_results:
                    writer.writerow([
                        issue['folder_path'], issue['file_name'], issue['folder_code'],
                        issue['file_code'], issue['issue_type'], issue['detail']
                    ])
            messagebox.showinfo("成功", f"结果已导出到:\n{file_path}")
            return True
        except Exception as e:
            messagebox.showerror("错误", f"导出失败:\n{str(e)}")
            return False

    def _export_compare_csv(self):
        if not self.compare_results:
            return False
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialfile=f"文件比对结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not file_path:
            return False
        try:
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['原视频文件名(A)', '已转码文件名(B)', '状态', '备注', '原文件路径'])
                for r in self.compare_results:
                    writer.writerow([
                        r.get('source_name', ''),
                        r.get('target_name', '') or '-',
                        r.get('status', ''),
                        r.get('note', ''),
                        str(r.get('source_file', ''))
                    ])
            messagebox.showinfo("成功", f"结果已导出到:\n{file_path}")
            return True
        except Exception as e:
            messagebox.showerror("错误", f"导出失败:\n{str(e)}")
            return False

    def _export_results(self):
        if self.mode_var.get() == "check":
            self._export_check_csv()
        else:
            self._export_compare_csv()

    def _show_context_menu(self, event):
        item = self.check_tree.identify_row(event.y)
        if item:
            self.check_tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def _copy_path(self):
        selected = self.check_tree.selection()
        if selected:
            path = self.check_tree.item(selected[0])['values'][0]
            self.frame.clipboard_clear()
            self.frame.clipboard_append(path)
            self.frame.update()

    def _copy_filename(self):
        selected = self.check_tree.selection()
        if selected:
            filename = self.check_tree.item(selected[0])['values'][1]
            if filename:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(filename)
                self.frame.update()

    def _copy_folder_code(self):
        selected = self.check_tree.selection()
        if selected:
            folder_code = self.check_tree.item(selected[0])['values'][2]
            if folder_code:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(folder_code)
                self.frame.update()

    def _open_in_explorer(self):
        selected = self.check_tree.selection()
        if selected:
            path = self.check_tree.item(selected[0])['values'][0]
            if os.path.exists(path):
                os.startfile(path)
            else:
                messagebox.showwarning("警告", "路径不存在或无法访问")

    # =====================================================================
    # 双文件夹比对模式 - 逻辑（移植自 file_compare_tab.py）
    # =====================================================================
    def _browse_source_folder(self):
        directory = filedialog.askdirectory(title="选择原视频文件夹(A)")
        if directory:
            self.source_var.set(directory)
            self.source_folder = Path(directory)

    def _browse_target_folder(self):
        directory = filedialog.askdirectory(title="选择已转码文件夹(B)")
        if directory:
            self.target_var.set(directory)
            self.target_folder = Path(directory)

    def _start_compare(self):
        source_path_str = self.source_var.get().strip()
        target_path_str = self.target_var.get().strip()
        if not source_path_str or not target_path_str:
            messagebox.showwarning("警告", "请设置原视频文件夹和已转码文件夹")
            return
        self.source_folder = Path(source_path_str)
        self.target_folder = Path(target_path_str)
        if not self.source_folder.exists() or not self.target_folder.exists():
            messagebox.showwarning("警告", "文件夹路径不存在")
            return

        self._clear_compare_results()
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.is_running = True

        self.operation_thread = threading.Thread(target=self._compare_thread, daemon=True)
        self.operation_thread.start()

    def _stop_compare(self):
        self.is_running = False
        self.log("[双文件夹比对] 正在停止...")
        self.stop_btn.config(state=tk.DISABLED)

    def _compare_thread(self):
        try:
            self.log("=" * 60)
            self.log("[双文件夹比对] 开始对比")
            self.log(f"原视频文件夹(A): {self.source_folder}")
            self.log(f"已转码文件夹(B): {self.target_folder}")
            self.log("=" * 60)

            self._update_progress(10, "正在扫描原视频文件夹...")
            source_files = self._collect_video_files(self.source_folder)
            source_count = len(source_files)
            if not self.is_running:
                return
            self.log(f"[双文件夹比对] A文件夹发现 {source_count} 个视频文件")

            self._update_progress(30, "正在扫描已转码文件夹...")
            target_files = self._collect_video_files(self.target_folder)
            target_count = len(target_files)
            if not self.is_running:
                return
            self.log(f"[双文件夹比对] B文件夹发现 {target_count} 个视频文件")

            self._update_progress(50, f"扫描完成 A:{source_count} B:{target_count}，开始对比...")

            target_index = {}
            target_names = {}
            for tf in target_files:
                target_index[tf.name.lower()] = tf
                stem_lower = tf.stem.lower()
                if stem_lower not in target_index:
                    target_index[stem_lower] = tf
                target_names[stem_lower] = tf

            perfect_match = 0
            similar_match = 0
            unmatched = 0
            total = len(source_files)

            for idx, source_file in enumerate(source_files, 1):
                if not self.is_running:
                    break
                progress = 50 + int((idx / total) * 50)
                self._update_progress(progress, f"正在对比: {source_file.name[:40]}...")
                result = self._compare_single_file(source_file, target_index, target_names)
                self.compare_results.append(result)
                if result['status'] == 'perfect':
                    perfect_match += 1
                    self.matched_files.append(result)
                elif result['status'] == 'similar':
                    similar_match += 1
                    self.matched_files.append(result)
                else:
                    unmatched += 1
                    self.unmatched_files.append(source_file)
                if idx % 10 == 0 or idx == total:
                    self._update_stats(source_count, target_count, perfect_match, similar_match, unmatched)

            self._update_progress(100, "对比完成")
            self.frame.after(0, self._update_compare_result_ui)
            self.log("\n" + "=" * 60)
            self.log(f"[双文件夹比对] 对比完成: 完全匹配 {perfect_match}, 文件名不一致 {similar_match}, 未匹配 {unmatched}")
            self.log("=" * 60)
            self.frame.after(0, lambda: self._compare_completed(perfect_match, similar_match, unmatched))
        except Exception as e:
            self.log(f"[双文件夹比对] 错误: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.frame.after(0, lambda: messagebox.showerror("错误", f"对比过程出错: {e}"))
            self.frame.after(0, self._reset_compare_ui)

    def _collect_video_files(self, folder: Path) -> List[Path]:
        video_files = []
        try:
            for item in folder.iterdir():
                if item.is_file() and item.suffix.lower() in self.VIDEO_EXTENSIONS:
                    video_files.append(item)
        except Exception as e:
            self.log(f"[双文件夹比对] 访问文件夹失败 {folder}: {e}")
        video_files.sort(key=lambda x: x.name.lower())
        return video_files

    def _compare_single_file(self, source_file: Path, target_index: Dict[str, Path],
                             target_names: Dict[str, Path]) -> Dict:
        source_name = source_file.name
        source_stem = source_file.stem.lower()
        result = {
            'source_file': source_file, 'target_file': None, 'status': 'unmatched',
            'note': '未找到匹配文件', 'source_name': source_name, 'target_name': None
        }
        if source_name.lower() in target_index:
            matched_file = target_index[source_name.lower()]
            result['target_file'] = matched_file
            result['target_name'] = matched_file.name
            result['status'] = 'perfect'
            result['note'] = '文件名完全一致'
            return result
        if source_stem in target_names:
            matched_file = target_names[source_stem]
            result['target_file'] = matched_file
            result['target_name'] = matched_file.name
            result['status'] = 'perfect'
            result['note'] = f'文件名相同（格式不同: {matched_file.suffix}）'
            return result
        similar_pattern = re.compile(r'^' + re.escape(source_stem) + r'[\s\-_~]*(\d+)$', re.IGNORECASE)
        for target_stem, target_file in target_names.items():
            if similar_pattern.match(target_stem):
                result['target_file'] = target_file
                result['target_name'] = target_file.name
                result['status'] = 'similar'
                result['note'] = f'文件名不一致（找到: {target_file.name}）'
                return result
        return result

    def _update_compare_result_ui(self):
        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)
        for item in self.unmatched_tree.get_children():
            self.unmatched_tree.delete(item)
        for result in self.matched_files:
            if result['status'] == 'perfect':
                tags, status_text = ('perfect',), "✓ 已完成"
            else:
                tags, status_text = ('similar',), "⚠ 已完成"
            self.matched_tree.insert('', tk.END, values=(
                result['source_name'],
                result['target_name'] if result['target_name'] else '-',
                status_text, result['note']
            ), tags=tags)
        for source_file in self.unmatched_files:
            self.unmatched_tree.insert('', tk.END, values=(source_file.name, str(source_file)), tags=('unmatched',))

    def _update_progress(self, value: int, message: str):
        self.progress_queue.put(('progress', value, message))

    def _update_stats(self, source_count: int, target_count: int, perfect: int, similar: int, unmatched: int):
        self.progress_queue.put(('stats', source_count, target_count, perfect, similar, unmatched))

    def _check_progress(self):
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                msg_type = msg[0]
                if msg_type == 'progress':
                    _, value, message = msg
                    self.progress_var.set(value)
                    if hasattr(self, 'progress_label'):
                        self.progress_label.config(text=message)
                elif msg_type == 'stats':
                    _, source_count, target_count, perfect, similar, unmatched = msg
                    self.stats_label.config(text=
                        f"A文件夹: {source_count}个文件 | B文件夹: {target_count}个文件 | "
                        f"已匹配: {perfect} | 文件名不一致: {similar} | 未匹配: {unmatched}"
                    )
        except queue.Empty:
            pass
        # 仅当存在（比对模式已构建）时才继续轮询
        if hasattr(self, 'progress_label'):
            self.frame.after(100, self._check_progress)

    def _compare_completed(self, perfect: int, similar: int, unmatched: int):
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False
        if self.compare_results:
            self.export_btn.config(state=tk.NORMAL)
        msg = f"对比完成！\n\n完全匹配: {perfect}\n文件名不一致: {similar}\n未匹配(待转码): {unmatched}"
        messagebox.showinfo("完成", msg)

    def _reset_compare_ui(self):
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False

    def _clear_compare_results(self):
        self.compare_results.clear()
        self.matched_files.clear()
        self.unmatched_files.clear()
        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)
        for item in self.unmatched_tree.get_children():
            self.unmatched_tree.delete(item)
        self.progress_var.set(0)
        if hasattr(self, 'progress_label'):
            self.progress_label.config(text="准备就绪")
        self.stats_label.config(text="A文件夹: 0个文件 | B文件夹: 0个文件 | 已匹配: 0 | 文件名不一致: 0 | 未匹配: 0")
        self.log("[双文件夹比对] 结果已清空")

    def _select_files_in_explorer(self, files: List[Path], folder_path: Path, description: str):
        if not files:
            messagebox.showwarning("警告", f"没有{description}可选中")
            return
        existing_files = [f for f in files if f.exists()]
        if not existing_files:
            messagebox.showwarning("警告", f"{description}文件不存在")
            return
        try:
            if len(existing_files) == 1:
                cmd = f'explorer /select,"{existing_files[0]}"'
                subprocess.Popen(cmd, shell=True)
                self.log(f"[双文件夹比对] 已选中 {description}: {existing_files[0].name}")
            else:
                subprocess.Popen(f'explorer "{folder_path}"', shell=True)
                batch_lines = ['@echo off', 'echo 正在选中文件...', 'timeout /t 1 /nobreak >nul']
                for f in existing_files[:10]:
                    batch_lines.append(f'explorer /select,"{f}"')
                    batch_lines.append('timeout /t 1 /nobreak >nul')
                with tempfile.NamedTemporaryFile(mode='w', suffix='.bat', delete=False, encoding='gbk') as f:
                    f.write('\n'.join(batch_lines))
                    batch_file = f.name
                subprocess.Popen(batch_file, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
                self.frame.after(10000, lambda p=batch_file: os.unlink(p) if os.path.exists(p) else None)
                self.log(f"[双文件夹比对] 已打开文件夹并尝试选中 {len(existing_files)} 个{description}: {folder_path}")
                if len(existing_files) > 10:
                    messagebox.showinfo("提示",
                        f"由于Windows限制，已打开文件夹: {folder_path}\n"
                        f"并尝试选中前10个文件。其余文件请手动选择。\n总共 {len(existing_files)} 个文件")
        except Exception as e:
            self.log(f"[双文件夹比对] 选中文件失败: {e}")
            try:
                subprocess.Popen(f'explorer "{folder_path}"', shell=True)
                self.log(f"[双文件夹比对] 已打开文件夹: {folder_path}")
            except Exception:
                pass
            messagebox.showerror("错误", f"选中文件失败: {e}\n已尝试打开文件夹")

    def _select_matched_files(self):
        if not self.matched_files:
            messagebox.showwarning("警告", "没有已匹配的文件，请先执行对比操作")
            return
        target_files = [r['target_file'] for r in self.matched_files
                        if r['target_file'] and r['target_file'].exists()]
        if not target_files:
            messagebox.showwarning("警告", "匹配的文件在B文件夹中不存在")
            return
        self._select_files_in_explorer(target_files, self.target_folder, "已转码文件")

    def _select_unmatched_files(self):
        if not self.unmatched_files:
            messagebox.showwarning("警告", "没有未匹配的文件，请先执行对比操作")
            return
        self._select_files_in_explorer(self.unmatched_files, self.source_folder, "未转码文件")


# ==================== 独立运行入口 ====================
class SimpleFolderCheckApp:
    """独立运行时的简单主窗口（用于测试和单独使用）"""

    def __init__(self, root):
        self.root = root
        self.root.title("文件夹检查 / 文件比对工具")
        self.root.geometry("1200x850")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tab = FolderCheckTab(self.notebook, controller=None)
        self.notebook.add(self.tab.get_frame(), text="文件夹检查/比对")

        self.status_bar = ttk.Label(root, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        print("[SimpleFolderCheckApp] 独立运行模式已启动")


def main():
    """独立运行入口"""
    root = tk.Tk()
    app = SimpleFolderCheckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
