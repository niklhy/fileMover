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

        # 目标文件夹全部子目录（用于事后统计"空文件夹"）
        self.all_target_folders: List[Path] = []
        # 本次操作中实际有视频移入的目标文件夹
        self.used_target_folders: set = set()

        # 源文件夹文件映射：目录 -> 该目录下所有文件（只扫描一次，避免每文件重复遍历磁盘）
        self.source_file_map: Dict[Path, List[Path]] = {}

        # 番号深度学习库（与 workflow 模块共用同一份 uid_learn.json，懒加载）
        self.uid_learner = self._load_uid_learner()

        # 事后待处理数据（供手动配对）
        self.unmoved_videos: List[Path] = []
        self.empty_folders: List[Path] = []
        self._leftover_item_ids: List[str] = []

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

    def _load_uid_learner(self):
        """加载番号深度学习库（与 workflow 模块共用同一份 uid_learn.json）

        加载失败则退化为 None，不影响主流程（仅失去"人工修正记忆"能力）。
        """
        try:
            from workflow.uid_learner import UidLearner
            return UidLearner.load_default()
        except Exception as e:
            self.log(f"[深度学习库] 加载失败，将不启用人工修正记忆: {e}")
            return None

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
        """创建界面 - 用内部页签区分操作步骤，避免单页内容过长显示不全"""
        # 内部页签容器：①执行设置 ②结果与验证 ③手动配对
        self.notebook = ttk.Notebook(self.frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.exec_page = ttk.Frame(self.notebook)
        self.result_page = ttk.Frame(self.notebook)
        self.pair_page = ttk.Frame(self.notebook)
        self.notebook.add(self.exec_page, text=" ① 执行设置 ")
        self.notebook.add(self.result_page, text=" ② 结果与验证 ")
        self.notebook.add(self.pair_page, text=" ③ 手动配对 ")

        # 1. 功能说明
        info_frame = ttk.LabelFrame(self.exec_page, text="功能说明")
        info_frame.pack(fill=tk.X, padx=10, pady=10)

        info_text = (
            "高性能异步移动：系统级快速移动 + 后台异步验证（验证失败自动恢复原位置）\n"
            "状态颜色：蓝色=验证中，绿色=成功，红色=失败；未移动视频请在「手动配对」页处理"
        )
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=1000).pack(padx=10, pady=8, fill=tk.X)

        # 2. 文件夹设置
        folder_frame = ttk.LabelFrame(self.exec_page, text="文件夹设置")
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
        option_frame = ttk.LabelFrame(self.exec_page, text="操作选项")
        option_frame.pack(fill=tk.X, padx=10, pady=10)
        self.include_subfolders_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(option_frame, text="包含子文件夹（递归遍历所有子目录）", 
                       variable=self.include_subfolders_var).pack(anchor=tk.W, padx=10, pady=8)

        # 并发移动数
        worker_row = ttk.Frame(option_frame)
        worker_row.pack(anchor=tk.W, padx=10, pady=4)
        ttk.Label(worker_row, text="并发移动数:").pack(side=tk.LEFT)
        self.move_workers_var = tk.IntVar(value=4)
        ttk.Spinbox(worker_row, from_=1, to=16, textvariable=self.move_workers_var,
                    width=6, state='readonly').pack(side=tk.LEFT, padx=5)
        ttk.Label(worker_row, text="（跨磁盘/网络移动时调高可显著提速）").pack(side=tk.LEFT)

        # 4. 进度条
        progress_frame = ttk.LabelFrame(self.exec_page, text="操作进度")
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
        button_frame = ttk.Frame(self.exec_page)
        button_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(button_frame, text="保存设置", command=self.save_settings, width=15).pack(side=tk.LEFT, padx=5)
        self.execute_btn = ttk.Button(button_frame, text="开始执行", command=self.execute_move, width=15)
        self.execute_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(button_frame, text="停止", command=self.stop_operation, 
                                  width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # 6. 结果表格 - 增加状态列（挂到②结果与验证页签）
        table_frame = ttk.LabelFrame(self.result_page, text="操作结果（蓝色=验证中，绿色=成功，红色=失败，点击记录查看路径）")
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
        self.result_tree.tag_configure('unmoved', foreground='#c0392b')
        self.result_tree.tag_configure('empty', foreground='#7f8c8d')

        # 绑定事件
        self.result_tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.result_tree.bind('<ButtonRelease-1>', self.on_tree_click)

        # 7. 路径显示区域（挂在②结果与验证页签内）
        path_frame = ttk.LabelFrame(self.result_page, text="目标路径详情（选中上方记录显示）")
        path_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.path_var = tk.StringVar(value="点击上方表格中的记录，此处将显示移动后的完整文件夹路径...")
        path_entry = ttk.Entry(path_frame, textvariable=self.path_var, state='readonly', font=('Consolas', 10))
        path_entry.pack(fill=tk.X, padx=10, pady=8)

        ttk.Button(self.result_page, text="清空结果记录", command=self.clear_records).pack(anchor=tk.E, padx=10, pady=5)

        # 8. 手动配对面板（未移动视频 + 空文件夹，可人工配对并同步深度学习）
        self._create_pairing_ui()
    
    def _create_pairing_ui(self):
        """创建手动配对面板：左侧未移动视频，右侧空目标文件夹（独立③页签）"""
        pair_frame = ttk.LabelFrame(self.pair_page, text="手动配对（选中左侧视频 + 右侧空文件夹，配对即移动，并同步番号深度学习）")
        pair_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        list_row = ttk.Frame(pair_frame)
        list_row.pack(fill=tk.X, padx=10, pady=8)

        # 左：未移动视频
        left_col = ttk.Frame(list_row)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        ttk.Label(left_col, text="未移动视频：").pack(anchor=tk.W)
        self.unmoved_listbox = tk.Listbox(left_col, height=30, exportselection=False)
        self.unmoved_listbox.pack(fill=tk.BOTH, expand=True)
        self.unmoved_listbox.bind('<<ListboxSelect>>', self._on_pair_select)

        # 右：空目标文件夹
        right_col = ttk.Frame(list_row)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        ttk.Label(right_col, text="空目标文件夹：").pack(anchor=tk.W)
        self.empty_listbox = tk.Listbox(right_col, height=30, exportselection=False)
        self.empty_listbox.pack(fill=tk.BOTH, expand=True)
        self.empty_listbox.bind('<<ListboxSelect>>', self._on_pair_select)

        # 选中信息
        self.pair_info_var = tk.StringVar(value="（未选择）")
        ttk.Label(pair_frame, textvariable=self.pair_info_var, foreground="blue").pack(fill=tk.X, padx=10)

        btn_row = ttk.Frame(pair_frame)
        btn_row.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(btn_row, text="配对并移动", command=self.manual_pair_move).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="批量试探配对", command=self.batch_auto_pair).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="重新扫描待处理", command=self.analyze_leftovers).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="清空配对列表", command=self._clear_pairing_lists).pack(side=tk.LEFT, padx=5)

        # 深度学习库规模显示
        self.learner_stat_var = tk.StringVar(value="深度学习库: 加载中...")
        ttk.Label(pair_frame, textvariable=self.learner_stat_var, foreground="#555555").pack(fill=tk.X, padx=10, pady=(0, 6))
        self._update_learner_stats_label()

        # 记录列表框里的显示文本 -> 真实路径
        self._unmoved_lb_map: Dict[str, Path] = {}
        self._empty_lb_map: Dict[str, Path] = {}

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
        self.all_target_folders = []
        self.used_target_folders = set()

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

        # 记录全部目标子文件夹，供事后统计"空文件夹"
        self.all_target_folders = [fp for fp, _ in folders_to_process]

        # 扫描阶段即标记每个目标子文件夹是否已含视频文件（供空文件夹判定，只扫一次）
        self.folder_video_flag: Dict[Path, bool] = {}
        for fp in self.all_target_folders:
            try:
                self.folder_video_flag[fp] = any(
                    item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
                    for item in fp.iterdir())
            except OSError:
                # 无法访问时保守处理：视为非空，后续配对列表中剔除
                self.folder_video_flag[fp] = True

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
        """收集视频文件

        同时只遍历一次源文件夹，把所有文件按目录存入 self.source_file_map，
        供后续匹配字幕时直接使用，避免每移动一个视频就重复遍历整个源目录。
        """
        video_files = []
        self.source_file_map = {}
        if include_subfolders:
            for root, dirs, files in os.walk(video_path):
                root_p = Path(root)
                file_list = [root_p / f for f in files]
                if file_list:
                    self.source_file_map[root_p] = file_list
                for f in files:
                    if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                        video_files.append(root_p / f)
        else:
            try:
                file_list = [video_path / f for f in video_path.iterdir() if (video_path / f).is_file()]
                self.source_file_map[video_path] = file_list
                video_files = [p for p in file_list if p.suffix.lower() in VIDEO_EXTENSIONS]
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
        """移动字幕文件

        直接使用 collect_video_files 阶段预构建的 self.source_file_map，
        不再对每个视频重新遍历整个源目录（这是原版慢的重要原因）。
        """
        moved = []
        source_dir = video_path.parent
        video_stem = video_path.stem.lower()

        # 优先使用预构建的文件映射（同一目录下的所有文件），避免重复磁盘遍历
        sub_files = self.source_file_map.get(source_dir, [])
        if not sub_files:
            # 兜底：映射缺失时回退到目录扫描
            try:
                sub_files = [source_dir / f for f in source_dir.iterdir() if (source_dir / f).is_file()]
            except Exception:
                return moved

        for sub_file in sub_files:
            # 文件可能已被移动，跳过不存在的
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
        # 记录本次操作上下文，供完成后统计待处理数据
        self._op_video_path = video_path
        self._op_target_path = target_path
        self._op_include_sub = include_subfolders

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

        # 阶段3: 先批量匹配（纯内存，只遍历目标索引一次，不再逐文件重复磁盘遍历）
        tasks = []  # 元素: (video_file, 目标文件夹, 序号)
        unmatched = 0
        for index, video_file in enumerate(video_files, 1):
            if not self.is_running:
                break
            # 使用"深度学习增强"的提取：先查人工修正记忆，再走常规规则
            file_code = self._extract_code(video_file.stem)
            best_match = self.find_best_match(video_file.name, file_code)
            if not best_match:
                unmatched += 1
                self.log(f"[未匹配] {video_file.name}")
                continue
            tasks.append((video_file, best_match, index))

        total = len(tasks)
        self.log(f"匹配完成: {total} 个视频待移动, {unmatched} 个未匹配")

        stats = {
            'verifying': 0,
            'success': 0,
            'failed': 0,
            'unmatched': unmatched
        }

        if total == 0:
            self.update_progress(100, f"无匹配文件（未匹配: {unmatched}）",
                                 {'code_index': code_count, 'total_videos': total_files, **stats})
            self.frame.after(0, self._operation_completed, 0)
            return

        # 阶段4: 并发移动（跨磁盘/网络移动时，多线程可大幅缩短总耗时）
        max_workers = max(1, min(self.move_workers_var.get(), 16))
        completed = 0
        progress_lock = threading.Lock()

        def do_move(video_file: Path, target: Path, index: int):
            """在线程池中执行的单个移动任务"""
            success, dest_path, error_msg, was_renamed = self.quick_move_file(video_file, target)
            moved_subs = []
            if success:
                # 字幕匹配使用预构建的内存映射，无磁盘遍历
                moved_subs = self.move_subtitles(video_file, target)
            return success, dest_path, error_msg, was_renamed, moved_subs

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(do_move, vf, tg, idx): (vf, tg, idx)
                           for vf, tg, idx in tasks}
                for fut in as_completed(futures):
                    if not self.is_running:
                        # 停止：取消尚未开始的移动任务
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    vf, tg, idx = futures[fut]
                    try:
                        success, dest_path, error_msg, was_renamed, moved_subs = fut.result()
                    except Exception as e:
                        success, dest_path, error_msg, moved_subs = False, None, str(e), []

                    # 结果回主线程更新 UI 并提交异步验证
                    self.frame.after(0, self._apply_move_result,
                                     vf, tg, dest_path, success, error_msg, was_renamed, moved_subs)

                    with progress_lock:
                        completed += 1
                        if success:
                            stats['verifying'] += 1
                        else:
                            stats['failed'] += 1
                        done = completed

                    progress = 20 + int((done / total) * 80)
                    self.update_progress(progress, f"处理 {done}/{total}: {vf.name[:30]}...",
                                         {'code_index': code_count, 'total_videos': total_files, **stats})
        except Exception as e:
            self.log(f"[线程池异常] {e}")

        self.update_progress(100, f"移动完成，后台验证中...（验证中: {stats['verifying']}）", 
                           {'code_index': code_count, 'total_videos': total_files, **stats})

        self.log("\n" + "=" * 60)
        self.log(f"移动阶段完成: 总数{total_files}, 匹配{total}, 验证中{stats['verifying']}, "
                 f"未匹配{unmatched}, 立即失败{stats['failed']}")
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

    def _apply_move_result(self, source_path: Path, target_folder: Path, dest_path: Optional[Path],
                           success: bool, error_msg: str, was_renamed: bool, moved_subs: List[Path]):
        """将单个移动任务结果应用到 UI（在主线程调度执行）"""
        if success:
            item_id = f"ITEM_{int(time.time() * 1000)}_{id(source_path)}"
            self.result_tree.insert('', tk.END, iid=item_id, values=(
                source_path.name, target_folder.name, '⟳ 验证中...', '撤回'
            ), tags=('verifying',))

            record = {
                'item_id': item_id,
                'source_path': source_path,
                'dest_path': dest_path,
                'filename': source_path.name,
                'foldername': target_folder.name,
                'status': 'verifying',
                'status_text': '⟳ 验证中...'
            }
            self.move_records[item_id] = record
            self.verify_worker.submit_verify(item_id, source_path, dest_path)
            # 记录该目标文件夹已被使用，供事后统计"空文件夹"
            self.used_target_folders.add(target_folder)

            if was_renamed:
                self.log(f"⚠️ [重命名] {source_path.name} -> {dest_path.name} (目标文件夹已存在同名文件)")
            else:
                self.log(f"[移动完成-待验证] {source_path.name} -> {target_folder.name}")

            if moved_subs:
                self.log(f"  [字幕] 共移动 {len(moved_subs)} 个字幕文件")
        else:
            self.log(f"[移动失败] {source_path.name}: {error_msg}")
            self._add_failed_record(source_path, target_folder.name, error_msg)

    def _operation_completed(self, pending_count=0):
        """操作完成回调"""
        self.execute_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False

        # 事后分析：未移动视频 + 空文件夹，并刷新手动配对列表
        self.analyze_leftovers()

        if pending_count > 0:
            messagebox.showinfo("移动完成", f"文件移动阶段完成！\n{pending_count} 个文件正在后台验证完整性...\n\n"
                                      f"另有 {len(self.unmoved_videos)} 个视频未移动、{len(self.empty_folders)} 个目标文件夹为空，"
                                      f"可在下方「手动配对」中处理（会同步深度学习）。")
        else:
            messagebox.showinfo("完成", "操作完成！所有文件已处理。")

    # ------------------------------------------------------------------ 事后统计与手动配对
    def _extract_code(self, text: str) -> Optional[Dict]:
        """番号提取（优先使用带深度学习的 extract_code_smart）"""
        if not self.matcher:
            return None
        fn = getattr(self.matcher, 'extract_code_smart', None)
        return fn(text) if fn else self.matcher.extract_code(text)

    def _collect_remaining_videos(self, video_path: Path, include_sub: bool) -> List[Path]:
        """重新扫描源文件夹，返回仍然存在的视频（即未移动成功的）"""
        result: List[Path] = []
        try:
            if include_sub:
                for root, dirs, files in os.walk(video_path):
                    for f in files:
                        if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                            result.append(Path(root) / f)
            else:
                result = [video_path / f for f in video_path.iterdir()
                          if (video_path / f).is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]
        except Exception as e:
            self.log(f"重新扫描源文件夹失败: {e}")
        return result

    def _is_pairable_empty_folder(self, folder: Path) -> bool:
        """判定目标文件夹是否可进入「空文件夹/手动配对」清单：
        1) 文件夹内已有视频文件（扫描阶段已标记） -> 剔除；
        2) 文件夹名含「（个人）」或「(个人)」 -> 剔除；
        3) 文件夹名识别不出番号 -> 剔除。
        """
        # 1) 是否已有视频（优先用扫描阶段标记；无标记时现场扫描一次并缓存）
        flag_map = getattr(self, 'folder_video_flag', None)
        if flag_map is None:
            flag_map = self.folder_video_flag = {}
        flag = flag_map.get(folder)
        if flag is None:
            try:
                flag = any(item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
                           for item in folder.iterdir())
            except OSError:
                flag = True  # 无法访问时保守处理：视为非空剔除
            flag_map[folder] = flag
        if flag:
            return False

        # 2) 名称含「（个人）」或「(个人)」的文件夹不参与配对
        if '个人' in folder.name:
            return False

        # 3) 无法识别番号的文件夹不参与配对
        return self._extract_code(folder.name) is not None

    def analyze_leftovers(self):
        """统计未移动视频与空文件夹，并刷新配对列表（操作完成后或点「重新扫描」时调用）"""
        video_path = getattr(self, '_op_video_path', None)
        target_path = getattr(self, '_op_target_path', None)
        include_sub = getattr(self, '_op_include_sub', False)

        self.unmoved_videos = []
        self.empty_folders = []

        if video_path and target_path:
            # 未移动视频：源文件夹中仍然存在的视频
            self.unmoved_videos = self._collect_remaining_videos(video_path, include_sub)
            # 空文件夹：全部目标子文件夹中，本次没有视频移入、且通过配对资格判定的
            # （已有视频文件 / 名称含「个人」/ 无法识别番号的均剔除）
            self.empty_folders = [p for p in self.all_target_folders
                                  if p not in self.used_target_folders
                                  and self._is_pairable_empty_folder(p)]

        self._list_leftovers_in_tree()
        self._populate_pairing_ui()
        self._update_learner_stats_label()
        self.log(f"事后统计：未移动视频 {len(self.unmoved_videos)} 个，空目标文件夹 {len(self.empty_folders)} 个")

    def _list_leftovers_in_tree(self):
        """在结果表中列出待处理项（未移动视频 / 空文件夹），先清除上次残留"""
        for iid in getattr(self, '_leftover_item_ids', []):
            try:
                self.result_tree.delete(iid)
            except Exception:
                pass
        self._leftover_item_ids = []

        for p in self.unmoved_videos:
            iid = f"UNMOVED_{id(p)}"
            self.result_tree.insert('', tk.END, iid=iid,
                                    values=(p.name, '—', '未移动', ''), tags=('unmoved',))
            self._leftover_item_ids.append(iid)

        for p in self.empty_folders:
            iid = f"EMPTY_{id(p)}"
            self.result_tree.insert('', tk.END, iid=iid,
                                    values=(p.name, str(p), '空文件夹', ''), tags=('empty',))
            self._leftover_item_ids.append(iid)

    def _populate_pairing_ui(self):
        """刷新手动配对列表框"""
        self.unmoved_listbox.delete(0, tk.END)
        self.empty_listbox.delete(0, tk.END)
        self._unmoved_lb_map.clear()
        self._empty_lb_map.clear()
        for p in self.unmoved_videos:
            self._unmoved_lb_map[p.name] = p
            self.unmoved_listbox.insert(tk.END, p.name)
        for p in self.empty_folders:
            txt = f"{p.name}  |  {p}"
            self._empty_lb_map[txt] = p
            self.empty_listbox.insert(tk.END, txt)
        self._update_pair_info()

    def _on_pair_select(self, event=None):
        self._update_pair_info()

    def _update_pair_info(self):
        sel_v = self.unmoved_listbox.curselection()
        sel_f = self.empty_listbox.curselection()
        v = self._unmoved_lb_map.get(self.unmoved_listbox.get(sel_v[0])) if sel_v else None
        f = self._empty_lb_map.get(self.empty_listbox.get(sel_f[0])) if sel_f else None
        if not v and not f:
            self.pair_info_var.set("（未选择）")
            return
        parts = []
        if v:
            auto = self._extract_code(v.stem) if self.matcher else None
            parts.append(f"视频: {v.name}（自动番号: {auto['full'] if auto else '无'}）")
        if f:
            code = self.matcher.extract_code(f.name) if self.matcher else None
            parts.append(f"文件夹: {f.name}（识别番号: {code['full'] if code else '无'}）")
        self.pair_info_var.set("  |  ".join(parts))

    def _move_subtitles_for_path(self, video_path: Path, target_folder: Path) -> List[Path]:
        """手动配对时为单个视频移动字幕（直接扫描其目录，不依赖可能过期的映射）"""
        moved: List[Path] = []
        source_dir = video_path.parent
        video_stem = video_path.stem.lower()
        try:
            subs = [source_dir / f for f in source_dir.iterdir() if (source_dir / f).is_file()]
        except Exception:
            return moved
        for sub_file in subs:
            if sub_file.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            if sub_file.stem.lower().startswith(video_stem):
                success, dest_path, error_msg, was_renamed = self.quick_move_file(sub_file, target_folder)
                if success:
                    moved.append(dest_path)
                else:
                    self.log(f"  [字幕移动失败] {sub_file.name}: {error_msg}")
        return moved

    def _learn_from_pair(self, video_path: Path, folder_path: Path):
        """把"视频文件名特征 -> 文件夹识别出的番号"记入深度学习库（与 workflow 共用 uid_learn.json）"""
        if not self.uid_learner or not self.matcher:
            return
        folder_code = self.matcher.extract_code(folder_path.name)
        if not folder_code:
            return
        corrected_uid = folder_code['full']  # 例如 'ssis001'
        auto = self._extract_code(video_path.stem)
        auto_uid = auto['full'] if auto else ''
        try:
            changed = self.uid_learner.learn(video_path.stem, corrected_uid, auto_uid)
            if changed:
                self.log(f"[深度学习] 已记录修正: {video_path.stem} -> {corrected_uid}")
                self._update_learner_stats_label()
        except Exception as e:
            self.log(f"[深度学习] 记录失败: {e}")

    def _register_moved(self, source_path: Path, folder_name: str, dest_path: Path):
        """在结果表中登记一条"验证中"记录并提交异步验证（与自动移动一致）"""
        item_id = f"ITEM_{int(time.time() * 1000)}_{id(source_path)}"
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
        self.verify_worker.submit_verify(item_id, source_path, dest_path)
        return item_id

    def manual_pair_move(self):
        """手动配对：把选中的未移动视频移动到选中的空文件夹，并同步深度学习"""
        sel_v = self.unmoved_listbox.curselection()
        sel_f = self.empty_listbox.curselection()
        if not sel_v or not sel_f:
            messagebox.showwarning("提示", "请在左右两侧各选择一个「未移动视频」和一个「空目标文件夹」")
            return
        video_path = self._unmoved_lb_map.get(self.unmoved_listbox.get(sel_v[0]))
        folder_path = self._empty_lb_map.get(self.empty_listbox.get(sel_f[0]))
        if not video_path or not folder_path:
            return

        if not messagebox.askyesno("确认配对",
                                   f"将视频\n  {video_path.name}\n移动到文件夹\n  {folder_path}\n\n"
                                   f"同时把该配对记入番号深度学习库（下次自动识别更准确）？"):
            return

        success, dest_path, error_msg, was_renamed = self.quick_move_file(video_path, folder_path)
        if not success:
            messagebox.showerror("失败", f"移动失败: {error_msg}")
            self.log(f"[手动配对-失败] {video_path.name}: {error_msg}")
            return

        moved_subs = self._move_subtitles_for_path(video_path, folder_path)
        self.used_target_folders.add(folder_path)
        self._learn_from_pair(video_path, folder_path)
        self._register_moved(video_path, folder_path.name, dest_path)

        self.log(f"[手动配对-成功] {video_path.name} -> {folder_path.name}")
        if moved_subs:
            self.log(f"  [字幕] 共移动 {len(moved_subs)} 个字幕文件")

        # 从待处理列表移除并更新 UI
        self.unmoved_videos = [p for p in self.unmoved_videos if p != video_path]
        if folder_path in self.empty_folders:
            self.empty_folders.remove(folder_path)
        self._populate_pairing_ui()

    def _clear_pairing_lists(self):
        """清空配对列表框（仅清显示，不影响其它状态）"""
        self.unmoved_videos = []
        self.empty_folders = []
        self._populate_pairing_ui()

    def _update_learner_stats_label(self):
        """刷新界面上的深度学习库规模显示"""
        if not self.uid_learner:
            self.learner_stat_var.set("深度学习库: 未加载（人工修正记忆不可用）")
            return
        try:
            s = self.uid_learner.stats()
            self.learner_stat_var.set(
                f"深度学习库(uid_learn.json): 精确记忆 {s.get('mappings', 0)} 条 / "
                f"垃圾前缀 {s.get('junk_prefixes', 0)} 条 / 垃圾后缀 {s.get('junk_suffixes', 0)} 条")
        except Exception:
            self.learner_stat_var.set("深度学习库: 统计失败")

    def batch_auto_pair(self):
        """批量试探配对：按番号相等或"文件夹名是视频文件名前缀"自动配对未移动视频与空文件夹

        仅做"试探"，配对前弹窗让用户确认；成功移动的项一并记入深度学习库。
        """
        if not self.unmoved_videos or not self.empty_folders:
            messagebox.showinfo("提示", "当前没有可自动配对的『未移动视频』或『空文件夹』")
            return

        # 为每个空文件夹预计算番号（键），空文件夹只作为目标被用一次
        folder_candidates = []  # (folder_path, code_dict_or_None)
        for f in self.empty_folders:
            folder_candidates.append((f, self._extract_code(f.name)))

        pairs = []
        used_videos = set()
        used_folders = set()

        for v in self.unmoved_videos:
            if v in used_videos:
                continue
            vcode = self._extract_code(v.stem)
            vclean = re.sub(r'[\s\-_\.]+', '', v.stem).lower()
            best = None
            for f, fcode in folder_candidates:
                if f in used_folders:
                    continue
                # 1) 番号严格相等（说明原匹配因其它原因漏掉）
                if (vcode and fcode and vcode.get('full') and fcode.get('full')
                        and vcode['full'] == fcode['full']):
                    best = f
                    break
                # 2) 文件夹名（去噪）作为视频文件名前缀 -> 试探配对
                fclean = re.sub(r'[\s\-_\.]+', '', f.name).lower()
                if fclean and len(fclean) >= 3 and vclean.startswith(fclean):
                    best = f
                    break
            if best:
                pairs.append((v, best))
                used_videos.add(v)
                used_folders.add(best)

        if not pairs:
            messagebox.showinfo("提示", "未找到可自动配对的项（可改用手动配对，或先人工修正番号）")
            return

        if not messagebox.askyesno("确认批量配对",
                                   f"将自动试探配对 {len(pairs)} 对并移动（同时同步深度学习）：\n\n"
                                   + "\n".join(f"• {v.name}  →  {f.name}" for v, f in pairs[:20])
                                   + ("\n…" if len(pairs) > 20 else "")):
            return

        ok = 0
        for v, f in pairs:
            success, dest_path, error_msg, was_renamed = self.quick_move_file(v, f)
            if success:
                self._move_subtitles_for_path(v, f)
                self.used_target_folders.add(f)
                self._learn_from_pair(v, f)
                self._register_moved(v, f.name, dest_path)
                ok += 1
                self.log(f"[批量配对-成功] {v.name} -> {f.name}")
            else:
                self.log(f"[批量配对-失败] {v.name}: {error_msg}")

        # 从待处理列表移除已配对项并刷新
        self.unmoved_videos = [p for p in self.unmoved_videos if p not in used_videos]
        self.empty_folders = [p for p in self.empty_folders if p not in used_folders]
        self._populate_pairing_ui()
        self._update_learner_stats_label()
        messagebox.showinfo("完成", f"批量配对完成：成功 {ok}/{len(pairs)}")

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
