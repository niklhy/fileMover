#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网页图片抓取选项卡模块 - 无额外依赖版本
支持独立运行和通过main.py嵌入运行
功能：
1. 通过剪贴板或手动输入接收链接
2. 后台读取网页内容
3. 抓取主图并显示
4. 抓取磁力链下载链接
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import json
import re
import threading
import queue
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional, Dict, List, Set, Callable, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from PIL import Image, ImageTk
import io
import time

# 常量定义
CONFIG_FILE_NAME = "app_config.json"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


# ========== 统一配置管理器 ==========
class ConfigManager:
    """统一配置管理器 - 单例模式，管理所有Tab的配置"""
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
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE_NAME)

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
        old_value = self._config[section].get(key)
        self._config[section][key] = value
        if old_value != value:
            self._trigger_callbacks(section, key, value)
        if auto_save:
            self.save_config()

    def get_section(self, section: str, default: Optional[Dict] = None) -> Dict:
        return self._config.get(section, default or {})

    def set_section(self, section: str, data: Dict, auto_save: bool = True):
        self._config[section] = data
        if auto_save:
            self.save_config()

    def register_callback(self, section: str, callback: Callable[[str, Any], None]):
        if section not in self._callbacks:
            self._callbacks[section] = []
        self._callbacks[section].append(callback)

    def _trigger_callbacks(self, section: str, key: str, value: Any):
        for callback in self._callbacks.get(section, []):
            try:
                callback(key, value)
            except Exception as e:
                print(f"回调执行失败: {e}")


# ========== 配置数据类 ==========
@dataclass
class WebGrabConfig:
    """网页抓取配置"""
    user_agent: str = USER_AGENTS[0]
    timeout: int = 30
    proxy_mode: str = "direct"  # direct, system, custom
    proxy_url: str = ""
    browser_mode: str = "chrome"  # chrome, firefox, edge, safari
    connection_mode: str = "http"  # http, https
    max_images: int = 10
    auto_download: bool = False
    save_path: str = ""
    custom_headers: Dict[str, str] = field(default_factory=dict)
    cookies: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'WebGrabConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ========== 网页抓取工作类 ==========
class WebGrabWorker:
    """网页抓取后台工作类"""

    def __init__(self, config: WebGrabConfig,
                 log_callback: Optional[Callable] = None,
                 progress_callback: Optional[Callable] = None,
                 complete_callback: Optional[Callable] = None,
                 image_found_callback: Optional[Callable] = None,
                 magnet_found_callback: Optional[Callable] = None):
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.complete_callback = complete_callback
        self.image_found_callback = image_found_callback
        self.magnet_found_callback = magnet_found_callback
        self.is_running = True
        self.html_content = ""
        self.images: List[Dict] = []
        self.magnets: List[Dict] = []

    def stop(self):
        self.is_running = False

    def log(self, message: str):
        if self.log_callback and self.is_running:
            self.log_callback(message)

    def update_progress(self, progress: int, message: str = ""):
        if self.progress_callback and self.is_running:
            self.progress_callback(progress, message)

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            'User-Agent': self.config.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

        # 根据浏览器模式调整
        if self.config.browser_mode == "firefox":
            headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        elif self.config.browser_mode == "safari":
            headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'

        # 添加自定义请求头
        if self.config.custom_headers:
            headers.update(self.config.custom_headers)

        # 添加Cookie
        if self.config.cookies:
            headers['Cookie'] = self.config.cookies

        return headers

    def _build_opener(self):
        """构建URL打开器"""
        handlers = []

        # 代理设置
        if self.config.proxy_mode == "system":
            # 使用系统代理
            pass
        elif self.config.proxy_mode == "custom" and self.config.proxy_url:
            proxy = urllib.request.ProxyHandler({
                'http': self.config.proxy_url,
                'https': self.config.proxy_url,
            })
            handlers.append(proxy)

        return urllib.request.build_opener(*handlers)

    def fetch_page(self, url: str) -> Optional[str]:
        """获取网页内容"""
        try:
            self.log(f"正在请求: {url}")
            self.update_progress(10, "正在连接...")

            headers = self._build_headers()
            req = urllib.request.Request(url, headers=headers)
            opener = self._build_opener()

            with opener.open(req, timeout=self.config.timeout) as response:
                self.update_progress(30, "正在下载...")

                # 检查响应状态
                if response.status != 200:
                    self.log(f"请求失败，状态码: {response.status}")
                    return None

                # 读取内容
                content = response.read()

                # 处理压缩
                encoding = response.headers.get('Content-Encoding', '').lower()
                if encoding == 'gzip':
                    import gzip
                    content = gzip.decompress(content)
                elif encoding == 'deflate':
                    import zlib
                    content = zlib.decompress(content)

                # 检测编码
                content_type = response.headers.get('Content-Type', '')
                charset = 'utf-8'
                if 'charset=' in content_type:
                    charset = content_type.split('charset=')[-1].split(';')[0].strip()

                try:
                    html = content.decode(charset, errors='ignore')
                except:
                    html = content.decode('utf-8', errors='ignore')

                self.update_progress(50, "下载完成")
                self.log(f"网页下载成功，大小: {len(html)} 字符")
                return html

        except urllib.error.HTTPError as e:
            self.log(f"HTTP错误: {e.code} - {e.reason}")
            return None
        except urllib.error.URLError as e:
            self.log(f"URL错误: {e.reason}")
            return None
        except Exception as e:
            self.log(f"请求异常: {e}")
            return None

    def extract_images(self, html: str, base_url: str) -> List[Dict]:
        """提取网页中的图片"""
        images = []

        # 方法1: 提取og:image（主图）
        og_patterns = [
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\'>]+)["\']',
            r'<meta[^>]*content=["\']([^"\'>]+)["\'][^>]*property=["\']og:image["\']',
            r'<meta[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\'>]+)["\']',
        ]
        for pattern in og_patterns:
            matches = re.findall(pattern, html, re.I)
            for match in matches:
                img_url = urllib.parse.urljoin(base_url, match)
                if img_url not in [img['url'] for img in images]:
                    images.append({
                        'url': img_url,
                        'type': 'main',
                        'source': 'og:image'
                    })
                    self.log(f"发现主图: {img_url}")

        # 方法2: 提取所有img标签
        img_pattern = r'<img[^>]*src=["\']([^"\'>]+)["\'][^>]*>'
        img_tags = re.findall(img_pattern, html, re.I)

        for src in img_tags:
            # 跳过小图标、logo等
            if any(x in src.lower() for x in ['icon', 'logo', 'button', 'spacer', 'blank', '1x1']):
                continue

            img_url = urllib.parse.urljoin(base_url, src)

            # 跳过base64图片
            if img_url.startswith('data:'):
                continue

            # 跳过已存在的
            if img_url in [img['url'] for img in images]:
                continue

            # 尝试获取图片尺寸信息
            width, height = self._get_image_size_from_tag(html, src)

            img_type = 'content'
            if width and height:
                if width >= 300 and height >= 300:
                    img_type = 'large'
                elif width < 100 or height < 100:
                    continue  # 跳过太小的图

            images.append({
                'url': img_url,
                'type': img_type,
                'width': width,
                'height': height,
                'source': 'img_tag'
            })

            if len(images) >= self.config.max_images:
                break

        self.log(f"共发现 {len(images)} 张图片")
        return images

    def _get_image_size_from_tag(self, html: str, src: str) -> Tuple[Optional[int], Optional[int]]:
        """从img标签中提取尺寸"""
        try:
            # 查找包含此src的img标签
            pattern = r'<img[^>]*src=["\']' + re.escape(src) + r'["\'][^>]*>'
            match = re.search(pattern, html, re.I)
            if match:
                tag = match.group(0)
                w_match = re.search(r'width=["\']?(\d+)', tag, re.I)
                h_match = re.search(r'height=["\']?(\d+)', tag, re.I)
                width = int(w_match.group(1)) if w_match else None
                height = int(h_match.group(1)) if h_match else None
                return width, height
        except:
            pass
        return None, None

    def extract_magnets(self, html: str, base_url: str) -> List[Dict]:
        """提取磁力链接"""
        magnets = []

        # 磁力链接模式
        magnet_pattern = r'magnet:\?xt=urn:[a-zA-Z0-9]+:[a-zA-Z0-9]+[^"\'<>\s]*'

        # 查找所有磁力链接
        for match in re.finditer(magnet_pattern, html, re.I):
            magnet_url = match.group(0)
            # 清理URL
            magnet_url = magnet_url.replace('&amp;', '&').replace('\\\\', '')

            if magnet_url not in [m['url'] for m in magnets]:
                # 尝试提取显示名称
                display_name = self._extract_magnet_name(html, match.start(), match.end())

                magnets.append({
                    'url': magnet_url,
                    'name': display_name or '未命名',
                    'source': 'magnet'
                })
                self.log(f"发现磁力链: {display_name or magnet_url[:50]}...")

        # 也查找包含磁力链接的a标签
        a_pattern = r'<a[^>]*href=["\'](magnet:\?[^"\']+)["\'][^>]*>([^<]*)</a>'
        for match in re.finditer(a_pattern, html, re.I):
            magnet_url = match.group(1).replace('&amp;', '&')
            link_text = match.group(2).strip()

            if magnet_url not in [m['url'] for m in magnets]:
                magnets.append({
                    'url': magnet_url,
                    'name': link_text or '未命名',
                    'source': 'magnet_link'
                })

        self.log(f"共发现 {len(magnets)} 个磁力链接")
        return magnets

    def _extract_magnet_name(self, html: str, start: int, end: int) -> Optional[str]:
        """从磁力链接周围提取名称"""
        # 向前查找
        before = html[max(0, start - 200):start]
        # 向后查找
        after = html[end:end + 200]

        # 尝试在a标签中找到名称
        a_pattern = r'<a[^>]*>([^<]+)</a>[^<]*$'
        match = re.search(a_pattern, before, re.I)
        if match:
            name = match.group(1).strip()
            if name and len(name) < 100:
                return name

        # 尝试查找标题
        title_pattern = r'title=["\']([^"\']+)["\']'
        match = re.search(title_pattern, before[-100:], re.I)
        if match:
            return match.group(1).strip()

        return None

    def download_image(self, url: str) -> Optional[bytes]:
        """下载图片"""
        try:
            headers = self._build_headers()
            headers['Referer'] = url  # 添加Referer

            req = urllib.request.Request(url, headers=headers)
            opener = self._build_opener()

            with opener.open(req, timeout=self.config.timeout) as response:
                if response.status == 200:
                    return response.read()
        except Exception as e:
            self.log(f"图片下载失败: {e}")
        return None

    def process_url(self, url: str):
        """处理URL"""
        self.log("=" * 60)
        self.log(f"开始处理: {url}")
        self.log("=" * 60)

        # 获取网页
        html = self.fetch_page(url)
        if not html:
            self.log("获取网页失败")
            if self.complete_callback:
                self.complete_callback(False, "获取网页失败", [], [])
            return

        self.html_content = html

        # 提取图片
        self.update_progress(60, "正在分析图片...")
        self.images = self.extract_images(html, url)

        # 提取磁力链接
        self.update_progress(80, "正在分析磁力链接...")
        self.magnets = self.extract_magnets(html, url)

        # 回调通知
        if self.image_found_callback:
            for img in self.images:
                if self.is_running:
                    self.image_found_callback(img)

        if self.magnet_found_callback:
            for magnet in self.magnets:
                if self.is_running:
                    self.magnet_found_callback(magnet)

        self.update_progress(100, "处理完成")

        if self.complete_callback:
            self.complete_callback(True, "处理完成", self.images, self.magnets)


# ========== GUI选项卡类 ==========
class WebGrabTab:
    """网页抓取选项卡"""

    CONFIG_SECTION = "web_grab"  # 配置分区标识

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

        # 工作线程
        self.operation_queue = queue.Queue()
        self.worker = None
        self.is_running = False

        # 数据
        self.current_images: List[Dict] = []
        self.current_magnets: List[Dict] = []
        self.image_labels: List[ttk.Label] = []

        # 创建界面
        self._create_widgets()
        self._load_from_config_manager()
        self._bind_events()

    def _create_widgets(self):
        """创建界面"""
        self.frame = ttk.Frame(self.parent)

        # 主容器 - 左右分栏
        main_paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧面板
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)

        # 右侧面板
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=1)

        # ====== 左侧面板 ======
        # URL输入区域
        url_frame = ttk.LabelFrame(left_frame, text="网页地址")
        url_frame.pack(fill=tk.X, padx=5, pady=5)

        url_input_frame = ttk.Frame(url_frame)
        url_input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(url_input_frame, textvariable=self.url_var)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        ttk.Button(url_input_frame, text="抓取",
                  command=self._start_grab, width=8).pack(side=tk.RIGHT)

        # 剪贴板导入区域
        clipboard_frame = ttk.LabelFrame(left_frame, text="快速导入（点击按钮从剪贴板读取URL）")
        clipboard_frame.pack(fill=tk.X, padx=5, pady=5)

        clipboard_btn_frame = ttk.Frame(clipboard_frame)
        clipboard_btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(clipboard_btn_frame, text="从剪贴板导入URL",
                  command=self._import_from_clipboard, width=20).pack(side=tk.LEFT, padx=5)

        ttk.Button(clipboard_btn_frame, text="清空",
                  command=self._clear_url, width=8).pack(side=tk.LEFT, padx=5)

        # 使用说明
        help_frame = ttk.LabelFrame(left_frame, text="使用说明")
        help_frame.pack(fill=tk.X, padx=5, pady=5)

        help_text = (
            "使用方法：\n"
            "1. 复制网页链接（在浏览器中按Ctrl+C）\n"
            "2. 点击上方【从剪贴板导入URL】按钮\n"
            "3. 或直接在上方输入框粘贴URL\n"
            "4. 点击【抓取】按钮开始分析网页"
        )

        ttk.Label(help_frame, text=help_text, justify=tk.LEFT, foreground="gray").pack(padx=10, pady=5, anchor=tk.W)

        # 抓取选项
        options_frame = ttk.LabelFrame(left_frame, text="抓取选项")
        options_frame.pack(fill=tk.X, padx=5, pady=5)

        # 选项行1
        opt_row1 = ttk.Frame(options_frame)
        opt_row1.pack(fill=tk.X, padx=5, pady=2)

        self.max_images_var = tk.IntVar(value=10)
        ttk.Label(opt_row1, text="最大图片数:").pack(side=tk.LEFT)
        ttk.Spinbox(opt_row1, from_=1, to=50, textvariable=self.max_images_var, width=5).pack(side=tk.LEFT, padx=5)

        self.auto_download_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_row1, text="自动下载",
                       variable=self.auto_download_var).pack(side=tk.LEFT, padx=10)

        # 选项行2 - 保存路径
        opt_row2 = ttk.Frame(options_frame)
        opt_row2.pack(fill=tk.X, padx=5, pady=2)

        ttk.Label(opt_row2, text="保存路径:").pack(side=tk.LEFT)
        self.save_path_var = tk.StringVar()
        ttk.Entry(opt_row2, textvariable=self.save_path_var, width=30).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(opt_row2, text="浏览...", command=self._browse_save_path, width=8).pack(side=tk.RIGHT)

        # 请求设置按钮
        settings_frame = ttk.Frame(left_frame)
        settings_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(settings_frame, text="请求头设置",
                  command=self._show_headers_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(settings_frame, text="浏览器模式",
                  command=self._show_browser_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(settings_frame, text="连接设置",
                  command=self._show_connection_dialog).pack(side=tk.LEFT, padx=2)

        # 进度条
        progress_frame = ttk.LabelFrame(left_frame, text="抓取进度")
        progress_frame.pack(fill=tk.X, padx=5, pady=5)

        self.progress_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL,
                                           length=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)

        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack(pady=(0, 5))

        # 日志区域
        log_frame = ttk.LabelFrame(left_frame, text="操作日志")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ====== 右侧面板 ======
        # 图片显示区域
        images_frame = ttk.LabelFrame(right_frame, text="抓取到的图片")
        images_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 图片画布和滚动条
        canvas_frame = ttk.Frame(images_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.images_canvas = tk.Canvas(canvas_frame, background='#f0f0f0')
        images_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL,
                                        command=self.images_canvas.yview)
        self.images_canvas.configure(yscrollcommand=images_scrollbar.set)

        self.images_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        images_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 图片显示框架
        self.images_frame = ttk.Frame(self.images_canvas)
        self.images_canvas.create_window((0, 0), window=self.images_frame, anchor=tk.NW)

        # 绑定滚动
        self.images_frame.bind("<Configure>", self._on_images_frame_configure)

        # 磁力链接区域
        magnets_frame = ttk.LabelFrame(right_frame, text="磁力下载链接")
        magnets_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 磁力链接列表
        magnets_list_frame = ttk.Frame(magnets_frame)
        magnets_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ('name', 'url')
        self.magnets_tree = ttk.Treeview(magnets_list_frame, columns=columns,
                                        show='headings', height=8)

        self.magnets_tree.heading('name', text='名称')
        self.magnets_tree.heading('url', text='链接')
        self.magnets_tree.column('name', width=150)
        self.magnets_tree.column('url', width=350)

        magnets_scrollbar = ttk.Scrollbar(magnets_list_frame, orient=tk.VERTICAL,
                                         command=self.magnets_tree.yview)
        self.magnets_tree.configure(yscrollcommand=magnets_scrollbar.set)

        self.magnets_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        magnets_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 磁力链接按钮
        magnets_btn_frame = ttk.Frame(magnets_frame)
        magnets_btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(magnets_btn_frame, text="复制选中",
                  command=self._copy_selected_magnet).pack(side=tk.LEFT, padx=2)
        ttk.Button(magnets_btn_frame, text="复制全部",
                  command=self._copy_all_magnets).pack(side=tk.LEFT, padx=2)
        ttk.Button(magnets_btn_frame, text="导出到文件",
                  command=self._export_magnets).pack(side=tk.LEFT, padx=2)

        # 底部按钮
        bottom_frame = ttk.Frame(self.frame)
        bottom_frame.pack(fill=tk.X, padx=5, pady=5)

        self.start_btn = ttk.Button(bottom_frame, text="开始抓取",
                                   command=self._start_grab, width=12)
        self.start_btn.pack(side=tk.LEFT, padx=2)

        self.stop_btn = ttk.Button(bottom_frame, text="停止",
                                  command=self._stop_grab, width=8, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="清空结果",
                  command=self._clear_results, width=10).pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="保存配置",
                  command=self._save_to_config_manager, width=10).pack(side=tk.RIGHT, padx=2)

    def _bind_events(self):
        """绑定事件"""
        self.url_var.trace('w', lambda *args: self._save_to_config_manager())
        self.max_images_var.trace('w', lambda *args: self._save_to_config_manager())
        self.auto_download_var.trace('w', lambda *args: self._save_to_config_manager())
        self.save_path_var.trace('w', lambda *args: self._save_to_config_manager())

    def _browse_save_path(self):
        """浏览保存路径"""
        directory = filedialog.askdirectory(title="选择保存路径")
        if directory:
            self.save_path_var.set(directory)

    def _import_from_clipboard(self):
        """从剪贴板导入URL"""
        try:
            clipboard_content = self.frame.clipboard_get()
            self._log(f"从剪贴板读取: {clipboard_content[:100]}")

            # 解析URL
            url = self._parse_url(clipboard_content)
            if url:
                self.url_var.set(url)
                self._log(f"已导入URL: {url}")
                # 自动开始抓取
                self._start_grab()
            else:
                messagebox.showwarning("警告", "剪贴板中没有找到有效的URL")
        except tk.TclError:
            messagebox.showwarning("警告", "剪贴板为空或无法访问")

    def _parse_url(self, text: str) -> Optional[str]:
        """解析URL"""
        # 查找http/https链接
        url_pattern = r'(https?://[^\s<>"\']+)'
        match = re.search(url_pattern, text)
        if match:
            return match.group(1)

        # 如果直接是URL
        if text.startswith('http://') or text.startswith('https://'):
            return text.strip()

        return None

    def _clear_url(self):
        """清空URL"""
        self.url_var.set("")

    def _show_headers_dialog(self):
        """显示请求头设置对话框"""
        dialog = tk.Toplevel(self.frame)
        dialog.title("请求头设置")
        dialog.geometry("500x400")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()

        # User-Agent
        ua_frame = ttk.LabelFrame(dialog, text="User-Agent")
        ua_frame.pack(fill=tk.X, padx=10, pady=5)

        self.ua_var = tk.StringVar(value=self.config_manager.get(self.CONFIG_SECTION, 'user_agent', USER_AGENTS[0]))
        ttk.Entry(ua_frame, textvariable=self.ua_var).pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(ua_frame, text="使用常用UA",
                  command=lambda: self._set_common_ua()).pack(pady=2)

        # 自定义请求头
        headers_frame = ttk.LabelFrame(dialog, text="自定义请求头（JSON格式）")
        headers_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.headers_text = scrolledtext.ScrolledText(headers_frame, height=8)
        self.headers_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 加载现有自定义请求头
        custom_headers = self.config_manager.get(self.CONFIG_SECTION, 'custom_headers', {})
        if custom_headers:
            self.headers_text.insert('1.0', json.dumps(custom_headers, indent=2, ensure_ascii=False))

        # Cookie
        cookie_frame = ttk.LabelFrame(dialog, text="Cookie")
        cookie_frame.pack(fill=tk.X, padx=10, pady=5)

        self.cookie_var = tk.StringVar(value=self.config_manager.get(self.CONFIG_SECTION, 'cookies', ''))
        ttk.Entry(cookie_frame, textvariable=self.cookie_var).pack(fill=tk.X, padx=5, pady=5)

        # 按钮
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text="确定",
                  command=lambda: self._save_headers_dialog(dialog)).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消",
                  command=dialog.destroy).pack(side=tk.RIGHT)

    def _set_common_ua(self):
        """设置常用User-Agent"""
        ua_window = tk.Toplevel(self.frame)
        ua_window.title("选择常用User-Agent")
        ua_window.geometry("600x300")

        listbox = tk.Listbox(ua_window, width=80, height=12)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        for ua in USER_AGENTS:
            listbox.insert(tk.END, ua)

        def select_ua():
            selection = listbox.curselection()
            if selection:
                self.ua_var.set(USER_AGENTS[selection[0]])
                ua_window.destroy()

        ttk.Button(ua_window, text="选择", command=select_ua).pack(pady=5)

    def _save_headers_dialog(self, dialog):
        """保存请求头设置"""
        try:
            # 解析自定义请求头
            headers_str = self.headers_text.get('1.0', tk.END).strip()
            custom_headers = {}
            if headers_str:
                custom_headers = json.loads(headers_str)

            self.config_manager.set(self.CONFIG_SECTION, 'user_agent', self.ua_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'custom_headers', custom_headers)
            self.config_manager.set(self.CONFIG_SECTION, 'cookies', self.cookie_var.get())

            self._log("请求头设置已保存")
            dialog.destroy()
        except json.JSONDecodeError as e:
            messagebox.showerror("错误", f"JSON格式错误: {e}")

    def _show_browser_dialog(self):
        """显示浏览器模式对话框"""
        dialog = tk.Toplevel(self.frame)
        dialog.title("浏览器模式设置")
        dialog.geometry("400x200")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()

        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        ttk.Label(frame, text="选择浏览器模式:").pack(anchor=tk.W, pady=(0, 10))

        browser_var = tk.StringVar(value=self.config_manager.get(self.CONFIG_SECTION, 'browser_mode', 'chrome'))

        browsers = [
            ("Chrome", "chrome"),
            ("Firefox", "firefox"),
            ("Edge", "edge"),
            ("Safari", "safari"),
        ]

        for text, value in browsers:
            ttk.Radiobutton(frame, text=text, value=value,
                           variable=browser_var).pack(anchor=tk.W, pady=2)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=20, pady=10)

        def save():
            self.config_manager.set(self.CONFIG_SECTION, 'browser_mode', browser_var.get())
            self._log(f"浏览器模式已设置为: {browser_var.get()}")
            dialog.destroy()

        ttk.Button(btn_frame, text="确定", command=save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)

    def _show_connection_dialog(self):
        """显示连接设置对话框"""
        dialog = tk.Toplevel(self.frame)
        dialog.title("连接设置")
        dialog.geometry("400x250")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()

        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # 超时设置
        timeout_frame = ttk.Frame(frame)
        timeout_frame.pack(fill=tk.X, pady=5)

        ttk.Label(timeout_frame, text="超时时间(秒):").pack(side=tk.LEFT)
        timeout_var = tk.IntVar(value=self.config_manager.get(self.CONFIG_SECTION, 'timeout', 30))
        ttk.Spinbox(timeout_frame, from_=5, to=120, textvariable=timeout_var, width=5).pack(side=tk.LEFT, padx=5)

        # 代理设置
        proxy_frame = ttk.LabelFrame(frame, text="代理设置")
        proxy_frame.pack(fill=tk.X, pady=10)

        proxy_mode_var = tk.StringVar(value=self.config_manager.get(self.CONFIG_SECTION, 'proxy_mode', 'direct'))

        ttk.Radiobutton(proxy_frame, text="直接连接", value="direct",
                       variable=proxy_mode_var).pack(anchor=tk.W, padx=5, pady=2)
        ttk.Radiobutton(proxy_frame, text="系统代理", value="system",
                       variable=proxy_mode_var).pack(anchor=tk.W, padx=5, pady=2)
        ttk.Radiobutton(proxy_frame, text="自定义代理", value="custom",
                       variable=proxy_mode_var).pack(anchor=tk.W, padx=5, pady=2)

        proxy_url_var = tk.StringVar(value=self.config_manager.get(self.CONFIG_SECTION, 'proxy_url', ''))
        proxy_url_frame = ttk.Frame(proxy_frame)
        proxy_url_frame.pack(fill=tk.X, padx=20, pady=2)
        ttk.Label(proxy_url_frame, text="代理地址:").pack(side=tk.LEFT)
        ttk.Entry(proxy_url_frame, textvariable=proxy_url_var, width=25).pack(side=tk.LEFT, padx=5)

        # 连接模式
        conn_frame = ttk.LabelFrame(frame, text="连接模式")
        conn_frame.pack(fill=tk.X, pady=10)

        conn_mode_var = tk.StringVar(value=self.config_manager.get(self.CONFIG_SECTION, 'connection_mode', 'http'))

        ttk.Radiobutton(conn_frame, text="HTTP", value="http",
                       variable=conn_mode_var).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(conn_frame, text="HTTPS", value="https",
                       variable=conn_mode_var).pack(side=tk.LEFT, padx=10)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=20, pady=10)

        def save():
            self.config_manager.set(self.CONFIG_SECTION, 'timeout', timeout_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'proxy_mode', proxy_mode_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'proxy_url', proxy_url_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'connection_mode', conn_mode_var.get())
            self._log("连接设置已保存")
            dialog.destroy()

        ttk.Button(btn_frame, text="确定", command=save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)

    def _save_to_config_manager(self):
        """保存配置"""
        config_data = {
            'max_images': self.max_images_var.get(),
            'auto_download': self.auto_download_var.get(),
            'save_path': self.save_path_var.get(),
        }
        self.config_manager.set_section(self.CONFIG_SECTION, config_data, auto_save=True)

    def _load_from_config_manager(self):
        """加载配置"""
        section_config = self.config_manager.get_section(self.CONFIG_SECTION)

        if section_config:
            self.max_images_var.set(section_config.get('max_images', 10))
            self.auto_download_var.set(section_config.get('auto_download', False))
            self.save_path_var.set(section_config.get('save_path', ''))

    def _log(self, message: str):
        """记录日志"""
        if not self.is_standalone and self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

        # 同时显示在日志区域
        if hasattr(self, 'log_text'):
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + '\n')
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

    def _on_images_frame_configure(self, event):
        """图片框架配置事件"""
        self.images_canvas.configure(scrollregion=self.images_canvas.bbox("all"))

    def _start_grab(self):
        """开始抓取"""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入或导入网页地址")
            return

        if not url.startswith('http://') and not url.startswith('https://'):
            url = 'https://' + url
            self.url_var.set(url)

        # 清空之前的结果
        self._clear_results()

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.is_running = True

        self.progress_bar['value'] = 0
        self.progress_label.config(text="正在初始化...")

        # 创建配置
        config = WebGrabConfig(
            user_agent=self.config_manager.get(self.CONFIG_SECTION, 'user_agent', USER_AGENTS[0]),
            timeout=self.config_manager.get(self.CONFIG_SECTION, 'timeout', 30),
            proxy_mode=self.config_manager.get(self.CONFIG_SECTION, 'proxy_mode', 'direct'),
            proxy_url=self.config_manager.get(self.CONFIG_SECTION, 'proxy_url', ''),
            browser_mode=self.config_manager.get(self.CONFIG_SECTION, 'browser_mode', 'chrome'),
            connection_mode=self.config_manager.get(self.CONFIG_SECTION, 'connection_mode', 'http'),
            max_images=self.max_images_var.get(),
            auto_download=self.auto_download_var.get(),
            save_path=self.save_path_var.get(),
            custom_headers=self.config_manager.get(self.CONFIG_SECTION, 'custom_headers', {}),
            cookies=self.config_manager.get(self.CONFIG_SECTION, 'cookies', ''),
        )

        self._log(f"开始抓取: {url}")
        self._log(f"使用User-Agent: {config.user_agent[:50]}...")

        # 启动工作线程
        self.worker = WebGrabWorker(
            config=config,
            log_callback=self._log,
            progress_callback=self._update_progress,
            complete_callback=self._on_complete,
            image_found_callback=self._on_image_found,
            magnet_found_callback=self._on_magnet_found
        )

        thread = threading.Thread(target=self._run_worker, args=(url,), daemon=True)
        thread.start()
        self._update_ui()

    def _run_worker(self, url: str):
        """后台线程"""
        try:
            self.worker.process_url(url)
        except Exception as e:
            self.operation_queue.put(('error', str(e)))
        finally:
            self.operation_queue.put(('finished', None))

    def _stop_grab(self):
        """停止抓取"""
        self.is_running = False
        if self.worker:
            self.worker.stop()
        self.progress_label.config(text="已停止")
        self._log("抓取已停止")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    def _update_progress(self, progress: int, message: str):
        """更新进度"""
        self.operation_queue.put(('progress', progress, message))

    def _on_image_found(self, image_info: Dict):
        """发现图片回调"""
        self.operation_queue.put(('image', image_info))

    def _on_magnet_found(self, magnet_info: Dict):
        """发现磁力链回调"""
        self.operation_queue.put(('magnet', magnet_info))

    def _on_complete(self, success: bool, message: str, images: List[Dict], magnets: List[Dict]):
        """完成回调"""
        self.operation_queue.put(('complete', success, message, images, magnets))

    def _update_ui(self):
        """更新UI"""
        try:
            while not self.operation_queue.empty():
                msg = self.operation_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == 'progress':
                    _, progress, message = msg
                    self.progress_bar['value'] = progress
                    self.progress_label.config(text=message)

                elif msg_type == 'image':
                    _, image_info = msg
                    self._display_image(image_info)

                elif msg_type == 'magnet':
                    _, magnet_info = msg
                    self._display_magnet(magnet_info)

                elif msg_type == 'complete':
                    _, success, message, images, magnets = msg
                    self.progress_label.config(text=message)
                    self.progress_bar['value'] = 100

                    if success:
                        self._log(f"抓取完成！图片: {len(images)} 张，磁力链: {len(magnets)} 个")
                        if self.auto_download_var.get() and self.save_path_var.get():
                            self._download_images(images)
                    else:
                        self._log(f"抓取失败: {message}")

                elif msg_type == 'error':
                    _, error_msg = msg
                    self._log(f"[错误] {error_msg}")
                    messagebox.showerror("错误", f"抓取失败: {error_msg}")

                elif msg_type == 'finished':
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self.is_running = False

        except queue.Empty:
            pass

        if self.is_running:
            self.frame.after(100, self._update_ui)

    def _display_image(self, image_info: Dict):
        """显示图片"""
        try:
            # 下载图片
            img_data = self.worker.download_image(image_info['url'])
            if not img_data:
                return

            # 创建图片
            img = Image.open(io.BytesIO(img_data))

            # 调整大小
            max_size = (200, 200)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)

            # 转换为PhotoImage
            photo = ImageTk.PhotoImage(img)

            # 创建标签显示图片
            img_frame = ttk.Frame(self.images_frame, relief=tk.RIDGE, borderwidth=1)
            img_frame.pack(side=tk.LEFT, padx=5, pady=5)

            img_label = ttk.Label(img_frame, image=photo)
            img_label.image = photo  # 保持引用
            img_label.pack()

            # 添加URL标签
            url_label = ttk.Label(img_frame, text=image_info['url'][:40] + '...',
                                 wraplength=180, font=('', 8))
            url_label.pack()

            # 添加类型标签
            type_text = "主图" if image_info.get('type') == 'main' else "内容图"
            type_label = ttk.Label(img_frame, text=type_text, foreground='blue')
            type_label.pack()

            # 绑定点击事件 - 复制URL
            def copy_url(event, url=image_info['url']):
                self.frame.clipboard_clear()
                self.frame.clipboard_append(url)
                self._log(f"已复制图片URL: {url[:50]}...")

            img_label.bind('<Button-1>', copy_url)
            url_label.bind('<Button-1>', copy_url)

            self.image_labels.append(img_label)
            self.current_images.append(image_info)

        except Exception as e:
            self._log(f"显示图片失败: {e}")

    def _display_magnet(self, magnet_info: Dict):
        """显示磁力链接"""
        self.magnets_tree.insert('', tk.END, values=(
            magnet_info.get('name', '未命名'),
            magnet_info.get('url', '')[:80] + '...' if len(magnet_info.get('url', '')) > 80 else magnet_info.get('url', '')
        ))

        self.current_magnets.append(magnet_info)

    def _download_images(self, images: List[Dict]):
        """下载图片"""
        save_path = self.save_path_var.get()
        if not save_path:
            return

        os.makedirs(save_path, exist_ok=True)

        for idx, img in enumerate(images, 1):
            try:
                img_data = self.worker.download_image(img['url'])
                if img_data:
                    # 从URL获取扩展名
                    ext = os.path.splitext(urllib.parse.urlparse(img['url']).path)[1]
                    if not ext:
                        ext = '.jpg'

                    filename = f"image_{idx:03d}{ext}"
                    filepath = os.path.join(save_path, filename)

                    with open(filepath, 'wb') as f:
                        f.write(img_data)

                    self._log(f"已保存: {filename}")
            except Exception as e:
                self._log(f"保存图片失败: {e}")

    def _copy_selected_magnet(self):
        """复制选中的磁力链接"""
        selection = self.magnets_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要复制的磁力链接")
            return

        urls = []
        for item in selection:
            values = self.magnets_tree.item(item, 'values')
            if values:
                # 找到完整的URL
                for magnet in self.current_magnets:
                    if magnet.get('name') == values[0]:
                        urls.append(magnet.get('url', ''))
                        break

        if urls:
            self.frame.clipboard_clear()
            self.frame.clipboard_append('\n'.join(urls))
            self._log(f"已复制 {len(urls)} 个磁力链接")

    def _copy_all_magnets(self):
        """复制所有磁力链接"""
        if not self.current_magnets:
            messagebox.showinfo("提示", "没有可复制的磁力链接")
            return

        urls = [m.get('url', '') for m in self.current_magnets]
        self.frame.clipboard_clear()
        self.frame.clipboard_append('\n'.join(urls))
        self._log(f"已复制全部 {len(urls)} 个磁力链接")

    def _export_magnets(self):
        """导出磁力链接到文件"""
        if not self.current_magnets:
            messagebox.showinfo("提示", "没有可导出的磁力链接")
            return

        file_path = filedialog.asksaveasfilename(
            title="导出磁力链接",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for magnet in self.current_magnets:
                        f.write(f"{magnet.get('name', '未命名')}\n")
                        f.write(f"{magnet.get('url', '')}\n")
                        f.write("\n")

                self._log(f"已导出到: {file_path}")
                messagebox.showinfo("成功", f"已导出 {len(self.current_magnets)} 个磁力链接")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")

    def _clear_results(self):
        """清空结果"""
        # 清空图片
        for widget in self.images_frame.winfo_children():
            widget.destroy()
        self.image_labels.clear()
        self.current_images.clear()

        # 清空磁力链接
        for item in self.magnets_tree.get_children():
            self.magnets_tree.delete(item)
        self.current_magnets.clear()

        # 清空日志
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state=tk.DISABLED)

        # 重置进度
        self.progress_bar['value'] = 0
        self.progress_label.config(text="准备就绪")

    def get_frame(self) -> ttk.Frame:
        """获取选项卡主框架"""
        return self.frame


# ========== 独立运行入口 ==========
if __name__ == "__main__":
    root = tk.Tk()
    root.title("网页图片抓取工具 - 独立版")
    root.geometry("1200x800")

    tab = WebGrabTab(root, controller=None)
    tab.get_frame().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    root.mainloop()