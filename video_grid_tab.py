#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频宫格缩略图生成器 Tab 页
支持独立运行和通过 main.py 嵌入运行
功能：将视频文件转换为宫格缩略图（多帧拼接）
优化：支持智能跳过已存在的缩略图，提供覆盖选项
      修复竖拍视频黑边问题，自适应宽高比
      底部增加视频信息文字说明
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import json
import re
import math
import threading
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Set, Callable, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont, ImageTk
import numpy as np
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

# 尝试导入 cv2
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    cv2 = None

# 配置管理器（与 file_organize_tab.py 和 video_browser_tab.py 共享）
class ConfigManager:
    """统一配置管理器 - 单例模式"""
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
        self._config: Dict[str, Any] = {}
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        # 本实例修改过的分区：写盘时只覆盖这些分区，避免用旧副本覆盖其他模块的数据
        self._dirty_sections: Set[str] = set()
        self._load_config()
    
    def _get_default_config_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            "app_config.json"
        )

    def _read_disk(self) -> Dict[str, Any]:
        """读取磁盘上的最新配置内容"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"读取配置失败: {e}")
        return {}

    def _load_config(self):
        self._config = self._read_disk()
    
    def save_config(self, show_error: bool = False) -> bool:
        """写盘前先与磁盘最新内容合并，只覆盖本实例改动过的分区。

        原因：file_organize_tab / web_grab_tab / video_grid_tab 各自持有独立的
        单例，却共用同一个 app_config.json。若直接把整份内存副本写回，会互相
        覆盖（曾导致“保存到指定文件夹”的路径被其他模块用旧数据写空）。
        """
        try:
            merged = self._read_disk()
            for section in self._dirty_sections:
                if section in self._config:
                    merged[section] = self._config[section]
            self._config = merged
            self._dirty_sections.clear()
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
    
    def get(self, section: str, key: str, default=None):
        if section not in self._config:
            self._config[section] = {}
        return self._config[section].get(key, default)
    
    def set(self, section: str, key: str, value: Any, auto_save: bool = True):
        if section not in self._config:
            self._config[section] = {}
        self._config[section][key] = value
        self._dirty_sections.add(section)
        if auto_save:
            self.save_config()
    
    def get_section(self, section: str, default: Optional[Dict] = None) -> Dict:
        """读取分区时以磁盘最新内容为准，避免读到本实例启动时的旧副本"""
        disk = self._read_disk()
        if section in disk:
            self._config[section] = disk[section]
            return disk[section]
        return self._config.get(section, default or {})
    
    def set_section(self, section: str, data: Dict, auto_save: bool = True):
        # 以磁盘最新内容为基准，只更新本分区
        self._config = self._read_disk()
        self._config[section] = data
        self._dirty_sections = {section}
        if auto_save:
            self.save_config()


@dataclass
class VideoGridConfig:
    """视频宫格缩略图配置"""
    root_dir: str = ""
    include_subdirs: bool = True
    grid_x: int = 4
    grid_y: int = 4
    target_width: int = 1600
    spacing: int = 10
    jpg_quality: int = 90
    overwrite_existing: bool = False  # 新增：是否覆盖已存在的缩略图
    info_font_size: int = 20  # 新增：信息文字大小
    video_exts: List[str] = field(default_factory=lambda: [
        'mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'rmvb', 'ts', 'm2ts', 'mpg', 'mpeg'
    ])
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'VideoGridConfig':
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class GridThumbnailPanel:
    """宫格缩略图生成器面板（由 VideoGridTab 容器承载，作为子选项卡）"""
    
    CONFIG_SECTION = "video_grid"
    
    def __init__(self, parent, controller=None, config_manager: Optional[ConfigManager] = None):
        self.parent = parent
        self.controller = controller
        self.is_standalone = controller is None
        
        # 配置管理器
        if config_manager:
            self.config_manager = config_manager
        elif controller and hasattr(controller, 'config_manager'):
            self.config_manager = controller.config_manager
        else:
            self.config_manager = ConfigManager()
        
        # 状态变量
        self.is_running = False
        self.stop_requested = False
        self.total_videos = 0
        self.processed_count = 0
        self.skipped_videos: List[str] = []  # 记录被跳过的视频
        
        # 检查 cv2 可用性
        if not HAS_CV2:
            messagebox.showwarning("警告", "未安装 OpenCV (cv2)，视频处理功能不可用。\n请运行: pip install opencv-python")
        
        # 创建界面（根据模式动态创建）
        self._create_widgets()
        self._load_from_config_manager()
        self._bind_events()
        
        # 尝试加载中文字体
        self._load_chinese_font()
    
    def _load_chinese_font(self):
        """尝试加载系统中文字体"""
        self.chinese_font = None
        possible_fonts = [
            "C:/Windows/Fonts/simhei.ttf",  # 黑体
            "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
            "C:/Windows/Fonts/simsun.ttc",  # 宋体
            "C:/Windows/Fonts/simkai.ttf",  # 楷体
        ]
        
        try:
            font_size = getattr(self, 'font_size_var', None)
            size = font_size.get() if font_size else 20
            
            for font_path in possible_fonts:
                if os.path.exists(font_path):
                    self.chinese_font = ImageFont.truetype(font_path, size)
                    break
            
            if self.chinese_font is None:
                # 尝试使用默认字体，但可能不支持中文
                self.chinese_font = ImageFont.load_default()
        except Exception:
            self.chinese_font = ImageFont.load_default()
    
    def _log(self, message: str, level: str = "info"):
        """记录日志 - 双模式适配"""
        timestamp = self._get_timestamp()
        log_line = f"[{timestamp}] {message}"
        
        if not self.is_standalone and self.controller and hasattr(self.controller, 'log'):
            # 嵌入模式：发送到主程序的日志 Tab
            self.controller.log(log_line)
        else:
            # 独立模式：输出到控制台，如果有日志文本框则也显示
            print(log_line)
            # 独立模式下 log_text 存在，嵌入模式下可能不存在
            if hasattr(self, 'log_text') and self.log_text.winfo_exists():
                tag = level
                self.log_text.insert(tk.END, log_line + "\n", tag)
                self.log_text.see(tk.END)
    
    def _get_timestamp(self) -> str:
        """获取时间戳"""
        import datetime
        return datetime.datetime.now().strftime("%H:%M:%S")
    
    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小显示"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
    
    def _get_video_codec(self, video_path: str) -> str:
        """获取视频编码格式"""
        if not HAS_CV2:
            return "未知"
        
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return "未知"
            
            # 获取四字符编码
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])
            cap.release()
            
            # 常见编码映射
            codec_map = {
                'avc1': 'H.264',
                'H264': 'H.264',
                'X264': 'H.264',
                'hev1': 'HEVC/H.265',
                'hvc1': 'HEVC/H.265',
                'HEVC': 'HEVC/H.265',
                'vp09': 'VP9',
                'av01': 'AV1',
                'mpg4': 'MPEG-4',
                'MP42': 'MPEG-4',
                'MJPG': 'Motion JPEG',
            }
            
            return codec_map.get(codec, codec)
        except Exception:
            return "未知"
    
    def _create_widgets(self):
        """创建界面 - 根据运行模式动态调整"""
        self.frame = ttk.Frame(self.parent)
        
        # 根据模式选择布局
        if self.is_standalone:
            # 独立模式：使用 PanedWindow 分割左右（设置区 | 日志区）
            main_paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
            main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            
            # 创建设置区域
            left_frame = self._create_settings_frame(main_paned)
            main_paned.add(left_frame, weight=1)
            
            # 创建日志区域（独立模式专用）
            right_frame = self._create_log_frame(main_paned)
            main_paned.add(right_frame, weight=1)
        else:
            # 嵌入模式：只显示设置区域，日志在主程序的"操作日志"Tab
            left_frame = self._create_settings_frame(self.frame)
            left_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_settings_frame(self, parent) -> ttk.Frame:
        """创建设置区域"""
        left_frame = ttk.Frame(parent, width=500)
        left_frame.pack_propagate(False)
        
        # 参数设置框架
        settings_frame = ttk.LabelFrame(left_frame, text="参数设置", padding=10)
        settings_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # 文件夹路径
        row = 0
        ttk.Label(settings_frame, text="视频文件夹:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.dir_var = tk.StringVar()
        ttk.Entry(settings_frame, textvariable=self.dir_var, width=40).grid(row=row, column=1, sticky=tk.EW, padx=5)
        ttk.Button(settings_frame, text="浏览...", command=self._browse_folder, width=8).grid(row=row, column=2, padx=5)
        
        # 包含子目录
        row += 1
        self.subdirs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="包括子目录（处理所有子文件夹中的视频）", 
                       variable=self.subdirs_var).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=5)
        
        # ===== 新增：跳过/覆盖选项 =====
        row += 1
        self.overwrite_var = tk.BooleanVar(value=False)
        overwrite_cb = ttk.Checkbutton(
            settings_frame, 
            text="覆盖已存在的缩略图（默认跳过同名文件）", 
            variable=self.overwrite_var
        )
        overwrite_cb.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=5)
        # 添加工具提示
        self._create_tooltip(overwrite_cb, "勾选后：如果存在同名.jpg文件，将重新生成并覆盖\n不勾选：自动跳过已存在的缩略图，提高处理速度")
        
        # 横向格数
        row += 1
        ttk.Label(settings_frame, text="横向格数:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.grid_x_var = tk.IntVar(value=4)
        ttk.Spinbox(settings_frame, from_=1, to=10, textvariable=self.grid_x_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=5)
        
        # 纵向格数
        row += 1
        ttk.Label(settings_frame, text="纵向格数:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.grid_y_var = tk.IntVar(value=4)
        ttk.Spinbox(settings_frame, from_=1, to=10, textvariable=self.grid_y_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=5)
        
        # 输出图片宽度
        row += 1
        ttk.Label(settings_frame, text="输出宽度:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.width_var = tk.IntVar(value=1600)
        ttk.Spinbox(settings_frame, from_=256, to=4096, increment=128, 
                   textvariable=self.width_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=5)
        ttk.Label(settings_frame, text="像素").grid(row=row, column=2, sticky=tk.W)
        
        # 间隔宽度
        row += 1
        ttk.Label(settings_frame, text="间隔宽度:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.spacing_var = tk.IntVar(value=10)
        ttk.Spinbox(settings_frame, from_=0, to=50, textvariable=self.spacing_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=5)
        ttk.Label(settings_frame, text="像素").grid(row=row, column=2, sticky=tk.W)
        
        # JPG 质量
        row += 1
        ttk.Label(settings_frame, text="JPG质量:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.quality_var = tk.IntVar(value=90)
        quality_scale = ttk.Scale(settings_frame, from_=50, to=100, orient=tk.HORIZONTAL, 
                                 variable=self.quality_var, length=200)
        quality_scale.grid(row=row, column=1, sticky=tk.W, padx=5)
        self.quality_label = ttk.Label(settings_frame, text="90%")
        self.quality_label.grid(row=row, column=2, sticky=tk.W)
        self.quality_var.trace('w', lambda *args: self._update_quality_label())
        
        # 新增：字体大小设置
        row += 1
        ttk.Label(settings_frame, text="信息字体:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.font_size_var = tk.IntVar(value=20)
        ttk.Spinbox(settings_frame, from_=12, to=48, textvariable=self.font_size_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=5)
        ttk.Label(settings_frame, text="字号").grid(row=row, column=2, sticky=tk.W)
        self._create_tooltip(ttk.Label(settings_frame, text="?"), "设置底部视频信息的文字大小，建议20-24")
        
        # 视频格式
        row += 1
        ttk.Label(settings_frame, text="视频格式:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.exts_var = tk.StringVar(value="mp4,mkv,avi,mov,wmv,flv,rmvb,ts,m2ts,mpg,mpeg")
        ttk.Entry(settings_frame, textvariable=self.exts_var, width=40).grid(row=row, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=5)
        ttk.Label(settings_frame, text="(逗号分隔)").grid(row=row+1, column=1, sticky=tk.W)
        
        # 配置列权重
        settings_frame.columnconfigure(1, weight=1)
        
        # 按钮区域
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=10)
        
        self.start_btn = tk.Button(btn_frame, text="▶ 开始生成", command=self._start_process,
                                  bg='#4CAF50', fg='white', font=('Microsoft YaHei', 10, 'bold'),
                                  width=15, height=1)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = tk.Button(btn_frame, text="⏹ 停止", command=self._stop_process,
                                 bg='#f44336', fg='white', font=('Microsoft YaHei', 10, 'bold'),
                                 width=15, height=1, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="💾 保存配置", command=self._manual_save_config, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 重置", command=self._reset_config, width=12).pack(side=tk.LEFT, padx=5)
        
        # 进度条区域
        progress_frame = ttk.LabelFrame(left_frame, text="处理进度", padding=10)
        progress_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, 
                                            maximum=100, mode='determinate', length=400)
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)
        
        self.status_var = tk.StringVar(value="就绪")
        status_label = ttk.Label(progress_frame, textvariable=self.status_var, 
                                font=('Microsoft YaHei', 9, 'bold'), foreground='#2196F3')
        status_label.pack(side=tk.LEFT, padx=5)
        
        self.count_var = tk.StringVar(value="0/0")
        count_label = ttk.Label(progress_frame, textvariable=self.count_var, font=('Microsoft YaHei', 9))
        count_label.pack(side=tk.RIGHT, padx=5)
        
        return left_frame
    
    def _create_tooltip(self, widget, text):
        """为控件创建鼠标悬停提示"""
        def enter(event):
            x, y, _, _ = widget.bbox("insert")
            x += widget.winfo_rootx() + 25
            y += widget.winfo_rooty() + 25
            
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{x}+{y}")
            
            label = ttk.Label(self.tooltip, text=text, background="#ffffe0", 
                            relief="solid", borderwidth=1, wraplength=300,
                            font=('Microsoft YaHei', 9), padding=5)
            label.pack()
        
        def leave(event):
            if hasattr(self, 'tooltip') and self.tooltip.winfo_exists():
                self.tooltip.destroy()
        
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)
    
    def _create_log_frame(self, parent) -> ttk.LabelFrame:
        """创建日志区域（仅独立模式使用）"""
        right_frame = ttk.LabelFrame(parent, text="操作日志", padding=5)
        
        self.log_text = scrolledtext.ScrolledText(right_frame, wrap=tk.WORD, height=20,
                                                 font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 设置日志标签样式
        self.log_text.tag_configure("info", foreground="black")
        self.log_text.tag_configure("success", foreground="#2E7D32", font=('Consolas', 9, 'bold'))
        self.log_text.tag_configure("warning", foreground="#F57C00")
        self.log_text.tag_configure("error", foreground="#C62828", font=('Consolas', 9, 'bold'))
        self.log_text.tag_configure("highlight", foreground="#1565C0", font=('Consolas', 9, 'bold'))
        self.log_text.tag_configure("skip", foreground="#757575", font=('Consolas', 9, 'italic'))  # 新增：跳过样式
        
        # 日志按钮
        log_btn_frame = ttk.Frame(right_frame)
        log_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(log_btn_frame, text="清空", command=self._clear_log, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_btn_frame, text="保存日志", command=self._save_log, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_btn_frame, text="📋 查看跳过列表", command=self._show_skipped_list, width=15).pack(side=tk.LEFT, padx=5)
        
        return right_frame
    
    def _update_quality_label(self):
        """更新质量标签"""
        self.quality_label.config(text=f"{self.quality_var.get()}%")
    
    def _bind_events(self):
        """绑定事件自动保存"""
        self.dir_var.trace('w', lambda *args: self._auto_save_config())
        self.subdirs_var.trace('w', lambda *args: self._auto_save_config())
        self.overwrite_var.trace('w', lambda *args: self._auto_save_config())  # 新增
        self.grid_x_var.trace('w', lambda *args: self._auto_save_config())
        self.grid_y_var.trace('w', lambda *args: self._auto_save_config())
        self.width_var.trace('w', lambda *args: self._auto_save_config())
        self.spacing_var.trace('w', lambda *args: self._auto_save_config())
        self.quality_var.trace('w', lambda *args: self._auto_save_config())
        self.exts_var.trace('w', lambda *args: self._auto_save_config())
        self.font_size_var.trace('w', lambda *args: self._update_font_and_save())
    
    def _update_font_and_save(self):
        """更新字体并保存配置"""
        try:
            if self.chinese_font and hasattr(self.chinese_font, 'size'):
                # 重新加载字体以更新大小
                self._load_chinese_font()
        except:
            pass
        self._auto_save_config()
    
    def _auto_save_config(self):
        """自动保存配置（带延迟）"""
        if hasattr(self, '_save_timer'):
            self.frame.after_cancel(self._save_timer)
        self._save_timer = self.frame.after(1000, self._do_save_config)
    
    def _do_save_config(self):
        """执行保存"""
        config_data = {
            'root_dir': self.dir_var.get(),
            'include_subdirs': self.subdirs_var.get(),
            'overwrite_existing': self.overwrite_var.get(),  # 新增
            'grid_x': self.grid_x_var.get(),
            'grid_y': self.grid_y_var.get(),
            'target_width': self.width_var.get(),
            'spacing': self.spacing_var.get(),
            'jpg_quality': self.quality_var.get(),
            'info_font_size': self.font_size_var.get(),  # 新增
            'video_exts': [e.strip() for e in self.exts_var.get().split(',') if e.strip()]
        }
        self.config_manager.set_section(self.CONFIG_SECTION, config_data)
    
    def _manual_save_config(self):
        """手动保存配置"""
        self._do_save_config()
        messagebox.showinfo("成功", "配置已保存！")
        self._log("配置已手动保存", "success")
    
    def _load_from_config_manager(self):
        """从配置管理器加载"""
        section = self.config_manager.get_section(self.CONFIG_SECTION)
        if section:
            self.dir_var.set(section.get('root_dir', ''))
            self.subdirs_var.set(section.get('include_subdirs', True))
            self.overwrite_var.set(section.get('overwrite_existing', False))  # 新增：加载覆盖选项
            self.grid_x_var.set(section.get('grid_x', 4))
            self.grid_y_var.set(section.get('grid_y', 4))
            self.width_var.set(section.get('target_width', 1600))
            self.spacing_var.set(section.get('spacing', 10))
            self.quality_var.set(section.get('jpg_quality', 90))
            self.font_size_var.set(section.get('info_font_size', 20))  # 新增
            
            exts = section.get('video_exts', [])
            if exts:
                self.exts_var.set(','.join(exts))
        
        self._update_quality_label()
    
    def _reset_config(self):
        """重置配置"""
        if not messagebox.askyesno("确认", "确定要重置为默认配置吗？"):
            return
        
        default = VideoGridConfig()
        self.dir_var.set(default.root_dir)
        self.subdirs_var.set(default.include_subdirs)
        self.overwrite_var.set(default.overwrite_existing)  # 新增
        self.grid_x_var.set(default.grid_x)
        self.grid_y_var.set(default.grid_y)
        self.width_var.set(default.target_width)
        self.spacing_var.set(default.spacing)
        self.quality_var.set(default.jpg_quality)
        self.font_size_var.set(default.info_font_size)  # 新增
        self.exts_var.set(','.join(default.video_exts))
        
        self._do_save_config()
        self._log("配置已重置为默认值", "warning")
    
    def _browse_folder(self):
        """浏览文件夹"""
        folder = filedialog.askdirectory(title="选择视频文件夹")
        if folder:
            self.dir_var.set(folder)
            self._auto_save_config()
    
    def _clear_log(self):
        """清空日志（仅独立模式可用）"""
        if hasattr(self, 'log_text') and self.log_text.winfo_exists():
            self.log_text.delete(1.0, tk.END)
    
    def _save_log(self):
        """保存日志到文件（仅独立模式可用）"""
        if not (hasattr(self, 'log_text') and self.log_text.winfo_exists()):
            return
            
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.log_text.get(1.0, tk.END))
                self._log(f"日志已保存到: {file_path}", "success")
            except Exception as e:
                messagebox.showerror("错误", f"保存日志失败: {e}")
    
    def _show_skipped_list(self):
        """显示被跳过的视频列表（新增功能）"""
        if not self.skipped_videos:
            messagebox.showinfo("跳过列表", "本次运行没有跳过的视频文件。")
            return
        
        # 创建新窗口显示跳过列表
        skip_window = tk.Toplevel(self.frame)
        skip_window.title(f"已跳过的视频列表（共 {len(self.skipped_videos)} 个）")
        skip_window.geometry("600x400")
        
        text_area = scrolledtext.ScrolledText(skip_window, wrap=tk.WORD, font=('Consolas', 9))
        text_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        text_area.insert(tk.END, f"以下视频文件因已存在同名缩略图而被跳过（共 {len(self.skipped_videos)} 个）：\n\n")
        for i, path in enumerate(self.skipped_videos, 1):
            text_area.insert(tk.END, f"{i}. {path}\n")
        
        ttk.Button(skip_window, text="关闭", command=skip_window.destroy).pack(pady=5)
    
    def _get_video_exts(self) -> Set[str]:
        """获取视频扩展名集合"""
        exts = set()
        for ext in self.exts_var.get().split(','):
            ext = ext.strip().lower()
            if ext:
                if not ext.startswith('.'):
                    ext = '.' + ext
                exts.add(ext)
        return exts if exts else {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.rmvb', '.ts', '.m2ts', '.mpg', '.mpeg'}
    
    def _is_video(self, path: Path) -> bool:
        """检查是否为视频文件"""
        return path.suffix.lower() in self._get_video_exts()
    
    def _get_frames(self, video_path: str, n: int) -> List[np.ndarray]:
        """从视频等距抽取 n 帧"""
        if not HAS_CV2:
            raise ImportError("OpenCV (cv2) 未安装")
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise IOError(f"无法打开视频: {video_path}")
        
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total < n:
            cap.release()
            raise ValueError(f"总帧数 {total} 不足 {n} 帧")
        
        indices = [int(i * (total - 1) / (n - 1)) for i in range(n)]
        frames = []
        
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                frames.append(frame)
        
        cap.release()
        return frames
    
    def _build_grid(self, images: List[np.ndarray], grid_x: int, grid_y: int, 
                   target_w: int, spacing: int, video_info: dict) -> np.ndarray:
        """
        拼接宫格图像（优化版：自适应宽高比，无黑边）
        
        Args:
            images: 视频帧列表
            grid_x: 横向格数
            grid_y: 纵向格数  
            target_w: 目标输出宽度
            spacing: 间隔宽度
            video_info: 视频信息字典，包含宽度、高度用于计算宽高比
        """
        n = len(images)
        
        # 计算可用宽度（减去间隔）
        available_w = target_w - (grid_x - 1) * spacing
        base_cell_w = available_w // grid_x
        
        # 分析所有帧的实际宽高比，计算每格的目标尺寸
        cell_dimensions = []
        for img in images:
            h, w = img.shape[:2]
            aspect = w / h
            cell_dimensions.append((w, h, aspect))
        
        # 根据原始视频宽高比决定每格高度（保持原始比例，不强制16:9）
        # 如果是竖屏视频(9:16)，高度会大于宽度
        avg_aspect = sum(d[2] for d in cell_dimensions) / len(cell_dimensions) if cell_dimensions else 16/9
        
        # 计算基础单元格高度（根据平均宽高比）
        base_cell_h = int(base_cell_w / avg_aspect)
        
        # 计算整个宫格区域的高度（行高+间隔）
        grid_height = base_cell_h * grid_y + (grid_y - 1) * spacing
        
        # 计算底部信息区域高度（根据字体大小动态调整，最小100像素）
        info_height = max(100, self.font_size_var.get() * 5)
        
        # 创建画布（宫格区域 + 底部信息区域）
        total_height = grid_height + info_height
        canvas = np.ones((total_height, target_w, 3), dtype=np.uint8) * 255  # 白色背景
        
        # 缩放并放置每张小图
        for i, img in enumerate(images):
            if i >= grid_x * grid_y:
                break
            
            r, c = divmod(i, grid_x)
            h0, w0 = img.shape[:2]
            
            # 计算目标位置（居中放置）
            y_start = r * (base_cell_h + spacing)
            x_start = c * (base_cell_w + spacing)
            
            # 计算缩放比例（保持原始宽高比，填满单元格）
            scale_w = base_cell_w / w0
            scale_h = base_cell_h / h0
            scale = min(scale_w, scale_h)  # 使用较小比例确保完全在单元格内
            
            new_w = int(w0 * scale)
            new_h = int(h0 * scale)
            
            # 居中偏移
            x_offset = x_start + (base_cell_w - new_w) // 2
            y_offset = y_start + (base_cell_h - new_h) // 2
            
            # 缩放图像
            if new_w > 0 and new_h > 0:
                resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
                # 确保不越界
                y_end = min(y_offset + new_h, y_start + base_cell_h)
                x_end = min(x_offset + new_w, x_start + base_cell_w)
                roi_h = y_end - y_offset
                roi_w = x_end - x_offset
                
                if roi_h > 0 and roi_w > 0:
                    canvas[y_offset:y_end, x_offset:x_end] = resized[:roi_h, :roi_w]
        
        # 转换为PIL图像以添加文字
        return canvas, info_height, grid_height
    
    def _draw_video_info(self, canvas: np.ndarray, video_info: dict, 
                        font_size: int, grid_height: int) -> np.ndarray:
        """
        在图像底部绘制视频信息文字
        
        Args:
            canvas: 原始画布（BGR格式）
            video_info: 视频信息字典
            font_size: 字体大小
            grid_height: 宫格区域高度（文字从该区域下方开始）
        """
        # 转换为RGB用于PIL处理
        img_rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)
        
        # 重新加载字体以确保大小正确
        try:
            if os.path.exists("C:/Windows/Fonts/simhei.ttf"):
                font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", font_size)
            elif os.path.exists("C:/Windows/Fonts/msyh.ttc"):
                font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", font_size)
            else:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        # 准备文字信息
        file_name = video_info.get('filename', '未知')
        file_ext = video_info.get('ext', '未知').upper()
        codec = video_info.get('codec', '未知')
        size_str = video_info.get('size_str', '未知')
        resolution = video_info.get('resolution', '未知')
        duration = video_info.get('duration', '未知')
        
        # 文字行
        lines = [
            f"文件名: {file_name}",
            f"格式: {file_ext}  |  编码: {codec}  |  分辨率: {resolution}  |  时长: {duration}  |  大小: {size_str}"
        ]
        
        # 计算文字位置
        margin = 20
        y_start = grid_height + margin
        
        # 绘制背景条（半透明灰色背景提升可读性）
        text_area_height = len(lines) * (font_size + 8) + margin * 2
        overlay = Image.new('RGBA', pil_img.size, (255, 255, 255, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [(0, grid_height), (pil_img.width, grid_height + text_area_height)],
            fill=(240, 240, 240, 200)  # 浅灰色半透明背景
        )
        pil_img = Image.alpha_composite(pil_img.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(pil_img)
        
        # 绘制文字（黑色）
        text_color = (50, 50, 50)
        
        for i, line in enumerate(lines):
            y = y_start + i * (font_size + 8)
            draw.text((margin, y), line, font=font, fill=text_color)
        
        # 转回OpenCV格式（BGR）
        result = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return result
    
    def _safe_imwrite(self, output_path: Path, img_array: np.ndarray, quality: int = 90):
        """安全写入图像（支持 UNC 路径）"""
        img_rgb = cv2.cvtColor(img_array, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        
        try:
            # 直接尝试保存
            pil_img.save(str(output_path), 'JPEG', quality=quality, optimize=True)
            return True
        except Exception:
            pass
        
        try:
            # 使用临时文件中转
            fd, temp_path = tempfile.mkstemp(suffix='.jpg', prefix='thumb_')
            os.close(fd)
            temp_path = Path(temp_path)
            
            pil_img.save(str(temp_path), 'JPEG', quality=quality, optimize=True)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(temp_path), str(output_path))
            temp_path.unlink(missing_ok=True)
            return True
        except Exception as e:
            self._log(f"写入图像失败: {e}", "error")
            return False
    
    def _get_video_metadata(self, video_path: Path) -> dict:
        """获取视频元数据信息"""
        info = {
            'filename': video_path.stem,
            'ext': video_path.suffix[1:].upper(),
            'codec': '未知',
            'size_bytes': 0,
            'size_str': '0 B',
            'resolution': '未知',
            'duration': '未知',
            'width': 0,
            'height': 0
        }
        
        # 获取文件大小
        try:
            stat = video_path.stat()
            info['size_bytes'] = stat.st_size
            info['size_str'] = self._format_file_size(stat.st_size)
        except:
            pass
        
        if not HAS_CV2:
            return info
        
        try:
            cap = cv2.VideoCapture(str(video_path))
            if cap.isOpened():
                # 获取编码
                fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
                codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])
                codec_map = {
                    'avc1': 'H.264', 'H264': 'H.264', 'X264': 'H.264',
                    'hev1': 'H.265', 'hvc1': 'H.265', 'HEVC': 'H.265',
                    'vp09': 'VP9', 'av01': 'AV1', 'mpg4': 'MPEG-4'
                }
                info['codec'] = codec_map.get(codec, codec)
                
                # 获取分辨率
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                info['width'] = width
                info['height'] = height
                info['resolution'] = f"{width}x{height}"
                
                # 获取时长
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if fps > 0 and frame_count > 0:
                    duration_sec = frame_count / fps
                    mins, secs = divmod(int(duration_sec), 60)
                    hours, mins = divmod(mins, 60)
                    if hours > 0:
                        info['duration'] = f"{hours}:{mins:02d}:{secs:02d}"
                    else:
                        info['duration'] = f"{mins}:{secs:02d}"
                
                cap.release()
        except Exception as e:
            self._log(f"获取视频信息失败: {e}", "warning")
        
        return info
    
    def _check_existing_thumbnail(self, video_path: Path) -> Optional[dict]:
        """检查是否已存在缩略图，返回文件信息"""
        out_path = video_path.parent / (video_path.stem + '.jpg')
        if out_path.exists():
            stat = out_path.stat()
            return {
                'path': out_path,
                'size': stat.st_size,
                'mtime': stat.st_mtime,
                'size_str': self._format_file_size(stat.st_size)
            }
        return None
    
    def _process_single_video(self, video_path: Path) -> tuple:
        """处理单个视频（优化版：支持智能跳过和覆盖选项，带视频信息）"""
        out_path = video_path.parent / (video_path.stem + '.jpg')
        
        # 检查是否已存在缩略图
        existing = self._check_existing_thumbnail(video_path)
        
        if existing and not self.overwrite_var.get():
            # 记录跳过的文件（用于后续查看）
            self.skipped_videos.append(str(video_path))
            return "skipped", f"已存在缩略图: {out_path.name} ({existing['size_str']})，已自动跳过"
        
        if existing and self.overwrite_var.get():
            self._log(f"覆盖模式：正在重新生成 {out_path.name} (原文件 {existing['size_str']})", "warning")
        
        try:
            # 获取视频元数据
            video_info = self._get_video_metadata(video_path)
            
            # 获取帧
            total_frames = self.grid_x_var.get() * self.grid_y_var.get()
            frames = self._get_frames(str(video_path), total_frames)
            
            # 构建宫格（返回画布和信息区高度）
            target_w = self.width_var.get()
            spacing = self.spacing_var.get()
            grid_x = self.grid_x_var.get()
            grid_y = self.grid_y_var.get()
            
            canvas, info_height, grid_height = self._build_grid(
                frames, grid_x, grid_y, target_w, spacing, video_info
            )
            
            # 绘制视频信息
            final_img = self._draw_video_info(
                canvas, video_info, self.font_size_var.get(), grid_height
            )
            
            # 保存图像
            success = self._safe_imwrite(out_path, final_img, self.quality_var.get())
            
            if not success:
                return "error", "写入失败"
            
            if existing:
                return "success", f"已覆盖: {out_path.name} ({video_info['resolution']}, {video_info['size_str']})"
            else:
                return "success", f"生成成功: {out_path.name} ({video_info['resolution']}, {video_info['size_str']})"
            
        except Exception as e:
            return "error", str(e)
    
    def _process_videos_thread(self):
        """后台处理线程（优化统计信息）"""
        root = Path(self.dir_var.get()).expanduser().resolve()
        
        if not root.exists():
            self._log(f"错误: 目录不存在 {root}", "error")
            self._process_finished()
            return
        
        include_subdirs = self.subdirs_var.get()
        
        self._log(f"正在扫描目录: {root}", "highlight")
        self._log(f"包含子目录: {'是' if include_subdirs else '否'}", "info")
        self._log(f"覆盖模式: {'开启（将覆盖已存在的缩略图）' if self.overwrite_var.get() else '关闭（自动跳过已存在的缩略图）'}", 
                 "warning" if self.overwrite_var.get() else "info")
        
        # 收集视频文件
        if include_subdirs:
            videos = [p for p in root.rglob('*') if self._is_video(p)]
        else:
            videos = [p for p in root.iterdir() if p.is_file() and self._is_video(p)]
        
        self.total_videos = len(videos)
        self.processed_count = 0
        self.skipped_videos = []  # 重置跳过列表
        
        if not videos:
            self._log("未找到任何视频文件！", "warning")
            self._process_finished()
            return
        
        self._log(f"共发现 {len(videos)} 个视频文件", "highlight")
        self.status_var.set("处理中...")
        
        success_count = skip_count = fail_count = 0
        
        for i, video_path in enumerate(videos):
            if self.stop_requested:
                self._log("用户取消操作", "warning")
                break
            
            self.processed_count = i + 1
            progress = (i + 1) / len(videos) * 100
            
            self.progress_var.set(progress)
            self.count_var.set(f"{i+1}/{len(videos)}")
            self.frame.update_idletasks()  # 更新 UI
            
            status, msg = self._process_single_video(video_path)
            
            if status == "success":
                success_count += 1
                self._log(msg, "success")
            elif status == "skipped":
                skip_count += 1
                self._log(msg, "skip")  # 使用专门的 skip 标签
            else:
                fail_count += 1
                self._log(f"{video_path.name}: {msg}", "error")
        
        # 详细的统计报告
        self._log("="*60, "highlight")
        self._log(f"处理完成统计：成功 {success_count} 个", "success")
        if skip_count > 0:
            self._log(f"              跳过 {skip_count} 个（已存在缩略图）", "skip")
            if self.is_standalone:
                self._log(f"              [点击'查看跳过列表'按钮可查看详情]", "info")
        if fail_count > 0:
            self._log(f"              失败 {fail_count} 个", "error")
        self._log(f"              总计 {len(videos)} 个视频", "highlight")
        self._log("="*60, "highlight")
        
        self._process_finished()
    
    def _start_process(self):
        """开始处理"""
        if not HAS_CV2:
            messagebox.showerror("错误", "OpenCV (cv2) 未安装，无法处理视频")
            return
        
        if not self.dir_var.get():
            messagebox.showerror("错误", "请选择视频文件夹")
            return
        
        self.is_running = True
        self.stop_requested = False
        
        self.start_btn.config(state=tk.DISABLED, bg='#81C784')
        self.stop_btn.config(state=tk.NORMAL, bg='#f44336')
        self.progress_var.set(0)
        self.status_var.set("处理中...")
        
        self._log("="*60, "highlight")
        self._log("开始生成宫格缩略图", "highlight")
        self._log(f"参数: {self.grid_x_var.get()}×{self.grid_y_var.get()} 宫格, "
                 f"宽度 {self.width_var.get()}px, 间隔 {self.spacing_var.get()}px, "
                 f"字体 {self.font_size_var.get()}px", "info")
        self._log("="*60, "highlight")
        
        thread = threading.Thread(target=self._process_videos_thread, daemon=True)
        thread.start()
    
    def _stop_process(self):
        """停止处理"""
        self.stop_requested = True
        self.status_var.set("正在停止...")
        self._log("正在停止处理...", "warning")
    
    def _process_finished(self):
        """处理完成回调"""
        self.is_running = False
        self.start_btn.config(state=tk.NORMAL, bg='#4CAF50')
        self.stop_btn.config(state=tk.DISABLED, bg='#9E9E9E')
        self.status_var.set("就绪")
        self.progress_var.set(100 if not self.stop_requested else 0)
        
        # 播放提示音
        try:
            self.frame.bell()
        except:
            pass
    
    def get_frame(self) -> ttk.Frame:
        """获取主框架"""
        return self.frame


# ======================================================================
# 视频剪辑转码功能（在本模块基础上扩展）
# ======================================================================

def format_clock(t: float) -> str:
    """将秒数格式化为 HH:MM:SS.hh（精确到0.01秒）"""
    if t is None or t < 0:
        t = 0.0
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:02d}:{m:02d}:{s:02d}.{cs:02d}"


def parse_clock(text) -> Optional[float]:
    """解析时间字符串，支持 HH:MM:SS.hh / MM:SS.hh / SS.hh / 纯秒数"""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    try:
        sec = 0.0
        for part in str(text).split(':'):
            part = part.replace(',', '.')
            sec = sec * 60 + float(part)
        return max(0.0, sec)
    except (ValueError, TypeError):
        return None


def enable_explorer_dragdrop(widget, on_files) -> bool:
    """
    启用从 Windows 资源管理器拖放文件到控件（依赖 tkinterdnd2）
    on_files: 回调函数，接收文件/文件夹路径列表
    返回是否启用成功
    """
    try:
        import tkinterdnd2
        root = widget.winfo_toplevel()
        if not getattr(root, "_tkdnd_required", False):
            tkinterdnd2.TkinterDnD._require(root)
            root._tkdnd_required = True
        widget.drop_target_register("DND_Files")
        widget.dnd_bind("<<Drop>>",
                        lambda e: on_files(list(widget.tk.splitlist(e.data))))
        return True
    except Exception:
        return False


@dataclass
class VideoListItem:
    """视频列表条目"""
    path: str
    size: int = 0
    ext: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    bitrate_kbps: float = 0.0
    fps: float = 0.0       # 源视频帧率（用于把剪辑时间对齐到帧格）
    start: float = 0.0     # 开始时间（秒）
    end: float = 0.0       # 结束时间（秒），默认=视频时长


@dataclass
class TranscodeTask:
    """转码任务（加入队列时固化参数）"""
    src: str = ""
    start: float = 0.0
    end: float = 0.0
    src_w: int = 0
    src_h: int = 0
    fps: float = 0.0        # 源视频帧率（用于把剪辑起点对齐到帧格）
    resolution: str = "1280x720"
    aspect: str = "保持原比例"
    bitrate: int = 2500
    auto_orient: bool = False    # 竖屏自动适配：按源视频屏幕方向自动调整目标分辨率
    portrait_res: str = "自动(按源比例)"   # 竖屏源视频的目标分辨率：自动(按源比例)/720x1280/720x1080
    applied_resolution: str = ""     # 本次实际输出分辨率（解析后回填，供日志/界面显示）
    save_mode: str = "source"   # source=原文件夹 / custom=指定文件夹
    save_dir: str = ""
    subtitle: str = ""          # 同名 SRT 字幕路径（为空表示不封装字幕）
    subtitle_mode: str = "none"     # none / soft_fast / soft
    out_ext: str = "mp4"            # 输出容器后缀（快速附加模式沿用源容器，利于直接拷贝流）
    keep_quality: bool = False      # 流拷贝剪辑：仅按起止时间裁剪，不改分辨率/码率/编码（-c copy）
    keep_bitrate: bool = False      # 不修改码率：按源视频自身码率编码（读不到时回退 bitrate）
    merge: bool = False             # 合并任务：把多个视频按顺序拼接为一个成片
    merge_name: str = ""            # 合并成片文件名（不含扩展名）；为空时取首段文件名_合并
    # 合并的各个片段：[{src,start,end,src_w,src_h,fps,srt}]，各自可有独立的前后剪辑时间
    segments: List[Dict[str, Any]] = field(default_factory=list)
    overwrite_src: bool = False     # 快速附加后直接覆盖原视频文件（并删除字幕）
    out_path: str = ""              # 本次任务实际输出文件路径（_worker 解析后写入，用于防误删）
    swap_back_to: str = ""          # 同目录同名场景：成片先写到 ~N 名，删原文件后改回此原名（安全覆盖）
    display: str = ""
    q_index: int = -1
    status: str = "待处理"   # 队列中的状态文本
    progress: float = 0.0    # 处理进度 0~100


class TimeEntry(tk.Entry):
    """
    时间输入框（掩码式录入）

    特性：
    1. 始终保持 HH:MM:SS.hh 掩码格式，"0"和分隔符由掩码固定；
    2. 键盘输入数字时覆盖当前位（而非插入），输入后自动跳到下一个数字位；
    3. 自动跳过 ":" 和 "." 分隔符，连续输入 1234 会得到 "12:34:00.00"；
    4. 支持 Ctrl+V 粘贴（按位覆盖），支持 Backspace/Delete 置零、左右方向键按位移动。
    """

    DEFAULT_MASK = "##:##:##.##"   # HH:MM:SS.hh

    def __init__(self, master, mask: Optional[str] = None,
                 textvariable: Optional[tk.StringVar] = None,
                 on_return: Optional[Callable] = None, **kwargs):
        self.mask = mask or self.DEFAULT_MASK
        self._positions = [i for i, ch in enumerate(self.mask) if ch == '#']
        self.var = textvariable or tk.StringVar()
        self.on_return = on_return
        if not self.var.get():
            self.var.set(self._blank_text())

        kwargs.setdefault('justify', 'center')
        kwargs.setdefault('font', ('Consolas', 10))
        kwargs.setdefault('width', len(self.mask) + 1)
        super().__init__(master, textvariable=self.var, **kwargs)

        self._updating = False
        self.var.trace_add('write', self._on_var_write)
        self.bind('<KeyPress>', self._on_key_press)
        self.bind('<<Paste>>', self._on_paste)
        self.bind('<FocusIn>', self._on_focus_in)
        self._sync_mask()

    # ---------- 掩码工具 ----------
    def _blank_text(self) -> str:
        return self.mask.replace('#', '0')

    def _sync_mask(self):
        """按掩码归一化显示文本：非标准写法按“秒数”解析后重新格式化"""
        raw = self.var.get() or ''
        # 已符合掩码结构则无需处理
        if len(raw) == len(self.mask) and all(
                (m == '#' and c.isdigit()) or (m != '#' and c == m)
                for m, c in zip(self.mask, raw)):
            return
        sec = parse_clock(raw)
        if sec is None:
            self._set_text(self._blank_text())
            return
        text = format_clock(sec)
        if len(text) > len(self.mask):
            text = text[-len(self.mask):]
        elif len(text) < len(self.mask):
            blank = self._blank_text()
            text = blank[:len(self.mask) - len(text)] + text
        self._set_text(text)

    def _set_text(self, text: str):
        self._updating = True
        self.var.set(text)
        self._updating = False

    def _on_var_write(self, *_args):
        """外部写入变量时同步为掩码格式"""
        if self._updating:
            return
        self.after_idle(self._sync_mask)

    def _index_to_pos(self, index: int) -> int:
        """字符索引 -> 掩码数字位序号（落在分隔符上时取其后最近的数字位）"""
        for pos, idx in enumerate(self._positions):
            if idx >= index:
                return pos
        return len(self._positions) - 1

    # ---------- 输入处理 ----------
    def _put_digit(self, ch: str) -> str:
        """在当前位覆盖写入数字，并跳到下一位"""
        pos = self._index_to_pos(self.index(tk.INSERT))
        if pos >= len(self._positions):
            return 'break'
        text = list(self.var.get())
        text[self._positions[pos]] = ch
        self._set_text(''.join(text))
        nxt = pos + 1
        self.icursor(self._positions[nxt] if nxt < len(self._positions) else tk.END)
        return 'break'

    def _erase(self, backward: bool = True) -> str:
        """退格/删除：把对应位置零"""
        pos = self._index_to_pos(self.index(tk.INSERT))
        if backward:
            pos = max(0, pos - 1)
        text = list(self.var.get())
        text[self._positions[pos]] = '0'
        self._set_text(''.join(text))
        self.icursor(self._positions[pos])
        return 'break'

    def _move(self, delta: int) -> str:
        pos = self._index_to_pos(self.index(tk.INSERT)) + delta
        pos = max(0, min(len(self._positions) - 1, pos))
        self.icursor(self._positions[pos])
        return 'break'

    def _goto_pos(self, pos: int) -> str:
        pos = max(0, min(len(self._positions) - 1, pos))
        self.icursor(self._positions[pos])
        return 'break'

    def _on_focus_in(self, _event=None):
        """聚焦后把光标对齐到最近的数字位"""
        self.after_idle(lambda: self.icursor(
            self._positions[self._index_to_pos(self.index(tk.INSERT))]))

    def _on_paste(self, _event=None):
        """粘贴：按位覆盖填入数字，自动跳过分隔符"""
        try:
            clip = self.clipboard_get()
        except Exception:
            return 'break'
        digits = [c for c in clip if c.isdigit()]
        if not digits:
            return 'break'
        pos = self._index_to_pos(self.index(tk.INSERT))
        text = list(self.var.get())
        for d in digits:
            if pos >= len(self._positions):
                break
            text[self._positions[pos]] = d
            pos += 1
        self._set_text(''.join(text))
        self.icursor(self._positions[min(pos, len(self._positions) - 1)])
        return 'break'

    def _on_key_press(self, event) -> str:
        # Ctrl 组合键交给默认处理（复制/粘贴）
        if event.state & 0x4:
            return None

        keysym = event.keysym
        if keysym in ('Return', 'KP_Enter'):
            if callable(self.on_return):
                self.on_return()
            return 'break'
        if keysym == 'Tab':
            return None
        if keysym in ('Left', 'KP_Left'):
            return self._move(-1)
        if keysym in ('Right', 'KP_Right'):
            return self._move(1)
        if keysym == 'Home':
            return self._goto_pos(0)
        if keysym == 'End':
            return self._goto_pos(len(self._positions) - 1)
        if keysym in ('BackSpace', 'Delete'):
            return self._erase(keysym == 'BackSpace')
        if event.char and event.char.isdigit():
            return self._put_digit(event.char)
        # 其余按键一律拦截，保证掩码结构不被破坏
        return 'break'


class TrimRangeBar(tk.Canvas):
    """剪辑范围双滑块条（同时充当进度条）：前（左）滑块=开始时间，后（右）滑块=结束时间。

    - 拖动某个滑块 -> 回调 on_drag(which, t)，画面跟随该滑块时间
    - 单击某个滑块 -> 回调 on_select(which)，之后时间微调按钮/方向键作用于它
    - 单击/拖动轨道空白区 -> 回调 on_seek(t) 定位播放（充当原进度条的拖动定位）
    - 红色游标实时显示当前播放位置；两个滑块之间区域以蓝色高亮，选中滑块加粗高亮
    """

    HANDLE_W = 10     # 滑块手柄半宽（像素）
    HIT_PX = 14       # 命中判定范围（像素）
    PAD = 6           # 两端留白

    def __init__(self, master, on_select=None, on_drag=None, on_seek=None, **kw):
        kw.setdefault('height', 30)
        super().__init__(master, highlightthickness=0, **kw)
        self._on_select_cb = on_select
        self._on_drag_cb = on_drag
        self._on_seek_cb = on_seek
        self._dur = 0.0
        self._start = 0.0
        self._end = 0.0
        self._pos = 0.0      # 当前播放位置（红色游标）
        self._active = 'start'
        self._dragging = None            # 正在拖动的滑块 'start' / 'end' / None
        self.bind('<Configure>', lambda e: self.redraw())
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_motion)
        self.bind('<ButtonRelease-1>', self._on_release)

    # ---- 数据 ----
    def set_active(self, which: str):
        """切换选中（激活）的滑块，不触发回调"""
        if which in ('start', 'end') and which != self._active:
            self._active = which
            self.redraw()

    def set_values(self, dur: float, start: float, end: float, active: str, pos: float = None):
        self._dur = max(float(dur), 0.0)
        self._start = min(max(float(start), 0.0), self._dur)
        self._end = min(max(float(end), 0.0), self._dur)
        self._active = active if active in ('start', 'end') else 'start'
        if pos is not None:
            self._pos = min(max(float(pos), 0.0), self._dur)
        self.redraw()

    def set_position(self, t: float):
        """更新当前播放位置（红色游标）"""
        self._pos = min(max(float(t), 0.0), self._dur)
        self.redraw()

    # ---- 坐标换算 ----
    def _track_w(self) -> int:
        return max(1, self.winfo_width() - 2 * self.PAD)

    def _x_of(self, t: float) -> int:
        if self._dur <= 0:
            return self.PAD
        return self.PAD + int(round(t / self._dur * self._track_w()))

    def _t_of(self, x: int) -> float:
        if self._dur <= 0:
            return 0.0
        x = min(max(x - self.PAD, 0), self._track_w())
        return x / self._track_w() * self._dur

    # ---- 交互 ----
    def _on_press(self, event):
        if self._dur <= 0:
            return
        xs, xe = self._x_of(self._start), self._x_of(self._end)
        pick = None
        # 距哪个手柄更近（且在命中范围内）就选哪个；重叠时优先选中的那个
        if abs(event.x - xs) <= self.HIT_PX or abs(event.x - xe) <= self.HIT_PX:
            if self._active == 'end' and abs(event.x - xe) <= self.HIT_PX:
                pick = 'end'
            elif abs(event.x - xs) <= self.HIT_PX:
                pick = 'start'
            else:
                pick = 'end'
        if pick is None:
            # 点到轨道空白区 -> 当作进度条定位（跳转播放位置）
            if self._on_seek_cb:
                self._on_seek_cb(self._t_of(event.x))
            return
        self._dragging = pick
        if pick != self._active:
            self._active = pick
            if self._on_select_cb:
                self._on_select_cb(pick)
        self._apply(pick, self._t_of(event.x))

    def _on_motion(self, event):
        if not self._dragging:
            return
        self._apply(self._dragging, self._t_of(event.x))

    def _on_release(self, _event):
        self._dragging = None

    def _apply(self, which: str, t: float):
        # 保证开始<=结束（最小间隔 0.01s）
        if which == 'start':
            t = min(max(t, 0.0), max(self._end - 0.01, 0.0))
        else:
            t = min(max(t, self._start + 0.01), self._dur)
        if self._on_drag_cb:
            self._on_drag_cb(which, t)   # 由面板写回时间框并 seek 播放器

    # ---- 绘制 ----
    def redraw(self):
        self.delete('all')
        w = max(self.winfo_width(), 1)
        h = int(self.winfo_height() or 30)
        mid = h // 2
        if self._dur <= 0:
            self.create_rectangle(self.PAD, mid - 3, max(w - self.PAD, self.PAD + 1),
                                  mid + 3, fill='#d9d9d9', outline='')
            return
        x0, x1 = self.PAD, w - self.PAD
        xs, xe = self._x_of(self._start), self._x_of(self._end)
        # 轨道
        self.create_rectangle(x0, mid - 3, x1, mid + 3, fill='#c8c8c8', outline='')
        # 已选范围高亮
        if xe > xs:
            self.create_rectangle(xs, mid - 3, xe, mid + 3, fill='#7fb2ff', outline='')
        # 当前播放位置游标（红色竖线 + 顶部小三角），置于手柄之下
        if self._dur > 0:
            xp = self._x_of(self._pos)
            self.create_line(xp, mid - 13, xp, mid + 13, fill='#e53935', width=2)
            self.create_polygon(xp, mid - 15, xp - 4, mid - 21, xp + 4, mid - 21,
                                fill='#e53935', outline='')
        # 滑块手柄（选中者高亮）
        for which, x in (('start', xs), ('end', xe)):
            sel = (which == self._active)
            hw = self.HANDLE_W + (2 if sel else 0)
            self.create_polygon(x, mid - 11, x - hw, mid - 11, x - hw, mid + 1,
                                x, mid + 11, x + hw, mid + 1, x + hw, mid - 11,
                                fill='#0a66d0' if sel else '#8a8a8a',
                                outline='#063f85' if sel else '#5a5a5a')
            # 上方小刻度线，便于对齐时间
            self.create_line(x, mid - 14, x, mid + 14,
                             fill='#063f85' if sel else '#9a9a9a')


class EmbeddedPlayer:
    """基于 OpenCV 的内嵌简易播放器（Canvas 渲染帧）"""

    def __init__(self, parent, on_time=None, on_state=None):
        self.parent = parent
        self.on_time = on_time        # 回调：播放时间变化
        self.on_state = on_state      # 回调：播放状态变化(playing: bool)
        self.cap = None
        self.path = None
        self.duration = 0.0
        self.fps = 25.0
        self.playing = False
        self.current_time = 0.0
        self._job = None
        self._photo = None
        self._last_frame = None
        self.stop_at = None
        self.blanked = False        # 黑屏：仅隐藏画面，不影响播放/时间

        self._canvas = tk.Canvas(parent, bg="#000000", highlightthickness=0, height=300)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", lambda e: self._redraw_last())

    # ---- 黑屏 ----
    def set_blank(self, blank: bool = True):
        """开关黑屏：黑屏时只隐藏画面，播放位置/进度/时间保持正常"""
        self.blanked = bool(blank)
        if self.blanked:
            try:
                self._photo = None
                self._canvas.delete("all")
            except Exception:
                pass
        else:
            self._redraw_last()

    # ---- 内部渲染 ----
    def _redraw_last(self):
        if self.blanked:
            return
        if self._last_frame is not None and not self.playing:
            self._draw_frame(self._last_frame)

    def _draw_frame(self, frame):
        if self.blanked:
            return
        try:
            cw = max(self._canvas.winfo_width(), 1)
            ch = max(self._canvas.winfo_height(), 1)
            fh, fw = frame.shape[:2]
            scale = min(cw / fw, ch / fh)
            nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
            resized = cv2.resize(frame, (nw, nh))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
            self._canvas.delete("all")
            self._canvas.create_image(cw // 2, ch // 2, image=self._photo)
        except Exception:
            pass

    def _notify(self, t):
        self.current_time = t
        if self.on_time:
            try:
                self.on_time(t)
            except Exception:
                pass

    def _set_playing(self, state: bool):
        self.playing = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    # ---- 公共接口 ----
    def load(self, path) -> bool:
        """加载视频文件"""
        self.stop()
        if self.cap:
            self.cap.release()
            self.cap = None
        self.path = None
        if not HAS_CV2 or not path:
            return False
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return False
        self.cap = cap
        self.path = path
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        ms = cap.get(cv2.CAP_PROP_POS_MSEC)
        if frames > 0 and self.fps > 0:
            self.duration = frames / self.fps
        else:
            self.duration = ms / 1000.0
        self._last_frame = None
        self.seek(0.0)
        return True

    def play(self, from_time=None, stop_at=None):
        """播放；from_time=起始秒，stop_at=停止秒（用于片段预览）"""
        if not self.cap:
            return
        if from_time is not None:
            self._seek_internal(from_time)
        self.stop_at = stop_at
        self._set_playing(True)
        self._cancel_job()
        self._schedule(30)

    def pause(self):
        self._set_playing(False)
        self._cancel_job()

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def stop(self):
        """停止并回到开头"""
        self.pause()
        if self.cap:
            self.seek(0.0)

    def seek(self, t: float):
        """定位到指定时间（会暂停播放）"""
        if not self.cap:
            return
        self.pause()
        self._seek_internal(t)

    def _seek_internal(self, t: float):
        """精确定位到指定时间：优先按帧号 seek，避免只落到关键帧造成“只移动一点”"""
        t = max(0.0, min(t, max(self.duration, 0.0)))
        try:
            if self.fps and self.fps > 1e-6:
                # 取“不早于该时间的第一帧”(ceil)，与 ffmpeg -ss 的取帧规则一致，
                # 否则 0.1 秒精度下预览画面会与转码首帧差 1 帧
                idx = max(0, int(math.ceil(t * self.fps - 1e-6)))
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            else:
                self.cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        except Exception:
            try:
                self.cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            except Exception:
                pass
        ret, frame = self.cap.read()
        if ret:
            self._last_frame = frame
            self._draw_frame(frame)
            pf = self.cap.get(cv2.CAP_PROP_POS_FRAMES)
            if self.fps and self.fps > 1e-6 and pf >= 1:
                # read() 之后位置指向下一帧，回退一帧即为当前帧时间
                t = (pf - 1) / self.fps
            else:
                t = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        self._notify(t)

    def nudge(self, delta: float):
        """快进/快退指定秒数"""
        self.seek(self.current_time + delta)

    # ---- 播放循环 ----
    def _cancel_job(self):
        if self._job:
            try:
                self._canvas.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _schedule(self, delay):
        self._job = self._canvas.after(delay, self._loop)

    def _loop(self):
        if not self.playing or not self.cap:
            return
        ret, frame = self.cap.read()
        if not ret:
            # 播放到结尾
            self._set_playing(False)
            return
        self._last_frame = frame
        self._draw_frame(frame)
        t = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        self._notify(t)
        if self.stop_at is not None and t >= self.stop_at - 1e-6:
            self._set_playing(False)
            return
        self._schedule(30)

    def release(self):
        """释放视频资源并复位到初始状态（清空画面/缓存帧/时间/状态）"""
        self._cancel_job()
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        # 关键：清空缓存帧与时间，否则画布后续尺寸变化触发 _redraw_last
        #       时会把上一视频的画面重新绘制出来
        self._last_frame = None
        self._photo = None
        self.path = None
        self.duration = 0.0
        self.fps = 25.0
        self.playing = False
        self.current_time = 0.0
        self.stop_at = None
        self.blanked = False
        try:
            self._canvas.delete("all")
        except Exception:
            pass


class VideoTranscodePanel:
    """视频剪辑转码面板：拖放列表 + 内嵌播放器 + 精确剪辑 + HEVC 批量转码"""

    CONFIG_SECTION = "video_transcode"
    PRECISIONS = {"0.01S": 0.01, "0.05S": 0.05, "0.1S": 0.1, "0.5S": 0.5, "1S": 1.0}

    # 字幕处理方式：下拉显示名 -> 内部 key
    #   none      不处理字幕
    #   soft_fast 软字幕·快速附加：直接拷贝视频/音频流并挂字幕，不重新编码（无损、秒级完成）
    #   soft      软字幕·随转码封装：需要重新编码（可同时缩放/剪辑），字幕为可关闭的软字幕
    SUBTITLE_MODES = {
        "不添加字幕": "none",
        "软字幕·快速附加（不重编码）": "soft_fast",
        "软字幕·封装（随转码）": "soft",
    }

    # 编码器选项：内部 key -> 下拉显示名（优先显卡硬编，降低 CPU 占用）
    ENCODER_OPTIONS = {
        "auto": "自动（优先显卡加速）",
        "nvenc": "显卡 NVIDIA（NVENC）",
        "amf": "显卡 AMD（AMF）",
        "qsv": "显卡 Intel（QSV）",
        "cpu": "CPU（libx265 软编码）",
    }
    # key -> 对应的 ffmpeg 编码器（用于可用性探针）
    ENCODER_CODECS = {"nvenc": "hevc_nvenc", "amf": "hevc_amf", "qsv": "hevc_qsv"}
    # SRT 时间轴行：00:00:01,000 --> 00:00:03,500
    SRT_TIME_RE = re.compile(
        r'(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*'
        r'(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})')

    def __init__(self, parent, controller=None, config_manager: Optional[ConfigManager] = None):
        self.parent = parent
        self.controller = controller
        self.is_standalone = controller is None

        if config_manager:
            self.config_manager = config_manager
        elif controller and hasattr(controller, 'config_manager'):
            self.config_manager = controller.config_manager
        else:
            self.config_manager = ConfigManager()

        # 状态
        self.items: Dict[str, VideoListItem] = {}      # tree iid -> 条目
        self._paths: Set[str] = set()                  # 已加入路径（去重）
        self._current_iid: Optional[str] = None        # 当前选中的列表项
        self._loading_item = False                     # 正在切换视频（挂起 trace 写回，防止串台）
        self.task_queue: List[TranscodeTask] = []      # 任务队列
        self.is_running = False
        self.stop_requested = False
        self.executor: Optional[ThreadPoolExecutor] = None
        self._procs: List[subprocess.Popen] = []
        self._lock = threading.Lock()
        self._done = 0
        self._total = 0
        self._success = 0
        self._fail = 0
        self._cancel = 0
        self._total_dur = 0.0
        self._start_ts = 0.0
        self._failures: List[tuple] = []
        self.ffmpeg_path = ""

        self._create_widgets()
        self._load_settings()
        self._bind_settings_save()

        # ffmpeg 自动检测
        if not self.ffmpeg_var.get().strip():
            found = shutil.which('ffmpeg')
            if found:
                self.ffmpeg_var.set(found)

        # 拖放支持
        if not enable_explorer_dragdrop(self.tree, self._add_paths):
            self._log("提示: 未检测到 tkinterdnd2，鼠标拖放不可用。"
                      "可执行 pip install tkinterdnd2 启用拖放，或使用“添加文件”按钮。", "warning")

        # 键盘快捷键：视频列表选中视频后，空格=播放/暂停，左右方向键=±1 秒定位。
        # 注意：ttk.Frame 不在子控件的 bindtag 中，绑在 self.frame 上收不到按键，
        # 必须绑定到顶层窗口；Treeview 自身的按键单独绑定并返回 'break'，
        # 以拦截其默认行为（空格重选条目导致重新载入、左右键折叠展开），见 _create_list_area。
        self.frame.winfo_toplevel().bind('<KeyPress>', self._on_panel_key, add='+')

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _create_widgets(self):
        self.frame = ttk.Frame(self.parent)

        # 底部状态栏
        self.status_bar_var = tk.StringVar(value="就绪 | 队列 0 项")
        status_bar = ttk.Label(self.frame, textvariable=self.status_bar_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=(6, 3))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        main_paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧：视频列表 + 播放器/剪辑
        left = ttk.Frame(main_paned)
        main_paned.add(left, weight=3)
        self._create_list_area(left)
        self._create_player_area(left)

        # 右侧：任务队列（上）+ 转码设置（下）+ 日志
        right = ttk.Frame(main_paned)
        main_paned.add(right, weight=3)
        self._create_queue_area(right)
        self._create_settings_area(right)
        self._create_log_area(right)

    def _create_list_area(self, parent):
        lf = ttk.LabelFrame(parent, text="视频列表（可从资源管理器拖入多个视频文件）", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        cols = ("name", "size", "ext", "res", "bitrate", "start", "end")
        headers = ("文件名", "大小", "扩展名", "分辨率", "数据速率", "开始时间", "结束时间")
        widths = (240, 80, 60, 95, 95, 105, 105)
        self._col_headers = dict(zip(cols, headers))
        self._sort_col = None
        self._sort_reverse = False
        self.tree = ttk.Treeview(lf, columns=cols, show="headings",
                                 selectmode="extended", height=8)
        for c, h, w in zip(cols, headers, widths):
            self.tree.heading(c, text=h, command=lambda col=c: self._sort_by(col))
            self.tree.column(c, width=w, anchor=tk.W if c == "name" else tk.CENTER)
        vsb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # 列表上的快捷键：空格=播放/暂停，左右方向键=±1 秒（返回 'break' 拦截
        # Treeview 类默认绑定，避免空格重新选中条目、左右键折叠展开）
        for _seq in ('<space>', '<KP_Space>', '<Left>', '<KP_Left>',
                     '<Right>', '<KP_Right>'):
            self.tree.bind(_seq, self._on_tree_key)

        # 按钮行放在列表下方（必须先于列表 pack 到 BOTTOM，否则会被 expand 的列表挤掉）
        btns = ttk.Frame(lf)
        btns.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        ttk.Button(btns, text="添加文件", command=self._browse_files, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="添加文件夹", command=self._browse_folder, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="移除选中", command=self._remove_selected, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="清空列表", command=self._clear_list, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="➕ 加入选中", command=lambda: self._join_queue(only_selected=True), width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="➕ 加入所有", command=lambda: self._join_queue(only_selected=False), width=10).pack(side=tk.LEFT, padx=2)
        # 拼接模式：勾选后“加入选中/加入所有”把多个视频按列表顺序合并为一个任务
        # （各段仍可按自己设定的起止时间剪辑，统一用右侧的输出参数）
        self.merge_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="🔗 拼接为一个视频", variable=self.merge_var
                        ).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Label(btns, text="（勾选后按列表顺序合并选中视频；每段沿用自己设定的起止时间）",
                  foreground="#777").pack(side=tk.LEFT, padx=(0, 4))

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _create_player_area(self, parent):
        lf = ttk.LabelFrame(parent, text="播放器与时间剪辑", padding=5)
        lf.pack(fill=tk.BOTH, padx=5, pady=5)

        # 播放画面
        self.player = EmbeddedPlayer(lf, on_time=self._on_player_time,
                                     on_state=self._on_player_state)

        # 剪辑范围双滑块条（同时充当进度条）：前滑块=开始时间，后滑块=结束时间
        # 拖动滑块即选中并让画面跟随；点按/拖拽轨道空白区即定位播放位置（红色游标）
        self.trim_bar = TrimRangeBar(lf,
                                     on_select=self._on_trim_slider_select,
                                     on_drag=self._on_trim_slider_drag,
                                     on_seek=self._on_track_seek)
        self.trim_bar.pack(fill=tk.X, pady=(4, 0))

        # 播放控制行
        ctrl = ttk.Frame(lf)
        ctrl.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(ctrl, text="« 5秒", width=7,
                   command=lambda: self._nudge(-5)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="« 1秒", width=7,
                   command=lambda: self._nudge(-1)).pack(side=tk.LEFT, padx=2)
        self.play_btn = ttk.Button(ctrl, text="▶ 播放", width=9, command=self._toggle_play)
        self.play_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="■ 停止", width=7,
                   command=lambda: self.player.stop()).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="1秒 »", width=7,
                   command=lambda: self._nudge(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="5秒 »", width=7,
                   command=lambda: self._nudge(5)).pack(side=tk.LEFT, padx=2)
        self.blank_btn = ttk.Button(ctrl, text="隐藏屏幕", width=9, command=self._toggle_blank)
        self.blank_btn.pack(side=tk.LEFT, padx=(10, 2))
        self.time_var = tk.StringVar(value="00:00:00.00 / 00:00:00.00")
        ttk.Label(ctrl, textvariable=self.time_var,
                  font=('Consolas', 10, 'bold')).pack(side=tk.RIGHT, padx=5)

        # 时间剪辑行（“-/+”紧贴时间框右侧，“设为当前”紧随其后；
        # 最右列为“移到最前 / 移到最后”，用于把前/后两个滑块一键移到片头 / 片尾）
        trim = ttk.Frame(lf)
        trim.pack(fill=tk.X, pady=(6, 0))
        trim.columnconfigure(5, weight=1)   # 间隔列，仅用于把“移到最前 / 最后”推到最右

        self.start_var = tk.StringVar(value="00:00:00.00")
        self.end_var = tk.StringVar(value="00:00:00.00")
        self.precision_var = tk.StringVar(value="0.1S")
        # 编辑开始/结束时间即实时写回当前选中的列表条目：
        # 避免只通过播放器按钮/文本框改了时间、却没点“✔ 保存到选中视频”，
        # 导致“加入队列”时用了未提交的旧时间（表现为成片起点提前好几秒）
        self.start_var.trace_add('write', self._on_trim_edit)
        self.end_var.trace_add('write', self._on_trim_edit)

        # 开始时间（掩码输入框，回车按新时间定位/播放）
        self._active_time_field = 'start'   # 当前激活的时间框/滑块：'start' / 'end'（默认=前滑块）
        self.start_lbl = ttk.Label(trim, text="开始时间:")
        self.start_lbl.grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self.start_entry = TimeEntry(trim, textvariable=self.start_var,
                                     on_return=lambda: self._on_time_return('start'))
        self.start_entry.grid(row=0, column=1, sticky=tk.W)
        self.start_entry.bind('<FocusIn>', lambda e: self._mark_active_time('start'), add='+')
        ttk.Button(trim, text="-", width=3,
                   command=lambda: self._adjust_time('start', -1)).grid(row=0, column=2, padx=(5, 1))
        ttk.Button(trim, text="+", width=3,
                   command=lambda: self._adjust_time('start', 1)).grid(row=0, column=3, padx=(1, 2))
        ttk.Button(trim, text="◀ 设为当前", width=11,
                   command=lambda: self._set_time_from_player('start')).grid(row=0, column=4, padx=(6, 0))
        ttk.Button(trim, text="⏮ 移到最前", width=11,
                   command=lambda: self._move_slider('start', 'first')).grid(
                   row=0, column=6, sticky=tk.E, padx=(8, 0))

        # 结束时间
        self.end_lbl = ttk.Label(trim, text="结束时间:")
        self.end_lbl.grid(row=1, column=0, sticky=tk.W, padx=(0, 4), pady=(4, 0))
        self.end_entry = TimeEntry(trim, textvariable=self.end_var,
                                   on_return=lambda: self._on_time_return('end'))
        self.end_entry.grid(row=1, column=1, sticky=tk.W, pady=(4, 0))
        self.end_entry.bind('<FocusIn>', lambda e: self._mark_active_time('end'), add='+')
        ttk.Button(trim, text="-", width=3,
                   command=lambda: self._adjust_time('end', -1)).grid(row=1, column=2, padx=(5, 1), pady=(4, 0))
        ttk.Button(trim, text="+", width=3,
                   command=lambda: self._adjust_time('end', 1)).grid(row=1, column=3, padx=(1, 2), pady=(4, 0))
        ttk.Button(trim, text="▶ 设为当前", width=11,
                   command=lambda: self._set_time_from_player('end')).grid(
                   row=1, column=4, padx=(6, 0), pady=(4, 0))
        ttk.Button(trim, text="⏭ 移到最后", width=11,
                   command=lambda: self._move_slider('end', 'last')).grid(
                   row=1, column=6, sticky=tk.E, padx=(8, 0), pady=(4, 0))

        # 精度 + 操作
        self._mark_active_time('start')   # 默认激活前滑块（开始时间），界面高亮同步
        row2 = ttk.Frame(trim)
        row2.grid(row=2, column=0, columnspan=5, sticky=tk.EW, pady=(8, 0))
        ttk.Label(row2, text="精度:").pack(side=tk.LEFT, padx=(0, 4))
        prec_cb = ttk.Combobox(row2, textvariable=self.precision_var,
                               values=list(self.PRECISIONS.keys()),
                               state="readonly", width=7)
        prec_cb.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(row2, text="⏵ 播放片段", width=11,
                   command=self._preview_clip).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="✔ 保存到选中视频", width=16,
                   command=self._apply_trim).pack(side=tk.LEFT, padx=8)

    def _create_settings_area(self, parent):
        lf = ttk.LabelFrame(parent, text="转码设置（对队列中所有视频生效）", padding=8)
        lf.pack(fill=tk.X, padx=5, pady=5)
        lf.columnconfigure(1, weight=1)

        # 保存位置
        self.save_src_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(lf, text="保存到原视频所在文件夹（自动加 ~1/~2 后缀）",
                        variable=self.save_src_var, value=True,
                        command=self._save_settings).grid(row=0, column=0, columnspan=3, sticky=tk.W)
        self.save_custom_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(lf, text="保存到指定文件夹:",
                        variable=self.save_src_var, value=False,
                        command=self._save_settings).grid(row=1, column=0, sticky=tk.W)
        self.save_dir_var = tk.StringVar()
        ttk.Entry(lf, textvariable=self.save_dir_var, width=42).grid(
            row=1, column=1, sticky=tk.EW, padx=4)
        ttk.Button(lf, text="浏览...", width=7, command=self._browse_save_dir).grid(row=1, column=2)

        # 新视频分辨率
        ttk.Label(lf, text="新视频分辨率:").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.res_var = tk.StringVar(value="1280x720")
        res_cb = ttk.Combobox(lf, textvariable=self.res_var, width=16,
                              values=["1280x720", "1920x1080", "2560x1440", "854x480",
                                      "保持原分辨率", "自定义分辨率"])
        res_cb.grid(row=2, column=1, sticky=tk.W, padx=4, pady=(6, 0))
        res_cb.bind("<<ComboboxSelected>>", lambda e: self._on_res_preset())
        # 自定义分辨率：选中后激活宽/高输入框（避免固定分辨率导致变形/加黑边）
        ttk.Label(lf, text="自定义宽:").grid(row=2, column=2, sticky=tk.W, padx=(8, 2), pady=(6, 0))
        self.cust_w_var = tk.StringVar()
        self._cust_w_entry = ttk.Entry(lf, textvariable=self.cust_w_var, width=7,
                                       state="disabled")
        self._cust_w_entry.grid(row=2, column=3, sticky=tk.W, padx=(0, 2), pady=(6, 0))
        ttk.Label(lf, text="高:").grid(row=2, column=4, sticky=tk.W, padx=(2, 2), pady=(6, 0))
        self.cust_h_var = tk.StringVar()
        self._cust_h_entry = ttk.Entry(lf, textvariable=self.cust_h_var, width=7,
                                       state="disabled")
        self._cust_h_entry.grid(row=2, column=5, sticky=tk.W, padx=(0, 4), pady=(6, 0))

        # 竖屏自动适配：混合横屏/竖屏素材时，竖屏源自动改用竖屏目标分辨率
        self.auto_orient_var = tk.BooleanVar(value=True)
        ao_chk = ttk.Checkbutton(lf, text="竖屏自动适配分辨率",
                                 variable=self.auto_orient_var,
                                 command=self._save_settings)
        ao_chk.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        self._add_tooltip(ao_chk,
                          "同时有横屏和竖屏视频时勾选：\n"
                          "· 横屏源 → 用上面设定的分辨率（如 1280x720，按高度 720 缩放）\n"
                          "· 竖屏源 → 自动改用右侧“竖屏目标”分辨率（默认“自动(按源比例)”，"
                          "按 720P 取竖屏并匹配源比例，如 9:16 源 → 720x1280，无黑边）\n"
                          "按原比例缩放后，宽或高不足的部分自动补黑边，画面不会被压扁")

        # 横宽比
        ttk.Label(lf, text="横宽比:").grid(row=4, column=0, sticky=tk.W, pady=(6, 0))
        self.aspect_var = tk.StringVar(value="保持原比例")
        ttk.Combobox(lf, textvariable=self.aspect_var, width=16,
                     values=["保持原比例", "16:9", "4:3", "9:16", "1:1"]
                     ).grid(row=4, column=1, sticky=tk.W, padx=4, pady=(6, 0))

        # 竖屏目标分辨率（仅“竖屏自动适配”勾选时生效）
        ttk.Label(lf, text="竖屏目标:").grid(row=5, column=0, sticky=tk.W, pady=(6, 0))
        self.portrait_res_var = tk.StringVar(value="自动(按源比例)")
        ttk.Combobox(lf, textvariable=self.portrait_res_var, width=16, state="readonly",
                     values=["自动(按源比例)", "720x1280", "720x1080"]
                     ).grid(row=5, column=1, sticky=tk.W, padx=4, pady=(6, 0))

        # 方向（已移到“竖屏目标”下方）
        ttk.Label(lf, text="方向:").grid(row=6, column=0, sticky=tk.W, pady=(6, 0))
        self.orientation_var = tk.StringVar(value="横向")
        orient_cb = ttk.Combobox(lf, textvariable=self.orientation_var, width=16, state="readonly",
                                 values=["横向", "竖向"])
        orient_cb.grid(row=6, column=1, sticky=tk.W, padx=4, pady=(6, 0))
        orient_cb.bind("<<ComboboxSelected>>", lambda e: self._apply_orientation())

        # 视频码率（只作用于视频流 -b:v，音频固定 -b:a 128k）
        ttk.Label(lf, text="视频码率(kbps):").grid(row=7, column=0, sticky=tk.W, pady=(6, 0))
        self.bitrate_var = tk.IntVar(value=2500)
        bitrate_spin = ttk.Spinbox(lf, from_=100, to=50000, increment=100,
                                   textvariable=self.bitrate_var, width=12)
        bitrate_spin.grid(row=7, column=1, sticky=tk.W, padx=4, pady=(6, 0))
        self._add_tooltip(bitrate_spin,
                          "仅设置“视频流”码率（ffmpeg -b:v），音频固定 -b:a 128k，\n"
                          "与列表“数据速率”列口径一致：均不含音频")

        # 并发数
        ttk.Label(lf, text="同时处理数:").grid(row=8, column=0, sticky=tk.W, pady=(6, 0))
        self.conc_var = tk.IntVar(value=2)
        ttk.Spinbox(lf, from_=1, to=5, textvariable=self.conc_var, width=12).grid(
            row=8, column=1, sticky=tk.W, padx=4, pady=(6, 0))

        # 完成后操作
        ttk.Label(lf, text="完成后操作:").grid(row=9, column=0, sticky=tk.W, pady=(6, 0))
        self.after_var = tk.StringVar(value="无操作")
        ttk.Combobox(lf, textvariable=self.after_var, width=16, state="readonly",
                     values=["无操作", "睡眠", "关机"]).grid(
            row=9, column=1, sticky=tk.W, padx=4, pady=(6, 0))

        # 字幕封装（说明改为悬停提示，减少页面占用）
        ttk.Label(lf, text="字幕:").grid(row=10, column=0, sticky=tk.W, pady=(6, 0))
        self.subtitle_var = tk.StringVar(value="不添加字幕")
        sub_cb = ttk.Combobox(lf, textvariable=self.subtitle_var, width=22, state="readonly",
                              values=list(self.SUBTITLE_MODES.keys()))
        sub_cb.grid(row=10, column=1, columnspan=2, sticky=tk.W, padx=4, pady=(6, 0))
        sub_cb.bind("<<ComboboxSelected>>", lambda e: self._log_subtitle_choice())
        self._add_tooltip(sub_cb, "自动匹配视频同目录下同名 *.srt 字幕\n"
                                  "· 快速附加：直接拷贝原视频/音频流并挂上字幕，不重新编码（无损、秒级完成），"
                                  "但不做剪辑和缩放\n"
                                  "· 封装（随转码）：重新编码的同时封装软字幕，可按剪辑时间平移时间轴")

        # 转码完成删除原文件
        self.del_src_var = tk.BooleanVar(value=False)
        del_chk = ttk.Checkbutton(lf, text="转码完成删除原文件",
                                  variable=self.del_src_var,
                                  command=self._save_settings)
        del_chk.grid(row=11, column=0, columnspan=3, sticky=tk.W, padx=4, pady=(6, 0))
        self._add_tooltip(del_chk, "勾选后，每个视频转码成功即自动删除原文件（失败的文件保留）")

        # 处理后删除字幕文件
        self.del_srt_var = tk.BooleanVar(value=False)
        del_srt_chk = ttk.Checkbutton(lf, text="处理后删除字幕文件",
                                      variable=self.del_srt_var,
                                      command=self._save_settings)
        del_srt_chk.grid(row=12, column=0, columnspan=3, sticky=tk.W, padx=4, pady=(2, 0))
        self._add_tooltip(del_srt_chk, "处理成功且确实使用了字幕时，自动删除对应的 .srt 字幕文件"
                                       "（失败或未匹配到字幕的文件不受影响）")

        # 快速附加后覆盖原视频
        self.overwrite_src_var = tk.BooleanVar(value=False)
        ow_chk = ttk.Checkbutton(lf, text="快速附加后覆盖原视频（并删除字幕）",
                                 variable=self.overwrite_src_var,
                                 command=self._save_settings)
        ow_chk.grid(row=13, column=0, columnspan=3, sticky=tk.W, padx=4, pady=(2, 0))
        self._add_tooltip(ow_chk, "仅对“软字幕·快速附加（不重编码）”生效：生成的含字幕新视频直接替换原视频文件，"
                                  "并自动删除同名 .srt，即用“含字幕的新视频”替换掉“原视频 + 字幕文件”")

        # 编码器（优先显卡硬件编码，降低 CPU 占用）
        ttk.Label(lf, text="编码器:").grid(row=14, column=0, sticky=tk.W, pady=(6, 0))
        self.encoder_var = tk.StringVar(value=self.ENCODER_OPTIONS["auto"])
        enc_cb = ttk.Combobox(lf, textvariable=self.encoder_var, width=18, state="readonly",
                              values=list(self.ENCODER_OPTIONS.values()))
        enc_cb.grid(row=14, column=1, sticky=tk.W, padx=4, pady=(6, 0))
        enc_cb.bind("<<ComboboxSelected>>", lambda e: self._log_encoder_choice())

        # ffmpeg 路径
        ttk.Label(lf, text="ffmpeg路径:").grid(row=13, column=0, sticky=tk.W, pady=(6, 0))
        self.ffmpeg_var = tk.StringVar()
        ff_frame = ttk.Frame(lf)
        ff_frame.grid(row=13, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=(6, 0))
        ttk.Entry(ff_frame, textvariable=self.ffmpeg_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(ff_frame, text="浏览...", width=7, command=self._browse_ffmpeg).pack(side=tk.LEFT, padx=2)

        # 操作按钮：保存设置 / 开始转码 / 停止（同一样式，同一行，减少占用）
        btn_row = ttk.Frame(lf)
        btn_row.grid(row=12, column=0, columnspan=3, sticky=tk.EW, pady=(10, 0))
        ttk.Button(btn_row, text="💾 保存设置", width=12,
                   command=self._manual_save_settings).pack(side=tk.LEFT, padx=4)
        self.start_btn = ttk.Button(btn_row, text="▶ 开始转码", width=14,
                                    command=self._start_transcode)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(btn_row, text="⏹ 停止", width=12,
                                   command=self._stop_transcode, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=4)

    def _create_queue_area(self, parent):
        lf = ttk.LabelFrame(parent, text="任务队列", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 任务列表（高度缩减，横向/纵向均可滚动）
        list_top = ttk.Frame(lf)
        list_top.pack(fill=tk.BOTH, expand=True)
        list_top.grid_rowconfigure(0, weight=1)
        list_top.grid_columnconfigure(0, weight=1)

        # EXTENDED：支持 Ctrl/Shift 多选，配合“移除选中”可一次移除多个任务
        self.queue_listbox = tk.Listbox(list_top, height=4, font=('Consolas', 9),
                                        selectmode=tk.EXTENDED)
        qsb = ttk.Scrollbar(list_top, orient=tk.VERTICAL, command=self.queue_listbox.yview)
        hsb = ttk.Scrollbar(list_top, orient=tk.HORIZONTAL, command=self.queue_listbox.xview)
        self.queue_listbox.configure(yscrollcommand=qsb.set, xscrollcommand=hsb.set)
        self.queue_listbox.grid(row=0, column=0, sticky=tk.NSEW)
        qsb.grid(row=0, column=1, sticky=tk.NS)
        hsb.grid(row=1, column=0, sticky=tk.EW)

        # 总体进度条（所有任务 0~100%）
        prog_frame = ttk.Frame(lf)
        prog_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(prog_frame, text="总进度:").pack(side=tk.LEFT)
        self.overall_progress_var = tk.DoubleVar(value=0.0)
        self.overall_bar = ttk.Progressbar(prog_frame, mode='determinate',
                                           maximum=100.0, variable=self.overall_progress_var)
        self.overall_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        self.overall_pct_var = tk.StringVar(value="0%")
        ttk.Label(prog_frame, textvariable=self.overall_pct_var, width=5,
                  anchor=tk.E, font=('Consolas', 9, 'bold')).pack(side=tk.LEFT)

        # 按钮移到列表下方
        qbtns = ttk.Frame(lf)
        qbtns.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(qbtns, text="移除选中", command=self._remove_from_queue, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(qbtns, text="清空队列", command=self._clear_queue, width=10).pack(side=tk.LEFT, padx=2)

    def _create_log_area(self, parent):
        lf = ttk.LabelFrame(parent, text="日志", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text = scrolledtext.ScrolledText(lf, wrap=tk.WORD, height=8, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("info", foreground="black")
        self.log_text.tag_configure("success", foreground="#2E7D32")
        self.log_text.tag_configure("warning", foreground="#F57C00")
        self.log_text.tag_configure("error", foreground="#C62828")
        self.log_text.tag_configure("highlight", foreground="#1565C0")

    # ------------------------------------------------------------------
    # 日志 / 状态
    # ------------------------------------------------------------------
    def _log(self, message: str, level: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        if not self.is_standalone and self.controller and hasattr(self.controller, 'log'):
            self.controller.log(log_line)
        if hasattr(self, 'log_text') and self.log_text.winfo_exists():
            self.log_text.insert(tk.END, log_line + "\n", level)
            self.log_text.see(tk.END)

    def _update_status_bar(self, prefix: str = ""):
        if self.is_running:
            text = (f"{prefix}转码中: {self._done}/{self._total} | "
                    f"成功 {self._success} | 失败 {self._fail} | 取消 {self._cancel}")
        else:
            text = f"{prefix}队列 {len(self.task_queue)} 项 | 成功 {self._success} | 失败 {self._fail}"
        try:
            self.frame.after(0, lambda: self.status_bar_var.set(text))
        except Exception:
            pass

    @staticmethod
    def _get_int(var, default: int) -> int:
        try:
            return int(var.get())
        except Exception:
            return default

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _load_settings(self):
        s = self.config_manager.get_section(self.CONFIG_SECTION)
        self.save_src_var.set(s.get('save_mode', 'source') == 'source')
        self.save_dir_var.set(s.get('save_dir', ''))
        self.res_var.set(s.get('resolution', '1280x720'))
        self.cust_w_var.set(s.get('custom_w', ''))
        self.cust_h_var.set(s.get('custom_h', ''))
        self.orientation_var.set(s.get('orientation', '横向'))
        self.auto_orient_var.set(bool(s.get('auto_orient', True)))
        self.portrait_res_var.set(s.get('portrait_res', '自动(按源比例)'))
        self.aspect_var.set(s.get('aspect', '保持原比例'))
        self.bitrate_var.set(s.get('bitrate', 2500))
        self.conc_var.set(s.get('concurrency', 2))
        self.after_var.set(s.get('after_done', '无操作'))
        self.precision_var.set(s.get('precision', '0.1S'))
        self.ffmpeg_var.set(s.get('ffmpeg_path', ''))
        self.encoder_var.set(self.ENCODER_OPTIONS.get(
            s.get('encoder', 'auto'), self.ENCODER_OPTIONS['auto']))
        # 兼容旧配置：以前是布尔值（True=随转码封装字幕）
        sub = s.get('subtitle', "不添加字幕")
        if isinstance(sub, bool):
            sub = "软字幕·封装（随转码）" if sub else "不添加字幕"
        self.subtitle_var.set(sub if sub in self.SUBTITLE_MODES else "不添加字幕")
        self.del_src_var.set(bool(s.get('del_src', False)))
        self.del_srt_var.set(bool(s.get('del_srt', False)))
        self.overwrite_src_var.set(bool(s.get('overwrite_src', False)))
        # 已填目标文件夹就必须落到“保存到指定文件夹”，否则会被保存到视频原文件夹
        self._sync_save_mode_with_dir()
        self._apply_orientation()   # 加载后按已保存的方向校正分辨率
        self._on_res_preset()       # 同步“自定义分辨率”宽/高输入框的可用状态

    def _manual_save_settings(self):
        """点击“保存设置”按钮：写入配置文件并提示"""
        if self._save_settings():
            messagebox.showinfo("成功", "设置已保存到配置文件！")
            self._log("转码设置已保存到配置文件", "success")

    def _save_settings(self) -> bool:
        data = {
            'save_mode': 'source' if self.save_src_var.get() else 'custom',
            'save_dir': self.save_dir_var.get(),
            'resolution': self.res_var.get(),
            'custom_w': self.cust_w_var.get().strip(),
            'custom_h': self.cust_h_var.get().strip(),
            'orientation': self.orientation_var.get(),
            'auto_orient': bool(self.auto_orient_var.get()),
            'portrait_res': self.portrait_res_var.get(),
            'aspect': self.aspect_var.get(),
            'bitrate': self._get_int(self.bitrate_var, 2500),
            'concurrency': self._get_int(self.conc_var, 2),
            'after_done': self.after_var.get(),
            'precision': self.precision_var.get(),
            'ffmpeg_path': self.ffmpeg_var.get(),
            'encoder': self._encoder_choice_key(),
            'subtitle': self.subtitle_var.get(),
            'del_src': self.del_src_var.get(),
            'del_srt': self.del_srt_var.get(),
            'overwrite_src': self.overwrite_src_var.get(),
        }
        saved = self.config_manager.set_section(self.CONFIG_SECTION, data)
        return True if saved is None else bool(saved)

    def _sync_save_mode_with_dir(self):
        """目标文件夹非空时自动选中“保存到指定文件夹”，避免两个控件状态不一致

        否则会出现：填了目标文件夹、单选却仍是“保存到原视频所在文件夹”，
        导致转码结果仍写到视频原文件夹。
        """
        if self.save_dir_var.get().strip() and self.save_src_var.get():
            self.save_src_var.set(False)

    def _bind_settings_save(self):
        for var in (self.save_src_var, self.save_dir_var, self.res_var,
                    self.cust_w_var, self.cust_h_var,
                    self.orientation_var, self.auto_orient_var,
                    self.portrait_res_var, self.aspect_var, self.after_var,
                    self.precision_var, self.ffmpeg_var, self.encoder_var,
                    self.subtitle_var, self.del_src_var, self.del_srt_var,
                    self.overwrite_src_var):
            var.trace('w', lambda *a: self._save_settings())
        # 填写目标文件夹时同步单选按钮
        self.save_dir_var.trace('w', lambda *a: self._sync_save_mode_with_dir())

    def _browse_save_dir(self):
        folder = filedialog.askdirectory(title="选择保存文件夹")
        if folder:
            self.save_dir_var.set(folder)
            self.save_src_var.set(False)

    @staticmethod
    def _add_tooltip(widget, text: str):
        """为控件添加悬停提示（懒创建，鼠标移入显示、移出销毁）"""
        tipwin = [None]

        def enter(_e):
            if tipwin[0] is not None:
                return
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            ttk.Label(tw, text=text, relief=tk.SOLID, borderwidth=1,
                      background="#FFFFE0", padding=(6, 4),
                      font=('Microsoft YaHei', 9), wraplength=320).pack()
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw.wm_geometry(f"+{x}+{y}")
            tipwin[0] = tw

        def leave(_e):
            if tipwin[0] is not None:
                tipwin[0].destroy()
                tipwin[0] = None

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def _on_res_preset(self):
        """下拉选择分辨率后：选“自定义分辨率”则激活宽/高输入框，
        否则置灰；非自定义时仍按方向做横竖互换。"""
        custom = self.res_var.get() == "自定义分辨率"
        st = 'normal' if custom else 'disabled'
        for e in (self._cust_w_entry, self._cust_h_entry):
            try:
                e.configure(state=st)
            except Exception:
                pass
        if not custom:
            self._apply_orientation()

    def _resolve_custom_resolution(self) -> str:
        """解析当前分辨率设置：自定义时返回 “宽x高”，否则返回下拉值。"""
        if self.res_var.get() == "自定义分辨率":
            try:
                w = int(self.cust_w_var.get().strip() or 0)
                h = int(self.cust_h_var.get().strip() or 0)
            except ValueError:
                w = h = 0
            if w > 0 and h > 0:
                return f"{w}x{h}"
            return "1280x720"     # 未填合法宽高：回退，避免空值
        return self.res_var.get().strip() or "1280x720"

    def _apply_orientation(self):
        """按方向（横向/竖向）调整分辨率：竖向时宽高互换，如 1280x720 → 720x1280"""
        res = self.res_var.get().strip()
        if not res or res == "保持原分辨率":
            return
        try:
            w, h = [int(x) for x in res.lower().split('x')]
        except (ValueError, AttributeError):
            return
        want_portrait = self.orientation_var.get() == '竖向'
        if (h > w) != want_portrait:
            self.res_var.set(f"{h}x{w}")
            self._log(f"已按{'竖向' if want_portrait else '横向'}调整分辨率: {res} → {h}x{w}", "info")

    def _browse_ffmpeg(self):
        p = filedialog.askopenfilename(title="选择 ffmpeg.exe",
                                       filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if p:
            self.ffmpeg_var.set(p)

    # ------------------------------------------------------------------
    # 视频列表
    # ------------------------------------------------------------------
    def _browse_files(self):
        files = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.rmvb *.ts *.m2ts *.mpg *.mpeg *.webm"),
                       ("所有文件", "*.*")])
        if files:
            self._add_paths(list(files))

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="选择视频文件夹")
        if folder:
            self._add_paths([folder])

    @staticmethod
    def _norm_key(p: str) -> str:
        """把路径归一化为去重集合使用的标准键。

        拖放/浏览得到的路径与 Path() 规范化后的路径在分隔符（/ 与 \\）、
        大小写、相对/绝对形式上可能不一致，必须统一后才能正确去重，
        否则会出现“移除后重新添加提示已在列表中”的问题。
        """
        try:
            return os.path.normcase(os.path.abspath(str(p).strip('"')))
        except Exception:
            return str(p).strip('"').lower()

    def _add_paths(self, paths: List[str]):
        """添加文件/文件夹（文件夹仅扫描第一层视频文件），返回实际新增的文件列表"""
        video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.rmvb',
                      '.ts', '.m2ts', '.mpg', '.mpeg', '.webm'}
        files: List[str] = []
        for p in paths:
            p = str(p).strip('"')
            if os.path.isdir(p):
                try:
                    for name in os.listdir(p):
                        fp = os.path.join(p, name)
                        if os.path.isfile(fp) and os.path.splitext(fp)[1].lower() in video_exts:
                            files.append(fp)
                except OSError as e:
                    self._log(f"读取文件夹失败 {p}: {e}", "error")
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in video_exts:
                files.append(p)

        new_files = [f for f in files if self._norm_key(f) not in self._paths]
        if not new_files:
            if files:
                self._log("所选视频均已在列表中", "warning")
            else:
                self._log("未找到视频文件", "warning")
            return []

        self._log(f"正在读取 {len(new_files)} 个视频信息...", "highlight")
        threading.Thread(target=self._load_metadata_thread, args=(new_files,), daemon=True).start()
        return new_files

    def add_videos(self, paths: List[str]) -> int:
        """对外接口：把视频文件/文件夹加入待转码列表，返回实际新增数量

        供其它模块（如文件夹检查/比对）调用，将选中的未转码视频批量加入转码队列。
        """
        return len(self._add_paths(paths))

    def _load_metadata_thread(self, files: List[str]):
        """后台线程读取视频元数据，逐条插入列表"""
        for f in files:
            key = self._norm_key(f)
            if key in self._paths:
                continue
            item = self._probe_video(f)
            self._paths.add(key)
            try:
                self.frame.after(0, self._insert_item, item)
            except Exception:
                return

    def _probe_stream_bitrate_kbps(self, path: str) -> float:
        """用 ffprobe 读取视频流码率（kbps）。

        只统计视频流，与资源管理器“数据速率 / Data rate”口径一致（不含音频；
        音频在转码时固定 -b:a 128k）。返回 0 表示读取失败，调用方回退估算值。
        """
        ffprobe = self._get_ffprobe()   # 复用已有实现（ffmpeg 同目录 -> PATH，带缓存）
        if not ffprobe:
            return 0.0
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            # 注意：-select_streams 不支持 "v+a" 这种写法（会报 Invalid stream specifier），
            # 故一次性取出所有流的类型与码率，只累加 video / audio（排除字幕、封面等）
            res = subprocess.run(
                [ffprobe, '-v', 'error', '-show_entries', 'stream=codec_type,bit_rate',
                 '-of', 'csv=p=0', path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=20, creationflags=flags)
            total = 0
            for line in res.stdout.decode('utf-8', 'replace').splitlines():
                # 每行形如 "video,1797735" / "audio,128971"，码率可能为 N/A
                parts = [x.strip() for x in line.split(',')]
                # 只取视频流，与资源管理器“数据速率”一致（音频不计入）
                if len(parts) >= 2 and parts[0] == 'video':
                    if parts[-1].isdigit():
                        total += int(parts[-1])
            return total / 1000.0 if total > 0 else 0.0
        except Exception:
            return 0.0

    def _probe_video(self, path: str) -> VideoListItem:
        p = Path(path)
        item = VideoListItem(path=str(p), ext=p.suffix[1:].upper())
        try:
            item.size = p.stat().st_size
        except OSError:
            pass
        if HAS_CV2:
            try:
                cap = cv2.VideoCapture(str(p))
                if cap.isOpened():
                    item.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    item.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = cap.get(cv2.CAP_PROP_FPS) or 0
                    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                    if fps > 0 and frames > 0:
                        item.duration = frames / fps
                        item.fps = fps
                    cap.release()
            except Exception:
                pass
        # ffprobe 兜底：OpenCV（内置 FFmpeg）解不了的容器/编码——如 rm/rmvb、
        # WMV9/VC-1、部分 flv/ts——会得到 0 宽高与 0 时长，导致“加入队列后只输出
        # 0.01 秒”的空片段。这里用 ffprobe 补齐时长/分辨率/帧率（系统 ffmpeg 原生
        # 支持 rv30/rv40、wmv1/2/3、vc1、cook 等解码，无需额外插件）。
        if not (item.duration > 0 and item.width > 0 and item.fps > 0):
            dur, w, h, fps = self._ffprobe_video_meta(str(p))
            if dur > 0 and item.duration <= 0:
                item.duration = dur
            if w > 0 and item.width <= 0:
                item.width = w
            if h > 0 and item.height <= 0:
                item.height = h
            if fps > 0 and item.fps <= 0:
                item.fps = fps
            if dur > 0 or w > 0:
                self._log(f"[探测] {p.name}: OpenCV 无法读取，已用 ffprobe 兜底"
                          f"（{w}x{h} @{fps:g}fps, {dur:.2f}s）", "info")
        if item.duration > 0 and item.size > 0:
            # 估算值：整个文件（含容器头/索引等开销）÷ 时长，通常比真实码率偏高
            item.bitrate_kbps = item.size * 8 / 1000 / item.duration
        # 优先采用 ffprobe 读到的真实流码率（视频+音频），读不到时保留上面的估算值
        real_bitrate = self._probe_stream_bitrate_kbps(str(p))
        if real_bitrate > 0:
            item.bitrate_kbps = real_bitrate
        item.end = item.duration  # 默认从头到尾
        return item

    def _ffprobe_video_meta(self, path: str):
        """用 ffprobe 读 (时长, 宽, 高, 帧率)；读不到的项返回 0"""
        fp = self._get_ffprobe()
        if not fp:
            return 0.0, 0, 0, 0.0
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        dur, w, h, fps = 0.0, 0, 0, 0.0
        try:
            res = subprocess.run(
                [fp, '-v', 'error', '-show_entries',
                 'format=duration:stream=codec_type,width,height,r_frame_rate',
                 '-of', 'json', path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=30, creationflags=flags)
            data = json.loads(res.stdout or '{}')
            dur = float(data.get('format', {}).get('duration') or 0)
            for s in data.get('streams', []):
                if s.get('codec_type') != 'video':
                    continue
                w = int(s.get('width') or 0)
                h = int(s.get('height') or 0)
                num, _, den = str(s.get('r_frame_rate') or '0/1').partition('/')
                try:
                    fps = float(num) / float(den or 1)
                except (ValueError, ZeroDivisionError):
                    fps = 0.0
                break
        except Exception:
            pass
        return dur, w, h, fps

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / 1024 ** 2:.1f} MB"
        else:
            return f"{size_bytes / 1024 ** 3:.2f} GB"

    def _insert_item(self, item: VideoListItem):
        res = f"{item.width}x{item.height}" if item.width > 0 else "未知"
        bitrate = f"{int(item.bitrate_kbps)} kbps" if item.bitrate_kbps > 0 else "未知"
        iid = self.tree.insert("", tk.END, values=(
            Path(item.path).name, self._format_size(item.size), item.ext,
            res, bitrate, format_clock(item.start), format_clock(item.end)))
        self.items[iid] = item
        self._log(f"已添加: {Path(item.path).name} ({res}, {self._format_size(item.size)})", "info")

    def _sort_by(self, col: str):
        """点击表头按该列排序：首次点击为升序，再次点击同一列切换为降序，表头显示 ▲/▼ 指示"""
        # 同列再次点击则翻转方向；切换到其它列时从升序开始
        new_reverse = (not self._sort_reverse) if self._sort_col == col else False
        self._sort_col = col
        self._sort_reverse = new_reverse
        pairs = list(self.items.items())

        def key(kv):
            it = kv[1]
            if col == "size":
                return it.size or 0
            if col == "bitrate":
                return it.bitrate_kbps or 0
            if col == "start":
                return it.start
            if col == "end":
                return it.end
            if col == "res":
                return (it.width or 0, it.height or 0)
            if col == "name":
                return Path(it.path).name.lower()
            if col == "ext":
                return (it.ext or "").lower()
            return 0

        pairs.sort(key=key, reverse=new_reverse)
        for idx, (iid, _it) in enumerate(pairs):
            self.tree.move(iid, "", idx)
        self._update_sort_indicator(col, new_reverse)

    def _update_sort_indicator(self, col: str, reverse: bool):
        """刷新各列表头文本，在激活列上标注排序箭头"""
        arrow = " ▼" if reverse else " ▲"
        for c, h in self._col_headers.items():
            self.tree.heading(c, text=(h + arrow) if c == col else h)

    def _remove_selected(self):
        for iid in self.tree.selection():
            item = self.items.pop(iid, None)
            if item:
                # 用与加入时相同的归一化键删除，避免路径分隔符/大小写差异导致残留
                self._paths.discard(self._norm_key(item.path))
            self.tree.delete(iid)

    def _clear_list(self):
        self.player.release()
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self._paths.clear()
        self._current_iid = None
        self.time_var.set("00:00:00.00 / 00:00:00.00")
        self._log("视频列表已清空（任务队列保留）", "info")

    # ------------------------------------------------------------------
    # 播放器 / 剪辑
    # ------------------------------------------------------------------
    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        self._current_iid = sel[0]
        item = self.items.get(self._current_iid)
        if not item:
            return
        if not self.player.load(item.path):
            self._log(f"无法打开视频: {Path(item.path).name}", "error")
            return
        # 切换视频时，恢复该视频“已保存”的剪辑时间（而非强制复位为默认）。
        # 用 _loading_item 挂起 _on_trim_edit 的 trace 写回：否则先 set 的 start_var
        # 会带着上一个视频残留的 end_var 值串写进当前条目（结束滑块“自动提前”的根源）。
        self._loading_item = True
        try:
            self.start_var.set(format_clock(item.start))
            self.end_var.set(format_clock(item.end))
        finally:
            self._loading_item = False
        self._refresh_trim_columns()
        self._update_trim_bar()
        # 播放器跳转到该视频已设置的开始时间，并以保存的开始/结束时间作为当前片段范围
        t0 = item.start
        if self.player.cap is not None:
            self.player.seek(t0)
        self._on_player_time(t0)

    def _refresh_trim_columns(self, iid: Optional[str] = None):
        """把条目的开始/结束时间刷新到列表对应列，保持界面与数据一致"""
        iid = iid or self._current_iid
        if not iid or iid not in self.items:
            return
        item = self.items[iid]
        self.tree.set(iid, "start", format_clock(item.start))
        self.tree.set(iid, "end", format_clock(item.end))

    def _sync_seek_range(self):
        """根据当前视频时长同步剪辑范围条量程与播放位置游标"""
        dur = max(self.player.duration, 0.01)
        self.trim_bar.set_position(0.0)
        self._update_trim_bar()

    def _on_player_time(self, t: float):
        self.time_var.set(f"{format_clock(t)} / {format_clock(self.player.duration)}")
        self.trim_bar.set_position(t)

    def _on_player_state(self, playing: bool):
        self.play_btn.config(text="⏸ 暂停" if playing else "▶ 播放")

    # ---- 轨道点击/拖拽定位（替代原进度条） ----
    def _on_track_seek(self, t: float):
        """点按/拖拽双滑块条轨道空白区：定位播放位置（不改动开始/结束时间）"""
        if self.player.cap is None:
            return
        if self.player.playing:
            self.player.play(from_time=t)
        else:
            self.player.seek(t)

    def _toggle_play(self):
        self.player.toggle()

    def _on_tree_key(self, event):
        """视频列表上的按键：转交面板快捷键处理，并拦截 Treeview 默认行为"""
        return self._on_panel_key(event)

    def _on_panel_key(self, event):
        """键盘快捷键（面板级，输入框聚焦时不拦截以免影响输入）：
        - 空格：播放/暂停当前选中视频
        - 左/右方向键：视频定位前/后 1 秒
        """
        w = event.widget
        # 焦点在输入框/下拉/按钮/文本域时不接管按键，避免干扰正常输入与按钮触发
        if isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, ttk.Spinbox,
                         ttk.Button, tk.Button, tk.Text)):
            return
        if self.player.cap is None:
            return
        sym = event.keysym
        if sym == 'space':
            self.player.toggle()
            return 'break'
        if sym == 'Left':
            self._scrub(-1.0)
            return 'break'
        if sym == 'Right':
            self._scrub(1.0)
            return 'break'

    def _scrub(self, delta: float):
        """以当前播放位置为基准前后定位 delta 秒（默认 1 秒），并同步到激活时间框"""
        if self.player.cap is None:
            return
        t = self.player.current_time + delta
        t = max(0.0, t)
        if self.player.duration > 0:
            t = min(t, self.player.duration)
        if self.player.playing:
            self.player.play(from_time=t)
        else:
            self.player.seek(t)
        self._sync_active_time(t)

    def _toggle_blank(self):
        """隐藏/显示播放器画面（黑屏），不影响播放状态与时间"""
        blank = not self.player.blanked
        self.player.set_blank(blank)
        try:
            self.blank_btn.config(text="显示屏幕" if blank else "隐藏屏幕")
        except Exception:
            pass

    def _nudge(self, delta: float):
        """按当前激活时间框的时间严格前后移动整秒，并同步播放器画面"""
        if self.player.cap is None:
            return
        which = getattr(self, '_active_time_field', 'start')
        var = self.start_var if which == 'start' else self.end_var
        base = parse_clock(var.get())
        if base is None:
            base = self.player.current_time
        target = max(0.0, round(base + delta, 2))
        if self.player.duration > 0:
            target = min(target, self.player.duration)
        var.set(format_clock(target))
        self.player.seek(target)

    def _mark_active_time(self, which: str):
        """记录当前激活的时间框/滑块（'start' / 'end'），并同步高亮显示"""
        self._active_time_field = which
        try:
            self.start_lbl.config(foreground='#0a66d0' if which == 'start' else '#333333')
            self.end_lbl.config(foreground='#0a66d0' if which == 'end' else '#333333')
        except Exception:
            pass
        if hasattr(self, 'trim_bar'):
            self.trim_bar.set_active(which)

    def _sync_active_time(self, t: float):
        """把播放器位置同步写入当前激活的时间框"""
        which = getattr(self, '_active_time_field', 'start')
        (self.start_var if which == 'start' else self.end_var).set(format_clock(t))

    # ---- 剪辑范围双滑块 ----
    def _on_trim_slider_select(self, which: str):
        """点击滑块：把它设为当前激活滑块，之后 ±/方向键/设为当前 都作用于它"""
        self._mark_active_time(which)

    def _on_trim_slider_drag(self, which: str, t: float):
        """拖动滑块：写回对应时间框（自动同步列表条目），播放器画面跟随该滑块"""
        (self.start_var if which == 'start' else self.end_var).set(format_clock(t))
        if self.player.cap is not None:
            self.player.seek(t)
        self._update_trim_bar()

    def _update_trim_bar(self):
        """把当前时间框/视频时长/激活滑块同步到剪辑范围条"""
        if not hasattr(self, 'trim_bar'):
            return
        dur = max(self.player.duration, 0.01)
        s = parse_clock(self.start_var.get())
        e = parse_clock(self.end_var.get())
        self.trim_bar.set_values(dur,
                                 0.0 if s is None else s,
                                 dur if e is None else e,
                                 getattr(self, '_active_time_field', 'start'))

    def _on_time_return(self, which: str):
        """时间框回车：播放中则从新时间继续播放；否则仅定位并显示该帧（保持暂停）"""
        self._mark_active_time(which)
        if self.player.cap is None:
            return
        var = self.start_var if which == 'start' else self.end_var
        t = parse_clock(var.get())
        if t is None:
            return
        if self.player.duration > 0:
            t = min(t, self.player.duration)
        if self.player.playing:
            self.player.play(from_time=t)
        else:
            self.player.seek(t)

    def _precision(self) -> float:
        return self.PRECISIONS.get(self.precision_var.get(), 0.1)

    def _adjust_time(self, which: str, direction: int):
        """按精度微调开始/结束时间，并同步跳转播放器画面与进度条"""
        self._mark_active_time(which)
        var = self.start_var if which == 'start' else self.end_var
        t = parse_clock(var.get())
        if t is None:
            t = 0.0
        t = max(0.0, round(t + direction * self._precision(), 2))
        var.set(format_clock(t))
        # 播放器跳转到该时间点，同步更新进度条和对应视频画面（即使只调0.01秒）
        if self.player.cap is not None:
            self.player.seek(t)

    def _set_time_from_player(self, which: str):
        """将播放器当前时间设为开始/结束时间"""
        self._mark_active_time(which)
        t = self.player.current_time
        (self.start_var if which == 'start' else self.end_var).set(format_clock(t))

    def _move_slider(self, which: str, position: str):
        """把剪辑范围的滑块快速移到片头 / 片尾。

        which: 'start'（前滑块=开始时间）/ 'end'（后滑块=结束时间）
        position: 'first'（最开始时间，即 00:00:00）/ 'last'（最后时间，即视频时长）
        """
        if self.player.cap is None:
            return
        self._mark_active_time(which)
        dur = self.player.duration
        t = 0.0 if position == 'first' else (dur if dur > 0 else 0.0)
        (self.start_var if which == 'start' else self.end_var).set(format_clock(t))
        # 播放器画面跟随该滑块移动到目标时间（播放中则从该时间继续播放）
        if self.player.playing:
            self.player.play(from_time=t)
        else:
            self.player.seek(t)
        self._update_trim_bar()

    def _preview_clip(self):
        """点击“播放片段”：立即跳到开始时间播放，到结束时间停止"""
        if self.player.cap is None:
            messagebox.showwarning("提示", "请先在视频列表中选择一个视频")
            return
        s = parse_clock(self.start_var.get())
        e = parse_clock(self.end_var.get())
        if s is None or e is None:
            messagebox.showwarning("提示", "时间格式无效，请输入 HH:MM:SS.hh 格式")
            return
        # 结束时间超过片尾时按片尾停止
        if self.player.duration > 0:
            e = min(e, self.player.duration)
            s = min(s, max(self.player.duration - 0.01, 0.0))
        if e <= s:
            messagebox.showwarning("提示", "请先设置有效的开始/结束时间（结束需大于开始）")
            return
        self.player.play(from_time=s, stop_at=e)

    def _on_trim_edit(self, *_args):
        """开始/结束时间被修改时实时写回当前选中的列表条目。

        这样“加入队列 / 开始转码”会直接采用屏幕上显示的时间，无需再点“✔ 保存到选中视频”。
        仅在时间合法（结束>开始、未超片长）时提交，避免输入过程中的中间值污染条目。
        """
        if self._loading_item:
            return
        if not self._current_iid or self._current_iid not in self.items:
            return
        item = self.items[self._current_iid]
        s = parse_clock(self.start_var.get())
        e = parse_clock(self.end_var.get())
        if s is None or e is None or e <= s:
            return
        if item.duration > 0 and s >= item.duration:
            return
        item.start = s
        item.end = min(e, item.duration) if item.duration > 0 else e
        self._refresh_trim_columns()
        self._update_trim_bar()

    def _apply_trim(self):
        """确认后保存到视频列表中"""
        if not self._current_iid or self._current_iid not in self.items:
            messagebox.showwarning("提示", "请先在列表中选择一个视频")
            return
        item = self.items[self._current_iid]
        s = parse_clock(self.start_var.get())
        e = parse_clock(self.end_var.get())
        if s is None or e is None:
            messagebox.showerror("错误", "时间格式无效，请输入 HH:MM:SS.hh 格式")
            return
        if e <= s:
            messagebox.showerror("错误", "结束时间必须大于开始时间")
            return
        if item.duration > 0 and s >= item.duration:
            messagebox.showerror("错误", "开始时间超出视频时长")
            return
        e = min(e, item.duration) if item.duration > 0 else e
        item.start = s
        item.end = e
        self.tree.set(self._current_iid, "start", format_clock(s))
        self.tree.set(self._current_iid, "end", format_clock(e))
        self._log(f"已保存剪辑时间: {Path(item.path).name} "
                  f"[{format_clock(s)} ~ {format_clock(e)}]", "success")

    # ------------------------------------------------------------------
    # 任务队列
    # ------------------------------------------------------------------
    @staticmethod
    def _frame_index_of(t: float, fps: float) -> Optional[int]:
        """把时间换算为“不早于该时间的第一帧”序号（与 ffmpeg -ss 的取帧规则一致）"""
        if not fps or fps <= 1e-6:
            return None
        return max(0, int(math.ceil(t * fps - 1e-6)))

    @classmethod
    def _snap_to_frame(cls, t: float, fps: float) -> float:
        """把时间对齐到源视频帧格：0.1 秒精度设定也能落到确定的某一帧上"""
        idx = cls._frame_index_of(t, fps)
        return t if idx is None else idx / fps

    def _join_queue(self, only_selected: bool = False):
        if not self.items:
            messagebox.showwarning("提示", "视频列表为空，请先添加视频")
            return
        save_mode = 'source' if self.save_src_var.get() else 'custom'
        if save_mode == 'custom' and not self.save_dir_var.get().strip():
            messagebox.showwarning("提示", "请先设置保存文件夹")
            return
        added = 0
        sub_mode = self._subtitle_mode()
        # “加入选中”：仅把列表中 Ctrl/Shift 多选的项加入队列（按列表显示顺序）；
        # 未选中任何项时提示。“加入所有”：把列表全部视频加入队列。
        if only_selected:
            sel_set = set(self.tree.selection())
            targets = [self.items[i] for i in self.tree.get_children()
                       if i in sel_set and i in self.items]
            if not targets:
                messagebox.showinfo("提示", "请先在视频列表中选中要加入队列的视频\n"
                                            "（Ctrl+点击 多选，Shift+点击 连选）")
                return
            self._log(f"已选中 {len(targets)} 个视频，仅将选中项加入队列", "highlight")
        else:
            targets = list(self.items.values())
            self._log(f"将列表全部 {len(targets)} 个视频加入队列", "highlight")
        # 拼接模式：把多个视频合并为一个任务（各段沿用自己设定的起止时间）
        if getattr(self, 'merge_var', None) is not None and self.merge_var.get():
            self._join_merge(targets, save_mode, sub_mode)
            return
        for item in targets:
            # 起点对齐到源视频帧格，保证“预览画面 = 转码首帧”
            s = self._snap_to_frame(item.start, item.fps)
            if abs(s - item.start) > 1e-3:
                self._log(f"{Path(item.path).name}: 起点按源帧率对齐 "
                          f"{item.start:.3f}s → {s:.3f}s", "info")
            end = item.end
            if sub_mode == 'soft_fast':
                # 不重编码就无法精确裁剪（只能落在关键帧上）。若本任务实际设置了剪辑，
                # 自动改用“软字幕·封装（随转码）”，既能裁剪又能挂字幕，避免剪辑被静默丢弃
                if item.start > 0.01 or (item.duration > 0 and item.end < item.duration - 0.01):
                    self._log(f"{Path(item.path).name}: “软字幕·快速附加（不重编码）”与剪辑冲突，"
                              f"已自动改用“软字幕·封装（随转码）”以同时保留剪辑与字幕", "warning")
                    sub_mode = 'soft'
                else:
                    s, end = 0.0, (item.duration if item.duration > 0 else item.end)
            task = TranscodeTask(
                src=item.path, start=s, end=end,
                src_w=item.width, src_h=item.height, fps=item.fps,
                resolution=self._resolve_custom_resolution(),
                aspect=self.aspect_var.get(),
                bitrate=self._get_int(self.bitrate_var, 2500),
                save_mode=save_mode,
                save_dir=self.save_dir_var.get().strip(),
                auto_orient=bool(self.auto_orient_var.get()),
                portrait_res=self.portrait_res_var.get().strip() or "自动(按源比例)",
            )
            # 字幕模式：查找视频同目录下的同名 srt（找不到则该任务按“不添加”处理）
            task.subtitle_mode = sub_mode
            if sub_mode != 'none':
                task.subtitle = self._find_subtitle(item.path) or ""
                if not task.subtitle:
                    task.subtitle_mode = 'none'
                elif sub_mode == 'soft_fast':
                    # 沿用源容器，最利于直接拷贝流（mp4/mov 保持原样，其他落到 mkv）
                    src_ext = Path(item.path).suffix.lower().lstrip('.')
                    task.out_ext = src_ext if src_ext in ('mp4', 'mov', 'm4v', 'mkv') else 'mkv'
                    # 覆盖原视频：用新生成的含字幕视频替换原视频（随后还会删除 srt）
                    if self.overwrite_src_var.get():
                        task.overwrite_src = True
                        if save_mode == 'custom':
                            self._log(f"{Path(item.path).name}: 已开启覆盖模式，"
                                      f"忽略“保存到指定文件夹”，直接替换原视频", "warning")
            task.q_index = len(self.task_queue)
            task.status = "待处理 0%"
            task.progress = 0.0
            if task.subtitle_mode == 'soft_fast':
                tag = ("[原画质·快速附加软字幕→覆盖原视频]" if task.overwrite_src
                       else "[原画质·快速附加软字幕]")
                task.display = (f"{task.q_index + 1}. {Path(item.path).name} {tag}")
            else:
                task.display = (f"{task.q_index + 1}. {Path(item.path).name} "
                                f"[{format_clock(s)}~{format_clock(end)}] "
                                f"{task.resolution} {task.bitrate}k")
            self.task_queue.append(task)
            self.queue_listbox.insert(tk.END, f"{task.display}  [{task.status}]")
            added += 1
        self._log(f"已将 {added} 个视频加入任务队列", "highlight")
        if sub_mode != 'none':
            with_sub = sum(1 for t in self.task_queue if t.subtitle)
            missing = len(self.task_queue) - with_sub
            self._log(f"字幕：{with_sub}/{len(self.task_queue)} 个任务匹配到同名 srt"
                      + (f"，{missing} 个未找到" if missing else ""), "info")
        self._update_status_bar()
        self._update_overall_progress()

    def _join_merge(self, targets, save_mode: str, sub_mode: str):
        """拼接模式：把多个视频按列表顺序合并为一个转码任务

        - 所有片段共用同一套输出参数（分辨率/比例/码率/编码器/容器/字幕方式）；
        - 每个片段仍沿用自己设定的起止时间（前一段剪完后接上后一段，以此类推）；
        - 片段自带同名 srt 时会随该段一起封装，时间轴按“本段起点”平移，
          拼接后整体字幕自然对齐（详见 _run_merge）。
        """
        if len(targets) < 2:
            messagebox.showinfo("提示", "拼接模式需要选中至少 2 个视频\n"
                                        "（Ctrl+点击 多选，Shift+点击 连选）")
            return
        if sub_mode == 'soft_fast':
            # 合并必须重编码（统一规格 + 逐段精确剪辑），快速附加（流拷贝）不适用
            self._log("[合并] “软字幕·快速附加（不重编码）”不适用于拼接，"
                      "已改用“软字幕·封装（随转码）”", "warning")
            sub_mode = 'soft'

        segments = []
        for item in targets:
            # 起点对齐到源视频帧格，保证“预览画面 = 转码首帧”
            s = self._snap_to_frame(item.start, item.fps)
            if abs(s - item.start) > 1e-3:
                self._log(f"{Path(item.path).name}: 起点按源帧率对齐 "
                          f"{item.start:.3f}s → {s:.3f}s", "info")
            end = item.end
            if item.duration > 0 and end <= s:
                self._log(f"[合并] 跳过 {Path(item.path).name}：起止时间无效"
                          f"（{format_clock(s)}~{format_clock(end)}）", "warning")
                continue
            srt = ""
            if sub_mode != 'none':
                srt = self._find_subtitle(item.path) or ""
                if not srt:
                    self._log(f"[合并] {Path(item.path).name}: 未找到同名 srt，"
                              f"该段按无字幕处理", "info")
            segments.append({"src": item.path, "start": s, "end": end,
                             "src_w": item.width, "src_h": item.height,
                             "fps": item.fps, "srt": srt})

        if len(segments) < 2:
            messagebox.showwarning("提示", "有效片段不足 2 个，无法拼接")
            return

        first = Path(segments[0]['src'])
        task = TranscodeTask(
            src=str(first), start=segments[0]['start'], end=segments[0]['end'],
            src_w=segments[0].get('src_w', 0), src_h=segments[0].get('src_h', 0),
            fps=segments[0].get('fps', 0),
            resolution=self._resolve_custom_resolution(),
            aspect=self.aspect_var.get(),
            bitrate=self._get_int(self.bitrate_var, 2500),
            keep_bitrate=False,       # 面板未提供该选项，合并按设定码率统一编码
            auto_orient=bool(self.auto_orient_var.get()),
            portrait_res=self.portrait_res_var.get().strip() or "自动(按源比例)",
            save_mode=save_mode,
            save_dir=self.save_dir_var.get().strip(),
            out_ext='mp4',            # 合并成片统一输出 mp4（H.265）
            subtitle_mode=sub_mode,
            subtitle=segments[0].get('srt', ''),
            keep_quality=False,        # 拼接必须重编码以统一规格
            merge=True,
            merge_name=f"{first.stem}_合并",
            segments=segments,
        )
        task.q_index = len(self.task_queue)
        task.status = "待处理 0%"
        task.progress = 0.0
        total_dur = sum(max(0.05, s['end'] - s['start']) for s in segments)
        task.display = (f"{task.q_index + 1}. 🔗 合并 {len(segments)} 段 "
                        f"→ {task.merge_name}.{task.out_ext} "
                        f"（共 {format_clock(total_dur)}）")
        self.task_queue.append(task)
        self.queue_listbox.insert(tk.END, f"{task.display}  [{task.status}]")
        self._log(f"[合并] 已加入队列：{len(segments)} 个片段按顺序拼接 → "
                  f"{task.merge_name}.{task.out_ext}（总时长 {format_clock(total_dur)}）",
                  "highlight")
        for i, s in enumerate(segments, 1):
            self._log(f"    第 {i} 段: {Path(s['src']).name} "
                      f"[{format_clock(s['start'])}~{format_clock(s['end'])}]"
                      f"{'（含字幕）' if s.get('srt') else ''}")
        self._update_status_bar()
        self._update_overall_progress()

    def _clear_queue(self):
        if self.is_running:
            messagebox.showwarning("提示", "转码进行中，无法清空队列")
            return
        self.task_queue.clear()
        self.queue_listbox.delete(0, tk.END)
        self._log("任务队列已清空", "warning")
        self._update_status_bar()
        self._update_overall_progress()

    def _remove_from_queue(self):
        """从任务队列中移除选中的任务（转码进行中禁止操作）"""
        if self.is_running:
            messagebox.showwarning("提示", "转码进行中，无法移除队列任务")
            return
        sel = self.queue_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在任务队列中选择要移除的任务")
            return
        # 倒序删除，避免索引偏移
        remove_idxs = sorted(int(i) for i in sel)
        removed_count = 0
        for idx in reversed(remove_idxs):
            if 0 <= idx < len(self.task_queue):
                removed = self.task_queue.pop(idx)
                self._log(f"已移除队列任务: {Path(removed.src).name}", "info")
                removed_count += 1
        # 重建列表显示并复位 q_index
        self.queue_listbox.delete(0, tk.END)
        for new_idx, task in enumerate(self.task_queue):
            task.q_index = new_idx
            self.queue_listbox.insert(tk.END, f"{task.display}  [{task.status}]")
        self._log(f"共移除 {removed_count} 个队列任务，剩余 {len(self.task_queue)} 个", "highlight")
        self._update_status_bar()
        self._update_overall_progress()

    # ------------------------------------------------------------------
    # 转码执行
    # ------------------------------------------------------------------
    def _get_ffmpeg(self) -> Optional[str]:
        p = self.ffmpeg_var.get().strip()
        if p and os.path.isfile(p):
            return p
        return shutil.which('ffmpeg')

    def _get_ffprobe(self) -> Optional[str]:
        """ffprobe 路径：优先取 ffmpeg 同目录，其次 PATH"""
        cached = getattr(self, '_ffprobe_path', None)
        if cached:
            return cached
        cand = None
        ff = self._get_ffmpeg()
        if ff:
            guess = os.path.join(os.path.dirname(ff),
                                 os.path.basename(ff).replace('ffmpeg', 'ffprobe'))
            cand = guess if os.path.isfile(guess) else None
        if not cand:
            cand = shutil.which('ffprobe')
        self._ffprobe_path = cand
        return cand

    def _source_bit_depth(self, src: str) -> int:
        """探测源视频像素位深（8 / 10 / 12...），探测失败按 8 位处理（不阻断转码）。

        背景：>8bit（如常见的 Hi10P / yuv420p10le）经 NVDEC 解码后的 CUDA surface
        无法被 scale_cuda / pad_cuda 处理，实测必报
        "[vf#0:0] Task finished with error code: -40 (Function not implemented)"。
        提前探测即可跳过这条必然失败的全显存流水线。
        """
        cache = getattr(self, '_bit_depth_cache', None)
        if cache is None:
            cache = self._bit_depth_cache = {}
        if src in cache:
            return cache[src]
        depth = 8
        fp = self._get_ffprobe()
        if fp:
            try:
                creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                res = subprocess.run(
                    [fp, '-v', 'error', '-select_streams', 'v:0',
                     '-show_entries', 'stream=pix_fmt', '-of', 'csv=p=0', src],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace',
                    timeout=30, creationflags=creation)
                fmt = (res.stdout or '').strip().lower()
                m = re.search(r'p(\d+)', fmt)      # yuv420p10le / p010le -> 10
                if m:
                    depth = int(m.group(1))
            except Exception:
                depth = 8
        cache[src] = depth
        return depth

    # ---- 编码器：优先显卡硬件编码 ----
    def _subtitle_mode(self) -> str:
        """把字幕下拉显示名转换为内部 key（none/soft_fast/soft）"""
        return self.SUBTITLE_MODES.get(self.subtitle_var.get().strip(), "none")

    def _log_subtitle_choice(self):
        mode = self._subtitle_mode()
        if mode == 'soft_fast':
            self._log("字幕：快速附加模式 — 直接拷贝原视频/音频流并挂上软字幕，"
                      "不重新编码（无损、秒级完成），不做剪辑与缩放", "success")
        elif mode == 'soft':
            self._log("字幕：随转码封装软字幕（会重新编码，可按剪辑时间平移时间轴）", "info")
        else:
            self._log("字幕：不处理字幕", "info")

    def _encoder_choice_key(self) -> str:
        """把下拉显示名转换为内部 key（auto/nvenc/amf/qsv/cpu）"""
        name = self.encoder_var.get().strip()
        for key, disp in self.ENCODER_OPTIONS.items():
            if disp == name:
                return key
        return 'auto'

    def _log_encoder_choice(self):
        """选择编码器后即时提示（用列表快速判断，真正可用性在开始转码时探针验证）"""
        key = self._encoder_choice_key()
        if key == 'cpu':
            self._log("编码器已切换为 CPU 软编码（libx265），CPU 占用较高", "warning")
            return
        listed = self._list_hw_encoders()
        gpu = self._nvidia_gpu_info()
        if key == 'auto':
            if gpu:
                self._log(f"显卡: {gpu}", "info")
            self._log("自动模式：开始转码时会优先选用显卡硬编（无需硬件时自动回退 CPU）", "info")
            return
        self._log(f"已选择 {self.ENCODER_OPTIONS[key]}，" +
                  ("ffmpeg 已编译支持" if key in listed else "当前 ffmpeg 未支持，运行时会回退 CPU"),
                  "info" if key in listed else "warning")

    def _nvidia_gpu_info(self) -> Optional[str]:
        """用 nvidia-smi 获取显卡信息（与 web_grab_tab 的 translation_env_report 同思路）。

        单看 `ffmpeg -encoders` 只能证明“编译支持”，不能证明本机真能编码，
        因此这里再用 nvidia-smi 确认硬件，并用实际编码探针验证可用性。
        """
        if getattr(self, '_gpu_info_done', False):
            return self._gpu_info_text
        text = None
        try:
            creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            res = subprocess.run(
                ['nvidia-smi', '--query-gpu=name,driver_version,memory.total',
                 '--format=csv,noheader'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=10, creationflags=creation)
            if res.returncode == 0 and res.stdout.strip():
                text = res.stdout.strip().splitlines()[0].strip()
        except Exception:
            pass
        self._gpu_info_done = True
        self._gpu_info_text = text
        return text

    def _list_hw_encoders(self) -> List[str]:
        """ffmpeg 编译支持的显卡编码器（仅列表比对，速度快，用于 UI 即时提示）"""
        cached = getattr(self, '_hw_encoder_list', None)
        if cached is not None:
            return cached
        listed: List[str] = []
        ff = self._get_ffmpeg()
        if ff:
            try:
                creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                res = subprocess.run([ff, '-hide_banner', '-encoders'],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace',
                                     timeout=30, creationflags=creation)
                blob = res.stdout or ''
                listed = [key for key, codec in self.ENCODER_CODECS.items()
                          if re.search(rf'(?<![\w.]){codec}(?![\w.])', blob)]
            except Exception:
                pass
        self._hw_encoder_list = listed
        return listed

    def _probe_encoder(self, codec: str) -> Tuple[bool, str]:
        """用极小的一次编码验证编码器在本机是否真能工作，返回 (可用, 失败原因)"""
        probes = getattr(self, '_encoder_probes', None)
        if probes is None:
            probes = self._encoder_probes = {}
        if codec in probes:
            return probes[codec]
        probed: Tuple[bool, str] = (False, "未找到 ffmpeg")
        ff = self._get_ffmpeg()
        if ff:
            try:
                creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                res = subprocess.run(
                    [ff, '-hide_banner', '-loglevel', 'error',
                     '-f', 'lavfi', '-i', 'color=c=black:s=1280x720:d=0.04',
                     '-c:v', codec, '-f', 'null', '-'],
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=60, creationflags=creation)
                if res.returncode == 0:
                    probed = (True, "")
                else:
                    lines = (res.stderr or '').strip().splitlines()
                    probed = (False, lines[0][:160] if lines else f"退出码 {res.returncode}")
            except Exception as e:
                probed = (False, str(e)[:160])
        probes[codec] = probed
        return probed

    def _has_cuda_filters(self) -> bool:
        """ffmpeg 是否支持显存内缩放/填充（scale_cuda + pad_cuda）"""
        cached = getattr(self, '_cuda_filters', None)
        if cached is not None:
            return cached
        result = False
        ff = self._get_ffmpeg()
        if ff:
            try:
                creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                res = subprocess.run([ff, '-hide_banner', '-filters'],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace',
                                     timeout=30, creationflags=creation)
                blob = res.stdout or ''
                result = ('scale_cuda' in blob) and ('pad_cuda' in blob)
            except Exception:
                pass
        self._cuda_filters = result
        return result

    def _detect_hw_encoders(self) -> List[str]:
        """检测本机【真正可用】的显卡 H.265 编码器：编译支持 + 探针通过"""
        cached = getattr(self, '_hw_encoders', None)
        if cached is not None:
            return cached
        found: List[str] = []
        for key in ('nvenc', 'amf', 'qsv'):
            if key not in self._list_hw_encoders():
                continue
            codec = self.ENCODER_CODECS[key]
            ok, msg = self._probe_encoder(codec)
            if ok:
                found.append(key)
                continue
            self._log(f"{self.ENCODER_OPTIONS[key]}：ffmpeg 已编译支持，但本机不可用 — {msg}", "info")
            # 驱动版本不足时给出可直接操作的提示
            m = re.search(r'driver.*?([\d]+\.[\d]+)\s*or newer', msg, re.I)
            if m:
                self._log(f"  → 请将 NVIDIA 驱动升级到 {m.group(1)} 或更高版本后再试", "warning")
        self._hw_encoders = found
        return found

    def _resolve_encoder(self) -> str:
        """确定本次实际使用的编码器 key；所选显卡编码器不可用时回退 CPU"""
        choice = self._encoder_choice_key()
        if choice == 'cpu':
            self._log("编码器选定：CPU 软编码（libx265，不使用显卡）", "info")
            return 'cpu'
        avail = self._detect_hw_encoders()
        if choice != 'auto':
            if choice in avail:
                self._log(f"编码器选定：{self.ENCODER_OPTIONS[choice]}（显卡硬件加速）", "success")
                return choice
            self._log(f"所选 {self.ENCODER_OPTIONS[choice]} 在当前本机不可用，"
                      f"本次回退 CPU 软编码", "warning")
            return 'cpu'
        if avail:
            enc = avail[0]
            self._log(f"编码器选定：{self.ENCODER_OPTIONS.get(enc, enc)}"
                      f"（{'显卡硬件加速' if enc != 'cpu' else 'CPU 软编码'}）",
                      "success" if enc != 'cpu' else "warning")
            return enc
        self._log("未检测到可用显卡编码器，使用 CPU 软编码（libx265）", "warning")
        return 'cpu'

    def _video_encoder_args(self, enc: str, bitrate: int) -> List[str]:
        """按编码器生成视频编码参数（GPU 硬编优先）"""
        bitrate = max(100, int(bitrate))
        peak = int(bitrate * 1.5)
        if enc == 'nvenc':
            return ['-c:v', 'hevc_nvenc', '-preset', 'p4', '-rc', 'vbr',
                    '-b:v', f'{bitrate}k', '-maxrate', f'{peak}k',
                    '-bufsize', f'{peak * 2}k', '-tag:v', 'hvc1']
        if enc == 'amf':
            return ['-c:v', 'hevc_amf', '-quality', 'balanced', '-rc', 'cbr',
                    '-b:v', f'{bitrate}k', '-tag:v', 'hvc1']
        if enc == 'qsv':
            return ['-c:v', 'hevc_qsv', '-preset', 'medium',
                    '-b:v', f'{bitrate}k', '-maxrate', f'{peak}k', '-tag:v', 'hvc1']
        return ['-c:v', 'libx265', '-preset', 'medium',
                '-b:v', f'{bitrate}k', '-tag:v', 'hvc1',
                '-x265-params', 'log-level=error']

    def _resolve_target_size(self, task: TranscodeTask) -> Tuple[int, int]:
        """计算本次实际输出的目标尺寸（含“竖屏自动适配”）。

        规则：
        - 基准分辨率（如 1280x720）的短边即“720P”基准值 P；长:短 比例为基准比例。
        - 自动适配开启且源为竖屏：目标框跟随源方向，短边仍为 P，长边 = P × 基准比例
          （如 1280x720 → 720x1280），按“源自身比例”取竖屏 720P，故 1080x1920 /
          720x1280 这类 9:16 源可直接填满、无黑边。
        - “竖屏目标”设为固定值（720x1280 / 720x1080）时，竖屏源改用该固定值。
        - 横屏源或关闭适配：沿用基准框（按高度 P 缩放）。
        - 横宽比设置（非“保持原比例”）优先：先把原视频按比例缩放（限定目标框比例），
          再按上面的分辨率规则确定像素尺寸；宽高不足处由后续 pad 补黑边。
        """
        PORTRAIT_AUTO = "自动(按源比例)"
        res = task.resolution
        if res in ("保持原分辨率", "") and task.src_w > 0 and task.src_h > 0:
            base_w, base_h = task.src_w, task.src_h
        else:
            rl = (res or '').lower().strip()
            # 固定长边/短边：另一边按原片比例自动匹配，方向随原片横竖自动取宽或高
            # （已含竖屏自动适配，直接返回，无需再走竖屏/比例适配逻辑）
            if ':' in rl and rl.split(':', 1)[0] in ('long', 'short') \
                    and task.src_w > 0 and task.src_h > 0:
                edge, val_s = rl.split(':', 1)
                try:
                    N = int(val_s)
                except ValueError:
                    N = 0
                if N > 0:
                    long_src = max(task.src_w, task.src_h)
                    short_src = min(task.src_w, task.src_h)
                    f = N / long_src if edge == 'long' else N / short_src
                    W = int(round(task.src_w * f))
                    H = int(round(task.src_h * f))
                    W -= W % 2
                    H -= H % 2
                    return max(2, W), max(2, H)
                # 无合法数值：回退默认基准框
                base_w, base_h = 1280, 720
            else:
                parts = rl.split('x')
                auto_w = len(parts) > 0 and parts[0].strip() == 'auto'
                auto_h = len(parts) > 1 and parts[1].strip() == 'auto'
                # 仅指定一边（另一边“自动”）：按原视频宽高比自动计算另一边，
                # 直接返回结果（此时方向天然与原片一致，无需再走竖屏/比例适配逻辑）
                if (auto_w or auto_h) and task.src_w > 0 and task.src_h > 0:
                    src_ratio = task.src_w / task.src_h      # 宽/高
                    if auto_w and not auto_h:
                        try:
                            H = int(parts[1])
                        except ValueError:
                            H = 720
                        H -= H % 2
                        W = int(round(H * src_ratio))
                        tw, th = W - (W % 2), H
                        return max(2, tw), max(2, th)
                    if auto_h and not auto_w:
                        try:
                            W = int(parts[0])
                        except ValueError:
                            W = 1280
                        W -= W % 2
                        H = int(round(W / src_ratio))
                        th = H - (H % 2)
                        return max(2, W), max(2, th)
                # 其余（标准分辨率或全自定义）：按原逻辑解析基准框
            try:
                base_w, base_h = [int(x) for x in parts]
            except (ValueError, AttributeError):
                base_w, base_h = 1280, 720
        if base_w <= 0 or base_h <= 0:
            base_w, base_h = 1280, 720
        P = min(base_w, base_h)                              # 720P 基准值
        base_ratio = max(base_w, base_h) / min(base_w, base_h)

        # 目标方向：自动适配时跟随源；否则跟随基准框方向
        auto = bool(getattr(task, 'auto_orient', False))
        if auto and task.src_w > 0 and task.src_h > 0:
            target_portrait = task.src_h > task.src_w
        else:
            target_portrait = base_h > base_w

        # 默认（自动）竖屏/横屏目标框：P 为短边，长边按“基准比例”
        if target_portrait:
            tw, th = P, int(round(P * base_ratio))
        else:
            tw, th = int(round(P * base_ratio)), P

        # 竖屏：固定值优先；“自动”则按“源视频自身比例”取竖屏 720P（避免无谓黑边）
        pr = (getattr(task, 'portrait_res', '') or '').strip()
        if target_portrait and pr and pr not in (PORTRAIT_AUTO,):
            try:
                pw, ph = [int(x) for x in pr.lower().split('x')]
                if pw > 0 and ph > 0:
                    tw, th = pw, ph
            except (ValueError, AttributeError):
                pass
        elif target_portrait and pr == PORTRAIT_AUTO and task.src_w > 0 and task.src_h > 0:
            src_ratio = max(task.src_w, task.src_h) / min(task.src_w, task.src_h)
            tw, th = P, int(round(P * src_ratio))

        # 横宽比：先把原视频按比例缩放（限定目标框比例），再按分辨率规则定尺寸
        aspect = task.aspect
        if aspect and aspect != "保持原比例":
            try:
                aw, ah = [float(x) for x in aspect.split(':')]
                ratio = aw / ah
                if tw / th > ratio:
                    tw = int(th * ratio)
                else:
                    th = int(tw / ratio)
            except (ValueError, ZeroDivisionError):
                pass
        tw -= tw % 2
        th -= th % 2
        return max(2, tw), max(2, th)

    def _log_applied_resolution(self, task: TranscodeTask):
        """在日志里写明本次实际输出分辨率（竖屏自动适配时会与设定值不同）。"""
        applied = getattr(task, 'applied_resolution', '') or task.resolution
        name = Path(task.src).name if task.src else ""
        if getattr(task, 'auto_orient', False) and task.src_w > 0 and task.src_h > 0:
            orient = "竖屏" if task.src_h > task.src_w else "横屏"
            if orient == "竖屏" and applied != task.resolution:
                self._log(f"[分辨率] {name}: 检测到竖屏源 {task.src_w}x{task.src_h}，"
                          f"目标分辨率已自动调整为 {applied}"
                          f"（设定 {task.resolution}，按原比例缩放后不足部分补黑边）", "info")
            else:
                self._log(f"[分辨率] {name}: {orient}源 {task.src_w}x{task.src_h}"
                          f" → 输出 {applied}（按原比例缩放，不足部分补黑边）", "info")
        else:
            self._log(f"[分辨率] {name}: 输出 {applied}"
                      f"（按原比例缩放，不足部分补黑边）", "info")

    def _build_vf(self, task: TranscodeTask, gpu: bool = False) -> Optional[str]:
        """根据分辨率/横宽比设置构建滤镜，保持比例时用黑边填充

        gpu=True 时生成 CUDA 滤镜链（scale_cuda / pad_cuda），在显存内完成处理。
        """
        res = task.resolution
        keep_original = (res in ("保持原分辨率", ""))
        tw, th = self._resolve_target_size(task)
        task.applied_resolution = f"{tw}x{th}"     # 回填实际输出尺寸，供日志/界面显示

        # 只有“保持原分辨率”（且无需比例校正）才可以省略滤镜。
        # 一旦用户显式指定了目标分辨率，就必须始终加 scale/pad：
        # 探测到的 src_w/src_h 只是源视频“开头”的尺寸，部分视频中途会改变分辨率
        # （如开头 1280x720、后面 856x480），此时若因“目标尺寸==探测尺寸”而跳过滤镜，
        # 裁剪后的片段会按它自身的尺寸原样输出，导致成片分辨率与设置不符。
        if (keep_original and task.src_w > 0
                and (tw, th) == (task.src_w, task.src_h)
                and (not task.aspect or task.aspect == "保持原比例")):
            return None
        if gpu:
            # 显存内缩放 + 黑边填充：全程不回系统内存（不可再加 hwdownload，本机会报
            # "Input frame is not the in the configured hwframe context"）
            return (f"scale_cuda={tw}:{th}:force_original_aspect_ratio=decrease,"
                    f"pad_cuda={tw}:{th}:(ow-iw)/2:(oh-ih)/2")
        # force_original_aspect_ratio=decrease：按原比例缩放到刚好装进目标框；
        # pad 居中补黑边填满目标框（如 4:3 源 → 1280x720 时左右各补一条黑边）
        return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black")

    # ---- 字幕 ----
    def _find_subtitle(self, video_path: str) -> Optional[str]:
        """查找视频同目录下同名的 .srt 字幕（忽略大小写）"""
        p = Path(video_path)
        stem = p.stem.lower()
        try:
            for f in p.parent.iterdir():
                if f.is_file() and f.suffix.lower() == '.srt' and f.stem.lower() == stem:
                    return str(f)
        except OSError:
            pass
        return None

    @staticmethod
    def _srt_seconds(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, '0')) / 1000.0

    @staticmethod
    def _fmt_srt_time(t: float) -> str:
        t = max(0.0, t)
        total = int(round(t * 1000))
        hh, rem = divmod(total, 3_600_000)
        mm, rest = divmod(rem, 60_000)
        ss, ms = divmod(rest, 1000)
        return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

    def _make_shifted_srt(self, src_srt: Path, offset: float, limit: float,
                          dest: Path) -> Optional[str]:
        """把字幕时间轴按剪辑起点平移，生成临时 srt（只保留落在本片段内的字幕）"""
        try:
            raw = src_srt.read_text(encoding='utf-8-sig', errors='replace')
        except Exception:
            try:
                raw = src_srt.read_text(encoding='gbk', errors='replace')
            except Exception as e:
                self._log(f"读取字幕失败 {src_srt.name}: {e}", "warning")
                return None

        kept: List[str] = []
        for block in re.split(r'\n\s*\n', raw.strip()):
            m = self.SRT_TIME_RE.search(block)
            if not m:
                continue
            t1 = self._srt_seconds(*m.group(1, 2, 3, 4))
            t2 = self._srt_seconds(*m.group(5, 6, 7, 8))
            n1, n2 = t1 - offset, t2 - offset
            if n2 <= 0.0 or n1 >= limit:      # 完全落在本片段之外
                continue
            new_ts = (f"{self._fmt_srt_time(max(0.0, n1))} --> "
                      f"{self._fmt_srt_time(min(n2, limit))}")
            kept.append(block[:m.start()] + new_ts + block[m.end():])

        if not kept:
            return None

        lines: List[str] = []
        for i, block in enumerate(kept, 1):
            body = [ln for ln in block.strip().splitlines()]
            if body and body[0].strip().isdigit():   # 去掉原序号，重新编号
                body = body[1:]
            lines.append(str(i))
            lines.extend(body)
            lines.append('')
        try:
            dest.write_text('\n'.join(lines), encoding='utf-8')
            return str(dest)
        except Exception as e:
            self._log(f"写入临时字幕失败: {e}", "warning")
            return None

    @staticmethod
    def _sub_codec_for(out_path) -> str:
        """按输出容器选择字幕编码：mp4/mov/m4v 用 mov_text，其余（mkv 等）用 subrip"""
        ext = Path(out_path).suffix.lower().lstrip('.')
        return 'mov_text' if ext in ('mp4', 'mov', 'm4v') else 'subrip'

    def _soft_attach_cmd(self, task: TranscodeTask, tmp_out: Path) -> List[str]:
        """软字幕·快速附加：拷贝原视频/音频流并挂上字幕轨道，不重新编码（无损、秒级完成）"""
        ext = Path(tmp_out).suffix.lower().lstrip('.')
        # mp4/mov/m4v 用 mov_text；mkv 等用 subrip（SubRip 文本字幕）
        is_mp4 = ext in ('mp4', 'mov', 'm4v')
        cmd = [self.ffmpeg_path, '-y', '-nostdin',
               '-i', task.src, '-i', task.subtitle,
               '-map', '0:v:0', '-map', '0:a:0?', '-map', '1:0',
               '-c:v', 'copy', '-c:a', 'copy',
               '-c:s', 'mov_text' if is_mp4 else 'subrip',
               '-metadata:s:s:0', 'language=chi',
               '-disposition:s:0', 'default']
        # movflags 是 mp4/mov 封装器的私有选项，mkv 带上会报 “Option movflags not found”
        if is_mp4:
            cmd += ['-movflags', '+faststart']
        cmd += ['-progress', 'pipe:1', '-nostats', str(tmp_out)]
        return cmd

    def _copy_trim_cmd(self, task: TranscodeTask, tmp_out: Path,
                       start: float, dur: float, tmp_srt: Optional[Path],
                       coarse: Optional[float] = None) -> List[str]:
        """流拷贝剪辑：仅按起止时间裁剪，视频/音频流原样拷贝（-c copy），
        不改分辨率 / 码率 / 编码（无损）。可选一并挂上字幕（同样走拷贝流，
        仅新增字幕轨道，不重编码视频）。

        注意：流拷贝只能落在关键帧上，因此本命令的裁剪精度为“关键帧级”；
        起点之前的部分会截到最近的上一个关键帧。coarse 传入由实解 I 帧
        探测到的真实关键帧位置（_keyframe_aligned_seek），避免依赖可能
        不准确的容器索引 —— AVI 索引把非关键帧标成关键帧时，按索引 seek
        会从 GOP 中间拷贝，成片开头约 1 秒花屏。
        若需要起点精确到帧，_worker 会先尝试 _smart_cut（精准无损剪辑），
        仅在本方法作为其失败回退或起点恰在关键帧上时被直接使用。
        """
        ext = Path(tmp_out).suffix.lower().lstrip('.')
        is_mp4 = ext in ('mp4', 'mov', 'm4v')
        ss = coarse if (coarse is not None and coarse < start - 1e-6) else start
        cmd = [self.ffmpeg_path, '-y', '-nostdin', '-fflags', '+genpts',
               '-ss', f"{ss:.6f}", '-i', task.src]
        # 字幕：仅在确实选择了字幕时一并封装（字幕时间轴已由 _worker 按起点平移）
        sub = bool(getattr(task, 'subtitle', '')) and getattr(task, 'subtitle_mode', 'none') != 'none'
        if sub:
            srt = str(tmp_srt) if tmp_srt and Path(tmp_srt).exists() else task.subtitle
            cmd += ['-i', srt,
                    '-map', '0:v:0', '-map', '0:a:0?', '-map', '1:0',
                    '-c', 'copy',
                    '-c:s', 'mov_text' if is_mp4 else 'subrip',
                    '-metadata:s:s:0', 'language=chi',
                    '-disposition:s:0', 'default']
        else:
            cmd += ['-map', '0', '-c', 'copy']
        cmd += ['-t', f"{dur:.6f}", '-avoid_negative_ts', 'make_zero']
        # movflags 仅 mp4/mov 支持；mkv 带上会报 “Option movflags not found”
        if is_mp4:
            cmd += ['-movflags', '+faststart']
        cmd += ['-progress', 'pipe:1', '-nostats', str(tmp_out)]
        return cmd

    # ---- 精准无损剪辑（智能剪切） ----
    # 背景：流拷贝(-c copy)只能从关键帧开始，起点会向前回退到最近的关键帧，
    # 导致成片比设定起点提前数秒。“智能剪切”仅把“起点→下一个关键帧”的几秒
    # 重编码（视觉无损质量），其余整段无损拷贝后无损拼接 —— 起点精确到帧（0.1s 级）。
    _SMART_CUT_ENCODERS = {'h264': ('libx264', '16'), 'hevc': ('libx265', '18')}

    def _probe_keyframes_around(self, src: str, start: float) -> List[float]:
        """探测起点附近的关键帧时间点（秒）。

        只解码 I 帧（-skip_frame nokey），读取窗口为 [start-60s, start+120s]，
        覆盖常见 1~10s 的 GOP；探测失败返回空列表（调用方回退普通流拷贝）。
        """
        fp = self._get_ffprobe()
        if not fp:
            return []
        back = min(60.0, max(0.0, start))
        a = max(0.0, start - back)
        read_len = max(1.0, (start + 120.0) - a)
        creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            res = subprocess.run(
                [fp, '-v', 'error', '-read_intervals', f'{a:.3f}%+{read_len:.3f}',
                 '-select_streams', 'v:0', '-skip_frame', 'nokey',
                 '-show_entries', 'frame=pts_time', '-of', 'csv=p=0', src],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace',
                timeout=120, creationflags=creation)
            kfs = []
            for ln in (res.stdout or '').splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    t = float(ln.split(',')[0])
                except ValueError:
                    continue
                if t >= 0:
                    kfs.append(t)
            return kfs
        except Exception:
            return []

    def _keyframe_aligned_seek(self, src: str, start: float
                               ) -> Tuple[float, Optional[float]]:
        """计算“真实关键帧对齐”的两级 seek 参数 (输入侧 coarse, 输出侧 fine)。

        背景：AVI 等容器的索引可能不准确（把非关键帧也标成关键帧），
        单级输入 seek（-ss 在 -i 前）会落在 GOP 中间，解码缺少参考帧 →
        成片开头约一个 GOP（~1 秒）出现绿块花屏。先用 ffprobe 实解 I 帧
        （-skip_frame nokey，不依赖容器索引）找到 ≤ start 的真实关键帧作为
        输入侧 seek 点，再用输出侧 seek 补齐剩余偏移（帧精确），从根源上
        消除开头花屏。

        返回 (coarse, fine)：fine 为 None 表示无需输出侧补齐
        （起点已在真实关键帧上，或探测失败 → 回退单级 seek 的旧行为）。
        """
        if start <= 0.04:
            return start, None
        kfs = self._probe_keyframes_around(src, start)
        kf_prev = None
        for t in kfs:
            if t <= start + 1e-6:
                kf_prev = t
            else:
                break
        if kf_prev is None or start - kf_prev <= 0.04:
            return start, None
        return kf_prev, start - kf_prev

    def _probe_has_audio(self, src: str) -> bool:
        """探测源是否含音频流（探测失败按无音频处理，最终混流时音频可选）"""
        fp = self._get_ffprobe()
        if not fp:
            return False
        creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            res = subprocess.run(
                [fp, '-v', 'error', '-select_streams', 'a:0',
                 '-show_entries', 'stream=index', '-of', 'csv=p=0', src],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace',
                timeout=30, creationflags=creation)
            return bool((res.stdout or '').strip())
        except Exception:
            return False

    # 卡死看门狗：ffmpeg 在个别视频（尤其显卡编码/解码）上可能无输出地挂起，
    # 导致转码线程永久阻塞、进度卡在 ~99% 不动。这里监控“最近一次有输出”的时间，
    # 超过阈值即终止进程，使任务以失败结束（进而被跳过），不再卡死整个批次。
    STALL_TIMEOUT = 180  # 秒：超过该时长无任何输出即判定挂起并终止

    def _start_stall_watchdog(self, proc, flag, name="ffmpeg"):
        """启动守护线程：proc 存活且超过 STALL_TIMEOUT 无任何输出时终止它。

        ``flag`` 为可变字典 ``{'last': 时间戳, 'stalled': False}``，主循环每读到
        一行就刷新 ``flag['last']``；看门狗据此判断是否卡死。
        """
        timeout = self.STALL_TIMEOUT

        def _watch():
            while proc.poll() is None:
                if getattr(self, 'stop_requested', False):
                    return
                if time.monotonic() - flag['last'] > timeout:
                    flag['stalled'] = True
                    try:
                        nm = Path(name).name if name else "ffmpeg"
                    except Exception:
                        nm = str(name)
                    self._log(f"[看门狗] {nm}: 超过 {int(timeout)} 秒无任何输出，"
                              f"疑似编码器/解码器挂起，已终止本任务（将作为失败跳过）",
                              "error")
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    return
                time.sleep(1.0)

        threading.Thread(target=_watch, daemon=True).start()

    def _run_ffmpeg_stage(self, cmd: List[str], progress_base: float,
                          progress_span: float, total_dur: float,
                          stage_name: str, task: TranscodeTask) -> bool:
        """执行智能剪切的单个 ffmpeg 阶段：支持停止与暂停（进程挂起）及进度上报。

        进程同样登记到 self._procs，与普通转码共用暂停（NtSuspendProcess）机制。
        返回 True 表示该阶段成功完成（退出码 0）。
        """
        creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace',
                                    creationflags=creation)
            with self._lock:
                self._procs.append(proc)
            flag = {'last': time.monotonic(), 'stalled': False}
            self._start_stall_watchdog(proc, flag, name=stage_name)
            for line in proc.stdout:
                flag['last'] = time.monotonic()
                if self.stop_requested:
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    return False
                line = line.strip()
                if (progress_span > 0 and total_dur > 0 and
                        (line.startswith('out_time_ms=') or line.startswith('out_time_us='))):
                    try:
                        out_time = float(line.split('=', 1)[1]) / 1_000_000
                        pct = progress_base + min(0.99, max(0.0, out_time / total_dur)) * progress_span
                        self._set_task_status(task, f"{stage_name} {int(pct)}%", progress=pct)
                    except (ValueError, ZeroDivisionError):
                        pass
            rc = proc.wait()
            if flag['stalled']:
                return False
            return rc == 0
        except Exception:
            return False
        finally:
            if proc is not None:
                with self._lock:
                    if proc in self._procs:
                        self._procs.remove(proc)

    def _smart_cut(self, task: TranscodeTask, tmp_out: Path, start: float,
                   dur: float, tmp_srt: Optional[Path]) -> bool:
        """精准无损剪辑（智能剪切）：解决流拷贝起点提前到关键帧的问题。

        原理：起点落在 GOP 中间时，仅重编码“起点 → 下一个关键帧”这短短几秒
        （crf 16/18，视觉无损），其后到结束整段无损拷贝，再无损拼接并混流
        （音频自起点整段拷贝，可选挂字幕）。起点因此精确到帧，其余画质与
        普通流拷贝完全一致。

        任一环节失败 / 用户停止 / 源编码不支持（非 h264/hevc）时返回 False，
        自动回退到普通关键帧流拷贝 —— 保证该功能绝不引入新的失败。
        成功时与本模块普通转码成功路径一致：改名成片、清理临时字幕、
        调用 _finish_one 并返回 True（调用方直接结束本任务）。
        """
        if start <= 0.04:                       # 从 0 开始本就精确，无需智能剪切
            return False
        if getattr(self, 'stop_requested', False):
            return False
        if not self._get_ffprobe():
            return False
        src = task.src
        end = start + dur

        # 1) 源视频编码/像素格式：仅 h264/hevc 可与重编码头段无缝拼接
        creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            res = subprocess.run(
                [self._get_ffprobe(), '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=codec_name,pix_fmt', '-of', 'csv=p=0', src],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                timeout=30, creationflags=creation)
            parts = (res.stdout or '').strip().split(',')
            vcodec = parts[0].strip().lower() if parts else ''
            pix_fmt = parts[1].strip() if len(parts) > 1 else ''
        except Exception:
            return False
        if vcodec not in self._SMART_CUT_ENCODERS:
            self._log(f"[精准剪辑] {Path(src).name}: 源编码 {vcodec or '未知'} "
                      f"不支持无缝拼接，按普通关键帧流拷贝处理", "info")
            return False

        # 2) 关键帧位置：kf_prev ≤ start < kf_next
        kfs = self._probe_keyframes_around(src, start)
        kf_prev = None
        kf_next = None
        for t in kfs:
            if t <= start + 1e-6:
                kf_prev = t
            elif t > start + 0.04:
                kf_next = t
                break
        if kf_next is None:
            return False
        if kf_prev is not None and start - kf_prev <= 0.04:
            return False            # 起点就在关键帧上（±1 帧），普通流拷贝已精确

        # 3) 分段：结束点在同一 GOP 内则整段重编码，否则“头段重编码 + 主体拷贝”
        if end <= kf_next + 0.01:
            head_dur = dur
            body_dur = 0.0
        else:
            head_dur = kf_next - start
            body_dur = end - kf_next
        if head_dur <= 0.05:
            return False

        out = Path(getattr(task, 'out_path', '') or tmp_out)
        base = tmp_out.parent
        head = base / f".{tmp_out.stem}.smart_head.mkv"
        body = base / f".{tmp_out.stem}.smart_body.mkv"
        audio = base / f".{tmp_out.stem}.smart_audio.mka"
        listing = base / f".{tmp_out.stem}.smart_list.txt"
        stage_files = (head, body, audio, listing)
        try:
            enc, crf = self._SMART_CUT_ENCODERS[vcodec]
            name = Path(src).name
            self._log(f"[精准剪辑] {name}: 起点 {start:.3f}s 位于关键帧 "
                      f"{(kf_prev if kf_prev is not None else 0):.3f}s~{kf_next:.3f}s 之间，"
                      f"仅重编码前 {head_dur:.2f}s（视觉无损），其余无损拷贝 → 起点精确到帧",
                      "info")

            # 4) 头段：重编码 [start, kf_next)。输入级 seek 对齐 kf_prev（实解
            #    I 帧探测到的真实关键帧，不依赖可能不准的容器索引），输出侧补齐
            #    剩余偏移 = 帧精确起点，且解码从真实关键帧开始（避免开头花屏）
            head_coarse = kf_prev if kf_prev is not None else 0.0
            head_cmd = [self.ffmpeg_path, '-y', '-nostdin', '-fflags', '+genpts',
                        '-ss', f'{head_coarse:.6f}', '-i', src]
            if start - head_coarse > 0.04:
                head_cmd += ['-ss', f'{start - head_coarse:.6f}']
            head_cmd += ['-t', f'{head_dur:.6f}',
                         '-map', '0:v:0', '-an', '-sn', '-dn',
                         '-c:v', enc, '-preset', 'veryfast', '-crf', crf]
            if pix_fmt:
                head_cmd += ['-pix_fmt', pix_fmt]
            head_cmd += [str(head)]
            if not self._run_ffmpeg_stage(head_cmd, 0.0, 12.0, head_dur,
                                          "精准剪辑-头段", task):
                return False

            # 5) 音频：两级 seek（输入侧粗 seek 到 start-2s + 输出侧精确丢弃剩余），
            #    无损拷贝且起点精确 —— 实测 MP4/AAC 直接用输入级 seek 会比设定起点
            #    多包含约 1 秒内容（编辑列表落点问题），破坏音画同步
            has_audio = self._probe_has_audio(src)
            if has_audio:
                coarse = max(0.0, start - 2.0)
                fine = start - coarse
                audio_cmd = [self.ffmpeg_path, '-y', '-nostdin',
                             '-ss', f'{coarse:.6f}', '-i', src,
                             '-ss', f'{fine:.6f}', '-t', f'{dur:.6f}',
                             '-map', '0:a:0', '-vn', '-sn', '-c:a', 'copy',
                             str(audio)]
                if not self._run_ffmpeg_stage(audio_cmd, 12.0, 3.0, dur,
                                              "精准剪辑-音频", task):
                    return False

            # 6) 主体：[kf_next, end) 无损拷贝（大头，耗时与原流拷贝相当）
            if body_dur > 0.01:
                body_cmd = [self.ffmpeg_path, '-y', '-nostdin',
                            '-ss', f'{kf_next:.6f}', '-i', src,
                            '-t', f'{body_dur:.6f}',
                            '-map', '0:v:0', '-an', '-sn', '-dn',
                            '-c:v', 'copy', str(body)]
                if not self._run_ffmpeg_stage(body_cmd, 15.0, 80.0, body_dur,
                                              "精准剪辑-主体", task):
                    return False

            # 7) 拼接清单 + 混流（含可选字幕）→ tmp_out（与普通转码相同的临时文件）
            try:
                with open(listing, 'w', encoding='utf-8') as f:
                    f.write(f"file '{head.as_posix()}'\n")
                    if body_dur > 0.01:
                        f.write(f"file '{body.as_posix()}'\n")
            except Exception:
                return False

            ext = tmp_out.suffix.lower().lstrip('.')
            is_mp4 = ext in ('mp4', 'mov', 'm4v')
            attach_srt = bool(tmp_srt) and Path(tmp_srt).exists()
            mux = [self.ffmpeg_path, '-y', '-nostdin',
                   '-f', 'concat', '-safe', '0', '-i', str(listing)]
            if has_audio:
                mux += ['-i', str(audio)]
            if attach_srt:
                mux += ['-i', str(tmp_srt)]
            mux += ['-map', '0:v:0']
            nxt = 1
            if has_audio:
                mux += ['-map', f'{nxt}:a:0?']
                nxt += 1
            if attach_srt:
                mux += ['-map', f'{nxt}:s:0']
            mux += ['-c:v', 'copy']
            if has_audio:
                mux += ['-c:a', 'copy']
            if attach_srt:
                mux += ['-c:s', 'mov_text' if is_mp4 else 'subrip',
                        '-metadata:s:s:0', 'language=chi',
                        '-disposition:s:0', 'default']
            mux += ['-avoid_negative_ts', 'make_zero']
            if is_mp4:
                mux += ['-movflags', '+faststart']
            mux += ['-progress', 'pipe:1', '-nostats', str(tmp_out)]
            if not self._run_ffmpeg_stage(mux, 95.0, 5.0, dur,
                                          "精准剪辑-封装", task):
                return False

            if self.stop_requested:
                return False
            if not (tmp_out.exists() and tmp_out.stat().st_size > 0):
                return False

            # 8) 成功：与普通转码一致 —— 临时成片改名 + 清理临时字幕 + 标记成功
            os.replace(str(tmp_out), str(out))
            try:
                if tmp_srt and Path(tmp_srt).exists():
                    Path(tmp_srt).unlink()
            except Exception:
                pass
            self._finish_one(task, "success", f"{out.name}")
            return True
        finally:
            for p in stage_files:
                try:
                    if p and Path(p).exists():
                        Path(p).unlink()
                except Exception:
                    pass

    # ---- 多视频合并（拼接） ----
    def _build_merged_srt(self, entries: List[Tuple[str, float, float, float]],
                          dest: Path) -> Optional[str]:
        """把各段字幕合并为一个按成片时间轴全局对齐的 srt。

        entries: [(srt路径, 本段剪辑起点, 本段时长, 本段在成片中的起始时间)]
        每条字幕先按“本段剪辑起点”平移到段内局部时间，再加上该段的累计偏移，
        使前一段的字幕不会串到后一段上（拼接时整体对齐）。
        """
        try:
            raw_entries = []
            for srt_path, seg_start, seg_dur, seg_base in entries:
                try:
                    raw = Path(srt_path).read_text(encoding='utf-8-sig', errors='replace')
                except Exception:
                    try:
                        raw = Path(srt_path).read_text(encoding='gbk', errors='replace')
                    except Exception:
                        continue
                for block in re.split(r'\n\s*\n', raw.strip()):
                    m = self.SRT_TIME_RE.search(block)
                    if not m:
                        continue
                    t1 = self._srt_seconds(*m.group(1, 2, 3, 4))
                    t2 = self._srt_seconds(*m.group(5, 6, 7, 8))
                    n1, n2 = t1 - seg_start, t2 - seg_start   # 段内局部时间
                    if n2 <= 0.0 or n1 >= seg_dur:            # 完全落在本段之外
                        continue
                    g1 = seg_base + max(0.0, n1)              # 成片全局时间
                    g2 = seg_base + min(n2, seg_dur)
                    # 只取时间戳行之后的文字（丢掉原序号与旧时间戳）
                    body = [ln for ln in block[m.end():].splitlines() if ln.strip()]
                    if not body:
                        continue
                    raw_entries.append((g1, g2, body))

            if not raw_entries:
                return None
            raw_entries.sort(key=lambda x: (x[0], x[1]))
            # 相邻段边界处若时间重叠，轻微收敛，避免同一时刻两条字幕叠加
            lines: List[str] = []
            prev_end = -1.0
            for idx, (g1, g2, body) in enumerate(raw_entries, 1):
                if g1 < prev_end:
                    g1 = prev_end
                if g2 <= g1:
                    continue
                prev_end = g2
                lines.append(str(idx))
                lines.append(f"{self._fmt_srt_time(g1)} --> {self._fmt_srt_time(g2)}")
                lines.extend(body)
                lines.append('')
            if not lines:
                return None
            dest.write_text('\n'.join(lines), encoding='utf-8')
            return str(dest)
        except Exception as e:
            self._log(f"[合并] 合并字幕失败: {e}", "warning")
            return None

    def _probe_pix_fmt(self, path: str) -> str:
        """用 ffprobe 读视频流像素格式（如 yuv420p / yuv420p10le）"""
        fp = self._get_ffprobe()
        if not fp:
            return ""
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            res = subprocess.run(
                [fp, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=pix_fmt', '-of', 'csv=p=0', path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=30, creationflags=flags)
            for ln in (res.stdout or '').splitlines():
                ln = ln.strip()
                if ln:
                    return ln
        except Exception:
            pass
        return ""

    def _run_merge(self, task: TranscodeTask):
        """多视频合并：按统一参数逐段编码为同规格片段，再无损拼接为一个成片。

        要点：
        - 所有片段共用同一套输出参数（分辨率/比例/码率/编码器/容器/字幕方式）；
        - 每段沿用自己设定的起止时间（前一段剪完后接上后一段，以此类推）；
        - 像素格式/帧率/音频规格/字幕轨数量全部统一——concat 的 `-c copy`
          要求各段流参数完全一致，否则拼接产物时间轴会错乱；
        - 每段自带 srt 时按“本段起点”平移到本段局部时间轴后随段封装，
          拼接后整体字幕自然对齐（无 srt 的段补一条空字幕轨以保持流数一致）。
        """
        segs = [s for s in (getattr(task, 'segments', None) or []) if s.get('src')]
        if len(segs) < 2:
            self._finish_one(task, "fail", "合并任务片段不足 2 个")
            return
        try:
            out = self._resolve_output(task)
            out.parent.mkdir(parents=True, exist_ok=True)
            task.out_path = str(out)
        except Exception as e:
            self._finish_one(task, "fail", f"输出路径无效: {e}")
            return
        tmp_out = out.with_name(f".{out.stem}.transcoding{out.suffix}")
        base = tmp_out.parent
        ext = tmp_out.suffix.lower().lstrip('.')
        is_mp4 = ext in ('mp4', 'mov', 'm4v')

        # 1) 统一规格：像素格式 / 帧率 / 是否有音频
        pix_fmts = [self._probe_pix_fmt(s['src']) for s in segs]
        pix = pix_fmts[0] if pix_fmts and all(p == pix_fmts[0] for p in pix_fmts) else ''
        if not pix:
            pix = 'yuv420p'
            self._log("[合并] 未能统一探测像素格式，按 yuv420p 处理", "warning")
        elif not all(p == pix_fmts[0] for p in pix_fmts):
            self._log(f"[合并] 各段像素格式不一致（{', '.join(sorted(set(pix_fmts)))}），"
                      f"统一为 {pix}", "warning")
        fps = next((float(s.get('fps') or 0) for s in segs
                    if float(s.get('fps') or 0) > 0), 25.0)
        has_audio = all(self._probe_has_audio(s['src']) for s in segs)
        if not has_audio:
            self._log("[合并] 有片段不含音频流，成片将不带音频（concat 要求各段流一致）",
                      "warning")
        want_sub = getattr(task, 'subtitle_mode', 'none') != 'none'

        # 统一目标尺寸：拼接要求各段完全一致，故按首段的屏幕方向一次性解析出
        # 目标尺寸（含竖屏自动适配），后续各段都强制使用它
        uni_w, uni_h = self._resolve_target_size(TranscodeTask(
            src=str(segs[0]['src']),
            src_w=int(segs[0].get('src_w') or 0), src_h=int(segs[0].get('src_h') or 0),
            resolution=task.resolution, aspect=task.aspect,
            auto_orient=bool(getattr(task, 'auto_orient', False)),
            portrait_res=getattr(task, 'portrait_res', '') or '自动(按源比例)'))
        if f"{uni_w}x{uni_h}" != task.resolution:
            self._log(f"[合并] 统一输出分辨率 {uni_w}x{uni_h}"
                      f"（按首段屏幕方向适配；拼接要求各段尺寸一致）", "info")

        seg_files: List[Path] = []
        stage_files: List[Path] = []
        # 供最后合并字幕用：(srt路径, 本段起点, 本段时长, 本段在成片中的起始时间)
        sub_entries: List[Tuple[str, float, float, float]] = []
        total_dur = sum(max(0.05, float(s.get('end') or 0) - float(s.get('start') or 0))
                        for s in segs)
        done_dur = 0.0
        seg_base = 0.0          # 当前段在成片中的起始时间（累计时长）
        enc = getattr(self, '_active_encoder', 'cpu')

        try:
            for i, s in enumerate(segs, 1):
                if self.stop_requested:
                    self._finish_one(task, "skipped", "")
                    return
                seg_src = Path(s['src'])
                start = max(0.0, float(s.get('start') or 0))
                end = float(s.get('end') or 0)
                if end <= start:
                    self._log(f"[合并] 第 {i} 段 {seg_src.name} 起止时间无效，已跳过", "warning")
                    continue
                dur = end - start
                seg_out = base / f".{tmp_out.stem}.seg{i}{tmp_out.suffix}"
                stage_files.append(seg_out)
                seg_files.append(seg_out)

                # 字幕不在段内封装：统一在最后按累计偏移合并（保证各段流结构一致，
                # 且整体字幕时间轴精确对齐）
                if want_sub and s.get('srt') and Path(s['srt']).exists():
                    sub_entries.append((str(s['srt']), start, dur, seg_base))

                # 片段级任务：复用同一套 vf / 编码参数
                # 拼接要求各段输出尺寸完全一致，故统一用首段决定的目标尺寸，
                # 避免“一段横屏一段竖屏”时自动适配出不同尺寸导致拼接失败
                seg_task = TranscodeTask(
                    src=str(seg_src), start=start, end=end,
                    src_w=int(s.get('src_w') or 0), src_h=int(s.get('src_h') or 0),
                    fps=float(s.get('fps') or 0),
                    resolution=f"{uni_w}x{uni_h}", aspect=task.aspect,
                    auto_orient=False,
                    bitrate=task.bitrate, keep_bitrate=False,
                    save_mode='custom', save_dir=str(base),
                    out_ext=ext, subtitle_mode='none',
                    subtitle='', keep_quality=False)
                seg_task.out_path = str(seg_out)
                vf = self._build_vf(seg_task)
                cmd = [self.ffmpeg_path, '-y', '-nostdin',
                       '-ss', f'{start:.6f}', '-i', str(seg_src),
                       '-t', f'{dur:.6f}']
                if vf:
                    cmd += ['-vf', vf]
                cmd += ['-r', f'{fps:g}', '-pix_fmt', pix]
                cmd += self._video_encoder_args(enc, task.bitrate)
                cmd += ['-map', '0:v:0']
                if has_audio:
                    cmd += ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', '128k',
                            '-ar', '48000', '-ac', '2']
                if is_mp4:
                    cmd += ['-movflags', '+faststart']
                cmd += ['-progress', 'pipe:1', '-nostats', str(seg_out)]

                span = (dur / total_dur) * 92.0 if total_dur > 0 else 0.0
                self._log(f"[合并] 第 {i}/{len(segs)} 段: {seg_src.name} "
                          f"[{format_clock(start)}~{format_clock(end)}] "
                          f"→ 按统一参数编码", "info")
                if not self._run_ffmpeg_stage(cmd, done_dur, span, dur,
                                              f"合并-第{i}段", task):
                    self._cleanup(stage_files)
                    self._finish_one(task, "fail",
                                     f"第 {i} 段编码失败: {seg_src.name}")
                    return
                done_dur += span
                seg_base += dur

            if not seg_files:
                self._cleanup(stage_files)
                self._finish_one(task, "fail", "没有可拼接的有效片段")
                return

            # 2) 合并各段字幕为一个全局对齐的 srt（按累计偏移平移）
            merged_srt = None
            if want_sub and sub_entries:
                cand = base / f".{tmp_out.stem}.merged.srt"
                made = self._build_merged_srt(sub_entries, cand)
                if made:
                    merged_srt = Path(made)
                    stage_files.append(merged_srt)
                    self._log(f"[合并] 已合并 {len(sub_entries)} 段字幕并整体对齐时间轴",
                              "info")
                else:
                    self._log("[合并] 各段范围内均无有效字幕，成片不带字幕", "info")

            # 3) 无损拼接（各段规格一致，直接拷贝流；字幕作为第 2 路输入混流）
            listing = base / f".{tmp_out.stem}.merge_list.txt"
            stage_files.append(listing)
            try:
                with open(listing, 'w', encoding='utf-8') as f:
                    for p in seg_files:
                        f.write(f"file '{p.as_posix()}'\n")
            except Exception as e:
                self._cleanup(stage_files)
                self._finish_one(task, "fail", f"写入拼接清单失败: {e}")
                return
            mux = [self.ffmpeg_path, '-y', '-nostdin',
                   '-f', 'concat', '-safe', '0', '-i', str(listing)]
            if merged_srt is not None:
                mux += ['-i', str(merged_srt)]
            mux += ['-map', '0:v:0']
            if has_audio:
                mux += ['-map', '0:a:0?']
            if merged_srt is not None:
                mux += ['-map', '1:0', '-c:s', self._sub_codec_for(tmp_out),
                        '-metadata:s:s:0', 'language=chi']
            mux += ['-c:v', 'copy']
            if has_audio:
                mux += ['-c:a', 'copy']
            # 注意：不能加 -avoid_negative_ts make_zero —— 它会把字幕流整体
            # 推移到视频首帧 pts（实测偏移约 0.06~0.1s），导致字幕对不齐。
            if is_mp4:
                mux += ['-movflags', '+faststart']
            mux += ['-progress', 'pipe:1', '-nostats', str(tmp_out)]
            self._log(f"[合并] 开始拼接 {len(seg_files)} 段 → {out.name}", "info")
            if not self._run_ffmpeg_stage(mux, 92.0, 8.0, total_dur, "合并-拼接", task):
                self._cleanup(stage_files)
                self._finish_one(task, "fail", "拼接失败")
                return
            if self.stop_requested:
                self._cleanup(stage_files)
                self._finish_one(task, "skipped", "")
                return
            if not (tmp_out.exists() and tmp_out.stat().st_size > 0):
                self._cleanup(stage_files)
                self._finish_one(task, "fail", "拼接输出文件为空")
                return

            os.replace(str(tmp_out), str(out))
            self._cleanup(stage_files)
            self._log(f"[合并] 完成：{len(seg_files)} 段已拼接为 {out.name}"
                      f"（总时长 {format_clock(total_dur)}）", "success")
            self._finish_one(task, "success", f"{out.name}")
        except Exception as e:
            self._cleanup(stage_files)
            self._finish_one(task, "fail", f"合并失败: {e}")

    @staticmethod
    def _cleanup(paths: List[Path]):
        """清理临时文件（忽略不存在/失败）"""
        for p in paths or []:
            try:
                if p and Path(p).exists():
                    Path(p).unlink()
            except Exception:
                pass

    def _resolve_output(self, task: TranscodeTask) -> Path:
        src = Path(task.src)
        # 覆盖模式：输出就是原视频文件本身（先写临时文件，成功后替换它）
        if getattr(task, 'overwrite_src', False):
            return src
        # 快速附加字幕时沿用源容器后缀，保证视频/音频流可直接拷贝
        ext = (getattr(task, 'out_ext', '') or 'mp4').lstrip('.') or 'mp4'
        # 合并任务：成片名取“首段文件名_合并”，避免与任一片段同名而被误删/覆盖
        if getattr(task, 'merge', False):
            stem = (getattr(task, 'merge_name', '') or '').strip()
            if not stem:
                segs = getattr(task, 'segments', None) or []
                first = Path(segs[0].get('src') or task.src) if segs else src
                stem = f"{first.stem}_合并"
            if task.save_mode == 'source':
                segs = getattr(task, 'segments', None) or []
                base_dir = Path(segs[0].get('src') or task.src).parent if segs else src.parent
            else:
                base_dir = Path(task.save_dir)
            base_dir.mkdir(parents=True, exist_ok=True)
            cand = base_dir / f"{stem}.{ext}"
            if cand.exists():
                n = 1
                while True:
                    alt = base_dir / f"{stem}~{n}.{ext}"
                    if not alt.exists():
                        return alt
                    n += 1
            return cand
        if task.save_mode == 'source':
            # 保存到原文件夹，增加数字后缀 ~1、~2 ...
            n = 1
            while True:
                cand = src.parent / f"{src.stem}~{n}.{ext}"
                if not cand.exists():
                    return cand
                n += 1
        d = Path(task.save_dir)
        cand = d / f"{src.stem}.{ext}"
        # 指定文件夹里“输出路径 == 源文件本身”（同目录同名同后缀）时绝不能直接压上去：
        # 否则转码成功后“删除原文件”删掉的就是刚生成的成片（数据丢失）。
        # 改为先输出到 name~N.ext，待删掉原文件、原字幕后，再把成片改名回原名（安全覆盖）。
        if os.path.normcase(os.path.abspath(str(cand))) == os.path.normcase(os.path.abspath(str(src))):
            n = 1
            while True:
                cand = d / f"{src.stem}~{n}.{ext}"
                if not cand.exists():
                    task.swap_back_to = str(src)   # 成片最终要改回的名字
                    self._log(f"[输出重名] {src.name}: 指定文件夹与源文件相同，"
                              f"先输出为 {cand.name}，删除原文件后改名回 {src.name}（避免误删成片）", "warning")
                    return cand
                n += 1
        return cand

    def _start_transcode(self):
        if self.is_running:
            return
        if not self.task_queue:
            messagebox.showwarning("提示", "任务队列为空，请先加入任务")
            return
        ffmpeg = self._get_ffmpeg()
        if not ffmpeg:
            messagebox.showerror("错误", "未找到 ffmpeg，请安装并在设置中指定 ffmpeg 路径")
            self._log("错误: 未找到 ffmpeg，无法转码", "error")
            return
        self.ffmpeg_path = ffmpeg

        # 显卡信息与编码器可用性（nvidia-smi 确认硬件 + 实际编码探针）
        gpu = self._nvidia_gpu_info()
        if gpu:
            self._log(f"显卡: {gpu}", "info")

        # 确定编码器：优先显卡硬件编码，降低 CPU 占用
        self._active_encoder = self._resolve_encoder()
        enc_name = self.ENCODER_OPTIONS.get(self._active_encoder, self._active_encoder)
        # 是否走“全显存”流水线：CUDA 解码 + 显存内缩放/填充 + 显卡编码
        self._use_gpu_pipeline = (self._active_encoder == 'nvenc'
                                  and self._has_cuda_filters())

        conc = max(1, min(5, self._get_int(self.conc_var, 2)))
        # 消费级显卡同时进行的硬件编码会话有上限，超限会导致 session 创建失败
        if self._active_encoder != 'cpu' and conc > 3:
            conc = 3
            self._log("硬件编码：同时处理数已限制为 3（显卡并发编码会话上限）", "warning")
        self.is_running = True
        self.stop_requested = False
        self._done = self._success = self._fail = self._cancel = 0
        self._total = len(self.task_queue)
        self._total_dur = sum(
            (sum(max(float(s.get('end') or 0) - float(s.get('start') or 0), 0)
                 for s in (getattr(t, 'segments', None) or []))
             if getattr(t, 'merge', False) else max(t.end - t.start, 0))
            for t in self.task_queue)
        self._start_ts = time.time()
        self._failures = []

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        self._log("=" * 60, "highlight")
        self._log(f"开始转码: 共 {self._total} 个任务, 并发 {conc}, "
                  f"编码器 {enc_name} ({'显卡硬编' if self._active_encoder != 'cpu' else 'CPU软编'})",
                  "highlight")
        if self._use_gpu_pipeline:
            self._log("流水线: 全显存（CUDA解码 → scale_cuda/pad_cuda → NVENC编码），CPU 占用最低",
                      "success")
        elif self._active_encoder != 'cpu':
            self._log("流水线: CPU 解码/缩放 + 显卡编码（未启用显存内处理）", "info")
        else:
            self._log("流水线: 全部由 CPU 处理（libx265 软编码）", "warning")
        self._log(f"ffmpeg: {ffmpeg}", "info")
        self._log("=" * 60, "highlight")

        for t in self.task_queue:
            self._set_task_status(t, "待处理 0%", progress=0.0)
        self._update_overall_progress()

        self.executor = ThreadPoolExecutor(max_workers=conc)
        for t in self.task_queue:
            self.executor.submit(self._worker, t)
        self._update_status_bar()

    def _stop_transcode(self):
        self.stop_requested = True
        with self._lock:
            for p in self._procs:
                try:
                    p.terminate()
                except Exception:
                    pass
        self._log("正在停止所有转码任务...", "warning")
        self._update_status_bar("正在停止: ")

    def _set_task_status(self, task: TranscodeTask, status_text: str,
                         progress: Optional[float] = None):
        """更新队列中单个任务的状态文字与进度。

        tk.Listbox 的 itemconfig 不支持 text 选项，必须“删除+插入”才能改文字。
        """
        task.status = status_text
        if progress is not None:
            task.progress = float(progress)
        idx = task.q_index
        text = f"{task.display}  [{status_text}]"

        def _upd():
            try:
                if 0 <= idx < self.queue_listbox.size():
                    self.queue_listbox.delete(idx)
                    self.queue_listbox.insert(idx, text)
            except Exception:
                pass
        try:
            self.frame.after(0, _upd)
        except Exception:
            pass
        self._update_overall_progress()

    def _update_overall_progress(self):
        """按所有任务的平均进度更新总体进度条"""
        n = len(self.task_queue)
        overall = (sum(getattr(t, 'progress', 0.0) for t in self.task_queue) / n) if n else 0.0

        def _upd():
            try:
                self.overall_progress_var.set(overall)
                self.overall_pct_var.set(f"{overall:.0f}%")
            except Exception:
                pass
        try:
            self.frame.after(0, _upd)
        except Exception:
            pass

    def _worker(self, task: TranscodeTask):
        """工作线程：调用 ffmpeg 完成单个转码任务"""
        if self.stop_requested:
            self._finish_one(task, "skipped", "")
            return

        # 合并任务：多段按统一参数编码后无损拼接为一个成片
        if getattr(task, 'merge', False) and (getattr(task, 'segments', None) or []):
            self._run_merge(task)
            return

        src = task.src
        # 起点已在加入队列时对齐到帧格；时长至少保证输出一帧，避免 0.01 秒精度下切出空片段
        start = task.start
        min_dur = (1.0 / task.fps) if task.fps > 1e-6 else 0.01
        dur = max(task.end - start, min_dur)
        try:
            out = self._resolve_output(task)
            out.parent.mkdir(parents=True, exist_ok=True)
            task.out_path = str(out)   # 记录实际输出路径，供完成后删除原文件时防误删
        except Exception as e:
            self._finish_one(task, "fail", f"输出路径无效: {e}")
            return

        # 转码过程先写到目标文件夹里的临时文件，成功后再改名，避免留下半截文件
        tmp_out = out.with_name(f".{out.stem}.transcoding{out.suffix}")
        # 字幕：按剪辑起点平移时间轴后生成临时 srt（与目标文件同目录）
        tmp_srt = out.with_name(f".{out.stem}.transcoding.srt") if task.subtitle else None
        if tmp_srt:
            made = self._make_shifted_srt(Path(task.subtitle), start, dur, tmp_srt)
            tmp_srt = Path(made) if made else None   # 片段内无字幕则不封装

        def cleanup_temp(remove_subtitle: bool = True):
            """清理临时文件。

            重试时必须保留临时字幕：降级后的命令仍以它作为第二路输入，
            一旦在这里把它删掉，后续每次重试都会因“打不开该字幕”而必然失败。
            """
            targets = (tmp_out, tmp_srt) if remove_subtitle else (tmp_out,)
            for p in targets:
                try:
                    if p and Path(p).exists():
                        Path(p).unlink()
                except Exception:
                    pass

        vf_cpu = self._build_vf(task)
        active_enc = getattr(self, '_active_encoder', 'cpu')
        # 记录本次实际输出分辨率（竖屏自动适配后可能与设定值不同），便于核对
        self._log_applied_resolution(task)
        bitrate = max(100, int(task.bitrate))
        # 不修改码率：读取源视频自身码率（kbps）作为目标码率，
        # 使不同码率的视频无需逐个调整；读取失败时回退为任务设定的码率
        if getattr(task, 'keep_bitrate', False):
            src_br = self._probe_stream_bitrate_kbps(src)
            if src_br >= 100:
                bitrate = int(src_br)
                self._log(f"[不修改码率] {Path(src).name}: 沿用原片码率 {bitrate} kbps",
                          "info")
            else:
                self._log(f"[不修改码率] {Path(src).name}: 未能读取原片码率，"
                          f"回退为 {bitrate} kbps", "warning")

        # 输入 seek 对齐真实关键帧 + -fflags +genpts：AVI 等容器索引可能不准确
        # （把非关键帧也标成关键帧），单级输入 seek 会落在 GOP 中间，解码缺少
        # 参考帧 → 成片开头约一个 GOP（~1 秒）绿块花屏。先用 ffprobe 实解 I 帧
        # 找到 ≤start 的真实关键帧作输入侧 seek 点，再输出侧补齐剩余偏移。
        coarse, fine = self._keyframe_aligned_seek(src, start)
        if fine is not None:
            self._log(f"[关键帧对齐] {Path(src).name}: 容器索引不可靠，输入 seek "
                      f"对齐到真实关键帧 {coarse:.3f}s，输出侧补齐 {fine:.3f}s"
                      f"（避免开头约 1 秒花屏）", "info")

        def make_cmd(extra_flags: List[str], vf: Optional[str], enc: str) -> List[str]:
            cmd = ([self.ffmpeg_path, '-y', '-nostdin', '-fflags', '+genpts']
                   + extra_flags)
            # 时间用微秒级精度：3 位毫秒舍入在高帧率(如59.94fps)下可能多跳一帧
            cmd += ['-ss', f"{coarse:.6f}", '-i', src]
            if tmp_srt:
                cmd += ['-i', str(tmp_srt)]      # 第二个输入：已平移的字幕
            if fine is not None:                 # 输出侧补齐剩余偏移（帧精确）
                cmd += ['-ss', f"{fine:.6f}"]
            cmd += ['-t', f"{dur:.6f}"]
            if vf:
                cmd += ['-vf', vf]
            cmd += self._video_encoder_args(enc, bitrate)
            cmd += ['-c:a', 'aac', '-b:a', '128k']
            if tmp_srt:
                # 视频/音频取第 1 个输入（音频可选），字幕取第 2 个输入
                # 字幕编码按输出容器选择：matroska 不支持 mov_text（会直接报
                # "Subtitle codec mov_text is not supported"），必须用 subrip
                cmd += ['-map', '0:v:0', '-map', '0:a:0?', '-map', '1:0',
                        '-c:s', self._sub_codec_for(tmp_out),
                        '-metadata:s:s:0', 'language=chi']
            cmd += ['-movflags', '+faststart',
                    '-progress', 'pipe:1', '-nostats', str(tmp_out)]
            return cmd

        # 逐级降级：
        #  0) 全显存：CUDA 解码 + 显存内缩放/填充 + 显卡编码（CPU 占用最低）
        #  1) CPU 缩放 + 显卡编码
        #  2) 全 CPU 软编码（兜底）
        attempts: List[List[str]] = []
        fallback_notes: List[str] = []
        # 流拷贝剪辑：仅按起止时间裁剪，视频/音频流原样拷贝（-c copy），
        # 不改分辨率/码率/编码。字幕若设置则一并拷贝封装（只新增字幕轨道）。
        copy_mode = bool(getattr(task, 'keep_quality', False))
        fast_attach = (getattr(task, 'subtitle_mode', 'none') == 'soft_fast'
                       and task.subtitle and not copy_mode)
        if copy_mode:
            self._log(f"[流拷贝剪辑] {Path(src).name}: 保留原分辨率/码率，"
                      f"仅按关键帧裁剪（-c copy）", "info")
            if task.subtitle and getattr(task, 'subtitle_mode', 'none') != 'none':
                self._log(f"[流拷贝剪辑] {Path(src).name}: 字幕将随流拷贝一并封装"
                          f"（视频不重编码）", "info")
            # 精准无损剪辑（智能剪切）：起点落在关键帧组中间时，仅重编码头段几秒，
            # 使起点精确到帧（0.1s 级）；失败/停止自动回退到下面的普通关键帧流拷贝
            if self._smart_cut(task, tmp_out, start, dur, tmp_srt):
                return
            if start > 0.04:
                # 流拷贝只能落在关键帧上：明确告知起点可能提前，避免静默偏差
                self._log(f"[剪辑精度] {Path(src).name}: 精准剪切不可用，"
                          f"本次按关键帧流拷贝处理，起点可能提前到上一个关键帧", "warning")
            attempts.append(self._copy_trim_cmd(task, tmp_out, start, dur, tmp_srt,
                                                coarse=coarse))
        elif fast_attach:
            self._log(f"[快速附加字幕] {Path(src).name}: 拷贝原视频/音频流，不重新编码", "info")
            attempts.append(self._soft_attach_cmd(task, tmp_out))
        else:
            # >8bit 源：NVDEC 解码出的 CUDA surface 无法被 scale_cuda / pad_cuda 处理，
            # 实测必报 "Task finished with error code: -40 (Function not implemented)"，
            # 直接跳过该尝试，避免每个这样的视频都白跑一次并刷一堆报错。
            #
            # 剪辑精度：只要设置了剪辑起点（start>0），就不走“全显存”流水线。
            # 硬件解码（-hwaccel cuda / NVDEC）的输入侧 seek 只能落在关键帧上，
            # 成片起点会退到上一个 GOP（实测比设定提前约 3 秒）；而软件解码的
            # accurate_seek（ffmpeg 默认开启）是帧精确的。结束侧本就是输出级
            # -t（按时间戳丢弃），起点准了之后结束也随之精确 —— 故整体满足
            # “涉及起止的剪辑必须精确到 0.1 秒”。代价仅是该条视频改用 CPU 解码，
            # 仍由显卡/CPU 编码，画质与后续步骤不受影响。
            if start > 0.04:
                self._log(f"[剪辑精度] {Path(src).name}: 已设剪辑起点 {start:.2f}s，"
                          f"改用 CPU 解码以保证起点精确到 0.1 秒"
                          f"（硬件解码只能落在关键帧上，会提前到上一个 GOP）", "info")
                use_gpu_pipeline = False
            elif active_enc == 'nvenc' and self._source_bit_depth(src) > 8:
                self._log(f"[跳过显存流水线] {Path(src).name}: 源为高位深视频，"
                          f"scale_cuda/pad_cuda 不支持，直接用 CPU 缩放 + 显卡编码", "info")
                use_gpu_pipeline = False
            else:
                use_gpu_pipeline = (active_enc == 'nvenc' and self._has_cuda_filters())
            if use_gpu_pipeline:
                attempts.append(make_cmd(
                    ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda'],
                    self._build_vf(task, gpu=True), active_enc))
                fallback_notes.append("显存内处理不可用，改为 CPU 缩放重试")
            attempts.append(make_cmd([], vf_cpu, active_enc))
            if active_enc != 'cpu':
                fallback_notes.append("显卡编码不可用，改用 CPU 软编码重试")
                attempts.append(make_cmd([], vf_cpu, 'cpu'))

        creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = None
        last_err = ""
        try:
            for attempt_idx, cur_cmd in enumerate(attempts):
                if self.stop_requested:
                    self._finish_one(task, "skipped", "")
                    return
                err_lines: List[str] = []
                # 输出尾部（排除进度行）：失败且没有 error 关键字时用于还原真实原因
                tail: List[str] = []
                progress_keys = ('frame=', 'fps=', 'bitrate=', 'total_size=', 'out_time',
                                 'progress=', 'speed=', 'dropped_frames=', 'dup_frames=')
                proc = subprocess.Popen(cur_cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT,
                                        text=True, encoding='utf-8', errors='replace',
                                        creationflags=creation)
                with self._lock:
                    self._procs.append(proc)
                flag = {'last': time.monotonic(), 'stalled': False}
                self._start_stall_watchdog(proc, flag, name=Path(src).name)

                for line in proc.stdout:
                    flag['last'] = time.monotonic()
                    if self.stop_requested:
                        proc.terminate()
                        break
                    line = line.strip()
                    if line and not line.startswith(progress_keys):
                        tail.append(line)
                        if len(tail) > 40:
                            del tail[0]
                    if line.startswith('out_time_ms=') or line.startswith('out_time_us='):
                        try:
                            out_time = float(line.split('=', 1)[1]) / 1_000_000
                            pct = min(99, int(out_time / dur * 100))
                            self._set_task_status(task, f"转换中 {pct}%", progress=pct)
                        except (ValueError, ZeroDivisionError):
                            pass
                    elif ('error' in line.lower() or 'invalid' in line.lower()
                            or 'failed' in line.lower()):
                        err_lines.append(line)

                rc = proc.wait()
                with self._lock:
                    if proc in self._procs:
                        self._procs.remove(proc)

                if self.stop_requested:
                    cleanup_temp()
                    self._finish_one(task, "skipped", "")
                    return
                if flag['stalled']:
                    # 卡死看门狗已终止进程：作为失败结束，跳过本视频（不重试）
                    cleanup_temp()
                    self._log(f"[看门狗] {Path(src).name}: 转码过程卡死，已终止并跳过本视频",
                              "error")
                    self._finish_one(task, "fail",
                                     "转码过程卡死（疑似编码器/解码器挂起），已终止并跳过本视频")
                    return
                if rc == 0 and tmp_out.exists() and tmp_out.stat().st_size > 0:
                    rename_err = None
                    try:
                        os.replace(str(tmp_out), str(out))
                    except Exception as e:
                        rename_err = str(e)
                    cleanup_temp()      # 清理临时字幕（临时视频已改名）
                    if rename_err is None:
                        self._finish_one(task, "success", f"{out.name}")
                        return
                    last_err = f"临时文件改名为最终文件失败: {rename_err}"
                else:
                    if err_lines:
                        last_err = '; '.join(err_lines[-3:])
                    elif tail:
                        # 没有带 error 关键字的行时，退回输出尾部，避免只剩一句无信息量的“退出码”
                        last_err = '; '.join(tail[-3:])
                    else:
                        last_err = f"ffmpeg 退出码 {rc}"
                    cleanup_temp(remove_subtitle=False)   # 保留临时字幕供重试使用
                if attempt_idx + 1 < len(attempts):
                    self._log(f"[重试] {Path(src).name}: {fallback_notes[attempt_idx]} "
                              f"({last_err[:200]})", "warning")
                    continue
                self._finish_one(task, "fail", last_err[:600])
                return
        except FileNotFoundError:
            cleanup_temp()
            self._finish_one(task, "fail", "找不到 ffmpeg 可执行文件")
        except Exception as e:
            cleanup_temp()
            self._finish_one(task, "fail", str(e))
        finally:
            if proc is not None:
                with self._lock:
                    if proc in self._procs:
                        self._procs.remove(proc)

    def _finish_one(self, task: TranscodeTask, status: str, detail: str):
        with self._lock:
            if status == 'success':
                self._success += 1
            elif status == 'fail':
                self._fail += 1
                self._failures.append((task.src, detail))
            else:
                self._cancel += 1
            self._done += 1
            done = self._done

        if status == 'success':
            status_text, level = "完成 100%", "success"
            self._log(f"[转码完成] {Path(task.src).name} -> {detail}", level)
            # 转码成功且勾选时，删除原文件（失败/取消的文件保留）
            # 删除前三重校验，任何一条命中都跳过删除，避免把刚生成的成片当成“原文件”删掉：
            #   1) 覆盖模式（输出本来就写在源路径上）
            #   2) 输出路径与源文件是同一个文件（如“保存到指定文件夹”恰好就是源目录）
            #   3) 输出文件不在磁盘上（成片未确认落盘，绝不先删原文件）
            skip_del_src = getattr(task, 'overwrite_src', False)
            out_p = getattr(task, 'out_path', '')
            if out_p and not skip_del_src:
                if (os.path.normcase(os.path.abspath(out_p))
                        == os.path.normcase(os.path.abspath(task.src))):
                    skip_del_src = True
                elif not os.path.exists(out_p):
                    skip_del_src = True
                    self._log(f"[跳过删除原文件] {Path(task.src).name}: "
                              f"未找到输出文件，为防误删已保留原文件", "warning")
            src_deleted = False
            if self.del_src_var.get() and not skip_del_src:
                try:
                    os.remove(task.src)
                    src_deleted = True
                    self._log(f"[已删除原文件] {Path(task.src).name}", "info")
                except Exception as e:
                    self._log(f"[删除原文件失败] {Path(task.src).name}: {e}", "warning")
            # 删除字幕：勾选了“处理后删除字幕文件”，或覆盖模式（用新视频替换“原视频+字幕”）
            if task.subtitle and (self.del_srt_var.get()
                                  or getattr(task, 'overwrite_src', False)):
                try:
                    os.remove(task.subtitle)
                    self._log(f"[已删除字幕] {Path(task.subtitle).name}", "info")
                except Exception as e:
                    self._log(f"[删除字幕失败] {Path(task.subtitle).name}: {e}", "warning")
            # 同目录同名场景：删掉原视频与原字幕后，把成片（name~N.ext）改名回原名，实现安全覆盖。
            # 只有“删除原文件”确实发生（原文件已不存在）才改名，否则保留 name~N 文件，绝不覆盖用户想留的原片。
            swap = getattr(task, 'swap_back_to', '')
            if swap and getattr(self, 'del_src_var', None) and src_deleted and os.path.exists(out_p):
                try:
                    os.replace(str(out_p), swap)
                    self._log(f"[已改名] {Path(out_p).name} -> {Path(swap).name}", "info")
                except Exception as e:
                    self._log(f"[改名失败 保留 {Path(out_p).name}] {e}", "warning")
            if task.subtitle and (self.del_srt_var.get()
                                  or getattr(task, 'overwrite_src', False)):
                try:
                    os.remove(task.subtitle)
                    self._log(f"[已删除字幕] {Path(task.subtitle).name}", "info")
                except Exception as e:
                    self._log(f"[删除字幕失败] {Path(task.subtitle).name}: {e}", "warning")
        elif status == 'fail':
            status_text, level = f"失败: {detail}", "error"
            self._log(f"[转码失败] {Path(task.src).name}: {detail}", level)
        else:
            status_text, level = "已取消", "warning"
            self._log(f"[已取消] {Path(task.src).name}", level)
        self._set_task_status(task, status_text, progress=100.0)
        self._update_status_bar()

        if done >= self._total:
            try:
                self.frame.after(0, self._all_done)
            except Exception:
                pass

    def _all_done(self):
        """全部任务完成：统计 + 日志 + 完成后操作（不弹窗）"""
        self.is_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        # 所有任务处理完毕：进度条置满
        try:
            self.overall_progress_var.set(100.0)
            self.overall_pct_var.set("100%")
        except Exception:
            pass
        try:
            self.executor.shutdown(wait=False)
        except Exception:
            pass

        elapsed = time.time() - self._start_ts
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        self._log("=" * 60, "highlight")
        self._log(f"全部任务完成！视频个数: {self._total}, 处理总时长(片段): "
                  f"{self._total_dur:.1f} 秒, 耗时 {h:02d}:{m:02d}:{s:02d}", "highlight")
        self._log(f"成功 {self._success} 个, 失败 {self._fail} 个, 取消 {self._cancel} 个",
                  "success" if self._fail == 0 else "warning")
        if self._failures:
            self._log("以下文件转换失败:", "error")
            for path, err in self._failures:
                self._log(f"  ✗ {path} — {err}", "error")
        self._log("=" * 60, "highlight")

        self.status_bar_var.set(
            f"全部任务完成 | 共 {self._total} 个视频 | 成功 {self._success} | "
            f"失败 {self._fail} | 取消 {self._cancel} | 耗时 {h:02d}:{m:02d}:{s:02d}")
        try:
            self.frame.bell()
        except Exception:
            pass

        # 完成后操作
        action = self.after_var.get()
        if action == "睡眠":
            self._log("执行系统睡眠...", "warning")
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        elif action == "关机":
            self._log("系统将在 30 秒后关机（可执行 shutdown /a 取消）", "warning")
            subprocess.Popen(['shutdown', '/s', '/t', '30'])

    # ------------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------------
    def cleanup(self):
        self.stop_requested = True
        with self._lock:
            for p in self._procs:
                try:
                    p.terminate()
                except Exception:
                    pass
        try:
            self.player.release()
        except Exception:
            pass


class VideoGridTab:
    """视频工具模块容器：宫格缩略图 + 视频剪辑转码（子选项卡）"""

    def __init__(self, parent, controller=None, config_manager: Optional[ConfigManager] = None):
        self.parent = parent
        self.frame = ttk.Frame(parent)

        notebook = ttk.Notebook(self.frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.grid_panel = GridThumbnailPanel(notebook, controller=controller,
                                             config_manager=config_manager)
        notebook.add(self.grid_panel.frame, text="宫格缩略图")

        self.transcode_panel = VideoTranscodePanel(notebook, controller=controller,
                                                   config_manager=config_manager)
        notebook.add(self.transcode_panel.frame, text="视频剪辑转码")

        # 默认显示“视频剪辑转码”界面
        notebook.select(self.transcode_panel.frame)

    def get_frame(self) -> ttk.Frame:
        """获取主框架"""
        return self.frame

    def cleanup(self):
        """清理子面板资源"""
        for panel in (self.grid_panel, self.transcode_panel):
            cleanup = getattr(panel, 'cleanup', None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:
                    pass


# ===== 独立运行入口 =====
if __name__ == "__main__":
    root = tk.Tk()
    root.title("视频宫格缩略图生成器 - 独立版")
    root.geometry("1000x600")
    root.minsize(800, 500)
    
    # 创建 Tab 实例（独立模式）
    tab = VideoGridTab(root, controller=None)
    tab.get_frame().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    root.mainloop()