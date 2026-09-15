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
import threading
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Set, Callable, Any
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont
import numpy as np

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
        self._load_config()
    
    def _get_default_config_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            "app_config.json"
        )
    
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
    
    def get(self, section: str, key: str, default=None):
        if section not in self._config:
            self._config[section] = {}
        return self._config[section].get(key, default)
    
    def set(self, section: str, key: str, value: Any, auto_save: bool = True):
        if section not in self._config:
            self._config[section] = {}
        self._config[section][key] = value
        if auto_save:
            self.save_config()
    
    def get_section(self, section: str, default: Optional[Dict] = None) -> Dict:
        return self._config.get(section, default or {})
    
    def set_section(self, section: str, data: Dict, auto_save: bool = True):
        self._config[section] = data
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


class VideoGridTab:
    """视频宫格缩略图生成器 Tab 页"""
    
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