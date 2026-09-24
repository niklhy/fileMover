#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 文件移动选项卡 - 支持独立运行（无日志面板）和嵌入主窗体两种模式

"""
文件移动工具 - 模块化/独立双模式
功能：本功能主要用于从同一父文件夹的不同子文件夹中，将视频文件移动到匹配的子文件夹。
1. 将视频文件移到匹配文件夹内（根据番号匹配）
2. 将子文件夹中的视频文件移到当前文件夹
3. 支持独立运行（控制台输出日志）或作为 Tab 嵌入主窗体

使用方式：
- 独立运行：python file_move_tab.py （日志输出到控制台）
- 嵌入主窗体：from file_move_tab import FileMoveTab; FileMoveTab(notebook, controller)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import List, Dict, Optional, Callable, Set
import shutil
import json
import os
import threading
import queue

# 导入通用配置路径（独立运行时使用软件的通用配置文件）
try:
    from config import get_config_path
except ImportError:
    def get_config_path() -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_mover_config.json")

# 导入番号匹配器
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    import re
    class PatternMatcher:
        def extract_code(self, text: str) -> Optional[Dict]:
            m = re.match(r'^([a-zA-Z]+-?\d+)', text)
            if m:
                code = m.group(1).replace('-', '').lower()
                return {'full': code, 'letters': ''.join(re.findall(r'[a-zA-Z]+', code)), 
                        'numbers': ''.join(re.findall(r'\d+', code))}
            return None
        
        def is_strict_match(self, file_code: Dict, folder_code: Dict) -> int:
            if not file_code or not folder_code:
                return 0
            if file_code['full'] == folder_code['full']:
                return 2
            if folder_code['full'] in file_code['full'] or file_code['full'] in folder_code['full']:
                return 2
            letters_match = (folder_code['letters'] in file_code['letters']) or \
                          (file_code['letters'] in folder_code['letters'])
            if file_code.get('numbers') and folder_code.get('numbers'):
                f_nums = file_code['numbers'].lstrip('0') or '0'
                fd_nums = folder_code['numbers'].lstrip('0') or '0'
                if f_nums == fd_nums:
                    if letters_match:
                        return 1
            return 0


class StandaloneController:
    """独立运行时的控制器 - 日志输出到控制台"""
    
    def __init__(self, config_file: Optional[str] = None):
        # 默认使用软件的通用配置文件 file_mover_config.json
        self.config_file = config_file if config_file else get_config_path()
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        default_config = {
            "file_move_tab": {
                "directory": "",
                "move_to_folder": True,
                "move_to_root": False,
                "include_subfolders": False,
                "dry_run": True,
                "move_all_files": False
            }
        }
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    default_config["file_move_tab"].update(loaded.get("file_move_tab", {}))
                    return default_config
        except Exception as e:
            print(f"加载配置失败: {e}")
        return default_config
    
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
        """独立模式下日志输出到控制台"""
        print(message)


class FileMoveOperations:
    """文件移动操作核心类"""
    
    VIDEO_EXTENSIONS: Set[str] = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.rmvb', '.rm', '.m4v'}
    
    def __init__(self, log_callback: Optional[Callable] = None, 
                 progress_callback: Optional[Callable] = None,
                 move_all_files: bool = False):
        self.matcher = PatternMatcher()
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.move_all_files = move_all_files
        self.record_unmatched: Optional[Callable] = None
        self.record_moved: Optional[Callable] = None
        self.record_error: Optional[Callable] = None
        self.stats = {
            'moved_to_folder': 0,
            'moved_to_root': 0,
            'errors': 0,
            'total_files': 0,
            'processed_files': 0
        }
        self.is_running = True
    
    def stop(self):
        self.is_running = False
    
    def log(self, message: str):
        if self.log_callback and self.is_running:
            self.log_callback(message)
    
    def update_progress(self, current: int, total: int, message: str = ""):
        if self.progress_callback and self.is_running and total > 0:
            progress = int((current / total) * 100)
            self.progress_callback(progress, message)
    
    def get_video_files(self, directory: Path) -> List[Path]:
        try:
            return [f for f in directory.iterdir() 
                   if f.is_file() and f.suffix.lower() in self.VIDEO_EXTENSIONS]
        except (PermissionError, OSError) as e:
            self.log(f"无法访问目录 {directory}: {e}")
            return []
    
    def get_files(self, directory: Path) -> List[Path]:
        """根据 move_all_files 设置获取文件列表"""
        try:
            if self.move_all_files:
                return [f for f in directory.iterdir() if f.is_file()]
            return self.get_video_files(directory)
        except (PermissionError, OSError) as e:
            self.log(f"无法访问目录 {directory}: {e}")
            return []
    
    def get_subfolders(self, directory: Path) -> List[Path]:
        try:
            return [f for f in directory.iterdir() if f.is_dir()]
        except (PermissionError, OSError) as e:
            self.log(f"无法读取子文件夹 {directory}: {e}")
            return []
    
    def get_descendant_files(self, directory: Path) -> List[Path]:
        """递归收集目录下所有后代文件（不包含目录本身的直接文件），并应用文件类型过滤"""
        try:
            files = []
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                if not self.move_all_files and path.suffix.lower() not in self.VIDEO_EXTENSIONS:
                    continue
                # 跳过当前目录本身的文件，只保留子文件夹中的文件
                if path.parent == directory:
                    continue
                files.append(path)
            return files
        except (PermissionError, OSError) as e:
            self.log(f"无法递归访问目录 {directory}: {e}")
            return []
    
    def is_match(self, file_name: str, folder_code: Dict) -> bool:
        file_code = self.matcher.extract_code(file_name)
        if not file_code or not folder_code:
            return False
        match_level = self.matcher.is_strict_match(file_code, folder_code)
        return match_level >= 1
    
    def move_file_to_folder(self, file_path: Path, target_folder: Path, dry_run: bool = False) -> bool:
        if not self.is_running:
            return False
        
        if not target_folder.exists():
            try:
                if not dry_run:
                    target_folder.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                self.log(f"无法创建文件夹 {target_folder}: {e}")
                if self.record_error:
                    self.record_error({'file': str(file_path), 'error': f"无法创建文件夹: {e}"})
                self.stats['errors'] += 1
                return False
        
        dst = target_folder / file_path.name
        
        counter = 1
        stem, suffix = dst.stem, dst.suffix
        while dst.exists():
            dst = target_folder / f"{stem}_{counter}{suffix}"
            counter += 1
        
        action = "[演示]" if dry_run else "[移动]"
        self.log(f"{action} {file_path.name} -> {target_folder.name}/")
        
        if not dry_run and self.is_running:
            try:
                shutil.move(str(file_path), str(dst))
                if self.record_moved:
                    self.record_moved({
                        'file': file_path.name,
                        'from': str(file_path.parent),
                        'to': str(dst)
                    })
                return True
            except Exception as e:
                self.log(f"移动失败 {file_path.name}: {e}")
                if self.record_error:
                    self.record_error({'file': str(file_path), 'error': str(e)})
                self.stats['errors'] += 1
                return False
        return True
    
    def move_to_matching_folders(self, root_dir: Path, include_subfolders: bool, dry_run: bool = False):
        self.log(f"\n=== 开始将视频文件移到匹配文件夹内 ===")
        self.log(f"根目录: {root_dir}")
        self.log(f"包含子文件夹: {'是' if include_subfolders else '否'}")
        self.log(f"模式: {'演示' if dry_run else '实际执行'}")
        
        if not self.is_running:
            return
        
        dirs_to_process = [root_dir]
        if include_subfolders:
            dirs_to_process.extend(d for d in root_dir.rglob("*") if d.is_dir())
        
        total_files = sum(len(self.get_files(d)) for d in dirs_to_process)
        self.stats['total_files'] = total_files
        processed = 0
        
        for current_dir in dirs_to_process:
            if not self.is_running:
                return
            
            files = self.get_files(current_dir)
            if not files:
                continue
            
            subfolders = self.get_subfolders(current_dir)
            if not subfolders:
                continue
            
            folder_patterns = []
            folder_pattern_list = []
            for folder in subfolders:
                pattern = self.matcher.extract_code(folder.name)
                if pattern:
                    folder_patterns.append((folder, pattern))
                    folder_pattern_list.append(f"{folder.name} ({pattern['full']})")
            
            if not folder_patterns:
                continue
            
            rel_path = current_dir.relative_to(root_dir) if current_dir != root_dir else "."
            self.log(f"\n处理目录: {rel_path}")
            
            for file_path in files:
                if not self.is_running:
                    return
                
                processed += 1
                self.update_progress(processed, total_files, f"处理: {file_path.name}")
                
                matched = False
                for folder, pattern in folder_patterns:
                    if self.is_match(file_path.name, pattern):
                        if self.move_file_to_folder(file_path, folder, dry_run):
                            self.stats['moved_to_folder'] += 1
                        matched = True
                        break
                
                if not matched:
                    self.log(f"  [未匹配] {file_path.name}")
                    if self.record_unmatched:
                        self.record_unmatched({
                            'file': file_path.name,
                            'directory': str(current_dir),
                            'patterns': folder_pattern_list
                        })
    
    def move_from_subfolders_to_root(self, root_dir: Path, include_subfolders: bool, dry_run: bool = False):
        self.log(f"\n=== 开始将子文件夹中的视频文件移到当前文件夹 ===")
        self.log(f"根目录: {root_dir}")
        
        if not self.is_running:
            return
        
        file_mappings = []
        if include_subfolders:
            # 递归模式：将根目录下所有层级的子文件都移到根目录
            for file_path in self.get_descendant_files(root_dir):
                file_mappings.append((file_path, root_dir))
        else:
            # 非递归模式：仅将根目录的直接子文件夹中的文件移到根目录
            for subfolder in self.get_subfolders(root_dir):
                for file_path in self.get_files(subfolder):
                    file_mappings.append((file_path, root_dir))
        
        total_files = len(file_mappings)
        self.stats['total_files'] += total_files
        processed = 0
        
        for src_file, target_dir in file_mappings:
            if not self.is_running:
                return
            
            processed += 1
            rel_src = src_file.relative_to(root_dir)
            self.update_progress(processed, total_files, f"移动: {rel_src}")
            
            dst = target_dir / src_file.name
            
            counter = 1
            stem, suffix = dst.stem, dst.suffix
            while dst.exists():
                dst = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            action = "[演示]" if dry_run else "[移动]"
            rel_dst = dst.relative_to(root_dir)
            self.log(f"{action} {rel_src} -> {rel_dst}")
            
            if not dry_run and self.is_running:
                try:
                    shutil.move(str(src_file), str(dst))
                    self.stats['moved_to_root'] += 1
                    if self.record_moved:
                        self.record_moved({
                            'file': src_file.name,
                            'from': str(src_file.parent),
                            'to': str(dst)
                        })
                except Exception as e:
                    self.log(f"移动失败 {src_file.name}: {e}")
                    if self.record_error:
                        self.record_error({'file': str(src_file), 'error': str(e)})
                    self.stats['errors'] += 1
    
    def run_operation(self, root_dir: Path, move_to_folder: bool, move_to_root: bool,
                     include_subfolders: bool, dry_run: bool, move_all_files: bool = False) -> dict:
        self.stats = {
            'moved_to_folder': 0,
            'moved_to_root': 0,
            'errors': 0,
            'total_files': 0,
            'processed_files': 0
        }
        self.is_running = True
        self.move_all_files = move_all_files
        
        if move_to_folder:
            self.move_to_matching_folders(root_dir, include_subfolders, dry_run)
        
        if move_to_root and self.is_running:
            self.move_from_subfolders_to_root(root_dir, include_subfolders, dry_run)
        
        return self.stats


class FileMoveTab:
    def __init__(self, parent, controller, show_log_panel: bool = False):
        self.controller = controller
        self.parent = parent
        self.show_log_panel = show_log_panel
        self.is_running = False
        
        # 获取配置
        if hasattr(controller, 'get_config'):
            config = controller.get_config()
        else:
            config = {}
        self.tab_config = config.get('file_move_tab', {})
        
        # 操作队列
        self.operation_queue = queue.Queue()
        self.operation_thread = None
        self.operations = None
        
        self.unmatched_files: List[dict] = []
        self.moved_files: List[dict] = []
        self.error_files: List[dict] = []
        
        # 创建界面
        if show_log_panel:
            self._create_embedded_layout(parent)
        else:
            # 独立运行或不需要日志面板时，直接创建主框架
            self.frame = ttk.Frame(parent)
            self._create_main_content(self.frame)
            self.frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self._load_config()
    
    def _create_embedded_layout(self, parent):
        """嵌入模式布局（带日志面板 - 保留用于兼容性）"""
        # 主分割窗格
        paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # 上部：操作区（带滚动条）
        top_canvas = tk.Canvas(paned)
        top_scrollbar = ttk.Scrollbar(paned, orient="vertical", command=top_canvas.yview)
        top_frame = ttk.Frame(top_canvas)
        
        top_frame.bind("<Configure>", lambda e: top_canvas.configure(scrollregion=top_canvas.bbox("all")))
        
        window_id = top_canvas.create_window((0, 0), window=top_frame, anchor="nw")
        def update_width(event=None):
            top_canvas.itemconfig(window_id, width=top_canvas.winfo_width())
        top_canvas.bind('<Configure>', update_width)
        
        top_canvas.configure(yscrollcommand=top_scrollbar.set)
        
        def on_mousewheel(event):
            top_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        top_canvas.bind("<MouseWheel>", on_mousewheel)
        
        top_canvas.pack(side="left", fill="both", expand=True)
        top_scrollbar.pack(side="right", fill="y")
        
        paned.add(top_canvas, weight=3)
        
        # 下部：日志区
        bottom_frame = ttk.LabelFrame(paned, text="操作日志")
        paned.add(bottom_frame, weight=1)
        
        import tkinter.scrolledtext as scrolledtext
        self.log_text = scrolledtext.ScrolledText(bottom_frame, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 绑定日志回调
        if hasattr(self.controller, 'log_callback'):
            self.controller.log_callback = self._append_log
        
        self._create_main_content(top_frame)
        self.frame = top_frame
    
    def _append_log(self, message: str):
        if hasattr(self, 'log_text'):
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
    
    def _create_main_content(self, parent):
        """创建操作界面"""
        if not hasattr(self, 'frame'):
            self.frame = parent
        
        # 功能说明
        info_frame = ttk.LabelFrame(parent, text="功能说明")
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        info_text = (
            "根据子文件夹名称（番号）自动匹配并移动视频文件：\n"
            "• 模式1：将视频文件移到匹配文件夹内（如 ABC-123.mp4 → ABC-123/）\n"
            "• 模式2：将子文件夹中的视频移到当前文件夹（反向操作）\n"
            "• 支持递归处理所有子目录  • 支持演示模式预览操作"
        )
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=800).pack(padx=10, pady=5)
        
        # 文件夹设置
        dir_frame = ttk.LabelFrame(parent, text="文件夹设置")
        dir_frame.pack(fill=tk.X, padx=10, pady=5)
        
        path_row = ttk.Frame(dir_frame)
        path_row.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(path_row, text="操作文件夹:").pack(side=tk.LEFT, padx=(0, 5))
        self.dir_var = tk.StringVar()
        ttk.Entry(path_row, textvariable=self.dir_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(path_row, text="浏览...", command=self._browse_directory).pack(side=tk.RIGHT)
        
        ttk.Label(dir_frame, text="提示: 支持本地路径和网络路径", foreground="gray", font=("", 8)).pack(anchor=tk.W, padx=10, pady=(0, 5))
        
        # 操作选项
        option_frame = ttk.LabelFrame(parent, text="操作选项")
        option_frame.pack(fill=tk.X, padx=10, pady=5)
        
        left_col = ttk.Frame(option_frame)
        left_col.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=5)
        
        self.move_to_folder_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(left_col, text="将视频文件移到匹配文件夹内", variable=self.move_to_folder_var).pack(anchor=tk.W, pady=2)
        
        self.move_to_root_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left_col, text="将子文件夹中的视频移到当前文件夹", variable=self.move_to_root_var).pack(anchor=tk.W, pady=2)
        
        right_col = ttk.Frame(option_frame)
        right_col.pack(side=tk.LEFT, fill=tk.Y, padx=20, pady=5)
        
        self.include_subfolders_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_col, text="包含子文件夹（递归）", variable=self.include_subfolders_var).pack(anchor=tk.W, pady=2)
        
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right_col, text="仅演示（不实际移动）", variable=self.dry_run_var).pack(anchor=tk.W, pady=2)
        
        self.move_all_files_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(right_col, text="只移动视频文件", variable=self.move_all_files_var, value=False).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(right_col, text="移动所有文件", variable=self.move_all_files_var, value=True).pack(anchor=tk.W, pady=2)
        
        # 进度区域
        progress_frame = ttk.LabelFrame(parent, text="操作进度")
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack()
        
        self.stats_var = tk.StringVar(value="待处理: 0 | 已移动: 0 | 未匹配: 0 | 错误: 0")
        ttk.Label(progress_frame, textvariable=self.stats_var, font=("", 9, "bold")).pack(pady=5)
        
        # 按钮区域
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.start_btn = ttk.Button(btn_frame, text="开始执行", command=self._start_operation, width=15)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop_operation, width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        self.save_btn = ttk.Button(btn_frame, text="保存设置", command=self._save_config, width=12)
        self.save_btn.pack(side=tk.LEFT, padx=5)
        
        # 仅在显示日志面板时显示清空日志按钮
        if self.show_log_panel:
            ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side=tk.RIGHT, padx=5)
    
    def _save_config(self):
        """保存当前设置到通用配置文件"""
        try:
            config = {
                'file_move_tab': {
                    'directory': self.dir_var.get(),
                    'move_to_folder': self.move_to_folder_var.get(),
                    'move_to_root': self.move_to_root_var.get(),
                    'include_subfolders': self.include_subfolders_var.get(),
                    'dry_run': self.dry_run_var.get(),
                    'move_all_files': self.move_all_files_var.get()
                }
            }
            if hasattr(self.controller, 'save_config'):
                if self.controller.save_config(config):
                    self._log("[配置] 设置已保存")
                    messagebox.showinfo("保存成功", "当前设置已保存到配置文件")
                else:
                    messagebox.showerror("保存失败", "保存设置时发生错误，请查看日志")
            else:
                messagebox.showwarning("保存失败", "当前控制器不支持保存配置")
        except Exception as e:
            print(f"保存配置失败: {e}")
            messagebox.showerror("保存失败", f"保存设置失败: {e}")
    
    def _load_config(self):
        try:
            cfg = self.tab_config
            self.dir_var.set(cfg.get('directory', ''))
            self.move_to_folder_var.set(cfg.get('move_to_folder', True))
            self.move_to_root_var.set(cfg.get('move_to_root', False))
            self.include_subfolders_var.set(cfg.get('include_subfolders', False))
            self.dry_run_var.set(cfg.get('dry_run', True))
            self.move_all_files_var.set(cfg.get('move_all_files', False))
        except Exception as e:
            print(f"加载配置失败: {e}")
    
    def _browse_directory(self):
        directory = filedialog.askdirectory(title="选择操作文件夹")
        if directory:
            self.dir_var.set(directory)
    
    def _log(self, message: str):
        """统一日志接口：优先使用控制器的 log 方法（独立模式输出到控制台）"""
        if hasattr(self.controller, 'log'):
            self.controller.log(message)
    
    def _clear_log(self):
        if hasattr(self, 'log_text'):
            self.log_text.delete(1.0, tk.END)
    
    def _log_summary(self):
        self._log("\n" + "=" * 60)
        self._log("操作完成统计")
        self._log(f"总处理文件数: {self.stats['total_files']}")
        self._log(f"成功移动到文件夹: {self.stats['moved_to_folder']}")
        self._log(f"成功移动到根目录: {self.stats['moved_to_root']}")
        self._log(f"错误数: {self.stats['errors']}")
        
        if self.unmatched_files:
            self._log(f"\n未匹配文件数: {len(self.unmatched_files)}")
            for f in self.unmatched_files[:10]:
                self._log(f"  - {f['file']}")
        self._log("=" * 60)
    
    def _start_operation(self):
        dir_path = self.dir_var.get().strip()
        if not dir_path:
            messagebox.showwarning("警告", "请选择操作文件夹")
            return
        
        if not self.move_to_folder_var.get() and not self.move_to_root_var.get():
            messagebox.showwarning("警告", "请至少选择一项操作")
            return
        
        try:
            root_dir = Path(dir_path)
            if not root_dir.exists():
                messagebox.showwarning("警告", f"无法访问文件夹：{dir_path}")
                return
        except Exception as e:
            messagebox.showwarning("警告", f"无效路径：{e}")
            return
        
        if not self.dry_run_var.get():
            if not messagebox.askyesno("确认", "您选择了实际移动文件\n确定要继续吗？"):
                return
        
        self.unmatched_files.clear()
        self.moved_files.clear()
        self.error_files.clear()
        self.is_running = True
        
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_bar['value'] = 0
        self.progress_label.config(text="正在准备...")
        self.stats_var.set("正在计算文件数量...")
        
        while not self.operation_queue.empty():
            try:
                self.operation_queue.get_nowait()
            except queue.Empty:
                break
        
        self._log("\n" + "=" * 60)
        self._log("开始文件移动操作")
        self._log(f"目录: {dir_path}")
        self._log("=" * 60)
        
        self.operation_thread = threading.Thread(
            target=self._run_operation,
            args=(root_dir,),
            daemon=True
        )
        self.operation_thread.start()
        self._update_progress()
    
    def _stop_operation(self):
        self.is_running = False
        if self.operations:
            self.operations.stop()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_label.config(text="操作已停止")
        self._log("操作被用户停止")
    
    def _run_operation(self, root_dir: Path):
        try:
            self.operations = FileMoveOperations(
                log_callback=self._queue_log,
                progress_callback=self._queue_progress,
                move_all_files=self.move_all_files_var.get()
            )
            self.operations.record_unmatched = self._queue_unmatched
            self.operations.record_moved = self._queue_moved
            self.operations.record_error = self._queue_error
            
            stats = self.operations.run_operation(
                root_dir=root_dir,
                move_to_folder=self.move_to_folder_var.get(),
                move_to_root=self.move_to_root_var.get(),
                include_subfolders=self.include_subfolders_var.get(),
                dry_run=self.dry_run_var.get(),
                move_all_files=self.move_all_files_var.get()
            )
            
            if self.is_running:
                self.operation_queue.put(('complete', stats))
                
        except Exception as e:
            self.operation_queue.put(('error', str(e)))
        finally:
            self.operation_queue.put(('finished', None))
            self.operations = None
    
    def _queue_log(self, message: str):
        if self.is_running:
            self.operation_queue.put(('log', message))
    
    def _queue_progress(self, progress: int, message: str):
        if self.is_running:
            self.operation_queue.put(('progress', (progress, message)))
    
    def _queue_unmatched(self, file_info: dict):
        self.unmatched_files.append(file_info)
    
    def _queue_moved(self, moved_info: dict):
        self.moved_files.append(moved_info)
    
    def _queue_error(self, error_info: dict):
        self.error_files.append(error_info)
    
    def _update_progress(self):
        try:
            while not self.operation_queue.empty():
                msg_type, data = self.operation_queue.get_nowait()
                
                if msg_type == 'log':
                    self._log(data)
                elif msg_type == 'progress':
                    progress, message = data
                    self.progress_bar['value'] = progress
                    self.progress_label.config(text=message)
                elif msg_type == 'complete':
                    self.stats = data
                    total_moved = data['moved_to_folder'] + data['moved_to_root']
                    self.stats_var.set(
                        f"完成 | 待处理: {data['total_files']} | "
                        f"已移动: {total_moved} | "
                        f"未匹配: {len(self.unmatched_files)} | "
                        f"错误: {data['errors']}"
                    )
                    self.progress_label.config(text="操作完成")
                    self._log_summary()
                    
                    if not self.dry_run_var.get():
                        messagebox.showinfo("成功", 
                            f"操作完成！\n成功移动: {total_moved} 个文件\n"
                            f"未匹配: {len(self.unmatched_files)} 个\n错误: {data['errors']} 个")
                elif msg_type == 'error':
                    self._log(f"\n错误: {data}")
                    self.progress_label.config(text="操作失败")
                    messagebox.showerror("错误", f"操作失败: {data}")
                elif msg_type == 'finished':
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self.is_running = False
                    
        except queue.Empty:
            pass
        
        if self.is_running:
            widget = self.frame if hasattr(self, 'frame') else self.parent
            widget.after(100, self._update_progress)


def main():
    """独立运行入口 - 无日志面板，日志输出到控制台"""
    root = tk.Tk()
    root.title("文件移动工具")
    root.geometry("700x500")  # 缩小窗口高度（原800）
    root.minsize(600, 400)
    
    controller = StandaloneController()
    
    # 创建主容器
    main_container = ttk.Frame(root)
    main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    # 独立运行时 show_log_panel=False，不显示日志面板
    tab = FileMoveTab(main_container, controller, show_log_panel=False)
    
    # 状态栏
    status_bar = ttk.Label(root, text="就绪 - 日志输出到控制台", relief=tk.SUNKEN, anchor=tk.W)
    status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def on_closing():
        if tab.is_running:
            if messagebox.askokcancel("确认", "操作正在进行中，确定要退出吗？"):
                tab._stop_operation()
                root.after(500, root.destroy)
        else:
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()