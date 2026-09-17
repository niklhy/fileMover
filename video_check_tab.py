#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频文件检查模块 - 检查文件夹名与视频文件名番号匹配情况

双模式运行：
1. 作为模块被主程序调用：from video_check_tab import VideoCheckTab
2. 独立运行：直接执行本文件，会创建一个简单的测试窗口
"""

import os
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# 导入番号匹配器（尝试导入，失败时给出提示）
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    PatternMatcher = None
    print("[VideoCheckTab] 警告: 无法导入 pattern_matcher，番号匹配功能将不可用")


class VideoCheckTab:
    """视频文件检查标签页"""

    # 配置文件名 - 直接指定为 app_config.json
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_config.json")

    # 支持的文件类型
    VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.m4v', '.mpeg', '.mpg'}
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif'}
    SUBTITLE_EXTENSIONS = {'.srt'}

    # 需要跳过的文件夹名标记
    SKIP_FOLDER_MARKERS = ['(个人)', '(不好看)']

    # 自带字幕的视频文件名后缀标记（不区分大小写检查）
    # 匹配规则：文件名（去扩展名）以这些后缀结尾
    BUILTIN_SUBTITLE_SUFFIXES = ['-C', '-c', '-UNC', 'UC']

    def __init__(self, parent_notebook, controller=None):
        """
        初始化标签页

        Args:
            parent_notebook: 父级Notebook容器
            controller: 主控制器对象（FileMoverGUI实例），可为None（独立运行时）
        """
        self.parent = parent_notebook
        self.controller = controller

        # 初始化番号匹配器
        if PatternMatcher is not None:
            self.matcher = PatternMatcher()
        else:
            self.matcher = None

        # 创建主框架
        self.frame = ttk.Frame(parent_notebook)

        # 存储当前配置
        self.config_key = "video_check"  # 配置键名前缀，避免与其他tab冲突

        # 排序状态跟踪
        self.sort_reverse = False
        self.sort_column = None

        self._init_ui()
        self._load_config()  # 启动时加载配置

    def _init_ui(self):
        """初始化用户界面"""
        # 主容器使用网格布局
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(4, weight=1)  # 结果表格区域可扩展

        # ========== 1. 功能介绍区域 ==========
        desc_frame = ttk.LabelFrame(self.frame, text="功能说明", padding=10)
        desc_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        desc_frame.columnconfigure(0, weight=1)

        description = (
            "本工具用于检查文件夹结构与视频文件命名的一致性。\n"
            "功能包括：\n"
            "1. 遍历指定文件夹（可选包含子文件夹）\n"
            "2. 检查文件夹名称与内部视频文件名中的番号/编号是否匹配\n"
            "3. 检测文件夹中是否缺失视频文件或封面图片\n"
            "4. 可选检查字幕文件（.srt），文件名含 -C/-c/-UNC/UC 后缀的视频视为自带字幕\n"
            "5. 结果支持点击列头排序，右键可复制路径\n"
            "6. 可设置排除文件夹，跳过不需要检查的目录"
        )

        self.desc_text = tk.Text(desc_frame, height=6, wrap=tk.WORD, bg='#f0f0f0', relief=tk.FLAT)
        self.desc_text.grid(row=0, column=0, sticky="ew")
        self.desc_text.insert('1.0', description)
        self.desc_text.config(state=tk.DISABLED)  # 设为只读

        # ========== 2. 配置区域 ==========
        config_frame = ttk.LabelFrame(self.frame, text="配置", padding=10)
        config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        config_frame.columnconfigure(1, weight=1)

        # 文件夹路径
        ttk.Label(config_frame, text="基础文件夹:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(config_frame, textvariable=self.folder_var)
        self.folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 5))

        browse_btn = ttk.Button(config_frame, text="浏览...", command=self._browse_folder)
        browse_btn.grid(row=0, column=2, sticky="e")

        # 提示标签
        tip_label = ttk.Label(config_frame, text="支持本地路径 (C:\\...) 或网络共享 (\\server\\share)",
                              foreground="gray", font=('Microsoft YaHei', 8))
        tip_label.grid(row=1, column=1, sticky="w", pady=(2, 0))

        # 包含子文件夹选项 + 检查字幕选项（同一行）
        options_frame = ttk.Frame(config_frame)
        options_frame.grid(row=2, column=1, sticky="w", pady=(5, 0))

        self.include_subfolders_var = tk.BooleanVar(value=False)
        self.subfolder_cb = ttk.Checkbutton(
            options_frame,
            text="包含子文件夹 (递归遍历所有子目录)",
            variable=self.include_subfolders_var
        )
        self.subfolder_cb.pack(side=tk.LEFT, padx=(0, 20))

        # 新增：检查字幕文件选项
        self.check_subtitle_var = tk.BooleanVar(value=False)
        self.subtitle_cb = ttk.Checkbutton(
            options_frame,
            text="检查是否有字幕文件",
            variable=self.check_subtitle_var
        )
        self.subtitle_cb.pack(side=tk.LEFT)

        # ========== 排除文件夹区域 (新增) ==========
        exclude_frame = ttk.LabelFrame(config_frame, text="排除文件夹 (这些文件夹及其子目录将被跳过)", padding=5)
        exclude_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        exclude_frame.columnconfigure(0, weight=1)

        # Listbox 显示排除的文件夹
        self.excluded_listbox = tk.Listbox(exclude_frame, height=4, selectmode=tk.EXTENDED)
        self.excluded_listbox.grid(row=0, column=0, sticky="ew", rowspan=3)

        # 滚动条
        exclude_sb = ttk.Scrollbar(exclude_frame, orient="vertical", command=self.excluded_listbox.yview)
        self.excluded_listbox.config(yscrollcommand=exclude_sb.set)
        exclude_sb.grid(row=0, column=1, sticky="ns", rowspan=3)

        # 按钮框架
        exclude_btn_frame = ttk.Frame(exclude_frame)
        exclude_btn_frame.grid(row=0, column=2, sticky="ns", padx=(5, 0))

        self.add_exclude_btn = ttk.Button(exclude_btn_frame, text="➕ 添加", command=self._add_excluded_folder, width=12)
        self.add_exclude_btn.pack(side=tk.TOP, pady=(0, 2))

        self.remove_exclude_btn = ttk.Button(exclude_btn_frame, text="➖ 移除", command=self._remove_excluded_folder, width=12)
        self.remove_exclude_btn.pack(side=tk.TOP, pady=2)

        self.clear_exclude_btn = ttk.Button(exclude_btn_frame, text="🗑️ 清空", command=self._clear_all_excluded, width=12)
        self.clear_exclude_btn.pack(side=tk.TOP, pady=2)

        # 提示标签
        exclude_tip = ttk.Label(exclude_frame, text="提示：设置新的基础文件夹时，排除列表将自动清空",
                                foreground="gray", font=('Microsoft YaHei', 8))
        exclude_tip.grid(row=3, column=0, sticky="w", pady=(2, 0))

        # ========== 3. 操作按钮区域 ==========
        btn_frame = ttk.Frame(self.frame)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)

        self.save_btn = ttk.Button(btn_frame, text="💾 保存配置", command=self._save_config)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.run_btn = ttk.Button(btn_frame, text="▶ 开始检查", command=self._run_check)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.export_btn = ttk.Button(btn_frame, text="📄 导出结果", command=self._export_results, state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT)

        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(btn_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        # ========== 4. 统计信息区域 ==========
        stats_frame = ttk.LabelFrame(self.frame, text="统计信息", padding=5)
        stats_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)

        self.stats_label = ttk.Label(stats_frame, text="就绪")
        self.stats_label.pack(side=tk.LEFT)

        # ========== 5. 结果表格区域 ==========
        result_frame = ttk.LabelFrame(self.frame, text="检查结果 (点击列头可排序)", padding=5)
        result_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(5, 10))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)

        # 创建表格
        columns = ("folder_path", "file_name", "folder_code", "file_code", "issue_type", "detail")
        self.tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show='headings',
            selectmode='browse'
        )

        # 设置列标题和宽度（禁用自动拉伸，实现固定宽度+滚动条）
        column_config = {
            "folder_path": ("文件夹路径", 300),
            "file_name": ("文件名", 200),
            "folder_code": ("文件夹番号", 100),
            "file_code": ("文件番号", 100),
            "issue_type": ("问题类型", 100),
            "detail": ("详细说明", 250)
        }

        for col, (text, width) in column_config.items():
            # stretch=False 禁用自动调整，保持固定宽度，超出显示滚动条
            self.tree.heading(col, text=text, command=lambda c=col: self._sort_tree(c))
            self.tree.column(col, width=width, minwidth=50, stretch=False, anchor='w')

        # 滚动条
        vsb = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(result_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # 右键菜单
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="复制路径", command=self._copy_path)
        self.context_menu.add_command(label="复制文件名", command=self._copy_filename)
        self.context_menu.add_command(label="复制文件夹番号", command=self._copy_folder_code)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="在资源管理器中打开", command=self._open_in_explorer)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # 存储检查结果
        self.check_results: List[Dict] = []

    def _browse_folder(self):
        """打开文件夹选择对话框"""
        folder = filedialog.askdirectory(title="选择要检查的文件夹")
        if folder:
            self.folder_var.set(folder)
            # 清空排除文件夹列表（设置新的基础文件夹时自动清空）
            self.excluded_listbox.delete(0, tk.END)
            print(f"[VideoCheckTab] 基础文件夹已更改，排除列表已自动清空")

    def _add_excluded_folder(self):
        """添加排除文件夹，默认打开基础文件夹路径"""
        # 获取基础文件夹路径作为默认初始目录
        initial_dir = self.folder_var.get().strip()

        # 如果基础文件夹路径有效且存在，则作为默认目录；否则使用默认路径
        if initial_dir and os.path.isdir(initial_dir):
            folder = filedialog.askdirectory(
                title="选择要排除的文件夹",
                initialdir=initial_dir
            )
        else:
            folder = filedialog.askdirectory(title="选择要排除的文件夹")

        if not folder:
            return

        folder_normalized = os.path.normpath(folder)

        # 检查是否已存在
        existing = list(self.excluded_listbox.get(0, tk.END))
        if folder_normalized in existing:
            messagebox.showinfo("提示", "该文件夹已在排除列表中")
            return

        # 检查是否与主文件夹冲突（只允许排除子文件夹，不能排除基础文件夹本身）
        base_folder = self.folder_var.get().strip()
        if base_folder:
            base_normalized = os.path.normpath(base_folder)
            # 仅禁止排除基础文件夹本身，允许排除其子目录
            if folder_normalized == base_normalized:
                messagebox.showwarning("警告", "不能排除基础文件夹本身")
                return

            # 可选提示：如果选择的文件夹不在基础文件夹内，给出确认提示
            if not folder_normalized.startswith(base_normalized + os.sep):
                if not messagebox.askyesno("确认",
                    f"选择的文件夹:\n{folder_normalized}\n\n不在基础文件夹内:\n{base_normalized}\n\n确定要添加吗？"):
                    return

        self.excluded_listbox.insert(tk.END, folder_normalized)

    def _remove_excluded_folder(self):
        """移除选中的排除文件夹"""
        selected = self.excluded_listbox.curselection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要移除的文件夹")
            return

        # 从后往前删除以避免索引变化问题
        for index in reversed(selected):
            self.excluded_listbox.delete(index)

    def _clear_all_excluded(self):
        """一键清空所有排除文件夹"""
        if self.excluded_listbox.size() == 0:
            return

        if messagebox.askyesno("确认", "确定要清空所有排除的文件夹吗？"):
            self.excluded_listbox.delete(0, tk.END)
            print(f"[VideoCheckTab] 排除列表已清空")

    def _load_config(self):
        """直接从 app_config.json 加载配置（不通过 controller）"""
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)

                # 使用特定的键名，避免与其他tab冲突
                self.folder_var.set(config.get(f"{self.config_key}_folder", ""))
                self.include_subfolders_var.set(config.get(f"{self.config_key}_include_subfolders", False))
                # 新增：加载字幕检查选项
                self.check_subtitle_var.set(config.get(f"{self.config_key}_check_subtitle", False))

                # 加载排除文件夹列表
                excluded_folders = config.get(f"{self.config_key}_excluded_folders", [])
                self.excluded_listbox.delete(0, tk.END)
                base_folder = config.get(f"{self.config_key}_folder", "")

                for path in excluded_folders:
                    # 只加载存在的路径
                    if os.path.exists(path):
                        # 仅跳过与基础文件夹完全相同的排除项（不能排除根目录本身）
                        if base_folder:
                            if os.path.normpath(path) == os.path.normpath(base_folder):
                                continue
                        self.excluded_listbox.insert(tk.END, path)

                print(f"[VideoCheckTab] 配置已从 {self.CONFIG_FILE} 加载")
        except Exception as e:
            print(f"[VideoCheckTab] 加载配置失败: {e}")

    def _save_config(self):
        """直接保存配置到 app_config.json（不通过 controller，避免写入 file_mover_config.json）"""
        try:
            config = {}

            # 如果文件已存在，先读取现有配置（保留其他tab的数据）
            if os.path.exists(self.CONFIG_FILE):
                try:
                    with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                except Exception as e:
                    print(f"[VideoCheckTab] 读取现有配置失败，将创建新文件: {e}")

            # 获取排除文件夹列表
            excluded_folders = list(self.excluded_listbox.get(0, tk.END))

            # 更新本tab的配置（使用独立键名，不覆盖其他tab配置）
            config.update({
                f"{self.config_key}_folder": self.folder_var.get(),
                f"{self.config_key}_include_subfolders": self.include_subfolders_var.get(),
                f"{self.config_key}_check_subtitle": self.check_subtitle_var.get(),
                f"{self.config_key}_excluded_folders": excluded_folders
            })

            # 写入 app_config.json
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            messagebox.showinfo("成功", f"配置已保存到 {self.CONFIG_FILE}\n排除文件夹数量: {len(excluded_folders)}")
            print(f"[VideoCheckTab] 配置已保存到 {self.CONFIG_FILE}，排除文件夹: {len(excluded_folders)} 个")

        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {e}")
            print(f"[VideoCheckTab] 保存配置失败: {e}")

    def _validate_path(self, path: str) -> bool:
        """验证路径是否有效（支持本地和网络路径）"""
        if not path:
            return False
        try:
            # 检查路径是否存在
            return os.path.exists(path)
        except Exception:
            return False

    def _get_all_folders(self, base_path: str, include_subfolders: bool) -> List[str]:
        """获取所有要检查的文件夹路径"""
        folders = []
        base_path = os.path.normpath(base_path)

        # 获取排除列表（标准化路径）- 添加防御性检查
        try:
            exclude_tuple = self.excluded_listbox.get(0, tk.END)
            if exclude_tuple is None:
                excluded_paths = []
            else:
                excluded_paths = [os.path.normpath(p) for p in exclude_tuple if p]
        except Exception as e:
            print(f"[VideoCheckTab] 获取排除列表失败: {e}")
            excluded_paths = []

        # 检查基础路径本身是否被排除
        for excl in excluded_paths:
            if base_path == excl or base_path.startswith(excl + os.sep):
                return []  # 基础路径被排除，返回空列表

        if not include_subfolders:
            # 仅检查当前文件夹
            if os.path.isdir(base_path):
                folders.append(base_path)
        else:
            # 递归遍历所有子文件夹
            try:
                for root, dirs, files in os.walk(base_path):
                    root_normalized = os.path.normpath(root)

                    # 检查当前路径是否在排除列表中
                    is_excluded = False
                    for excl in excluded_paths:
                        if root_normalized == excl or root_normalized.startswith(excl + os.sep):
                            is_excluded = True
                            break

                    if is_excluded:
                        # 从 dirs 中移除所有项目，阻止递归进入子目录
                        dirs[:] = []
                        continue

                    # 跳过隐藏文件夹和系统文件夹（保留原有逻辑）
                    dirs[:] = [d for d in dirs if d and not d.startswith('.') and not d.startswith('$')]
                    folders.append(root)
            except Exception as e:
                print(f"[VideoCheckTab] 遍历文件夹失败: {e}")

        return folders

    def _is_skip_folder(self, folder_name: str) -> bool:
        """检查文件夹名是否包含跳过标记"""
        for marker in self.SKIP_FOLDER_MARKERS:
            if marker in folder_name:
                return True
        return False

    def _has_builtin_subtitle(self, video_filename: str) -> bool:
        """
        检查视频文件名是否带有内置字幕标记

        规则：文件名（去扩展名）以 -C、-c、-UNC、UC 结尾（不区分大小写）
        例如: ABC-123-C.mp4, ABC-123-c.mp4, ABC-123-UNC.mp4, ABC-123UC.mp4

        Args:
            video_filename: 视频文件名（含扩展名）

        Returns:
            True 表示视为自带字幕，跳过字幕检查
        """
        name_no_ext = os.path.splitext(video_filename)[0]
        # 统一转为大写进行不区分大小写的后缀匹配
        upper_name = name_no_ext.upper()

        for suffix in self.BUILTIN_SUBTITLE_SUFFIXES:
            if upper_name.endswith(suffix.upper()):
                return True
        return False

    def _check_folder(self, folder_path: str) -> List[Dict]:
        """检查单个文件夹，返回问题列表

        新增逻辑：如果文件夹名中含有'(个人)'或'(不好看)'，则跳过检查。
        """
        issues = []
        folder_name = os.path.basename(folder_path)

        # 新增：跳过包含标记的文件夹
        if self._is_skip_folder(folder_name):
            print(f"[VideoCheckTab] 跳过标记文件夹: {folder_path}")
            return []

        # 新增：检查是否为分类文件夹（无任何文件，且至少有一个子文件夹）
        try:
            # 防御性检查：确保路径存在且是目录
            if not os.path.isdir(folder_path):
                return [{
                    "folder_path": folder_path,
                    "file_name": "",
                    "folder_code": "未识别",
                    "file_code": "",
                    "issue_type": "访问错误",
                    "detail": "路径不存在或不是文件夹"
                }]

            all_items = os.listdir(folder_path)

            # 防御性检查：确保 all_items 不为 None（某些系统可能返回 None）
            if all_items is None:
                all_items = []

            # 过滤隐藏项和系统项
            visible_items = [item for item in all_items
                           if not item.startswith('.') and not item.startswith('$')]

            # 统计实际文件和子文件夹数量
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

            # 分类文件夹判断：没有任何文件，且至少有一个子文件夹
            # 修复：使用 >= 1 而不是 > 1，支持单个子文件夹的分类文件夹
            if file_count == 0 and subdir_count >= 1:
                print(f"[VideoCheckTab] 跳过分类文件夹: {folder_path}")
                return []  # 视为分类文件夹，不检查，不报告缺失视频

        except Exception as e:
            # 捕获所有异常，包括 TypeError（NoneType 迭代错误）
            print(f"[VideoCheckTab] 分类检查失败 [{folder_path}]: {e}")
            # 继续原有逻辑，让后面的错误处理接管

        # 获取文件夹番号
        if self.matcher is not None:
            folder_code_info = self.matcher.extract_code(folder_name)
            folder_code = folder_code_info['full'] if folder_code_info else None
            folder_code_display = folder_code_info['canonical'] if folder_code_info else "未识别"
        else:
            folder_code = None
            folder_code_display = "未识别"

        # 获取文件夹内所有文件
        try:
            files = os.listdir(folder_path)
            # 防御性检查
            if files is None:
                files = []
        except PermissionError:
            return [{
                "folder_path": folder_path,
                "file_name": "",
                "folder_code": folder_code_display,
                "file_code": "",
                "issue_type": "访问错误",
                "detail": "无法访问该文件夹（权限不足）"
            }]
        except Exception as e:
            return [{
                "folder_path": folder_path,
                "file_name": "",
                "folder_code": folder_code_display,
                "file_code": "",
                "issue_type": "访问错误",
                "detail": f"读取文件夹失败: {str(e)}"
            }]

        # 分类文件
        video_files = []
        image_files = []
        subtitle_files = []

        for file in files:
            if file is None:
                continue  # 防御性检查
            if file.startswith('.'):  # 跳过隐藏文件
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in self.VIDEO_EXTENSIONS:
                video_files.append(file)
            elif ext in self.IMAGE_EXTENSIONS:
                image_files.append(file)
            elif ext in self.SUBTITLE_EXTENSIONS:
                subtitle_files.append(file)

        # 检查是否缺失视频文件（分类文件夹已在前面过滤）
        if not video_files:
            issues.append({
                "folder_path": folder_path,
                "file_name": "",
                "folder_code": folder_code_display,
                "file_code": "",
                "issue_type": "缺失视频",
                "detail": f"文件夹中未找到视频文件（支持格式: {', '.join(self.VIDEO_EXTENSIONS)}）"
            })

        # 检查是否缺失图片文件
        if not image_files:
            issues.append({
                "folder_path": folder_path,
                "file_name": "",
                "folder_code": folder_code_display,
                "file_code": "",
                "issue_type": "缺失图片",
                "detail": f"文件夹中未找到封面图片（支持格式: {', '.join(self.IMAGE_EXTENSIONS)}）"
            })

        # ========== 新增：检查字幕文件 ==========
        if self.check_subtitle_var.get():
            # 分离出"需要检查字幕"的视频（即不自带字幕的视频）
            videos_need_subtitle_check = []
            builtin_subtitle_videos = []

            for video_file in video_files:
                if self._has_builtin_subtitle(video_file):
                    builtin_subtitle_videos.append(video_file)
                else:
                    videos_need_subtitle_check.append(video_file)

            # 如果存在需要检查字幕的视频，但没有任何字幕文件，则报告缺失字幕
            if videos_need_subtitle_check and not subtitle_files:
                # 列出跳过检查的视频信息
                skipped_info = ""
                if builtin_subtitle_videos:
                    skipped_names = ", ".join(builtin_subtitle_videos)
                    skipped_info = f"；以下视频因文件名含字幕标记已跳过检查: {skipped_names}"

                issues.append({
                    "folder_path": folder_path,
                    "file_name": "",
                    "folder_code": folder_code_display,
                    "file_code": "",
                    "issue_type": "缺失字幕",
                    "detail": f"文件夹中有 {len(videos_need_subtitle_check)} 个视频需要字幕，但未找到字幕文件（.srt）{skipped_info}"
                })

        # 检查视频文件名与文件夹名匹配情况（逐个检查所有视频文件）
        for video_file in video_files:
            file_name_no_ext = os.path.splitext(video_file)[0]

            if self.matcher is not None:
                file_code_info = self.matcher.extract_code(file_name_no_ext)
            else:
                file_code_info = None

            if not file_code_info:
                # 视频文件名未能提取番号
                issues.append({
                    "folder_path": folder_path,
                    "file_name": video_file,
                    "folder_code": folder_code_display,
                    "file_code": "未识别",
                    "issue_type": "命名不规范",
                    "detail": "视频文件名未能识别出番号/编号"
                })
                continue

            file_code = file_code_info['full']
            file_code_display = file_code_info['canonical']

            # 如果文件夹没有番号，但文件有，视为不匹配问题（移除了短文件夹名跳过逻辑）
            if not folder_code:
                issues.append({
                    "folder_path": folder_path,
                    "file_name": video_file,
                    "folder_code": "未识别",
                    "file_code": file_code_display,
                    "issue_type": "命名不匹配",
                    "detail": f"文件夹名未识别番号，但视频文件识别为 {file_code_display}"
                })
                continue

            # 使用PatternMatcher进行匹配检查
            if self.matcher is not None:
                match_level = self.matcher.is_strict_match(file_code_info, folder_code_info)
            else:
                match_level = 0

            if match_level == 0:
                # 完全不匹配
                issues.append({
                    "folder_path": folder_path,
                    "file_name": video_file,
                    "folder_code": folder_code_display,
                    "file_code": file_code_display,
                    "issue_type": "番号不匹配",
                    "detail": f"文件夹番号 {folder_code_display} 与文件番号 {file_code_display} 不匹配"
                })
            elif match_level == 1:
                # 弱匹配（字母或数字部分匹配但不完全一致）
                issues.append({
                    "folder_path": folder_path,
                    "file_name": video_file,
                    "folder_code": folder_code_display,
                    "file_code": file_code_display,
                    "issue_type": "疑似不匹配",
                    "detail": f"文件夹番号 {folder_code_display} 与文件番号 {file_code_display} 部分匹配但不完全一致"
                })
            # match_level == 2 为强匹配，不记录问题

        return issues

    def _run_check(self):
        """执行检查操作"""
        base_folder = self.folder_var.get().strip()

        # 验证输入
        if not base_folder:
            messagebox.showwarning("提示", "请先选择要检查的文件夹")
            return

        if not self._validate_path(base_folder):
            messagebox.showerror("错误", f"路径不存在或无法访问:\n{base_folder}")
            return

        # 验证排除路径（防止手动输入错误）
        excluded_paths = list(self.excluded_listbox.get(0, tk.END))
        invalid_excluded = []
        for path in excluded_paths:
            if not os.path.exists(path):
                invalid_excluded.append(path)

        if invalid_excluded:
            if messagebox.askyesno("路径不存在", f"以下排除路径不存在，是否从列表中移除？\n\n" + "\n".join(invalid_excluded)):
                # 移除不存在的路径
                for path in invalid_excluded:
                    idx = list(self.excluded_listbox.get(0, tk.END)).index(path)
                    self.excluded_listbox.delete(idx)
            else:
                return

        # 清空之前的结果
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.check_results.clear()
        self.export_btn.config(state=tk.DISABLED)

        # 重置排序状态
        self.sort_column = None

        # 更新UI状态
        self.run_btn.config(state=tk.DISABLED, text="检查中...")
        self.save_btn.config(state=tk.DISABLED)
        self.stats_label.config(text="正在扫描文件夹...")
        self.frame.update()

        try:
            # 获取所有文件夹
            include_sub = self.include_subfolders_var.get()
            folders = self._get_all_folders(base_folder, include_sub)
            total = len(folders)

            if total == 0:
                if excluded_paths:
                    messagebox.showinfo("提示", "未找到需要检查的文件夹（所有文件夹均已被排除）")
                else:
                    messagebox.showinfo("提示", "未找到需要检查的文件夹")
                self.stats_label.config(text="就绪")
                return

            # 遍历检查
            all_issues = []
            for idx, folder in enumerate(folders, 1):
                # 更新进度
                progress = (idx / total) * 100
                self.progress_var.set(progress)
                self.stats_label.config(text=f"正在检查: {idx}/{total} - {os.path.basename(folder)}")
                self.frame.update()

                # 检查文件夹
                issues = self._check_folder(folder)
                all_issues.extend(issues)

            # 显示结果
            self.check_results = all_issues
            self._display_results(all_issues)

            # 更新统计
            issue_count = len(all_issues)
            folder_with_issues = len(set(issue['folder_path'] for issue in all_issues))
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
            # 恢复UI状态
            self.run_btn.config(state=tk.NORMAL, text="▶ 开始检查")
            self.save_btn.config(state=tk.NORMAL)
            self.progress_var.set(0)

    def _sort_tree(self, col: str):
        """点击列头时对表格进行排序"""
        # 获取所有行数据
        data = [(self.tree.set(child, col), child) for child in self.tree.get_children('')]

        # 尝试数字排序，失败则字符串排序
        try:
            # 检查是否都是数字
            float(data[0][0])
            data.sort(key=lambda x: float(x[0]), reverse=self.sort_reverse)
        except (ValueError, IndexError):
            # 字符串排序
            data.sort(key=lambda x: x[0].lower(), reverse=self.sort_reverse)

        # 重新排列行
        for index, (_, item) in enumerate(data):
            self.tree.move(item, '', index)

        # 切换排序方向供下次使用
        self.sort_reverse = not self.sort_reverse
        self.sort_column = col

        # 更新列头显示排序指示器
        for column in self.tree["columns"]:
            text = self.tree.heading(column, "text").replace(" ▲", "").replace(" ▼", "")
            if column == col:
                text += " ▼" if self.sort_reverse else " ▲"
            self.tree.heading(column, text=text)

    def _display_results(self, results: List[Dict]):
        """在表格中显示结果"""
        # 按问题类型排序（初始显示顺序）
        priority = {"番号不匹配": 1, "疑似不匹配": 2, "命名不规范": 3,
                   "缺失视频": 4, "缺失图片": 5, "缺失字幕": 6, "访问错误": 7}
        sorted_results = sorted(results, key=lambda x: priority.get(x['issue_type'], 99))

        # 定义标签颜色
        self.tree.tag_configure('mismatch', background='#ffcccc')  # 红色-不匹配
        self.tree.tag_configure('warning', background='#ffffcc')   # 黄色-警告
        self.tree.tag_configure('missing', background='#ccddff')   # 蓝色-缺失
        self.tree.tag_configure('error', background='#ffaaaa')     # 深红-错误

        for issue in sorted_results:
            # 根据问题类型选择标签
            tag = ''
            if issue['issue_type'] in ['番号不匹配']:
                tag = 'mismatch'
            elif issue['issue_type'] in ['疑似不匹配', '命名不规范']:
                tag = 'warning'
            elif issue['issue_type'] in ['缺失视频', '缺失图片', '缺失字幕']:
                tag = 'missing'
            elif issue['issue_type'] == '访问错误':
                tag = 'error'

            values = (
                issue['folder_path'],
                issue['file_name'],
                issue['folder_code'],
                issue['file_code'],
                issue['issue_type'],
                issue['detail']
            )
            self.tree.insert('', tk.END, values=values, tags=(tag,))

    def _export_results(self):
        """导出结果到CSV文件"""
        if not self.check_results:
            messagebox.showinfo("提示", "没有可导出的结果")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialfile=f"视频检查结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )

        if not file_path:
            return

        try:
            import csv
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                # 写入表头
                writer.writerow(['文件夹路径', '文件名', '文件夹番号', '文件番号', '问题类型', '详细说明'])
                # 写入数据
                for issue in self.check_results:
                    writer.writerow([
                        issue['folder_path'],
                        issue['file_name'],
                        issue['folder_code'],
                        issue['file_code'],
                        issue['issue_type'],
                        issue['detail']
                    ])

            messagebox.showinfo("成功", f"结果已导出到:\n{file_path}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败:\n{str(e)}")

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def _copy_path(self):
        """复制选中行的路径到剪贴板"""
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0])['values']
            path = values[0]  # folder_path列
            self.frame.clipboard_clear()
            self.frame.clipboard_append(path)
            self.frame.update()

    def _copy_filename(self):
        """复制选中行的文件名到剪贴板"""
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0])['values']
            filename = values[1]  # file_name列
            if filename:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(filename)
                self.frame.update()

    def _copy_folder_code(self):
        """复制选中行的文件夹番号到剪贴板"""
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0])['values']
            folder_code = values[2]  # folder_code列（索引2）
            if folder_code:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(folder_code)
                self.frame.update()

    def _open_in_explorer(self):
        """在资源管理器中打开选中路径"""
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0])['values']
            path = values[0]  # folder_path列
            if os.path.exists(path):
                os.startfile(path)
            else:
                messagebox.showwarning("警告", "路径不存在或无法访问")


# ==================== 独立运行入口 ====================
class SimpleVideoCheckApp:
    """独立运行时的简单主窗口（用于测试和单独使用）"""

    def __init__(self, root):
        self.root = root
        self.root.title("视频文件检查工具")
        self.root.geometry("1200x800")

        # 创建Notebook
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建VideoCheckTab实例（controller为None表示独立运行）
        self.video_tab = VideoCheckTab(self.notebook, controller=None)
        self.notebook.add(self.video_tab.frame, text="视频检查")

        # 状态栏
        self.status_bar = ttk.Label(root, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        print("[SimpleVideoCheckApp] 独立运行模式已启动")


def main():
    """独立运行入口"""
    root = tk.Tk()
    app = SimpleVideoCheckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()