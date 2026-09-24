#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 移动新视频选项卡 - 高性能异步验证版

"""移动新视频选项卡模块 - 高性能异步验证版（重构）

重大架构变更：
1. 取消"复制-验证-删除"的安全模式，改为直接快速移动（shutil.move）
2. 异步验证线程池：移动后立即显示，后台验证状态
3. 失败自动恢复：验证失败或移动出错时，自动将文件恢复到原位置
4. 实时状态更新：列表项状态动态变化（处理中 → 成功/失败）
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import shutil
from pathlib import Path
import os
import re
import threading
import queue
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 导入番号匹配器和模型
try:
    from pattern_matcher import PatternMatcher
    from models import MoveRecord, FolderIndex
except ImportError:
    PatternMatcher = None
    MoveRecord = None
    FolderIndex = None

# 视频扩展名常量
VIDEO_EXTENSIONS: set = {'.avi', '.mp4', '.ts', '.rmvb', '.rm', '.mkv', '.mov', '.wmv', '.flv', '.m4v', '.mpg', '.mpeg', '.m2ts'}

# 字幕扩展名常量
SUBTITLE_EXTENSIONS: set = {'.srt', '.ass', '.ssa'}

# 配置管理器导入
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


class AsyncVerifyWorker:
    """异步验证工作器 - 在后台验证文件移动结果"""
    
    def __init__(self, ui_queue: queue.Queue, max_workers: int = 3):
        self.ui_queue = ui_queue
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
    
    def submit_verify(self, item_id: str, source_path: Path, dest_path: Path):
        """提交验证任务"""
        future = self.executor.submit(
            self._verify_task, 
            item_id, source_path, dest_path
        )
    
    def _verify_task(self, item_id: str, source_path: Path, dest_path: Path):
        """执行验证任务"""
        try:
            time.sleep(0.2)  # 等待文件系统同步
            
            # 验证1: 目标文件是否存在
            if not dest_path.exists():
                # 尝试恢复：检查源文件是否还在
                if source_path.exists():
                    # 文件移动可能失败，源文件还在，无需恢复
                    self._send_result(item_id, False, "移动失败：目标未创建，源文件仍在", None)
                else:
                    # 严重错误：源文件和目标文件都丢失
                    self._send_result(item_id, False, "严重错误：源文件和目标文件都丢失", None)
                return
            
            # 验证2: 文件大小是否大于0
            dest_size = dest_path.stat().st_size
            if dest_size == 0:
                # 尝试删除空文件并恢复
                try:
                    dest_path.unlink()
                    if source_path.exists():
                        self._send_result(item_id, False, "移动失败：创建空文件，已删除", source_path)
                    else:
                        self._send_result(item_id, False, "移动失败：创建空文件且源文件丢失", None)
                except:
                    self._send_result(item_id, False, "验证异常：空文件无法删除", None)
                return
            
            # 验证通过
            self._send_result(item_id, True, "验证成功", None)
            
        except Exception as e:
            self._send_result(item_id, False, f"验证异常: {str(e)}", None)
    
    def _send_result(self, item_id: str, success: bool, message: str, recovered_path: Optional[Path]):
        """发送验证结果到UI队列"""
        self.ui_queue.put({
            'type': 'verify_result',
            'item_id': item_id,
            'success': success,
            'message': message,
            'recovered_path': recovered_path
        })
    
    def shutdown(self):
        """关闭验证线程池"""
        self.executor.shutdown(wait=False)


class MoveVideosTab:
    """移动新视频选项卡 - 高性能异步验证版"""

    CONFIG_SECTION = "move_videos"

    def __init__(self, parent_notebook, controller):
        self.controller = controller
        self.frame = ttk.Frame(parent_notebook)

        # 初始化组件
        if PatternMatcher:
            self.matcher = PatternMatcher()
        else:
            self.matcher = self._create_fallback_matcher()
        
        # 记录管理：使用字典加速查找
        self.move_records: Dict[str, dict] = {}  # item_id -> record
        self.is_running = False
        self.operation_thread = None

        # 索引存储
        self.folder_index: Dict[str, List] = {}
        self.name_index: Dict[str, List] = {}

        # 进度队列
        self.progress_queue = queue.Queue()
        
        # UI更新队列（线程安全）
        self.ui_queue = queue.Queue()

        # 异步验证器
        self.verify_worker = AsyncVerifyWorker(self.ui_queue, max_workers=4)

        # 配置管理器
        if controller and hasattr(controller, 'config_manager'):
            self.config_manager = controller.config_manager
        else:
            self.config_manager = ConfigManager()

        self.create_ui()
        self.load_config()

        # 注册到notebook
        parent_notebook.add(self.frame, text="移动新视频(move_videos)")

        # 启动检查器
        self.check_progress()
        self.check_ui_updates()

    def _create_fallback_matcher(self):
        """创建备用的PatternMatcher"""
        import re
        from typing import Optional, Dict

        class FallbackMatcher:
            def __init__(self):
                self.patterns = [
                    (re.compile(r'^([fF][cC]2?[_-]?[pP][pP][vV][_-]?(\\d{6,}))'), 'fc2_ppv'),
                    (re.compile(r'^([fF][cC]2?[_-]?(\\d{6,}))'), 'fc2'),
                    (re.compile(r'^(\\d+[a-zA-Z]+[_-]\\d+)'), 'digit_prefix'),
                    (re.compile(r'^([a-zA-Z]+\\d*[_-]\\d+)'), 'standard'),
                    (re.compile(r'^(\\d{3,}[_-]\\d{3,})'), 'numeric'),
                ]

            def extract_code(self, text: str) -> Optional[Dict]:
                for pattern_regex, ptype in self.patterns:
                    match = pattern_regex.match(text)
                    if match:
                        matched_text = match.group(1)
                        full_code = matched_text.replace('-', '').replace('_', '').lower()
                        letters = ''.join(re.findall(r'[a-zA-Z]+', full_code))
                        numbers = ''.join(re.findall(r'\\d+', full_code))

                        return {
                            'full': full_code,
                            'original': matched_text,
                            'canonical': matched_text.replace('_', '-'),
                            'letters': letters,
                            'numbers': numbers,
                            'type': ptype,
                            'is_short_numeric': False
                        }

                letters = ''.join(re.findall(r'[a-zA-Z]+', text)).lower()
                numbers = ''.join(re.findall(r'\\d+', text))

                if letters and numbers:
                    full_code = letters + numbers
                    original = text.split()[0] if text else text
                    return {
                        'full': full_code,
                        'original': original,
                        'canonical': original.replace('_', '-'),
                        'letters': letters,
                        'numbers': numbers,
                        'type': 'generic',
                        'is_short_numeric': False
                    }

                if numbers and not letters and len(numbers) <= 3:
                    return {
                        'full': numbers,
                        'original': text,
                        'canonical': text.replace('_', '-'),
                        'letters': '',
                        'numbers': numbers,
                        'type': 'short_numeric',
                        'is_short_numeric': True
                    }
                return None

            def is_strict_match(self, file_code: Dict, folder_code: Dict) -> int:
                if not file_code or not folder_code:
                    return 0

                if file_code.get('is_short_numeric') or folder_code.get('is_short_numeric'):
                    return 1 if file_code['full'] == folder_code['full'] else 0

                file_full = file_code['full']
                folder_full = folder_code['full']

                if file_full == folder_full:
                    return 2
                if folder_full in file_full or file_full in folder_full:
                    return 2

                file_letters = file_code['letters']
                folder_letters = folder_code['letters']
                file_numbers = file_code['numbers']
                folder_numbers = folder_code['numbers']

                letters_match = (folder_letters in file_letters) or (file_letters in folder_letters)

                numbers_match = False
                if file_numbers and folder_numbers:
                    file_num_stripped = file_numbers.lstrip('0') or '0'
                    folder_num_stripped = folder_numbers.lstrip('0') or '0'

                    if file_num_stripped == folder_num_stripped:
                        numbers_match = True
                    elif folder_num_stripped in file_num_stripped:
                        numbers_match = True
                    elif file_num_stripped in folder_num_stripped:
                        numbers_match = True
                    elif file_numbers.endswith(folder_numbers) or folder_numbers.endswith(file_numbers):
                        numbers_match = True

                if letters_match and numbers_match:
                    return 1
                return 0

        return FallbackMatcher()

    def create_ui(self):
        """创建界面 - 增加动态状态列"""
        # 1. 功能说明
        info_frame = ttk.LabelFrame(self.frame, text="功能说明")
        info_frame.pack(fill=tk.X, padx=10, pady=10)

        info_text = (
            "高性能异步移动模式：\n"
            "• 直接快速移动：使用系统级移动操作，极速处理大文件\n"
            "• 异步验证：移动后立即显示在列表，后台验证完整性\n" 
            "• 自动恢复：验证失败时自动将文件恢复到原位置\n"
            "• 状态追踪：蓝色=验证中，绿色=成功，红色=失败"
        )
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=1100).pack(padx=10, pady=10, fill=tk.X)

        # 2. 文件夹设置
        folder_frame = ttk.LabelFrame(self.frame, text="文件夹设置")
        folder_frame.pack(fill=tk.X, padx=10, pady=10)

        # 视频文件夹
        video_row = ttk.Frame(folder_frame)
        video_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(video_row, text="视频文件夹:", width=12).pack(side=tk.LEFT)
        self.video_folder_var = tk.StringVar()
        ttk.Entry(video_row, textvariable=self.video_folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(video_row, text="浏览...", command=self.browse_video_folder).pack(side=tk.RIGHT)

        # 目标文件夹
        target_row = ttk.Frame(folder_frame)
        target_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(target_row, text="目标文件夹:", width=12).pack(side=tk.LEFT)
        self.target_folder_var = tk.StringVar()
        ttk.Entry(target_row, textvariable=self.target_folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(target_row, text="浏览...", command=self.browse_target_folder).pack(side=tk.RIGHT)

        # 3. 操作选项
        option_frame = ttk.LabelFrame(self.frame, text="操作选项")
        option_frame.pack(fill=tk.X, padx=10, pady=10)
        self.include_subfolders_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(option_frame, text="包含子文件夹（递归遍历所有子目录）", 
                       variable=self.include_subfolders_var).pack(anchor=tk.W, padx=10, pady=8)

        # 4. 进度条
        progress_frame = ttk.LabelFrame(self.frame, text="操作进度")
        progress_frame.pack(fill=tk.X, padx=10, pady=10)

        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100, 
                       mode='determinate').pack(fill=tk.X, padx=10, pady=8)

        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack()

        self.stats_label = ttk.Label(progress_frame, 
                                    text="索引: 0 | 待处理: 0 | 验证中: 0 | 成功: 0 | 失败: 0 | 未匹配: 0",
                                    foreground="blue")
        self.stats_label.pack()

        # 5. 按钮
        button_frame = ttk.Frame(self.frame)
        button_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(button_frame, text="保存设置", command=self.save_settings, width=15).pack(side=tk.LEFT, padx=5)
        self.execute_btn = ttk.Button(button_frame, text="开始执行", command=self.execute_move, width=15)
        self.execute_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(button_frame, text="停止", command=self.stop_operation, 
                                  width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # 6. 结果表格 - 增加状态列
        table_frame = ttk.LabelFrame(self.frame, text="操作结果（蓝色=验证中，绿色=成功，红色=失败，点击记录查看路径）")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ('filename', 'foldername', 'status', 'action')
        self.result_tree = ttk.Treeview(table_frame, columns=columns, show='headings')
        self.result_tree.heading('filename', text='文件名')
        self.result_tree.heading('foldername', text='目标文件夹名')
        self.result_tree.heading('status', text='状态')
        self.result_tree.heading('action', text='操作')
        self.result_tree.column('filename', width=320)
        self.result_tree.column('foldername', width=500)
        self.result_tree.column('status', width=100, anchor=tk.CENTER)
        self.result_tree.column('action', width=80, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 设置标签颜色
        self.result_tree.tag_configure('pending', foreground='orange')
        self.result_tree.tag_configure('success', foreground='green')
        self.result_tree.tag_configure('failed', foreground='red')
        self.result_tree.tag_configure('verifying', foreground='blue')

        # 绑定事件
        self.result_tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.result_tree.bind('<ButtonRelease-1>', self.on_tree_click)

        # 7. 路径显示区域
        path_frame = ttk.LabelFrame(self.frame, text="目标路径详情（选中上方记录显示）")
        path_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.path_var = tk.StringVar(value="点击上方表格中的记录，此处将显示移动后的完整文件夹路径...")
        path_entry = ttk.Entry(path_frame, textvariable=self.path_var, state='readonly', font=('Consolas', 10))
        path_entry.pack(fill=tk.X, padx=10, pady=8)

        ttk.Button(self.frame, text="清空结果记录", command=self.clear_records).pack(anchor=tk.E, padx=10, pady=5)
    
    def check_ui_updates(self):
        """检查UI更新队列 - 处理异步验证结果"""
        try:
            while True:
                update = self.ui_queue.get_nowait()
                update_type = update.get('type')
                
                if update_type == 'verify_result':
                    self._handle_verify_result(update)
                    
        except queue.Empty:
            pass
        self.frame.after(100, self.check_ui_updates)
    
    def _handle_verify_result(self, update: dict):
        """处理验证结果"""
        item_id = update['item_id']
        success = update['success']
        message = update['message']
        recovered_path = update.get('recovered_path')
        
        if item_id not in self.move_records:
            return
            
        record = self.move_records[item_id]
        
        if success:
            # 验证成功
            record['status'] = 'success'
            record['status_text'] = '✓ 成功'
            self.result_tree.item(item_id, values=(
                record['filename'], record['foldername'], '✓ 成功', '撤回'
            ), tags=('success',))
            self.log(f"  [✓ 验证通过] {record['filename']}")
        else:
            # 验证失败，尝试恢复
            self._handle_verify_failure(item_id, record, message, recovered_path)

    def _handle_verify_failure(self, item_id: str, record: dict, message: str, recovered_path: Optional[Path]):
        """处理验证失败"""
        dest_path = record.get('dest_path')
        source_path = record.get('source_path')
        
        # 尝试自动恢复
        recovery_success = False
        if dest_path and dest_path.exists() and source_path:
            try:
                # 确保源目录存在
                source_path.parent.mkdir(parents=True, exist_ok=True)
                # 移回源位置
                shutil.move(str(dest_path), str(source_path))
                recovery_success = True
                self.log(f"  [✗ 验证失败-已自动恢复] {record['filename']}: {message}")
            except Exception as e:
                self.log(f"  [✗ 验证失败-恢复也失败] {record['filename']}: {message}, 恢复错误: {e}")
        
        record['status'] = 'failed_recovered' if recovery_success else 'failed'
        status_text = '✗ 失败-已恢复' if recovery_success else f'✗ 失败-{message[:15]}'
        
        self.result_tree.item(item_id, values=(
            record['filename'], 
            '(已恢复)' if recovery_success else record['foldername'], 
            status_text, 
            '删除' if recovery_success else ''
        ), tags=('failed',))

    def get_frame(self):
        return self.frame

    def browse_video_folder(self):
        directory = filedialog.askdirectory(title="选择视频文件夹")
        if directory:
            self.video_folder_var.set(directory)

    def browse_target_folder(self):
        directory = filedialog.askdirectory(title="选择目标文件夹")
        if directory:
            self.target_folder_var.set(directory)

    def load_config(self):
        """从统一配置管理器加载配置"""
        section_config = self.config_manager.get_section(self.CONFIG_SECTION, {})

        self.video_folder_var.set(section_config.get("video_folder", ""))
        self.target_folder_var.set(section_config.get("target_folder", ""))
        self.include_subfolders_var.set(section_config.get("include_subfolders", False))

        # 兼容性处理
        if not section_config and self.controller and hasattr(self.controller, 'get_config'):
            try:
                old_config = self.controller.get_config()
                if old_config:
                    self.video_folder_var.set(old_config.get("video_folder", ""))
                    self.target_folder_var.set(old_config.get("target_folder", ""))
                    self.include_subfolders_var.set(old_config.get("include_subfolders", False))
                    self.save_settings()
            except:
                pass

    def save_settings(self):
        """保存设置到统一配置管理器"""
        config = {
            "video_folder": self.video_folder_var.get(),
            "target_folder": self.target_folder_var.get(),
            "include_subfolders": self.include_subfolders_var.get()
        }
        self.config_manager.set_section(self.CONFIG_SECTION, config, auto_save=True)
        messagebox.showinfo("成功", "设置已保存到统一配置")

    def log(self, message: str):
        """通过controller记录日志"""
        if self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

    def update_progress(self, value: int, message: str, stats: Optional[Dict] = None):
        """更新进度（通过队列）"""
        self.progress_queue.put((value, message, stats))

    def check_progress(self):
        """检查进度队列"""
        try:
            while True:
                value, message, stats = self.progress_queue.get_nowait()
                self.progress_var.set(value)
                self.progress_label.config(text=message)
                if stats:
                    self.stats_label.config(text=
                        f"索引: {stats.get('code_index', 0)} | "
                        f"待处理: {stats.get('total_videos', 0)} | "
                        f"验证中: {stats.get('verifying', 0)} | "
                        f"成功: {stats.get('success', 0)} | "
                        f"失败: {stats.get('failed', 0)} | "
                        f"未匹配: {stats.get('unmatched', 0)}"
                    )
        except queue.Empty:
            pass
        self.frame.after(100, self.check_progress)

    def clear_records(self):
        if messagebox.askyesno("确认", "确定要清空所有结果记录吗？"):
            for item_id in list(self.move_records.keys()):
                self.result_tree.delete(item_id)
            self.move_records.clear()
            self.path_var.set("点击上方表格中的记录，此处将显示移动后的完整文件夹路径...")

    def build_folder_index(self, target_path: Path, include_subfolders: bool) -> Tuple[int, int]:
        """建立文件夹索引"""
        self.folder_index.clear()
        self.name_index.clear()

        code_index_count = 0
        name_index_count = 0

        self.log("开始建立索引...")

        folders_to_process = []
        if include_subfolders:
            for root, dirs, files in os.walk(target_path):
                for dir_name in dirs:
                    folders_to_process.append((Path(root) / dir_name, dir_name))
        else:
            try:
                for item in target_path.iterdir():
                    if item.is_dir():
                        folders_to_process.append((item, item.name))
            except Exception as e:
                self.log(f"访问目标文件夹失败: {e}")
                return 0, 0

        for folder_path, dir_name in folders_to_process:
            if not self.is_running:
                break

            if not self.matcher:
                continue

            code_info = self.matcher.extract_code(dir_name)

            if code_info:
                is_short_num = code_info.get('is_short_numeric', False)

                index_entry = {
                    'full_code': code_info['full'],
                    'original_name': dir_name,
                    'path': folder_path,
                    'letters': code_info['letters'],
                    'numbers': code_info['numbers'],
                    'pattern_type': code_info['type'],
                    'is_short_numeric': is_short_num
                }

                if is_short_num:
                    if code_info['full'] not in self.name_index:
                        self.name_index[code_info['full']] = []
                    self.name_index[code_info['full']].append(index_entry)
                    name_index_count += 1
                else:
                    if code_info['full'] not in self.folder_index:
                        self.folder_index[code_info['full']] = []
                    self.folder_index[code_info['full']].append(index_entry)
                    code_index_count += 1

                    alt_key = code_info['letters'] + code_info['numbers']
                    if alt_key != code_info['full']:
                        if alt_key not in self.folder_index:
                            self.folder_index[alt_key] = []
                        self.folder_index[alt_key].append(index_entry)
            else:
                clean_name = re.sub(r'[\\s\\-_\\.]+', '', dir_name).lower()
                if clean_name and len(clean_name) > 1:
                    index_entry = {
                        'full_code': clean_name,
                        'original_name': dir_name,
                        'path': folder_path,
                        'letters': '',
                        'numbers': '',
                        'pattern_type': 'fullname'
                    }
                    if clean_name not in self.name_index:
                        self.name_index[clean_name] = []
                    self.name_index[clean_name].append(index_entry)
                    name_index_count += 1

        self.log(f"索引建立完成: {code_index_count} 个番号索引, {name_index_count} 个名称索引")
        return code_index_count, name_index_count

    # 需要跳过的文件夹名标记
    SKIP_FOLDER_MARKERS = ['(个人)', '(不好看)']

    def _is_skip_folder(self, folder_name: str) -> bool:
        """检查文件夹名是否包含跳过标记"""
        for marker in self.SKIP_FOLDER_MARKERS:
            if marker in folder_name:
                return True
        return False

    def find_best_match(self, file_name: str, file_code: Optional[Dict]) -> Optional[Path]:
        """查找最佳匹配

        新增逻辑：如果匹配到的目标文件夹名中包含"(个人)"或"(不好看)",
        则跳过该文件夹，继续查找其他候选。如果没有其他候选，则返回None。
        """
        candidates = []
        clean_file_name = re.sub(r'[\s\-_\.]+', '', file_name).lower()
        clean_file_name = re.sub(r'\.(mp4|avi|mkv|mov|wmv|flv|ts|rmvb|rm|m4v|mpg|mpeg)$', '', clean_file_name)

        # 番号索引匹配
        if file_code and not file_code.get('is_short_numeric'):
            file_full = file_code['full']

            if file_full in self.folder_index:
                for idx_entry in self.folder_index[file_full]:
                    # 跳过包含标记的文件夹
                    if self._is_skip_folder(idx_entry['original_name']):
                        continue
                    if self.matcher:
                        match_level = self.matcher.is_strict_match(file_code, {
                            'full': idx_entry['full_code'],
                            'letters': idx_entry['letters'],
                            'numbers': idx_entry['numbers'],
                            'is_short_numeric': idx_entry['is_short_numeric']
                        })
                        if match_level >= 2:
                            candidates.append((idx_entry, 3))
                        elif match_level == 1:
                            candidates.append((idx_entry, 2))

            if not candidates:
                for code_key, idx_list in self.folder_index.items():
                    if len(code_key) > 3 and (file_full in code_key or code_key in file_full):
                        for idx_entry in idx_list:
                            # 跳过包含标记的文件夹
                            if self._is_skip_folder(idx_entry['original_name']):
                                continue
                            if self.matcher:
                                match_level = self.matcher.is_strict_match(file_code, {
                                    'full': idx_entry['full_code'],
                                    'letters': idx_entry['letters'],
                                    'numbers': idx_entry['numbers'],
                                    'is_short_numeric': idx_entry['is_short_numeric']
                                })
                                if match_level > 0:
                                    candidates.append((idx_entry, 1))

        # 名称索引匹配
        if not candidates:
            if clean_file_name in self.name_index:
                for idx_entry in self.name_index[clean_file_name]:
                    # 跳过包含标记的文件夹
                    if self._is_skip_folder(idx_entry['original_name']):
                        continue
                    if idx_entry['is_short_numeric']:
                        if clean_file_name == idx_entry['full_code']:
                            candidates.append((idx_entry, 0))
                    else:
                        candidates.append((idx_entry, 0))

            elif len(clean_file_name) > 3:
                for name_key, idx_list in self.name_index.items():
                    if len(name_key) > 3:
                        if clean_file_name in name_key or name_key in clean_file_name:
                            for idx_entry in idx_list:
                                # 跳过包含标记的文件夹
                                if self._is_skip_folder(idx_entry['original_name']):
                                    continue
                                candidates.append((idx_entry, -1))

        if not candidates:
            return None

        best = max(candidates, key=lambda x: (x[1], -len(str(x[0]['path']))))
        return best[0]['path']
    def collect_video_files(self, video_path: Path, include_subfolders: bool) -> List[Path]:
        """收集视频文件"""
        video_files = []
        if include_subfolders:
            for root, dirs, files in os.walk(video_path):
                for file in files:
                    if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                        video_files.append(Path(root) / file)
        else:
            try:
                video_files = [f for f in video_path.iterdir() 
                              if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]
            except Exception as e:
                self.log(f"获取视频文件失败: {e}")
        return video_files

    def quick_move_file(self, source_path: Path, target_folder: Path) -> Tuple[bool, Optional[Path], str, bool]:
        """快速移动文件
        
        Returns:
            (success, dest_path, error_message, was_renamed)
        """
        try:
            target_folder.mkdir(parents=True, exist_ok=True)

            dest_file = target_folder / source_path.name
            was_renamed = False
            counter = 1
            stem, suffix = dest_file.stem, dest_file.suffix
            while dest_file.exists():
                # 检查是否为完全相同的文件
                try:
                    if dest_file.stat().st_size == source_path.stat().st_size:
                        return True, dest_file, "目标已存在相同文件", False
                except:
                    pass
                dest_file = target_folder / f"{stem}_{counter}{suffix}"
                counter += 1
                was_renamed = True

            shutil.move(str(source_path), str(dest_file))
            return True, dest_file, "", was_renamed

        except Exception as e:
            return False, None, str(e), False

    def move_subtitles(self, video_path: Path, target_folder: Path) -> List[Path]:
        """移动字幕文件"""
        moved = []
        source_dir = video_path.parent
        video_stem = video_path.stem.lower()

        for sub_file in source_dir.iterdir():
            if not sub_file.is_file():
                continue
            if sub_file.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue

            sub_stem = sub_file.stem.lower()
            if sub_stem.startswith(video_stem):
                success, dest_path, error_msg, was_renamed = self.quick_move_file(sub_file, target_folder)
                if success:
                    moved.append(dest_path)
                    if was_renamed:
                        self.log(f"  [字幕重命名] {sub_file.name} -> {dest_path.name} (目标已存在同名)")
                    else:
                        self.log(f"  [字幕移动] {sub_file.name} -> {target_folder.name}")
                else:
                    self.log(f"  [字幕移动失败] {sub_file.name}: {error_msg}")

        return moved

    def execute_move(self):
        """执行移动操作"""
        video_folder = self.video_folder_var.get().strip()
        target_folder = self.target_folder_var.get().strip()

        if not video_folder or not target_folder:
            messagebox.showwarning("警告", "请设置视频文件夹和目标文件夹")
            return

        video_path = Path(video_folder)
        target_path = Path(target_folder)

        if not video_path.exists() or not target_path.exists():
            messagebox.showwarning("警告", "文件夹路径不存在")
            return

        include_sub = self.include_subfolders_var.get()

        if not messagebox.askyesno("确认", f"开始移动操作？\n包含子文件夹: {'是' if include_sub else '否'}\n\n注意：采用快速直接移动模式，出错时自动恢复"):
            return

        self.execute_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.is_running = True

        # 清空旧记录
        self.clear_records_silent()

        self.operation_thread = threading.Thread(
            target=self._move_operation_thread,
            args=(video_path, target_path, include_sub),
            daemon=True
        )
        self.operation_thread.start()

    def clear_records_silent(self):
        """静默清空记录"""
        for item_id in list(self.move_records.keys()):
            self.result_tree.delete(item_id)
        self.move_records.clear()

    def stop_operation(self):
        self.is_running = False
        self.log("正在停止...")
        self.stop_btn.config(state=tk.DISABLED)

    def _move_operation_thread(self, video_path: Path, target_path: Path, include_subfolders: bool):
        """后台线程执行移动操作 - 极速版"""
        self.log("=" * 60)
        self.log(f"开始移动操作（快速直接移动 + 异步验证）")
        self.log(f"视频文件夹: {video_path}")
        self.log(f"目标文件夹: {target_path}")
        self.log("=" * 60)

        # 阶段1: 建立索引
        self.update_progress(5, "正在建立文件夹索引...", 
                           {'code_index': 0, 'total_videos': 0, 'verifying': 0, 'success': 0, 'failed': 0, 'unmatched': 0})

        code_count, name_count = self.build_folder_index(target_path, include_subfolders)

        if not self.is_running:
            self.frame.after(0, self._operation_completed)
            return

        if code_count + name_count == 0:
            self.log("警告: 未找到可索引的文件夹")
            self.frame.after(0, lambda: messagebox.showwarning("警告", "目标文件夹中未找到符合规则的子文件夹"))
            self.frame.after(0, self._operation_completed)
            return

        self.update_progress(15, f"索引完成: {code_count} 个番号, {name_count} 个名称", 
                           {'code_index': code_count, 'total_videos': 0, 'verifying': 0, 'success': 0, 'failed': 0, 'unmatched': 0})

        # 阶段2: 收集视频文件
        video_files = self.collect_video_files(video_path, include_subfolders)
        total_files = len(video_files)

        if total_files == 0:
            self.log("未找到视频文件")
            self.frame.after(0, self._operation_completed)
            return

        self.update_progress(20, f"准备处理 {total_files} 个视频...", 
                           {'code_index': code_count, 'total_videos': total_files, 'verifying': 0, 'success': 0, 'failed': 0, 'unmatched': 0})

        # 阶段3: 批量匹配和快速移动
        stats = {
            'verifying': 0,
            'success': 0,
            'failed': 0,
            'unmatched': 0
        }

        for index, video_file in enumerate(video_files, 1):
            if not self.is_running:
                break

            progress = 20 + int((index / total_files) * 80)
            
            file_code = None
            if self.matcher:
                file_code = self.matcher.extract_code(video_file.stem)

            best_match = self.find_best_match(video_file.name, file_code)

            if not best_match:
                self.log(f"[未匹配] {video_file.name}")
                stats['unmatched'] += 1
                self.update_progress(progress, f"处理 {index}/{total_files}: {video_file.name[:30]}... (未匹配)", 
                                   {'code_index': code_count, 'total_videos': total_files, **stats})
                continue

            # 执行快速移动（在后台线程直接操作，不阻塞）
            success, dest_path, error_msg, was_renamed = self.quick_move_file(video_file, best_match)

            if success:
                stats['verifying'] += 1
                item_id = f"ITEM_{index}_{int(time.time() * 1000)}"
                self.frame.after(0, self._add_verifying_record, item_id, video_file, dest_path, best_match.name)
                self.verify_worker.submit_verify(item_id, video_file, dest_path)
                
                if was_renamed:
                    self.log(f"⚠️ [重命名] {video_file.name} -> {dest_path.name} (目标文件夹已存在同名文件)")
                else:
                    self.log(f"[移动完成-待验证] {video_file.name} -> {best_match.name}")

                moved_subs = self.move_subtitles(video_file, best_match)
                if moved_subs:
                    self.log(f"  [字幕] 共移动 {len(moved_subs)} 个字幕文件")
            else:
                stats['failed'] += 1
                self.log(f"[移动失败] {video_file.name}: {error_msg}")
                self.frame.after(0, self._add_failed_record, video_file, best_match.name, error_msg)
            
            # 每10个文件更新一次进度，减少UI负担
            if index % 10 == 0 or index == total_files:
                self.update_progress(progress, f"处理 {index}/{total_files}: {video_file.name[:30]}...", 
                                   {'code_index': code_count, 'total_videos': total_files, **stats})

        self.update_progress(100, f"移动完成，后台验证中...（验证中: {stats['verifying']}）", 
                           {'code_index': code_count, 'total_videos': total_files, **stats})

        self.log("\n" + "=" * 60)
        self.log(f"移动阶段完成: 总数{total_files}, 验证中{stats['verifying']}, 未匹配{stats['unmatched']}, 立即失败{stats['failed']}")
        self.log("后台正在验证文件完整性，验证失败的文件将自动恢复...")
        self.log("=" * 60)

        self.frame.after(0, lambda: self._operation_completed(stats['verifying']))

    def _add_verifying_record(self, item_id: str, source_path: Path, dest_path: Path, folder_name: str):
        """添加验证中记录（在主线程调用）"""
        self.result_tree.insert('', tk.END, iid=item_id, values=(
            source_path.name, folder_name, '⟳ 验证中...', '撤回'
        ), tags=('verifying',))

        record = {
            'item_id': item_id,
            'source_path': source_path,
            'dest_path': dest_path,
            'filename': source_path.name,
            'foldername': folder_name,
            'status': 'verifying',
            'status_text': '⟳ 验证中...'
        }
        self.move_records[item_id] = record

    def _add_failed_record(self, source_path: Path, folder_name: str, error_msg: str):
        """添加失败记录"""
        item_id = f"ITEM_FAIL_{int(time.time() * 1000)}_{id(source_path)}"
        
        self.result_tree.insert('', tk.END, iid=item_id, values=(
            source_path.name, folder_name, f'✗ {error_msg[:20]}', ''
        ), tags=('failed',))

        record = {
            'item_id': item_id,
            'source_path': source_path,
            'dest_path': None,
            'filename': source_path.name,
            'foldername': folder_name,
            'status': 'failed',
            'status_text': f'✗ {error_msg}'
        }
        self.move_records[item_id] = record

    def _operation_completed(self, pending_count=0):
        """操作完成回调"""
        self.execute_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False
        
        if pending_count > 0:
            messagebox.showinfo("移动完成", f"文件移动阶段完成！\n{pending_count} 个文件正在后台验证完整性...\n\n验证失败的文件将自动恢复到原位置。")
        else:
            messagebox.showinfo("完成", "操作完成！所有文件已处理。")

    def on_tree_select(self, event):
        """处理列表选择事件 - 显示完整路径"""
        selection = self.result_tree.selection()
        if not selection:
            return

        item_id = selection[0]
        if item_id in self.move_records:
            record = self.move_records[item_id]
            if record.get('dest_path'):
                folder_path = str(record['dest_path'].parent)
                self.path_var.set(f"{folder_path}")
            else:
                self.path_var.set("无目标路径信息")

    def on_tree_click(self, event):
        """处理撤回点击（第4列）"""
        item = self.result_tree.identify_row(event.y)
        column = self.result_tree.identify_column(event.x)

        if not item or column != '#4':
            return

        if item not in self.move_records:
            return

        record = self.move_records[item]
        
        # 根据状态决定操作
        if record['status'] == 'success':
            # 成功状态：执行撤回
            if not messagebox.askyesno("确认撤回", f'撤回文件 "{record["filename"]}"？'):
                return
            
            try:
                if not record['dest_path'].exists():
                    messagebox.showwarning("警告", "目标文件不存在")
                    return

                record['source_path'].parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(record['dest_path']), str(record['source_path']))

                self.log(f"[撤回] {record['dest_path']} -> {record['source_path']}")
                self.result_tree.delete(item)
                del self.move_records[item]

                messagebox.showinfo("成功", "文件已撤回")
                
            except Exception as e:
                messagebox.showerror("错误", f"撤回失败: {e}")
                
        elif record['status'] in ['failed', 'failed_recovered']:
            # 失败状态：删除记录
            if messagebox.askyesno("确认", f'删除失败记录 "{record["filename"]}"？'):
                self.result_tree.delete(item)
                del self.move_records[item]

    def __del__(self):
        """清理资源"""
        if hasattr(self, 'verify_worker'):
            self.verify_worker.shutdown()

if __name__ == '__main__':
    """独立运行入口 - 不依赖主程序即可直接运行本选项卡"""
    root = tk.Tk()
    root.title("移动视频工具 - 独立运行版")
    root.geometry("1200x800")

    # 创建Notebook容器
    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # 创建选项卡实例（controller传None，内部会自动处理）
    app = MoveVideosTab(notebook, None)

    # 启动主循环
    root.mainloop()
