#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 文件比对选项卡 - 对比原视频文件夹和已转码文件夹

"""
文件比对工具 - 检查转码完成情况
功能：
1. 对比A文件夹（原视频）和B文件夹（已转码）的文件名
2. 完全匹配：标记为已完成
3. 前缀匹配+数字后缀（~1, -1等）：标记为已完成但提示不一致
4. 未匹配文件单独显示
5. 一键在资源管理器中选中已转码/未转码文件
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import os
import re
import json
import subprocess
import threading
import queue
import tempfile
from typing import Optional, Dict, List, Tuple, Set

# 配置管理器导入（与其他tab使用相同的单例）
try:
    from file_organize_tab import ConfigManager
except ImportError:
    class ConfigManager:
        """统一配置管理器 - 单例模式（兼容版）"""
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


class FileCompareTab:
    """文件比对选项卡"""

    CONFIG_SECTION = "file_compare"

    # 视频扩展名
    VIDEO_EXTENSIONS: Set[str] = {'.avi', '.mp4', '.ts', '.rmvb', '.rm', '.mkv', '.mov', '.wmv', '.flv', '.m4v', '.mpg', '.mpeg', '.m2ts'}

    def __init__(self, parent_notebook, controller):
        self.controller = controller
        self.frame = ttk.Frame(parent_notebook)

        # 数据存储
        self.compare_results: List[Dict] = []
        self.matched_files: List[Dict] = []
        self.unmatched_files: List[Path] = []

        # 文件夹路径
        self.source_folder: Optional[Path] = None
        self.target_folder: Optional[Path] = None

        # 线程控制
        self.is_running = False
        self.operation_thread = None
        self.progress_queue = queue.Queue()

        # 获取配置管理器
        if controller and hasattr(controller, 'config_manager'):
            self.config_manager = controller.config_manager
        else:
            self.config_manager = ConfigManager()

        self.create_ui()
        self.load_config()

        # 注册到notebook
        parent_notebook.add(self.frame, text='文件比对')

        # 启动进度检查
        self.check_progress()

    def create_ui(self):
        """创建界面"""
        # 1. 功能说明
        info_frame = ttk.LabelFrame(self.frame, text="功能说明")
        info_frame.pack(fill=tk.X, padx=10, pady=10)

        info_text = (
            "本功能用于对比原视频文件夹和已转码文件夹，检查转码完成情况。\n\n"
            "对比规则：\n"
            "• 完全匹配：A文件夹中的 \"video.mp4\" 在B文件夹中存在同名文件，标记为已完成\n"
            "• 近似匹配：文件名前缀相同，后跟~1、-1等数字后缀（如 \"video~1.mp4\"），标记为已完成但提示文件名不一致\n"
            "• 未匹配：A文件夹中的文件在B文件夹中找不到对应文件，标记为待转码"
        )
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=1100).pack(padx=10, pady=10, fill=tk.X)

        # 2. 文件夹设置
        folder_frame = ttk.LabelFrame(self.frame, text="文件夹设置")
        folder_frame.pack(fill=tk.X, padx=10, pady=10)

        # 原视频文件夹（A）
        source_row = ttk.Frame(folder_frame)
        source_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(source_row, text="原视频文件夹(A):", width=15).pack(side=tk.LEFT)
        self.source_var = tk.StringVar()
        ttk.Entry(source_row, textvariable=self.source_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(source_row, text="浏览...", command=self.browse_source_folder).pack(side=tk.RIGHT)

        # 已转码文件夹（B）
        target_row = ttk.Frame(folder_frame)
        target_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(target_row, text="已转码文件夹(B):", width=15).pack(side=tk.LEFT)
        self.target_var = tk.StringVar()
        ttk.Entry(target_row, textvariable=self.target_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(target_row, text="浏览...", command=self.browse_target_folder).pack(side=tk.RIGHT)

        # 3. 操作按钮
        button_frame = ttk.Frame(self.frame)
        button_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(button_frame, text="保存配置", command=self.save_settings, width=15).pack(side=tk.LEFT, padx=5)
        self.compare_btn = ttk.Button(button_frame, text="开始对比", command=self.start_compare, width=15)
        self.compare_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(button_frame, text="停止", command=self.stop_compare, width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        ttk.Separator(button_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(button_frame, text="一键选择已转码文件", command=self.select_matched_files, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="一键选择未转码文件", command=self.select_unmatched_files, width=20).pack(side=tk.LEFT, padx=5)

        # 4. 进度条
        progress_frame = ttk.LabelFrame(self.frame, text="操作进度")
        progress_frame.pack(fill=tk.X, padx=10, pady=10)

        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100, 
                       mode='determinate').pack(fill=tk.X, padx=10, pady=5)

        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack()

        self.stats_label = ttk.Label(progress_frame, 
                                    text="A文件夹: 0个文件 | B文件夹: 0个文件 | 已匹配: 0 | 文件名不一致: 0 | 未匹配: 0",
                                    foreground="blue")
        self.stats_label.pack()

        # 5. 结果展示 - 使用Notebook分割
        result_notebook = ttk.Notebook(self.frame)
        result_notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 1: 匹配结果（包括完全匹配和近似匹配）
        matched_frame = ttk.Frame(result_notebook)
        result_notebook.add(matched_frame, text="匹配结果")

        # Tab 2: 未匹配文件
        unmatched_frame = ttk.Frame(result_notebook)
        result_notebook.add(unmatched_frame, text="未转码文件")

        # 匹配结果表格
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

        # 设置标签颜色
        self.matched_tree.tag_configure('perfect', foreground='green')
        self.matched_tree.tag_configure('similar', foreground='orange')

        # 未匹配结果表格
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

        # 未匹配文件设置红色标签
        self.unmatched_tree.tag_configure('unmatched', foreground='red')

        # 6. 清空按钮
        ttk.Button(self.frame, text="清空对比结果", command=self.clear_results).pack(anchor=tk.E, padx=10, pady=5)

    def get_frame(self):
        """获取选项卡框架"""
        return self.frame

    def log(self, message: str):
        """通过controller记录日志到日志选项卡"""
        if self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

    def browse_source_folder(self):
        """浏览原视频文件夹"""
        directory = filedialog.askdirectory(title="选择原视频文件夹(A)")
        if directory:
            self.source_var.set(directory)
            self.source_folder = Path(directory)

    def browse_target_folder(self):
        """浏览已转码文件夹"""
        directory = filedialog.askdirectory(title="选择已转码文件夹(B)")
        if directory:
            self.target_var.set(directory)
            self.target_folder = Path(directory)

    def load_config(self):
        """加载配置"""
        section_config = self.config_manager.get_section(self.CONFIG_SECTION, {})

        source_path = section_config.get("source_folder", "")
        target_path = section_config.get("target_folder", "")

        self.source_var.set(source_path)
        self.target_var.set(target_path)

        if source_path:
            self.source_folder = Path(source_path)
        if target_path:
            self.target_folder = Path(target_path)

    def save_settings(self):
        """保存设置"""
        config = {
            "source_folder": self.source_var.get(),
            "target_folder": self.target_var.get()
        }
        self.config_manager.set_section(self.CONFIG_SECTION, config, auto_save=True)
        self.log("[文件比对] 配置已保存")
        messagebox.showinfo("成功", "配置已保存")

    def start_compare(self):
        """开始对比"""
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

        # 清空之前的结果
        self.clear_results()

        self.compare_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.is_running = True

        self.operation_thread = threading.Thread(
            target=self._compare_thread,
            daemon=True
        )
        self.operation_thread.start()

    def stop_compare(self):
        """停止对比"""
        self.is_running = False
        self.log("[文件比对] 正在停止...")
        self.stop_btn.config(state=tk.DISABLED)

    def _compare_thread(self):
        """后台线程执行对比"""
        try:
            self.log("=" * 60)
            self.log("[文件比对] 开始对比")
            self.log(f"原视频文件夹(A): {self.source_folder}")
            self.log(f"已转码文件夹(B): {self.target_folder}")
            self.log("=" * 60)

            # 1. 收集A文件夹中的视频文件
            self.update_progress(10, "正在扫描原视频文件夹...")
            source_files = self._collect_video_files(self.source_folder)
            source_count = len(source_files)

            if not self.is_running:
                return

            self.log(f"[文件比对] A文件夹发现 {source_count} 个视频文件")

            # 2. 收集B文件夹中的视频文件
            self.update_progress(30, "正在扫描已转码文件夹...")
            target_files = self._collect_video_files(self.target_folder)
            target_count = len(target_files)

            if not self.is_running:
                return

            self.log(f"[文件比对] B文件夹发现 {target_count} 个视频文件")

            self.update_progress(50, f"扫描完成 A:{source_count} B:{target_count}，开始对比...")

            # 3. 建立B文件夹的索引（文件名->路径映射）
            target_index = {}  # 文件名（无扩展名）-> 文件路径
            target_names = {}  # 用于近似匹配

            for tf in target_files:
                # 完整文件名索引
                target_index[tf.name.lower()] = tf
                # 无扩展名索引（用于完全匹配）
                stem_lower = tf.stem.lower()
                if stem_lower not in target_index:
                    target_index[stem_lower] = tf
                target_names[stem_lower] = tf

            # 4. 逐个对比A文件夹的文件
            perfect_match = 0
            similar_match = 0
            unmatched = 0
            total = len(source_files)

            for idx, source_file in enumerate(source_files, 1):
                if not self.is_running:
                    break

                progress = 50 + int((idx / total) * 50)
                self.update_progress(progress, f"正在对比: {source_file.name[:40]}...")

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

                # 更新UI（每10个文件更新一次，避免卡顿）
                if idx % 10 == 0 or idx == total:
                    self.update_stats(source_count, target_count, perfect_match, similar_match, unmatched)

            self.update_progress(100, "对比完成")

            # 在主线程更新UI
            self.frame.after(0, self._update_result_ui)

            self.log("\n" + "=" * 60)
            self.log(f"[文件比对] 对比完成: 完全匹配 {perfect_match}, 文件名不一致 {similar_match}, 未匹配 {unmatched}")
            self.log("=" * 60)

            self.frame.after(0, lambda: self._compare_completed(perfect_match, similar_match, unmatched))

        except Exception as e:
            self.log(f"[文件比对] 错误: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.frame.after(0, lambda: messagebox.showerror("错误", f"对比过程出错: {e}"))
            self.frame.after(0, self._reset_ui)

    def _collect_video_files(self, folder: Path) -> List[Path]:
        """收集文件夹中的所有视频文件"""
        video_files = []
        try:
            for item in folder.iterdir():
                if item.is_file() and item.suffix.lower() in self.VIDEO_EXTENSIONS:
                    video_files.append(item)
        except Exception as e:
            self.log(f"[文件比对] 访问文件夹失败 {folder}: {e}")

        # 按文件名排序
        video_files.sort(key=lambda x: x.name.lower())
        return video_files

    def _compare_single_file(self, source_file: Path, target_index: Dict[str, Path], 
                            target_names: Dict[str, Path]) -> Dict:
        """
        对比单个文件

        Returns:
            {
                'source_file': Path,
                'target_file': Path or None,
                'status': 'perfect'/'similar'/'unmatched',
                'note': str,
                'source_name': str,
                'target_name': str or None
            }
        """
        source_name = source_file.name
        source_stem = source_file.stem.lower()
        source_ext = source_file.suffix.lower()

        result = {
            'source_file': source_file,
            'target_file': None,
            'status': 'unmatched',
            'note': '未找到匹配文件',
            'source_name': source_name,
            'target_name': None
        }

        # 1. 尝试完全匹配（包括扩展名）
        if source_name.lower() in target_index:
            matched_file = target_index[source_name.lower()]
            result['target_file'] = matched_file
            result['target_name'] = matched_file.name
            result['status'] = 'perfect'
            result['note'] = '文件名完全一致'
            return result

        # 2. 尝试完全匹配（不考虑扩展名，如 video.mp4 匹配 video.avi）
        if source_stem in target_names:
            matched_file = target_names[source_stem]
            result['target_file'] = matched_file
            result['target_name'] = matched_file.name
            result['status'] = 'perfect'
            result['note'] = f'文件名相同（格式不同: {matched_file.suffix}）'
            return result

        # 3. 尝试近似匹配（前缀相同，后跟~1, -1等数字后缀）
        # 匹配模式：文件名~数字.扩展名 或 文件名-数字.扩展名 或 文件名_数字.扩展名
        similar_pattern = re.compile(r'^' + re.escape(source_stem) + r'[\s\-_~]*(\d+)$', re.IGNORECASE)

        for target_stem, target_file in target_names.items():
            if similar_pattern.match(target_stem):
                result['target_file'] = target_file
                result['target_name'] = target_file.name
                result['status'] = 'similar'
                result['note'] = f'文件名不一致（找到: {target_file.name}）'
                return result

        return result

    def _update_result_ui(self):
        """在主线程更新结果UI"""
        # 清空现有内容
        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)
        for item in self.unmatched_tree.get_children():
            self.unmatched_tree.delete(item)

        # 填充匹配结果
        for result in self.matched_files:
            if result['status'] == 'perfect':
                tags = ('perfect',)
                status_text = "✓ 已完成"
            else:
                tags = ('similar',)
                status_text = "⚠ 已完成"

            self.matched_tree.insert('', tk.END, values=(
                result['source_name'],
                result['target_name'] if result['target_name'] else '-',
                status_text,
                result['note']
            ), tags=tags)

        # 填充未匹配结果
        for source_file in self.unmatched_files:
            self.unmatched_tree.insert('', tk.END, values=(
                source_file.name,
                str(source_file)
            ), tags=('unmatched',))

    def update_progress(self, value: int, message: str):
        """更新进度（通过队列）"""
        self.progress_queue.put(('progress', value, message))

    def update_stats(self, source_count: int, target_count: int, 
                    perfect: int, similar: int, unmatched: int):
        """更新统计信息"""
        self.progress_queue.put(('stats', source_count, target_count, perfect, similar, unmatched))

    def check_progress(self):
        """检查进度队列"""
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == 'progress':
                    _, value, message = msg
                    self.progress_var.set(value)
                    self.progress_label.config(text=message)
                elif msg_type == 'stats':
                    _, source_count, target_count, perfect, similar, unmatched = msg
                    self.stats_label.config(text=
                        f"A文件夹: {source_count}个文件 | B文件夹: {target_count}个文件 | "
                        f"已匹配: {perfect} | 文件名不一致: {similar} | 未匹配: {unmatched}"
                    )
        except queue.Empty:
            pass
        self.frame.after(100, self.check_progress)

    def _compare_completed(self, perfect: int, similar: int, unmatched: int):
        """对比完成回调"""
        self.compare_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False

        msg = f"对比完成！\n\n完全匹配: {perfect}\n文件名不一致: {similar}\n未匹配(待转码): {unmatched}"
        messagebox.showinfo("完成", msg)

    def _reset_ui(self):
        """重置UI状态"""
        self.compare_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False

    def clear_results(self):
        """清空对比结果"""
        self.compare_results.clear()
        self.matched_files.clear()
        self.unmatched_files.clear()

        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)
        for item in self.unmatched_tree.get_children():
            self.unmatched_tree.delete(item)

        self.progress_var.set(0)
        self.progress_label.config(text="准备就绪")
        self.stats_label.config(text="A文件夹: 0个文件 | B文件夹: 0个文件 | 已匹配: 0 | 文件名不一致: 0 | 未匹配: 0")
        self.log("[文件比对] 结果已清空")

    def _select_files_in_explorer(self, files: List[Path], folder_path: Path, description: str):
        """
        在Windows资源管理器中选中指定文件

        Args:
            files: 要选择的文件路径列表
            folder_path: 文件夹路径（用于提示）
            description: 文件描述（用于日志）
        """
        if not files:
            messagebox.showwarning("警告", f"没有{description}可选中")
            return

        # 过滤不存在的文件
        existing_files = [f for f in files if f.exists()]
        if not existing_files:
            messagebox.showwarning("警告", f"{description}文件不存在")
            return

        try:
            if len(existing_files) == 1:
                # 单个文件，直接使用explorer /select
                cmd = f'explorer /select,"{existing_files[0]}"'
                subprocess.Popen(cmd, shell=True)
                self.log(f"[文件比对] 已选中 {description}: {existing_files[0].name}")
            else:
                # 多个文件：打开文件夹，然后尝试用批处理逐个选中（前10个）
                subprocess.Popen(f'explorer "{folder_path}"', shell=True)

                # 创建临时批处理文件来逐个选中（前10个，避免过长）
                batch_lines = ['@echo off', 'echo 正在选中文件...', 'timeout /t 1 /nobreak >nul']
                for i, f in enumerate(existing_files[:10]):
                    batch_lines.append(f'explorer /select,"{f}"')
                    batch_lines.append('timeout /t 1 /nobreak >nul')

                with tempfile.NamedTemporaryFile(mode='w', suffix='.bat', delete=False, encoding='gbk') as f:
                    f.write('\n'.join(batch_lines))
                    batch_file = f.name

                subprocess.Popen(batch_file, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)

                # 10秒后删除临时文件
                self.frame.after(10000, lambda p=batch_file: os.unlink(p) if os.path.exists(p) else None)

                self.log(f"[文件比对] 已打开文件夹并尝试选中 {len(existing_files)} 个{description}: {folder_path}")

                if len(existing_files) > 10:
                    messagebox.showinfo("提示", 
                        f"由于Windows限制，已打开文件夹: {folder_path}\n"
                        f"并尝试选中前10个文件。其余文件请手动选择。\n"
                        f"总共 {len(existing_files)} 个文件")

        except Exception as e:
            self.log(f"[文件比对] 选中文件失败: {e}")
            # 失败时至少打开文件夹
            try:
                subprocess.Popen(f'explorer "{folder_path}"', shell=True)
                self.log(f"[文件比对] 已打开文件夹: {folder_path}")
            except:
                pass
            messagebox.showerror("错误", f"选中文件失败: {e}\n已尝试打开文件夹")

    def select_matched_files(self):
        """一键选择已转码文件（在资源管理器中选中）"""
        if not self.matched_files:
            messagebox.showwarning("警告", "没有已匹配的文件，请先执行对比操作")
            return

        # 收集所有匹配到的目标文件（B文件夹中的文件）
        target_files = []
        for result in self.matched_files:
            if result['target_file'] and result['target_file'].exists():
                target_files.append(result['target_file'])

        if not target_files:
            messagebox.showwarning("警告", "匹配的文件在B文件夹中不存在")
            return

        self._select_files_in_explorer(target_files, self.target_folder, "已转码文件")

    def select_unmatched_files(self):
        """一键选择未转码文件（在资源管理器中选中）"""
        if not self.unmatched_files:
            messagebox.showwarning("警告", "没有未匹配的文件，请先执行对比操作")
            return

        self._select_files_in_explorer(self.unmatched_files, self.source_folder, "未转码文件")