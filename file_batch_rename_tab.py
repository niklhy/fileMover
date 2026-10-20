#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名批量修改选项卡模块 - 参考 batch_rename_tool.py 的功能实现
支持独立运行和通过 main.py 嵌入运行（作为 Notebook 中的一个选项卡）

功能（与原批量改名工具一致）：
1. 整体重命名：按命名规则（支持 * 原文件名、# 递进编号）生成新名
2. 替换：替换文件名主体中的指定字符
3. 添加/删除：文件名前/后添加、扩展名前添加、按位置添加、删除字符、扩展名删除等
4. 实时预览：原文件名 / 预览 / 结果 三列，并标记冲突、非法等状态
5. 冲突处理：自动追加序号 / 跳过冲突文件 / 覆盖目标文件
6. 统一大小写转换
7. 链式冲突（新旧名互相占用）自动两阶段改名
"""

import os
import string
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # noqa: F401
    _HAS_DND = True
except ImportError:
    DND_FILES = None
    _HAS_DND = False


class BatchRenameTab:
    """文件名批量修改选项卡（可嵌入 Notebook，也可独立运行）"""

    CONFIG_SECTION = "file_batch_rename"

    def __init__(self, parent_notebook, controller=None, config_manager=None):
        self.parent = parent_notebook
        self.controller = controller
        self.is_standalone = controller is None
        self.config_manager = config_manager

        # 文件列表与预览数据
        self.files = []
        self.previews = []
        # 待执行的预览刷新任务（用于合并连续的快速变更，避免卡顿）
        self._refresh_job = None
        self.overall_placeholder = '在此输入“A_#”则文件名为“A_<数字编号>”'
        self._overall_placeholder_visible = False

        # 主框架（用于嵌入 Notebook）
        self.frame = ttk.Frame(parent_notebook)

        self._build_vars()
        self._build_ui()
        self._bind_var_traces()
        self.refresh_preview()

        # 定时校准列宽需要挂在 Tk 根上（Frame 没有 after 方法）
        self._root = self.frame.winfo_toplevel()
        if self._root is not None:
            self._root.after(100, self._auto_fit_loop)

    # ========== 变量定义 ==========
    def _build_vars(self):
        self.overall_name = tk.StringVar(value="")
        self.overall_start = tk.IntVar(value=1)
        self.overall_step = tk.IntVar(value=1)
        self.overall_digits = tk.IntVar(value=3)
        self.overall_zero_pad = tk.BooleanVar(value=True)
        self.overall_alpha = tk.BooleanVar(value=False)
        self.overall_keep_ext = tk.BooleanVar(value=True)
        self.overall_ext_text = tk.StringVar(value="")
        self.overall_conflict = tk.StringVar(value="自动追加序号")
        self.case_convert = tk.StringVar(value="不改变")
        self.replace_from = tk.StringVar(value="")
        self.replace_to = tk.StringVar(value="")
        self.add_prefix_enabled = tk.BooleanVar(value=False)
        self.add_suffix_enabled = tk.BooleanVar(value=False)
        self.add_before_ext_enabled = tk.BooleanVar(value=False)
        self.add_at_pos_enabled = tk.BooleanVar(value=False)
        self.add_prefix = tk.StringVar(value="")
        self.add_suffix = tk.StringVar(value="")
        self.add_before_ext = tk.StringVar(value="")
        self.add_pos = tk.IntVar(value=1)
        self.add_at_pos = tk.StringVar(value="")
        self.find_pos_enabled = tk.BooleanVar(value=False)
        self.find_pos_text = tk.StringVar(value="")
        self.delete_text = tk.StringVar(value="")
        self.delete_name_enabled = tk.BooleanVar(value=False)
        self.delete_ext_enabled = tk.BooleanVar(value=False)
        self.delete_start = tk.IntVar(value=1)
        self.delete_count = tk.IntVar(value=1)
        self.delete_ext_count = tk.IntVar(value=1)
        self.clear_after_rename = tk.BooleanVar(value=False)

    # ========== UI 构建 ==========
    def _build_ui(self):
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        main = ttk.Frame(self.frame, padding=8)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")
        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        right = ttk.Frame(paned)
        paned.add(left, weight=0)
        paned.add(right, weight=1)
        left.configure(width=360)
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        style = ttk.Style(self.frame)
        style.configure("Equal.TNotebook.Tab", width=12, anchor="center")
        self.notebook = ttk.Notebook(left, style="Equal.TNotebook")
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.refresh_preview())
        self._build_overall_tab()
        self._build_replace_tab()
        self._build_add_delete_tab()
        self._build_file_list(right)
        self._build_bottom_buttons(main)

    def _build_overall_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="整体")
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="命名规则").grid(row=0, column=0, columnspan=2, sticky="w")
        style = ttk.Style(self.frame)
        style.configure("Placeholder.TEntry", foreground="#888888")
        self.overall_entry = ttk.Entry(tab, textvariable=self.overall_name)
        self.overall_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        self.overall_entry.bind("<FocusIn>", self._hide_overall_placeholder)
        self.overall_entry.bind("<FocusOut>", self._show_overall_placeholder)
        self._show_overall_placeholder()
        ttk.Label(tab, text="说明：* 插入原文件名，# 插入递进数字/字母").grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(tab, text="例如：前缀_*_#").grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(tab, text="开始于").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Spinbox(tab, from_=0, to=999999, textvariable=self.overall_start, width=10).grid(row=4, column=1, sticky="w", pady=3)
        ttk.Label(tab, text="增量").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Spinbox(tab, from_=1, to=999999, textvariable=self.overall_step, width=10).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Label(tab, text="位数").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Spinbox(tab, from_=1, to=20, textvariable=self.overall_digits, width=10).grid(row=6, column=1, sticky="w", pady=3)
        ttk.Checkbutton(tab, text="数字编号补齐前导 0", variable=self.overall_zero_pad).grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 3))
        ttk.Checkbutton(tab, text="使用字母编号（A、B、C...）", variable=self.overall_alpha).grid(row=8, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(tab, text="保留原扩展名", variable=self.overall_keep_ext, command=self._sync_states).grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 3))
        ttk.Label(tab, text="指定扩展名").grid(row=10, column=0, sticky="w", pady=3)
        self.overall_ext_entry = ttk.Entry(tab, textvariable=self.overall_ext_text)
        self.overall_ext_entry.grid(row=10, column=1, sticky="ew", pady=3)
        ttk.Label(tab, text="如：jpg 或 .jpg；保留原扩展名时禁用").grid(row=11, column=0, columnspan=2, sticky="w")
        ttk.Label(tab, text="冲突处理").grid(row=12, column=0, sticky="w", pady=(16, 3))
        ttk.Combobox(tab, textvariable=self.overall_conflict, values=["自动追加序号", "跳过冲突文件", "覆盖目标文件"], state="readonly", width=16).grid(row=12, column=1, sticky="w", pady=(16, 3))
        ttk.Label(tab, text="统一转换").grid(row=13, column=0, sticky="w", pady=(12, 3))
        ttk.Combobox(
            tab,
            textvariable=self.case_convert,
            values=[
                "不改变",
                "改变文件名为小写",
                "改变扩展名为小写",
                "改变文件名和扩展名为小写",
                "改变文件名为大写",
                "改变扩展名为大写",
                "改变文件名和扩展名为大写",
            ],
            state="readonly",
            width=24,
        ).grid(row=13, column=1, sticky="w", pady=(12, 3))
        self._sync_states()

    def _build_replace_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="替换")
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="替换文件名中的字符").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        ttk.Label(tab, text="把").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(tab, textvariable=self.replace_from).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(tab, text="替换成").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(tab, textvariable=self.replace_to).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(tab, text="说明：只替换文件名主体，默认保留扩展名。").grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_add_delete_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="添加/删除")
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=1)
        row = 0
        ttk.Label(tab, text="添加字符串").grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8)); row += 1
        self._check_entry(tab, row, "文件名前添加", self.add_prefix_enabled, self.add_prefix); row += 1
        self._check_entry(tab, row, "文件名后添加", self.add_suffix_enabled, self.add_suffix); row += 1
        self._check_entry(tab, row, "扩展名前添加", self.add_before_ext_enabled, self.add_before_ext); row += 1
        ttk.Checkbutton(tab, text="从第几个字符开始", variable=self.add_at_pos_enabled, command=self._sync_states).grid(row=row, column=0, sticky="w", pady=4)
        self.add_pos_spin = ttk.Spinbox(tab, from_=1, to=9999, textvariable=self.add_pos, width=8)
        self.add_pos_spin.grid(row=row, column=1, sticky="w", pady=4)
        self.add_at_pos_entry = ttk.Entry(tab, textvariable=self.add_at_pos)
        self.add_at_pos_entry.grid(row=row, column=2, sticky="ew", pady=4)
        row += 1
        ttk.Checkbutton(tab, text="查找字符位置", variable=self.find_pos_enabled, command=self._sync_states).grid(row=row, column=0, sticky="w", pady=4)
        self.find_pos_entry = ttk.Entry(tab, textvariable=self.find_pos_text, width=14)
        self.find_pos_entry.grid(row=row, column=1, sticky="w", pady=4)
        ttk.Label(tab, text="从首次出现该字符的位置开始插入/删除").grid(row=row, column=2, sticky="w", pady=4)
        row += 1
        ttk.Separator(tab).grid(row=row, column=0, columnspan=3, sticky="ew", pady=14); row += 1
        ttk.Label(tab, text="删除文件名中的").grid(row=row, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(tab, textvariable=self.delete_text).grid(row=row, column=1, columnspan=2, sticky="ew", pady=(0, 8))
        row += 1
        ttk.Label(tab, text="删除字符").grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 8)); row += 1
        ttk.Checkbutton(tab, text="删除文件名中的", variable=self.delete_name_enabled, command=self._sync_states).grid(row=row, column=0, sticky="w", pady=4)
        self.delete_start_spin = ttk.Spinbox(tab, from_=1, to=9999, textvariable=self.delete_start, width=8)
        self.delete_start_spin.grid(row=row, column=1, sticky="w", pady=4)
        ttk.Label(tab, text="开始，共删除").grid(row=row, column=2, sticky="w", pady=4)
        row += 1
        ttk.Label(tab, text="字符数").grid(row=row, column=0, sticky="w", pady=4)
        self.delete_count_spin = ttk.Spinbox(tab, from_=1, to=9999, textvariable=self.delete_count, width=8)
        self.delete_count_spin.grid(row=row, column=1, sticky="w", pady=4)
        row += 1
        ttk.Checkbutton(tab, text="扩展删除", variable=self.delete_ext_enabled, command=self._sync_states).grid(row=row, column=0, sticky="w", pady=(12, 4))
        self.delete_ext_count_spin = ttk.Spinbox(tab, from_=1, to=9999, textvariable=self.delete_ext_count, width=8)
        self.delete_ext_count_spin.grid(row=row, column=1, sticky="w", pady=(12, 4))
        ttk.Label(tab, text="个字符").grid(row=row, column=2, sticky="w", pady=(12, 4))
        self._sync_states()

    def _check_entry(self, parent, row, text, bool_var, str_var):
        ttk.Checkbutton(parent, text=text, variable=bool_var, command=self._sync_states).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=str_var)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        setattr(self, text + "_entry", entry)

    def _build_file_list(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        tip = "文件列表（可从资源管理器多选文件后拖到这里）" if _HAS_DND else "文件列表（如需拖拽，请安装 tkinterdnd2）"
        ttk.Label(parent, text=tip).grid(row=0, column=0, sticky="w", pady=(0, 4))
        table_frame = ttk.Frame(parent)
        table_frame.grid(row=1, column=0, sticky="nsew")
        columns = ("original", "preview", "result")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("original", text="原文件名", anchor="center")
        self.tree.heading("preview", text="预览", anchor="center")
        self.tree.heading("result", text="结果", anchor="center")
        self.tree.column("original", minwidth=20, width=300, stretch=False)
        self.tree.column("preview", minwidth=20, width=300, stretch=False)
        self.tree.column("result", minwidth=20, width=140, stretch=False)
        self.tree.bind("<Configure>", self._resize_list_columns, add="+")
        self.tree.bind("<Expose>", self._resize_list_columns, add="+")
        self.tree.bind("<MouseWheel>", self._on_list_mousewheel)
        self.tree.bind("<Button-4>", lambda _e: self.tree.yview_scroll(-1, "units"))
        self.tree.bind("<Button-5>", lambda _e: self.tree.yview_scroll(1, "units"))
        self.ybar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.ybar.set)
        self.ybar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        table_frame.pack_propagate(False)
        table_frame.bind("<Configure>", self._resize_list_columns, add="+")
        self._enable_drag_drop(table_frame)
        list_buttons = ttk.Frame(parent)
        list_buttons.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(list_buttons, text="添加(A)", command=self.add_files).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(list_buttons, text="添加文件夹", command=self.add_folder).grid(row=0, column=1, padx=6)
        ttk.Button(list_buttons, text="移除(I)", command=self.remove_selected).grid(row=0, column=2, padx=6)
        ttk.Button(list_buttons, text="刷新预览", command=self.refresh_preview).grid(row=0, column=3, padx=6)
        ttk.Checkbutton(list_buttons, text="更名后清空列表", variable=self.clear_after_rename).grid(row=0, column=4, padx=(14, 0))
        move_buttons = ttk.Frame(parent)
        move_buttons.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(move_buttons, text="移到最前", command=self.move_to_top).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(move_buttons, text="上移", command=self.move_up).grid(row=0, column=1, padx=6)
        ttk.Button(move_buttons, text="下移", command=self.move_down).grid(row=0, column=2, padx=6)
        ttk.Button(move_buttons, text="移到最后", command=self.move_to_bottom).grid(row=0, column=3, padx=6)
        ttk.Button(move_buttons, text="清空", command=self.clear_files).grid(row=0, column=4, padx=(14, 0))

    def _build_bottom_buttons(self, parent):
        bottom = ttk.Frame(parent)
        bottom.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Button(bottom, text="开始重命名", command=self.rename_files).grid(row=0, column=1, padx=6)
        ttk.Button(bottom, text="重置", command=self.clear_files).grid(row=0, column=2, padx=6)
        ttk.Button(bottom, text="帮助", command=self.show_help).grid(row=0, column=3, padx=(6, 0))

    # ========== 列表交互 ==========
    def _on_list_mousewheel(self, event):
        self.tree.yview_scroll(-1 * (event.delta // 120), "units")
        return "break"

    def _resize_list_columns(self, event=None):
        if not hasattr(self, "tree") or not self.tree.winfo_exists():
            return
        width = self.tree.winfo_width()
        if width <= 1:
            return
        result_w = int(width * 0.16)
        side_w = (width - result_w) // 2
        target = {"original": side_w, "preview": side_w, "result": result_w}
        if all(self.tree.column(c, "width") == w for c, w in target.items()):
            return
        self.tree.column("original", width=side_w)
        self.tree.column("preview", width=side_w)
        self.tree.column("result", width=result_w)

    def _auto_fit_loop(self):
        try:
            self._resize_list_columns()
        except Exception:
            pass
        if self._root is not None and self._root.winfo_exists():
            self._root.after(250, self._auto_fit_loop)

    def _enable_drag_drop(self, table_frame):
        if not _HAS_DND:
            return
        try:
            # tkdnd 的 Tcl 扩展只有在根窗口为 TkinterDnD.Tk 时才会被加载，
            # 嵌入到普通 tk.Tk 主程序时 drop_target_register 会失败，此时静默降级为不可用。
            for widget in (table_frame, self.tree):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_files_dropped)
        except Exception:
            pass

    def _on_files_dropped(self, event):
        paths = self.frame.tk.splitlist(event.data)
        files = []
        for path in paths:
            path = path.strip()
            if os.path.isfile(path):
                files.append(path)
            elif os.path.isdir(path):
                files.extend(str(p) for p in Path(path).iterdir() if p.is_file())
        self._add_paths(files)

    def _show_overall_placeholder(self, _event=None):
        if not hasattr(self, "overall_entry"):
            return
        if self.overall_name.get():
            return
        self._overall_placeholder_visible = True
        self.overall_entry.configure(style="Placeholder.TEntry")
        self.overall_name.set(self.overall_placeholder)

    def _hide_overall_placeholder(self, _event=None):
        if not hasattr(self, "overall_entry"):
            return
        if self._overall_placeholder_visible:
            self.overall_name.set("")
            self.overall_entry.configure(style="TEntry")
            self._overall_placeholder_visible = False

    def _get_overall_pattern(self):
        if self._overall_placeholder_visible:
            return ""
        return self.overall_name.get()

    def _bind_var_traces(self):
        vars_to_trace = [
            self.overall_name, self.overall_start, self.overall_step, self.overall_digits, self.overall_zero_pad,
            self.overall_alpha, self.overall_keep_ext, self.overall_ext_text, self.overall_conflict,
            self.case_convert, self.replace_from, self.replace_to, self.add_prefix_enabled, self.add_suffix_enabled,
            self.add_before_ext_enabled, self.add_at_pos_enabled, self.add_prefix, self.add_suffix,
            self.add_before_ext, self.add_pos, self.add_at_pos, self.delete_text, self.delete_name_enabled,
            self.delete_ext_enabled, self.delete_start, self.delete_count, self.delete_ext_count,
            self.find_pos_enabled, self.find_pos_text,
        ]
        for var in vars_to_trace:
            var.trace_add("write", lambda *_: self._schedule_refresh())

    def _sync_states(self):
        if hasattr(self, "overall_ext_entry"):
            self.overall_ext_entry.configure(state="disabled" if self.overall_keep_ext.get() else "normal")
        controls = [
            ("文件名前添加_entry", self.add_prefix_enabled.get()),
            ("文件名后添加_entry", self.add_suffix_enabled.get()),
            ("扩展名前添加_entry", self.add_before_ext_enabled.get()),
        ]
        for attr, enabled in controls:
            if hasattr(self, attr):
                getattr(self, attr).configure(state="normal" if enabled else "disabled")
        if hasattr(self, "add_pos_spin") and hasattr(self, "add_at_pos_entry"):
            for widget in (self.add_pos_spin, self.add_at_pos_entry):
                widget.configure(state="normal" if self.add_at_pos_enabled.get() else "disabled")
        if hasattr(self, "find_pos_entry"):
            self.find_pos_entry.configure(state="normal" if self.find_pos_enabled.get() else "disabled")
        if hasattr(self, "delete_start_spin") and hasattr(self, "delete_count_spin"):
            for widget in (self.delete_start_spin, self.delete_count_spin):
                widget.configure(state="normal" if self.delete_name_enabled.get() else "disabled")
        if hasattr(self, "delete_ext_count_spin"):
            self.delete_ext_count_spin.configure(state="normal" if self.delete_ext_enabled.get() else "disabled")
        self.refresh_preview()

    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择要改名的文件")
        self._add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择文件夹")
        if not folder:
            return
        paths = [str(p) for p in Path(folder).iterdir() if p.is_file()]
        self._add_paths(paths)

    def _add_paths(self, paths):
        existing = set(self.files)
        for path in paths:
            path = os.path.abspath(path)
            if os.path.isfile(path) and path not in existing:
                self.files.append(path)
                existing.add(path)
        self.refresh_preview()

    def remove_selected(self):
        selected = set(self.tree.selection())
        if not selected:
            return
        keep = []
        for idx, path in enumerate(self.files):
            if str(idx) not in selected:
                keep.append(path)
        self.files = keep
        self.refresh_preview()

    def clear_files(self):
        self.files.clear()
        self.refresh_preview()

    def move_to_top(self):
        indexes = sorted(int(i) for i in self.tree.selection())
        if not indexes:
            return
        selected = [self.files[i] for i in indexes]
        selected_set = set(indexes)
        rest = [path for i, path in enumerate(self.files) if i not in selected_set]
        self.files = selected + rest
        self.refresh_preview()
        for i in range(len(selected)):
            self.tree.selection_add(str(i))

    def move_up(self):
        indexes = sorted(int(i) for i in self.tree.selection())
        if not indexes or indexes[0] == 0:
            return
        for i in indexes:
            self.files[i - 1], self.files[i] = self.files[i], self.files[i - 1]
        self.refresh_preview()
        for i in indexes:
            self.tree.selection_add(str(i - 1))

    def move_down(self):
        indexes = sorted((int(i) for i in self.tree.selection()), reverse=True)
        if not indexes or indexes[0] == len(self.files) - 1:
            return
        for i in indexes:
            self.files[i + 1], self.files[i] = self.files[i], self.files[i + 1]
        self.refresh_preview()
        for i in indexes:
            self.tree.selection_add(str(i + 1))

    def move_to_bottom(self):
        indexes = sorted(int(i) for i in self.tree.selection())
        if not indexes:
            return
        selected = [self.files[i] for i in indexes]
        selected_set = set(indexes)
        rest = [path for i, path in enumerate(self.files) if i not in selected_set]
        self.files = rest + selected
        self.refresh_preview()
        start = len(rest)
        for i in range(start, start + len(selected)):
            self.tree.selection_add(str(i))

    def refresh_preview(self):
        if not hasattr(self, "tree"):
            return
        # 取消可能待执行的刷新任务，避免重复刷新
        if self._refresh_job is not None:
            try:
                self._root.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        # 模式只取一次，避免在循环中反复调用 notebook.select()
        mode = self.notebook.tab(self.notebook.select(), "text") if hasattr(self, "notebook") else "整体"
        entries = []
        for idx, path in enumerate(self.files):
            entries.append((path, os.path.basename(path), self.make_new_name(path, idx, mode)))
        pending_old = {
            os.path.abspath(p) for p, original, new_name in entries if new_name != original
        }
        self.previews = []
        existing = self.tree.get_children()
        if len(existing) == len(entries):
            # 行数未变：就地更新各行列值，避免整体删除重建（文件多时卡顿主因）
            for iid, (path, original, new_name) in zip(existing, entries):
                status = self._preview_status(path, new_name, pending_old)
                self.previews.append((path, new_name, status))
                self.tree.item(iid, values=(original, new_name, status))
        else:
            for item in existing:
                self.tree.delete(item)
            for idx, (path, original, new_name) in enumerate(entries):
                status = self._preview_status(path, new_name, pending_old)
                self.previews.append((path, new_name, status))
                self.tree.insert("", "end", iid=str(idx), values=(original, new_name, status))
        self._resize_list_columns()

    def _schedule_refresh(self):
        """合并连续的快速变更（如长按 Spinbox 箭头），延迟一次性刷新，消除卡顿"""
        if not hasattr(self, "tree"):
            return
        try:
            if self._root is not None and self._root.winfo_exists():
                if self._refresh_job is not None:
                    self._root.after_cancel(self._refresh_job)
                self._refresh_job = self._root.after(120, self.refresh_preview)
        except Exception:
            self.refresh_preview()

    def make_new_name(self, path, index, mode=None):
        if mode is None:
            mode = self.notebook.tab(self.notebook.select(), "text") if hasattr(self, "notebook") else "整体"
        p = Path(path)
        stem, suffix = p.stem, p.suffix
        if mode == "整体":
            return self._make_overall_name(index, stem, suffix)
        if mode == "替换":
            old = self.replace_from.get()
            new = self.replace_to.get()
            new_stem = stem.replace(old, new) if old else stem
            return new_stem + suffix
        return self._make_add_delete_name(stem, suffix)

    def _make_overall_name(self, index, original_stem, original_suffix):
        pattern = self._get_overall_pattern() or "#"
        serial = self._make_serial(index)
        if "*" in pattern or "#" in pattern:
            new_stem = pattern.replace("*", original_stem).replace("#", serial)
        else:
            new_stem = pattern + serial
        if self.overall_keep_ext.get():
            ext = original_suffix
        else:
            ext = self.overall_ext_text.get().strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
        new_stem, ext = self._apply_case_convert(new_stem, ext)
        return new_stem + ext

    def _make_serial(self, index):
        start = self._safe_int(self.overall_start, 1)
        step = self._safe_int(self.overall_step, 1)
        number = start + index * step
        if self.overall_alpha.get():
            return self._number_to_alpha(max(1, number))
        if self.overall_zero_pad.get():
            return str(number).zfill(max(1, self._safe_int(self.overall_digits, 3)))
        return str(number)

    def _apply_case_convert(self, stem, suffix):
        mode = self.case_convert.get()
        if mode == "改变文件名为小写":
            return stem.lower(), suffix
        if mode == "改变扩展名为小写":
            return stem, suffix.lower()
        if mode == "改变文件名和扩展名为小写":
            return stem.lower(), suffix.lower()
        if mode == "改变文件名为大写":
            return stem.upper(), suffix
        if mode == "改变扩展名为大写":
            return stem, suffix.upper()
        if mode == "改变文件名和扩展名为大写":
            return stem.upper(), suffix.upper()
        return stem, suffix

    def _make_add_delete_name(self, stem, suffix):
        new_stem = stem
        new_suffix = suffix
        delete_text = self.delete_text.get()
        if delete_text:
            new_stem = new_stem.replace(delete_text, "")
        if self.delete_name_enabled.get():
            offset = max(1, self._safe_int(self.delete_start, 1)) - 1
            count = max(0, self._safe_int(self.delete_count, 1))
            find_pos = self._find_char_pos(new_stem)
            if self.find_pos_enabled.get() and self.find_pos_text.get():
                if find_pos is None:
                    count = 0
                else:
                    start = find_pos + offset
            else:
                start = offset
            if count > 0:
                new_stem = new_stem[:start] + new_stem[start + count:]
                if start == 0:
                    new_stem = new_stem.lstrip()
        if self.delete_ext_enabled.get() and new_suffix:
            count = max(0, self._safe_int(self.delete_ext_count, 1))
            ext_body = new_suffix[1:]
            new_suffix = "." + ext_body[count:] if count < len(ext_body) else ""
        if self.add_prefix_enabled.get():
            new_stem = self.add_prefix.get() + new_stem
        if self.add_suffix_enabled.get():
            new_stem = new_stem + self.add_suffix.get()
        if self.add_at_pos_enabled.get():
            offset = max(1, self._safe_int(self.add_pos, 1)) - 1
            find_pos = self._find_char_pos(new_stem)
            if self.find_pos_enabled.get() and self.find_pos_text.get():
                pos = None if find_pos is None else find_pos + offset
            else:
                pos = offset
            if pos is not None:
                pos = min(pos, len(new_stem))
                new_stem = new_stem[:pos] + self.add_at_pos.get() + new_stem[pos:]
        if self.add_before_ext_enabled.get():
            new_stem = new_stem + self.add_before_ext.get()
        return new_stem + new_suffix

    def _find_char_pos(self, stem):
        if not self.find_pos_enabled.get():
            return None
        find_text = self.find_pos_text.get()
        if not find_text:
            return None
        pos = stem.find(find_text)
        return pos if pos >= 0 else None

    def _preview_status(self, old_path, new_name, pending_old=frozenset()):
        if not new_name or any(ch in new_name for ch in '<>:"/\\|?*'):
            return "名称非法"
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.abspath(old_path) == os.path.abspath(new_path):
            return "不变"
        if os.path.exists(new_path):
            abs_new = os.path.abspath(new_path)
            if abs_new in pending_old:
                pass
            elif os.name == "nt" and abs_new.lower() == os.path.abspath(old_path).lower():
                pass
            else:
                return "目标已存在"
        return "就绪"

    @staticmethod
    def _safe_int(var, default):
        try:
            return int(var.get())
        except Exception:
            return default

    @staticmethod
    def _number_to_alpha(n):
        letters = string.ascii_uppercase
        result = ""
        while n > 0:
            n, rem = divmod(n - 1, 26)
            result = letters[rem] + result
        return result or "A"

    def rename_files(self):
        self.refresh_preview()
        if not self.previews:
            messagebox.showwarning("提示", "请先添加要重命名的文件。")
            return
        if not messagebox.askyesno("确认", "确定要按预览结果重命名这些文件吗？"):
            return

        conflict_mode = self.overall_conflict.get()
        messages = []
        failed = 0
        used_targets = set()

        pending_old = {
            os.path.abspath(p)
            for p, new_name, status in self.previews
            if status != "名称非法" and os.path.basename(p) != new_name
        }

        plan = []
        for old_path, new_name, status in self.previews:
            if status == "名称非法":
                failed += 1
                messages.append(f"跳过：{os.path.basename(old_path)}，名称非法")
                continue
            target = os.path.join(os.path.dirname(old_path), new_name)
            abs_target = os.path.abspath(target)
            abs_old = os.path.abspath(old_path)
            if abs_target == abs_old or (os.name == "nt" and abs_target.lower() == abs_old.lower()):
                plan.append((old_path, target))
                used_targets.add(abs_target)
                continue
            real_conflict = (abs_target in used_targets) or (
                os.path.exists(target) and abs_target not in pending_old
            )
            if real_conflict:
                if conflict_mode == "跳过冲突文件":
                    failed += 1
                    messages.append(f"跳过：{new_name} 已存在")
                    continue
                if conflict_mode == "自动追加序号":
                    target = self._deduplicate_path(target, used_targets)
                    abs_target = os.path.abspath(target)
            used_targets.add(abs_target)
            plan.append((old_path, target))

        chain_conflict = any(
            os.path.abspath(target) in pending_old for _, target in plan if target is not None
        )

        success = 0
        final_map = {}

        def _do_rename(src, dst):
            if conflict_mode == "覆盖目标文件":
                os.replace(src, dst)
            else:
                os.rename(src, dst)

        if chain_conflict:
            temp_items = []
            for i, (old_path, target) in enumerate(plan):
                if target is None:
                    continue
                tmp = os.path.join(
                    os.path.dirname(old_path),
                    f"__rename_tmp_{i:04d}_{uuid.uuid4().hex[:8]}{Path(old_path).suffix}",
                )
                try:
                    os.rename(old_path, tmp)
                    temp_items.append((old_path, tmp, target))
                except Exception as exc:
                    failed += 1
                    messages.append(f"失败：{os.path.basename(old_path)}：{exc}")
            for old_path, tmp, target in temp_items:
                try:
                    _do_rename(tmp, target)
                    success += 1
                    final_map[os.path.abspath(old_path)] = os.path.abspath(target)
                except Exception as exc:
                    failed += 1
                    messages.append(
                        f"失败：{os.path.basename(old_path)} -> {os.path.basename(target)}：{exc}"
                    )
        else:
            for old_path, target in plan:
                if target is None:
                    continue
                try:
                    _do_rename(old_path, target)
                    success += 1
                    final_map[os.path.abspath(old_path)] = os.path.abspath(target)
                except Exception as exc:
                    failed += 1
                    messages.append(
                        f"失败：{os.path.basename(old_path)} -> {os.path.basename(target)}：{exc}"
                    )

        if self.clear_after_rename.get():
            self.files.clear()
        else:
            self.files = [
                final_map.get(os.path.abspath(p), p)
                for p in self.files
                if os.path.exists(final_map.get(os.path.abspath(p), p))
            ]
        self.refresh_preview()
        summary = f"完成：成功 {success} 个，失败 {failed} 个。"
        if messages:
            summary += "\n\n" + "\n".join(messages[:12])
            if len(messages) > 12:
                summary += f"\n……另有 {len(messages) - 12} 条未显示。"
        messagebox.showinfo("重命名结果", summary)
        self._log(summary)

    def _deduplicate_path(self, target, used_targets):
        p = Path(target)
        idx = 1
        while True:
            candidate = str(p.with_name(f"{p.stem}_{idx}{p.suffix}"))
            if candidate not in used_targets and not os.path.exists(candidate):
                return candidate
            idx += 1

    def show_help(self):
        messagebox.showinfo(
            "帮助",
            "使用方法：\n"
            "1. 可从 Windows 资源管理器多选文件，直接拖到右侧文件列表。\n"
            "2. 也可以点击“添加”或“添加文件夹”导入文件。\n"
            "3. 每个文件占一行，原文件名列只显示文件名。\n"
            "4. 在左侧选择“整体 / 替换 / 添加删除”设置规则。\n"
            "5. 右侧预览列会实时显示处理后的新文件名。\n"
            "6. 确认无误后点击“开始重命名”。\n"
            "7. “添加/删除”页可勾选“查找字符位置”：填入某字符后，\n"
            "   “从第几个字符开始插入”和“删除字符”都会以文件名中\n"
            "   第一次出现该字符的位置为起点（数字为相对偏移，1 表示\n"
            "   从该字符本身开始）；文件名中未找到该字符时跳过该操作。\n"
            "8. 新旧文件名互相重叠（如“第一弹”改成“第二弹”）时，\n"
            "   程序会自动采用两阶段改名，不会多出 _1 之类的后缀。\n\n"
            "窗口支持拖拽调整大小；右侧文件列表会随窗口放大自动扩展。\n"
            "如当前环境无法拖拽，请先安装 tkinterdnd2：pip install tkinterdnd2。",
        )

    def _log(self, message: str):
        """记录日志：优先使用主程序日志接口，否则打印"""
        if not self.is_standalone and self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

    def get_frame(self) -> ttk.Frame:
        """获取选项卡主框架"""
        return self.frame


# ========== 独立运行入口 ==========
if __name__ == "__main__":
    root = tk.Tk()
    root.title("文件名批量修改 - 独立版")
    root.geometry("980x620")
    root.minsize(820, 480)
    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    tab = BatchRenameTab(notebook, controller=None)
    notebook.add(tab.get_frame(), text="文件名批量修改")
    root.mainloop()
