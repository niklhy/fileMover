#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网页图片抓取选项卡模块 - 无额外依赖版本
支持独立运行和通过main.py嵌入运行
功能：
1. 通过剪贴板、手动输入或拖放接收链接
2. 后台读取网页内容
3. 抓取主图并显示
4. 抓取磁力链下载链接
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import json
import re
import html
import glob
import hashlib
import importlib.util
import subprocess
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional, Dict, List, Set, Callable, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from PIL import Image, ImageTk
import io
import sys
import webbrowser
import time
import ssl
import random
import gzip
import zlib
import ctypes

# ========== 可选依赖（与参考脚本 crawler_core.py 保持一致） ==========
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BeautifulSoup = None
    BS4_AVAILABLE = False

try:
    import chardet  # type: ignore
    CHARDET_AVAILABLE = True
except ImportError:
    chardet = None
    CHARDET_AVAILABLE = False

# 番号/后缀识别（复用项目内的 pattern_matcher，用于识别 -U / -UC / -UNC 等无码标记）
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    # 独立运行时兜底：只保留本项目用到的无码后缀识别
    class PatternMatcher:  # type: ignore
        """兜底匹配器（同目录缺少 pattern_matcher.py 时使用）"""

        uncensored_patterns = [
            (re.compile(r'[-_]UC(?=\.|$|[-_])', re.I), '-UC'),
            (re.compile(r'[-_]UNC(?=\.|$|[-_])', re.I), '-UNC'),
            (re.compile(r'[-_](?:Uncensored|un|u|ai)(?=\.|$|[-_])', re.I), '-Uncensored'),
        ]

# 浏览器渲染支持（用于读取 JS 动态生成的内容）
try:
    from playwright.sync_api import sync_playwright  # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

# 拼音排序支持（列表模式按“首字拼音声母”排序时用到；缺失则按 Unicode 码位兜底）
try:
    import pypinyin
    PYPINYIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    pypinyin = None
    PYPINYIN_AVAILABLE = False

# 本地翻译大模型（在“本地设置”里为每个模型指定所在文件夹）
# - nllb-200-distilled-600M：CTranslate2 格式目录（依赖 ctranslate2 + sentencepiece）
# - Sakura-*：GGUF 格式目录（依赖 llama-cpp-python）
TRANSLATION_MODELS = [
    'nllb-200-distilled-600M',
    'Sakura-7B-Qwen2.5-v1.0-GGUF',
    'Sakura-14B-Qwen2.5-v1.0-GGUF',
    'Sakura-GalTransl-7B-v3.7',
]
# NLLB 的语言标记：日文 → 简体中文
NLLB_SOURCE_LANG = 'jpn_Jpan'
NLLB_TARGET_LANG = 'zho_Hans'

try:
    from selenium import webdriver  # type: ignore
    from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore
    from selenium.webdriver.common.by import By  # type: ignore
    from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
    from selenium.webdriver.support import expected_conditions as EC  # type: ignore
    SELENIUM_AVAILABLE = True
except ImportError:
    webdriver = None
    ChromeOptions = None
    SELENIUM_AVAILABLE = False

# 常量定义
CONFIG_FILE_NAME = "app_config.json"

# 视频检索索引缓存文件：首次检索时扫描根目录生成，之后直接读该文件加速后续搜索
# （放在软件所在文件夹下；采用 JSON 文件而非纯内存缓存，可跨重启持久化、无需重复扫描）
VIDEO_INDEX_CACHE_FILE = "video_index_cache.json"

# 系列名模糊匹配：最多列出的文件夹数量（避免短系列名产生大量结果）
MAX_SERIES_FOLDER_HITS = 20

# 视频检索索引缓存格式版本：解析规则变更（如放宽演员文件夹命名）后递增，
# 旧版本的缓存会自动失效并重新扫描，避免用户必须手动点“重建索引”
# v3：索引记录新增 series_scope 标记（文件夹“范围=系列”），用于系列名单独检索
VIDEO_INDEX_VERSION = 3

# 文件夹“范围”取值：全部（默认，参与番号/演员/系列所有检索）/ 系列（仅参与系列名检索）
FOLDER_SCOPE_ALL = "all"
FOLDER_SCOPE_SERIES = "series"
FOLDER_SCOPE_LABELS = {FOLDER_SCOPE_ALL: "全部", FOLDER_SCOPE_SERIES: "系列"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ========== 拖放支持 ==========
# tkinterdnd2 为可选依赖：安装后可从浏览器直接拖入图片，未安装时自动降级为手动输入
try:
    from tkinterdnd2 import TkinterDnD
    DND_AVAILABLE = True
except Exception:
    TkinterDnD = None
    DND_AVAILABLE = False

# tkdnd 的平台无关类型名（Windows 下 DND_HTML 对应 CF_HTML，DND_URL 对应 UniformResourceLocator）
DND_HTML = 'DND_HTML'
DND_URL = 'DND_URL'
DND_TEXT = 'DND_Text'
DND_FILES = 'DND_Files'
DND_ALL = '*'
DND_ACTION_COPY = 'copy'

# 主图 CSS 选择器默认值（可在 app_config.json 的 web_grab.main_image_selector 中微调，
# 该选择器在内容A（body > div.container）范围内生效，只取 1 张）
DEFAULT_MAIN_IMAGE_SELECTOR = "div.movie a.bigImage"

# 视频剪辑缩略图容器默认值（可在 app_config.json 的 web_grab.sample_image_selector 中微调，
# 同样在内容A范围内查找，数量不限，允许为空）
DEFAULT_SAMPLE_CONTAINER_SELECTOR = "div#sample-waterfall"

# 剪辑图链接选择器：该链接的 href 才是大图，容器里的 img 通常只是小图
DEFAULT_SAMPLE_LINK_SELECTOR = "a.sample-box"

# 磁力链表默认值（可在 app_config.json 的 web_grab.magnet_table_selector 中微调，
# 该表格由 JS 异步生成，通常需要浏览器渲染后才能取到）
DEFAULT_MAGNET_TABLE_SELECTOR = "table#magnet-table"

# 磁力链异步接口（表为空时按该接口取行数据）：
# 路径可为相对路径（自动补上页面的 scheme://netloc）或完整 URL；
# 参数模板中的 {xxx} 会替换为页面脚本里的 var xxx = 值，{floor} 为随机整数
DEFAULT_MAGNET_AJAX_PATH = "/ajax/uncledatoolsbyajax.php"
DEFAULT_MAGNET_AJAX_PARAMS = "gid={gid}&lang={lang}&img={img}&uc={uc}&floor={floor}"

# 缩略图参数：每个缩略图放在 1:1 的方框中居中显示
THUMB_SIZE = 150
THUMB_PAD = 10

# 图片浏览浮窗层
VIEWER_BG = '#101010'              # 浮窗背景
VIEWER_MARGIN = 70                 # 图片与浮窗边缘的最小间距

# 图片浏览层信息栏（位于图片【下方】的独立区域，不再压在图片上遮挡画面）
VIEWER_INFO_BG = '#1a1a1a'         # 信息栏底色
VIEWER_INFO_FG = '#e8e8e8'         # 信息栏主文字颜色（序号 + 标题）
VIEWER_INFO_FG_DIM = '#9e9e9e'     # 信息栏次要文字颜色（演员 / 图片信息 / 操作提示）

# 图片浏览层的缩放（滚轮）与拖动浏览
VIEWER_ZOOM_STEP = 1.15            # 滚轮每一格的缩放比例
VIEWER_ZOOM_MIN = 0.1              # 最小缩放（相对“适应窗口”）
VIEWER_ZOOM_MAX = 8.0              # 最大缩放（相对“适应窗口”）

# 图片浏览层左右切换按钮（实心三角图标；位置固定在窗口两侧中线，不随图片横竖尺寸变化）
VIEWER_ARROW_BTN_SIZE = 64            # 点击区边长（略大于图标，点击更易命中）
VIEWER_ARROW_ICON_W = 22              # 三角图标宽
VIEWER_ARROW_ICON_H = 30              # 三角图标高
VIEWER_ARROW_MARGIN_X = 24            # 按钮点击区距窗口左右边缘的间距
VIEWER_ARROW_BG = '#1e1e1e'           # 按钮点击区底色
VIEWER_ARROW_BG_HOVER = '#333333'     # 按钮点击区悬停底色
VIEWER_ARROW_COLOR = '#d8d8d8'        # 三角图标颜色
VIEWER_ARROW_COLOR_HOVER = '#ffffff'  # 三角图标悬停颜色

# 缩略图显示/隐藏图标按钮（固定在图区旁，不随图区滚动条移动）
THUMB_TOGGLE_BTN_SIZE = 30            # 图标按钮尺寸

# 大图缓存上限（超出后按写入顺序淘汰）
IMAGE_CACHE_MAX_BYTES = 200 * 1024 * 1024

# 图片下载线程池并发数
IMAGE_POOL_MAX_WORKERS = 6

# 设置浮窗层（与图片浏览层同风格）
SETTINGS_TITLE_BG = '#1c1c1c'
SETTINGS_TITLE_HEIGHT = 54

# 保存目录历史记录保留数量
SAVE_DIR_HISTORY_SIZE = 10

# 保存目录历史下拉层：层展开期间轮询鼠标左键的间隔（毫秒）
SAVE_DIR_POPUP_POLL_MS = 60


def _left_button_down() -> bool:
    """当前是否按下了鼠标左键（仅 Windows 支持，其它平台恒返回 False）"""
    if os.name != 'nt':
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False

# 磁力链表格：识别到无码/破解标记（-U / -UC / -UNC 等）的行标红
MAGNET_UNCENSORED_TAG = 'uncensored'   # 无码标记行的 Treeview 标签名
MAGNET_UNCENSORED_COLOR = '#cc0000'    # 无码标记行文字颜色（红色）

# 页签栏鼠标滚轮横向滚动步长（单位：页签栏可视宽度的 1/10）
TAB_WHEEL_STEP_UNITS = 1

# 通配符 {#} 批量抓取的最大页签数（超过则弹窗确认，避免一次生成过多页签）
MAX_BATCH_PAGES = 300

# 字段抓取方式（与参考工具一致：css / regex / custom）
FIELD_RULE_TYPES = ('css', 'regex', 'custom')

# 字段抓取规则默认值（均在内容A内执行，可在“爬虫设置”中修改方式与内容）
DEFAULT_FIELD_RULES: List[Dict] = [
    {
        'field_id': 'title',
        'field_name': '标题',
        'rule_type': 'css',
        'rule_content': 'h3',
        'multiple': False,
        'enabled': True,
    },
    {
        'field_id': 'actors',
        'field_name': '演员',
        'rule_type': 'css',
        'rule_content': 'p span.genre>a',
        'multiple': True,
        'enabled': True,
    },
    {
        'field_id': 'code',
        'field_name': '番号',
        'rule_type': 'custom',
        'rule_content': (
            "node = soup.select_one('span[style=\"color:#CC0000;\"]')\n"
            "result = node.text.strip() if node else ''"
        ),
        'multiple': False,
        'enabled': True,
    },
    {
        'field_id': 'date',
        'field_name': '發行日期',
        'rule_type': 'custom',
        'rule_content': (
            "node = soup.find('span', class_='header', string='發行日期:')\n"
            "result = node.next_sibling.strip() if node and node.next_sibling else ''"
        ),
        'multiple': False,
        'enabled': True,
    },
    {
        'field_id': 'duration',
        'field_name': '長度(秒)',
        'rule_type': 'custom',
        'rule_content': (
            "node = soup.find('span', class_='header', string='長度:')\n"
            "text = node.next_sibling.strip() if node and node.next_sibling else ''\n"
            "hours = re.search(r'(\\d+)\\s*小時', text)\n"
            "minutes = re.search(r'(\\d+)\\s*分鐘', text)\n"
            "total_seconds = 0\n"
            "if hours:\n"
            "    total_seconds += int(hours.group(1)) * 3600\n"
            "if minutes:\n"
            "    total_seconds += int(minutes.group(1)) * 60\n"
            "result = total_seconds"
        ),
        'multiple': False,
        'enabled': True,
    },
    {
        'field_id': 'series',
        'field_name': '系列',
        'rule_type': 'custom',
        'rule_content': (
            "node = soup.find('span', class_='header', string='系列:')\n"
            "result = ''\n"
            "if node:\n"
            "    sib = node.next_sibling\n"
            "    if sib is not None:\n"
            "        result = sib.get_text(strip=True) if hasattr(sib, 'get_text') else str(sib).strip()\n"
            "    if not result and node.find_next_sibling() is not None:\n"
            "        s2 = node.find_next_sibling()\n"
            "        result = s2.get_text(strip=True) if hasattr(s2, 'get_text') else str(s2).strip()"
        ),
        'multiple': False,
        'enabled': True,
    },
    {
        'field_id': 'publisher',
        'field_name': '发行商',
        'rule_type': 'custom',
        'rule_content': (
            "node = soup.find('span', class_='header', string='發行商:')\n"
            "result = ''\n"
            "if node:\n"
            "    sib = node.next_sibling\n"
            "    if sib is not None:\n"
            "        result = sib.get_text(strip=True) if hasattr(sib, 'get_text') else str(sib).strip()\n"
            "    if not result and node.find_next_sibling() is not None:\n"
            "        s2 = node.find_next_sibling()\n"
            "        result = s2.get_text(strip=True) if hasattr(s2, 'get_text') else str(s2).strip()"
        ),
        'multiple': False,
        'enabled': True,
    },
    {
        'field_id': 'maker',
        'field_name': '制作商',
        'rule_type': 'custom',
        'rule_content': (
            "node = soup.find('span', class_='header', string='製作商:')\n"
            "result = ''\n"
            "if node:\n"
            "    sib = node.next_sibling\n"
            "    if sib is not None:\n"
            "        result = sib.get_text(strip=True) if hasattr(sib, 'get_text') else str(sib).strip()\n"
            "    if not result and node.find_next_sibling() is not None:\n"
            "        s2 = node.find_next_sibling()\n"
            "        result = s2.get_text(strip=True) if hasattr(s2, 'get_text') else str(s2).strip()"
        ),
        'multiple': False,
        'enabled': True,
    },
    {
        # 类别（“類別:”标题后面那段 heap：<span class="genre"><label>
        # <input type="checkbox" ...><a href=".../genre/f">單體作品</a></label></span>）
        # 这里只取“类别名”（多个）；类别对应的网址与标签代码由 extract_categories 精确定位抓取
        'field_id': 'categories',
        'field_name': '類別',
        'rule_type': 'custom',
        # 定位时排除导航栏里的“類別”菜单项：<a>/<script>/<style> 一律跳过，
        # 且只认“自身直接文本含類別”的标签（导航菜单的文字都在后代 <a>/<li> 里）
        'rule_content': (
            "def own_text(tag):\n"
            "    parts = [s for s in tag.find_all(string=True, recursive=False) if isinstance(s, str)]\n"
            "    return ' '.join([p.strip() for p in parts]).strip()\n"
            "\n"
            "header = None\n"
            "for tag in soup.find_all(True):\n"
            "    if tag.name in ('a', 'script', 'style'):\n"
            "        continue\n"
            "    if '類別' in own_text(tag):\n"
            "        header = tag\n"
            "        break\n"
            "result = []\n"
            "if header is not None:\n"
            "    block = None\n"
            "    node = header\n"
            "    tried = 0\n"
            "    while node is not None and tried < 5:\n"
            "        node = node.find_next_sibling()\n"
            "        tried += 1\n"
            "        if node is None:\n"
            "            break\n"
            "        if node.find('a', href=True) is not None:\n"
            "            block = node\n"
            "            break\n"
            "    if block is not None:\n"
            "        for a in block.find_all('a', href=True):\n"
            "            name = a.get_text(strip=True)\n"
            "            if name and name not in result:\n"
            "                result.append(name)"
        ),
        'multiple': True,
        'enabled': True,
    },
]

# 拖放区配色
DROP_ZONE_BG = '#f7f7f7'
DROP_ZONE_BORDER = '#b0b0b0'
DROP_ZONE_ACTIVE_BG = '#e6f7e6'
DROP_ZONE_ACTIVE_BORDER = '#4caf50'

# ========== 视频列表页（瀑布流列表）爬虫默认配置 ==========
# 列表页与详情页结构完全不同：列表页的每条记录都在 div#waterfall 下的 div.movie-box 内，
# 这里的选择器都相对于“单个视频条目(div.movie-box)”执行；写法为 CSS 选择器，
# 取值时可用 “css@属性名” 形式（如 img@src 表示取该元素的 src 属性，不写则取文本）。
DEFAULT_LIST_CONTAINER_SELECTOR = "div#waterfall"              # 视频流容器
# 单个视频条目：不同版本站点上 movie-box 可能是 div，也可能是 a（用逗号并列兼容两种）
DEFAULT_LIST_ITEM_SELECTOR = "div.movie-box, a.movie-box"
DEFAULT_LIST_THUMB_SELECTOR = "div.photo-frame img@src"        # 视频缩略图
DEFAULT_LIST_TITLE_SELECTOR = "div.photo-frame img@title"      # 视频标题
# 番号：站点常把番号放在 <date> 里，也可能与日期同写在 span 文本里；
# 解析时会遍历所有候选文本，挑出“像番号”的那一个（见 _pick_code）
DEFAULT_LIST_CODE_SELECTOR = "div.photo-info date, div.photo-info span"
# 发布日期：遍历候选取日期格式文本，取不到再在整条记录文本中兜底查找（见 _pick_date）
DEFAULT_LIST_DATE_SELECTOR = "div.photo-info date, div.photo-info span"
DEFAULT_LIST_LINK_SELECTOR = "a@href"                          # 详情页链接
# 视频标志：可能同时存在多个（高清 / 字幕），全部取出后用空格拼接
DEFAULT_LIST_FLAG_SELECTOR = "div.item-tag button, span.movie-box-hd"
DEFAULT_LIST_NEXT_SELECTOR = "a#next"                          # “下一页”链接
# 分页控件容器（页脚的 <ul class="pagination pagination-lg">）：
# 从中读取当前页码与总页数（见 parse_pagination）
DEFAULT_PAGINATION_SELECTOR = "ul.pagination"
# 公告层：其中的多个网址会被自动补充到“网址库”
DEFAULT_LIST_ALERT_SELECTOR = "div.alert.alert-info.alert-dismissable.alert-common"
# 页码地址模板：{page} 为页码占位；用于“上一页/下一页/跳转”
DEFAULT_LIST_PAGE_TEMPLATE = "https://www.busfan.cyou/page/{page}"

# 列表页默认选择器版本：默认值有修正时递增；
# 旧版本配置文件里保存的（可能已失效的）默认值会在下次启动时自动升级覆盖。
LIST_CONFIG_VERSION = 3

# 详情页里需要“同时抓取对应超链接”的字段（系列 / 演员 / 发行商 / 制作商）
DETAIL_LINK_FIELDS = ('series', 'actors', 'publisher', 'maker')

# 列表页搜索：搜索地址模板（{keyword} 为关键词；优先使用页面中自动识别到的跳转路径）
DEFAULT_LIST_SEARCH_PATH = "/search/{keyword}"

# 网址库入口切换：抓取失败时最多自动尝试的入口数量
LIST_HOST_MAX_TRIES = 3

# 列表页番号/日期兜底正则（选择器取不到时使用）
LIST_DATE_RE = re.compile(r'(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})')
LIST_CODE_RE = re.compile(r'([A-Za-z]{2,}[A-Za-z0-9]*[-_]?\d{2,}(?:[-_]\d+)?)')
# “像番号”的整串文本（如 YAKO-094 / FC2-PPV-123456 / 300MIUM-1185）
LIST_CODE_TEXT_RE = re.compile(r'^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+$')

# 列表页浏览层（从主窗口右侧滑出的层）尺寸与动画
LIST_PANEL_WIDTH_RATIO = 0.8      # 层宽度 = 主窗口宽度的 80%
LIST_PANEL_ANIM_MS = 10           # 每帧间隔（毫秒）
LIST_PANEL_ANIM_STEPS = 12        # 滑动动画帧数
LIST_THUMB_SIZE = 200             # 列表页缩略图方框宽度（高度不变，宽度收窄，一排 4 个）
LIST_THUMB_IMG_H = 220            # 缩略图在方框内的最大显示高度
LIST_CARD_H = LIST_THUMB_IMG_H + 84   # 卡片总高（缩略图 + 番号 + 2行标题 + 日期/标志）

# 列表页浏览层配色：与主窗口一致的浅色风格（不再是图片浏览层的黑色底）
LIST_PANEL_BG = '#f0f0f0'
LIST_PANEL_BAR_BG = '#e9e9e9'
LIST_PANEL_STATUS_BG = '#ececec'
LIST_PANEL_FG = '#333333'
LIST_PANEL_FG_DIM = '#777777'
LIST_PANEL_AREA_BG = '#f5f5f5'
LIST_PANEL_EDGE_BG = '#1e88e5'    # 层左侧的明显颜色边条（与主窗口背景区分开）

# 列表层磁盘缓存：页面解析结果与缩略图存放在项目文件夹的 cache/list_page 目录，
# 关闭层再打开时直接显示缓存内容（无需重新联网抓取）；
# 缓存文件夹容量超过 LIST_CACHE_MAX_BYTES 后自动清掉最旧的文件
LIST_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'cache', 'list_page')
# 缓存文件夹容量上限的“兜底默认值”（50MB）。实际生效值由“本地设置 → 列表缓存上限(MB)”
# 动态控制（存于 self._list_cache_max_bytes），此处仅在配置缺失/异常时回退使用。
LIST_CACHE_MAX_BYTES = 50 * 1024 * 1024   # 50MB（兜底默认）

# 多页连续抓取时的缩略图节流：上一页还有超过 LIST_BATCH_THUMB_PENDING 张图没下载完，
# 就先等一等再抓下一页（最多等 LIST_BATCH_THUMB_WAIT_SEC 秒），
# 避免图片请求无限积压、导致缩略图迟迟不显示
LIST_BATCH_THUMB_PENDING = 40
LIST_BATCH_THUMB_WAIT_SEC = 20

# 類別库缓存：从 {入口}/genre 抓取的全部类别（按页面的大类/小类分组）。
# 手动刷新前一直沿用缓存；文件损坏/不存在时打开层后提示刷新
GENRE_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'cache', 'genre_library.json')

# “页面”图标按钮：与“显示/隐藏缩略图”图标按钮完全一致的尺寸与配色
PAGE_BTN_BG = '#ffffff'
PAGE_BTN_BG_HOVER = '#f7f7f7'
PAGE_BTN_BORDER = '#c8c8c8'
PAGE_BTN_COLOR = '#333333'
PAGE_BTN_COLOR_HOVER = '#000000'


def normalize_field_rules(rules) -> List[Dict]:
    """规范化字段抓取规则，保证抓取一定可用。

    历史版本在“爬虫设置”控件已销毁时仍会补存规则，导致配置里的
    ``rule_content`` 被写空，表现为“所有字段都抓不到”。这里统一做自愈：
      1) ``rule_content`` 为空 → 回退该字段的默认规则内容；
      2) ``rule_type`` 非法（非 css/regex/custom）→ 回退默认方式；
      3) 配置里缺失的字段（系列/发行商/制作商等）→ 追加默认规则；
      4) 整体不是列表或为空 → 直接用默认规则。

    :param rules: 配置中读取到的规则列表（任意类型）
    :return: 可直接用于抓取的规则列表（list[dict]）
    """
    defaults = {str(rule.get('field_id', '')): rule for rule in DEFAULT_FIELD_RULES}
    if not isinstance(rules, list) or not rules:
        return [dict(rule) for rule in DEFAULT_FIELD_RULES]

    result: List[Dict] = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        merged = dict(item)
        field_id = str(merged.get('field_id') or '')
        default = defaults.get(field_id) or {}

        rule_type = str(merged.get('rule_type') or '').lower()
        if rule_type not in FIELD_RULE_TYPES:
            rule_type = str(default.get('rule_type') or 'css')
        merged['rule_type'] = rule_type

        if not str(merged.get('rule_content') or '').strip() and default:
            merged['rule_content'] = default.get('rule_content', '')
        result.append(merged)

    existing_ids = {str(r.get('field_id') or '') for r in result}
    for rule in DEFAULT_FIELD_RULES:
        if str(rule.get('field_id', '')) not in existing_ids:
            result.append(dict(rule))
    return result


# ========== 视频列表页解析工具（模块级函数，便于单独测试） ==========
def _split_css_attr(selector: str) -> Tuple[str, str]:
    """把 “css@属性名” 形式的选择器拆成 (css选择器, 属性名)。
    没有 @ 时属性名为空字符串，表示取元素的文本内容。
    """
    selector = (selector or '').strip()
    if not selector:
        return '', ''
    if '@' in selector:
        sel, _, attr = selector.rpartition('@')
        return sel.strip(), attr.strip()
    return selector, ''


def _extract_texts_in_scope(scope, selector: str) -> List[str]:
    """在 scope（BeautifulSoup 节点）内按选择器取出【所有】匹配项的值。

    - selector 为空 → 直接对 scope 本身取值；
    - selector 支持 “css@属性名” 写法（取属性而不是文本）；
    - 匹配不到子节点时会尝试用选择器直接匹配 scope 本身
      （如条目本身就是 a.movie-box 时，“a@href” 要能取到它自己的 href）。
    """
    if scope is None:
        return []

    sel, attr = _split_css_attr(selector)
    nodes = []
    if sel:
        try:
            nodes = list(scope.select(sel))
        except Exception:
            nodes = []
        if not nodes:
            try:
                if scope.matches(sel):
                    nodes = [scope]
            except Exception:
                nodes = []
    else:
        nodes = [scope]

    values: List[str] = []
    for node in nodes:
        if attr:
            try:
                val = str(node.get(attr) or '').strip()
            except Exception:
                val = ''
        else:
            try:
                val = node.get_text(' ', strip=True)
            except Exception:
                val = str(node).strip()
        if val:
            values.append(val)
    return values


def _extract_in_scope(scope, selector: str) -> str:
    """取选择器匹配到的第一个值（文本或属性）"""
    values = _extract_texts_in_scope(scope, selector)
    return values[0] if values else ''


def _item_text(node) -> str:
    """整条记录的纯文本（用于兜底查找）"""
    try:
        return node.get_text(' ', strip=True)
    except Exception:
        try:
            return str(node)
        except Exception:
            return ''


def _looks_like_code(text: str) -> bool:
    """判断一段文本是否“像番号”（如 YAKO-094 / FC2-PPV-123456 / MIRD-004）"""
    text = (text or '').strip()
    if not text or len(text) > 32 or not LIST_CODE_TEXT_RE.match(text):
        return False
    # 纯日期（2026-06-12）不算番号
    return LIST_DATE_RE.fullmatch(text) is None


def _code_candidates(text: str) -> List[str]:
    """从一段文本中按出现顺序提取所有“像番号”的候选（先剔除日期，避免日期被当成番号）

    番号形式很杂：YAKO-094 / T38-065 / 300MIUM-1185 / FC2-PPV-123456 /
    012345-678 等，因此这里不限定“开头必须有 2 个以上字母”，
    只要满足“字母数字 + 连字符/下划线 + 数字”且不是纯日期即可。
    """
    cleaned = LIST_DATE_RE.sub(' ', text or '')
    found: List[str] = []
    for m in re.finditer(r'[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+', cleaned):
        token = m.group(0)
        if _looks_like_code(token) and token not in found:
            found.append(token)
    return found


def _pick_code(node, selector: str) -> str:
    """挑选番号：先看选择器匹配到的文本，再在整条记录文本中兜底搜索"""
    for text in _extract_texts_in_scope(node, selector):
        found = _code_candidates(text)
        if found:
            return found[0]
    # 选择器没取到（或结构有差异）时，直接在整条记录文本中查找
    found = _code_candidates(_item_text(node))
    return found[0] if found else ''


def _pick_date(node, selector: str) -> str:
    """从候选文本里挑选发布日期（形如 2026-06-12），最后在整段文本中兜底"""
    for text in _extract_texts_in_scope(node, selector):
        m = LIST_DATE_RE.search(text or '')
        if m:
            return m.group(1)
    m = LIST_DATE_RE.search(_item_text(node))
    return m.group(1) if m else ''


def _join_flags(selector_texts: List[str]) -> str:
    """把多个标志文本合并显示（去重保序，如 “高清 字幕”）"""
    result: List[str] = []
    for text in selector_texts:
        for part in re.split(r'[\s/|、,，]+', text or ''):
            part = part.strip()
            if part and part not in result:
                result.append(part)
    return ' '.join(result)


def _build_list_item(node, base_url: str, cfg: Dict) -> Dict:
    """把单个视频条目节点(div.movie-box)解析成一条记录"""
    thumb = _extract_in_scope(node, cfg.get('thumb') or DEFAULT_LIST_THUMB_SELECTOR)
    if not thumb:
        # 图片懒加载兜底：真实地址常在 data-src / data-original 等属性里
        for fallback in ('img@data-src', 'img@data-original',
                         'img@data-lazy-src', 'img@data-echo'):
            thumb = _extract_in_scope(node, fallback)
            if thumb:
                break

    title = _extract_in_scope(node, cfg.get('title') or DEFAULT_LIST_TITLE_SELECTOR)
    if not title:
        title = _extract_in_scope(node, 'div.photo-frame img@alt')
    if not title:
        title = _extract_in_scope(node, 'div.photo-info span.item-title')
    if not title:
        title = _extract_in_scope(node, 'h3')

    # 番号 / 发布日期：站点写法差异较大，按“内容特征”挑选，避免抓错列
    code = _pick_code(node, cfg.get('code') or DEFAULT_LIST_CODE_SELECTOR)
    date = _pick_date(node, cfg.get('date') or DEFAULT_LIST_DATE_SELECTOR)

    # 视频标志：可能有多个（高清 / 字幕）
    flag = _join_flags(
        _extract_texts_in_scope(node, cfg.get('flag') or DEFAULT_LIST_FLAG_SELECTOR))

    if not title:
        title = code

    link = _extract_in_scope(node, cfg.get('link') or DEFAULT_LIST_LINK_SELECTOR)
    if not link:
        link = _extract_in_scope(node, 'a@href')
    if not link:
        try:
            link = str(node.get('href') or '').strip()
        except Exception:
            link = ''
    url = urllib.parse.urljoin(base_url, link) if link else ''

    return {
        'thumb': urllib.parse.urljoin(base_url, thumb) if thumb else '',
        'title': title,
        'code': code,
        'date': date,
        'flag': flag,
        'url': url,
    }


def _parse_list_items_regex(page_html: str, base_url: str, cfg: Dict) -> List[Dict]:
    """未安装 beautifulsoup4 时的正则兜底解析（只覆盖标准瀑布流结构）"""
    items: List[Dict] = []
    item_cls = 'movie-box'
    pattern = re.compile(
        r'<a\b([^>]*\bclass=["\'][^"\']*\b' + re.escape(item_cls) + r'\b[^"\']*["\'][^>]*)>'
        r'(.*?)</a>', re.S | re.I)
    for m in pattern.finditer(page_html or ''):
        attrs, inner = m.group(1), m.group(2)
        href_m = re.search(r'href\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
        img_m = re.search(r'<img\b([^>]*)>', inner, re.I)
        img_attrs = img_m.group(1) if img_m else ''
        src_m = (re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', img_attrs, re.I)
                 or re.search(r'\bdata-src\s*=\s*["\']([^"\']+)["\']', img_attrs, re.I))
        title_m = re.search(r'\btitle\s*=\s*["\']([^"\']*)["\']', img_attrs, re.I)

        # 番号/日期：优先读 <date> 标签，其次退回整段文本
        date_tag_texts = [html.unescape(re.sub(r'<[^>]+>', ' ', d)) for d in
                          re.findall(r'<date\b[^>]*>(.*?)</date>', inner, re.S | re.I)]
        plain = html.unescape(re.sub(r'<[^>]+>', ' ', inner))

        code = ''
        for text in date_tag_texts:
            candidate = LIST_DATE_RE.sub('', text).strip()
            if _looks_like_code(candidate):
                code = candidate
                break
        if not code:
            m = LIST_CODE_RE.search(LIST_DATE_RE.sub(' ', plain))
            code = m.group(1) if m else LIST_DATE_RE.sub('', plain).strip()

        date = ''
        for text in date_tag_texts:
            m = LIST_DATE_RE.search(text)
            if m:
                date = m.group(1)
                break
        if not date:
            m = LIST_DATE_RE.search(plain)
            date = m.group(1) if m else ''

        # 视频标志：div.item-tag 里的按钮文本（高清 / 字幕）
        flag = ''
        tag_m = re.search(r'<div\b[^>]*\bclass=["\'][^"\']*\bitem-tag\b[^"\']*["\'][^>]*>(.*?)</div>',
                          inner, re.S | re.I)
        if tag_m:
            flag = _join_flags([html.unescape(re.sub(r'<[^>]+>', ' ', tag_m.group(1)))])

        items.append({
            'thumb': urllib.parse.urljoin(base_url, src_m.group(1)) if src_m else '',
            'title': (html.unescape(title_m.group(1)).strip() if title_m else '') or code,
            'code': code,
            'date': date,
            'flag': flag,
            'url': urllib.parse.urljoin(base_url, href_m.group(1)) if href_m else '',
        })
    return items


def parse_list_items(html: str, base_url: str, cfg: Dict) -> List[Dict]:
    """按配置从列表页 HTML 中解析出全部视频条目。

    :param html: 页面源码
    :param base_url: 页面地址（用于把相对地址补全为绝对地址）
    :param cfg: 解析配置（container/item/thumb/title/code/date/flag/link）
    :return: 记录列表 [{thumb,title,code,date,flag,url}, ...]
    """
    items: List[Dict] = []
    if not html:
        return items

    if not BS4_AVAILABLE or BeautifulSoup is None:
        items = _parse_list_items_regex(html, base_url, cfg)
    else:
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception:
            return _parse_list_items_regex(html, base_url, cfg)

        scope = soup
        container_sel = (cfg.get('container') or DEFAULT_LIST_CONTAINER_SELECTOR).strip()
        if container_sel:
            try:
                node = soup.select_one(container_sel)
            except Exception:
                node = None
            if node is not None:
                scope = node

        item_sel = (cfg.get('item') or DEFAULT_LIST_ITEM_SELECTOR).strip()
        # 依次尝试：配置的条目选择器 → 容器内 → 整页 → 常见兜底选择器
        candidates = [item_sel, 'div.movie-box, a.movie-box', 'a.movie-box',
                      'div.movie-box', 'div.item', '#waterfall a']
        nodes = []
        for candidate in candidates:
            if not candidate:
                continue
            for target in ((scope, soup) if scope is not soup else (scope,)):
                try:
                    found = target.select(candidate)
                except Exception:
                    found = []
                if found:
                    nodes = found
                    break
            if nodes:
                break

        for node in nodes:
            items.append(_build_list_item(node, base_url, cfg))

    # 按链接去重（保持原顺序）
    seen: Set[str] = set()
    unique: List[Dict] = []
    for item in items:
        key = item.get('url') or (item.get('thumb') + '|' + item.get('title', ''))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_alert_urls(html: str, base_url: str, selector: str) -> List[str]:
    """解析公告层(div.alert alert-info ...)中的网址，用于自动补充“网址库”"""
    urls: List[str] = []
    if not html or not BS4_AVAILABLE or BeautifulSoup is None:
        return urls
    try:
        soup = BeautifulSoup(html, 'html.parser')
        nodes = soup.select(selector or DEFAULT_LIST_ALERT_SELECTOR)
    except Exception:
        return urls

    for node in nodes:
        try:
            for a in node.find_all('a', href=True):
                href = str(a.get('href') or '').strip()
                if href and not href.lower().startswith(('javascript:', '#')):
                    urls.append(urllib.parse.urljoin(base_url, href))
            # 直接以文本形式写明地址的情况
            text = node.get_text(' ', strip=True)
            for m in re.finditer(r'https?://[^\s"\'<>()，,、]+', text):
                urls.append(m.group(0))
        except Exception:
            continue

    seen: Set[str] = set()
    result: List[str] = []
    for url in urls:
        url = url.rstrip('.,;、，')
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _js_function_body(html: str, func: str) -> str:
    """按大括号配对取出指定 JS 函数的函数体（正则遇到嵌套 {} 会提前截断）"""
    m = re.search(r'function\s+' + re.escape(func) + r'\s*\([^)]*\)', html or '', re.I)
    if not m:
        return ''
    start = html.find('{', m.end())
    if start < 0:
        return ''
    depth = 0
    for index in range(start, len(html)):
        char = html[index]
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return html[start + 1:index]
    return html[start + 1:]


def parse_search_action(html: str) -> Dict[str, str]:
    """识别页面搜索框的逻辑：输入框 id、按钮 onclick、以及跳转到搜索结果页的路径

    目标结构形如：
        <div class="navbar-form navbar-left fullsearch-form">
          <input id="search-input" type="text" ...>
          <button class="btn btn-default" type="submit" onclick="searchs('search-input')">
    脚本里通常有：function searchs(id){ ... location.href='/search/'+word ... }
    """
    info: Dict[str, str] = {}
    if not html or not BS4_AVAILABLE or BeautifulSoup is None:
        return info

    try:
        soup = BeautifulSoup(html, 'html.parser')
    except Exception:
        return info

    scope = soup
    for selector in ('div.navbar-form', 'form.navbar-form', 'form[role="search"]', 'form'):
        try:
            node = soup.select_one(selector)
        except Exception:
            node = None
        if node is not None:
            scope = node
            break

    try:
        inp = scope.select_one('input#search-input, input.form-control, input[type="text"]')
    except Exception:
        inp = None
    if inp is not None:
        info['input_id'] = str(inp.get('id') or '')
        info['placeholder'] = str(inp.get('placeholder') or '')

    button = None
    for selector in ('button.btn-default', 'button[type="submit"]', 'button'):
        try:
            button = scope.select_one(selector)
        except Exception:
            button = None
        if button is not None:
            break
    if button is not None:
        onclick = str(button.get('onclick') or '')
        info['onclick'] = onclick
        m = re.search(r'(\w+)\s*\(', onclick)
        if m:
            info['js_func'] = m.group(1)

    # 在页面脚本里查找该 JS 函数的跳转地址模板
    func = info.get('js_func')
    if func:
        body = _js_function_body(html or '', func)
        if body:
            info['func_body'] = body[:200]
            # 优先取 location.href = '/xxx/' 形式的跳转地址
            hm = re.search(r'''location\.href\s*=\s*['"]([^'"]*)['"]''', body)
            if hm:
                info['path'] = hm.group(1)
            else:
                for sm in re.finditer(r'''['"](/[^'"]*)['"]''', body):
                    path = sm.group(1)
                    if '{' in path or '?' in path:
                        continue
                    info['path'] = path
                    break

    return info


def build_search_url(base_url: str, keyword: str, path: str = '',
                     fallback_path: str = DEFAULT_LIST_SEARCH_PATH) -> str:
    """按识别到的搜索路径（或配置模板）生成搜索结果页地址"""
    keyword = (keyword or '').strip()
    if not keyword:
        return ''
    template = (path or '').strip() or (fallback_path or DEFAULT_LIST_SEARCH_PATH)
    if '{keyword}' in template:
        target = template.replace('{keyword}', urllib.parse.quote(keyword, safe=''))
    else:
        # 自动识别出来的通常只是前缀（如 “/search/”），关键词拼在其后
        target = template + urllib.parse.quote(keyword, safe='')
    return urllib.parse.urljoin(base_url or '', target)


def parse_next_page_url(html: str, base_url: str, selector: str) -> Optional[str]:
    """解析“下一页”链接（优先用页面上的 a#next，其次退回页码模板）"""
    if not html or not BS4_AVAILABLE or BeautifulSoup is None:
        return None
    try:
        soup = BeautifulSoup(html, 'html.parser')
        node = soup.select_one(selector or DEFAULT_LIST_NEXT_SELECTOR)
    except Exception:
        return None
    if node is None:
        return None
    href = str(node.get('href') or '').strip()
    if not href or href.lower().startswith(('javascript:', '#')):
        return None
    return urllib.parse.urljoin(base_url, href)


def parse_pagination(html: str, base_url: str,
                     selector: str = DEFAULT_PAGINATION_SELECTOR) -> Dict[str, Any]:
    """解析列表页下方的分页控件，得到“当前第几页 / 共几页 / 页码区间”。

    目标结构形如（当前第 1 页，共 2 页）：
        <ul class="pagination pagination-lg">
          <li class="active"><a href="/star/137l/1">1</a></li>
          <li><a href="/star/137l/2">2</a></li>
          <li><a id="next" href="/star/137l/2">下一頁</a></li>
        </ul>

    :param html:     页面源码
    :param base_url: 页面地址（当前页取不到时，用地址末尾数字兜底）
    :param selector: 分页容器选择器
    :return: dict
        current: 当前页码（int，未知为 None）
        pages:   分页控件中列出的页码列表（升序）
        min/max: 页码区间（有页码列表时有效）
        total:   总页数（int，仅当“下一页”未超出列出的最大页码时可确定；
                 否则为 None，表示总页数未显示，只能给出页码区间）
    """
    result: Dict[str, Any] = {'current': None, 'pages': [], 'min': None,
                              'max': None, 'total': None}
    if not html or not BS4_AVAILABLE or BeautifulSoup is None:
        return result
    try:
        soup = BeautifulSoup(html, 'html.parser')
    except Exception:
        return result

    def _first_int(text: str):
        """取文本里第一个整数（取不到返回 None）"""
        m = re.search(r'(\d+)', str(text or ''))
        return int(m.group(1)) if m else None

    ul = None
    try:
        ul = soup.select_one(selector or DEFAULT_PAGINATION_SELECTOR)
    except Exception:
        ul = None
    if ul is None:
        # 兜底：任意含 pagination 类名的 ul
        ul = soup.find('ul', class_=lambda c: c and 'pagination' in str(c))

    pages: List[int] = []
    current = None
    next_page = None
    if ul is not None:
        for li in ul.find_all('li'):
            classes = ' '.join(li.get('class') or [])
            for a in li.find_all('a'):
                href = str(a.get('href') or '').strip()
                text = a.get_text(strip=True)
                a_classes = ' '.join(a.get('class') or [])
                # “下一页/上一页”等文字链接：只取地址里的页码，不计入页码列表
                num = _first_int(text)
                href_num = _first_int(
                    os.path.basename(urllib.parse.urlsplit(href).path.rstrip('/')))
                if num is None:
                    if 'next' in (str(a.get('id') or '').lower() + a_classes.lower()):
                        next_page = href_num
                    continue
                if num not in pages:
                    pages.append(num)
                if 'active' in classes or 'active' in a_classes:
                    current = num

    # 当前页兜底：地址末尾的页码数字（如 /star/137l/3）
    if current is None:
        try:
            path = urllib.parse.urlsplit(base_url or '').path.rstrip('/')
            current = _first_int(os.path.basename(path))
        except Exception:
            current = None

    result['pages'] = sorted(pages)
    result['current'] = current
    if pages:
        result['min'] = min(pages)
        result['max'] = max(pages)
        # “下一页”指向超出已列出页码时，说明页面未显示总页数，只能给区间
        if next_page is not None and next_page > max(pages):
            result['total'] = None
        else:
            result['total'] = max(pages)
    return result


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
        self._dirty_sections: Set[str] = set()
        self._load_config()

    def _get_default_config_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE_NAME)

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
        单例，却共用同一个 app_config.json；直接写回整份内存副本会互相覆盖。
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
        old_value = self._config[section].get(key)
        self._config[section][key] = value
        self._dirty_sections.add(section)
        if old_value != value:
            self._trigger_callbacks(section, key, value)
        if auto_save:
            self.save_config()

    def get_section(self, section: str, default: Optional[Dict] = None) -> Dict:
        """读取分区时以磁盘最新内容为准，避免读到旧副本"""
        disk = self._read_disk()
        if section in disk:
            self._config[section] = disk[section]
            return disk[section]
        return self._config.get(section, default or {})

    def set_section(self, section: str, data: Dict, auto_save: bool = True):
        self._config = self._read_disk()
        self._config[section] = data
        self._dirty_sections = {section}
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
    browser_mode: str = "chrome"  # chrome, firefox, edge, safari（仅影响请求头指纹）
    connection_mode: str = "http"  # http, https
    max_images: int = 10
    auto_download: bool = False
    save_path: str = ""
    custom_headers: Dict[str, str] = field(default_factory=dict)
    cookies: str = ""

    # 以下字段与参考脚本 crawler_core.py 的 CrawlerConfig 对齐
    retry_times: int = 3            # 失败重试次数
    random_ua: bool = True          # 每次请求随机 User-Agent
    interval_min: int = 0           # 请求前随机延迟下限(毫秒)，0 表示不延迟
    interval_max: int = 0           # 请求前随机延迟上限(毫秒)
    encoding: str = "auto"          # auto 或指定编码
    main_image_selector: str = DEFAULT_MAIN_IMAGE_SELECTOR   # 主图 CSS 选择器（内容A内查找，只取1张）
    sample_image_selector: str = DEFAULT_SAMPLE_CONTAINER_SELECTOR   # 剪辑图容器（内容A内查找，数量不限）
    sample_link_selector: str = DEFAULT_SAMPLE_LINK_SELECTOR   # 剪辑图大图链接（href 才是大图）
    magnet_table_selector: str = DEFAULT_MAGNET_TABLE_SELECTOR   # 磁力表（内容A内查找，JS异步生成）
    auto_render_on_missing: bool = True   # 静态源码未取到磁力表时自动改用浏览器渲染重试
    field_rules: List[Dict] = field(      # 字段抓取规则（标题/演员/番号/日期/时长）
        default_factory=lambda: [dict(rule) for rule in DEFAULT_FIELD_RULES])
    auto_save_json: bool = False          # 抓取完成后是否自动把页面数据保存为 JSON
    magnet_ajax_enabled: bool = True      # 磁力表为空时是否请求异步接口
    magnet_ajax_path: str = DEFAULT_MAGNET_AJAX_PATH       # 异步接口路径
    magnet_ajax_params: str = DEFAULT_MAGNET_AJAX_PARAMS   # 异步接口参数模板
    render_js: bool = False         # 是否用真实浏览器渲染（读取 JS 生成的内容）
    render_engine: str = "playwright"   # playwright / selenium
    browser_type: str = "chromium"  # chromium / chrome
    headless: bool = True           # 无头模式
    browser_timeout: int = 30       # 浏览器渲染超时（秒）
    wait_for_selector: str = ""     # 渲染后等待出现的 CSS 选择器
    block_images: bool = True       # 渲染时拦截图片，加快加载

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'WebGrabConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _cuda_dll_dirs() -> List[str]:
    """收集所有可能包含 CUDA 运行库 DLL 的目录（Windows）"""
    dirs: List[str] = []

    def add(path: str):
        if path and os.path.isdir(path) and path not in dirs:
            dirs.append(path)

    # 1. 环境变量指向的 CUDA Toolkit
    for env_name in ('CUDA_PATH', 'CUDA_HOME', 'CUDA_ROOT'):
        base = os.environ.get(env_name)
        if base:
            add(os.path.join(base, 'bin'))
    # 2. 默认安装位置的 CUDA Toolkit（扫描所有版本）
    if sys.platform == 'win32':
        for base in glob.glob(r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*'):
            add(os.path.join(base, 'bin'))
    # 3. PyTorch 自带的 DLL 目录
    try:
        import torch  # type: ignore
        add(os.path.join(os.path.dirname(torch.__file__), 'lib'))
    except Exception:
        pass
    # 4. pip 安装的 nvidia-* 包自带的 DLL 目录
    try:
        import nvidia  # type: ignore
        nvidia_dir = os.path.dirname(nvidia.__file__)
        for sub in ('cublas', 'cudnn', 'cuda_runtime', 'cufft', 'curand',
                    'cusolver', 'cusparse', 'nvjitlink'):
            add(os.path.join(nvidia_dir, sub, 'bin'))
    except Exception:
        pass
    # 5. llama-cpp-python 自带的 CUDA 运行库
    try:
        import llama_cpp  # type: ignore
        add(os.path.join(os.path.dirname(llama_cpp.__file__), 'libs'))
    except Exception:
        pass
    return dirs


def ensure_cuda_dll_path():
    """把 CUDA 运行库目录注册进 DLL 搜索路径

    Windows 下 ctranslate2 与 llama.dll 都需要 cublas/cudnn 等运行库，
    必须在 import ctranslate2 / llama_cpp 之前调用，否则 GPU 模式会失败。
    """
    if sys.platform != 'win32':
        return
    if getattr(ensure_cuda_dll_path, '_done', False):
        return
    for directory in _cuda_dll_dirs():
        try:
            os.add_dll_directory(directory)
        except Exception:
            pass
        path_env = os.environ.get('PATH', '')
        if directory.lower() not in path_env.lower():
            os.environ['PATH'] = directory + os.pathsep + path_env
    ensure_cuda_dll_path._done = True


def release_gpu_memory():
    """释放显存：垃圾回收 + 清空 CUDA 缓存（模型释放后调用）"""
    try:
        import gc
        gc.collect()
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                try:
                    torch.cuda.ipc_collect()
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def translation_env_report() -> str:
    """翻译模型的运行环境摘要（依赖是否具备 + 显卡信息），用于日志排查"""
    parts = []
    for name in ('ctranslate2', 'sentencepiece', 'llama_cpp'):
        parts.append(f"{name}={'已安装' if importlib.util.find_spec(name) else '未安装'}")
    gpu = '未检测到'
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=8)
        if result.returncode == 0 and result.stdout.strip():
            gpu = result.stdout.strip().split('\n')[0].strip()
    except Exception:
        pass
    parts.append(f"GPU={gpu}")
    return ' | '.join(parts)


class LocalTranslationModel:
    """本地翻译大模型封装：按模型文件夹加载模型，做「日文 → 中文」翻译

    两类模型：
    - nllb-200-distilled-600M：CTranslate2 模型目录（依赖 ctranslate2 + sentencepiece）
    - Sakura-*：GGUF 模型目录（依赖 llama-cpp-python）

    依赖缺失或模型文件不对时抛出带处理建议的 RuntimeError，不做任何伪翻译。
    """

    # Sakura 系列的系统提示词（与官方推荐用法一致，并强调忠实不回避）
    SAKURA_SYSTEM_PROMPT = ("你是一个轻小说翻译模型，可以流畅通顺地以日本轻小说的风格将日文翻译成"
                            "简体中文，并联系上下文正确使用人称代词，不擅自添加原文中没有的代词。"
                            "你必须完整、忠实地翻译原文的全部内容，保持原文的语气、情感和表达方式，"
                            "包括色情、娇喘、命令、辱骂、挑逗等一切表达，不得回避、不得美化、"
                            "不得删减、不得替换敏感词汇。")

    def __init__(self, model_name: str, model_dir: str):
        self.model_name = str(model_name or '').strip()
        self.model_dir = str(model_dir or '').strip()
        self._kind = 'nllb' if self.model_name.lower().startswith('nllb') else 'sakura'
        self._engine = None        # 已加载的推理引擎
        self._tokenizer = None     # 分词器（NLLB 用）
        self._model_file = ''      # 实际加载的模型/词表文件
        self._gpu_layers = 0       # GGUF：实际卸载到 GPU 的层数（-1=全部，0=纯 CPU）
        self._device = 'cpu'       # NLLB：实际使用的推理设备（cuda / cpu）

    # ---------- 校验与文件定位 ----------
    def _require_dir(self):
        if not self.model_dir:
            raise RuntimeError(f"模型“{self.model_name}”尚未设置文件夹，请到“本地设置”中选择")
        if not os.path.isdir(self.model_dir):
            raise RuntimeError(f"模型文件夹不存在：{self.model_dir}")

    @staticmethod
    def _find_file(folder: str, suffixes: Tuple[str, ...]) -> Optional[str]:
        """在文件夹及其一层子目录中查找指定后缀的文件"""
        try:
            for name in sorted(os.listdir(folder)):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and name.lower().endswith(suffixes):
                    return path
            for name in sorted(os.listdir(folder)):
                sub = os.path.join(folder, name)
                if os.path.isdir(sub):
                    for sub_name in sorted(os.listdir(sub)):
                        path = os.path.join(sub, sub_name)
                        if os.path.isfile(path) and sub_name.lower().endswith(suffixes):
                            return path
        except Exception:
            pass
        return None

    # ---------- 加载 ----------
    def load(self):
        """加载模型（幂等，失败时抛 RuntimeError）"""
        if self._engine is not None:
            return
        self._require_dir()
        if self._kind == 'nllb':
            self._load_nllb()
        else:
            self._load_sakura()

    def is_loaded(self) -> bool:
        """模型是否已载入内存/显存"""
        return self._engine is not None

    def unload(self):
        """释放模型占用的内存/显存（窗口打开时默认不加载，用完可手动释放）"""
        engine = self._engine
        self._engine = None
        self._tokenizer = None
        try:
            # llama-cpp-python 的 Llama 提供 close() 释放资源
            close = getattr(engine, 'close', None)
            if callable(close):
                close()
        except Exception:
            pass
        del engine
        # 回收内存并清空 CUDA 缓存（含 IPC 共享显存），避免释放后仍占用显存
        release_gpu_memory()

    def _load_nllb(self):
        # CUDA 运行库必须先在 import 前注册，否则 ctranslate2 找不到 cublas/cudnn
        ensure_cuda_dll_path()
        try:
            import ctranslate2  # type: ignore
        except ImportError:
            raise RuntimeError('缺少 ctranslate2，无法加载 NLLB 模型，请执行：pip install ctranslate2')
        try:
            import sentencepiece  # type: ignore
        except ImportError:
            raise RuntimeError('缺少 sentencepiece，无法加载 NLLB 模型，请执行：pip install sentencepiece')
        spm = self._find_file(self.model_dir, ('.model', '.spm'))
        if not spm:
            raise RuntimeError(f"模型文件夹内未找到词表文件（*.model / *.spm）：{self.model_dir}")
        self._tokenizer = sentencepiece.SentencePieceProcessor(model_file=spm)
        # 有可用 CUDA 设备就走 GPU，失败自动回退 CPU
        candidates = ['cpu']
        try:
            if ctranslate2.get_cuda_device_count() > 0:
                candidates.insert(0, 'cuda')
        except Exception:
            pass
        last_error = None
        for device in candidates:
            try:
                self._engine = ctranslate2.Translator(self.model_dir, device=device)
                self._device = device
                break
            except Exception as e:
                last_error = e
        if self._engine is None:
            raise RuntimeError(f"NLLB 模型加载失败（已尝试 {('/'.join(candidates))}）：{last_error}")
        self._model_file = spm

    def _load_sakura(self):
        # llama.dll 依赖 CUDA 运行库，必须在 import 前注册 DLL 路径
        ensure_cuda_dll_path()
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError:
            # 该包对 Python 版本敏感（官方无 cp314 预编译包），提示里带上当前解释器便于排查
            raise RuntimeError(
                '缺少 llama-cpp-python，无法加载 Sakura GGUF 模型。'
                f'当前解释器：Python {sys.version.split()[0]}（{sys.executable}）；'
                '请改用已安装该包的解释器启动本程序，或执行：pip install llama-cpp-python')
        gguf = self._find_file(self.model_dir, ('.gguf',))
        if not gguf:
            raise RuntimeError(f"模型文件夹内未找到 GGUF 模型文件（*.gguf）：{self.model_dir}")
        # 依次尝试：全部层上 GPU -> 部分层上 GPU -> 纯 CPU
        last_error = None
        for gpu_layers in (-1, 20, 0):
            try:
                self._engine = Llama(model_path=gguf, n_ctx=2048,
                                     n_gpu_layers=gpu_layers, verbose=False)
                self._gpu_layers = gpu_layers
                break
            except Exception as e:
                last_error = e
        if self._engine is None:
            raise RuntimeError(f"加载 GGUF 模型失败（GPU/CPU 均尝试过）：{last_error}")
        self._model_file = gguf

    # ---------- 翻译 ----------
    def translate(self, text: str) -> str:
        """把一段日文翻译为中文；空文本原样返回"""
        text = str(text or '').strip()
        if not text:
            return ''
        self.load()
        out = self._translate_nllb(text) if self._kind == 'nllb' else self._translate_sakura(text)
        return re.sub(r'\s+', ' ', out).strip()

    def _translate_nllb(self, text: str) -> str:
        tokens = list(self._tokenizer.encode(text, out_type=str))
        # NLLB 需要在源句末尾追加结束符与源语言标记
        tokens += ['</s>', NLLB_SOURCE_LANG]
        results = self._engine.translate_batch([tokens], target_prefix=[[NLLB_TARGET_LANG]])
        if not results or not results[0].hypotheses:
            return ''
        pieces = results[0].hypotheses[0]
        # 逐个 subword 拼接，再把 sentencepiece 的下划线还原成空格
        return ''.join(pieces).replace('\u2581', ' ').strip()

    def _translate_sakura(self, text: str) -> str:
        prompt = ("将下面的日文文本完整忠实地翻译成中文，保持原文语气，"
                  "不要回避或美化任何内容：" + text)
        max_tokens = max(64, min(1024, len(text) * 3 + 64))
        try:
            result = self._engine.create_chat_completion(
                messages=[
                    {'role': 'system', 'content': self.SAKURA_SYSTEM_PROMPT},
                    {'role': 'user', 'content': prompt},
                ],
                temperature=0.1, top_p=0.9, max_tokens=max_tokens)
            out = (result['choices'][0]['message']['content'] or '')
        except Exception:
            # 部分 GGUF 不带 chat 模板，退化为纯文本补全
            completion = self._engine.create_completion(
                prompt=f"{self.SAKURA_SYSTEM_PROMPT}\n{prompt}\n",
                temperature=0.1, max_tokens=max_tokens)
            out = (completion['choices'][0]['text'] or '')
        return out.strip().strip('"').strip('「').strip('」')

    def describe(self) -> str:
        """用于日志的模型描述"""
        state = '已加载' if self._engine is not None else '未加载'
        device = ''
        if self._engine is not None:
            if self._kind == 'sakura':
                device = f"，GPU 层数 {self._gpu_layers}" if self._gpu_layers else '，纯 CPU'
            else:
                device = f"，设备 {self._device}"
        return f"{self.model_name}（{self.model_dir or '未设置文件夹'}，{state}{device}）"


def _format_title_display(text: Any, max_len: int = 50) -> str:
    """标题展示：清洗为单行纯文本，超过 max_len 字时仅显示前 max_len 字"""
    if not isinstance(text, str):
        text = str(text or '')
    text = html.unescape(text)                # 先反转义实体（含 &#10; 等换行实体）
    text = re.sub(r'<[^>]+>', ' ', text)      # 去残留 HTML 标签
    text = text.replace('&nbsp;', ' ')
    text = re.sub(r'\s+', ' ', text).strip()  # 压缩换行/多余空白为单空格
    if max_len and len(text) > max_len:
        text = text[:max_len] + '…'
    return text


# ========== 网页抓取工作类 ==========
class WebGrabWorker:
    """网页抓取后台工作类"""

    def __init__(self, config: WebGrabConfig,
                 log_callback: Optional[Callable] = None,
                 progress_callback: Optional[Callable] = None,
                 complete_callback: Optional[Callable] = None,
                 image_found_callback: Optional[Callable] = None,
                 magnet_found_callback: Optional[Callable] = None,
                 record_callback: Optional[Callable] = None):
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.complete_callback = complete_callback
        self.image_found_callback = image_found_callback
        self.magnet_found_callback = magnet_found_callback
        self.record_callback = record_callback
        self.is_running = True
        self.html_content = ""
        self.images: List[Dict] = []
        self.magnets: List[Dict] = []

        # 抓取元信息（供“测试爬取内容”查看）：引擎/状态码/编码/大小/耗时
        self.last_meta: Dict[str, Any] = {}

        # 结构化字段抓取结果（标题/演员/番号/日期/时长等）
        self.fields: Dict[str, Any] = {}
        # 字段对应的超链接（系列/演员/发行商/制作商），用于点击后在列表页层打开
        self.field_links: Dict[str, Any] = {}
        # 类别（“類別”）：[{'name','url','code'}]，code 即网址末段的标签代码（如 f / gt）
        self.categories: List[Dict[str, str]] = []

        # 内容A：body > div.container 的代码内容（后续所有提取都在这段代码内进行）
        self.content_a: str = ""
        # 当前页面地址（下载图片时用作 Referer）
        self.base_url: str = ""

        # 与参考脚本一致：放宽 SSL 校验，避免证书问题导致抓取失败
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def stop(self):
        self.is_running = False

    def log(self, message: str):
        if self.log_callback and self.is_running:
            self.log_callback(message)

    def update_progress(self, progress: int, message: str = ""):
        if self.progress_callback and self.is_running:
            self.progress_callback(progress, message)

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头（与参考脚本一致，支持随机 User-Agent）"""
        user_agent = random.choice(USER_AGENTS) if self.config.random_ua else self.config.user_agent

        headers = {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            # 只声明标准库能解压的方式，避免拿到无法解码的 br 内容
            'Accept-Encoding': 'gzip, deflate',
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
        """构建URL打开器（代理 + 忽略证书校验的SSL上下文）"""
        handlers = []

        # 代理设置
        if self.config.proxy_mode == "custom" and self.config.proxy_url:
            handlers.append(urllib.request.ProxyHandler({
                'http': self.config.proxy_url,
                'https': self.config.proxy_url,
            }))

        handlers.append(urllib.request.HTTPSHandler(context=self.ssl_context))
        return urllib.request.build_opener(*handlers)

    def _random_delay(self):
        """请求前的随机延迟（对应参考脚本的 interval_min / interval_max）"""
        lo = max(0, int(self.config.interval_min or 0))
        hi = max(lo, int(self.config.interval_max or 0))
        if hi > 0:
            time.sleep(random.randint(lo, hi) / 1000.0)

    def _decompress_response(self, data: bytes, headers) -> bytes:
        """解压 gzip / deflate 响应（与参考脚本一致）"""
        try:
            content_encoding = (headers.get('Content-Encoding') or '').lower()
        except Exception:
            content_encoding = ''

        if content_encoding == 'gzip':
            try:
                return gzip.decompress(data)
            except Exception as e:
                self.log(f"Gzip解压失败: {e}")
        elif content_encoding == 'deflate':
            try:
                return zlib.decompress(data, -zlib.MAX_WBITS)
            except Exception:
                try:
                    return zlib.decompress(data)
                except Exception as e:
                    self.log(f"Deflate解压失败: {e}")

        # 兜底：按 gzip 魔数判断（部分站点不返回 Content-Encoding）
        if len(data) > 3 and data[:3] == b'\x1f\x8b\x08':
            try:
                return gzip.decompress(data)
            except Exception as e:
                self.log(f"按魔数解压失败: {e}")

        return data

    def _detect_encoding(self, data: bytes, headers) -> str:
        """智能检测网页编码（与参考脚本一致：响应头 -> meta -> BOM -> chardet）"""
        if self.config.encoding and str(self.config.encoding).lower() != 'auto':
            return self.config.encoding

        try:
            content_type = headers.get('Content-Type', '') or ''
        except Exception:
            content_type = ''

        if content_type:
            m = re.search(r'charset=["\']?([A-Za-z0-9-]+)["\']?', content_type, re.I)
            if m:
                return m.group(1)

        if data:
            for trial in ('utf-8', 'gb18030', 'gbk', 'big5', 'latin-1'):
                try:
                    sample = data[:8000].decode(trial, errors='ignore')
                except Exception:
                    continue
                for pattern in (
                    r'<meta[^>]+charset=["\']?([A-Za-z0-9-]+)["\'\s/>]',
                    r'<meta[^>]+http-equiv=["\']?content-type["\']?[^>]+content=["\'][^"\']*charset=([A-Za-z0-9-]+)',
                ):
                    m = re.search(pattern, sample, re.I)
                    if m:
                        return m.group(1)

        if data.startswith(b'\xef\xbb\xbf'):
            return 'utf-8'

        if CHARDET_AVAILABLE and data:
            try:
                detected = chardet.detect(data)
                if detected and detected.get('encoding') and detected.get('confidence', 0) > 0.5:
                    return detected['encoding']
            except Exception:
                pass

        return 'utf-8'

    def _render_with_playwright(self, url: str) -> Optional[str]:
        """用 Playwright 渲染页面（可拿到 JS 生成的内容）"""
        try:
            self.log(f"Playwright 渲染: {url}")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.config.headless)
                context = browser.new_context(
                    user_agent=self._build_headers()['User-Agent'],
                    viewport={'width': 1920, 'height': 1080},
                )
                if self.config.block_images:
                    context.route("**/*.{png,jpg,jpeg,gif,svg,webp}", lambda route: route.abort())

                page = context.new_page()
                page.goto(url, wait_until='networkidle',
                          timeout=max(5, self.config.browser_timeout) * 1000)

                selector = (self.config.wait_for_selector or '').strip()
                if selector:
                    try:
                        page.wait_for_selector(selector, timeout=10000)
                    except Exception:
                        self.log(f"等待选择器超时: {selector}")

                html = page.content()
                browser.close()

            self.last_meta['engine'] = 'Playwright(渲染后)'
            self.log(f"Playwright 渲染完成，大小: {len(html)} 字符")
            return html
        except Exception as e:
            self.log(f"Playwright 渲染失败: {e}")
            return None

    def _render_with_selenium(self, url: str) -> Optional[str]:
        """用 Selenium 渲染页面（Playwright 不可用时的备选）"""
        driver = None
        try:
            self.log(f"Selenium 渲染: {url}")
            options = ChromeOptions()
            if self.config.headless:
                options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')
            options.add_argument(f'user-agent={self._build_headers()["User-Agent"]}')

            driver = webdriver.Chrome(options=options)
            driver.get(url)
            WebDriverWait(driver, max(5, self.config.browser_timeout)).until(
                lambda d: d.execute_script('return document.readyState') == 'complete')

            selector = (self.config.wait_for_selector or '').strip()
            if selector:
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                except Exception:
                    self.log(f"等待选择器超时: {selector}")

            time.sleep(2)
            html = driver.page_source
            self.last_meta['engine'] = 'Selenium(渲染后)'
            self.log(f"Selenium 渲染完成，大小: {len(html)} 字符")
            return html
        except Exception as e:
            self.log(f"Selenium 渲染失败: {e}")
            return None
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _fetch_rendered(self, url: str) -> Optional[str]:
        """按配置的渲染引擎获取渲染后的页面源码"""
        engine = (self.config.render_engine or 'playwright').lower()

        if engine == 'playwright' and PLAYWRIGHT_AVAILABLE:
            html = self._render_with_playwright(url)
            if html:
                return html

        if SELENIUM_AVAILABLE:
            html = self._render_with_selenium(url)
            if html:
                return html

        if not PLAYWRIGHT_AVAILABLE and not SELENIUM_AVAILABLE:
            self.log("未安装 playwright / selenium，无法渲染 JS，回退为静态抓取")
        return None

    def _fetch_static(self, url: str) -> Optional[str]:
        """静态抓取（带重试、随机延迟、解压与编码检测）"""
        retry_times = max(1, int(self.config.retry_times or 1))
        last_error = ""

        for attempt in range(retry_times):
            if not self.is_running:
                return None

            if attempt > 0:
                wait_sec = min(2 ** attempt, 10)
                self.log(f"第 {attempt + 1}/{retry_times} 次重试（等待 {wait_sec}s）...")
                time.sleep(wait_sec)

            try:
                self.log(f"正在请求: {url}")
                self.update_progress(10, "正在连接...")
                self._random_delay()

                headers = self._build_headers()
                req = urllib.request.Request(url, headers=headers)
                opener = self._build_opener()
                start_time = time.time()

                with opener.open(req, timeout=self.config.timeout) as response:
                    self.update_progress(30, "正在下载...")

                    if response.status != 200:
                        last_error = f"状态码 {response.status}"
                        self.log(f"请求失败，{last_error}")
                        continue

                    raw = response.read()
                    data = self._decompress_response(raw, response.headers)
                    charset = self._detect_encoding(data, response.headers)
                    html = data.decode(charset, errors='replace')

                    self.last_meta.update({
                        'url': url,
                        'engine': '静态(urllib)',
                        'status': response.status,
                        'encoding': charset,
                        'bytes': len(raw),
                        'chars': len(html),
                        'elapsed': round(time.time() - start_time, 2),
                        'fetch_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                    })

                    self.update_progress(50, "下载完成")
                    self.log(f"网页下载成功: {len(html)} 字符（编码 {charset}，耗时 {self.last_meta['elapsed']}s）")
                    self.html_content = html
                    return html

            except urllib.error.HTTPError as e:
                last_error = f"HTTP错误 {e.code} - {e.reason}"
                self.log(last_error)
            except urllib.error.URLError as e:
                last_error = f"URL错误 {e.reason}"
                self.log(last_error)
            except Exception as e:
                last_error = f"请求异常 {e}"
                self.log(last_error)

        self.log(f"抓取失败（已重试 {retry_times} 次）: {last_error}")
        self.last_meta['error'] = last_error
        return None

    def fetch_page(self, url: str, use_render: Optional[bool] = None) -> Optional[str]:
        """获取网页内容；use_render 为 None 时按配置的 render_js 决定"""
        render = self.config.render_js if use_render is None else use_render

        if render:
            start_time = time.time()
            html = self._fetch_rendered(url)
            if html:
                self.html_content = html
                self.last_meta.update({
                    'url': url,
                    'status': 200,
                    'encoding': 'utf-8',
                    'bytes': len(html.encode('utf-8', errors='ignore')),
                    'chars': len(html),
                    'elapsed': round(time.time() - start_time, 2),
                    'fetch_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                })
                self.content_a = self._extract_content_a(html)
                return html
            self.log("浏览器渲染不可用，回退为静态抓取")

        html = self._fetch_static(url)
        if html:
            self.content_a = self._extract_content_a(html)
        return html

    def _slice_div_by_class(self, html: str, class_name: str) -> Optional[str]:
        """正则兜底：截取指定 class 的 div 成对标签内容"""
        pattern = re.compile(
            r'<div\b[^>]*\bclass=["\'][^"\']*\b' + re.escape(class_name) + r'\b[^"\']*["\'][^>]*>',
            re.I)
        match = pattern.search(html)
        if not match:
            return None

        start = match.start()
        depth = 0
        for tag in re.finditer(r'<(/?)div\b[^>]*>', html[match.end():], re.I):
            if tag.group(1):
                depth -= 1
                if depth <= 0:
                    return html[start:match.end() + tag.end()]
            else:
                depth += 1
        return html[start:]

    def _extract_content_a(self, html: str) -> str:
        """提取内容A：body > div.container 的代码内容，保存到临时变量 content_a"""
        if not html:
            return ""

        if BS4_AVAILABLE:
            try:
                soup = BeautifulSoup(html, 'html.parser')
                for selector in ('body > div.container', 'div.container'):
                    node = soup.select_one(selector)
                    if node is not None:
                        content = str(node)
                        self.log(f"内容A 定位成功（CSS: {selector}），长度: {len(content)} 字符")
                        return content
            except Exception as e:
                self.log(f"内容A 解析失败: {e}")

        # 未安装 bs4 时按正则兜底
        content = self._slice_div_by_class(html, 'container')
        if content:
            self.log(f"内容A 定位成功（正则兜底），长度: {len(content)} 字符")
            return content

        self.log("未找到 body > div.container，内容A 回退为整页源码")
        return html

    def extract_images(self, html: str, base_url: str, full_html: Optional[str] = None) -> List[Dict]:
        """从内容A（body > div.container）中提取图片"""
        images: List[Dict] = []
        seen: Set[str] = set()

        def add_image(src: str, img_type: str = 'content', title: str = '',
                      source: str = '', width: Optional[int] = None,
                      height: Optional[int] = None, thumb: str = ''):
            if not src:
                return
            src = src.strip()
            if src.startswith('data:'):
                return

            # 跳过小图标、logo等
            if any(x in src.lower() for x in ('icon', 'logo', 'button', 'spacer', 'blank', '1x1')):
                return

            img_url = urllib.parse.urljoin(base_url, src)
            if img_url in seen:
                return
            seen.add(img_url)

            thumb_url = urllib.parse.urljoin(base_url, thumb.strip()) if thumb else img_url
            images.append({
                'url': img_url,          # 大图（剪辑图取自 a.sample-box 的 href）
                'thumb': thumb_url,      # 缩略图（列表展示用，加载更快）
                'type': img_type,
                'title': title or '',
                'width': width,
                'height': height,
                'source': source
            })
            # 仅对主图输出明细日志，其余图片在采集完成后统一汇总总数（减少日志数量）
            if img_type == 'main':
                self.log(f"发现主图[{source}]: {img_url}")

        if not BS4_AVAILABLE:
            self.log("未安装 beautifulsoup4，无法按 CSS 容器提取图片（请执行 pip install beautifulsoup4）")
            self.log("共发现 0 张图片")
            return images

        # 图片来源只有两处（均在内容A中，由配置指定，不再抓取其它容器的图片）：
        #   1) 主图容器：只取 1 张，排在第 1 位
        #   2) 剪辑图容器：数量不限，允许为空
        try:
            soup = BeautifulSoup(html, 'html.parser')

            entries: List[Dict] = []
            picked: Set[str] = set()

            def add_entry(src: str, thumb: str, title: str, width, height, role: str):
                if not src or src in picked:
                    return False
                picked.add(src)
                entries.append({
                    'src': src,
                    'thumb': thumb or src,
                    'title': (title or '').strip(),
                    'width': width,
                    'height': height,
                    'role': role,
                })
                return True

            def collect(selector: str, default_selector: str, role: str, limit: int = 0) -> int:
                """按选择器收集图片节点；返回已收集数量（limit>0 时最多收集 limit 张）"""
                real_selector = (selector or '').strip() or default_selector
                try:
                    nodes = soup.select(real_selector)
                    self.log(f"{role}选择器命中 {len(nodes)} 个元素: {real_selector}")
                except Exception as e:
                    self.log(f"{role}选择器无效({real_selector}): {e}")
                    return 0

                found = 0
                for node in nodes:
                    # 命中的可能是 img 本身，也可能是包着 img 的 a / div 等容器
                    targets = [node] if getattr(node, 'name', None) == 'img' else node.select('img')
                    for img in targets:
                        if limit and found >= limit:
                            return found
                        src = self._pick_img_src(img)
                        if not src:
                            continue
                        if add_entry(src, src,
                                     (img.get('title') or img.get('alt') or ''),
                                     self._to_int(img.get('width')),
                                     self._to_int(img.get('height')), role):
                            found += 1
                return found

            def collect_samples(selector: str, default_selector: str,
                                link_selector: str, role: str = '剪辑图') -> int:
                """剪辑图：a.sample-box 的 href 才是大图，img 只是缩略图"""
                real_selector = (selector or '').strip() or default_selector
                try:
                    nodes = soup.select(real_selector)
                    self.log(f"{role}选择器命中 {len(nodes)} 个元素: {real_selector}")
                except Exception as e:
                    self.log(f"{role}选择器无效({real_selector}): {e}")
                    return 0

                real_link = (link_selector or '').strip() or DEFAULT_SAMPLE_LINK_SELECTOR
                found = 0
                for node in nodes:
                    try:
                        links = node.select(real_link)
                    except Exception:
                        links = []
                    if not links and getattr(node, 'name', None) == 'a':
                        links = [node]

                    if links:
                        self.log(f"{role}链接选择器命中 {len(links)} 个: {real_link}")
                        for link in links:
                            href = (link.get('href') or '').strip()
                            img = link.select_one('img')
                            thumb = self._pick_img_src(img) if img is not None else ''
                            src = href or thumb
                            if not src:
                                continue
                            title = (link.get('title')
                                     or (img.get('title') if img is not None else '')
                                     or (img.get('alt') if img is not None else ''))
                            if add_entry(src, thumb, title,
                                         self._to_int(img.get('width')) if img is not None else None,
                                         self._to_int(img.get('height')) if img is not None else None,
                                         role):
                                found += 1
                    else:
                        # 没有链接时退化为容器内的 img
                        for img in node.select('img'):
                            src = self._pick_img_src(img)
                            if not src:
                                continue
                            if add_entry(src, src, (img.get('title') or img.get('alt') or ''),
                                         self._to_int(img.get('width')),
                                         self._to_int(img.get('height')), role):
                                found += 1
                return found

            # 1) 主图：只取 1 张
            collect(self.config.main_image_selector, DEFAULT_MAIN_IMAGE_SELECTOR, '主图', limit=1)

            # 2) 视频剪辑缩略图：以 a.sample-box 的 href 为大图，数量不限，允许为空
            collect_samples(self.config.sample_image_selector, DEFAULT_SAMPLE_CONTAINER_SELECTOR,
                            self.config.sample_link_selector)

            for entry in entries:
                width, height = entry['width'], entry['height']
                if width and height and (width < 100 or height < 100):
                    continue  # 跳过太小的图

                if entry['role'] == '主图':
                    img_type, css_source = 'main', f'css:{self.config.main_image_selector}'
                else:
                    img_type, css_source = 'sample', f'css:{self.config.sample_image_selector}'

                add_image(entry['src'], img_type, entry['title'], css_source,
                          width, height, thumb=entry['thumb'])

                if self.config.max_images > 0 and len(images) >= self.config.max_images:
                    self.log(f"已达到数量上限 {self.config.max_images} 张（设为 0 可不限）")
                    break
        except Exception as e:
            self.log(f"CSS提取图片失败: {e}")

        self.log(f"共发现 {len(images)} 张图片")
        return images

    @staticmethod
    def _pick_img_src(node) -> str:
        """从 img 标签中取出图片地址（兼容懒加载属性与 srcset）"""
        for attr in ('src', 'data-src', 'data-original', 'data-lazy-src', 'data-echo', 'data-url'):
            value = (node.get(attr) or '').strip()
            if value:
                return value
        srcset = (node.get('srcset') or '').strip()
        if srcset:
            return srcset.split(',')[0].strip().split(' ')[0]
        return ''

    @staticmethod
    def _to_int(value) -> Optional[int]:
        """把属性值转成整数，失败返回 None"""
        try:
            text = str(value or '').strip()
            return int(text) if text.isdigit() else None
        except Exception:
            return None

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

    def _parse_magnet_rows(self, rows_html: str, source: str, seen: Set[str]) -> List[Dict]:
        """解析磁力表的行数据（3列：标题 / 文件大小 / 分享日期）"""
        magnets: List[Dict] = []
        if not BS4_AVAILABLE or not rows_html:
            return magnets

        try:
            # 接口返回的是裸 <tr> 片段，补上 table 外壳保证解析一致
            markup = rows_html if '<table' in rows_html.lower() else f'<table>{rows_html}</table>'
            soup = BeautifulSoup(markup, 'html.parser')

            for row in soup.select('tr'):
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue

                title_cell = cells[0]
                link = (title_cell.select_one('a[href^="magnet:"]')
                        or row.select_one('a[href^="magnet:"]'))
                if link is None:
                    continue

                # 识别“高清”标识（<a class="btn ..." title="包含高清HD的磁力連結">高清</a>）
                hd = False
                for badge in title_cell.select('a, span, i'):
                    badge_text = badge.get_text(strip=True)
                    badge_title = badge.get('title') or ''
                    if badge_text in ('高清', 'HD') or '高清' in badge_title:
                        hd = True

                # 去掉按钮类标签（“高清”等），只保留真正的标题文字
                for badge in title_cell.select('a.btn, span.btn, i.btn'):
                    badge.decompose()

                name = title_cell.get_text(' ', strip=True)
                size = cells[1].get_text(' ', strip=True)
                date = cells[2].get_text(' ', strip=True)

                url = (link.get('href') or '').strip().replace('&amp;', '&')
                if not url or url in seen:
                    continue
                seen.add(url)
                magnets.append({
                    'url': url,
                    'name': name or '未命名',
                    'size': size or '',
                    'date': date or '',
                    'hd': bool(hd),
                    'source': source
                })
                # 不在每行都打日志：解析完全部行后由调用方汇总一条总数日志
        except Exception as e:
            self.log(f"磁力表解析失败: {e}")

        return magnets

    # ========== 结构化字段抓取（css / regex / custom 三种方式） ==========
    def _build_soup(self, html: str):
        """构建 BeautifulSoup 对象"""
        if not BS4_AVAILABLE or not html:
            return None
        try:
            return BeautifulSoup(html, 'html.parser')
        except Exception as e:
            self.log(f"BeautifulSoup 解析失败: {e}")
            return None

    def _extract_by_css_rule(self, soup, selector: str, multiple: bool):
        """css 方式：取元素文本"""
        empty: Any = [] if multiple else ''
        if soup is None or not selector:
            return empty
        try:
            elements = soup.select(selector)
        except Exception as e:
            self.log(f"CSS选择器无效({selector}): {e}")
            return empty
        if not elements:
            return empty

        if multiple:
            values: List[str] = []
            for element in elements:
                text = element.get_text(strip=True)
                if text and text not in values:
                    values.append(text)
            return values
        return elements[0].get_text(strip=True)

    def _extract_by_regex_rule(self, html: str, pattern: str, multiple: bool):
        """regex 方式：按正则取值"""
        empty: Any = [] if multiple else ''
        if not html or not pattern:
            return empty
        try:
            matches = re.findall(pattern, html, re.I | re.S)
        except Exception as e:
            self.log(f"正则无效({pattern}): {e}")
            return empty

        values: List[str] = []
        for match in matches:
            if isinstance(match, tuple):
                match = next((m for m in match if m), '')
            text = str(match).strip()
            if text and text not in values:
                values.append(text)

        if multiple:
            return values
        return values[0] if values else ''

    def _extract_by_custom_rule(self, html: str, soup, code: str):
        """custom 方式：执行 Python 代码，结果写入 result（可用 soup / html / re）"""
        if not code:
            return ''
        if soup is None:
            self.log("自定义代码需要 BeautifulSoup，请先安装 beautifulsoup4")
            return ''

        safe_builtins = {
            'len': len, 'str': str, 'int': int, 'float': float, 'bool': bool,
            'list': list, 'dict': dict, 'set': set, 'tuple': tuple, 'range': range,
            'enumerate': enumerate, 'zip': zip, 'map': map, 'filter': filter,
            'sum': sum, 'min': min, 'max': max, 'any': any, 'all': all,
            'isinstance': isinstance, 'hasattr': hasattr, 'getattr': getattr,
            'print': lambda *args: self.log(" ".join(str(a) for a in args)),
        }
        try:
            exec_globals = {'__builtins__': safe_builtins, 're': re, 'html': html, 'soup': soup}
            exec_locals: Dict[str, Any] = {}
            exec(code, exec_globals, exec_locals)
            return exec_locals.get('result', '')
        except Exception as e:
            self.log(f"自定义代码执行失败: {e}")
            return ''

    @staticmethod
    def _strip_html(text: Any) -> str:
        """去掉 HTML 标签并反转义实体，得到纯文本"""
        if not isinstance(text, str):
            return str(text)
        text = html.unescape(text)                # 先反转义实体（含 &#10; 等换行实体）
        text = re.sub(r'<[^>]+>', ' ', text)      # 去标签
        text = text.replace('&nbsp;', ' ')
        text = re.sub(r'\s+', ' ', text).strip()  # 压缩空白（含换行/制表符）
        return text

    def _clean_field_value(self, value: Any) -> Any:
        """字段值清洗：字符串去 HTML，列表逐元素清洗"""
        if isinstance(value, list):
            return [self._strip_html(v) for v in value if v is not None]
        return self._strip_html(value)

    def _extract_by_rule(self, html: str, soup, rule: Dict) -> Any:
        """按单条规则抓取"""
        rule_type = str(rule.get('rule_type') or 'css').lower()
        content = rule.get('rule_content') or ''
        multiple = bool(rule.get('multiple', False))

        if rule_type == 'css':
            return self._extract_by_css_rule(soup, content, multiple)
        if rule_type == 'regex':
            return self._extract_by_regex_rule(html, content, multiple)
        if rule_type == 'custom':
            return self._extract_by_custom_rule(html, soup, content)
        return [] if multiple else ''

    def extract_fields(self, html: str) -> Dict[str, Any]:
        """按配置的规则抓取标题/演员/番号/日期/时长等字段

        规则先经 normalize_field_rules 自愈（空内容/非法方式自动回退默认），
        避免历史配置被写空后出现“所有字段都抓不到”。
        """
        fields: Dict[str, Any] = {}
        rules = normalize_field_rules(self.config.field_rules)
        if not rules:
            return fields

        soup = self._build_soup(html)
        for rule in rules:
            if not isinstance(rule, dict) or not rule.get('enabled', True):
                continue
            field_id = str(rule.get('field_id') or '').strip()
            if not field_id:
                continue

            value = self._extract_by_rule(html, soup, rule)
            # 仅保留纯文本（去掉可能残留的 HTML 代码与实体）
            fields[field_id] = self._clean_field_value(value)

            preview = value if not isinstance(value, list) else '、'.join(str(v) for v in value)
            # 字段明细日志只显示标题，其余字段不输出（减少日志数量）
            if field_id == 'title':
                self.log(f"字段[标题] => {str(preview)[:80]}")

        return fields

    def extract_field_links(self, html: str, base_url: str,
                            fields: Dict) -> Dict[str, Any]:
        """为字段文本匹配对应的超链接（系列 / 演员 / 发行商 / 制作商）

        做法通用：在页面中查找“文本与字段值一致（或互相包含）”的 <a> 元素并取其 href，
        因此不论该字段用的是 css / regex / custom 哪种抓取方式都能取到链接。
        返回 {字段: [{'name': 文本, 'url': 链接}, ...]}
        """
        links: Dict[str, Any] = {}
        if not html or not fields:
            return links

        soup = self._build_soup(html)
        if soup is None:
            return links

        anchors: List[Tuple[str, str]] = []
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            href = str(a.get('href') or '').strip()
            if not text or not href:
                continue
            if href.lower().startswith(('javascript:', '#')):
                continue
            anchors.append((text, urllib.parse.urljoin(base_url or '', href)))

        def find_url(name: str) -> str:
            name = (name or '').strip()
            if not name:
                return ''
            for text, url in anchors:          # 完全相等优先
                if text == name:
                    return url
            for text, url in anchors:          # 其次互相包含
                if name in text or text in name:
                    return url
            return ''

        for field_id in DETAIL_LINK_FIELDS:
            value = fields.get(field_id)
            if value is None:
                continue
            names = value if isinstance(value, list) else [value]
            found: List[Dict[str, str]] = []
            for name in names:
                name = str(name or '').strip()
                if not name:
                    continue
                found.append({'name': name, 'url': find_url(name)})
            if found:
                links[field_id] = found

        return links

    @staticmethod
    def _genre_block(soup):
        """定位“類別:”标题后面装着类别链接的那个代码块

        页面结构：<p class="header">類別:<span id="genre-toggle"></span></p>
                 紧跟其后的 <p> 里逐个 <span class="genre"><label>
                 <input type="checkbox" name="gr_sel" value="f">
                 <a href=".../genre/f">單體作品</a></label></span>

        注意：导航栏里也有“類別”菜单项（<li> 文本为“類別 有碼類別 無碼類別”），
        只看“文本含類別且较短”会先命中它，因此按下面顺序精确定位：
          ① 站内“單選/多選切换”按钮 <span id="genre-toggle"> 的父节点；
          ② 文本恰好是“類別[:：]”的标签（导航栏 <li> 的文本更长，自动排除）；
          ③ 自身直接文本（不含后代）含“類別”，且跳过 <a>/<script>/<style>。
        """
        if soup is None:
            return None

        def _own_text(tag) -> str:
            """标签自身的直接文本（不含后代标签里的文字）"""
            try:
                pieces = [s for s in tag.find_all(string=True, recursive=False)
                          if isinstance(s, str)]
            except Exception:
                pieces = []
            return ' '.join(p.strip() for p in pieces).strip()

        header = None
        try:
            toggle = soup.find(id='genre-toggle')
        except Exception:
            toggle = None
        if toggle is not None:
            try:
                header = toggle.find_parent(['p', 'span', 'div', 'td', 'th', 'header'])
            except Exception:
                header = getattr(toggle, 'parent', None)
        if header is None:
            for tag in soup.find_all(True):
                if tag.name in ('a', 'script', 'style'):
                    continue
                text = tag.get_text(' ', strip=True) if hasattr(tag, 'get_text') else ''
                if re.match(r'^類別\s*[:：]?$', text or ''):
                    header = tag
                    break
        if header is None:
            for tag in soup.find_all(True):
                if tag.name in ('a', 'script', 'style'):
                    continue
                text = tag.get_text(' ', strip=True) if hasattr(tag, 'get_text') else ''
                if '類別' in _own_text(tag) and len(text) <= 40:
                    header = tag
                    break
        if header is None:
            return None

        # 向后找真正装着类别链接的兄弟块：优先含 span.genre / gr_sel 复选框的那些
        fallback = None
        node = header
        for _ in range(5):
            try:
                node = node.find_next_sibling()
            except Exception:
                break
            if node is None:
                break
            try:
                has_genre = bool(node.find(
                    'span',
                    class_=lambda v: v and 'genre' in (v if isinstance(v, str)
                                                       else ' '.join(v)).lower()))
                has_check = bool(node.find('input', attrs={'name': 'gr_sel'}) or
                                 node.find('input', attrs={'type': 'checkbox'}))
                has_link = bool(node.find('a', href=True))
            except Exception:
                continue
            if has_link and (has_genre or has_check):
                return node
            if has_link and fallback is None:
                fallback = node
        return fallback

    def extract_categories(self, html: str, base_url: str) -> List[Dict[str, str]]:
        """抓取“類別”区块：类别名 + 类别网址 + 网址末段的标签代码

        :return: [{'name': 类别名, 'url': 类别网址, 'code': 标签代码}, ...]
        """
        if not html:
            return []
        soup = self._build_soup(html)
        block = self._genre_block(soup) if soup is not None else None
        if block is None:
            return []

        anchors: List[Any] = []
        # 优先取 <span class="genre"> 里的链接（页面上“多選提交”按钮所在的 span 无链接）
        try:
            for span in block.find_all(
                    'span',
                    class_=lambda v: v and 'genre' in (v if isinstance(v, str) else ' '.join(v)).lower()):
                a = span.find('a', href=True)
                if a is not None:
                    anchors.append(a)
        except Exception:
            anchors = []
        if not anchors:
            # 兜底：同一 <label> 内与复选框并列的链接
            try:
                for label in block.find_all('label'):
                    if label.find('input', attrs={'type': 'checkbox'}) is None:
                        continue
                    a = label.find('a', href=True)
                    if a is not None:
                        anchors.append(a)
            except Exception:
                pass
        if not anchors:
            return []

        entries: List[Dict[str, str]] = []
        seen = set()
        for a in anchors:
            name = str(a.get_text(strip=True) or '').strip()
            href = str(a.get('href') or '').strip()
            if not name or not href or href.lower().startswith(('javascript:', '#')):
                continue
            if name in seen:
                continue
            seen.add(name)
            url = urllib.parse.urljoin(base_url or '', href)
            # 标签代码 = 网址路径最后一段（如 .../genre/f -> f）
            code = urllib.parse.urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]
            entries.append({'name': name, 'url': url, 'code': code})
        return entries

    def _build_record(self, url: str, status: str) -> Dict[str, Any]:
        """组装一条页面记录（为多网页同时抓取预留：以 url 为键保存）"""
        fields = dict(self.fields or {})
        actors = fields.get('actors', [])
        if isinstance(actors, str):
            actors = [a.strip() for a in actors.split(',') if a.strip()]
        elif not isinstance(actors, list):
            actors = [str(actors)] if actors else []

        return {
            'url': url,
            'status': status,
            'title': fields.get('title', '') or '',
            'actors': actors,
            'code': fields.get('code', '') or '',
            'date': fields.get('date', '') or '',
            'duration': fields.get('duration', 0) or 0,
            'series': fields.get('series', '') or '',
            'publisher': fields.get('publisher', '') or '',
            'maker': fields.get('maker', '') or '',
            # 类别：[{'name','url','code'}]（名前 + 类别网址 + 标签代码）
            'categories': [dict(x) for x in (getattr(self, 'categories', None) or [])],
            'fields': fields,
            # 系列/演员/发行商/制作商对应的详情页链接（点击可在列表页层新建页签打开）
            'field_links': dict(self.field_links or {}),
            'images': list(self.images or []),
            'magnets': list(self.magnets or []),
            'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'engine': self.last_meta.get('engine', ''),
        }

    def _fetch_magnet_ajax(self, page_url: str, page_html: str) -> Optional[str]:
        """磁力表由 ajax 异步填充时，按配置的接口路径与参数模板直接请求接口"""
        if not self.config.magnet_ajax_enabled:
            self.log("异步磁力接口已在配置中关闭")
            return None

        path = (self.config.magnet_ajax_path or '').strip() or DEFAULT_MAGNET_AJAX_PATH
        template = (self.config.magnet_ajax_params or '').strip() or DEFAULT_MAGNET_AJAX_PARAMS

        try:
            # 参数模板中的 {xxx} 取自页面脚本的 var xxx = 值，{floor} 为随机数
            values: Dict[str, str] = {}
            for name in re.findall(r'\{(\w+)\}', template):
                if name == 'floor':
                    values[name] = str(random.randint(1, 1000))
                    continue
                match = re.search(
                    r'var\s+' + re.escape(name) + r'\s*=\s*[\'"]?([^\'";\s]+)', page_html or '')
                if not match:
                    self.log(f"页面中未找到变量 {name}，跳过异步磁力接口")
                    return None
                values[name] = match.group(1)

            query = template
            for name, value in values.items():
                query = query.replace('{' + name + '}', urllib.parse.quote(value, safe=''))

            if path.startswith(('http://', 'https://')):
                ajax_url = path
            else:
                parts = urllib.parse.urlsplit(page_url)
                if not path.startswith('/'):
                    path = '/' + path
                ajax_url = f"{parts.scheme}://{parts.netloc}{path}"

            separator = '&' if '?' in ajax_url else '?'
            ajax_url = f"{ajax_url}{separator}{query}"
            self.log(f"请求磁力链接接口: {ajax_url}")

            headers = self._build_headers()
            headers['Referer'] = page_url
            headers['Accept'] = '*/*'
            headers['X-Requested-With'] = 'XMLHttpRequest'

            req = urllib.request.Request(ajax_url, headers=headers)
            with self._build_opener().open(req, timeout=self.config.timeout) as response:
                raw = response.read()
                data = self._decompress_response(raw, response.headers)
                text = data.decode(self._detect_encoding(data, response.headers), errors='replace')

            self.log(f"磁力链接接口返回 {len(text)} 字符，含磁力链: {'magnet:' in text}")
            return text
        except Exception as e:
            self.log(f"请求磁力链接接口失败: {e}")
            return None

    def extract_magnets(self, html: str, base_url: str, full_html: Optional[str] = None) -> List[Dict]:
        """从内容A的磁力表中提取磁力链接（表格为空时会自动请求异步接口）"""
        magnets: List[Dict] = []
        seen: Set[str] = set()
        selector = (self.config.magnet_table_selector or '').strip() or DEFAULT_MAGNET_TABLE_SELECTOR

        # 方式1: 解析磁力表（3列：标题 / 文件大小 / 分享日期）
        table_found = False
        if BS4_AVAILABLE:
            try:
                soup = BeautifulSoup(html, 'html.parser')
                table = soup.select_one(selector)
                if table is None:
                    self.log(f"未找到磁力表: {selector}")
                else:
                    table_found = True
                    self.log(f"磁力表已定位: {selector}")
                    magnets.extend(self._parse_magnet_rows(str(table), f'css:{selector}', seen))
            except Exception as e:
                self.log(f"磁力表解析失败: {e}")

            # 表格存在但为空 → 内容由 ajax 异步填充，直接请求接口取行数据
            if table_found and not magnets:
                self.log("磁力表为空，改为请求异步接口获取磁力链接...")
                ajax_html = self._fetch_magnet_ajax(base_url, full_html or html)
                if ajax_html:
                    magnets.extend(
                        self._parse_magnet_rows(ajax_html, 'ajax:uncledatoolsbyajax', seen))

        if table_found:
            self.log(self._magnet_summary(magnets))
            return magnets

        # 方式2: 正则兜底（表格结构变化或未装 bs4 时仍尽量取到链接）
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

        self.log(self._magnet_summary(magnets))
        return magnets

    @staticmethod
    def _magnet_summary(magnets: List[Dict]) -> str:
        """磁力链抓取汇总日志（全部抓取完成后只输出这一条）

        取代原先“每抓到一个链接就写一条日志”的做法。
        """
        total = len(magnets)
        if not total:
            return "磁力链抓取完成：未发现磁力链接"
        hd_count = sum(1 for m in magnets if m.get('hd'))
        parts = [f"磁力链抓取完成：共 {total} 个"]
        if hd_count:
            parts.append(f"含高清标识 {hd_count} 个")
        sizes = [str(m.get('size') or '').strip() for m in magnets]
        if any(sizes):
            largest = max((s for s in sizes if s), key=len, default='')
            if largest:
                parts.append(f"最大体积约 {largest}")
        return "，".join(parts)

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
        """下载图片（Referer 用页面地址，部分图床会校验来源）"""
        try:
            headers = self._build_headers()
            headers['Accept'] = 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'
            headers['Accept-Encoding'] = 'identity'
            if self.base_url:
                headers['Referer'] = self.base_url
            else:
                headers['Referer'] = urllib.parse.urlsplit(url)._replace(path='/', query='', fragment='').geturl()

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
            # 列出具体失败原因（无数据 / 连接出错 / 状态码等），便于在日志中定位
            reason = str(self.last_meta.get('error') or '').strip() or '连接出错（无法获取网页内容）'
            self.log(f"获取网页失败：{reason}")
            if self.record_callback:
                self.record_callback(self._build_record(url, 'failed'))
            if self.complete_callback:
                self.complete_callback(False, f"连接出错：{reason}", [], [])
            return

        self.html_content = html
        self.base_url = url

        # 内容A：body > div.container 的代码内容，后续全部在这段代码内提取
        content_a = self.content_a or self._extract_content_a(html)
        self.content_a = content_a
        self.update_progress(55, f"内容A 已就绪（{len(content_a)} 字符）")

        # 提取图片（在内容A中进行）
        self.update_progress(60, "正在分析图片...")
        self.images = self.extract_images(content_a, url, full_html=html)

        # 提取磁力链接（在内容A中进行）
        self.update_progress(80, "正在分析磁力链接...")
        self.magnets = self.extract_magnets(content_a, url, full_html=html)

        # 磁力表由 JS 异步生成：静态源码取不到时，自动改用浏览器渲染重试一次
        already_rendered = '渲染' in str(self.last_meta.get('engine', ''))
        if not self.magnets and self.config.auto_render_on_missing and not already_rendered:
            if not (PLAYWRIGHT_AVAILABLE or SELENIUM_AVAILABLE):
                self.log("磁力表未取到：未安装 playwright / selenium，无法等待 JS 生成表格，"
                         "可在“浏览器模式”中确认或安装后重试")
            else:
                self.log("静态源码中未找到磁力表，改用浏览器渲染重试（等待 JS 生成表格）...")
                self.update_progress(85, "等待页面 JS 生成磁力表...")

                origin_wait = self.config.wait_for_selector
                if not (origin_wait or '').strip():
                    self.config.wait_for_selector = (self.config.magnet_table_selector
                                                     or DEFAULT_MAGNET_TABLE_SELECTOR)
                try:
                    rendered_html = self._fetch_rendered(url)
                finally:
                    self.config.wait_for_selector = origin_wait

                if rendered_html:
                    html = rendered_html
                    self.html_content = html
                    self.content_a = self._extract_content_a(html)
                    content_a = self.content_a
                    self.images = self.extract_images(content_a, url, full_html=html)
                    self.magnets = self.extract_magnets(content_a, url, full_html=html)
                else:
                    self.log("浏览器渲染也未取到页面，磁力表仍为空")

        # 抓取结构化字段（标题/演员/番号/日期/时长）
        self.update_progress(90, "正在抓取标题/演员/番号等字段...")
        self.fields = self.extract_fields(content_a)
        # 同时抓取这些字段对应的超链接（系列 / 演员 / 发行商 / 制作商），
        # 供主窗口点击后直接在“视频列表页”层新建页签打开
        self.field_links = self.extract_field_links(
            html or content_a, self.base_url, self.fields)
        # 类别（“類別”）：名称 + 类别网址 + 标签代码；同时写入 field_links，
        # 便于复用“点击字段 -> 在视频列表页层打开”的既有逻辑
        self.categories = self.extract_categories(html or content_a, self.base_url)
        if self.categories:
            self.field_links['categories'] = [
                {'name': str(e.get('name') or ''), 'url': str(e.get('url') or '')}
                for e in self.categories]
            # 旧配置里保存的“类别”自定义规则可能失效（取不到名字），
            # 这里用精确抓取的结果回填，避免字段里的类别名为空
            if not self.fields.get('categories'):
                self.fields['categories'] = [str(e.get('name') or '')
                                             for e in self.categories
                                             if str(e.get('name') or '')]
            self.log("类别(類別)抓取完成：" + "、".join(
                f"{e.get('name')}({e.get('code')})" for e in self.categories))

        # 判断页面是否有有效内容：标题/番号/图片/磁力链全为空 → 视为无数据
        has_data = bool(
            (self.fields or {}).get('title')
            or (self.fields or {}).get('code')
            or self.images
            or self.magnets
        )
        if not has_data:
            self.log("网页无数据：未识别到标题/番号/图片/磁力链，"
                     "该地址可能不存在、已删除、需要登录或无相应内容")

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

        # 回传页面记录（供窗口缓存，后续可保存为 JSON）
        if self.record_callback:
            self.record_callback(self._build_record(url, 'ok' if has_data else 'failed'))

        if self.complete_callback:
            if has_data:
                self.complete_callback(True, "处理完成", self.images, self.magnets)
            else:
                # 页面能打开但取不到任何字段/图片/磁力链：按失败处理，便于再次点击“抓取”重试
                self.complete_callback(False, "网页无数据（未识别到标题/图片/磁力链）",
                                       self.images, self.magnets)


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
        self.thumb_cells: List[tk.Frame] = []
        # 缩略图显示状态：默认隐藏（点击图区旁的图标按钮切换）
        self._thumbs_visible = False
        self.thumb_toggle_canvas = None
        # 磁力链表格行 -> 磁力链接信息（用于点击打开/复制）
        self.magnet_item_map: Dict[str, Dict] = {}

        # 大图缓存与异步预取
        self._image_cache: Dict[str, bytes] = {}
        self._image_cache_size = 0
        self._prefetching: Set[str] = set()

        # 线程安全：后台线程不能直接操作 Tk，统一放入队列由主线程执行
        self._main_tasks = queue.Queue()
        self._main_task_poller_running = False
        self._main_task_after_id = None   # 主线程轮询器的 after 句柄（清理时取消）

        # 图片下载线程池（网页/图片的抓取下载都不占用界面线程）
        self._image_pool = ThreadPoolExecutor(max_workers=IMAGE_POOL_MAX_WORKERS,
                                              thread_name_prefix='grab-img')
        self._thumb_loading: Set[str] = set()
        self._results_token = 0   # 结果批次标记，用于丢弃过期的异步任务

        # 爬虫设置浮窗层
        self._settings_win = None

        # “本地设置”浮窗层与已配置的视频根目录
        self._folder_win = None
        self.local_folders: List[str] = self._load_local_folders()
        self.local_folder_scopes: Dict[str, str] = self._load_local_folder_scopes()

        # 本地翻译大模型：模型名 -> 模型文件夹；以及当前的翻译显示状态
        self.translation_model_dirs: Dict[str, str] = self._load_translation_model_dirs()
        self._selected_translation_model = self._load_selected_translation_model()
        self._translators: Dict[str, LocalTranslationModel] = {}

        # 视频检索索引缓存（内存中是 dict，落盘为 JSON 文件；None 表示尚未建立）
        self._video_index: Optional[Dict[str, Any]] = None
        self._indexing = False      # 是否正在建立索引（避免重复点击）
        self._translation_originals: Dict[str, str] = {}
        self._translated = False
        self._translating = False
        self._model_busy = False        # 模型正在载入/释放，避免重复操作
        self._translated_full_title = ''  # 翻译后的完整标题（未翻译时为空）
        self._last_search_folders: List[str] = []   # 本次检索匹配到的文件夹（供“打开文件夹”）

        # 保存目录历史下拉层
        self._save_dir_popup = None
        self._save_dir_watch_running = False   # 层展开期间的“点击层外收起”轮询是否在跑

        # 页面数据缓存：url -> 记录（多网页抓取时按页签保存字段数据）
        self.page_records: Dict[str, Dict] = {}
        self.active_url: Optional[str] = None

        # 多网页（多链接）：每个 URL 一个页签，图片/磁力/字段数据缓存在 pages 中
        self.pages: Dict[str, Dict] = {}
        self.page_order: List[str] = []
        self.workers: List['WebGrabWorker'] = []
        self._pending_urls: List[str] = []
        self._running_urls: Set[str] = set()
        self._tab_widgets: Dict[str, Dict[str, tk.Widget]] = {}
        # 页面路径 -> 该页签当前规范网址。同一路径不同主机（网址库入口切换、
        # 手动换域名重抓）视为同一页面，避免为同一内容重复创建页签
        self._page_keys: Dict[str, str] = {}
        self._pattern_matcher = None      # 番号/后缀识别器（惰性创建，见 _get_pattern_matcher）

        # 字段规则在“爬虫设置”层中的控件引用
        self._field_rule_vars: List[Dict[str, Any]] = []

        # 图片浏览浮窗层
        self._viewer_win = None
        self._viewer_canvas = None
        self._viewer_index = 0
        self._viewer_photo = None
        self._viewer_token = 0
        self._viewer_pil = None          # 当前图片的 PIL 原图（缩放/拖动时复用，避免反复解码）
        self._viewer_zoom = 1.0          # 相对“适应窗口”的缩放倍数
        self._viewer_pan_x = 0.0         # 拖动平移量（像素）
        self._viewer_pan_y = 0.0
        self._viewer_drag_from = None    # 拖动起点
        self._viewer_drag_pan = (0.0, 0.0)
        self._viewer_drag_moved = False  # 本次按下是否已构成拖动（区分点击/拖动）
        self._viewer_press_tags = ()     # 按下时命中的画布元素标签
        self._viewer_size_text = ''      # 原图尺寸文字（如 800 x 534）
        self._viewer_info_vars = {}      # 信息栏三个文本变量
        self._viewer_info_labels = {}    # 信息栏三个文本控件

        # 拖放状态
        self._dnd_registered = False
        self._drop_handled = False
        # 最近一次拖入的网址（双击拖放区时复制到系统剪贴板）
        self._last_dropped_url: Optional[str] = None

        # 爬虫设置变量（界面移到“爬虫设置”浮窗层，变量仍在此处创建）
        self._init_crawler_vars()

        # 创建界面
        self._create_widgets()
        self._load_from_config_manager()
        self._bind_events()

        # 主线程任务轮询器（供后台线程安全回传结果）
        self._start_main_task_poller()

    def _init_crawler_vars(self):
        """创建爬虫设置相关的变量（界面在“爬虫设置”浮窗层中构建）"""
        self.max_images_var = tk.IntVar(value=10)
        self.auto_download_var = tk.BooleanVar(value=False)
        self.save_path_var = tk.StringVar()
        self.main_selector_var = tk.StringVar(value=DEFAULT_MAIN_IMAGE_SELECTOR)
        self.sample_selector_var = tk.StringVar(value=DEFAULT_SAMPLE_CONTAINER_SELECTOR)
        self.sample_link_var = tk.StringVar(value=DEFAULT_SAMPLE_LINK_SELECTOR)
        self.magnet_selector_var = tk.StringVar(value=DEFAULT_MAGNET_TABLE_SELECTOR)
        self.magnet_ajax_path_var = tk.StringVar(value=DEFAULT_MAGNET_AJAX_PATH)
        self.magnet_ajax_params_var = tk.StringVar(value=DEFAULT_MAGNET_AJAX_PARAMS)
        self.magnet_ajax_enabled_var = tk.BooleanVar(value=True)
        self.auto_render_var = tk.BooleanVar(value=True)
        self.auto_save_json_var = tk.BooleanVar(
            value=bool(self.config_manager.get(self.CONFIG_SECTION, 'auto_save_json', False)))
        # 多链接：勾选后可累积多个网页地址，一次性并发抓取
        self.multi_link_var = tk.BooleanVar(
            value=bool(self.config_manager.get(self.CONFIG_SECTION, 'multi_link', False)))
        # 当前选择的本地翻译大模型
        self.translation_model_var = tk.StringVar(value=self._default_translation_model())

        # ---------- 视频列表页（瀑布流列表）设置 ----------
        section = self.CONFIG_SECTION
        self.list_container_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_container_selector',
                                          DEFAULT_LIST_CONTAINER_SELECTOR))
        self.list_item_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_item_selector',
                                          DEFAULT_LIST_ITEM_SELECTOR))
        self.list_thumb_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_thumb_selector',
                                          DEFAULT_LIST_THUMB_SELECTOR))
        self.list_title_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_title_selector',
                                          DEFAULT_LIST_TITLE_SELECTOR))
        self.list_code_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_code_selector',
                                          DEFAULT_LIST_CODE_SELECTOR))
        self.list_date_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_date_selector',
                                          DEFAULT_LIST_DATE_SELECTOR))
        self.list_link_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_link_selector',
                                          DEFAULT_LIST_LINK_SELECTOR))
        self.list_flag_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_flag_selector',
                                          DEFAULT_LIST_FLAG_SELECTOR))
        self.list_next_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_next_selector',
                                          DEFAULT_LIST_NEXT_SELECTOR))
        self.list_alert_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_alert_selector',
                                          DEFAULT_LIST_ALERT_SELECTOR))
        self.list_page_template_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_page_template',
                                          DEFAULT_LIST_PAGE_TEMPLATE))
        # 搜索地址模板（{keyword} 为关键词；页面里识别到跳转路径时优先用识别结果）
        self.list_search_path_var = tk.StringVar(
            value=self.config_manager.get(section, 'list_search_path',
                                          DEFAULT_LIST_SEARCH_PATH))
        # 抓取列表页时自动把公告层中的网址补充到网址库
        self.list_auto_collect_var = tk.BooleanVar(
            value=bool(self.config_manager.get(section, 'list_auto_collect', True)))
        # 列表页是否用浏览器渲染后抓取（部分站点列表需 JS 渲染）
        self.list_render_var = tk.BooleanVar(
            value=bool(self.config_manager.get(section, 'list_render_js', False)))

        # 网址库（列表页地址集合）与列表页浏览层状态
        self.list_url_entry_var = tk.StringVar(value='')
        self.list_page_var = tk.StringVar(value='')
        # 多页连续抓取：从当前网址起连续抓取的页数（1 = 仅当前页）
        self.list_pages_var = tk.StringVar(value=str(
            self.config_manager.get(self.CONFIG_SECTION, 'list_pages_shown', 1) or 1))
        # {#} 数字占位符补零位数（空 = 不补零；填 3 表示 7 → 007）
        self.list_pad_var = tk.StringVar(
            value=str(self.config_manager.get(self.CONFIG_SECTION, 'list_wildcard_pad', '') or ''))
        self._list_urls: List[str] = self._load_list_urls()
        # 列表页页签：每个页签配套保存“网址 + 当前页码”，用于区分
        # 全部列表 / 演员作品列表 / 系列作品列表 等不同类别
        self.list_tabs: List[Dict] = self._load_list_tabs()
        self._list_active_tab = self._load_active_tab_index()
        # 网址库入口：当前正在使用的站点地址（抓取失败时自动切换到下一个）
        self._list_active_host = str(
            self.config_manager.get(self.CONFIG_SECTION, 'list_active_host', '') or '').strip()
        self._host_retried: Set[str] = set()   # 已重试过入口切换的地址（避免反复重试）
        self._list_search_var = tk.StringVar(value='')
        self._list_search_info: Dict[str, str] = {}   # 从页面识别到的搜索框逻辑
        self._list_tab_widgets: List[Dict] = []
        self._list_tabs_canvas = None
        self._list_tabs_inner = None
        self._list_tabs_window = None
        self._list_win = None              # 列表页浏览层（从右侧滑出，位置与主窗口锁定）
        self._list_win_hidden = False      # 层是否已由用户“关闭”（隐藏）：True 时主窗口还原也不自动弹出
        self._list_content = None          # 列表页层的滑动内容容器（层本身不动，只移动它）
        self._list_root_binds = []         # 主窗口 <Configure>/<Map>/<Unmap> 绑定句柄
        self._list_panel_offset = 0.0      # 滑入动画期间的横向偏移（像素）
        self._list_canvas = None
        self._list_items_frame = None
        # 类别（類別）浏览层：与列表页层同尺寸，从右侧滑出
        self._genre_win = None
        self._genre_root_binds = []
        self._genre_panel_offset = 0.0
        self._genre_content = None    # 类别层的滑动内容容器（层本身固定不动，只移动它）
        self._genre_last_geom = None  # 层最近一次下发的几何（未变化则不再下发）
        self._genre_canvas_width = None
        self._genre_frame_size = None
        self._genre_group_widths = {}
        self._genre_layer_busy = False
        self._genre_layer_canvas = None
        self._genre_layer_frame = None
        self._genre_layer_groups: List[Dict[str, Any]] = []   # [{'name','items':[...]}]
        self._genre_layer_pills: List[Any] = []               # [(pill, group_idx, item_idx)]
        self._genre_layer_containers: List[Tuple[Any, List[Any]]] = []
        self._genre_layer_selection = set()    # {(group_idx, item_idx)}：关闭层后再打开仍然保留
        self._genre_library_loaded = False
        self._list_cards: List[tk.Frame] = []
        self._list_photo_refs: List[Any] = []
        # 缩略图下载去重：正在下载的地址 + 等待同一张图片的卡片 Label 列表
        self._list_thumb_pending: Set[str] = set()
        self._list_thumb_waiters: Dict[str, List[Any]] = {}
        # 批量抓取（{#} / 多页）最后抓到的页码：供“下一页”从这里往后续接
        self._list_batch_last_page: Optional[int] = None
        # 本次批量抓取是否为分页列表（{#} 占位网址不参与“下一页”续接）
        self._list_batch_pages_mode: bool = False
        self._list_items: List[Dict] = []  # 当前列表页解析出的全部视频
        self._list_token = 0               # 列表页结果批次标记
        self._list_next_url: Optional[str] = None   # 页面上的“下一页”链接
        self._list_busy = False            # 是否正在抓取列表页
        self._list_status_var = tk.StringVar(value='就绪')
        self._list_page_label_var = tk.StringVar(value='页码: -')
        # 列表层显示方式：card=瀑布流缩略图卡片 / list=列表（番号/标题/标志/日期）
        self._list_view_mode = 'card'
        self._list_thumbs_visible = True   # 瀑布流模式下缩略图是否显示（隐藏时用空白图占位）
        self._list_pagination: Dict[str, Any] = {}   # 当前页分页信息（当前页/总页数/页码区间）
        self._list_thumb_canvas = None     # “显示/隐藏缩略图”图标按钮
        self._list_view_canvas = None      # “瀑布流/列表”切换图标按钮
        # 列表页磁盘缓存容量上限（字节）：由“本地设置”中的“列表缓存上限(MB)”动态控制，
        # 默认 50MB；可在本地设置里修改，修改后立即按新值清理超容文件
        self._list_cache_max_bytes = 50 * 1024 * 1024
        self._list_rows: List[Dict] = []   # 列表模式的行控件（frame/title/item）
        self._list_selected_row = None     # 列表模式当前选中的行
        self._list_title_tip = None        # 缩略图悬浮标题提示层
        # 列表层标题翻译：key -> 中文标题；_list_titles_translated 表示是否处于“已翻译”显示状态
        self._list_title_zh: Dict[str, str] = {}
        self._list_titles_translated = False
        self._list_translating = False
        self._list_sort_col: Optional[str] = None    # 当前排序列（None=保持原序）
        self._list_sort_reverse: bool = False        # 当前排序方向（True=降序）

    # ========== 爬虫设置浮窗层（样式与图片浏览层一致） ==========
    def _is_settings_alive(self) -> bool:
        """设置层是否还活着"""
        try:
            return self._settings_win is not None and bool(self._settings_win.winfo_exists())
        except Exception:
            return False

    def _open_crawler_settings(self):
        """打开“爬虫设置”窗口（标准 Windows 窗口样式，页签区分详情页 / 列表页）"""
        if self._is_settings_alive():
            self._settings_win.deiconify()
            self._settings_win.lift()
            self._settings_win.focus_force()
            return

        root = self.frame.winfo_toplevel()
        win = tk.Toplevel(root)
        # 标准窗口：保留系统标题栏与最小化/最大化/关闭按钮，并允许调整大小
        win.title("爬虫设置")
        win.transient(root)
        try:
            win.resizable(True, True)
        except Exception:
            pass

        try:
            root.update_idletasks()
            width = min(max(760, root.winfo_width() - 120), 1000)
            height = min(max(620, root.winfo_height() - 100), 800)
            pos_x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
            pos_y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
        except Exception:
            width, height, pos_x, pos_y = 900, 700, 0, 0
        win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        try:
            win.minsize(720, 560)
        except Exception:
            pass

        # 页签：视频详情页（原有配置）/ 视频列表页（新增配置）
        notebook = ttk.Notebook(win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        detail_page = ttk.Frame(notebook)
        list_page = ttk.Frame(notebook)
        notebook.add(detail_page, text="  视频详情页  ")
        notebook.add(list_page, text="  视频列表页  ")

        # 详情页内容较多 → 外层套一个滚动容器；列表页自带内层滚动框，直接构建
        self._build_scrolled_settings_panel(detail_page, self._build_crawler_settings)
        self._build_list_settings(list_page)

        # 底部提示条
        footer = ttk.Frame(win)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(footer, text="修改即时保存到 app_config.json",
                  foreground='#888888', font=('Microsoft YaHei', 9)).pack(
            side=tk.LEFT, padx=10, pady=6)
        ttk.Button(footer, text="关闭", width=8,
                   command=self._close_crawler_settings).pack(side=tk.RIGHT, padx=10, pady=4)

        win.protocol("WM_DELETE_WINDOW", self._close_crawler_settings)
        win.bind('<Escape>', lambda e: self._close_crawler_settings())
        win.focus_force()

        self._settings_win = win

    def _build_scrolled_settings_panel(self, parent, builder: Callable):
        """在给定容器内创建一个带竖向滚动的设置面板，并用 builder 填充内容"""
        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        canvas = tk.Canvas(body, highlightthickness=0, background='#f7f7f7')
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        panel = ttk.Frame(canvas, padding=10)
        panel_window = canvas.create_window((0, 0), window=panel, anchor=tk.NW)

        def on_canvas_resize(event):
            try:
                canvas.itemconfigure(panel_window, width=event.width)
                canvas.configure(scrollregion=canvas.bbox('all'))
            except tk.TclError:
                pass

        canvas.bind('<Configure>', on_canvas_resize)
        panel.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        self._bind_mousewheel(canvas, canvas)
        self._bind_mousewheel(panel, canvas)

        builder(panel)
        return panel

    def _close_crawler_settings(self, event=None):
        """关闭“爬虫设置”窗口（关闭前补存一次规则，避免漏存）"""
        try:
            self._save_field_rules()
        except Exception:
            pass
        try:
            self._save_list_config()
        except Exception:
            pass
        try:
            if self._settings_win is not None:
                self._settings_win.destroy()
        except Exception:
            pass
        self._settings_win = None

    # ========== 本地文件夹设置层 & 视频检索 ==========
    def _load_local_folders(self) -> List[str]:
        """读取已配置的本地/网络共享视频根目录"""
        try:
            value = self.config_manager.get(self.CONFIG_SECTION, 'local_folders', []) or []
            return [str(p).strip() for p in value if str(p).strip()]
        except Exception:
            return []

    def _save_local_folders(self, folders: List[str]):
        """写入视频根目录列表（供“检索视频”使用），并清理已移除文件夹的范围配置"""
        self.local_folders = [str(p).strip() for p in folders if str(p).strip()]
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'local_folders',
                                    list(self.local_folders))
        except Exception:
            pass
        # 同步清理已不在列表中的文件夹的范围标记
        scopes = {p: v for p, v in (getattr(self, 'local_folder_scopes', None) or {}).items()
                  if p in set(self.local_folders)}
        if scopes != (getattr(self, 'local_folder_scopes', None) or {}):
            self._save_local_folder_scopes(scopes)

    def _load_local_folder_scopes(self) -> Dict[str, str]:
        """读取各文件夹的“范围”标记：{'路径': 'series'}；未在其中的均为“全部”"""
        try:
            value = self.config_manager.get(self.CONFIG_SECTION,
                                            'local_folder_scopes', {}) or {}
            if not isinstance(value, dict):
                return {}
            return {self._format_display_path(k): str(v).strip()
                    for k, v in value.items() if str(v).strip()}
        except Exception:
            return {}

    def _save_local_folder_scopes(self, scopes: Dict[str, str]):
        """写入各文件夹的“范围”标记（仅保存“系列”，空/全部不落盘）"""
        self.local_folder_scopes = {
            self._format_display_path(k): str(v).strip()
            for k, v in (scopes or {}).items() if str(v or '').strip()}
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'local_folder_scopes',
                                    dict(self.local_folder_scopes))
        except Exception:
            pass

    def _folder_scope(self, folder: str) -> str:
        """取文件夹的范围：'series' 或 'all'（未设置默认全部）"""
        scopes = getattr(self, 'local_folder_scopes', None) or {}
        return FOLDER_SCOPE_SERIES if scopes.get(
            self._format_display_path(folder)) == FOLDER_SCOPE_SERIES else FOLDER_SCOPE_ALL

    def _series_scoped_folders(self) -> List[str]:
        """所有“范围=系列”的文件夹（系列名检索只在这些文件夹中进行）"""
        return [p for p in self.local_folders if self._folder_scope(p) == FOLDER_SCOPE_SERIES]

    def _set_folder_scope(self, folder: str, scope: str):
        """设置/取消某个文件夹的“范围=系列”标记并落盘"""
        folder = self._format_display_path(folder)
        scopes = dict(getattr(self, 'local_folder_scopes', None) or {})
        if scope == FOLDER_SCOPE_SERIES:
            scopes[folder] = FOLDER_SCOPE_SERIES
        else:
            scopes.pop(folder, None)
        self._save_local_folder_scopes(scopes)

    def _load_translation_model_dirs(self) -> Dict[str, str]:
        """读取各翻译模型配置的本地文件夹"""
        try:
            value = self.config_manager.get(self.CONFIG_SECTION, 'translation_model_dirs', {}) or {}
            if not isinstance(value, dict):
                return {}
            return {name: str(value.get(name) or '').strip() for name in TRANSLATION_MODELS}
        except Exception:
            return {}

    def _save_translation_model_dirs(self, dirs: Dict[str, str]):
        """写入翻译模型文件夹配置"""
        self.translation_model_dirs = {
            str(k): str(v).strip() for k, v in (dirs or {}).items() if str(v or '').strip()}
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'translation_model_dirs',
                                    dict(self.translation_model_dirs))
        except Exception:
            pass

    def _load_selected_translation_model(self) -> str:
        """读取上次保存配置时选中的翻译模型（不存在/无效则回退默认）"""
        try:
            saved = str(self.config_manager.get(self.CONFIG_SECTION,
                                                'translation_model', '') or '').strip()
            if saved in TRANSLATION_MODELS:
                return saved
        except Exception:
            pass
        return ''

    def _default_translation_model(self) -> str:
        """默认选中的翻译模型：优先用上次保存的选择，其次第一个已配置文件夹的模型"""
        saved = getattr(self, '_selected_translation_model', '')
        if saved in TRANSLATION_MODELS:
            return saved
        for name in TRANSLATION_MODELS:
            if self.translation_model_dirs.get(name):
                return name
        return TRANSLATION_MODELS[0]

    def _current_translation_model(self) -> str:
        """下拉菜单当前选中的模型名（去掉“（未配置）”之类的后缀）"""
        selected = str(self.translation_model_var.get() or '').strip()
        for name in TRANSLATION_MODELS:
            if selected == name or selected.startswith(name):
                return name
        return selected

    def _refresh_translation_model_combo(self):
        """刷新翻译模型下拉菜单：已配置的排在前面，未配置的加后缀提示"""
        combo = getattr(self, 'translation_model_combo', None)
        if combo is None:
            return
        configured = [n for n in TRANSLATION_MODELS if self.translation_model_dirs.get(n)]
        values = configured + [f"{n}（未配置）"
                               for n in TRANSLATION_MODELS if n not in configured]
        try:
            combo['values'] = values
            # 当前选中的模型（去掉“（未配置）”后缀）映射回下拉菜单中的展示项
            current = self._current_translation_model()
            display = next((v for v in values if v == current or v.startswith(current)), None)
            if display is None:
                display = values[0] if values else ''
            self.translation_model_var.set(display)
        except Exception:
            pass

    def _is_folder_settings_alive(self) -> bool:
        """“本地设置”浮窗层是否还活着"""
        try:
            return self._folder_win is not None and bool(self._folder_win.winfo_exists())
        except Exception:
            return False

    def _open_local_folder_settings(self):
        """打开“本地设置”层：管理保存视频的本地/网络共享文件夹与本地翻译大模型"""
        if self._is_folder_settings_alive():
            self._folder_win.lift()
            return

        root = self.frame.winfo_toplevel()
        win = tk.Toplevel(root)
        # 使用 Windows 默认窗口样式：系统标题栏/边框/配色（不再自绘标题栏与底部提示条）
        win.title("本地设置")
        win.transient(root)
        try:
            root.update_idletasks()
            width = min(max(620, root.winfo_width() - 200), 780)
            height = min(max(560, root.winfo_height() - 180), 720)
            pos_x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
            pos_y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
        except Exception:
            width, height, pos_x, pos_y = 680, 460, 200, 120
        win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        try:
            win.minsize(560, 480)
        except Exception:
            pass

        body = ttk.Frame(win)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        ttk.Label(body, text="用于“检索视频”的根目录，可添加本地文件夹或网络共享路径"
                            "（如 \\\\NAS\\Videos）",
                  foreground="#888888").pack(anchor=tk.W, pady=(0, 6))

        # 路径输入 + 范围（单选） + 浏览 + 添加
        input_row = ttk.Frame(body)
        input_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(input_row, text="文件夹路径:").pack(side=tk.LEFT)
        path_var = tk.StringVar(value="")
        ttk.Entry(input_row, textvariable=path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Label(input_row, text="范围:").pack(side=tk.LEFT, padx=(8, 2))
        scope_var = tk.StringVar(value=FOLDER_SCOPE_ALL)
        ttk.Radiobutton(input_row, text="全部", value=FOLDER_SCOPE_ALL,
                        variable=scope_var).pack(side=tk.LEFT)
        ttk.Radiobutton(input_row, text="系列", value=FOLDER_SCOPE_SERIES,
                        variable=scope_var).pack(side=tk.LEFT, padx=(2, 0))

        # 已添加列表（含“范围”列；双击行或点“切换范围”修改）
        list_frame = ttk.LabelFrame(
            body, text="已添加的文件夹（可多选后移除；双击行或选中后点“切换范围”修改范围）")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        lb_frame = ttk.Frame(list_frame)
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        cols = ("scope", "path")              # 范围列在前，文件夹路径列在后
        folder_tree = ttk.Treeview(lb_frame, columns=cols, show="headings",
                                   selectmode=tk.EXTENDED, height=12)
        folder_tree.heading("path", text="文件夹路径")
        folder_tree.heading("scope", text="范围")
        folder_tree.column("scope", width=70, minwidth=60, anchor=tk.CENTER, stretch=False)
        folder_tree.column("path", width=430, anchor=tk.W, stretch=True)
        lb_scroll = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL, command=folder_tree.yview)
        folder_tree.configure(yscrollcommand=lb_scroll.set)
        folder_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lb_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def refresh_listbox():
            folder_tree.delete(*folder_tree.get_children())
            for i, path in enumerate(self.local_folders):
                folder_tree.insert("", tk.END, iid=str(i), values=(
                    FOLDER_SCOPE_LABELS[self._folder_scope(path)],
                    self._format_display_path(path)))

        refresh_listbox()

        def browse_folder():
            initial = path_var.get().strip() or (
                self.local_folders[0] if self.local_folders else '')
            try:
                directory = filedialog.askdirectory(
                    title="选择本地或网络共享文件夹", parent=win,
                    initialdir=initial or None)
            except Exception:
                directory = ''
            if directory:
                path_var.set(directory)

        def add_folder():
            path = self._format_display_path(path_var.get().strip())
            if not path:
                messagebox.showwarning("提示", "请先选择或填写文件夹路径", parent=win)
                return
            if path in {self._format_display_path(p) for p in self.local_folders}:
                messagebox.showinfo("提示", "该文件夹已在列表中", parent=win)
                return
            scope = scope_var.get()
            self._save_local_folders(list(self.local_folders) + [path])
            if scope == FOLDER_SCOPE_SERIES:
                self._set_folder_scope(path, FOLDER_SCOPE_SERIES)
            refresh_listbox()
            path_var.set("")
            scope_var.set(FOLDER_SCOPE_ALL)
            self._log(f"已添加本地文件夹: {path}"
                      f"（范围：{FOLDER_SCOPE_LABELS[self._folder_scope(path)]}）")

        def toggle_scope():
            """把选中文件夹的范围在“全部 ↔ 系列”之间切换（即改即存）"""
            selection = [int(i) for i in folder_tree.selection()]
            if not selection:
                messagebox.showwarning("提示", "请先在列表中选中要修改范围的文件夹", parent=win)
                return
            changed = []
            for i in selection:
                path = self.local_folders[i]
                new_scope = (FOLDER_SCOPE_ALL
                             if self._folder_scope(path) == FOLDER_SCOPE_SERIES
                             else FOLDER_SCOPE_SERIES)
                self._set_folder_scope(path, new_scope)
                changed.append(f"{path} → {FOLDER_SCOPE_LABELS[new_scope]}")
            refresh_listbox()
            self._log("已修改文件夹范围（下次检索自动按新范围重建索引）: " + "；".join(changed))

        def on_double_click(event):
            """双击行：切换该行文件夹的范围"""
            item = folder_tree.identify_row(event.y)
            if item:
                folder_tree.selection_set(item)
                toggle_scope()

        folder_tree.bind('<Double-1>', on_double_click)

        def remove_selected():
            selection = list(folder_tree.curselection())
            if not selection:
                messagebox.showwarning("提示", "请先在列表中选中要移除的文件夹", parent=win)
                return
            drop = set(selection)
            remain = [p for i, p in enumerate(self.local_folders) if i not in drop]
            self._save_local_folders(remain)   # 会同步清理对应范围配置
            refresh_listbox()
            self._log(f"已移除 {len(selection)} 个本地文件夹")

        btn_row = ttk.Frame(body)
        btn_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(btn_row, text="浏览...", width=10, command=browse_folder).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="添加", width=8, command=add_folder).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="切换范围", width=10, command=toggle_scope).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="移除选中", width=10, command=remove_selected).pack(
            side=tk.LEFT, padx=2)

        # ----- 列表页缓存：容量上限（MB） -----
        cache_frame = ttk.LabelFrame(body, text="列表页缓存（cache\\list_page 容量上限）")
        cache_frame.pack(fill=tk.X, pady=(8, 2))
        cache_inner = ttk.Frame(cache_frame)
        cache_inner.pack(fill=tk.X, padx=5, pady=4)
        ttk.Label(cache_inner, text="缓存上限(MB):").pack(side=tk.LEFT)
        cache_mb_var = tk.IntVar(
            value=max(1, getattr(self, '_list_cache_max_bytes', 0) // (1024 * 1024)))

        def on_cache_mb_change():
            """把缓存上限(MB)写入配置并立即生效（按新值清理超容文件）"""
            try:
                mb = int(cache_mb_var.get())
            except (TypeError, ValueError):
                mb = 50
            if mb < 1:
                mb = 1
            self._list_cache_max_bytes = mb * 1024 * 1024
            self.config_manager.set(self.CONFIG_SECTION, 'list_cache_max_mb', mb)
            self._log(f"列表页缓存上限已设为 {mb}MB")
            try:
                self._list_trim_cache()
            except Exception:
                pass

        cache_sb = ttk.Spinbox(cache_inner, from_=1, to=2000, textvariable=cache_mb_var,
                               width=6)
        cache_sb.pack(side=tk.LEFT, padx=5)
        cache_sb.config(command=on_cache_mb_change)          # 点击上下箭头
        cache_sb.bind('<FocusOut>', lambda e: on_cache_mb_change())   # 手动输入后失焦
        ttk.Label(cache_inner,
                  text="修改后立即生效，按修改时间从旧到新清理超容缓存",
                  foreground="#888888").pack(side=tk.LEFT, padx=6)

        # 操作按钮：打开缓存文件夹 / 一键清空缓存
        cache_btn_row = ttk.Frame(cache_frame)
        cache_btn_row.pack(fill=tk.X, padx=5, pady=(0, 6))

        def open_cache_folder():
            """用资源管理器打开缓存文件夹（便于手动清理）"""
            try:
                os.makedirs(LIST_CACHE_DIR, exist_ok=True)
                os.startfile(LIST_CACHE_DIR)   # Windows：以文件夹形式在资源管理器打开
            except Exception as e:
                messagebox.showerror("错误", f"无法打开缓存文件夹：{e}", parent=win)

        def clear_cache_all():
            """一键清空缓存文件夹内的全部文件"""
            try:
                os.makedirs(LIST_CACHE_DIR, exist_ok=True)
            except Exception:
                pass
            files = [fp for fp in glob.glob(os.path.join(LIST_CACHE_DIR, '*'))
                     if os.path.isfile(fp)]
            if not files:
                messagebox.showinfo("提示", "缓存文件夹当前为空，无需清理", parent=win)
                return
            if not messagebox.askyesno(
                    "确认", f"确定要清空缓存文件夹吗？\n将删除 {len(files)} 个文件"
                            f"（含列表页缩略图与详情页图片），此操作不可恢复。",
                    parent=win):
                return
            removed = 0
            for fp in files:
                try:
                    os.remove(fp)
                    removed += 1
                except OSError:
                    pass
            self._log(f"已手动清空列表缓存：删除 {removed} 个文件")
            messagebox.showinfo("完成", f"已清空缓存文件夹（删除 {removed} 个文件）", parent=win)

        ttk.Button(cache_btn_row, text="打开缓存文件夹", width=16,
                   command=open_cache_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(cache_btn_row, text="一键清空缓存", width=16,
                   command=clear_cache_all).pack(side=tk.LEFT, padx=2)

        # ----- 本地翻译大模型：为每个模型指定所在文件夹 -----
        model_frame = ttk.LabelFrame(body, text="本地翻译大模型（为各模型指定所在文件夹）")
        model_frame.pack(fill=tk.X, pady=(8, 2))

        model_path_vars: Dict[str, tk.StringVar] = {}

        def save_model_dirs():
            """把四个模型的文件夹写入配置并刷新主界面的模型下拉菜单"""
            self._save_translation_model_dirs(
                {name: var.get() for name, var in model_path_vars.items()})
            self._refresh_translation_model_combo()

        for model_name in TRANSLATION_MODELS:
            m_row = ttk.Frame(model_frame)
            m_row.pack(fill=tk.X, padx=5, pady=2)
            m_var = tk.StringVar(value=self.translation_model_dirs.get(model_name, ''))
            model_path_vars[model_name] = m_var
            ttk.Label(m_row, text=model_name, width=30, anchor=tk.W).pack(side=tk.LEFT)
            m_entry = ttk.Entry(m_row, textvariable=m_var)
            m_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            m_entry.bind('<FocusOut>', lambda e: save_model_dirs())
            ttk.Button(m_row, text="浏览...", width=8,
                       command=lambda v=m_var, n=model_name:
                       self._browse_model_dir(v, n, win, save_model_dirs)).pack(side=tk.RIGHT)

        env_var = tk.StringVar(value=translation_env_report())
        ttk.Label(model_frame, textvariable=env_var, foreground="#888888").pack(
            anchor=tk.W, padx=5, pady=(0, 2))
        ttk.Label(model_frame, text="nllb-200-distilled-600M 需 ctranslate2 + sentencepiece；"
                                    "Sakura 系列需 llama-cpp-python（CUDA 版需与显卡驱动匹配）",
                  foreground="#888888").pack(anchor=tk.W, padx=5, pady=(0, 4))

        # 关闭方式：系统标题栏 X / Esc 键（Windows 默认窗口行为）
        win.bind('<Escape>', lambda e: self._close_local_folder_settings())
        win.protocol("WM_DELETE_WINDOW", self._close_local_folder_settings)
        self._folder_win = win

    def _close_local_folder_settings(self, event=None):
        """关闭“本地设置”层"""
        try:
            if self._folder_win is not None:
                self._folder_win.destroy()
        except Exception:
            pass
        self._folder_win = None

    @staticmethod
    def _norm_code_value(text: Any) -> str:
        """番号归一化：只保留字母与数字并转大写，便于模糊比对"""
        return re.sub(r'[^A-Za-z0-9]', '', str(text or '')).upper()

    @staticmethod
    def _format_display_path(path: Any) -> str:
        """路径显示规范化，避免出现 / 与 \\ 混用：
        统一以 \\ 作为分隔符
        本地路径如 Z:\\下载\\202607\\VGD-202；
        网络共享路径以 \\\\ 开头，如 \\\\NAS\\Videos\\VGD-202
        """
        if not path:
            return ''
        text = str(path).strip()
        if text.startswith('\\\\') or text.startswith('//'):
            # 网络共享：保留开头的 \\，其余分隔符统一为 \
            return '\\\\' + re.sub(r'[\\/]+', '\\\\', text[2:])
        return re.sub(r'[\\/]+', '\\\\', text)

    def _current_code(self) -> str:
        """当前页签已抓取到的番号"""
        page = self.pages.get(self.active_url) or {}
        record = page.get('record') or {}
        code = str(record.get('code') or '').strip()
        if not code:
            code = str((record.get('fields') or {}).get('code') or '').strip()
        return code

    def _set_progress_ui(self, value: float, message: str):
        """刷新“处理进度”进度条与提示文字（必须在主线程调用，与抓取共用同一条）"""
        try:
            self.progress_bar.configure(mode='determinate', maximum=100)
            self.progress_bar['value'] = value
            self.progress_label.config(text=message)
        except Exception:
            pass

    def _build_video_index_now(self):
        """对已设置的本地文件夹做一次完整索引（不依赖是否已爬取网页）"""
        folders = list(self.local_folders)
        if not folders:
            messagebox.showwarning("提示", "请先点击“本地设置”添加视频根目录")
            return
        if getattr(self, '_indexing', False):
            messagebox.showinfo("提示", "索引正在建立中，请稍候")
            return
        self._indexing = True
        self._set_progress_ui(0, f"开始建立索引（0/{len(folders)}）")
        self._log(f"开始建立本地文件夹索引，根目录 {len(folders)} 个")
        threading.Thread(target=self._do_build_video_index,
                         args=(folders,), daemon=True).start()

    def _do_build_video_index(self, folders: List[str]):
        """后台线程：建立索引，完成后把结果反馈到界面"""
        try:
            data = self._build_video_index(
                folders, progress_cb=lambda v, m: self._index_progress(v, m),
                series_folders=self._series_scoped_folders())
            total = len(data.get('records', []))
            actor_total = sum(1 for r in data.get('records', [])
                              if r.get('is_actor_folder'))
            self._run_in_main_thread(
                lambda: self._on_video_index_built(total, actor_total))
        except Exception as e:
            message = str(e)
            self._log(f"建立索引失败: {message}")
            self._run_in_main_thread(
                lambda: self._on_video_index_failed(message))
        finally:
            self._indexing = False

    def _on_video_index_built(self, total: int, actor_total: int):
        """索引建立完成（主线程）"""
        self._set_progress_ui(
            100, f"索引完成：收录 {total} 个文件夹，其中演员文件夹 {actor_total} 个")
        self._log(f"索引建立完成：共 {total} 个文件夹，其中演员文件夹 {actor_total} 个"
                  "（之后点“翻译”会优先用索引匹配演员中文名）")

    def _on_video_index_failed(self, message: str):
        """索引建立失败（主线程）"""
        self._indexing = False
        self._set_progress_ui(0, "建立索引失败")
        try:
            messagebox.showerror("建立索引失败", message)
        except Exception:
            pass

    def _search_local_video(self, force_rebuild: bool = False):
        """按当前番号 + 演员名，在已配置的本地文件夹中模糊检索匹配的视频文件夹
        force_rebuild=True 时忽略已有索引、重新扫描根目录建立索引
        """
        code = self._current_code()
        actors = self._current_actors()
        series = self._current_series()
        if not code and not actors and not series:
            messagebox.showwarning("提示", "当前页签没有抓取到番号、演员或系列，请先抓取网页")
            return
        folders = list(self.local_folders)
        if not folders:
            messagebox.showwarning("提示", "请先点击“本地设置”添加视频根目录")
            return

        label = code or '（无番号）'
        series_roots = self._series_scoped_folders()
        self._set_search_result(f"正在检索番号 {label}、演员 {len(actors)} 位"
                                + (f"、系列 {series}" if series else '') + "，请稍候...")
        self._set_progress_ui(0, f"开始检索 {label}（0/{len(folders)}）")
        self._log(f"开始检索番号 {label}，演员 {len(actors)} 位"
                  + (f"，系列 {series}" if series else '')
                  + f"，根目录 {len(folders)} 个"
                  + (f"（其中“范围=系列” {len(series_roots)} 个，系列名只在这些文件夹中检索）"
                     if series and series_roots else "")
                  + ("（强制重建索引）" if force_rebuild else ""))
        if series and not series_roots:
            self._log("[提示] 没有设置“范围=系列”的文件夹，系列名将不参与本次检索"
                      "（可到“本地设置”勾选文件夹范围为“系列”）")
        threading.Thread(target=self._do_search_local_video,
                         args=(code, actors, series, folders, force_rebuild),
                         daemon=True).start()

    def _current_actors(self) -> List[str]:
        """当前页签已抓取到的演员名列表（优先取缓存原文，避免受显示截断影响）"""
        page = self.pages.get(self.active_url) or {}
        actors = [str(a).strip() for a in (page.get('actors') or []) if str(a).strip()]
        if not actors:
            actors = [a.strip() for a in self.actors_var.get().split('、') if a.strip()]
        return actors

    def _current_series(self) -> str:
        """当前页签已抓取到的系列名（优先取缓存原文，避免受显示截断影响）"""
        page = self.pages.get(self.active_url) or {}
        record = page.get('record') or {}
        series = str(record.get('series') or '').strip()
        if not series:
            series = self.series_var.get().strip()
        return series

    @staticmethod
    def _norm_series_name(text: Any) -> str:
        """系列名归一化：清除两端的标点/符号（系列名结尾常带「。」等）与全部空白

        例： "本中。" / " 本中 。" → "本中"
        """
        value = str(text or '').strip()
        if not value:
            return ''
        trim = (r'[\s。，、．\.，,;；:：!！\?？～~\-—_/\\|"\'`“”‘’'
                r'()（）\[\]【】《》「」『』]+')
        value = re.sub(f'^{trim}|{trim}$', '', value)
        value = re.sub(r'[\s\u3000]+', '', value)
        return value.lower()

    def _match_series_folders(self, series: str,
                              records: Optional[List[Dict[str, Any]]]
                              ) -> List[Dict[str, str]]:
        """用索引中的文件夹名做系列名模糊匹配（先归一化清除「。」等标点再比对）

        只在「范围=系列」的根目录收录的文件夹中匹配（series_scope=True），
        避免系列名与其它个人视频文件夹名相似而误命中。

        优先取「清除标点后完全相同」的文件夹，其次是互相包含且长度最接近的。

        :return: [{'series': 系列名, 'folder_name':.., 'file_path': 文件夹完整路径}]
        """
        target = self._norm_series_name(series)
        if not target or not records:
            return []

        scored: List[Tuple[int, int, Dict[str, str]]] = []
        seen: Set[str] = set()
        for record in records:
            if not record.get('series_scope'):        # 仅检索“范围=系列”的文件夹
                continue
            folder = str(record.get('folder_name') or '').strip()
            path = str(record.get('file_path') or '').strip()
            if not folder or not path or path in seen:
                continue
            norm = self._norm_series_name(folder)
            if len(norm) < 2 or len(target) < 2:
                continue
            if norm == target:
                score = 0                      # 完全一致，最优先
            elif target in norm or norm in target:
                score = 1 + abs(len(norm) - len(target))
            else:
                continue
            seen.add(path)
            scored.append((score, len(scored), {
                'series': series, 'folder_name': folder, 'file_path': path}))

        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored[:MAX_SERIES_FOLDER_HITS]]

    def _do_search_local_video(self, code: str, actors: List[str], series: str,
                               folders: List[str], force_rebuild: bool = False):
        """后台线程：用视频索引缓存检索番号、演员名与系列名（首次/强制时扫描根目录建立索引）"""
        target = self._norm_code_value(code)

        label = code or '（无番号）'
        self._run_in_main_thread(
            lambda: self._set_progress_ui(10, f"正在载入索引：{label}"))
        # 番号/演员名：检索所有文件夹；系列名：只检索“范围=系列”的文件夹
        index, from_cache = self._get_video_index(
            folders, force_rebuild, series_folders=self._series_scoped_folders())

        # 1) 在内存索引里按番号做模糊匹配（target in 归一化番号），无需再遍历磁盘
        matched: List[str] = []
        total = max(1, len(index))
        for i, entry in enumerate(index, 1):
            if target and target in entry.get('video_code', ''):
                matched.append(entry.get('file_path'))
            if i % 1000 == 0:
                v = 10 + 90 * i / total
                self._run_in_main_thread(
                    lambda v=v, i=i, t=total, m=len(matched):
                    self._set_progress_ui(v, f"正在匹配 {i}/{t}，已匹配 {m} 个"))

        # 2) 同时按演员名匹配索引中的「演员文件夹」（番号匹配不到时也能命中）
        actor_hits = self._match_actor_folders(actors, index)
        if actors and not actor_hits:
            self._run_in_main_thread(lambda: self._log(
                "索引中未找到与演员名匹配的演员文件夹"
                "（索引未包含演员文件夹时可点击“重建索引”）"))

        # 3) 按系列名模糊匹配文件夹名（只在“范围=系列”的文件夹中匹配，
        #    系列名结尾的「。」等标点已先清除）
        series_hits = self._match_series_folders(series, index)

        scanned_roots = (getattr(self, '_video_index', None) or {}).get('scan_roots') or folders
        missing = [f for f in folders if f not in set(scanned_roots)]
        self._run_in_main_thread(
            lambda: self._show_search_result(code, matched, missing, from_cache,
                                             actor_hits, actors, series_hits, series))

    # ========== 视频检索索引缓存（加速后续检索） ==========
    # 决策说明：
    #  - 数据结构：内存中用 dict 承载（norm_code -> [path] 的扁平列表），落盘即序列化为 JSON；
    #    JSON 本身就是“字典键值对”的持久化形式，二者不冲突，故内存 dict + JSON 文件。
    #  - 落盘位置：采用【JSON 文件】而非【纯内存缓存】。纯内存缓存进程退出即失效，
    #    下次仍需重新扫描；JSON 文件位于软件文件夹下，可跨重启动用，真正提升“后续搜索”速度。
    def _video_index_path(self) -> str:
        """视频检索索引缓存文件：放在软件所在文件夹下"""
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, VIDEO_INDEX_CACHE_FILE)

    def _load_video_index_file(self) -> Optional[Dict[str, Any]]:
        """读取索引缓存文件；文件缺失/损坏/根目录不匹配时返回 None"""
        path = self._video_index_path()
        try:
            if not os.path.isfile(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data.get('records'):
                return None
            # 版本不一致（如解析规则升级后）视为过期，重新扫描建立索引
            if int(data.get('version') or 0) != VIDEO_INDEX_VERSION:
                return None
            return data
        except Exception:
            return None

    def _save_video_index_file(self, data: Dict[str, Any]) -> None:
        """把索引缓存写入 JSON 文件（失败仅记日志，不影响本次检索）"""
        path = self._video_index_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"视频检索索引已缓存到：{self._format_display_path(path)}"
                      f"（{len(data.get('records', []))} 个文件夹）")
        except Exception as e:
            self._log(f"保存视频检索索引失败（本次仍按扫描结果检索）：{e}")

    def _actor_names_from_parent(self, folder_path: str) -> List[Dict[str, str]]:
        """从文件路径逐级向上查找“演员文件夹”，返回其演员名列表（未找到返回 []）

        例：Y:\\个人专属\\s三佳詩\\ABF-292... → 在父目录 s三佳詩 命中演员文件夹，
        返回 [{'japanese_name': '三佳詩', 'chinese_name': ''}]。遇到已设置的根目录即停止。
        """
        roots = {self._format_display_path(p)
                 for p in (getattr(self, 'local_folders', None) or [])}
        parent = os.path.dirname(folder_path.rstrip('\\/'))
        seen = set()
        while parent and parent not in seen:
            seen.add(parent)
            if parent in roots:          # 已抵达根目录，不再向上
                break
            name = os.path.basename(parent.rstrip('\\/'))
            parsed = self._parse_actor_folder_name(name)
            if parsed and parsed.get('actor_names'):
                return list(parsed['actor_names'])
            new_parent = os.path.dirname(parent)
            if new_parent == parent:     # 已到驱动器根（如 Y:\\），防止死循环
                break
            parent = new_parent
        return []

    def _add_folder_to_video_index(self, folder_path: str,
                                   info: Optional[Dict[str, Any]] = None):
        """把刚创建的视频文件夹（完整路径 + 视频信息）写入本地检索索引缓存

        新建的文件夹是位于“已设置根目录”下的子文件夹，后续全盘扫描也会收录它；
        这里提前写入索引（内存 + 落盘）是为了让之后的检索/去重立即识别它，
        避免被当作未收录的陌生文件夹重复收集。
        """
        folder_path = self._format_display_path((folder_path or '').strip())
        if not folder_path or not os.path.isdir(folder_path):
            return
        folder_name = os.path.basename(folder_path.rstrip('\\/')) or folder_path

        # 优先复用已在内存的索引，否则从文件读取
        data = self._video_index
        if not (isinstance(data, dict) and 'records' in data):
            data = self._load_video_index_file()
        if not (isinstance(data, dict) and 'records' in data):
            data = {'version': VIDEO_INDEX_VERSION, 'scan_roots': [],
                    'series_roots': [], 'last_scan': '', 'count': 0, 'records': []}

        records = data.setdefault('records', [])
        # 同一条 file_path 只保留一条，避免重复累积
        records[:] = [r for r in records
                      if self._format_display_path(r.get('file_path', '')) != folder_path]

        # 是否处于“范围=系列”的根目录下（系列名检索时单独匹配用）
        series_roots = {self._format_display_path(p)
                        for p in (data.get('series_roots') or [])}
        series_scope = any(folder_path == sr or folder_path.startswith(sr + '\\')
                           for sr in series_roots)

        record: Dict[str, Any] = {
            'file_path': folder_path,
            'folder_name': folder_name,
            'series_scope': series_scope,
            'is_actor_folder': False,
            'video_info': info or {},   # 完整视频信息（番号/标题/演员/系列/发行商/制作商/类别等）
        }
        code = str((info or {}).get('code') or '').strip()
        if code:
            record['video_code'] = self._norm_code_value(code)

        # 若本文件夹位于某个“演员文件夹”之内（如 s三佳詩\ABF-292...），
        # 把该父级演员名挂到本记录上，使按演员检索也能直接命中这个视频文件夹，
        # 而不只是命中其父演员文件夹
        actor_names = self._actor_names_from_parent(folder_path)
        if actor_names:
            record['actor_names'] = actor_names
        records.append(record)

        data['count'] = len(records)
        data['last_scan'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self._video_index = data
        self._save_video_index_file(data)
        self._log(f"已把新建文件夹加入本地检索索引：{folder_path}")

    def _build_video_index(self, folders: List[str],
                           progress_cb: Optional[Callable] = None,
                           series_folders: Optional[List[str]] = None) -> Dict[str, Any]:
        """扫描各根目录，建立「归一化番号 -> 文件夹路径」索引并落盘
        同时按演员文件夹命名规则解析文件夹名，标记其中的演员名（含曾用名）；
        “范围=系列”的根目录下收录的文件夹会单独打 series_scope 标记，
        供“系列名”检索时只在这些文件夹中匹配（避免与个人视频文件夹名混淆）
        """
        entries: List[Dict[str, Any]] = []
        scanned_roots: List[str] = []
        series_roots: List[str] = []
        series_set = {self._format_display_path(p) for p in (series_folders or [])}
        total = max(1, len(folders))
        for i, folder in enumerate(folders, 1):
            if progress_cb:
                shown = os.path.basename(folder.rstrip('\\/')) or folder
                progress_cb((i - 1) / total * 50,
                            f"正在建立索引 {i}/{total}：{shown}")
            if not folder or not os.path.isdir(folder):
                continue
            folder_disp = self._format_display_path(folder)
            is_series_root = folder_disp in series_set    # 该根目录是否“范围=系列”
            if is_series_root:
                series_roots.append(folder_disp)
            scanned_roots.append(folder)      # 保持与配置一致的原始路径（缓存命中比对用）
            try:
                for current, dirs, _files in os.walk(folder, onerror=lambda e: None):
                    current = self._format_display_path(current)   # 统一为反斜杠，避免 Y:/.../名 混用
                    for name in list(dirs):
                        record = {
                            'file_path': os.path.join(current, name),  # 文件夹完整路径（对应视频表 media_files.file_path / files.relative_path）
                            'folder_name': name,                       # 文件夹原始名称（按演员命名规则解析的来源）
                            'series_scope': is_series_root,            # 是否来自“范围=系列”的根目录（系列名单独检索用）
                        }
                        norm = self._norm_code_value(name)
                        if norm:
                            record['video_code'] = norm   # 归一化番号（对应视频表 media_files.video_code）
                        actor = self._parse_actor_folder_name(name)
                        record['is_actor_folder'] = bool(actor)  # 是否为按演员命名的文件夹（[声母][日文名]([中文名])）
                        if actor:
                            record['actor_names'] = actor['actor_names']  # 演员名集合（首项为主名，其余为曾用名；对应演员表 actors.japanese_name / actors.chinese_name）
                        entries.append(record)
            except Exception:
                pass
            if progress_cb:
                progress_cb(i / total * 50,
                            f"已扫描 {i}/{total}，收录 {len(entries)} 个文件夹")
        data = {
            'version': VIDEO_INDEX_VERSION,                   # 缓存格式版本号（缓存自身元信息，不对应数据库字段）
            'scan_roots': scanned_roots,                      # 已扫描的视频根目录列表（记录索引来源，不对应数据库字段）
            'series_roots': series_roots,                     # 其中“范围=系列”的根目录（范围变更后缓存自动失效重建）
            'last_scan': time.strftime('%Y-%m-%d %H:%M:%S'),  # 索引建立/扫描时间（对应视频表 files.last_scan）
            'count': len(entries),                            # 索引条目总数（不对应数据库字段）
            'records': entries,                               # 视频文件夹索引记录列表（每行对应一条视频记录）
        }
        self._video_index = data
        self._save_video_index_file(data)
        return data

    # ---- 演员文件夹名解析（索引建立时打标记） ----
    @staticmethod
    def _parse_actor_folder_name(folder_name: str) -> Optional[Dict[str, Any]]:
        """解析按演员命名的文件夹名，失败返回 None（即不是演员文件夹）

        命名规则（与 actor_folder_parser.py 一致，并放宽为支持多个曾用名）：
            [声母或0][演员日文名]([演员中文名])[+[曾用日文名]([曾用中文名])]...
            例：f楓ふうあ(枫芙爱)
                f鳳みゆ(凤美优)+川北メイサ(川北明沙)
                s三佳詩            ← 主名可只写日文名、省略括号（常见写法）
                hひなの花音(雏乃花音)+朝海汐   ← 曾用名允许只写日文名、不带括号
        """
        name = str(folder_name or '').strip()
        if not name:
            return None

        # 首位必须是声母（拼音首字母）或 0（用于“其他/数字”分组）
        head = re.match(r'^([0-9a-zA-Z])(.*)$', name)
        if not head:
            return None
        initial = head.group(1)
        rest = head.group(2)

        actor_names: List[Dict[str, str]] = []
        for part in re.split(r'\s*\+\s*', rest):   # 以 + 分隔主名与曾用名
            pair = re.match(r'^\s*([^()（）]+?)\s*[（(]\s*([^()（）]*?)\s*[）)]\s*$', part)
            if pair:
                jp = pair.group(1).strip()
                cn = pair.group(2).strip()
                # 日文名/中文名都必须含非 ASCII 字符，以此排除
                # 「VGD-202 (蓝光原盘)」这类带括号的番号文件夹（其“日文名”是纯 ASCII 编码）
                if jp and cn and not jp.isascii() and not cn.isascii():
                    actor_names.append({
                        'japanese_name': jp,   # 演员日文名（对应 actors.japanese_name）
                        'chinese_name': cn,    # 演员中文名（对应 actors.chinese_name）
                    })
                continue
            # 主名或曾用名可只写日文名、不带括号（如「s三佳詩」「+朝海汐」）：
            # 非 ASCII 即视为演员名（省略中文名，仍可参与匹配）
            bare = part.strip()
            if bare and not bare.isascii():
                actor_names.append({
                    'japanese_name': bare,     # 只有日文名（中文名留空，仍可参与匹配）
                    'chinese_name': '',
                })

        # 只要存在“非 ASCII 的日文名”即视为演员文件夹（覆盖 s三佳詩 这类省略括号的写法）；
        # 通过“日文名含非 ASCII”排除 sABF-202 这类纯番号文件夹。
        # 注：「s蓝光原盘」等纯描述性文件夹也会被纳入，但只有按该名检索时才会命中，影响极小，
        # 换取对常见「声母+日文名」命名的识别。
        actor_names = [a for a in actor_names
                       if a.get('japanese_name') and not a['japanese_name'].isascii()]
        if not actor_names:
            return None
        return {'initial': initial, 'actor_names': actor_names}

    def _get_video_index(self, folders: List[str], force_rebuild: bool = False,
                         series_folders: Optional[List[str]] = None
                         ) -> Tuple[List[Dict[str, str]], bool]:
        """获取视频索引（list[dict]）与是否来自缓存：
        1) 内存缓存命中且根目录/系列范围未变 -> 直接用；2) 否则读 JSON 文件；
        3) 仍无或需强制重建 -> 扫描根目录并建立索引
        """
        series_set = sorted(self._format_display_path(p) for p in (series_folders or []))

        def _scope_ok(data: Dict[str, Any]) -> bool:
            return sorted(data.get('series_roots') or []) == series_set

        cached = getattr(self, '_video_index', None)
        if (not force_rebuild and cached is not None
                and cached.get('scan_roots') == folders and _scope_ok(cached)
                and cached.get('records')):
            return cached['records'], True
        if not force_rebuild:
            data = self._load_video_index_file()
            if (data is not None and data.get('scan_roots') == folders
                    and _scope_ok(data) and data.get('records')):
                self._video_index = data
                return data['records'], True
        data = self._build_video_index(
            folders, progress_cb=lambda v, m: self._index_progress(v, m),
            series_folders=series_folders)
        return data['records'], False

    def _index_progress(self, value: float, message: str):
        """索引建立进度回调（在后台线程调用，转主线程刷新）"""
        self._run_in_main_thread(lambda: self._set_progress_ui(value, message))

    def _peek_video_index(self) -> Optional[List[Dict[str, Any]]]:
        """取已有索引但不触发扫描（供翻译前匹配演员名用，避免卡住界面）
        索引不存在，或其扫描根目录与当前配置不一致时，返回 None
        """
        roots = list(getattr(self, 'local_folders', None) or [])
        cached = getattr(self, '_video_index', None)
        if cached is not None and cached.get('records') and cached.get('scan_roots') == roots:
            return cached['records']
        data = self._load_video_index_file()
        if data is not None and data.get('records') and data.get('scan_roots') == roots:
            self._video_index = data
            return data['records']
        return None

    def _scan_folder_for_code(self, root: str, code: str) -> Optional[List[str]]:
        """（兼容/备用）在单个根目录下递归检索名称匹配番号的子文件夹（None=根目录不可用）"""
        if not root or not os.path.isdir(root):
            return None
        target = self._norm_code_value(code)
        if not target:
            return []
        results: List[str] = []
        try:
            for current, dirs, _files in os.walk(root, onerror=lambda e: None):
                try:
                    for name in list(dirs):
                        if target in self._norm_code_value(name):
                            results.append(os.path.join(current, name))
                except Exception:
                    continue
                if len(results) >= 200:
                    break
        except Exception:
            pass
        return results

    def _show_search_result(self, code: str, matched: List[str], missing: List[str],
                            from_cache: bool = False,
                            actor_hits: Optional[List[Dict[str, str]]] = None,
                            actors: Optional[List[str]] = None,
                            series_hits: Optional[List[Dict[str, str]]] = None,
                            series: Optional[str] = None):
        """把检索结果写入文本框（主线程），并同步进度/日志。

        文本内容全部由 ``_build_search_result_text`` 生成，
        想调整“视频检索结果”的内容或格式只需改那一个方法。
        """
        actor_hits = actor_hits or []
        actors = actors or []
        series_hits = series_hits or []
        series = (series or '').strip()

        text = self._build_search_result_text(
            code=code, matched=matched, missing=missing, from_cache=from_cache,
            actor_hits=actor_hits, actors=actors,
            series_hits=series_hits, series=series)
        self._set_search_result(text)

        # 记录本次检索匹配到的全部文件夹（番号 + 演员 + 系列，去重按命中顺序），
        # 供“打开文件夹”按钮弹出选择层使用
        seen = set()
        ordered: List[str] = []
        for p in (list(matched)
                  + [str(h.get('file_path') or '') for h in actor_hits]
                  + [str(h.get('file_path') or '') for h in series_hits]):
            p = str(p or '')
            if p and p.lower() not in seen:
                seen.add(p.lower())
                ordered.append(p)
        self._last_search_folders = ordered

        label = code or '（无番号）'
        actor_note = f"，演员匹配 {len(actor_hits)} 个文件夹" if actors else ""
        series_note = f"，系列匹配 {len(series_hits)} 个文件夹" if series else ""
        self._set_progress_ui(
            100, f"检索完成：{label}，匹配 {len(matched)} 个文件夹"
                 + actor_note + series_note
                 + ("，来自索引缓存" if from_cache else "，已重建索引")
                 + (f"，{len(missing)} 个根目录不可用" if missing else ""))
        self._log(f"检索完毕：番号 {label}，匹配 {len(matched)} 个文件夹"
                  + (f"；按演员名匹配 {len(actor_hits)} 个文件夹" if actors else
                     "（未抓取到演员名，跳过演员匹配）")
                  + (f"；按系列名匹配 {len(series_hits)} 个文件夹" if series else
                     "（未抓取到系列名，跳过系列匹配）"))

    # ==================================================================
    # 视频检索结果 —— 文本模板（要改内容/格式，只改这一个方法即可）
    # ------------------------------------------------------------------
    # 输出结构（同一行内的分隔符：全角空格「　」）：
    #
    #   番号：<番号>                        ← 第1行，永远有
    #   演员：<演员1>、<演员2>              ← 有演员时才有
    #   系列：<系列名>（比对名：<归一化名>） ← 有系列时才有；归一化名与原名相同则省略括号
    #   （空行）
    #   ┌ 番号段
    #   │  共找到 N 个匹配番号的文件夹（索引缓存）：
    #   │      <文件夹完整路径>
    #   └  未找到匹配番号的文件夹（索引缓存）
    #   （空行）
    #   ┌ 演员段（仅在抓到演员时输出）
    #   │  按演员名匹配到 N 个文件夹（索引缓存）：
    #   │      [演员 <视频演员名> → <命中的演员文件夹名>]（该文件夹同时匹配番号） <文件夹完整路径>
    #   └  未按演员名匹配到文件夹（索引缓存）
    #   （空行）
    #   ┌ 系列段（仅在抓到系列时输出）
    #   │  按系列名匹配到 N 个文件夹（索引缓存）：
    #   │      [系列 → <命中文件夹名>]（该文件夹同时匹配番号/演员） <文件夹完整路径>
    #   └  未按系列名匹配到文件夹（索引缓存）
    #   （空行）
    #   └ 根目录段（仅有不可用根目录时输出）
    #      以下根目录不存在或无法访问：
    #         <根目录完整路径>
    #
    # 说明：
    #   * src        —— 结果来源标注：「（索引缓存）」或「（已重新扫描建立索引）」
    #   * 命中项前缀 —— 演员段 `[演员 A → B]`；系列段 `[系列 → B]`
    #   * 交叉标注   —— 同一文件夹被多维度命中时追加「（该文件夹同时匹配番号/演员）」
    #   * 路径统一经 _format_display_path 规范化（统一反斜杠、网络路径保留 \\）
    # ==================================================================
    def _build_search_result_text(self, code: str, matched: List[str],
                                  missing: List[str], from_cache: bool,
                                  actor_hits: List[Dict[str, str]],
                                  actors: List[str],
                                  series_hits: List[Dict[str, str]],
                                  series: str) -> str:
        """按“视频检索结果模板”生成显示文本（纯函数式，便于单独调整格式）"""
        src = "（索引缓存）" if from_cache else "（已重新扫描建立索引）"
        matched_set = set(matched)
        actor_paths = {str(hit.get('file_path') or '') for hit in actor_hits}
        lines: List[str] = []

        # ---------- 头部：番号 / 演员 / 系列 ----------
        lines.append(f"番号：{code or '（无）'}")
        if actors:
            lines.append(f"演员：{'、'.join(actors)}")
        if series:
            norm_series = self._norm_series_name(series)
            lines.append(f"系列：{series}"
                         + (f"（比对名：{norm_series}）" if norm_series != series else ''))
        lines.append("")

        # ---------- 第1段：番号匹配 ----------
        if matched:
            lines.append(f"共找到 {len(matched)} 个匹配番号的文件夹 {src}：")
            for path in matched:
                lines.append(f"  {self._format_display_path(path)}")
        else:
            lines.append(f"未找到匹配番号的文件夹 {src}")

        # ---------- 第2段：演员名匹配（仅抓到演员时输出） ----------
        if actors:
            lines.append("")
            if actor_hits:
                lines.append(f"按演员名匹配到 {len(actor_hits)} 个文件夹 {src}：")
                for hit in actor_hits:
                    path = str(hit.get('file_path') or '')
                    same_code = "（该文件夹同时匹配番号）" if path in matched_set else ""
                    lines.append(
                        f"  → {hit.get('folder_name', '')}\n"
                        f"  →→ {same_code} {self._format_display_path(path)}")
            else:
                lines.append(f"未按演员名匹配到文件夹 {src}")

        # ---------- 第3段：系列名匹配（仅抓到系列时输出） ----------
        if series:
            lines.append("")
            if series_hits:
                lines.append(f"按【系列名】匹配到 {len(series_hits)} 个文件夹 {src}：")
                for hit in series_hits:
                    path = str(hit.get('file_path') or '')
                    note = ''
                    if path in matched_set:
                        note = "（该文件夹同时匹配番号）"
                    elif path in actor_paths:
                        note = "（该文件夹同时匹配演员）"
                    lines.append(
                        # f"  [系列 → {hit.get('folder_name', '')}]{note} "
                        # f"{self._format_display_path(path)}"
                        f"  → {hit.get('folder_name', '')}{note}\n"
                        f"  →( {self._format_display_path(path)})\n"
                        )
            else:
                lines.append(f"未按系列名匹配到文件夹 {src}")

        # ---------- 第4段：不可用的根目录 ----------
        if missing:
            lines.append("")
            lines.append("以下根目录不存在或无法访问：")
            for path in missing:
                lines.append(f"  {self._format_display_path(path)}")

        return "\n".join(lines)

    def _set_search_result(self, text: str):
        """刷新检索结果文本框内容"""
        try:
            self.search_result_text.configure(state=tk.NORMAL)
            self.search_result_text.delete('1.0', tk.END)
            self.search_result_text.insert(tk.END, text)
            self.search_result_text.see('1.0')
        except Exception:
            pass
        finally:
            try:
                self.search_result_text.configure(state=tk.DISABLED)
            except Exception:
                pass

    # ========== “打开文件夹”：弹出本次检索匹配到的文件夹选择层 ==========
    # 样式与“演员 ▼”下拉菜单一致（tk.Menu + tk_popup），并定位到按钮上方，
    # 避免窗口下方空间不足时菜单被遮挡 / 超出屏幕。
    def _open_matched_folders_popup(self):
        """在“打开文件夹”按钮上方弹出下拉菜单，列出本次检索匹配到的全部文件夹，
        点击即可用资源管理器打开对应文件夹"""
        folders = list(getattr(self, '_last_search_folders', []) or [])
        if not folders:
            self._log("[打开文件夹] 本次检索没有匹配到任何文件夹")
            return

        root = self.frame.winfo_toplevel()
        menu = tk.Menu(root, tearoff=0)
        for path in folders:
            disp = self._format_display_path(path)
            menu.add_command(label=disp,
                             command=lambda p=path: self._open_in_explorer(p))

        btn = getattr(self, 'open_folders_btn', None)
        try:
            # 先让菜单完成布局，再量取其高度，以便把菜单底部对齐按钮顶部（弹在按钮上方）
            menu.update_idletasks()
            h = menu.winfo_reqheight()
        except Exception:
            h = 0
        try:
            if btn is not None and btn.winfo_exists():
                pos_x = btn.winfo_rootx()
                pos_y = btn.winfo_rooty() - h      # 弹在按钮上方
                if pos_y < 0:                      # 上方空间不足：改弹在按钮下方
                    pos_y = btn.winfo_rooty() + btn.winfo_height()
            else:
                pos_x = self.frame.winfo_rootx() + 60
                pos_y = self.frame.winfo_rooty() + 200
        except Exception:
            pos_x, pos_y = 100, 100
        try:
            menu.tk_popup(pos_x, pos_y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _open_in_explorer(self, path: str):
        """用 Windows 资源管理器打开文件夹"""
        try:
            if os.path.isdir(path):
                os.startfile(path)          # 仅 Windows；本工具面向 Windows
                self._log(f"[打开文件夹] {self._format_display_path(path)}")
            else:
                self._log(f"[打开文件夹] 文件夹不存在：{self._format_display_path(path)}")
        except Exception as e:
            self._log(f"[打开文件夹] 打开失败 {self._format_display_path(path)}: {e}")

    def _browse_model_dir(self, var: tk.StringVar, model_name: str, parent,
                          on_done: Optional[Callable] = None):
        """选择某个翻译模型所在的文件夹"""
        try:
            directory = filedialog.askdirectory(
                title=f"选择“{model_name}”所在文件夹",
                parent=parent, initialdir=var.get().strip() or None)
        except Exception:
            directory = ''
        if directory:
            var.set(directory)
            if on_done:
                on_done()
            self._log(f"已设置模型 {model_name} 的文件夹: {directory}")

    # ========== 翻译：标题/系列/发行商/制作商/演员 的中日文切换 ==========
    def _on_translation_model_change(self, event=None):
        """切换翻译模型：已翻译时先恢复原文，由用户再点一次用新模型翻译"""
        name = self._current_translation_model()
        if self._translated:
            self._restore_original_texts()
            self._log(f"已切换翻译模型为 {name}，如需用新模型翻译请再次点击“翻译”")
        else:
            self._log(f"已切换翻译模型: {name}")
        self._refresh_model_buttons()   # 按钮状态跟随新模型的载入情况

    # ---------- 模型的载入 / 释放 ----------
    def _current_translator(self) -> Optional[LocalTranslationModel]:
        """当前选中模型对应的翻译器（尚未创建则为 None）"""
        return self._translators.get(self._current_translation_model())

    def _refresh_model_buttons(self, busy: bool = False, busy_kind: str = ''):
        """刷新“载入/释放”按钮：两者互斥，操作期间同时禁用以避免重复操作"""
        translator = self._current_translator()
        loaded = translator is not None and translator.is_loaded()
        load_btn = getattr(self, 'load_model_btn', None)
        unload_btn = getattr(self, 'unload_model_btn', None)
        try:
            if load_btn is not None:
                if busy and busy_kind == 'load':
                    load_btn.config(text="载入中...", state=tk.DISABLED)
                else:
                    load_btn.config(text="载入", state=tk.DISABLED if loaded else tk.NORMAL)
            if unload_btn is not None:
                if busy and busy_kind == 'unload':
                    unload_btn.config(text="释放中...", state=tk.DISABLED)
                else:
                    unload_btn.config(text="释放", state=tk.NORMAL if loaded else tk.DISABLED)
        except Exception:
            pass

    def _load_translation_model(self):
        """把选中的模型载入内存/显存（窗口打开时默认不加载）"""
        if self._model_busy:
            return
        model_name = self._current_translation_model()
        model_dir = self.translation_model_dirs.get(model_name, '')
        if not model_dir:
            messagebox.showwarning(
                "提示", f"请先在“本地设置”里为模型“{model_name}”选择所在文件夹")
            return
        self._model_busy = True
        self._refresh_model_buttons(True, 'load')
        self._log(f"翻译环境: {translation_env_report()}")
        self._log(f"正在载入模型 {model_name} ...")
        self._set_progress_ui(0, f"正在载入模型 {model_name}")
        threading.Thread(target=self._do_load_model,
                         args=(model_name, model_dir), daemon=True).start()

    def _do_load_model(self, model_name: str, model_dir: str):
        """后台线程：真正加载模型（耗时操作，不能阻塞界面）"""
        try:
            translator = self._get_translator(model_name, model_dir)
            translator.load()
            self._run_in_main_thread(lambda: self._on_model_loaded(model_name))
        except Exception as e:
            message = str(e)
            self._log(f"载入模型失败: {message}")
            self._run_in_main_thread(
                lambda: self._on_model_action_failed("载入模型失败", message))

    def _on_model_loaded(self, model_name: str):
        self._model_busy = False
        self._refresh_model_buttons()
        self._set_progress_ui(100, f"模型 {model_name} 已载入")
        self._log(f"模型 {model_name} 已载入内存，可以点击“翻译”")

    def _unload_translation_model(self):
        """释放已载入的模型，避免持续占用内存/显存"""
        if self._model_busy:
            return
        model_name = self._current_translation_model()
        translator = self._current_translator()
        if translator is None or not translator.is_loaded():
            self._refresh_model_buttons()
            return
        self._model_busy = True
        self._refresh_model_buttons(True, 'unload')
        self._log(f"正在释放模型 {model_name} ...")
        threading.Thread(target=self._do_unload_model,
                         args=(model_name, translator), daemon=True).start()

    def _do_unload_model(self, model_name: str, translator: LocalTranslationModel):
        """后台线程：释放模型资源"""
        try:
            translator.unload()
            self._run_in_main_thread(lambda: self._on_model_unloaded(model_name))
        except Exception as e:
            message = str(e)
            self._log(f"释放模型失败: {message}")
            self._run_in_main_thread(
                lambda: self._on_model_action_failed("释放模型失败", message))

    def _on_model_unloaded(self, model_name: str):
        self._model_busy = False
        self._refresh_model_buttons()
        self._set_progress_ui(0, f"模型 {model_name} 已释放")
        self._log(f"模型 {model_name} 已释放，不再占用内存/显存")

    def _on_model_action_failed(self, title: str, message: str):
        """载入/释放失败：复位按钮并提示"""
        self._model_busy = False
        self._refresh_model_buttons()
        self._set_progress_ui(0, title)
        try:
            messagebox.showerror(title, message)
        except Exception:
            pass

    def _toggle_translation(self):
        """翻译按钮：已翻译时恢复原文，否则调用本地模型翻译为中文"""
        if self._translating:
            return
        if self._translated:
            self._restore_original_texts()
            return

        source = self._collect_translate_source()
        if not any(str(v or '').strip() for v in source['fields'].values()) and not source['actors']:
            messagebox.showwarning("提示", "当前页签没有可翻译的标题/系列/发行商/制作商/演员")
            return

        # 优先用本地索引里的演员文件夹做模糊匹配：命中的直接取_folder里的中文名，不必再翻译
        original_actors = list(source['actors'])
        prematch = self._match_actors_by_index(original_actors)
        has_fields = any(str(v or '').strip() for v in source['fields'].values())
        source['original_actors'] = original_actors
        source['actor_prematch'] = prematch
        source['actors'] = [a for a in original_actors if a not in prematch]   # 只剩未命中的交给模型
        if prematch:
            self._log("演员索引命中：" + "、".join(
                f"{jp} -> {info.get('folder_name', '')}"
                for jp, info in prematch.items()))
            # 标题等字段都为空、且演员全部命中时，完全不需要调用模型
            if not has_fields and not source['actors']:
                self._apply_index_actors(original_actors, prematch)
                return

        model_name = self._current_translation_model()
        model_dir = self.translation_model_dirs.get(model_name, '')

        # 已有同模型的翻译缓存时直接复用，不再重复调用模型
        cached = self._translation_cache()
        if cached:
            cached_map: Dict[str, str] = {}
            for item in (cached['actors'] or []):
                jp, zh = self._split_actor_display(item)
                if jp:
                    cached_map[jp] = zh
            self._apply_translation(
                cached['fields'],
                self._compose_actors(original_actors, prematch, cached_map),
                cached=True)
            return

        if not model_dir:
            messagebox.showwarning(
                "提示", f"请先在“本地设置”里为模型“{model_name}”选择所在文件夹")
            return

        self._translating = True
        self._set_translate_button(True)
        self._log(f"开始用模型 {model_name} 翻译当前页签字段...")
        self._set_progress_ui(0, f"正在加载模型 {model_name}")
        threading.Thread(target=self._do_translate_fields,
                         args=(model_name, model_dir, source), daemon=True).start()

    # ---------- 翻译结果缓存（按页签 + 模型） ----------
    def _translation_cache(self) -> Optional[Dict[str, Any]]:
        """取当前页签已缓存的翻译结果（模型一致、且含字段时才有效）"""
        page = self.pages.get(self.active_url) or {}
        cached = page.get('translation') or {}
        if not isinstance(cached, dict) or not cached.get('fields'):
            return None
        if str(cached.get('model') or '') != self._current_translation_model():
            return None
        return cached

    def _save_translation_cache(self, model_name: str, fields: Dict[str, str],
                                actors: List[str]):
        """把翻译结果写入页签缓存；同时挂到页面记录上，便于随 JSON 一起导出"""
        page = self.pages.get(self.active_url)
        if page is None:
            return
        cached = {'model': model_name, 'fields': dict(fields), 'actors': list(actors)}
        page['translation'] = cached
        record = page.get('record')
        if isinstance(record, dict):
            record['translation'] = {
                'model': model_name,
                'title': str(fields.get('title') or ''),
                'series': str(fields.get('series') or ''),
                'publisher': str(fields.get('publisher') or ''),
                'maker': str(fields.get('maker') or ''),
                'actors': list(actors),
            }

    def _collect_translate_source(self) -> Dict[str, Any]:
        """取当前页签待翻译的原文（用缓存原始值，避免受标题显示截断影响）"""
        page = self.pages.get(self.active_url) or {}
        record = page.get('record') or {}
        actors = [str(a).strip() for a in (page.get('actors') or []) if str(a).strip()]
        if not actors:
            actors = [a.strip() for a in self.actors_var.get().split('、') if a.strip()]
        fields = {
            'title': str(page.get('title') or self.page_title_var.get() or ''),
            'series': str(record.get('series') or self.series_var.get() or ''),
            'publisher': str(record.get('publisher') or self.publisher_var.get() or ''),
            'maker': str(record.get('maker') or self.maker_var.get() or ''),
        }
        return {'fields': fields, 'actors': actors}

    # ---------- 演员名：优先用本地索引匹配（无需翻译模型） ----------
    @staticmethod
    def _split_actor_display(text: Any) -> Tuple[str, str]:
        """拆分用户看的「日文名(中文名)」显示串 -> (日文名, 中文名)"""
        value = str(text or '').strip()
        match = re.match(r'^(.+?)[（(](.*?)[）)]$', value)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return value, ''

    @staticmethod
    def _norm_actor_name(text: Any) -> str:
        """演员名归一化：去掉空白（含全角空格）并转小写，便于模糊比对"""
        value = str(text or '').strip()
        value = re.sub(r'[\s\u3000]+', '', value)
        return value.lower()

    def _match_single_actor(self, actor: str,
                            candidates: List[Tuple[str, str, str]]
                            ) -> Optional[Tuple[str, str]]:
        """单个演员名在索引中的模糊匹配：先归一化精确比对，再按包含关系兜底
        :return: (演员中文名, 命中的演员文件夹名)，未命中返回 None
        """
        target = self._norm_actor_name(actor)
        if not target:
            return None
        for jp, cn, folder in candidates:          # 1) 精确（归一化后相等）
            if target == self._norm_actor_name(jp):
                return (cn, folder)
        best, best_diff = None, None               # 2) 包含关系：取长度最接近的一个
        for jp, cn, folder in candidates:
            cand = self._norm_actor_name(jp)
            if len(cand) < 2 or len(target) < 2:
                continue
            if target in cand or cand in target:
                diff = abs(len(cand) - len(target))
                if best_diff is None or diff < best_diff:
                    best, best_diff = (cn, folder), diff
        return best

    def _actors_from_index(self) -> List[Tuple[str, str, str]]:
        """从索引里汇总全部「日文名, 中文名, 演员文件夹名」，含演员曾用名，去重"""
        records = self._peek_video_index()
        if not records:
            return []
        candidates: List[Tuple[str, str, str]] = []
        seen: Set[Tuple[str, str, str]] = set()
        for record in records:
            folder = str(record.get('folder_name') or '').strip()
            for item in (record.get('actor_names') or []):
                jp = str(item.get('japanese_name') or '').strip()
                cn = str(item.get('chinese_name') or '').strip()
                # 中文名可能为空（曾用名只写了日文名），只要有日文名即可参与匹配
                if not jp or not folder:
                    continue
                key = (jp, cn, folder)
                if key not in seen:
                    seen.add(key)
                    candidates.append(key)
        return candidates

    def _match_actors_by_index(self, actors: List[str]) -> Dict[str, Dict[str, str]]:
        """用本地索引中演员文件夹的名称做模糊匹配
        :return: {演员日文名: {'chinese_name': 中文名, 'folder_name': 演员文件夹名}}
        索引尚未建立时返回空字典（此时演员交给翻译模型处理）
        """
        if not actors:
            return {}
        candidates = self._actors_from_index()
        if not candidates:
            self._log("本地索引尚未建立（或其中无演员文件夹），演员将走模型翻译；"
                      "可点击“建立索引”先生成索引")
            return {}
        matched: Dict[str, Dict[str, str]] = {}
        for actor in actors:
            hit = self._match_single_actor(actor, candidates)
            if hit:
                cn, folder = hit
                matched[actor] = {'chinese_name': cn, 'folder_name': folder}
        return matched

    def _match_actor_folders(self, actors: List[str],
                             records: Optional[List[Dict[str, Any]]]
                             ) -> List[Dict[str, str]]:
        """用索引中的「演员文件夹」名称匹配演员名，返回命中项（含文件夹完整路径）

        与「翻译」按钮共用同一套模糊匹配策略（归一化精确 → 包含关系取长度最接近），
        因此当番号匹配不到时，只要演员名能命中演员文件夹，也能在检索结果中列出。

        :return: [{'actor': 视频演员名, 'japanese_name':.., 'chinese_name':..,
                   'folder_name':.., 'file_path': 文件夹完整路径}]
        """
        if not actors or not records:
            return []

        # 汇总候选：(日文名, 中文名, 文件夹名, 文件夹完整路径)
        candidates: List[Tuple[str, str, str, str]] = []
        seen: Set[Tuple[str, str, str, str]] = set()
        for record in records:
            folder = str(record.get('folder_name') or '').strip()
            path = str(record.get('file_path') or '').strip()
            if not folder or not path:
                continue
            # 演员文件夹本身，或其所属演员（由父目录推导、挂在 record 上的演员名）
            # 都参与匹配，使位于演员文件夹内的视频文件夹也能按演员名命中
            for item in (record.get('actor_names') or []):
                jp = str(item.get('japanese_name') or '').strip()
                cn = str(item.get('chinese_name') or '').strip()
                if not jp:      # 中文名可为空（曾用名只写日文名），有日文名即可
                    continue
                key = (jp, cn, folder, path)
                if key not in seen:
                    seen.add(key)
                    candidates.append(key)
        if not candidates:
            return []

        hits: List[Dict[str, str]] = []
        used_paths: Set[str] = set()      # 每个演员文件夹只归给一个演员，避免重复列出
        for actor in actors:
            target = self._norm_actor_name(actor)
            if not target:
                continue
            best: Optional[Tuple[str, str, str, str]] = None
            best_diff: Optional[int] = None
            for jp, cn, folder, path in candidates:
                if path in used_paths:
                    continue
                names = (self._norm_actor_name(jp), self._norm_actor_name(cn))
                if target in names:                       # 1) 归一化后精确命中
                    best, best_diff = (jp, cn, folder, path), -1
                    break
                for cand in names:                        # 2) 包含关系：取长度最接近
                    if len(cand) < 2 or len(target) < 2:
                        continue
                    if target in cand or cand in target:
                        diff = abs(len(cand) - len(target))
                        if best_diff is None or diff < best_diff:
                            best, best_diff = (jp, cn, folder, path), diff
            if best:
                used_paths.add(best[3])
                hits.append({
                    'actor': str(actor),
                    'japanese_name': best[0],
                    'chinese_name': best[1],
                    'folder_name': best[2],
                    'file_path': best[3],
                })
        return hits

    def _compose_actors(self, original_actors: List[str],
                        prematch: Dict[str, Dict[str, str]],
                        translated: Dict[str, str]) -> List[str]:
        """按原始顺序合成最终演员显示名：
        索引命中者原样填入【完整的演员文件夹名】（带曾用名时也一并带上），
        未命中的用模型翻译结果，格式「日文名(中文名)」
        """
        result: List[str] = []
        for jp in original_actors:
            hit = prematch.get(jp) or {}
            folder = str(hit.get('folder_name') or '').strip()
            if folder:
                result.append(folder)      # 原样写入整个演员文件夹名
                continue
            zh = (translated.get(jp) or '').strip()
            result.append(f"{jp}({zh})" if zh and zh != jp else jp)
        return result

    def _apply_index_actors(self, original_actors: List[str],
                            prematch: Dict[str, str]):
        """演员全部由本地索引命中：直接写入文本框，不调用翻译模型"""
        self._translation_originals = {
            'title': self.page_title_var.get(),
            'series': self.series_var.get(),
            'publisher': self.publisher_var.get(),
            'maker': self.maker_var.get(),
            'actors': self.actors_var.get(),
        }
        self._translated_full_title = ''
        self.actors_var.set('、'.join(
            self._compose_actors(original_actors, prematch, {})))
        self._translated = True
        self._translating = False
        self._set_translate_button(False)
        self._set_progress_ui(100, "演员已由本地索引匹配（再次点击“翻译”恢复原文）")
        self._log("演员由本地索引匹配完成，未调用翻译模型")

    def _do_translate_fields(self, model_name: str, model_dir: str, source: Dict[str, Any]):
        """后台线程：加载模型并逐字段翻译（进度实时反映到“处理进度”）"""
        try:
            translator = self._get_translator(model_name, model_dir)
            self._run_in_main_thread(
                lambda: self._set_progress_ui(10, f"模型 {model_name} 已就绪，开始翻译"))

            total = max(1, len(source['fields']) + len(source['actors']))
            done = 0
            translated: Dict[str, str] = {}
            for key, text in source['fields'].items():
                text = str(text or '').strip()
                translated[key] = translator.translate(text) if text else ''
                done += 1
                self._run_in_main_thread(
                    lambda d=done, t=total:
                    self._set_progress_ui(10 + d / t * 85, f"正在翻译 {d}/{t}"))

            # 演员：只翻译索引未命中的，命中者已在前置步骤取到中文名
            actor_map: Dict[str, str] = {}
            for name in source['actors']:
                actor_map[name] = translator.translate(name)
                done += 1
                self._run_in_main_thread(
                    lambda d=done, t=total:
                    self._set_progress_ui(10 + d / t * 85, f"正在翻译 {d}/{t}"))

            actors_out = self._compose_actors(
                source.get('original_actors') or [],
                source.get('actor_prematch') or {},
                actor_map)
            self._run_in_main_thread(lambda: self._apply_translation(translated, actors_out))
        except Exception as e:
            message = str(e)
            self._log(f"翻译失败: {message}")
            self._run_in_main_thread(lambda: self._on_translation_failed(message))

    def _get_translator(self, model_name: str, model_dir: str) -> LocalTranslationModel:
        """按模型名取（必要时重建）翻译器实例"""
        translator = self._translators.get(model_name)
        if translator is None or translator.model_dir != model_dir:
            translator = LocalTranslationModel(model_name, model_dir)
            self._translators[model_name] = translator
        return translator

    def _apply_translation(self, fields: Dict[str, str], actors: List[str],
                           cached: bool = False):
        """把翻译结果写回界面（主线程）；cached=True 表示来自缓存、未调用模型"""
        self._translation_originals = {
            'title': self.page_title_var.get(),
            'series': self.series_var.get(),
            'publisher': self.publisher_var.get(),
            'maker': self.maker_var.get(),
            'actors': self.actors_var.get(),
        }
        origin = self._translation_originals
        self._translated_full_title = str(fields.get('title') or '')
        self.page_title_var.set(_format_title_display(fields.get('title') or origin['title']))
        self.series_var.set(fields.get('series') or origin['series'])
        self.publisher_var.set(fields.get('publisher') or origin['publisher'])
        self.maker_var.set(fields.get('maker') or origin['maker'])
        if actors:
            self.actors_var.set('、'.join(actors))
        self._translated = True
        self._translating = False
        self._set_translate_button(False)
        if not cached:
            self._save_translation_cache(self._current_translation_model(), fields, actors)
        self._refresh_model_buttons()   # 翻译会自动载入模型，同步按钮状态
        if cached:
            self._set_progress_ui(100, "已读取缓存的翻译结果（再次点击“翻译”恢复原文）")
            self._log("已读取缓存的翻译结果，未重复调用模型")
        else:
            self._set_progress_ui(100, "翻译完成（再次点击“翻译”恢复原文）")
            self._log("翻译完成：标题/系列/发行商/制作商/演员已切换为中文显示")

    def _restore_original_texts(self):
        """恢复为原文显示"""
        originals = self._translation_originals or {}
        self._translated_full_title = ''
        if originals:
            self.page_title_var.set(originals.get('title', ''))
            self.series_var.set(originals.get('series', ''))
            self.publisher_var.set(originals.get('publisher', ''))
            self.maker_var.set(originals.get('maker', ''))
            self.actors_var.set(originals.get('actors', ''))
            self._log("已恢复原文显示")
        self._translated = False
        self._translation_originals = {}
        self._translated_full_title = ''
        self._translating = False
        self._set_translate_button(False)

    def _on_translation_failed(self, message: str):
        """翻译失败：复位按钮并提示"""
        self._translating = False
        self._translated = False
        self._set_translate_button(False)
        self._refresh_model_buttons()
        self._set_progress_ui(0, "翻译失败")
        try:
            messagebox.showerror("翻译失败", message)
        except Exception:
            pass

    def _set_translate_button(self, busy: bool):
        """刷新翻译按钮：进行中置灰并提示“翻译中...”"""
        btn = getattr(self, 'translate_btn', None)
        if btn is None:
            return
        try:
            if busy:
                btn.config(text="翻译中...", state=tk.DISABLED)
            else:
                btn.config(text="恢复原文" if self._translated else "翻译", state=tk.NORMAL)
        except Exception:
            pass

    # ---------- 退出清理：先释放模型再关闭 ----------
    def release_all_models(self, log: bool = True) -> List[str]:
        """释放所有已载入的翻译模型，返回被释放的模型名"""
        released: List[str] = []
        for name, translator in list(self._translators.items()):
            try:
                if translator is not None and translator.is_loaded():
                    translator.unload()
                    released.append(name)
            except Exception:
                pass
        self._model_busy = False
        try:
            self._refresh_model_buttons()
        except Exception:
            pass
        if released and log:
            self._log(f"已释放翻译模型：{', '.join(released)}")
        return released

    def cleanup(self, log: bool = True):
        """关闭页签/主窗口前的统一清理：先释放模型，再关闭浮窗层"""
        try:
            self.release_all_models(log=log)
        except Exception:
            pass
        # 停掉主线程任务轮询器与抓取刷新循环，避免窗口销毁后 after 回调报错
        self._main_task_poller_running = False
        self.is_running = False
        after_id = getattr(self, '_main_task_after_id', None)
        if after_id:
            try:
                self.frame.after_cancel(after_id)
            except Exception:
                pass
            self._main_task_after_id = None
        # 销毁阶段控件已不可读：禁止“关闭设置层时补存规则”，
        # 否则会把字段规则整体写空（曾导致所有字段抓取失效）
        self._skip_field_rules_save = True
        for close in (self._close_local_folder_settings,
                      self._close_crawler_settings,
                      self._close_image_viewer,
                      self._close_page_browser,
                      self._hide_save_dir_history):
            try:
                close(destroy=True) if close is self._close_page_browser else close()
            except Exception:
                pass
            try:
                close()
            except Exception:
                pass

    def _on_frame_destroy(self, event=None):
        """页签被销毁时兜底释放模型（避免软件已关闭但模型仍占用显存）"""
        try:
            if event is not None and event.widget is not self.frame:
                return
        except Exception:
            pass
        # 销毁阶段 Tk 可能已不可用，不再写日志
        self.cleanup(log=False)

    def _reset_translation_state(self):
        """切换/关闭页签或清空结果时回到原文显示状态"""
        self._translated = False
        self._translation_originals = {}
        self._translated_full_title = ''
        self._translating = False
        self._set_translate_button(False)

    # ========== 完整标题（点击标题行写入日志） ==========
    def _full_title_text(self) -> str:
        """当前应显示的完整标题：已翻译时用完整译名，否则用完整原文"""
        if self._translated and self._translated_full_title:
            return _format_title_display(self._translated_full_title, max_len=0)
        page = self.pages.get(self.active_url) or {}
        raw = str(page.get('title') or self.page_title_var.get() or '')
        return _format_title_display(raw, max_len=0)

    def _log_full_title(self, event=None):
        """点击标题：把完整标题写入操作日志（已翻译则输出完整译名）"""
        text = self._full_title_text()
        if not text:
            self._log("当前页签没有标题")
            return
        tag = "完整标题（中文翻译）" if self._translated else "完整标题（原文）"
        self._log(f"{tag}：{text}")

    def _build_crawler_settings(self, parent):
        """构建爬虫与内容容器的设置项"""
        # 行1：数量上限 / 自动下载
        row1 = ttk.Frame(parent)
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="数量上限(0=不限):").pack(side=tk.LEFT)
        ttk.Spinbox(row1, from_=0, to=200, textvariable=self.max_images_var,
                    width=5).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(row1, text="自动下载",
                        variable=self.auto_download_var).pack(side=tk.LEFT, padx=12)
        ttk.Checkbutton(row1, text="抓取后自动保存JSON",
                        variable=self.auto_save_json_var,
                        command=self._save_selector_config).pack(side=tk.LEFT, padx=12)

        # 行2：保存路径
        row2 = ttk.Frame(parent)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="保存路径:").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.save_path_var, width=30).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row2, text="浏览...", command=self._browse_save_path,
                   width=8).pack(side=tk.RIGHT)

        # 图片容器
        image_box = ttk.LabelFrame(parent, text="图片容器（均在内容A body > div.container 内查找）",
                                   padding=8)
        image_box.pack(fill=tk.X, pady=6)

        for label_text, var, reset_cmd in (
            ("主图容器:", self.main_selector_var, self._reset_main_selector),
            ("剪辑图容器:", self.sample_selector_var, self._reset_sample_selector),
            ("剪辑图链接:", self.sample_link_var, self._reset_sample_link),
        ):
            row = ttk.Frame(image_box)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label_text, width=11).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            ttk.Button(row, text="默认", command=reset_cmd, width=6).pack(side=tk.RIGHT)

        ttk.Label(image_box, text="主图只取 1 张并排在第 1 位；剪辑图取 a 链接的大图，数量不限；"
                                  "其余容器的图片不再抓取。",
                  foreground="gray", font=('', 8), wraplength=560,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        # 磁力链
        magnet_box = ttk.LabelFrame(parent, text="磁力链（表格由 JS 异步生成）", padding=8)
        magnet_box.pack(fill=tk.X, pady=6)

        row_m1 = ttk.Frame(magnet_box)
        row_m1.pack(fill=tk.X, pady=2)
        ttk.Label(row_m1, text="磁力表:", width=11).pack(side=tk.LEFT)
        ttk.Entry(row_m1, textvariable=self.magnet_selector_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row_m1, text="默认", command=self._reset_magnet_selector,
                   width=6).pack(side=tk.RIGHT)

        ttk.Checkbutton(magnet_box, text="磁力表未取到时自动用浏览器渲染重试（等待 JS 生成）",
                        variable=self.auto_render_var,
                        command=self._save_selector_config).pack(anchor=tk.W, pady=2)

        row_m2 = ttk.Frame(magnet_box)
        row_m2.pack(fill=tk.X, pady=2)
        ttk.Label(row_m2, text="磁力接口:", width=11).pack(side=tk.LEFT)
        ttk.Entry(row_m2, textvariable=self.magnet_ajax_path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(row_m2, text="默认", command=self._reset_magnet_ajax,
                   width=6).pack(side=tk.RIGHT)

        row_m3 = ttk.Frame(magnet_box)
        row_m3.pack(fill=tk.X, pady=2)
        ttk.Label(row_m3, text="接口参数:", width=11).pack(side=tk.LEFT)
        ttk.Entry(row_m3, textvariable=self.magnet_ajax_params_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Checkbutton(magnet_box, text="启用磁力链异步接口（表为空时直接请求接口取数据）",
                        variable=self.magnet_ajax_enabled_var,
                        command=self._save_selector_config).pack(anchor=tk.W, pady=2)

        ttk.Label(magnet_box, text="参数模板里的 {xxx} 取页面脚本 var xxx 的值，{floor} 为随机数；"
                                   "配置保存在 app_config.json 的 web_grab.* 各键。",
                  foreground="gray", font=('', 8), wraplength=560,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        # 字段抓取规则
        self._build_field_rules_section(parent)

    # ========== 列表页设置（爬虫设置 → “视频列表页”页签） ==========
    def _make_inner_scroll_box(self, parent, height: int = 180):
        """在 parent 内创建一个固定高度的带滚动条区域，返回 (内容框架, 画布)

        用于把较高的内容（如列表解析规则的十余行选择器）收到一个固定高度的小框中，
        避免整个设置窗口被撑高、下面的按钮被挤出可视区域。
        """
        outer = ttk.Frame(parent)
        outer.pack(fill=tk.X, expand=False)

        canvas = tk.Canvas(outer, height=height, highlightthickness=1,
                           highlightbackground='#c0c0c0', background='#fbfbfb')
        bar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas, padding=6)
        window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def on_canvas_resize(event):
            try:
                canvas.itemconfigure(window, width=event.width)
                canvas.configure(scrollregion=canvas.bbox('all'))
            except tk.TclError:
                pass

        canvas.bind('<Configure>', on_canvas_resize)
        inner.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        self._bind_mousewheel(canvas, canvas)
        return inner, canvas

    def _build_list_settings(self, parent):
        """构建“视频列表页”爬虫设置：网址库 + 列表解析规则

        布局（自上而下）：说明 → 网址库 → 列表解析规则（选择器放在固定高度的滚动框中）
        → 选项与按钮（始终可见）→ 状态行。
        """
        ttk.Label(parent, text="列表页（瀑布流）配置：用于抓取 div#waterfall 下的 div.movie-box 视频条目；"
                               "选择器均相对【单个视频条目】执行，写法可用 “css@属性名” 取属性值。",
                  foreground="#666666", wraplength=820, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(0, 6))

        # ---------- 网址库 ----------
        library_box = ttk.LabelFrame(parent, text="网址库（列表页地址，可连接测试）", padding=8)
        library_box.pack(fill=tk.X, pady=(0, 6))

        lb_frame = ttk.Frame(library_box)
        lb_frame.pack(fill=tk.X)
        self.list_library_listbox = tk.Listbox(lb_frame, height=4, selectmode=tk.EXTENDED,
                                               activestyle='none', font=('Microsoft YaHei', 9))
        lb_scroll = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL,
                                  command=self.list_library_listbox.yview)
        self.list_library_listbox.configure(yscrollcommand=lb_scroll.set)
        self.list_library_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_library_listbox.bind('<Double-Button-1>', lambda e: self._list_library_edit())

        btn_row = ttk.Frame(library_box)
        btn_row.pack(fill=tk.X, pady=(6, 2))
        ttk.Button(btn_row, text="添加网址", width=10,
                   command=self._list_library_add).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="编辑", width=8,
                   command=self._list_library_edit).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="删除", width=8,
                   command=self._list_library_remove).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="连接测试(选中)", width=14,
                   command=lambda: self._list_library_test(selected_only=True)).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="连接测试(全部)", width=14,
                   command=lambda: self._list_library_test(selected_only=False)).pack(
            side=tk.LEFT, padx=2)

        self.list_library_status_var = tk.StringVar(value="")
        ttk.Label(library_box, textvariable=self.list_library_status_var,
                  foreground="#0066cc", wraplength=820, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(2, 0))
        ttk.Label(library_box,
                  text="抓取列表页时会自动把公告层（div.alert.alert-info...）中的网址补充到本网址库；"
                       "手动添加的网址会自动生成一个列表页页签。"
                       "抓取时默认使用“当前可用入口”（可连接即一直复用），"
                       "该入口失效时自动切换到网址库中的下一个可连接入口并重试"
                       "（列表页与主窗口详情页抓取通用）。"
                       "视频列表层里可用页签区分“全部列表 / 演员作品 / 系列作品”，"
                       "每个页签分别保存自己的网址与当前页码。",
                  foreground="gray", font=('', 8), wraplength=820,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        # ---------- 列表解析规则（选择器放进固定高度的滚动框） ----------
        rule_box = ttk.LabelFrame(parent, text="列表解析规则（在框内滚动查看全部规则）", padding=8)
        rule_box.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        rules_area, rules_canvas = self._make_inner_scroll_box(rule_box, height=176)

        rule_rows = (
            ("列表容器:", self.list_container_var, 'container'),
            ("视频条目:", self.list_item_var, 'item'),
            ("缩略图:", self.list_thumb_var, 'thumb'),
            ("标题:", self.list_title_var, 'title'),
            ("番号:", self.list_code_var, 'code'),
            ("发布日期:", self.list_date_var, 'date'),
            ("视频标志:", self.list_flag_var, 'flag'),
            ("详情链接:", self.list_link_var, 'link'),
            ("下一页:", self.list_next_var, 'next'),
            ("公告层:", self.list_alert_var, 'alert'),
            ("页码模板:", self.list_page_template_var, 'page_template'),
            ("搜索地址:", self.list_search_path_var, 'search_path'),
        )
        for label_text, var, key in rule_rows:
            row = ttk.Frame(rules_area)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label_text, width=11).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            ttk.Button(row, text="默认", width=6,
                       command=lambda k=key: self._reset_list_selector(k)).pack(side=tk.RIGHT)

        ttk.Label(rules_area,
                  text="说明：番号/发布日期会按内容特征自动挑选（番号形如 YAKO-094，"
                       "日期形如 2026-06-12），因此两个字段可以填同一组候选选择器；"
                       "标志列会取出所有匹配文本（如“高清 字幕”）。"
                       "常用取值：div.photo-frame img@src（缩略图）、div.photo-frame img@title（标题）、"
                       "div.photo-info date, div.photo-info span（番号/日期候选）、"
                       "div.item-tag button（视频标志）、a@href（详情链接）。",
                  foreground="gray", font=('', 8), wraplength=780,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

        # 让选择器行内的控件也能驱动滚动框滚动
        self._bind_mousewheel_tree(rules_area, rules_canvas)

        # ---------- 选项与操作（固定显示在滚动框下方） ----------
        opt_row = ttk.Frame(rule_box)
        opt_row.pack(fill=tk.X, pady=(4, 2))
        ttk.Checkbutton(opt_row, text="抓取列表页时自动把公告层网址补充到网址库",
                        variable=self.list_auto_collect_var,
                        command=self._save_list_config).pack(side=tk.LEFT)
        ttk.Checkbutton(opt_row, text="用浏览器渲染后抓取列表页（部分站点列表由 JS 生成）",
                        variable=self.list_render_var,
                        command=self._save_list_config).pack(side=tk.LEFT, padx=12)

        act_row = ttk.Frame(rule_box)
        act_row.pack(fill=tk.X, pady=(4, 2))
        ttk.Button(act_row, text="测试解析（用网址库第一条）", width=24,
                   command=self._list_test_parse).pack(side=tk.LEFT, padx=2)
        ttk.Label(act_row, text="页码模板中的 {page} 会被替换为页码，用于翻页；"
                                "例如 https://www.busfan.cyou/page/{page}",
                  foreground="gray", font=('', 8)).pack(side=tk.LEFT, padx=8)

        # 测试解析的状态显示（在窗口内提示，不弹中断式对话框）
        self.list_rule_status_var = tk.StringVar(value="")
        ttk.Label(rule_box, textvariable=self.list_rule_status_var,
                  foreground="#0066cc", wraplength=820,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        # 载入已有网址库内容（否则列表为空，看不到已添加的网址）
        self._refresh_list_library()

    def _reset_list_selector(self, key: str):
        """把列表页某个选择器恢复为默认值"""
        mapping = {
            'container': DEFAULT_LIST_CONTAINER_SELECTOR,
            'item': DEFAULT_LIST_ITEM_SELECTOR,
            'thumb': DEFAULT_LIST_THUMB_SELECTOR,
            'title': DEFAULT_LIST_TITLE_SELECTOR,
            'code': DEFAULT_LIST_CODE_SELECTOR,
            'date': DEFAULT_LIST_DATE_SELECTOR,
            'flag': DEFAULT_LIST_FLAG_SELECTOR,
            'link': DEFAULT_LIST_LINK_SELECTOR,
            'next': DEFAULT_LIST_NEXT_SELECTOR,
            'alert': DEFAULT_LIST_ALERT_SELECTOR,
            'page_template': DEFAULT_LIST_PAGE_TEMPLATE,
            'search_path': DEFAULT_LIST_SEARCH_PATH,
        }
        vars_map = {
            'container': getattr(self, 'list_container_var', None),
            'item': getattr(self, 'list_item_var', None),
            'thumb': getattr(self, 'list_thumb_var', None),
            'title': getattr(self, 'list_title_var', None),
            'code': getattr(self, 'list_code_var', None),
            'date': getattr(self, 'list_date_var', None),
            'flag': getattr(self, 'list_flag_var', None),
            'link': getattr(self, 'list_link_var', None),
            'next': getattr(self, 'list_next_var', None),
            'alert': getattr(self, 'list_alert_var', None),
            'page_template': getattr(self, 'list_page_template_var', None),
        }
        var = vars_map.get(key)
        if var is not None and key in mapping:
            var.set(mapping[key])
            self._save_list_config()

    # ---------- 网址库 ----------
    def _load_list_urls(self) -> List[str]:
        """读取网址库（列表页地址集合）"""
        try:
            value = self.config_manager.get(self.CONFIG_SECTION, 'list_url_library', []) or []
            if not isinstance(value, list):
                return []
            return [str(u).strip() for u in value if str(u).strip()]
        except Exception:
            return []

    def _refresh_list_library(self):
        """把网址库内容刷新到设置窗口的列表框中（窗口未打开时静默跳过）"""
        listbox = getattr(self, 'list_library_listbox', None)
        if listbox is None:
            return
        try:
            if not listbox.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            listbox.delete(0, tk.END)
            for url in self._list_urls:
                listbox.insert(tk.END, url)
        except tk.TclError:
            pass

    def _save_list_urls(self):
        """写入网址库并刷新界面控件"""
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'list_url_library', list(self._list_urls))
        except Exception:
            pass
        self._refresh_list_library()
        self._refresh_list_url_combo()

    # ---------- 网址库入口自动切换（列表页与详情页抓取通用） ----------
    def _host_candidates(self) -> List[str]:
        """网址库中的所有站点入口（去重，忽略路径）"""
        hosts: List[str] = []
        for url in list(getattr(self, '_list_urls', []) or []):
            host = self._host_of(url)
            if host and host not in hosts:
                hosts.append(host)
        for tab in list(getattr(self, 'list_tabs', []) or []):
            host = self._host_of(str(tab.get('url') or ''))
            if host and host not in hosts:
                hosts.append(host)
        return hosts

    def _set_active_host(self, host: str):
        """记住当前正在使用的网址库入口（下次抓取默认沿用）"""
        host = (host or '').strip()
        if not host or host == self._list_active_host:
            return
        self._list_active_host = host
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'list_active_host', host)
        except Exception:
            pass
        self._log(f"[网址库] 当前入口切换为: {host}")

    def _list_pick_host(self, url: str = '') -> str:
        """取出本次抓取应使用的入口：已记住的入口直接复用（不重复探测）

        尚未记住入口时，才按网址库顺序探测，取第一个可连接的作为当前入口。
        """
        hosts = self._host_candidates()
        if not hosts:
            return ''

        active = str(getattr(self, '_list_active_host', '') or '').strip()
        if active in hosts:
            return active

        current_host = self._host_of(url)
        order = ([current_host] if current_host in hosts else []) + \
                [h for h in hosts if h != current_host]
        for host in order[:LIST_HOST_MAX_TRIES]:
            ok, message = self._probe_list_url(host + '/')
            self._log(f"[网址库] 连接测试 {host} -> {message}")
            if ok:
                self._set_active_host(host)
                return host
        return ''

    def _list_switch_host(self, exclude: Optional[str] = None) -> str:
        """当前入口不可用时，切换到网址库中的下一个可连接入口"""
        hosts = self._host_candidates()
        if not hosts:
            return ''
        exclude = (exclude or '').strip() or str(getattr(self, '_list_active_host', '') or '')
        for host in hosts:
            if host == exclude:
                continue
            ok, message = self._probe_list_url(host + '/')
            self._log(f"[网址库] 尝试切换入口 {host} -> {message}")
            if ok:
                self._set_active_host(host)
                return host
        return ''

    def _resolve_grab_url(self, url: str) -> str:
        """抓取前把地址切到网址库中当前可用的入口（后台线程调用）"""
        host = self._list_pick_host(url)
        if not host:
            return url
        return self._apply_host(url, host)

    def _retry_with_next_host(self, url: str, message: str = ''):
        """详情页抓取失败：自动切换到网址库中的下一个可连接入口并重抓一次"""
        url = self._canonical_page_url(url)
        if url in self._host_retried:
            return False
        if len(self._host_candidates()) <= 1:
            return False
        self._host_retried.add(url)

        def worker():
            next_host = self._list_switch_host()
            if not next_host:
                self._log(f"[{self._tab_name(url)}] 网址库中无可用的其它入口，放弃重试")
                return
            self._log(f"[{self._tab_name(url)}] 入口不可用，已切换到 {next_host} 重新抓取")
            # 回到主线程重抓（结果仍路由回同一个页签）
            self._run_in_main_thread(lambda u=url: self._start_single_grab(u))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _list_tab_open_url(self, url: str, name: Optional[str] = None) -> bool:
        """在列表页浏览层新建（或复用）一个页签并打开指定网址"""
        url = self._normalize_url(url)
        if not url:
            return False

        index = next((i for i, tab in enumerate(self.list_tabs)
                      if str(tab.get('url') or '') == url), None)
        if index is None:
            self.list_tabs.append({
                'name': name or self._list_tab_default_name(url),
                'url': url,
                'page': self._list_page_from_url(url) or 1,
            })
            index = len(self.list_tabs) - 1
            self._save_list_tabs()

        self._open_page_browser()      # 层未打开时先打开（已打开则仅置顶）
        self._list_tab_select(index)
        return True

    def _list_do_search(self, keyword: Optional[str] = None):
        """执行列表层搜索：在新建页签中打开搜索结果页"""
        keyword = (keyword if keyword is not None
                   else self._list_search_var.get()).strip()
        if not keyword:
            messagebox.showinfo("提示", "请输入番号、影片名或演员名等搜索关键词")
            return

        base_url = self.list_url_entry_var.get().strip() or self._host_of(
            str(self._list_active_tab_dict().get('url') or '') if self._list_active_tab_dict()
            else '') or str(getattr(self, '_list_active_host', '') or '')
        if not base_url:
            messagebox.showinfo("提示", "请先打开一个列表页，再进行搜索")
            return

        path = str(self._list_search_info.get('path') or '').strip()
        url = build_search_url(base_url, keyword, path,
                               (self.list_search_path_var.get()
                                if hasattr(self, 'list_search_path_var') else '')
                               or DEFAULT_LIST_SEARCH_PATH)
        if not url:
            return

        self._log(f"[列表页] 搜索「{keyword}」→ {url}")
        self._list_tab_open_url(url, name=f"搜索-{keyword}")
        self._list_status_var.set(f"搜索「{keyword}」：{url}")

    def search_list_by_keyword(self, keyword: str) -> bool:
        """供其它模块调用：在“视频列表层”以 keyword 为搜索关键字新建一个搜索页签。

        不会切换主页面签；若列表层尚未打开会自动从右侧滑出。返回是否成功创建
        （无可用基础网址时返回 False，由调用方自行提示用户）。
        """
        keyword = (keyword or '').strip()
        if not keyword:
            return False
        base_url = (self.list_url_entry_var.get().strip()
                    or self._host_of(
                        str(self._list_active_tab_dict().get('url') or '')
                        if self._list_active_tab_dict() else '')
                    or str(getattr(self, '_list_active_host', '') or ''))
        if not base_url:
            return False
        path = str(self._list_search_info.get('path') or '').strip()
        url = build_search_url(base_url, keyword, path,
                               (self.list_search_path_var.get()
                                if hasattr(self, 'list_search_path_var') else '')
                               or DEFAULT_LIST_SEARCH_PATH)
        if not url:
            return False
        self._log(f"[列表页] 搜索「{keyword}」→ {url}")
        self._list_tab_open_url(url, name=f"搜索-{keyword}")
        self._list_status_var.set(f"搜索「{keyword}」：{url}")
        return True

    # ---------- 列表页页签（每个页签配套保存“网址 + 页码”） ----------
    @staticmethod
    def _list_tab_default_name(url: str) -> str:
        """由网址生成默认页签名：/page/173 → “列表 173”，其余取地址末段"""
        m = re.search(r'/page/(\d+)', url or '')
        if m:
            return f"列表 {m.group(1)}"
        try:
            parsed = urllib.parse.urlparse(url or '')
            name = (parsed.path or '').rstrip('/').split('/')[-1] or parsed.netloc
            return name or '页签'
        except Exception:
            return '页签'

    def _load_list_tabs(self) -> List[Dict]:
        """读取列表页页签；旧配置（网址库 + 上次地址）会自动迁移成页签"""
        tabs: List[Dict] = []
        try:
            value = self.config_manager.get(self.CONFIG_SECTION, 'list_tabs', None)
        except Exception:
            value = None

        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                url = self._normalize_url(str(item.get('url') or ''))
                if not url:
                    continue
                tabs.append({
                    'name': (str(item.get('name') or '').strip()
                             or self._list_tab_default_name(url)),
                    'url': url,
                    'page': int(item.get('page') or self._list_page_from_url(url) or 1),
                })

        if not tabs:
            # 兼容旧配置：把网址库（以及上次浏览的地址）迁移为页签
            urls = list(self._load_list_urls())
            last_url = self._normalize_url(
                str(self.config_manager.get(self.CONFIG_SECTION, 'list_last_url', '') or ''))
            if last_url and last_url not in urls:
                urls.insert(0, last_url)
            last_page = self._list_page_from_url(last_url) if last_url else None
            for url in urls:
                tabs.append({
                    'name': self._list_tab_default_name(url),
                    'url': url,
                    'page': (last_page if url == last_url else None)
                            or self._list_page_from_url(url) or 1,
                })

        if not tabs:
            template = (str(self.config_manager.get(
                self.CONFIG_SECTION, 'list_page_template', '') or '')
                .replace('{page}', '1'))
            tabs.append({'name': '全部列表', 'url': self._normalize_url(template), 'page': 1})

        return tabs

    def _load_active_tab_index(self) -> int:
        """读取上次活动的页签序号（越界则回退 0）"""
        try:
            index = int(self.config_manager.get(self.CONFIG_SECTION, 'list_active_tab', 0) or 0)
        except Exception:
            index = 0
        if not self.list_tabs:
            return 0
        return max(0, min(index, len(self.list_tabs) - 1))

    def _save_list_tabs(self):
        """保存页签（名称 / 网址 / 页码）与当前活动页签序号"""
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'list_tabs',
                                    [dict(t) for t in self.list_tabs])
            self.config_manager.set(self.CONFIG_SECTION, 'list_active_tab',
                                    int(self._list_active_tab))
        except Exception:
            pass
        self._refresh_list_url_combo()

    def _list_active_tab_dict(self) -> Optional[Dict]:
        """当前页签（不存在时返回 None）"""
        if not self.list_tabs:
            return None
        index = max(0, min(self._list_active_tab, len(self.list_tabs) - 1))
        return self.list_tabs[index]

    def _list_tab_display(self, tab: Dict) -> str:
        """页签显示文字：名称 + 当前页码"""
        name = str(tab.get('name') or '').strip() or self._list_tab_default_name(
            str(tab.get('url') or ''))
        page = tab.get('page')
        return f"{name}  P{page}" if page else name

    def _render_list_tabs(self):
        """重建列表页层的页签栏"""
        inner = getattr(self, '_list_tabs_inner', None)
        if inner is None:
            return
        try:
            if not inner.winfo_exists():
                return
        except tk.TclError:
            return

        for child in inner.winfo_children():
            child.destroy()
        self._list_tab_widgets = []

        for index, tab in enumerate(self.list_tabs):
            active = (index == self._list_active_tab)
            bg = '#4a90d9' if active else '#e0e0e0'
            fg = '#ffffff' if active else '#333333'

            frame = tk.Frame(inner, bd=1, relief=tk.SOLID, background=bg)
            frame.pack(side=tk.LEFT, padx=2, pady=2)
            label = tk.Label(frame, text=self._list_tab_display(tab), padx=8, pady=2,
                             background=bg, foreground=fg, cursor='hand2',
                             font=('Microsoft YaHei', 9))
            label.pack(side=tk.LEFT)

            widgets = {'frame': frame, 'label': label}
            # 至少保留一个页签，多于一个时才显示关闭按钮
            if len(self.list_tabs) > 1:
                close = tk.Label(frame, text='×', padx=5, pady=2, background=bg,
                                 foreground='#666666', cursor='hand2')
                close.pack(side=tk.LEFT)
                # 关闭按钮只负责删除页签，不绑定切换页签（否则点击会同时触发）
                close.bind('<Button-1>', lambda e, i=index: self._list_tab_delete(i))
                widgets['close'] = close

            for widget in (frame, label):
                widget.bind('<Button-1>', lambda e, i=index: self._list_tab_select(i))
                widget.bind('<Button-3>', lambda e, i=index: self._list_tab_menu(e, i))
            self._list_tab_widgets.append(widgets)

        try:
            self._list_tabs_canvas.configure(scrollregion=self._list_tabs_canvas.bbox('all'))
        except (tk.TclError, AttributeError):
            pass
        self._ensure_list_tab_visible()

    def _ensure_list_tab_visible(self):
        """把当前页签滚动到可视区域"""
        canvas = getattr(self, '_list_tabs_canvas', None)
        inner = getattr(self, '_list_tabs_inner', None)
        if canvas is None or inner is None or not self._list_tab_widgets:
            return
        index = max(0, min(self._list_active_tab, len(self._list_tab_widgets) - 1))
        widgets = self._list_tab_widgets[index]
        try:
            inner.update_idletasks()
            total = inner.winfo_reqwidth()
            view_w = canvas.winfo_width()
            if total <= 0 or view_w <= 0 or total <= view_w:
                return
            frame = widgets['frame']
            left = frame.winfo_x()
            right = left + frame.winfo_width()
            x0, x1 = canvas.xview()
            if left < x0 * total:
                canvas.xview_moveto(max(0.0, left / total))
            elif right > x1 * total:
                canvas.xview_moveto(min(1.0, max(0.0, (right - view_w) / total)))
        except (tk.TclError, ZeroDivisionError):
            pass

    def _list_tab_select(self, index: int):
        """切换页签：载入该页签保存的网址与页码"""
        if not (0 <= index < len(self.list_tabs)):
            return
        self._list_active_tab = index
        self._save_list_tabs()
        self._render_list_tabs()

        tab = self.list_tabs[index]
        url = str(tab.get('url') or '').strip()
        page = int(tab.get('page') or 0)
        self.list_url_entry_var.set(url)
        self.list_page_var.set(str(page) if page else '')
        if not url:
            return
        self._list_open_url(self._list_tab_target_url(url, page))
        self._list_status_var.set(f"已切换到页签「{tab.get('name', '')}」")

    def _list_tab_new(self):
        """新建页签（默认沿用当前网址与页码，名称可自定义）"""
        current_tab = self._list_active_tab_dict() or {}
        default_name = f"页签 {len(self.list_tabs) + 1}"
        name = self._ask_wide_text("新建页签", "页签名称（可自定义，如“演员-青空光”）：",
                                   initial=default_name, width=430)
        if not name:
            return

        url = str(current_tab.get('url') or self.list_url_entry_var.get() or '').strip()
        page = int(current_tab.get('page') or self.list_page_var.get() or 1)
        self.list_tabs.append({'name': name.strip(), 'url': url, 'page': page})
        self._list_active_tab = len(self.list_tabs) - 1
        self._save_list_tabs()
        self._render_list_tabs()
        self._log(f"[列表页] 已新建页签「{name.strip()}」（网址 {url}，页码 {page}）")

        if url:
            target = self._list_url_for_page(url, page) if page else url
            self._list_open_url(target or url)

    def _list_tab_rename(self, index: int):
        """重命名页签"""
        if not (0 <= index < len(self.list_tabs)):
            return
        old = self.list_tabs[index]
        name = self._ask_wide_text("重命名页签", "新的页签名称：",
                                   initial=str(old.get('name') or ''), width=430)
        if not name:
            return
        old['name'] = name.strip()
        self._save_list_tabs()
        self._render_list_tabs()

    def _list_tab_delete(self, index: int):
        """删除页签（至少保留一个）"""
        if len(self.list_tabs) <= 1 or not (0 <= index < len(self.list_tabs)):
            return
        tab = self.list_tabs.pop(index)
        self._list_active_tab = max(0, min(self._list_active_tab, len(self.list_tabs) - 1))
        self._save_list_tabs()
        self._render_list_tabs()
        self._log(f"[列表页] 已删除页签「{tab.get('name', '')}」")

        # 关闭页签后自动选中剩余页签之一，并把视频列表同步切换到该页签的内容
        # （优先读磁盘缓存，立即显示，不重新联网）
        self._list_tab_select(self._list_active_tab)

    def _list_tab_menu(self, event, index: int):
        """页签右键菜单：重命名 / 删除 / 设为当前"""
        if not (0 <= index < len(self.list_tabs)):
            return
        menu = tk.Menu(self.frame.winfo_toplevel(), tearoff=0)
        menu.add_command(label="切换到该页签", command=lambda: self._list_tab_select(index))
        menu.add_command(label="重命名...", command=lambda: self._list_tab_rename(index))
        menu.add_command(label="用当前网址更新此页签",
                         command=lambda: self._list_tab_update(index))
        menu.add_separator()
        menu.add_command(label="删除页签", command=lambda: self._list_tab_delete(index))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _list_tab_update(self, index: int):
        """把当前网址与页码写入指定页签"""
        if not (0 <= index < len(self.list_tabs)):
            return
        url = self.list_url_entry_var.get().strip()
        if not url:
            return
        page_text = self.list_page_var.get().strip()
        self.list_tabs[index]['url'] = url
        if page_text.isdigit():
            self.list_tabs[index]['page'] = int(page_text)
        self._save_list_tabs()
        self._render_list_tabs()
        self._log(f"[列表页] 页签「{self.list_tabs[index].get('name', '')}」已更新为 "
                  f"{url}（页码 {self.list_tabs[index].get('page', '')}）")

    def _list_tab_create_for(self, url: str) -> bool:
        """为指定网址新建一个页签（网址已存在则不重复创建）"""
        url = self._normalize_url(url)
        if not url:
            return False
        if any(str(t.get('url') or '') == url for t in self.list_tabs):
            return False
        self.list_tabs.append({
            'name': self._list_tab_default_name(url),
            'url': url,
            'page': self._list_page_from_url(url) or 1,
        })
        self._save_list_tabs()
        return True

    def _list_tab_set_url(self, old_url: str, new_url: str):
        """把某个网址对应的页签改成新网址"""
        old_url = self._normalize_url(old_url)
        new_url = self._normalize_url(new_url)
        for tab in self.list_tabs:
            if str(tab.get('url') or '') == old_url:
                tab['url'] = new_url
                tab['page'] = self._list_page_from_url(new_url) or 1
        self._save_list_tabs()

    def _list_tab_remove_url(self, url: str):
        """删除某个网址对应的页签（至少保留一个页签）"""
        url = self._normalize_url(url)
        if len(self.list_tabs) <= 1:
            return
        remain = [t for t in self.list_tabs if str(t.get('url') or '') != url]
        if len(remain) == len(self.list_tabs):
            return
        self.list_tabs = remain
        self._list_active_tab = max(0, min(self._list_active_tab, len(self.list_tabs) - 1))
        self._save_list_tabs()

    def _sync_active_tab(self, url: str, page: Optional[int] = None):
        """把“网址 + 页码”配套写入当前页签（没有页签时自动创建一个）"""
        if not url:
            return
        tab = self._list_active_tab_dict()
        if tab is None:
            self.list_tabs.append({
                'name': self._list_tab_default_name(url),
                'url': url,
                'page': page or 1,
            })
            self._list_active_tab = len(self.list_tabs) - 1
        else:
            tab['url'] = url
            if page:
                tab['page'] = page
        self._save_list_tabs()
        self._render_list_tabs()

    def _ask_wide_text(self, title: str, prompt: str, initial: str = '',
                       width: int = 860) -> Optional[str]:
        """弹出文本输入框（替代过窄的 simpledialog），默认在父窗口正中央显示

        - width：对话框宽度。输入网址等长文本用默认 860；仅输入页签名称等
          短文本时可传小值（如 430），避免窗口过宽；
        - 从列表页浏览层（Toplevel）里调用时以该层为父窗口并置顶显示，
          避免对话框被浏览层挡住、又因模态抓取导致浏览层无法操作的死锁。
        """
        # 父窗口：列表页浏览层打开时优先用浏览层，否则用主窗口
        parent = None
        layer = getattr(self, '_list_win', None)
        try:
            if layer is not None and layer.winfo_exists() and layer.winfo_viewable():
                parent = layer
        except tk.TclError:
            parent = None
        root = (parent or self.frame).winfo_toplevel()
        win = tk.Toplevel(root)
        win.title(title)
        win.transient(root)
        try:
            win.grab_set()
        except Exception:
            pass

        height = 170
        try:
            # 始终居中到主窗口正中央（列表层与主窗口位置锁定，主窗口即视觉中心）。
            # 注意：只能按“主窗口所在矩形”收敛，不能按主屏裁剪 —— 主窗口在副屏时
            # 坐标可能为负，按主屏 clamp 会把窗口顶到主屏左上角（0,0）。
            main = self.frame.winfo_toplevel()
            main.update_idletasks()
            rw, rh = main.winfo_width(), main.winfo_height()
            if rw <= 1 or rh <= 1:          # 尚未完成布局，改用请求尺寸
                rw, rh = main.winfo_reqwidth(), main.winfo_reqheight()
            if rw > 1 and rh > 1:
                px, py = main.winfo_rootx(), main.winfo_rooty()
                pos_x = px + max(0, (rw - width) // 2)
                pos_y = py + max(0, (rh - height) // 2)
                # 主窗口放不下时向内收，保证对话框完整落在主窗口范围内
                if rw >= width:
                    pos_x = min(pos_x, px + rw - width)
                if rh >= height:
                    pos_y = min(pos_y, py + rh - height)
            else:
                pos_x = (main.winfo_screenwidth() - width) // 2
                pos_y = (main.winfo_screenheight() - height) // 2
        except Exception:
            pos_x, pos_y = 200, 200
        win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        try:
            win.minsize(min(560, width), 150)   # 窄对话框（width=430）不被 minsize 撑大
        except Exception:
            pass

        ttk.Label(win, text=prompt).pack(anchor=tk.W, padx=14, pady=(14, 4))

        text_var = tk.StringVar(value=initial or '')
        entry = ttk.Entry(win, textvariable=text_var, font=('Consolas', 10))
        entry.pack(fill=tk.X, padx=14, ipady=3)
        entry.focus_set()
        try:
            entry.icursor(tk.END)
            entry.xview_moveto(1.0)
        except Exception:
            pass

        # 确保对话框显示在列表页浏览层等其它弹出层之上并拿到焦点
        # （否则模态抓取会让被挡住的浏览层既点不到也无法关闭）
        def _place_and_raise():
            try:
                win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
                win.lift()
                win.attributes('-topmost', True)
                win.focus_force()
            except Exception:
                pass

        try:
            win.update_idletasks()
        except Exception:
            pass
        _place_and_raise()
        # 窗口真正映射后再校正一次：transient 到无边框浏览层时系统可能改动位置
        try:
            win.after_idle(_place_and_raise)
        except Exception:
            pass

        result: Dict[str, Optional[str]] = {'value': None}

        def on_ok(event=None):
            result['value'] = text_var.get().strip()
            win.destroy()

        def on_cancel(event=None):
            result['value'] = None
            win.destroy()

        btn_bar = ttk.Frame(win)
        btn_bar.pack(fill=tk.X, padx=14, pady=12)
        ttk.Button(btn_bar, text="确定", width=10, command=on_ok).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_bar, text="取消", width=10, command=on_cancel).pack(side=tk.RIGHT, padx=4)

        win.bind('<Return>', on_ok)
        win.bind('<Escape>', on_cancel)
        win.protocol("WM_DELETE_WINDOW", on_cancel)
        win.wait_window()
        return result['value']

    def _list_urls_add(self, urls: List[str]) -> int:
        """把网址批量加入网址库（自动去重），返回新增数量"""
        added = 0
        for url in urls:
            url = self._normalize_url(url)
            if not url or url in self._list_urls:
                continue
            self._list_urls.append(url)
            added += 1
        if added:
            self._save_list_urls()
        return added

    def _list_library_add(self):
        """手动添加一个列表页网址到网址库（同时为该网址生成一个页签）"""
        url = self._ask_wide_text("添加网址", "请输入列表页网址：")
        if not url:
            return
        added = self._list_urls_add([url])
        if not added:
            self.list_library_status_var.set("该网址已存在")
            return

        normalized = self._normalize_url(url)
        created = self._list_tab_create_for(normalized)
        self.list_library_status_var.set(
            f"已添加: {normalized}" + ("（已生成对应页签）" if created else "（已有对应页签）"))

    def _list_library_edit(self):
        """编辑选中的网址"""
        sel = self.list_library_listbox.curselection()
        if not sel:
            self.list_library_status_var.set("请先在网址库中选择一条网址")
            return
        index = sel[0]
        if index >= len(self._list_urls):
            return
        old = self._list_urls[index]
        new = self._ask_wide_text("编辑网址", "修改网址：", initial=old)
        if not new:
            return
        new = self._normalize_url(new)
        if not new:
            return
        self._list_urls[index] = new
        self._save_list_urls()
        self._list_tab_set_url(old, new)
        self.list_library_status_var.set(f"已修改: {new}")

    def _list_library_remove(self):
        """删除选中的网址（同时删除其对应页签）"""
        sel = list(self.list_library_listbox.curselection())
        if not sel:
            self.list_library_status_var.set("请先在网址库中选择要删除的网址")
            return
        removed = []
        for index in sorted(sel, reverse=True):
            if 0 <= index < len(self._list_urls):
                removed.append(self._list_urls[index])
                del self._list_urls[index]
        self._save_list_urls()
        for url in removed:
            self._list_tab_remove_url(url)
        self.list_library_status_var.set("已删除选中网址（对应页签一并删除）")

    def _list_library_test(self, selected_only: bool = True):
        """连接测试：探测网址是否可访问"""
        if selected_only:
            sel = list(self.list_library_listbox.curselection())
            urls = [self._list_urls[i] for i in sel if 0 <= i < len(self._list_urls)]
        else:
            urls = list(self._list_urls)
        if not urls:
            self.list_library_status_var.set("网址库为空，请先添加网址")
            return
        self.list_library_status_var.set(f"正在测试 {len(urls)} 个网址...")

        def worker():
            results = []
            for url in urls:
                ok, message = self._probe_list_url(url)
                results.append((url, ok, message))
            self._run_in_main_thread(lambda: self._show_probe_results(results))

        threading.Thread(target=worker, daemon=True).start()

    def _probe_list_url(self, url: str) -> Tuple[bool, str]:
        """连接测试单个网址（后台线程执行）"""
        worker = self._build_list_worker()
        try:
            page = worker.fetch_page(url, use_render=False)
            if page:
                return True, f"可访问（{len(page)} 字符）"
            return False, "无响应内容"
        except Exception as e:
            return False, f"失败: {e}"

    def _show_probe_results(self, results: List[Tuple[str, bool, str]]):
        """显示连接测试结果"""
        self.list_library_status_var.set(
            f"连接测试完成：{sum(1 for _, ok, _ in results if ok)}/{len(results)} 可访问")
        for url, ok, message in results:
            self._log(f"[连接测试] {'OK ' if ok else 'FAIL'} {url} -> {message}")

        dialog = tk.Toplevel(self.frame)
        dialog.title("网址连接测试结果")
        dialog.geometry("760x360")
        dialog.transient(self.frame.winfo_toplevel())

        tree = ttk.Treeview(dialog, columns=('url', 'status', 'message'), show='headings')
        tree.heading('url', text='网址')
        tree.heading('status', text='状态')
        tree.heading('message', text='说明')
        tree.column('url', width=380)
        tree.column('status', width=70)
        tree.column('message', width=280)
        tree.tag_configure('ok', foreground='#107c10')
        tree.tag_configure('fail', foreground='#cc0000')
        for url, ok, message in results:
            tree.insert('', tk.END, values=(url, '可访问' if ok else '失败', message),
                        tags=('ok' if ok else 'fail',))
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(pady=(0, 8))

    # ---------- 列表页解析配置读写 ----------
    def _list_parse_cfg(self) -> Dict:
        """当前界面的列表解析配置"""
        return {
            'container': self.list_container_var.get(),
            'item': self.list_item_var.get(),
            'thumb': self.list_thumb_var.get(),
            'title': self.list_title_var.get(),
            'code': self.list_code_var.get(),
            'date': self.list_date_var.get(),
            'flag': self.list_flag_var.get(),
            'link': self.list_link_var.get(),
        }

    def _save_list_config(self):
        """保存列表页设置到配置文件"""
        if not hasattr(self, 'list_container_var'):
            return
        section = self.CONFIG_SECTION
        for key, var, default in self._list_selector_items():
            try:
                self.config_manager.set(section, key, (var.get() or '').strip() or default)
            except Exception:
                pass
        for key, var in (('list_auto_collect', self.list_auto_collect_var),
                         ('list_render_js', self.list_render_var)):
            try:
                self.config_manager.set(section, key, bool(var.get()))
            except Exception:
                pass
        for key, var in (('list_pages_shown', self.list_pages_var),
                         ('list_wildcard_pad', self.list_pad_var)):
            try:
                self.config_manager.set(section, key, (var.get() or '').strip())
            except Exception:
                pass
        try:
            self.config_manager.set(section, 'list_config_version', LIST_CONFIG_VERSION)
        except Exception:
            pass

    def _list_selector_items(self):
        """列表页各选择器：(配置键, 变量, 默认值)"""
        return (
            ('list_container_selector', self.list_container_var, DEFAULT_LIST_CONTAINER_SELECTOR),
            ('list_item_selector', self.list_item_var, DEFAULT_LIST_ITEM_SELECTOR),
            ('list_thumb_selector', self.list_thumb_var, DEFAULT_LIST_THUMB_SELECTOR),
            ('list_title_selector', self.list_title_var, DEFAULT_LIST_TITLE_SELECTOR),
            ('list_code_selector', self.list_code_var, DEFAULT_LIST_CODE_SELECTOR),
            ('list_date_selector', self.list_date_var, DEFAULT_LIST_DATE_SELECTOR),
            ('list_flag_selector', self.list_flag_var, DEFAULT_LIST_FLAG_SELECTOR),
            ('list_link_selector', self.list_link_var, DEFAULT_LIST_LINK_SELECTOR),
            ('list_next_selector', self.list_next_var, DEFAULT_LIST_NEXT_SELECTOR),
            ('list_alert_selector', self.list_alert_var, DEFAULT_LIST_ALERT_SELECTOR),
            ('list_page_template', self.list_page_template_var, DEFAULT_LIST_PAGE_TEMPLATE),
            ('list_search_path', self.list_search_path_var, DEFAULT_LIST_SEARCH_PATH),
        )

    def _load_list_config(self):
        """从配置文件载入列表页设置

        默认选择器版本升级（``list_config_version`` 落后于 ``LIST_CONFIG_VERSION``）时，
        配置里保存的旧默认值会被最新默认值覆盖，避免因页面结构变化而一直抓不到数据。
        """
        if not hasattr(self, 'list_container_var'):
            return
        section = self.CONFIG_SECTION

        try:
            stored_version = int(self.config_manager.get(section, 'list_config_version', 1) or 1)
        except Exception:
            stored_version = 1
        migrate = stored_version < LIST_CONFIG_VERSION

        for key, var, default in self._list_selector_items():
            try:
                value = self.config_manager.get(section, key, '')
                if migrate or not str(value or '').strip():
                    value = default
                var.set(value)
            except Exception:
                pass

        for key, var, default in (('list_auto_collect', self.list_auto_collect_var, True),
                                  ('list_render_js', self.list_render_var, False)):
            try:
                var.set(bool(self.config_manager.get(section, key, default)))
            except Exception:
                pass

        for key, var, default in (('list_pages_shown', self.list_pages_var, '1'),
                                  ('list_wildcard_pad', self.list_pad_var, '')):
            try:
                var.set(str(self.config_manager.get(section, key, default) or default))
            except Exception:
                pass

        if migrate:
            # 把升级后的选择器与新版本号写回配置
            self._save_list_config()
            self.config_manager.set(section, 'list_config_version', LIST_CONFIG_VERSION)
            self._log(f"列表页默认选择器已升级到 v{LIST_CONFIG_VERSION}（旧默认值已替换为最新默认值）")

        self._list_urls = self._load_list_urls()
        self._save_list_urls()
        # 列表页页签（网址 + 页码配套保存）
        self.list_tabs = self._load_list_tabs()
        self._list_active_tab = self._load_active_tab_index()

    def _build_list_worker(self) -> 'WebGrabWorker':
        """构造一个仅用于列表页抓取的抓取器（复用请求头/Cookie/代理等设置）"""
        return WebGrabWorker(config=self._build_grab_config(), log_callback=self._log)

    # ---------- 页码 / 翻页 ----------
    @staticmethod
    def _host_of(url: str) -> str:
        """取地址的 “协议://域名” 部分（如 https://www.busfan.cyou）"""
        try:
            parts = urllib.parse.urlsplit(url or '')
            if not parts.scheme or not parts.netloc:
                return ''
            return f"{parts.scheme}://{parts.netloc}"
        except Exception:
            return ''

    @staticmethod
    def _apply_host(url: str, host: str) -> str:
        """把地址的 “协议://域名” 换成指定入口，保留路径与参数"""
        if not url or not host:
            return url
        try:
            parts = urllib.parse.urlsplit(url)
            new = urllib.parse.urlsplit(host)
            if not new.scheme or not new.netloc:
                return url
            return urllib.parse.urlunsplit(
                (new.scheme, new.netloc, parts.path or '/', parts.query, parts.fragment))
        except Exception:
            return url

    @staticmethod
    def _list_page_from_url(url: str) -> Optional[int]:
        """从地址中取出页码

        支持三种分页地址形态：
        1. 站点首页分页：https://site/（第 1 页）/ https://site/page/{n}
        2. 子路径分页（演员/系列/搜索结果页）：/star/1371（第 1 页）/ /star/1371/{n}
        3. 查询参数分页：?page={n}
        """
        url = url or ''
        # 查询参数分页：xxx?page={n}
        m = re.search(r'[?&]page=(\d+)', url)
        if m:
            return int(m.group(1))
        # 站点首页分页：/page/{n}
        m = re.search(r'/page/(\d+)', url)
        if m:
            return int(m.group(1))
        try:
            parts = urllib.parse.urlsplit(url)
            if not parts.netloc:
                return None
            segs = [s for s in (parts.path or '').split('/') if s]
            # 子路径第 n 页（n>=2）：路径末段是数字且前面还有层级，如 /star/1371/2
            if len(segs) >= 3 and segs[-1].isdigit():
                return int(segs[-1])
            # 第 1 页形态：站点首页（/）或子路径首页（如 /star/1371）
            if len(segs) == 0 or (len(segs) == 2 and segs[-1].isdigit()):
                return 1
        except Exception:
            pass
        return None

    def _list_url_for_page(self, url: str, page: int) -> Optional[str]:
        """按页码生成目标地址

        三种形态分别处理：
        1. 站点首页分页：第 1 页 = 站点首页，其余页 = {基路径}/page/{页码}；
           配置了“页码模板”（含 {page}）时优先用模板（仅首页式分页生效，
           避免演员页等子路径被误改写到站点首页）。
        2. 子路径分页：/star/1371（第 1 页）/ /star/1371/{页码}，直接追加页码。
        3. 查询参数分页：改写 query 里的 page={页码}。
        """
        url = (url or '').strip()
        try:
            page = int(page)
        except (TypeError, ValueError):
            return None
        host = self._host_of(url)
        if not host:
            return None
        try:
            parts = urllib.parse.urlsplit(url)
            path = parts.path or '/'
            segs = [s for s in path.split('/') if s]
        except Exception:
            return None

        template = (self.list_page_template_var.get() or '').strip()
        has_template = bool(template) and '{page}' in template

        def _via_template() -> str:
            built = template.replace('{host}', host).replace('{page}', str(page))
            if page <= 1:
                # 第 1 页固定为站点首页
                p = urllib.parse.urlsplit(built).path
                if re.search(r'/page/\d+', p):
                    built = host + '/'
            return built

        # 1) 查询参数分页：xxx?page={n}
        if parts.query and re.search(r'(?:^|&)page=\d+', parts.query):
            new_query = re.sub(r'(?:(?<=^)|(?<=&))page=\d+', f'page={page}',
                               parts.query, count=1)
            return urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))

        # 2) 站点首页式分页：/ 或 /page/{n}
        if not segs or re.search(r'/page/\d+', path):
            if has_template:
                return _via_template()
            base = re.sub(r'/page/\d+', '', path)
            if page <= 1:
                return host + (base if base else '/')
            return f"{host}{base.rstrip('/')}/page/{page}"

        # 3) 子路径分页（演员/系列/搜索结果页）：/star/1371（第1页）/ /star/1371/{n}
        if len(segs) >= 3 and segs[-1].isdigit():
            base_path = '/' + '/'.join(segs[:-1])   # 已带页码：去掉末段得到第 1 页地址
        else:
            base_path = '/' + '/'.join(segs)        # 子路径第 1 页（末段是编号而非页码）
        if page <= 1:
            return host + base_path
        return f"{host}{base_path.rstrip('/')}/{page}"

    def _list_tab_target_url(self, url: str, page: Optional[int] = None) -> str:
        """按页签的网址与页码生成打开地址

        分页列表（站点首页 /page/{页码}、子路径 /star/1371/{页码}、?page={页码}）
        按页码改写地址；其它不带页码的地址一律原样返回，避免被误改写。
        """
        url = (url or '').strip()
        if not url:
            return ''
        page_no = self._list_page_from_url(url)
        if page_no is None:
            return url
        return self._list_url_for_page(url, int(page or page_no)) or url

    # ---------- 测试解析 ----------
    def _list_test_parse(self):
        """用网址库第一条（或当前输入）测试列表解析，结果弹窗显示"""
        url = ''
        try:
            sel = self.list_library_listbox.curselection()
            if sel:
                url = self._list_urls[sel[0]]
        except Exception:
            url = ''
        if not url and self._list_urls:
            url = self._list_urls[0]
        if not url:
            url = self._ask_wide_text("测试解析", "请输入用于测试的列表页网址：")
        if not url:
            self._set_rule_status("已取消：未提供测试网址")
            return
        url = self._normalize_url(url)
        self._log(f"正在测试列表解析: {url}")

        # 状态在窗口内提示，不弹中断式对话框
        self._set_rule_status("正在抓取并解析，请稍候...")

        def worker():
            w = self._build_list_worker()
            page = None
            error = ''
            try:
                page = w.fetch_page(url, use_render=self.list_render_var.get())
            except Exception as e:
                error = str(e)
                self._log(f"测试解析抓取失败: {e}")
            items = parse_list_items(page or '', url, self._list_parse_cfg())
            html_snippet = ''
            if BS4_AVAILABLE and page:
                try:
                    soup = BeautifulSoup(page, 'html.parser')
                    sel_item = (self.list_item_var.get() or DEFAULT_LIST_ITEM_SELECTOR)
                    node = soup.select_one(sel_item)
                    html_snippet = str(node)[:4000] if node is not None else ''
                except Exception:
                    html_snippet = ''
            self._run_in_main_thread(
                lambda: self._show_list_parse_result(url, items, html_snippet, error))

        threading.Thread(target=worker, daemon=True).start()

    def _set_rule_status(self, message: str, error: bool = False):
        """刷新“测试解析”的状态文字（窗口未打开时只写日志）"""
        var = getattr(self, 'list_rule_status_var', None)
        if var is not None:
            try:
                var.set(message)
            except tk.TclError:
                pass
        else:
            self._log(message)

    def _show_list_parse_result(self, url: str, items: List[Dict],
                                html_snippet: str, error: str = ''):
        """显示测试解析结果（同时把摘要写到设置窗口的状态行）"""
        if error:
            self._set_rule_status(f"抓取失败：{error}（{url}）", error=True)
        elif items:
            self._set_rule_status(f"解析完成：共 {len(items)} 条视频　{url}", error=False)
        else:
            self._set_rule_status(
                f"未解析到任何视频条目（{url}）：请检查“列表容器/视频条目”选择器，"
                "或勾选“浏览器渲染”后重试。", error=True)

        dialog = tk.Toplevel(self.frame)
        dialog.title("列表解析测试结果")
        dialog.geometry("1000x640")
        dialog.transient(self.frame.winfo_toplevel())

        ttk.Label(dialog, text=f"地址: {url}　解析到 {len(items)} 条视频",
                  foreground="#0066cc").pack(anchor=tk.W, padx=8, pady=(8, 4))
        if not items:
            ttk.Label(dialog, text="未解析到任何视频条目：请检查“列表容器/视频条目”选择器，"
                                   "或勾选“用浏览器渲染后抓取列表页”。",
                      foreground="#cc0000", wraplength=960,
                      justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=(0, 6))

        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=8)
        columns = ('index', 'code', 'title', 'date', 'flag', 'thumb', 'url')
        tree = ttk.Treeview(frame, columns=columns, show='headings')
        for key, text, width in (('index', '#', 40), ('code', '番号', 100),
                                 ('title', '标题', 260), ('date', '发布日期', 90),
                                 ('flag', '标志', 60), ('thumb', '缩略图', 180),
                                 ('url', '链接', 260)):
            tree.heading(key, text=text)
            tree.column(key, width=width)
        for i, item in enumerate(items, 1):
            tree.insert('', tk.END, values=(i, item.get('code', ''), item.get('title', ''),
                                            item.get('date', ''), item.get('flag', ''),
                                            item.get('thumb', ''), item.get('url', '')))
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        if html_snippet:
            snippet_box = ttk.LabelFrame(dialog, text="第一个条目的 HTML（便于核对选择器）")
            snippet_box.pack(fill=tk.BOTH, expand=False, padx=8, pady=6)
            text = scrolledtext.ScrolledText(snippet_box, height=8, wrap='none',
                                             font=('Consolas', 9))
            text.insert('1.0', html_snippet)
            text.configure(state=tk.DISABLED)
            text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(pady=(0, 8))

    def _field_rules_from_config(self) -> List[Dict]:
        """读取字段抓取规则（自愈：补齐缺失字段、修复空内容与非法方式）"""
        rules = self.config_manager.get(self.CONFIG_SECTION, 'field_rules', None)
        return normalize_field_rules(rules)

    def _build_field_rules_section(self, parent):
        """构建字段抓取规则设置区（css / regex / custom 三种方式可选）"""
        box = ttk.LabelFrame(parent, text="字段抓取规则（均在内容A内执行）", padding=8)
        box.pack(fill=tk.X, pady=6)

        head = ttk.Frame(box)
        head.pack(fill=tk.X)
        ttk.Label(head, text="字段", width=9).pack(side=tk.LEFT)
        ttk.Label(head, text="方式", width=8).pack(side=tk.LEFT)
        ttk.Label(head, text="选择器 / 正则 / Python代码").pack(side=tk.LEFT, padx=3)

        self._field_rule_vars = []
        for rule in self._field_rules_from_config():
            row = ttk.Frame(box)
            row.pack(fill=tk.X, pady=2)

            name_var = tk.StringVar(value=str(rule.get('field_name', '')))
            type_var = tk.StringVar(value=str(rule.get('rule_type', 'css')))
            multiple_var = tk.BooleanVar(value=bool(rule.get('multiple', False)))
            enabled_var = tk.BooleanVar(value=bool(rule.get('enabled', True)))

            ttk.Label(row, textvariable=name_var, width=9).pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=type_var, values=list(FIELD_RULE_TYPES),
                         width=7, state='readonly').pack(side=tk.LEFT, padx=2)

            content_text = tk.Text(row, height=2, wrap='none', font=('Consolas', 9))
            content_text.insert('1.0', str(rule.get('rule_content', '')))
            content_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
            content_text.bind('<KeyRelease>', lambda e: self._save_field_rules())
            content_text.bind('<FocusOut>', lambda e: self._save_field_rules())

            # 放大编辑：点击 ^ 弹出大尺寸多行编辑层，确定后回填当前文本框
            ttk.Button(row, text="^", width=2,
                       command=lambda t=content_text,
                       n=str(rule.get('field_name', '')):
                       self._open_field_content_editor(n, t)).pack(side=tk.LEFT, padx=(1, 3))

            ttk.Checkbutton(row, text="多个", variable=multiple_var,
                            command=self._save_field_rules).pack(side=tk.LEFT)
            ttk.Checkbutton(row, text="启用", variable=enabled_var,
                            command=self._save_field_rules).pack(side=tk.LEFT)

            type_var.trace('w', lambda *args: self._save_field_rules())
            self._field_rule_vars.append({
                'field_id': str(rule.get('field_id', '')),
                'field_name': name_var,
                'rule_type': type_var,
                'content_widget': content_text,
                'multiple': multiple_var,
                'enabled': enabled_var,
            })

        ttk.Button(box, text="恢复默认规则",
                   command=self._reset_field_rules).pack(anchor=tk.W, pady=(6, 2))
        ttk.Label(box, text="方式说明：css=选择器取文本；regex=正则取分组；"
                           "custom=Python代码，可用 soup / html / re，结果赋给 result。",
                  foreground="gray", font=('', 8), wraplength=560,
                  justify=tk.LEFT).pack(anchor=tk.W)

    def _open_field_content_editor(self, field_name: str, content_widget: tk.Text):
        """为字段规则的内容文本框弹出大尺寸编辑层（可关闭/确定，确定后回填原文本框）"""
        root = self.frame.winfo_toplevel()
        win = tk.Toplevel(root)
        win.title(f"字段内容编辑 - {field_name}")
        win.transient(root)
        win.configure(background=VIEWER_BG)
        try:
            win.attributes('-topmost', True)
        except Exception:
            pass

        # 尺寸：以主窗口为参照居中，且不小于可用编辑面积
        try:
            root.update_idletasks()
            width = min(max(680, root.winfo_width() - 200), 960)
            height = min(max(420, root.winfo_height() - 160), 640)
            pos_x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
            pos_y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
        except Exception:
            width, height, pos_x, pos_y = 760, 480, 0, 0
        win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

        # 标题栏（与爬虫设置层风格一致）
        title_bar = tk.Frame(win, background=SETTINGS_TITLE_BG, height=SETTINGS_TITLE_HEIGHT)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text=f"字段内容编辑 - {field_name}", background=SETTINGS_TITLE_BG,
                 foreground='#ffffff', font=('Microsoft YaHei', 12, 'bold')).pack(
            side=tk.LEFT, padx=16)

        # 主体：大尺寸多行文本框（带横竖滚动条）
        body = tk.Frame(win, background=VIEWER_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        text_frame = ttk.Frame(body)
        text_frame.pack(fill=tk.BOTH, expand=True)

        editor = tk.Text(text_frame, wrap='none', font=('Consolas', 11), undo=True)
        vbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=editor.yview)
        hbar = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=editor.xview)
        editor.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hbar.pack(fill=tk.X, pady=(4, 0))

        # 载入原文本框内容
        try:
            editor.insert('1.0', content_widget.get('1.0', tk.END).rstrip('\n'))
        except Exception:
            pass
        editor.focus_set()

        def on_close(event=None):
            try:
                win.destroy()
            except Exception:
                pass

        def on_confirm():
            # 回填原文本框并保存
            new_content = editor.get('1.0', tk.END).rstrip('\n')
            try:
                content_widget.delete('1.0', tk.END)
                content_widget.insert('1.0', new_content)
            except Exception:
                pass
            self._save_field_rules()
            on_close()

        # 底部按钮：确定 / 关闭
        btn_bar = ttk.Frame(body)
        btn_bar.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_bar, text="确定", width=10,
                   command=on_confirm).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_bar, text="关闭", width=10,
                   command=on_close).pack(side=tk.RIGHT, padx=4)

        win.bind('<Escape>', on_close)
        win.bind('<Control-Return>', lambda e: on_confirm())
        win.protocol("WM_DELETE_WINDOW", on_close)
        win.focus_force()

    def _save_field_rules(self):
        """保存字段抓取规则

        安全约定（修复“所有字段抓不到”的根因）：
          * 规则区从未构建（``_field_rule_vars`` 为空）时直接返回，不覆盖已有配置；
          * 单个控件读取失败（窗口已销毁）时沿用配置里的原值，绝不写入空内容；
          * 全部控件都读取失败时放弃本次保存。
        """
        if getattr(self, '_skip_field_rules_save', False):
            return
        items = list(getattr(self, '_field_rule_vars', None) or [])
        if not items:
            return

        previous = {}
        for old in (self.config_manager.get(self.CONFIG_SECTION, 'field_rules', []) or []):
            if isinstance(old, dict):
                previous[str(old.get('field_id') or '')] = old

        rules = []
        failed = 0
        for item in items:
            content = None
            try:
                content = item['content_widget'].get('1.0', tk.END).strip()
            except Exception:
                failed += 1
            if content is None:
                # 控件已销毁：沿用配置中的原值，避免把规则写空
                old = previous.get(item['field_id']) or {}
                content = str(old.get('rule_content') or '')
            rules.append({
                'field_id': item['field_id'],
                'field_name': item['field_name'].get(),
                'rule_type': item['rule_type'].get(),
                'rule_content': content,
                'multiple': bool(item['multiple'].get()),
                'enabled': bool(item['enabled'].get()),
            })

        if failed and failed == len(items):
            return          # 全部读取失败（界面正在销毁），放弃保存
        self.config_manager.set(self.CONFIG_SECTION, 'field_rules', rules)

    def _reset_field_rules(self):
        """恢复默认字段规则并重建设置层"""
        # 先标记跳过保存，避免关闭时把界面上的旧内容写回配置
        self._skip_field_rules_save = True
        try:
            self.config_manager.set(self.CONFIG_SECTION, 'field_rules',
                                    [dict(rule) for rule in DEFAULT_FIELD_RULES])
            self._log("字段抓取规则已恢复默认")
            self._close_crawler_settings()
            self._open_crawler_settings()
        finally:
            self._skip_field_rules_save = False

    def _create_widgets(self):
        """创建界面"""
        self.frame = ttk.Frame(self.parent)
        # 页签被销毁时（关闭主窗口/移除页签）兜底释放已载入的翻译模型
        self.frame.bind('<Destroy>', self._on_frame_destroy)

        # 底部按钮条（先于主分栏打包并固定在底部，保证窗口高度不足时按钮也始终可见）
        bottom_frame = ttk.Frame(self.frame)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

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
        # 地址框跟随窗口宽度自动伸缩（与下方“保存目录”“演员”输入框一致），
        # 右侧按钮固定，剩余宽度全部给地址框
        self.url_entry = ttk.Entry(url_input_frame, textvariable=self.url_var)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        ttk.Button(url_input_frame, text="抓取",
                  command=self._start_grab, width=8).pack(side=tk.RIGHT)
        ttk.Button(url_input_frame, text="清空",
                  command=self._clear_url, width=6).pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Button(url_input_frame, text="粘贴",
                  command=self._import_from_clipboard, width=6).pack(side=tk.RIGHT, padx=(0, 4))

        # 通配符批量抓取：网址中写 {#} 占位，再填起止数字即可批量生成页签抓取
        range_frame = ttk.Frame(url_frame)
        range_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        ttk.Label(range_frame, text="批量 {#} 起止:").pack(side=tk.LEFT)
        self.range_start_var = tk.StringVar()
        self.range_end_var = tk.StringVar()
        ttk.Entry(range_frame, textvariable=self.range_start_var,
                  width=6).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(range_frame, text="~").pack(side=tk.LEFT, padx=2)
        ttk.Entry(range_frame, textvariable=self.range_end_var,
                  width=6).pack(side=tk.LEFT)
        ttk.Label(range_frame, text="（网址用 {#} 占位，如 http://a.com/aaa-{#}）",
                  foreground="#888888").pack(side=tk.LEFT, padx=(8, 0))

        # 拖放区：把浏览器中的图片拖到这里，自动取出其所在的 a 标签链接
        self._create_drop_zone(url_frame)

        # 说明：爬虫与内容容器的设置已移到“爬虫设置”浮窗层（底部按钮区进入），
        # 这里不再占用左侧面板空间

        # 请求设置按钮
        settings_frame = ttk.Frame(left_frame)
        settings_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(settings_frame, text="请求头设置",
                  command=self._show_headers_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(settings_frame, text="浏览器模式",
                  command=self._show_browser_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(settings_frame, text="连接设置",
                  command=self._show_connection_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(settings_frame, text="测试爬取内容",
                  command=self._show_source_test_window).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(settings_frame, text="多链接",
                        variable=self.multi_link_var,
                        command=self._on_multi_link_toggle).pack(side=tk.LEFT, padx=(12, 2))
        ttk.Button(settings_frame, text="创建视频文件夹",
                   command=self._create_video_folder).pack(side=tk.RIGHT, padx=(0, 2))

        # 保存目录（文本框 + 选择按钮；点击文本框空白处弹出历史目录下拉层）
        save_dir_frame = ttk.Frame(left_frame)
        save_dir_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(save_dir_frame, text="保存目录:").pack(side=tk.LEFT)
        self.save_dir_entry = ttk.Entry(save_dir_frame, textvariable=self.save_path_var,
                                        width=26)
        self.save_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(save_dir_frame, text="选择", command=self._browse_save_path,
                   width=6).pack(side=tk.RIGHT)

        # 点击文本框中的空白位置（文本之后）弹出历史目录；已展开时再次点击则收起
        def on_save_dir_click(event):
            if self._save_dir_popup is not None:
                self._hide_save_dir_history()
                return
            try:
                click_index = self.save_dir_entry.index(f'@{event.x}')
                if click_index < len(self.save_path_var.get()):
                    return          # 点在已有文字上：只定位光标，不弹历史层
            except Exception:
                pass
            # 延迟到本次点击事件处理完之后再弹出，
            # 否则新弹出的层会被这次点击的“全局收起”逻辑当场关掉
            self.frame.after(1, self._show_save_dir_history)

        self.save_dir_entry.bind('<Button-1>', on_save_dir_click)
        # 编辑结束（失焦 / 回车）时，若目录尚不存在则逐级创建
        self.save_dir_entry.bind('<FocusOut>', self._on_save_dir_edit_done, add='+')
        self.save_dir_entry.bind('<Return>', self._on_save_dir_edit_done, add='+')

        # 常驻全局监听：点击应用内任意位置 / 按 Esc 即收起历史下拉层
        # 说明：用 add='+' 追加而不覆盖其它绑定；处理函数在“没有弹出层”时直接返回，无副作用。
        # 之前的实现是在弹出层创建时才 bind_all、关闭时 unbind_all，
        # 一旦被 <FocusOut> 等其它路径先关闭并解绑，层就会失去“点别处关闭”的能力。
        self.frame.bind_all('<Button-1>', self._on_global_click, add='+')
        self.frame.bind_all('<Escape>', self._on_global_escape, add='+')

        # 标题（文字行，来自内容A的抓取结果）
        title_row = ttk.Frame(left_frame)
        title_row.pack(fill=tk.X, padx=5, pady=(0, 4))

        title_name_label = ttk.Label(title_row, text="标题:", cursor="hand2")
        title_name_label.pack(side=tk.LEFT)
        self.page_title_var = tk.StringVar(value="")
        # 标题固定单行显示：不设 wraplength（避免按空格换行），
        # width=1 让标签不撑大左侧面板，超长部分在右边界自动裁切
        self.title_value_label = ttk.Label(
            title_row, textvariable=self.page_title_var, foreground="#0066cc",
            width=1, anchor=tk.W, justify=tk.LEFT, cursor="hand2")
        self.title_value_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        # 点击标题行把完整标题写入日志（已翻译时输出完整译名）
        title_name_label.bind('<Button-1>', self._log_full_title)
        self.title_value_label.bind('<Button-1>', self._log_full_title)

        # 系列 / 发行商 / 制作商（点击字段名复制到剪贴板）
        self.series_var = tk.StringVar(value="")
        self.publisher_var = tk.StringVar(value="")
        self.maker_var = tk.StringVar(value="")
        # 系列单独一行（点击系列名可在“视频列表页”层新建页签打开该系列）
        series_row = ttk.Frame(left_frame)
        series_row.pack(fill=tk.X, padx=5, pady=(0, 4))
        self._add_meta_row(series_row, "系列:", self.series_var, "系列", field_id='series')
        # 发行商与制作商放在同一行
        meta_row = ttk.Frame(left_frame)
        meta_row.pack(fill=tk.X, padx=5, pady=(0, 4))
        self._add_meta_row(meta_row, "发行商:", self.publisher_var, "发行商",
                           field_id='publisher')
        self._add_meta_row(meta_row, "制作商:", self.maker_var, "制作商", field_id='maker')

        # 类别（類別）：每个类别一个可点击标签，超出一行自动换行；
        # 点击单个 = 单选打开该类别页；勾选“多选”后可累计多个，组合成 /genre/代码-代码 打开
        genre_row = ttk.Frame(left_frame)
        genre_row.pack(fill=tk.X, padx=5, pady=(0, 4))
        self._genre_var = tk.StringVar(value="")
        genre_label = ttk.Label(genre_row, text="类别:", cursor="hand2")
        genre_label.pack(side=tk.LEFT, padx=(0, 2))
        genre_label.bind('<Button-1>',
                         lambda e: self._copy_text(self._genre_var.get(), "类别"))
        genre_label.bind('<Button-3>',
                         lambda e: self._copy_text(self._genre_var.get(), "类别"))
        # 右侧控制区（从右到左依次打包：清空 | 确定 | 多选）
        self._genre_multi_var = tk.BooleanVar(value=False)
        self._genre_clear_btn = ttk.Button(genre_row, text="清空", width=5,
                                           command=self._genre_clear_selection,
                                           state='disabled')
        self._genre_clear_btn.pack(side=tk.RIGHT, padx=(2, 0))
        # 多选模式下：选好所有类别后点“确定”再打开组合网址（单选模式下禁用）
        self._genre_ok_btn = ttk.Button(genre_row, text="确定", width=5,
                                        command=self._genre_confirm_selection,
                                        state='disabled')
        self._genre_ok_btn.pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Checkbutton(genre_row, text="多选", variable=self._genre_multi_var,
                        command=self._genre_on_multi_toggle).pack(side=tk.RIGHT, padx=(2, 0))
        # 类别标签容器（自动折行）
        self._genre_container = ttk.Frame(genre_row)
        self._genre_container.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._genre_container.bind('<Configure>', self._on_genre_container_resize)
        self._genre_entries: List[Dict[str, str]] = []
        self._genre_pills: List[Any] = []
        self._genre_lines: List[Any] = []
        self._genre_selection = set()
        self._genre_last_width = 0
        self._genre_reflowing = False

        # 演员（文本框 + 下拉箭头 + 复制按钮）
        actor_row = ttk.Frame(left_frame)
        actor_row.pack(fill=tk.X, padx=5, pady=(0, 4))

        ttk.Button(actor_row, text="复制", width=5,
                   command=lambda: self._copy_text(self.actors_var.get(), "演员")).pack(
            side=tk.RIGHT, padx=(2, 0))
        # 下拉箭头：多位演员时逐个列出，选中后在“视频列表页”层打开其作品列表
        self.actors_menu_btn = ttk.Button(actor_row, text="▼", width=3,
                                          command=self._show_actors_menu)
        self.actors_menu_btn.pack(side=tk.RIGHT, padx=(2, 0))
        actor_name_label = ttk.Label(actor_row, text="演员:", cursor="hand2")
        actor_name_label.pack(side=tk.LEFT)
        actor_name_label.bind('<Button-1>', lambda e: self._show_actors_menu())
        self.actors_var = tk.StringVar(value="")
        ttk.Entry(actor_row, textvariable=self.actors_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 翻译模型选择 + 翻译按钮（位于“处理进度”上方）
        trans_row = ttk.Frame(left_frame)
        trans_row.pack(fill=tk.X, padx=5, pady=(0, 4))

        ttk.Label(trans_row, text="翻译模型:").pack(side=tk.LEFT)
        self.translation_model_combo = ttk.Combobox(
            trans_row, textvariable=self.translation_model_var,
            values=TRANSLATION_MODELS, state='readonly', width=28)
        self.translation_model_combo.pack(side=tk.LEFT, padx=5)
        self.translation_model_combo.bind('<<ComboboxSelected>>',
                                          self._on_translation_model_change)
        # 右侧三个按钮（从右往左打包，显示顺序为：载入 / 释放 / 翻译）
        self.translate_btn = ttk.Button(trans_row, text="翻译", width=8,
                                        command=self._toggle_translation)
        self.translate_btn.pack(side=tk.RIGHT)
        self.unload_model_btn = ttk.Button(trans_row, text="释放", width=6,
                                           command=self._unload_translation_model)
        self.unload_model_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self.load_model_btn = ttk.Button(trans_row, text="载入", width=6,
                                         command=self._load_translation_model)
        self.load_model_btn.pack(side=tk.RIGHT, padx=(2, 0))

        # 进度条
        progress_frame = ttk.LabelFrame(left_frame, text="处理进度")
        progress_frame.pack(fill=tk.X, padx=5, pady=5)

        self.progress_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL,
                                           length=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)

        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack(pady=(0, 5))

        # 日志区域与视频检索结果：使用可拖动分隔条（PanedWindow），
        # 拖动中间分隔位置即可互相调整上下两个框的高度
        log_pane = ttk.Panedwindow(left_frame, orient=tk.VERTICAL)
        log_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 操作日志（默认权重 1）
        log_frame = ttk.LabelFrame(log_pane, text="操作日志")
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 视频检索结果（默认权重 3，占用较多空间）
        search_frame = ttk.LabelFrame(log_pane, text="视频检索结果")
        self.search_result_text = scrolledtext.ScrolledText(search_frame, height=14,
                                                            state=tk.DISABLED)
        self.search_result_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        log_pane.add(log_frame, weight=1)
        log_pane.add(search_frame, weight=3)

        # ====== 右侧面板 ======
        # 多网页页签栏（位于“抓取到的图片”上方）
        self._create_page_tabs(right_frame)

        # 图片显示区域
        images_frame = ttk.LabelFrame(right_frame, text="抓取到的图片")
        images_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 图区主体：左侧为可滚动画布，右侧为固定的“显示/隐藏缩略图”图标按钮
        # （按钮位于画布之外的独立边栏，因此不随图区滚动条移动）
        images_body = ttk.Frame(images_frame)
        images_body.pack(fill=tk.BOTH, expand=True)

        images_toolbar = ttk.Frame(images_body)
        images_toolbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 4), pady=5)

        self.thumb_toggle_canvas = tk.Canvas(
            images_toolbar, width=THUMB_TOGGLE_BTN_SIZE, height=THUMB_TOGGLE_BTN_SIZE,
            highlightthickness=0, background='#f0f0f0', cursor='hand2')
        self.thumb_toggle_canvas.pack(side=tk.TOP)
        self.thumb_toggle_canvas.bind(
            '<Button-1>', lambda e: self._toggle_thumb_visibility())
        self.thumb_toggle_canvas.bind(
            '<Enter>', lambda e: self._draw_thumb_toggle_icon(hover=True))
        self.thumb_toggle_canvas.bind(
            '<Leave>', lambda e: self._draw_thumb_toggle_icon(hover=False))
        self._draw_thumb_toggle_icon()

        # “页面”图标按钮：位于“显示/隐藏缩略图”图标按钮下方，尺寸与样式完全一致，
        # 点击后从右侧滑出列表页抓取层
        self.page_panel_canvas = tk.Canvas(
            images_toolbar, width=THUMB_TOGGLE_BTN_SIZE, height=THUMB_TOGGLE_BTN_SIZE,
            highlightthickness=0, background='#f0f0f0', cursor='hand2')
        self.page_panel_canvas.pack(side=tk.TOP, pady=(8, 0))
        self.page_panel_canvas.bind('<Button-1>', lambda e: self._open_page_browser())
        self.page_panel_canvas.bind(
            '<Enter>', lambda e: self._draw_page_panel_icon(hover=True))
        self.page_panel_canvas.bind(
            '<Leave>', lambda e: self._draw_page_panel_icon(hover=False))
        self._draw_page_panel_icon()

        # “类别”图标按钮：位于“页面”图标按钮下方，尺寸与样式完全一致，
        # 点击后从右侧滑出类别浏览层（与列表页层同尺寸）
        self.genre_panel_canvas = tk.Canvas(
            images_toolbar, width=THUMB_TOGGLE_BTN_SIZE, height=THUMB_TOGGLE_BTN_SIZE,
            highlightthickness=0, background='#f0f0f0', cursor='hand2')
        self.genre_panel_canvas.pack(side=tk.TOP, pady=(8, 0))
        self.genre_panel_canvas.bind('<Button-1>', lambda e: self._open_genre_browser())
        self.genre_panel_canvas.bind(
            '<Enter>', lambda e: self._draw_genre_panel_icon(hover=True))
        self.genre_panel_canvas.bind(
            '<Leave>', lambda e: self._draw_genre_panel_icon(hover=False))
        self._draw_genre_panel_icon()

        # 图片画布和滚动条
        canvas_frame = ttk.Frame(images_body)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0), pady=5)

        self.images_canvas = tk.Canvas(canvas_frame, background='#f0f0f0')
        images_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL,
                                        command=self.images_canvas.yview)
        self.images_canvas.configure(yscrollcommand=images_scrollbar.set)

        self.images_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        images_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 图片显示框架（宽度跟随画布，保证只出现竖向滚动条）
        self.images_frame = ttk.Frame(self.images_canvas)
        self._images_window = self.images_canvas.create_window((0, 0), window=self.images_frame,
                                                              anchor=tk.NW)

        # 缩略图按 1:1 方框排列，画布宽度变化时重排
        self.images_frame.bind("<Configure>", self._on_images_frame_configure)
        self.images_canvas.bind("<Configure>", self._on_images_canvas_configure)

        # 鼠标滚轮上下滚动
        self._bind_mousewheel(self.images_canvas)
        self._bind_mousewheel(self.images_frame)

        self._apply_thumb_visibility()   # 默认隐藏：初始化即显示“已隐藏”提示

        # 磁力链接区域
        magnets_frame = ttk.LabelFrame(right_frame, text="磁力下载链接")
        magnets_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 磁力链接列表
        magnets_list_frame = ttk.Frame(magnets_frame)
        magnets_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ('name', 'size', 'date')
        self.magnets_tree = ttk.Treeview(magnets_list_frame, columns=columns,
                                        show='headings', height=8)

        self.magnets_tree.heading('name', text='标题')
        self.magnets_tree.heading('size', text='文件大小')
        self.magnets_tree.heading('date', text='分享日期')
        self.magnets_tree.column('name', width=330)
        self.magnets_tree.column('size', width=90)
        self.magnets_tree.column('date', width=110)

        magnets_scrollbar = ttk.Scrollbar(magnets_list_frame, orient=tk.VERTICAL,
                                         command=self.magnets_tree.yview)
        self.magnets_tree.configure(yscrollcommand=magnets_scrollbar.set)

        self.magnets_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        magnets_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 单击任意列 → 完整标题写入日志；单击标题列 / 双击任意列 → 调用系统关联程序打开
        self.magnets_tree.bind('<Button-1>', self._on_magnet_click)
        self.magnets_tree.bind('<Double-1>', self._on_magnet_double_click)

        # 无码/破解标记（-U / -UC / -UNC 等）的行标红（整行文字变红）
        self.magnets_tree.tag_configure(MAGNET_UNCENSORED_TAG,
                                        foreground=MAGNET_UNCENSORED_COLOR)

        ttk.Label(magnets_frame,
                  text=("提示：单击行可把完整标题写入日志；单击标题列或双击调用系统关联程序"
                        "（如迅雷）打开磁力链接"),
                  foreground="gray", font=('', 8)).pack(anchor=tk.W, padx=5)

        # 磁力链接按钮
        magnets_btn_frame = ttk.Frame(magnets_frame)
        magnets_btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(magnets_btn_frame, text="打开选中",
                  command=self._open_selected_magnet).pack(side=tk.LEFT, padx=2)
        ttk.Button(magnets_btn_frame, text="复制选中",
                  command=self._copy_selected_magnet).pack(side=tk.LEFT, padx=2)
        ttk.Button(magnets_btn_frame, text="复制全部",
                  command=self._copy_all_magnets).pack(side=tk.LEFT, padx=2)
        ttk.Button(magnets_btn_frame, text="导出到文件",
                  command=self._export_magnets).pack(side=tk.LEFT, padx=2)

        # 底部按钮（bottom_frame 已在主分栏之前创建并固定在底部）
        self.start_btn = ttk.Button(bottom_frame, text="开始抓取",
                                   command=self._start_grab, width=12)

        self.start_btn = ttk.Button(bottom_frame, text="开始抓取",
                                   command=self._start_grab, width=12)
        self.start_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="检索视频",
                  command=self._search_local_video, width=10).pack(side=tk.LEFT, padx=2)

        # 打开本次检索匹配到的文件夹（在按钮上方弹出选择层）
        self.open_folders_btn = ttk.Button(bottom_frame, text="打开文件夹",
                                           command=self._open_matched_folders_popup,
                                           width=10)
        self.open_folders_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="建立索引",
                  command=self._build_video_index_now, width=10).pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="重建索引",
                  command=lambda: self._search_local_video(force_rebuild=True),
                  width=10).pack(side=tk.LEFT, padx=2)

        self.stop_btn = ttk.Button(bottom_frame, text="停止",
                                  command=self._stop_grab, width=8, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="清空结果",
                  command=self._clear_current_tab, width=10).pack(side=tk.LEFT, padx=2)

        ttk.Button(bottom_frame, text="保存配置",
                  command=self._save_to_config_manager, width=10).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom_frame, text="爬虫设置",
                  command=self._open_crawler_settings, width=10).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom_frame, text="本地设置",
                  command=self._open_local_folder_settings, width=10).pack(side=tk.RIGHT, padx=2)

        # 按已配置的模型文件夹刷新翻译模型下拉菜单（默认不加载任何模型）
        self._refresh_translation_model_combo()
        self._refresh_model_buttons()

    def _bind_events(self):
        """绑定事件"""
        self.url_var.trace('w', lambda *args: self._save_to_config_manager())
        self.max_images_var.trace('w', lambda *args: self._save_to_config_manager())
        self.auto_download_var.trace('w', lambda *args: self._save_to_config_manager())
        self.save_path_var.trace('w', lambda *args: self._save_to_config_manager())
        self.multi_link_var.trace('w', lambda *args: self._save_to_config_manager())
        self.main_selector_var.trace('w', lambda *args: self._save_selector_config())
        self.sample_selector_var.trace('w', lambda *args: self._save_selector_config())
        self.magnet_selector_var.trace('w', lambda *args: self._save_selector_config())
        self.sample_link_var.trace('w', lambda *args: self._save_selector_config())
        self.magnet_ajax_path_var.trace('w', lambda *args: self._save_selector_config())
        self.magnet_ajax_params_var.trace('w', lambda *args: self._save_selector_config())
        # 列表页设置：改完即时保存
        for var in (self.list_container_var, self.list_item_var, self.list_thumb_var,
                    self.list_title_var, self.list_code_var, self.list_date_var,
                    self.list_flag_var, self.list_link_var, self.list_next_var,
                    self.list_alert_var, self.list_page_template_var):
            var.trace('w', lambda *args: self._save_list_config())
        self.list_auto_collect_var.trace('w', lambda *args: self._save_list_config())
        self.list_render_var.trace('w', lambda *args: self._save_list_config())

    def _refresh_list_url_combo(self):
        """刷新列表页浏览层里的网址下拉框取值：网址库 + 各页签保存的网址"""
        combo = getattr(self, '_list_url_combo', None)
        if combo is None:
            return
        values: List[str] = []
        for url in list(self._list_urls) + [str(t.get('url') or '')
                                            for t in getattr(self, 'list_tabs', []) or []]:
            url = (url or '').strip()
            if url and url not in values:
                values.append(url)
        try:
            combo['values'] = values
        except tk.TclError:
            pass

    def _reset_main_selector(self):
        """恢复主图容器为默认值"""
        self.main_selector_var.set(DEFAULT_MAIN_IMAGE_SELECTOR)
        self._log(f"主图容器已恢复默认: {DEFAULT_MAIN_IMAGE_SELECTOR}")

    def _reset_sample_selector(self):
        """恢复剪辑图容器为默认值"""
        self.sample_selector_var.set(DEFAULT_SAMPLE_CONTAINER_SELECTOR)
        self._log(f"剪辑图容器已恢复默认: {DEFAULT_SAMPLE_CONTAINER_SELECTOR}")

    def _reset_sample_link(self):
        """恢复剪辑图链接选择器为默认值"""
        self.sample_link_var.set(DEFAULT_SAMPLE_LINK_SELECTOR)
        self._log(f"剪辑图链接已恢复默认: {DEFAULT_SAMPLE_LINK_SELECTOR}")

    def _reset_magnet_selector(self):
        """恢复磁力表选择器为默认值"""
        self.magnet_selector_var.set(DEFAULT_MAGNET_TABLE_SELECTOR)
        self._log(f"磁力表已恢复默认: {DEFAULT_MAGNET_TABLE_SELECTOR}")

    def _reset_magnet_ajax(self):
        """恢复磁力链异步接口为默认值"""
        self.magnet_ajax_path_var.set(DEFAULT_MAGNET_AJAX_PATH)
        self.magnet_ajax_params_var.set(DEFAULT_MAGNET_AJAX_PARAMS)
        self._log(f"磁力接口已恢复默认: {DEFAULT_MAGNET_AJAX_PATH}")

    def _save_selector_config(self):
        """保存容器选择器等配置到配置文件"""
        if not hasattr(self, 'main_selector_var'):
            return

        main_selector = (self.main_selector_var.get() or '').strip() or DEFAULT_MAIN_IMAGE_SELECTOR
        self.config_manager.set(self.CONFIG_SECTION, 'main_image_selector', main_selector)

        if hasattr(self, 'sample_selector_var'):
            sample_selector = ((self.sample_selector_var.get() or '').strip()
                               or DEFAULT_SAMPLE_CONTAINER_SELECTOR)
            self.config_manager.set(self.CONFIG_SECTION, 'sample_image_selector', sample_selector)

        if hasattr(self, 'sample_link_var'):
            self.config_manager.set(
                self.CONFIG_SECTION, 'sample_link_selector',
                (self.sample_link_var.get() or '').strip() or DEFAULT_SAMPLE_LINK_SELECTOR)

        if hasattr(self, 'magnet_selector_var'):
            magnet_selector = ((self.magnet_selector_var.get() or '').strip()
                               or DEFAULT_MAGNET_TABLE_SELECTOR)
            self.config_manager.set(self.CONFIG_SECTION, 'magnet_table_selector', magnet_selector)

        if hasattr(self, 'auto_render_var'):
            self.config_manager.set(self.CONFIG_SECTION, 'auto_render_on_missing',
                                    bool(self.auto_render_var.get()))

        if hasattr(self, 'auto_save_json_var'):
            self.config_manager.set(self.CONFIG_SECTION, 'auto_save_json',
                                    bool(self.auto_save_json_var.get()))

        if hasattr(self, 'magnet_ajax_path_var'):
            self.config_manager.set(
                self.CONFIG_SECTION, 'magnet_ajax_path',
                (self.magnet_ajax_path_var.get() or '').strip() or DEFAULT_MAGNET_AJAX_PATH)
            self.config_manager.set(
                self.CONFIG_SECTION, 'magnet_ajax_params',
                (self.magnet_ajax_params_var.get() or '').strip() or DEFAULT_MAGNET_AJAX_PARAMS)
            self.config_manager.set(self.CONFIG_SECTION, 'magnet_ajax_enabled',
                                    bool(self.magnet_ajax_enabled_var.get()))

    def _browse_save_path(self):
        """选择保存目录（默认打开当前已设置的目录）"""
        initial = self.save_path_var.get().strip()
        if not initial or not os.path.isdir(initial):
            history = self._save_path_history()
            initial = history[0] if history else ''

        try:
            directory = filedialog.askdirectory(title="选择保存目录",
                                                initialdir=initial or None)
        except Exception:
            directory = filedialog.askdirectory(title="选择保存目录")

        if directory:
            self._set_save_dir(directory)

    def _set_save_dir(self, directory: str):
        """设置保存目录并写入历史（分隔符统一为 Windows 反斜杠 \\）"""
        directory = self._format_display_path((directory or '').strip())
        if not directory:
            return
        self.save_path_var.set(directory)
        self._push_save_path_history(directory)

    def _ensure_save_dir(self, path: str = '') -> bool:
        """逐级创建保存目录（如 c:\\a 不存在而设置 c:\\a\\b\\c\\d，则创建 a\\b\\c\\d 各级）

        返回 True 表示目录已存在或创建成功。
        """
        path = (path or self.save_path_var.get()).strip()
        if not path:
            return False
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception as e:
            self._log(f"创建保存目录失败 {self._format_display_path(path)}: {e}")
            return False

    def _on_save_dir_edit_done(self, event=None):
        """保存目录编辑结束（失焦/回车）时，仅把分隔符统一为 Windows 反斜杠 \\。

        注意：此处**不**逐级创建文件夹——创建目录只在点击“创建视频文件夹”按钮时进行，
        文本框的任何编辑/失焦/回车都不会触发目录创建。
        """
        path = self.save_path_var.get().strip()
        if not path:
            return
        norm = self._format_display_path(path)
        if norm != path:
            self.save_path_var.set(norm)      # 手工输入 Z:/a/b → 显示 Z:\a\b

    def _save_path_history(self) -> List[str]:
        """读取历史保存目录"""
        value = self.config_manager.get(self.CONFIG_SECTION, 'save_path_history', [])
        if not isinstance(value, list):
            return []
        return [p for p in value if isinstance(p, str) and p.strip()]

    def _push_save_path_history(self, directory: str):
        """写入历史保存目录（最近使用的排前面，最多保留 SAVE_DIR_HISTORY_SIZE 个）"""
        history = [p for p in self._save_path_history() if p != directory]
        history.insert(0, directory)
        self.config_manager.set(self.CONFIG_SECTION, 'save_path_history',
                                history[:SAVE_DIR_HISTORY_SIZE])

    def _show_save_dir_history(self, event=None):
        """弹出历史保存目录下拉层"""
        history = self._save_path_history()

        # 先收起可能残留的旧层（_hide_* 内部会把 _save_dir_popup 置空，避免旧回调误关新层）
        self._hide_save_dir_history()

        entry = self.save_dir_entry
        win = tk.Toplevel(self.frame)
        win.overrideredirect(True)
        win.configure(background=SETTINGS_TITLE_BG)
        try:
            win.attributes('-topmost', True)
        except Exception:
            pass

        items = history if history else ['（暂无历史目录）']
        item_height = 28
        entry.update_idletasks()
        width = max(320, entry.winfo_width())
        height = item_height * len(items) + 8
        pos_x = entry.winfo_rootx()
        pos_y = entry.winfo_rooty() + entry.winfo_height() + 2
        win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

        self._save_dir_popup = win

        for path in items:
            row = tk.Frame(win, background=SETTINGS_TITLE_BG)
            row.pack(fill=tk.X, padx=2, pady=1)
            label = tk.Label(row, text=path, anchor=tk.W, background=SETTINGS_TITLE_BG,
                             foreground='#eeeeee', font=('Microsoft YaHei', 9))
            label.pack(fill=tk.X, padx=8, pady=4)

            if path not in history:
                continue

            def select(event, value=path):
                value = self._format_display_path(value)
                self.save_path_var.set(value)
                self._push_save_path_history(value)
                self._hide_save_dir_history()

            def on_enter(event, r=row, lb=label):
                r.configure(background='#3a3a3a')
                lb.configure(background='#3a3a3a')

            def on_leave(event, r=row, lb=label):
                r.configure(background=SETTINGS_TITLE_BG)
                lb.configure(background=SETTINGS_TITLE_BG)

            for widget in (row, label):
                widget.configure(cursor='hand2')
                widget.bind('<Button-1>', select)
                widget.bind('<Enter>', on_enter)
                widget.bind('<Leave>', on_leave)

        # 点击层内空白处也收起；万一该层仍拿到焦点，失焦同样收起（兜底，add='+' 不覆盖）
        win.bind('<Button-1>', lambda e: self._hide_save_dir_history())
        win.bind('<FocusOut>', lambda e: self._hide_save_dir_history(), add='+')

        # 注意：这里不再 focus_force 抢焦点，保证用户仍能继续在输入框里打字。
        # 因此“点别处关闭”不能只依赖失焦，另开启轮询兜底（见 _watch_save_dir_popup）。
        self._start_save_dir_watch()

    def _hide_save_dir_history(self):
        """收起历史保存目录下拉层（可重复调用）"""
        popup, self._save_dir_popup = self._save_dir_popup, None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def _on_global_click(self, event=None):
        """点击应用内任意位置：若点击不在历史下拉层内则收起该层（常驻绑定）"""
        popup = self._save_dir_popup
        if popup is None:
            return
        widget = getattr(event, 'widget', None)
        while widget is not None:
            if widget is popup:
                return
            widget = getattr(widget, 'master', None)
        self._hide_save_dir_history()

    def _on_global_escape(self, event=None):
        """按 Esc 收起历史下拉层（层不抢焦点，故需全局监听）"""
        if self._save_dir_popup is not None:
            self._hide_save_dir_history()

    def _start_save_dir_watch(self):
        """启动“在层外按鼠标左键即收起”的轮询兜底（层展开期间生效）"""
        if self._save_dir_watch_running:
            return
        self._save_dir_watch_running = True
        self._watch_save_dir_popup()

    def _watch_save_dir_popup(self):
        """轮询兜底：在层外按下鼠标左键就收起

        有些点击（点在不接收焦点的控件上、点在窗口标题栏、点到别的程序等）
        既不产生 <FocusOut> 也可能不走 Tk 的绑定，只靠事件绑定无法保证收起，
        因此层展开期间轮询鼠标左键状态，命中即收起。
        """
        if self._save_dir_popup is None:
            self._save_dir_watch_running = False
            return
        try:
            if _left_button_down() and not self._pointer_in_save_dir_popup():
                self._hide_save_dir_history()
                self._save_dir_watch_running = False
                return
        except Exception:
            pass
        try:
            self.frame.after(SAVE_DIR_POPUP_POLL_MS, self._watch_save_dir_popup)
        except Exception:
            self._save_dir_watch_running = False

    def _pointer_in_save_dir_popup(self) -> bool:
        """鼠标当前是否位于历史下拉层（或保存目录输入框）范围内"""
        try:
            x, y = self.frame.winfo_pointerx(), self.frame.winfo_pointery()
        except Exception:
            return True          # 取不到坐标时保守处理，不误收起
        for widget in (self._save_dir_popup, getattr(self, 'save_dir_entry', None)):
            if widget is None:
                continue
            try:
                wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
                if ((wx - 3) <= x <= (wx + widget.winfo_width() + 3)
                        and (wy - 3) <= y <= (wy + widget.winfo_height() + 3)):
                    return True
            except Exception:
                continue
        return False

    def _import_from_clipboard(self):
        """从剪贴板导入URL"""
        try:
            clipboard_content = self.frame.clipboard_get()
            self._log(f"从剪贴板读取: {clipboard_content[:100]}")

            # 解析URL
            url = self._parse_url(clipboard_content)
            if url:
                if self.multi_link_var.get():
                    # 多链接模式：只累积到待抓取列表，点击“抓取”后并发抓取
                    self._enqueue_url(url)
                    self._log(f"已加入待抓取列表: {url}")
                else:
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
        if hasattr(self, 'drop_hint_var'):
            self.drop_hint_var.set(self._default_drop_hint())

    # ========== 拖放区 ==========
    def _default_drop_hint(self) -> str:
        """拖放区默认提示文字"""
        if DND_AVAILABLE:
            return ("把浏览器中的图片拖放到此区域，自动读取图片所在的链接（a 标签）并抓取\n"
                    "双击本区域可复制最近拖入的网址")
        return "拖放功能不可用：请先执行 pip install tkinterdnd2"

    def _copy_dropped_url(self, event=None):
        """双击拖放区：复制网址到系统剪贴板

        多链接模式下复制“当前页签”对应的网址；
        单链接模式下复制最近一次拖入的网址。
        """
        # 多链接模式优先取当前（激活）页签对应的网址
        if getattr(self, 'multi_link_var', None) is not None and self.multi_link_var.get() and self.active_url:
            url = self.active_url
        else:
            url = getattr(self, '_last_dropped_url', None)
        if not url:
            self._log("拖放区还没有可复制的网址（请先拖入链接，或切换到对应页签）")
            if hasattr(self, 'drop_hint_var'):
                self.drop_hint_var.set("尚未有可复制的网址（双击本区域复制当前页签/最近拖入的网址）")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(url)
            self.frame.update_idletasks()   # 立即写入剪贴板，避免程序退出后内容丢失
            self._log(f"已复制拖入的网址到剪贴板: {url}")
            if hasattr(self, 'drop_hint_var'):
                self.drop_hint_var.set(f"已复制到剪贴板: {url[:90]}")
        except tk.TclError as e:
            self._log(f"复制到剪贴板失败: {e}")

    def _create_drop_zone(self, parent):
        """创建拖放区"""
        self.drop_zone = tk.Frame(parent, bg=DROP_ZONE_BG, height=62,
                                  highlightthickness=1,
                                  highlightbackground=DROP_ZONE_BORDER)
        self.drop_zone.pack(fill=tk.X, padx=5, pady=(0, 5))
        self.drop_zone.pack_propagate(False)

        self.drop_hint_var = tk.StringVar(value=self._default_drop_hint())
        self.drop_label = tk.Label(self.drop_zone, textvariable=self.drop_hint_var,
                                   bg=DROP_ZONE_BG, fg='#555555',
                                   font=('Microsoft YaHei', 9), justify=tk.CENTER)
        self.drop_label.pack(fill=tk.BOTH, expand=True)

        # 双击拖放区（或其文字）→ 把最近拖入的网址复制到系统剪贴板
        for widget in (self.drop_zone, self.drop_label):
            widget.bind('<Double-Button-1>', self._copy_dropped_url)

        if not DND_AVAILABLE:
            return

        # 拖放目标需要在窗口被创建/显示（拿到窗口句柄）之后注册
        self.drop_zone.bind('<Map>', self._register_drop_target)
        self.frame.after(300, self._register_drop_target)

    def _register_drop_target(self, event=None):
        """注册拖放目标（只注册一次）"""
        if self._dnd_registered or not DND_AVAILABLE:
            return
        try:
            root = self.drop_zone.winfo_toplevel()
            TkinterDnD.require(root)

            self.drop_zone.drop_target_register(DND_ALL, DND_HTML, DND_URL, DND_TEXT, DND_FILES)
            self.drop_zone.dnd_bind('<<DropEnter>>', self._on_drop_enter)
            self.drop_zone.dnd_bind('<<DropPosition>>', self._on_drop_position)
            self.drop_zone.dnd_bind('<<DropLeave>>', self._on_drop_leave)
            self.drop_zone.dnd_bind(f'<<Drop:{DND_HTML}>>', self._on_drop_html)
            self.drop_zone.dnd_bind(f'<<Drop:{DND_URL}>>', self._on_drop_url)
            self.drop_zone.dnd_bind(f'<<Drop:{DND_TEXT}>>', self._on_drop_text)
            self.drop_zone.dnd_bind(f'<<Drop:{DND_FILES}>>', self._on_drop_files)
            self.drop_zone.dnd_bind('<<Drop>>', self._on_drop_any)

            self._dnd_registered = True
            self._log(f"拖放区已就绪（tkdnd {TkinterDnD.TkdndVersion}），可从浏览器拖入图片")
        except Exception as e:
            # 窗口尚未创建时静默失败，等待 <Map> 事件再次触发
            self._dnd_registered = False
            if 'Map' not in str(e):
                self._log(f"拖放功能初始化失败: {e}")

    def _set_drop_zone_active(self, active: bool):
        """拖放高亮反馈"""
        if not hasattr(self, 'drop_zone'):
            return
        bg = DROP_ZONE_ACTIVE_BG if active else DROP_ZONE_BG
        border = DROP_ZONE_ACTIVE_BORDER if active else DROP_ZONE_BORDER
        self.drop_zone.configure(bg=bg, highlightbackground=border)
        self.drop_label.configure(bg=bg)

    def _on_drop_enter(self, event):
        """拖入拖放区"""
        self._drop_handled = False
        self._set_drop_zone_active(True)
        return DND_ACTION_COPY

    def _on_drop_position(self, event):
        """在拖放区中移动"""
        return DND_ACTION_COPY

    def _on_drop_leave(self, event):
        """离开拖放区"""
        self._set_drop_zone_active(False)
        return DND_ACTION_COPY

    def _on_drop_html(self, event):
        """拖入网页图片/链接（CF_HTML）"""
        self._drop_handled = True
        self._set_drop_zone_active(False)
        self._handle_dropped_html(event.data)
        return DND_ACTION_COPY

    def _on_drop_url(self, event):
        """拖入链接（UniformResourceLocator）"""
        self._drop_handled = True
        self._set_drop_zone_active(False)
        self._apply_dropped_url(self._parse_url(event.data or ""), "拖入的链接")
        return DND_ACTION_COPY

    def _on_drop_text(self, event):
        """拖入文本"""
        self._drop_handled = True
        self._set_drop_zone_active(False)
        self._apply_dropped_url(self._parse_url(event.data or ""), "拖入的文本")
        return DND_ACTION_COPY

    def _on_drop_files(self, event):
        """拖入本地文件（不支持，给出提示）"""
        self._drop_handled = True
        self._set_drop_zone_active(False)
        try:
            files = self.frame.tk.splitlist(event.data)
        except Exception:
            files = [event.data] if event.data else []
        self._log(f"拖入的是本地文件（{len(files)} 个），请改为拖动网页中的图片或链接")
        self.drop_hint_var.set("请拖动网页中的图片或链接，暂不支持本地文件")
        return DND_ACTION_COPY

    def _on_drop_any(self, event):
        """<<Drop>> 兜底：具体类型事件已处理时跳过，避免重复处理"""
        data = event.data

        def _process():
            if self._drop_handled:
                self._drop_handled = False
                return
            self._set_drop_zone_active(False)
            if data and ('<img' in data.lower() or 'SourceURL' in data or 'StartFragment' in data):
                self._handle_dropped_html(data)
            else:
                self._apply_dropped_url(self._parse_url(data or ""), "拖入的内容")

        self.frame.after(1, _process)
        return DND_ACTION_COPY

    @staticmethod
    def _parse_dropped_html(raw_html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """解析浏览器拖放携带的 CF_HTML
        返回 (图片所在 a 标签链接, 图片地址, 图片所在页面地址)
        """
        if not raw_html:
            return None, None, None

        # CF_HTML 头部中的来源页面地址
        source_url = None
        m = re.search(r'(?im)^\s*SourceURL:\s*(\S+)', raw_html)
        if m:
            source_url = m.group(1).strip()

        # 只取真正的拖放片段
        fragment = raw_html
        fm = re.search(r'<!--StartFragment-->(.*?)<!--EndFragment-->', raw_html, re.S | re.I)
        if fm:
            fragment = fm.group(1)

        def resolve(url: Optional[str]) -> Optional[str]:
            """还原实体并把相对地址补全为绝对地址"""
            if not url:
                return None
            url = html.unescape(url.strip())
            if url.startswith(('javascript:', '#')):
                return None
            if url.startswith('//'):
                url = 'https:' + url
            if source_url and not re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', url):
                url = urllib.parse.urljoin(source_url, url)
            return url

        # 图片地址
        img_src = None
        m = re.search(r'<img\b[^>]*?\ssrc\s*=\s*["\']([^"\']+)["\']', fragment, re.S | re.I)
        if m:
            img_src = resolve(m.group(1))

        # 包裹该图片的 a 标签链接
        link = None
        for am in re.finditer(r'<a\b([^>]*)>(.*?)</a>', fragment, re.S | re.I):
            attrs, inner = am.group(1), am.group(2)
            hm = re.search(r'href\s*=\s*["\']([^"\']+)["\']', attrs, re.S | re.I)
            if not hm:
                continue
            if '<img' not in inner.lower():
                continue
            link = resolve(hm.group(1))
            if not img_src:
                im = re.search(r'<img\b[^>]*?\ssrc\s*=\s*["\']([^"\']+)["\']', inner, re.S | re.I)
                if im:
                    img_src = resolve(im.group(1))
            break

        return link, img_src, source_url

    def _handle_dropped_html(self, raw_html: str):
        """处理拖入图片的 HTML 数据，选出最合适的链接"""
        link, img_src, source_url = self._parse_dropped_html(raw_html)

        if img_src:
            self._log(f"拖入的图片: {img_src[:150]}")
        if source_url:
            self._log(f"图片所在页面: {source_url[:150]}")

        # 优先取图片外层 a 标签的链接，其次回退到图片所在页面
        if link and link != img_src:
            target, desc = link, "图片所在链接(a标签)"
        elif source_url:
            target, desc = source_url, "图片所在页面"
        elif img_src:
            target, desc = img_src, "图片地址"
        else:
            target, desc = link, "链接"

        self._apply_dropped_url(target, desc)

    def _apply_dropped_url(self, url: Optional[str], desc: str):
        """应用拖放得到的链接并开始抓取"""
        if not url:
            self.drop_hint_var.set(self._default_drop_hint())
            self._log(f"未能从{desc}中识别出链接")
            messagebox.showwarning("警告", "未能识别拖放内容中的链接，请手动粘贴网页地址")
            return

        # 记录最近拖入的网址，供双击拖放区复制到剪贴板
        self._last_dropped_url = url

        if self.multi_link_var.get():
            # 多链接模式：累积地址并建立页签，立即并发抓取
            self._enqueue_url(url)
            self.drop_hint_var.set(f"[{desc}] 已加入并抓取: {url[:80]}\n双击可复制当前页签网址")
            self._log(f"已接收{desc}（并发抓取）: {url}")
            return

        self.url_var.set(url)
        self.drop_hint_var.set(f"[{desc}] {url[:90]}\n双击可复制该网址")
        self._start_grab()
        # _start_grab 会清空日志区，因此在其之后再补记一条
        self._log(f"已接收{desc}: {url}")

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
        """显示浏览器模式与 JS 渲染设置对话框"""
        dialog = tk.Toplevel(self.frame)
        dialog.title("浏览器模式 / JS 渲染设置")
        dialog.geometry("540x580")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()

        outer = ttk.Frame(dialog)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        def get(key, default):
            return self.config_manager.get(self.CONFIG_SECTION, key, default)

        # ====== 请求头指纹 ======
        fp_frame = ttk.LabelFrame(outer, text="请求头指纹（静态抓取使用的浏览器标识）")
        fp_frame.pack(fill=tk.X, pady=5)

        browser_var = tk.StringVar(value=get('browser_mode', 'chrome'))
        fp_row = ttk.Frame(fp_frame)
        fp_row.pack(fill=tk.X, padx=8, pady=5)
        for text, value in (("Chrome", "chrome"), ("Firefox", "firefox"),
                            ("Edge", "edge"), ("Safari", "safari")):
            ttk.Radiobutton(fp_row, text=text, value=value,
                            variable=browser_var).pack(side=tk.LEFT, padx=5)

        random_ua_var = tk.BooleanVar(value=bool(get('random_ua', True)))
        ttk.Checkbutton(fp_frame, text="每次请求随机 User-Agent（参考脚本默认行为）",
                        variable=random_ua_var).pack(anchor=tk.W, padx=8, pady=(0, 6))

        # ====== JS 渲染 ======
        render_frame = ttk.LabelFrame(outer, text="JS 渲染（读取页面打开后才生成的内容）")
        render_frame.pack(fill=tk.X, pady=5)

        render_var = tk.BooleanVar(value=bool(get('render_js', False)))
        ttk.Checkbutton(render_frame, text="启用浏览器渲染（需要 playwright 或 selenium）",
                        variable=render_var).pack(anchor=tk.W, padx=8, pady=(5, 2))

        engine_row = ttk.Frame(render_frame)
        engine_row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(engine_row, text="渲染引擎:").pack(side=tk.LEFT)
        engine_var = tk.StringVar(value=get('render_engine', 'playwright'))
        ttk.Radiobutton(engine_row,
                        text="Playwright" + ("（已安装）" if PLAYWRIGHT_AVAILABLE else "（未安装）"),
                        value="playwright", variable=engine_var).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(engine_row,
                        text="Selenium" + ("（已安装）" if SELENIUM_AVAILABLE else "（未安装）"),
                        value="selenium", variable=engine_var).pack(side=tk.LEFT, padx=5)

        headless_var = tk.BooleanVar(value=bool(get('headless', True)))
        block_img_var = tk.BooleanVar(value=bool(get('block_images', True)))
        opt_row = ttk.Frame(render_frame)
        opt_row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Checkbutton(opt_row, text="无头模式", variable=headless_var).pack(side=tk.LEFT)
        ttk.Checkbutton(opt_row, text="拦截图片（加快加载）",
                        variable=block_img_var).pack(side=tk.LEFT, padx=10)

        timeout_row = ttk.Frame(render_frame)
        timeout_row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(timeout_row, text="渲染超时(秒):").pack(side=tk.LEFT)
        btimeout_var = tk.IntVar(value=get('browser_timeout', 30))
        ttk.Spinbox(timeout_row, from_=5, to=120, textvariable=btimeout_var,
                    width=5).pack(side=tk.LEFT, padx=5)

        sel_row = ttk.Frame(render_frame)
        sel_row.pack(fill=tk.X, padx=8, pady=(2, 4))
        ttk.Label(sel_row, text="等待出现的CSS选择器:").pack(side=tk.LEFT)
        selector_var = tk.StringVar(value=get('wait_for_selector', ''))
        ttk.Entry(sel_row, textvariable=selector_var, width=26).pack(side=tk.LEFT, padx=5)

        ttk.Label(render_frame,
                  text="提示：若静态源码里找不到磁力链或图片，勾选渲染后重试，"
                       "并用“测试爬取内容”对比两种源码即可确认是否为 JS 动态生成。\n"
                       "磁力表等异步内容可在上方填写等待选择器，例如 table#magnet-table。",
                  foreground="gray", wraplength=490,
                  justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=(0, 6))

        # ====== 按钮 ======
        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill=tk.X, pady=8)

        def save():
            self.config_manager.set(self.CONFIG_SECTION, 'browser_mode', browser_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'random_ua', random_ua_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'render_js', render_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'render_engine', engine_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'headless', headless_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'block_images', block_img_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'browser_timeout', btimeout_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'wait_for_selector', selector_var.get())
            self._log(f"浏览器/渲染设置已保存（JS渲染: {'开' if render_var.get() else '关'}）")
            dialog.destroy()

        ttk.Button(btn_frame, text="确定", command=save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)

    def _show_connection_dialog(self):
        """显示连接设置对话框"""
        dialog = tk.Toplevel(self.frame)
        dialog.title("连接设置")
        dialog.geometry("400x330")
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

        # 失败重试（对齐参考脚本 retry_times）
        retry_frame = ttk.Frame(frame)
        retry_frame.pack(fill=tk.X, pady=5)
        ttk.Label(retry_frame, text="失败重试次数:").pack(side=tk.LEFT)
        retry_var = tk.IntVar(value=self.config_manager.get(self.CONFIG_SECTION, 'retry_times', 3))
        ttk.Spinbox(retry_frame, from_=1, to=10, textvariable=retry_var, width=5).pack(side=tk.LEFT, padx=5)

        # 请求前随机延迟（对齐参考脚本 interval_min / interval_max）
        interval_frame = ttk.Frame(frame)
        interval_frame.pack(fill=tk.X, pady=5)
        ttk.Label(interval_frame, text="请求前随机延迟(毫秒):").pack(side=tk.LEFT)
        interval_min_var = tk.IntVar(value=self.config_manager.get(self.CONFIG_SECTION, 'interval_min', 0))
        ttk.Spinbox(interval_frame, from_=0, to=5000, increment=100,
                    textvariable=interval_min_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(interval_frame, text="~").pack(side=tk.LEFT)
        interval_max_var = tk.IntVar(value=self.config_manager.get(self.CONFIG_SECTION, 'interval_max', 0))
        ttk.Spinbox(interval_frame, from_=0, to=5000, increment=100,
                    textvariable=interval_max_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(interval_frame, text="(0 表示不延迟)", foreground="gray").pack(side=tk.LEFT, padx=5)

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
            self.config_manager.set(self.CONFIG_SECTION, 'retry_times', retry_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'interval_min', interval_min_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'interval_max', interval_max_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'proxy_mode', proxy_mode_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'proxy_url', proxy_url_var.get())
            self.config_manager.set(self.CONFIG_SECTION, 'connection_mode', conn_mode_var.get())
            self._log("连接设置已保存")
            dialog.destroy()

        ttk.Button(btn_frame, text="确定", command=save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)

    def _save_to_config_manager(self):
        """保存配置（合并写入，避免覆盖同分区内的其它设置）"""
        section = dict(self.config_manager.get_section(self.CONFIG_SECTION) or {})
        selected_model = self._current_translation_model()
        section.update({
            'max_images': self.max_images_var.get(),
            'auto_download': self.auto_download_var.get(),
            'save_path': self.save_path_var.get(),
            'multi_link': bool(self.multi_link_var.get()),
            'translation_model': selected_model,
        })
        self.config_manager.set_section(self.CONFIG_SECTION, section, auto_save=True)
        # 记住本次保存时选中的模型，下次打开默认选中
        self._selected_translation_model = selected_model

    def _load_from_config_manager(self):
        """加载配置"""
        section_config = self.config_manager.get_section(self.CONFIG_SECTION)

        if section_config:
            self.max_images_var.set(section_config.get('max_images', 10))
            self.auto_download_var.set(section_config.get('auto_download', False))
            self.save_path_var.set(
                self._format_display_path(section_config.get('save_path', '')))
            self.main_selector_var.set(
                section_config.get('main_image_selector', DEFAULT_MAIN_IMAGE_SELECTOR))
            self.sample_selector_var.set(
                section_config.get('sample_image_selector', DEFAULT_SAMPLE_CONTAINER_SELECTOR))
            self.magnet_selector_var.set(
                section_config.get('magnet_table_selector', DEFAULT_MAGNET_TABLE_SELECTOR))
            self.sample_link_var.set(
                section_config.get('sample_link_selector', DEFAULT_SAMPLE_LINK_SELECTOR))
            self.auto_render_var.set(
                section_config.get('auto_render_on_missing', True))
            self.magnet_ajax_path_var.set(
                section_config.get('magnet_ajax_path', DEFAULT_MAGNET_AJAX_PATH))
            self.magnet_ajax_params_var.set(
                section_config.get('magnet_ajax_params', DEFAULT_MAGNET_AJAX_PARAMS))
            self.magnet_ajax_enabled_var.set(
                section_config.get('magnet_ajax_enabled', True))
            self.auto_save_json_var.set(
                section_config.get('auto_save_json', False))
            self.multi_link_var.set(bool(section_config.get('multi_link', False)))

        # 列表页设置（网址库 + 解析规则）
        self._load_list_config()

        # 列表页磁盘缓存容量上限（MB，默认 50），本地设置可改
        try:
            mb = int(section_config.get('list_cache_max_mb', 50) if section_config else 50)
        except (TypeError, ValueError):
            mb = 50
        if mb < 1:
            mb = 1
        self._list_cache_max_bytes = mb * 1024 * 1024

    def _log(self, message: str):
        """记录日志（线程安全：后台线程调用时自动转交主线程执行）"""
        if threading.current_thread() is not threading.main_thread():
            self._run_in_main_thread(lambda: self._write_log(message))
            return
        self._write_log(message)

    def _write_log(self, message: str):
        """真正写日志（只能在主线程调用）"""
        if not self.is_standalone and self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

        # 同时显示在日志区域
        if hasattr(self, 'log_text'):
            try:
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, message + '\n')
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
            except tk.TclError:
                pass

    def _on_images_frame_configure(self, event):
        """图片框架配置事件"""
        self.images_canvas.configure(scrollregion=self.images_canvas.bbox("all"))

    def _on_images_canvas_configure(self, event):
        """画布尺寸变化：内框宽度跟随画布，并重排缩略图（避免出现横向滚动条）"""
        try:
            self.images_canvas.itemconfigure(self._images_window, width=event.width)
        except Exception:
            pass
        self._relayout_images()

    def _bind_mousewheel(self, widget, canvas=None):
        """绑定鼠标滚轮到画布滚动（默认滚动画布为图片区）"""
        try:
            widget.bind('<MouseWheel>', lambda e: self._on_mousewheel(e, canvas), add='+')
        except Exception:
            pass

    def _bind_mousewheel_tree(self, widget, canvas=None):
        """递归给控件树绑定鼠标滚轮（鼠标停在子控件如输入框上时也能滚动画布）"""
        self._bind_mousewheel(widget, canvas)
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._bind_mousewheel_tree(child, canvas)

    def _on_mousewheel(self, event, canvas=None):
        """鼠标滚轮上下滚动"""
        try:
            (canvas or self.images_canvas).yview_scroll(int(-1 * (event.delta / 120)), 'units')
        except Exception:
            pass
        return 'break'

    def _relayout_images(self):
        """按画布宽度重排缩略图（横向不超出，列数自适应）"""
        if not hasattr(self, 'images_frame') or not self.thumb_cells:
            return

        try:
            width = max(self.images_canvas.winfo_width(), THUMB_SIZE)
        except Exception:
            width = THUMB_SIZE

        columns = max(1, width // (THUMB_SIZE + THUMB_PAD))
        for index, cell in enumerate(self.thumb_cells):
            row, col = divmod(index, columns)
            cell.grid(row=row, column=col, padx=THUMB_PAD // 2, pady=THUMB_PAD // 2)

        self.images_frame.update_idletasks()
        self.images_canvas.configure(scrollregion=self.images_canvas.bbox('all'))

    # ========== 视频列表页浏览层（从主窗口右侧滑出） ==========
    def _is_list_win_alive(self) -> bool:
        """列表页浏览层是否还活着"""
        try:
            return self._list_win is not None and bool(self._list_win.winfo_exists())
        except Exception:
            return False

    def _list_panel_geometry(self) -> Tuple[int, int, int, int]:
        """按主窗口当前位置与尺寸计算列表页层的目标几何，返回 (宽, 高, x, y)

        层始终贴在主窗口右内侧：x = 主窗口左边 + 主窗口宽 - 层宽，高度与主窗口一致。
        """
        root = self.frame.winfo_toplevel()
        try:
            rx, ry = root.winfo_rootx(), root.winfo_rooty()
            rw = max(root.winfo_width(), 640)
            rh = max(root.winfo_height(), 480)
        except Exception:
            rx, ry, rw, rh = 0, 0, 1200, 800

        width = max(480, int(rw * LIST_PANEL_WIDTH_RATIO))
        return width, rh, rx + rw - width, ry

    def _apply_list_panel_geometry(self, offset: Optional[float] = None):
        """定位列表页层，并按滑入进度摆放层内的内容容器

        与类别层同一套做法：层窗口始终停在目标位置（不会先跑到主窗口/屏幕外面），
        滑入动画改为在层内横向平移内容容器（超出层右边界的部分会被自动裁掉）。

        :param offset: 内容容器的横向偏移（层宽 → 完全在右侧外；0 → 完全显示），
                       None 时沿用当前偏移
        """
        if offset is not None:
            self._list_panel_offset = offset
        win = self._list_win
        if win is None:
            return
        width, height, pos_x, pos_y = self._list_panel_geometry()
        try:
            geom = f"{width}x{height}+{int(pos_x)}+{int(pos_y)}"
            # 几何没变化就不再下发（拖动主窗口/滑动期间会连续触发，重复下发会拖慢界面）
            if geom != getattr(self, '_list_last_geom', None):
                self._list_last_geom = geom
                win.geometry(geom)
        except tk.TclError:
            return
        self._apply_list_panel_offset()

    def _apply_list_panel_offset(self, offset: Optional[float] = None):
        """按滑入进度平移列表页层的内容容器（层窗口本身不动，滑动更流畅）"""
        if offset is None:
            offset = getattr(self, '_list_panel_offset', 0.0)
        else:
            self._list_panel_offset = offset
        content = getattr(self, '_list_content', None)
        if content is None:
            return
        try:
            content.place_configure(x=int(offset), y=0, relwidth=1.0, relheight=1.0)
        except tk.TclError:
            pass

    def _bind_page_panel_to_root(self):
        """把列表页层与主窗口位置锁定：主窗口移动/缩放/最小化/还原时层同步跟随"""
        root = self.frame.winfo_toplevel()
        bindings = []
        for sequence, handler in (('<Configure>', self._on_root_configure),
                                  ('<Map>', self._on_root_map),
                                  ('<Unmap>', self._on_root_unmap),
                                  ('<Button-1>', self._on_root_click)):
            try:
                bindings.append((sequence, root.bind(sequence, handler, add='+')))
            except Exception:
                pass
        self._list_root_binds = bindings

    def _unbind_page_panel_from_root(self):
        """解除列表页层与主窗口的位置锁定"""
        try:
            root = self.frame.winfo_toplevel()
        except Exception:
            self._list_root_binds = []
            return
        for sequence, func_id in list(getattr(self, '_list_root_binds', []) or []):
            try:
                root.unbind(sequence, func_id)
            except Exception:
                pass
        self._list_root_binds = []

    def _is_root_event(self, event) -> bool:
        """判断事件是否来自主窗口本身（子控件的同名事件不处理）"""
        if event is None:
            return True
        try:
            return event.widget is self.frame.winfo_toplevel()
        except Exception:
            return False

    def _on_root_configure(self, event=None):
        """主窗口移动或缩放：列表页层同步跟随（表现为主窗口内的层）"""
        if not self._is_list_win_alive() or not self._is_root_event(event):
            return
        try:
            self._apply_list_panel_geometry()
            self._list_win.lift()
        except Exception:
            pass

    def _on_root_unmap(self, event=None):
        """主窗口最小化/隐藏：列表页层一起隐藏"""
        if not self._is_list_win_alive() or not self._is_root_event(event):
            return
        try:
            self._list_win.withdraw()
        except tk.TclError:
            pass

    def _on_root_map(self, event=None):
        """主窗口还原显示：列表页层一起显示并重新对齐（仅当层被用户打开时）"""
        if not self._is_list_win_alive() or not self._is_root_event(event):
            return
        try:
            # 层是被用户“关闭”（隐藏）时，主窗口还原不要自动弹出
            if getattr(self, '_list_win_hidden', False):
                return
            self._apply_list_panel_geometry()
            self._list_win.deiconify()
            self._list_win.lift()
        except tk.TclError:
            pass

    def _on_root_click(self, event=None):
        """点击主窗口左侧空位（弹出层未遮住的部分）自动关闭列表页层"""
        if not self._is_list_win_alive() or event is None:
            return
        try:
            # 只处理主窗口内的点击（弹出层与其它窗口的点击不处理）
            if event.widget is not None and \
                    event.widget.winfo_toplevel() is not self.frame.winfo_toplevel():
                return
            panel_x = self._list_win.winfo_rootx()
            if event.x_root < panel_x:
                self._close_page_browser()
        except Exception:
            pass

    def _open_page_browser(self):
        """打开“视频列表页”浏览层：从主窗口右侧滑出，宽度为主窗口的 80%

        该层与主窗口位置锁定：主窗口移动、缩放、最小化、还原时层都会同步跟随，
        看起来就像层是主窗口内的一个容器。
        """
        if self._is_list_win_alive():
            # 已存在（曾被隐藏而非销毁）：直接显示，保留已浏览内容与滚动位置，不重建、不刷新
            win = self._list_win
            self._list_win_hidden = False
            try:
                self._apply_list_panel_geometry()   # 重新对齐主窗口（期间可能被拖动过）
                win.deiconify()
                win.lift()
                win.focus_force()
            except tk.TclError:
                pass
            self._bind_page_panel_to_root()
            return

        root = self.frame.winfo_toplevel()
        try:
            root.update_idletasks()
        except Exception:
            pass

        panel_w, panel_h, end_x, end_y = self._list_panel_geometry()

        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.configure(background=LIST_PANEL_BG)
        # 不使用全局置顶(-topmost)：transient 让层只保持在主窗口上方，
        # 其它软件窗口获得焦点后可正常遮挡本层
        win.transient(root)
        # 层直接停在最终位置（始终在主窗口之内），滑入动画只移动层内的内容容器
        win.geometry(f"{panel_w}x{panel_h}+{end_x}+{end_y}")
        self._list_win = win

        # 滑动内容容器：先整体放到层右侧之外（被裁掉、不可见）
        content = tk.Frame(win, background=LIST_PANEL_BG)
        self._list_content = content
        self._apply_list_panel_geometry(float(panel_w))
        # 显式显示并置于主窗口上方（否则 overrideredirect+transient 的层会落在主窗口之后，
        # 需等到拖动主窗口触发 Configure 才被 lift 显示出来）
        try:
            win.deiconify()
            win.update_idletasks()
            win.lift(root)
        except Exception:
            pass

        self._build_page_browser(content)
        self._refresh_list_url_combo()

        # 网址暂存：恢复上次活动的页签及其网址与页码（页签按类别保存，各自记住进度）
        self._list_active_tab = self._load_active_tab_index()
        self._render_list_tabs()
        tab = self._list_active_tab_dict() or {}
        last_url = str(tab.get('url') or '')
        page = int(tab.get('page') or 0)
        if last_url:
            self.list_url_entry_var.set(last_url)
            self.list_page_var.set(str(page) if page else '')

        self._bind_page_panel_to_root()   # 位置锁定：跟随主窗口移动
        self._slide_in_panel()
        win.focus_force()

        self._list_win_hidden = False

        if last_url:
            # 稍等滑动动画结束后自动载入“当前活动页签”的网址与页码。
            # 注意：用延迟回调再读取当前活动页签，避免此期间活动页签已被切换
            # （如从详情页点击演员/系列打开新页签）后又被旧的首页网址覆盖。
            self.frame.after(280, self._list_auto_open_active_tab)

    def _list_auto_open_active_tab(self):
        """浏览器刚弹出后自动加载“当前活动页签”（延迟读取，避免被切换操作覆盖）"""
        tab = self._list_active_tab_dict()
        if not tab:
            return
        url = str(tab.get('url') or '').strip()
        page = int(tab.get('page') or 0)
        if url:
            self._list_open_url(self._list_tab_target_url(url, page))

    def _slide_in_panel(self):
        """右侧滑入动画：只平移层内的内容容器（层窗口位置保持不变）"""
        total = max(1, LIST_PANEL_ANIM_STEPS)
        width, _, _, _ = self._list_panel_geometry()
        state = {'i': 0}
        if getattr(self, '_list_content', None) is None:
            self._apply_list_panel_geometry(0.0)
            return

        def _step():
            if not self._is_list_win_alive():
                return
            state['i'] += 1
            if state['i'] >= total:
                self._apply_list_panel_offset(0.0)
                try:
                    self._list_win.lift()
                except Exception:
                    pass
                return
            self._apply_list_panel_offset(width * (1 - state['i'] / total))
            self._list_win.after(LIST_PANEL_ANIM_MS, _step)

        _step()

    def _build_page_browser(self, host):
        """构建列表页浏览层界面（浅色风格，与主窗口一致）

        :param host: 内容容器（滑入时整体平移的那个 Frame），界面元素都挂在它下面
        """
        win = self._list_win          # 层窗口本身：只用于挂窗口级绑定
        # 打开列表层时按容量上限回收旧缓存（默认 50MB），避免历史缓存无限膨胀
        try:
            self._list_trim_cache()
        except Exception:
            pass
        # 左侧明显颜色边条：让弹出层边界与主窗口背景清楚地区分开
        tk.Frame(host, width=6, background=LIST_PANEL_EDGE_BG).pack(
            side=tk.LEFT, fill=tk.Y)

        # 标题栏
        title_bar = tk.Frame(host, background=LIST_PANEL_BAR_BG, height=42)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="视频列表页抓取", background=LIST_PANEL_BAR_BG,
                 foreground='#222222', font=('Microsoft YaHei', 11, 'bold')).pack(
            side=tk.LEFT, padx=14)
        tk.Label(title_bar, text="点击缩略图即可加入主窗口抓取该视频详情",
                 background=LIST_PANEL_BAR_BG, foreground=LIST_PANEL_FG_DIM,
                 font=('Microsoft YaHei', 9)).pack(side=tk.LEFT, padx=6)
        ttk.Button(title_bar, text="关闭", width=8,
                   command=self._close_page_browser).pack(side=tk.RIGHT, padx=10, pady=7)

        # 页签栏（每个页签配套保存网址与页码，可自定义名称、右键重命名/删除）
        tabs_outer = tk.Frame(host, height=30, background=LIST_PANEL_BG)
        tabs_outer.pack(fill=tk.X, padx=10, pady=(6, 0))
        tabs_outer.pack_propagate(False)

        self._list_tabs_canvas = tk.Canvas(tabs_outer, height=28, highlightthickness=0,
                                           background=LIST_PANEL_BG)
        self._list_tabs_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._list_tabs_inner = tk.Frame(self._list_tabs_canvas, background=LIST_PANEL_BG)
        self._list_tabs_window = self._list_tabs_canvas.create_window(
            (0, 0), window=self._list_tabs_inner, anchor=tk.NW)
        self._list_tabs_inner.bind(
            '<Configure>',
            lambda e: self._list_tabs_canvas.configure(
                scrollregion=self._list_tabs_canvas.bbox('all')))
        ttk.Button(tabs_outer, text="＋", width=3,
                   command=self._list_tab_new).pack(side=tk.RIGHT, padx=(4, 0))
        self._render_list_tabs()

        # 工具条
        toolbar = ttk.Frame(host, padding=(10, 6, 10, 2))
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="网址:").pack(side=tk.LEFT)
        self._list_url_combo = ttk.Combobox(toolbar, textvariable=self.list_url_entry_var,
                                            width=58)
        self._list_url_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self._list_url_combo.bind('<Return>', lambda e: self._list_open_url())
        self._list_url_combo.bind('<<ComboboxSelected>>', lambda e: self._list_open_url())
        ttk.Button(toolbar, text="打开", width=7,
                   command=self._list_open_url).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="刷新", width=7,
                   command=lambda: self._list_open_url(self.list_url_entry_var.get(),
                                                       use_cache=False)).pack(
            side=tk.LEFT, padx=2)

        # 搜索行：沿用页面搜索框逻辑，在新页签中打开搜索结果。
        # 右侧同时放置“浏览器渲染”开关、视图切换图标与页码标签，
        # 避免翻页行过宽在默认窗口宽度下被遮挡
        search_row = ttk.Frame(host, padding=(10, 2, 10, 4))
        search_row.pack(fill=tk.X)
        ttk.Label(search_row, text="搜索:").pack(side=tk.LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self._list_search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=5)
        search_entry.bind('<Return>', lambda e: self._list_do_search())
        ttk.Button(search_row, text="搜索", width=8,
                   command=self._list_do_search).pack(side=tk.LEFT, padx=2)

        # 右侧：页码标签（最右）、视图切换图标、“浏览器渲染”开关
        # （先按 RIGHT 打包的排在最右；说明文字最后打包，窗口过窄时优先被裁剪）
        tk.Label(search_row, textvariable=self._list_page_label_var,
                 foreground=LIST_PANEL_FG_DIM,
                 background=LIST_PANEL_BG).pack(side=tk.RIGHT, padx=6)

        # 两个小图标按钮（风格与主窗口“显示/隐藏缩略图”一致）
        icon_bar = ttk.Frame(search_row)
        icon_bar.pack(side=tk.RIGHT, padx=(0, 4))
        # ① 瀑布流缩略图 / 列表 切换
        self._list_view_canvas = tk.Canvas(
            icon_bar, width=THUMB_TOGGLE_BTN_SIZE, height=THUMB_TOGGLE_BTN_SIZE,
            highlightthickness=0, background=LIST_PANEL_BG, cursor='hand2')
        self._list_view_canvas.pack(side=tk.LEFT, padx=(0, 4))
        self._list_view_canvas.bind(
            '<Button-1>', lambda e: self._toggle_list_view_mode())
        self._list_view_canvas.bind(
            '<Enter>', lambda e: self._draw_list_view_icon(hover=True))
        self._list_view_canvas.bind(
            '<Leave>', lambda e: self._draw_list_view_icon(hover=False))
        self._draw_list_view_icon()
        # ② 显示 / 隐藏缩略图（隐藏时全部替换为空白图）
        self._list_thumb_canvas = tk.Canvas(
            icon_bar, width=THUMB_TOGGLE_BTN_SIZE, height=THUMB_TOGGLE_BTN_SIZE,
            highlightthickness=0, background=LIST_PANEL_BG, cursor='hand2')
        self._list_thumb_canvas.pack(side=tk.LEFT)
        self._list_thumb_canvas.bind(
            '<Button-1>', lambda e: self._toggle_list_thumbs())
        self._list_thumb_canvas.bind(
            '<Enter>', lambda e: self._draw_list_thumb_icon(hover=True))
        self._list_thumb_canvas.bind(
            '<Leave>', lambda e: self._draw_list_thumb_icon(hover=False))
        self._draw_list_thumb_icon()

        ttk.Checkbutton(search_row, text="浏览器渲染",
                        variable=self.list_render_var,
                        command=self._save_list_config).pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Label(search_row, text="（番号 / 影片名 / 演员名，结果在新页签中打开）",
                  foreground=LIST_PANEL_FG_DIM,
                  font=('Microsoft YaHei', 8)).pack(side=tk.LEFT, padx=6)

        nav = ttk.Frame(host, padding=(10, 2, 10, 4))
        nav.pack(fill=tk.X)
        ttk.Button(nav, text="◀ 上一页", width=10,
                   command=self._list_prev_page).pack(side=tk.LEFT, padx=2)
        ttk.Button(nav, text="下一页 ▶", width=10,
                   command=self._list_next_page).pack(side=tk.LEFT, padx=2)
        ttk.Label(nav, text="页码:").pack(side=tk.LEFT, padx=(14, 2))
        ttk.Entry(nav, textvariable=self.list_page_var, width=7).pack(side=tk.LEFT)
        ttk.Button(nav, text="跳转", width=7,
                   command=self._list_goto_page).pack(side=tk.LEFT, padx=4)
        # 多页连续抓取：从当前网址起连续抓取 N 页（1 = 仅当前页）
        ttk.Label(nav, text="显示页数:").pack(side=tk.LEFT, padx=(12, 2))
        pages_entry = ttk.Entry(nav, textvariable=self.list_pages_var, width=4)
        pages_entry.pack(side=tk.LEFT)
        pages_entry.bind('<Return>', lambda e: self._save_list_config())
        # {#} 数字占位符补零位数（配合主窗口“批量 {#} 起止”使用）
        ttk.Label(nav, text="位数:").pack(side=tk.LEFT, padx=(8, 2))
        pad_entry = ttk.Entry(nav, textvariable=self.list_pad_var, width=3)
        pad_entry.pack(side=tk.LEFT)
        pad_entry.bind('<Return>', lambda e: self._save_list_config())
        ttk.Label(nav, text="（位数 = {#} 补零）",
                  foreground=LIST_PANEL_FG_DIM,
                  font=('Microsoft YaHei', 8)).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(nav, text="自动收集公告层网址",
                        variable=self.list_auto_collect_var,
                        command=self._save_list_config).pack(side=tk.LEFT, padx=14)

        # 翻译按钮：把当前列表页所有视频标题翻译为中文（需先在主窗口载入翻译模型）。
        # 按 RIGHT 打包排在翻页行最右侧
        self._list_translate_btn = ttk.Button(nav, text="翻译", width=8,
                                              command=self._list_on_translate_click)
        self._list_translate_btn.pack(side=tk.RIGHT, padx=(0, 10))

        # 状态条
        status = tk.Frame(host, background=LIST_PANEL_STATUS_BG, height=24)
        status.pack(fill=tk.X)
        status.pack_propagate(False)
        tk.Label(status, textvariable=self._list_status_var, background=LIST_PANEL_STATUS_BG,
                 foreground='#444444', anchor='w',
                 font=('Microsoft YaHei', 9)).pack(side=tk.LEFT, padx=10)

        # 缩略图区
        body = tk.Frame(host, background=LIST_PANEL_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self._list_canvas = tk.Canvas(body, background=LIST_PANEL_AREA_BG,
                                      highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self._list_canvas.yview)
        self._list_canvas.configure(yscrollcommand=scrollbar.set)
        self._list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._list_items_frame = tk.Frame(self._list_canvas, background=LIST_PANEL_AREA_BG)
        self._list_window = self._list_canvas.create_window(
            (0, 0), window=self._list_items_frame, anchor=tk.NW)

        # 注意：滑动层/拖动主窗口时，层内每个控件都会收到“位置变化”的 Configure，
        # 若不加判断就重排，每一帧都要把全部卡片重新 grid 一遍（列表长时非常卡）。
        # 因此下面两个回调只在“尺寸真的变了”时才工作。
        self._list_items_frame.bind('<Configure>', self._on_list_items_frame_configure)
        self._list_canvas.bind('<Configure>', self._on_list_canvas_configure)
        self._bind_mousewheel(self._list_canvas, self._list_canvas)
        self._bind_mousewheel(self._list_items_frame, self._list_canvas)

        # 点击浏览层任意位置（含空白区）：把主窗口与弹出层作为一个整体
        # 提到其它应用之上（add='+' 不影响各控件自身的点击逻辑）
        win.bind('<Button-1>', self._activate_app_group, add='+')

        self._list_status_var.set("请输入或从网址库选择列表页地址，点击“打开”开始抓取")

    def _on_list_canvas_configure(self, event=None):
        """画布尺寸变化时才重排卡片（仅位置变化时直接忽略）"""
        canvas = self._list_canvas
        if canvas is None:
            return
        try:
            size = (event.width, event.height) if event is not None else (
                canvas.winfo_width(), canvas.winfo_height())
        except (tk.TclError, AttributeError):
            return
        if size == getattr(self, '_list_canvas_size', None):
            return
        self._list_canvas_size = size
        self._list_relayout()

    def _on_list_items_frame_configure(self, event=None):
        """内容区尺寸变化时才刷新滚动范围（仅位置变化时直接忽略）"""
        frame = self._list_items_frame
        canvas = self._list_canvas
        if frame is None or canvas is None:
            return
        try:
            size = (event.width, event.height) if event is not None else (
                frame.winfo_width(), frame.winfo_height())
        except (tk.TclError, AttributeError):
            return
        if size == getattr(self, '_list_items_frame_size', None):
            return
        self._list_items_frame_size = size
        try:
            canvas.configure(scrollregion=canvas.bbox('all'))
        except tk.TclError:
            pass

    def _activate_app_group(self, event=None):
        """点击弹出层任意位置时，把主窗口与弹出层作为一个整体激活置顶

        Windows 下点击 Toplevel 只会抬升该 Toplevel，主窗口仍被其它应用
        盖住；这里先抬升主窗口，再把弹出层抬回主窗口之上，使整个应用
        （主窗口 + 弹出层）覆盖在其它应用上方。
        """
        try:
            root = self.frame.winfo_toplevel()
            root.deiconify()          # 主窗口被最小化时一并还原
            root.lift()
            # 直接用记录的弹出层窗口，避免依赖 event.widget
            # （点击到已销毁/路径无法解析的控件时 event.widget 可能为字符串路径名，
            # 在其上调用 winfo_toplevel() 会抛 AttributeError）。
            # 类别层与列表页层共用此处理：优先取当前存活的那个
            panel = None
            if self._is_genre_win_alive():
                panel = self._genre_win
            elif self._is_list_win_alive():
                panel = getattr(self, '_list_win', None)
            else:
                panel = getattr(self, '_list_win', None)
            if panel is not None and panel is not root:
                panel.lift()          # 弹出层保持在主窗口之上
                # 关键：deiconify / lift 都会把焦点从刚点击的输入框上夺走
                # （实测：仅调用 root.deiconify() 或 lift() 就会让焦点落到主窗口）。
                # 因此必须在“抬升动作全部做完之后”再把焦点强制交还给输入框，
                # 否则网址/搜索/页码框点击后没有光标、无法输入。
                w = event.widget if event is not None else None
                if self._is_editable_widget(w):
                    self._force_entry_focus(event)
                else:
                    try:
                        panel.focus_set()
                    except tk.TclError:
                        pass
        except tk.TclError:
            pass

    @staticmethod
    def _is_editable_widget(w) -> bool:
        """判断控件是否为可编辑输入控件（网址/搜索/页码框等）"""
        if w is None:
            return False
        try:
            cls = w.winfo_class()
        except Exception:
            return False
        return cls in ('TEntry', 'Entry', 'TCombobox', 'Combobox',
                       'Text', 'TSpinbox', 'Spinbox')

    def _force_entry_focus(self, event=None):
        """把键盘焦点强制交还给被点击的输入框，并把光标定位到点击处

        必须在所有 deiconify / lift 动作完成之后调用：这些抬升操作会把焦点
        从输入框上夺走（实测仅 root.deiconify() 一次就会把焦点移到主窗口），
        在其之前调用 focus_force 会被随后的抬升冲掉，输入框将没有光标、无法输入。
        """
        w = event.widget if event is not None else None
        if w is None:
            return
        try:
            w.focus_force()          # 越过窗口管理器策略强制授予键盘焦点
        except Exception:
            try:
                w.focus_set()
            except Exception:
                pass
        try:
            idx = w.index(f'@{event.x},{event.y}')
            if idx is not None:
                w.icursor(idx)
        except Exception:
            try:
                w.icursor(tk.END)
            except Exception:
                pass

    def _close_page_browser(self, destroy: bool = False):
        """关闭列表页浏览层

        - 默认（destroy=False）：仅隐藏窗口（withdraw），保留层内全部内容、滚动位置与状态，
          下次“打开”时直接显示，不重建、不刷新（除非点击“刷新”按钮）；
        - destroy=True（软件关闭/页签销毁时调用）：真正销毁窗口并清空状态。
        无论哪种模式都会先解除与主窗口的位置锁定。
        """
        self._unbind_page_panel_from_root()
        win = self._list_win
        if win is not None:
            try:
                if destroy:
                    self._list_token += 1      # 丢弃仍在进行的抓取结果
                    win.destroy()
                else:
                    win.withdraw()             # 仅隐藏：内容/滚动位置原样保留
                    self._list_win_hidden = True
            except tk.TclError:
                pass
        if not destroy:
            return                             # 隐藏模式：保留所有状态，重开即用

        # 真正销毁：清掉层相关引用，避免 after 回调访问已销毁控件报错
        self._list_win = None
        self._list_content = None
        self._list_canvas = None
        self._list_items_frame = None
        self._list_canvas_size = None
        self._list_items_frame_size = None
        self._list_cards = []
        self._list_photo_refs = []
        self._list_url_combo = None
        self._list_tabs_canvas = None
        self._list_tabs_inner = None
        self._list_tabs_window = None
        self._list_tab_widgets = []
        self._list_rows = []
        self._list_selected_row = None
        self._list_thumb_waiters = {}
        self._list_batch_last_page = None
        self._list_thumb_canvas = None
        self._list_view_canvas = None
        self._list_busy = False
        self._list_panel_offset = 0.0
        self._list_last_geom = None
        self._list_win_hidden = False

    # ========== 类别（類別）浏览层：从右侧滑出，展示整站类别库 ==========
    def _is_genre_win_alive(self) -> bool:
        try:
            return self._genre_win is not None and bool(self._genre_win.winfo_exists())
        except Exception:
            return False

    def _genre_panel_geometry(self) -> Tuple[int, int, int, int]:
        """类别层几何：与列表页层完全一致（宽 = 主窗口的 80%，高度一致）"""
        return self._list_panel_geometry()

    def _apply_genre_panel_geometry(self, offset: Optional[float] = None):
        """定位类别层，并按滑入进度摆放层内的内容容器

        层窗口始终停在目标位置（不会跑到主窗口/屏幕外面），滑入动画改为在层内
        横向平移内容容器（超出层右边界的部分会被自动裁掉），视觉上与整体滑入一致，
        但不需要反复改动层窗口的位置，内容再多也不会在窗口外面先形成一层。

        :param offset: 内容容器的横向偏移（层宽 → 完全在右侧外；0 → 完全显示），
                       None 时沿用当前偏移
        """
        if offset is not None:
            self._genre_panel_offset = offset
        win = self._genre_win
        if win is None:
            return
        width, height, pos_x, pos_y = self._genre_panel_geometry()
        try:
            geom = f"{width}x{height}+{int(pos_x)}+{int(pos_y)}"
            # 几何没变化就不再下发（拖动主窗口/滑动期间会连续触发，重复下发会拖慢界面）
            if geom != getattr(self, '_genre_last_geom', None):
                self._genre_last_geom = geom
                win.geometry(geom)
        except tk.TclError:
            return
        self._apply_genre_panel_offset()

    def _apply_genre_panel_offset(self, offset: Optional[float] = None):
        """按滑入进度平移类别层的内容容器（层窗口本身不动，滑动更流畅）"""
        if offset is None:
            offset = getattr(self, '_genre_panel_offset', 0.0)
        else:
            self._genre_panel_offset = offset
        content = getattr(self, '_genre_content', None)
        if content is None:
            return
        try:
            content.place_configure(x=int(offset), y=0, relwidth=1.0, relheight=1.0)
        except tk.TclError:
            pass

    def _bind_genre_panel_to_root(self):
        """类别层与主窗口位置锁定（与列表页层同一套跟随逻辑）"""
        root = self.frame.winfo_toplevel()
        bindings = []
        for sequence, handler in (('<Configure>', self._on_root_configure_genre),
                                  ('<Map>', self._on_root_map_genre),
                                  ('<Unmap>', self._on_root_unmap_genre),
                                  ('<Button-1>', self._on_root_click_genre)):
            try:
                bindings.append((sequence, root.bind(sequence, handler, add='+')))
            except Exception:
                pass
        self._genre_root_binds = bindings

    def _unbind_genre_panel_from_root(self):
        try:
            root = self.frame.winfo_toplevel()
        except Exception:
            self._genre_root_binds = []
            return
        for sequence, func_id in list(getattr(self, '_genre_root_binds', []) or []):
            try:
                root.unbind(sequence, func_id)
            except Exception:
                pass
        self._genre_root_binds = []

    def _on_root_configure_genre(self, event=None):
        if not self._is_genre_win_alive() or not self._is_root_event(event):
            return
        try:
            self._apply_genre_panel_geometry()
            self._genre_win.lift()
        except Exception:
            pass

    def _on_root_unmap_genre(self, event=None):
        if not self._is_genre_win_alive() or not self._is_root_event(event):
            return
        try:
            self._genre_win.withdraw()
        except tk.TclError:
            pass

    def _on_root_map_genre(self, event=None):
        if not self._is_genre_win_alive() or not self._is_root_event(event):
            return
        try:
            self._apply_genre_panel_geometry()
            self._genre_win.deiconify()
            self._genre_win.lift()
        except tk.TclError:
            pass

    def _on_root_click_genre(self, event=None):
        """点击主窗口左侧空位（类别层未遮住的部分）自动关闭类别层"""
        if not self._is_genre_win_alive() or event is None:
            return
        try:
            if event.widget is not None and \
                    event.widget.winfo_toplevel() is not self.frame.winfo_toplevel():
                return
            panel_x = self._genre_win.winfo_rootx()
            if event.x_root < panel_x:
                self._close_genre_browser()
        except Exception:
            pass

    def _draw_genre_panel_icon(self, hover: bool = False):
        """绘制主窗口的“类别”图标按钮（与“页面”按钮同风格：标签造型）"""
        canvas = getattr(self, 'genre_panel_canvas', None)
        if canvas is None:
            return
        try:
            canvas.delete('all')
        except tk.TclError:
            return

        size = THUMB_TOGGLE_BTN_SIZE
        color = PAGE_BTN_COLOR_HOVER if hover else PAGE_BTN_COLOR
        canvas.create_rectangle(1, 1, size - 1, size - 1,
                                fill=PAGE_BTN_BG_HOVER if hover else PAGE_BTN_BG,
                                outline=PAGE_BTN_BORDER)
        # 标签（tag）造型：圆角方牌 + 左侧小孔
        x0, y0, x1, y1 = 6, 7, size - 6, size - 7
        canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=1)
        hole_r = 2
        hx, hy = x0 + 5, (y0 + y1) / 2
        canvas.create_oval(hx - hole_r, hy - hole_r, hx + hole_r, hy + hole_r,
                           outline=color)
        # 两条横线示意类别条目
        canvas.create_line(hx + 6, y0 + 6, x1 - 4, y0 + 6, fill=color)
        canvas.create_line(hx + 6, y1 - 6, x1 - 4, y1 - 6, fill=color)

    def _genre_library_path(self) -> str:
        return GENRE_CACHE_FILE

    def _genre_library_load(self):
        """从本地 JSON 加载类别库（损坏/不存在时保持为空，提示刷新）"""
        if self._genre_library_loaded:
            return
        self._genre_library_loaded = True
        try:
            with open(self._genre_library_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            groups = data.get('groups') if isinstance(data, dict) else None
            if isinstance(groups, list) and groups:
                self._genre_layer_groups = groups
                self._genre_library_fetched_at = str(data.get('fetched_at') or '')
        except Exception:
            pass

    def _genre_library_save(self, groups: List[Dict[str, Any]], base_url: str):
        """把类别库写入本地 JSON（手动刷新成功后调用）"""
        data = {
            'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'base_url': base_url,
            'groups': groups,
        }
        try:
            os.makedirs(os.path.dirname(self._genre_library_path()), exist_ok=True)
            with open(self._genre_library_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"类别库已保存: {self._genre_library_path()}")
        except Exception as e:
            self._log(f"类别库保存失败: {e}")

    def _parse_genre_page(self, html: str, base_url: str) -> List[Dict[str, Any]]:
        """解析 {入口}/genre 页面：按页面自身的 <h4> 大类分组，提取每个小类的名称/网址/代码

        :return: [{'name': 大类名, 'items': [{'name','url','code'}, ...]}, ...]
        """
        if not html:
            return []
        worker = WebGrabWorker(WebGrabConfig())
        soup = worker._build_soup(html)
        if soup is None:
            return []

        def _is_genre_box(tag) -> bool:
            cls = tag.get('class') or []
            if isinstance(cls, str):
                cls = cls.split()
            return any('genre-box' in c for c in cls)

        groups: List[Dict[str, Any]] = []
        for head in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            name = head.get_text(strip=True)
            if not name:
                continue
            # 找标题后面最近的 genre-box 容器
            block = None
            node = head
            for _ in range(4):
                node = node.find_next_sibling()
                if node is None:
                    break
                if _is_genre_box(node):
                    block = node
                    break
            if block is None:
                continue
            items: List[Dict[str, str]] = []
            seen = set()
            for a in block.find_all('a', href=True):
                item_name = str(a.get_text(strip=True) or '').strip()
                href = str(a.get('href') or '').strip()
                if not item_name or not href or item_name in seen:
                    continue
                seen.add(item_name)
                url = urllib.parse.urljoin(base_url or '', href)
                code = urllib.parse.urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]
                items.append({'name': item_name, 'url': url, 'code': code})
            if items:
                groups.append({'name': name, 'items': items})
        return groups

    def _open_genre_browser(self):
        """打开“类别”浏览层：内容从右侧滑出到位，尺寸与列表页层一致

        层窗口一创建就定位在最终位置（始终在主窗口之内），先只搭好轻量的层框架
        （标题条/工具条/空列表），让滑动过程没有重活；等滑动结束后再读取类别库
        并把大量标签渲染进去，避免“内容太多拖慢滑动”。
        """
        if self._is_genre_win_alive():
            self._genre_win.lift()
            self._genre_win.focus_force()
            return

        root = self.frame.winfo_toplevel()
        try:
            root.update_idletasks()
        except Exception:
            pass

        panel_w, panel_h, end_x, end_y = self._genre_panel_geometry()

        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.configure(background=LIST_PANEL_BG)
        # 不使用全局置顶(-topmost)：transient 让层只保持在主窗口上方
        win.transient(root)
        win.geometry(f"{panel_w}x{panel_h}+{end_x}+{end_y}")
        self._genre_win = win

        # 滑动内容容器：先整体放到层右侧之外（不可见）
        content = tk.Frame(win, background=LIST_PANEL_BG)
        self._genre_content = content
        self._apply_genre_panel_geometry(float(panel_w))

        # 只构建层框架：滑动期间层内控件很少，滑动不会被大量标签拖住
        self._build_genre_browser(content)
        self._bind_genre_panel_to_root()
        try:
            win.deiconify()
            win.update_idletasks()
            win.lift(root)
        except Exception:
            pass
        win.focus_force()
        # 滑动结束后再读取类别库并渲染（由 _slide_in_genre_panel 末尾调用）
        self._slide_in_genre_panel()

    def _slide_in_genre_panel(self):
        """类别层滑入动画：只平移层内的内容容器（层窗口位置不变）

        滑动期间层内只有标题条等少量控件；滑动结束后再调用
        _genre_load_and_render 读取类别库并渲染大量标签。
        """
        total = max(1, LIST_PANEL_ANIM_STEPS)
        width, _, _, _ = self._genre_panel_geometry()
        state = {'i': 0}

        def _finish():
            self._apply_genre_panel_offset(0.0)
            try:
                self._genre_win.lift()
            except Exception:
                pass
            self._genre_load_and_render()

        if getattr(self, '_genre_content', None) is None:
            _finish()
            return

        def _step():
            if not self._is_genre_win_alive():
                return
            state['i'] += 1
            if state['i'] >= total:
                _finish()
                return
            self._apply_genre_panel_offset(width * (1 - state['i'] / total))
            self._genre_win.after(LIST_PANEL_ANIM_MS, _step)

        _step()

    def _genre_load_and_render(self):
        """滑入结束后才读取类别库并渲染（避免厚重内容拖慢滑动）"""
        if not self._is_genre_win_alive():
            return
        try:
            if self._genre_layer_groups:
                self._genre_layer_status.set("正在载入类别库…")
        except Exception:
            pass
        self._genre_library_load()
        self._render_genre_library()

    def _build_genre_browser(self, host):
        """构建类别层界面：标题条 + 工具条（刷新/多选/确定/清空）+ 分组类别列表

        :param host: 内容容器（滑动时整体平移的那个 Frame），所有界面元素都挂在它下面
        """
        win = self._genre_win          # 层窗口本身：只用于挂窗口级绑定
        # 左侧颜色边条
        tk.Frame(host, width=6, background=LIST_PANEL_EDGE_BG).pack(
            side=tk.LEFT, fill=tk.Y)

        # 标题条
        title_bar = tk.Frame(host, background=LIST_PANEL_BAR_BG, height=42)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="視頻類別", background=LIST_PANEL_BAR_BG,
                 foreground='#222222', font=('Microsoft YaHei', 11, 'bold')).pack(
            side=tk.LEFT, padx=14)
        self._genre_layer_status = tk.StringVar(value="")
        tk.Label(title_bar, textvariable=self._genre_layer_status,
                 background=LIST_PANEL_BAR_BG, foreground=LIST_PANEL_FG_DIM,
                 font=('Microsoft YaHei', 9)).pack(side=tk.LEFT, padx=6)
        ttk.Button(title_bar, text="关闭", width=8,
                   command=self._close_genre_browser).pack(side=tk.RIGHT, padx=10, pady=7)

        # 工具条
        toolbar = ttk.Frame(host, padding=(10, 6, 10, 4))
        toolbar.pack(fill=tk.X)
        # 单选/多选状态也跟着保留：重开层后与上次的选择方式一致
        self._genre_layer_multi_var = tk.BooleanVar(
            value=bool(getattr(self, '_genre_multi_select', False)))
        ttk.Checkbutton(toolbar, text="多选（可同时满足多个类别）",
                        variable=self._genre_layer_multi_var,
                        command=self._genre_layer_on_multi_toggle).pack(side=tk.LEFT)
        self._genre_layer_confirm_btn = ttk.Button(
            toolbar, text="确定", width=8, state='disabled',
            command=self._genre_layer_confirm)
        self._genre_layer_confirm_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self._genre_layer_clear_btn = ttk.Button(
            toolbar, text="清空", width=6, state='disabled',
            command=self._genre_layer_clear_selection)
        self._genre_layer_clear_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self._genre_layer_refresh_btn = ttk.Button(
            toolbar, text="刷新", width=6, command=self._genre_layer_refresh)
        self._genre_layer_refresh_btn.pack(side=tk.RIGHT, padx=(2, 0))

        # 类别列表主体（可滚动）
        body = tk.Frame(host, background=LIST_PANEL_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        canvas = tk.Canvas(body, background=LIST_PANEL_AREA_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._genre_layer_canvas = canvas
        frame = tk.Frame(canvas, background=LIST_PANEL_AREA_BG)
        self._genre_layer_frame = frame
        canvas.create_window((0, 0), window=frame, anchor=tk.NW)
        # 滑动层/拖动主窗口时层内每个控件都会收到“位置变化”的 Configure，
        # 下面两个回调只在尺寸真的变了时才工作，否则每一帧都要重排/重算一次
        frame.bind('<Configure>', self._on_genre_layer_frame_configure)
        canvas.bind('<Configure>', self._on_genre_layer_canvas_configure)
        # 鼠标滚轮：绑到层窗口本身。Tk 的事件会沿 bindtags 传到所属 Toplevel，
        # 因此鼠标停在标题、按钮、类别标签等任意子控件上时都能滚动画布；
        # （只绑 canvas/frame 时，鼠标停在众多类别标签上就滚不动了）
        self._bind_mousewheel(win, canvas)
        # 部分系统/触控板只发 Button-4/5（滚轮上/下）
        win.bind('<Button-4>', lambda e: self._scroll_genre_layer(-1), add='+')
        win.bind('<Button-5>', lambda e: self._scroll_genre_layer(1), add='+')

        # 点击层内任意位置：把主窗口与层作为一个整体提到其它应用之上
        win.bind('<Button-1>', self._activate_app_group, add='+')

        # 类别库不再在这里读取渲染：等滑入动画结束后由 _genre_load_and_render 执行

    def _on_genre_layer_canvas_configure(self, event=None):
        """画布宽度变化时才同步内层宽度（仅位置变化时直接忽略）"""
        canvas = getattr(self, '_genre_layer_canvas', None)
        if canvas is None:
            return
        try:
            width = event.width if event is not None else canvas.winfo_width()
        except (tk.TclError, AttributeError):
            return
        if width == getattr(self, '_genre_canvas_width', None):
            return
        self._genre_canvas_width = width
        try:
            canvas.itemconfigure('all', width=width)
        except tk.TclError:
            pass

    def _on_genre_layer_frame_configure(self, event=None):
        """内容区尺寸变化时才刷新滚动范围（仅位置变化时直接忽略）"""
        frame = getattr(self, '_genre_layer_frame', None)
        canvas = getattr(self, '_genre_layer_canvas', None)
        if frame is None or canvas is None:
            return
        try:
            size = (event.width, event.height) if event is not None else (
                frame.winfo_width(), frame.winfo_height())
        except (tk.TclError, AttributeError):
            return
        if size == getattr(self, '_genre_frame_size', None):
            return
        self._genre_frame_size = size
        try:
            canvas.configure(scrollregion=canvas.bbox('all'))
        except tk.TclError:
            pass

    def _scroll_genre_layer(self, steps: int):
        """类别层列表滚动若干单位（供鼠标滚轮 / Button-4/5 使用）"""
        canvas = getattr(self, '_genre_layer_canvas', None)
        if canvas is None:
            return
        try:
            canvas.yview_scroll(int(steps), 'units')
        except Exception:
            pass
        return 'break'

    def _render_genre_library(self):
        """把类别库渲染为“大类标题 + 小类标签流”"""
        frame = self._genre_layer_frame
        if frame is None:
            return
        try:
            for child in frame.winfo_children():
                child.destroy()
        except tk.TclError:
            return
        self._genre_layer_pills = []
        self._genre_layer_containers = []
        self._genre_group_widths = {}
        # 保留上次已选中的类别（关闭层再打开仍可继续选择/反选）；
        # 只丢弃与当前类别库对不上的旧索引
        self._genre_layer_selection = self._genre_valid_selection()
        self._update_genre_layer_controls()

        groups = self._genre_layer_groups or []
        if not groups:
            tk.Label(frame, text="尚未抓取类别库，请点击右上角“刷新”从 {入口}/genre 获取。",
                     background=LIST_PANEL_AREA_BG, foreground='#8a8a8a',
                     font=('Microsoft YaHei', 10)).pack(anchor='w', padx=16, pady=16)
            return

        total = sum(len(g.get('items') or []) for g in groups)
        fetched = str(getattr(self, '_genre_library_fetched_at', '') or '')
        self._genre_layer_status.set(
            f"共 {len(groups)} 大类 / {total} 小类" + (f"（更新于 {fetched}）" if fetched else ""))

        for g_idx, group in enumerate(groups):
            head = tk.Label(frame, text=str(group.get('name') or ''), anchor='w',
                            background='#e8e8e8', foreground='#333333',
                            font=('Microsoft YaHei', 10, 'bold'), padx=10, pady=4)
            head.pack(fill=tk.X, pady=(8, 0))
            items = group.get('items') or []
            if not items:
                continue
            gframe = tk.Frame(frame, background=LIST_PANEL_AREA_BG)
            gframe.pack(fill=tk.X, padx=8, pady=(4, 0))
            pills = []
            for i_idx, item in enumerate(items):
                pill = tk.Label(gframe, text=str(item.get('name') or ''),
                                background=LIST_PANEL_AREA_BG, foreground="#0066cc",
                                font=('Microsoft YaHei', 9), cursor='hand2',
                                padx=4, pady=1)
                pill.bind('<Button-1>',
                          lambda e, gi=g_idx, ii=i_idx: self._genre_layer_on_click(gi, ii))
                pill.bind('<Button-3>',
                          lambda e, it=item: self._copy_text(
                              str(it.get('name') or ''), "类别"))
                # 先不 grid：等容器宽度确定后由 _grid_flow_pills 一次性分行布局，
                # 避免“先排成一行、再全部 forget 重排”的两遍布局（类别多时非常慢）
                pills.append(pill)
                self._genre_layer_pills.append((pill, g_idx, i_idx))
            self._genre_layer_containers.append((gframe, pills))
            gframe.bind('<Configure>', self._on_genre_layer_group_resize)

        self._reflow_genre_layer_groups()
        # 标签建好后按保留下来的选择重新高亮一次（上面那次调用时标签还没建出来）
        self._update_genre_layer_controls()
        # 首次渲染时层可能刚建立、容器宽度还是 1，等本次空闲（布局完成）后再重排一次，
        # 保证按真实宽度分行
        after_host = self._genre_win or self.frame
        if after_host is not None:
            try:
                after_host.after_idle(self._reflow_genre_layer_groups)
            except Exception:
                pass

    def _genre_valid_selection(self) -> Set[Tuple[int, int]]:
        """过滤掉与当前类别库对不上的旧选择（关闭层再打开时保留仍在的选中项）"""
        groups = getattr(self, '_genre_layer_groups', None) or []
        valid: Set[Tuple[int, int]] = set()
        for key in (getattr(self, '_genre_layer_selection', None) or set()):
            try:
                g_idx, i_idx = key
                if 0 <= g_idx < len(groups) and \
                        0 <= i_idx < len(groups[g_idx].get('items') or []):
                    valid.add((g_idx, i_idx))
            except Exception:
                continue
        return valid

    def _on_genre_layer_group_resize(self, event=None):
        """某个分组的容器宽度变化时，重排该组的标签

        注意：滑动层/拖动主窗口时，组内每个标签都会收到“位置变化”的 Configure，
        若不去重，每一帧都要把整组标签重新 grid 一遍（类别多时会明显卡顿）。
        因此这里只在容器宽度真的变了时才重排。
        """
        container = event.widget if event is not None else None
        if container is None:
            return
        for gframe, pills in getattr(self, '_genre_layer_containers', []):
            if gframe is container:
                try:
                    width = container.winfo_width()
                except tk.TclError:
                    return
                widths = getattr(self, '_genre_group_widths', None)
                if widths is None:
                    widths = self._genre_group_widths = {}
                if widths.get(container) == width:
                    return
                widths[container] = width
                self._grid_flow_pills(gframe, pills, width)
                return

    def _reflow_genre_layer_groups(self):
        """重排所有分组的标签流"""
        widths = getattr(self, '_genre_group_widths', None)
        if widths is None:
            widths = self._genre_group_widths = {}
        for gframe, pills in getattr(self, '_genre_layer_containers', []):
            try:
                width = gframe.winfo_width()
                widths[gframe] = width
                self._grid_flow_pills(gframe, pills, width)
            except Exception:
                pass

    @staticmethod
    def _grid_flow_pills(container, pills: List[Any], width: int) -> int:
        """按容器宽度把标签贪心分行并 grid 布局，返回行数（宽度不足时不动作）"""
        if container is None or not pills or width < 40:
            return 0
        widths = []
        for pill in pills:
            try:
                widths.append(pill.winfo_reqwidth() + 4)
            except tk.TclError:
                widths.append(0)
        rows: List[List[int]] = []
        cur: List[int] = []
        used = 0
        for index, w in enumerate(widths):
            if cur and used + w > width:
                rows.append(cur)
                cur, used = [], 0
            cur.append(index)
            used += w
        if cur:
            rows.append(cur)
        try:
            container.grid_anchor('nw')
        except Exception:
            pass
        for pill in pills:
            try:
                pill.grid_forget()
            except tk.TclError:
                pass
        for r, indexes in enumerate(rows):
            for c, i in enumerate(indexes):
                try:
                    pills[i].grid(row=r, column=c, sticky='w', padx=(0, 4), pady=1)
                except tk.TclError:
                    pass
        return len(rows)

    def _genre_layer_on_click(self, group_idx: int, item_idx: int):
        """点击类别小类：单选立即打开；多选仅切换选中（确定后统一打开）"""
        try:
            multi = bool(self._genre_layer_multi_var.get())
        except Exception:
            multi = False
        key = (group_idx, item_idx)
        if not multi:
            self._genre_layer_selection = {key} if key not in (
                self._genre_layer_selection or set()) else set()
            self._update_genre_layer_controls()
            if not self._genre_layer_selection:
                return
            self._genre_layer_open_selection()
            return

        self._genre_layer_selection = set(self._genre_layer_selection or set())
        if key in self._genre_layer_selection:
            self._genre_layer_selection.discard(key)
        else:
            self._genre_layer_selection.add(key)
        self._update_genre_layer_controls()

    def _update_genre_layer_controls(self):
        """刷新类别层的标签高亮与按钮可用性"""
        selected = set(getattr(self, '_genre_layer_selection', None) or set())
        for pill, g_idx, i_idx in getattr(self, '_genre_layer_pills', []):
            try:
                pill.configure(
                    background='#cfe4ff' if (g_idx, i_idx) in selected
                    else LIST_PANEL_AREA_BG)
            except tk.TclError:
                pass
        try:
            multi = bool(self._genre_layer_multi_var.get())
        except Exception:
            multi = False
        has = bool(selected)
        for attr, enabled in (('_genre_layer_clear_btn', has),
                              ('_genre_layer_confirm_btn', multi and has)):
            btn = getattr(self, attr, None)
            if btn is not None:
                try:
                    btn.configure(state=('normal' if enabled else 'disabled'))
                except tk.TclError:
                    pass

    def _genre_layer_clear_selection(self):
        """清空当前选择（仅在用户点击“清空”或刷新类别库时发生）"""
        self._genre_layer_selection = set()
        self._update_genre_layer_controls()
        self._log("已清空类别选择")

    def _genre_layer_on_multi_toggle(self):
        try:
            multi = bool(self._genre_layer_multi_var.get())
        except Exception:
            multi = False
        self._genre_multi_select = multi      # 关闭层再打开时沿用同一选择方式
        if not multi:
            self._genre_layer_selection = set()
            self._update_genre_layer_controls()
        self._log("类别层已切换为多选" if multi else "类别层已切换为单选")

    def _genre_layer_open_selection(self):
        """打开当前选中的类别：按分组各自组合网址（有碼/無碼前缀不同，需分开打开）"""
        selected = set(getattr(self, '_genre_layer_selection', None) or set())
        groups = self._genre_layer_groups or []
        by_prefix: Dict[str, List[Dict[str, str]]] = {}
        for g_idx, i_idx in sorted(selected):
            try:
                item = groups[g_idx].get('items')[i_idx]
            except Exception:
                continue
            url = str(item.get('url') or '').strip()
            if not url:
                continue
            parts = urllib.parse.urlsplit(url)
            prefix = parts.path.rstrip('/').rsplit('/', 1)[0]
            by_prefix.setdefault(f"{parts.scheme}://{parts.netloc}{prefix}",
                                 []).append(item)

        if not by_prefix:
            self._log("所选类别没有可用的网址")
            return
        for base, items in by_prefix.items():
            url = self._genre_combined_url(items)
            if not url:
                continue
            codes = '-'.join(str(it.get('code') or '') for it in items)
            names = '-'.join(str(it.get('name') or '') for it in items)
            tab_name = f"類別-{names}"[:40]
            self._log(f"[類別] {names} → 在视频列表页新建页签打开: {url}")
            self._list_tab_open_url(url, name=tab_name)

    def _genre_layer_confirm(self):
        selected = set(getattr(self, '_genre_layer_selection', None) or set())
        if not selected:
            self._log("尚未选择任何类别")
            return
        self._genre_layer_open_selection()

    def _genre_layer_refresh(self):
        """手动刷新类别库：抓取 {入口}/genre 并保存到本地 JSON（入口失败自动合并切换）"""
        if getattr(self, '_genre_layer_busy', False):
            return
        self._genre_layer_busy = True
        try:
            self._genre_layer_refresh_btn.configure(state='disabled')
        except tk.TclError:
            pass
        self._genre_layer_status.set("正在抓取类别库…")

        def work():
            groups: List[Dict[str, Any]] = []
            used_base = ''
            try:
                temp = WebGrabWorker(WebGrabConfig(), log_callback=self._log)
                current = self.list_url_entry_var.get().strip()
                last_error = ''
                for attempt in range(LIST_HOST_MAX_TRIES):
                    host = self._list_pick_host(current) or self._list_switch_host()
                    if not host:
                        break
                    url = host.rstrip('/') + '/genre'
                    self._log(f"[類別庫] 抓取 {url}（第 {attempt + 1} 次尝试）")
                    html = temp.fetch_page(url)
                    if html:
                        groups = self._parse_genre_page(html, host.rstrip('/'))
                    if groups:
                        used_base = host.rstrip('/')
                        break
                    last_error = '未解析到类别分组'
                    self._log(f"[類別庫] {url} 抓取/解析失败（{last_error}），尝试其它入口")
                if not groups:
                    self._run_in_main_thread(
                        lambda err=last_error: self._genre_refresh_done([], '', err))
                    return
            except Exception as e:
                self._run_in_main_thread(lambda err=str(e): self._genre_refresh_done([], '', err))
                return
            self._run_in_main_thread(
                lambda g=groups, b=used_base: self._genre_refresh_done(g, b, ''))

        threading.Thread(target=work, daemon=True).start()

    def _genre_refresh_done(self, groups: List[Dict[str, Any]], base_url: str,
                            error: str = ''):
        """类别库刷新完成（主线程）"""
        self._genre_layer_busy = False
        try:
            self._genre_layer_refresh_btn.configure(state='normal')
        except tk.TclError:
            pass
        if error or not groups:
            self._genre_layer_status.set(f"刷新失败：{error or '未解析到类别'}")
            self._log(f"[類別庫] 刷新失败：{error or '未解析到类别'}")
            return
        self._genre_layer_groups = groups
        self._genre_library_fetched_at = time.strftime('%Y-%m-%d %H:%M:%S')
        self._genre_library_save(groups, base_url)
        # 刷新后大类/小类的序号可能整体变化，旧的下标已对不上，清空重新选
        self._genre_layer_selection = set()
        self._render_genre_library()
        self._log(f"[類別庫] 刷新完成：{len(groups)} 个大类")

    def _close_genre_browser(self):
        """关闭类别层（同时解除与主窗口的位置锁定）

        已选中的类别不清空：下次打开时仍是选中状态，可继续选择/反选，
        或再打开新的网址浏览。
        """
        self._unbind_genre_panel_from_root()
        try:
            if self._genre_win is not None:
                self._genre_win.destroy()
        except Exception:
            pass
        self._genre_win = None
        self._genre_content = None
        self._genre_layer_canvas = None
        self._genre_layer_frame = None
        self._genre_layer_pills = []
        self._genre_layer_containers = []
        self._genre_group_widths = {}
        self._genre_canvas_width = None
        self._genre_frame_size = None
        self._genre_last_geom = None
        self._genre_panel_offset = 0.0

    # ---------- 列表页抓取 ----------
    def _list_cache_key(self, url: str) -> str:
        """缓存文件名：对网址取 md5（避免网址中的特殊字符进入文件名）"""
        return hashlib.md5(url.encode('utf-8')).hexdigest()

    def _list_cache_page(self, url: str, items: List[Dict], next_url: Optional[str],
                         pagination: Optional[Dict] = None):
        """把列表页解析结果写入磁盘缓存（关闭层再打开时可直接显示）"""
        try:
            os.makedirs(LIST_CACHE_DIR, exist_ok=True)
            data = {'url': url, 'time': time.time(),
                    'items': items, 'next_url': next_url,
                    'pagination': pagination or {}}
            path = os.path.join(LIST_CACHE_DIR, self._list_cache_key(url) + '.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            self._list_trim_cache()
        except Exception:
            pass

    def _list_load_cached_page(self, url: str) -> Optional[Dict]:
        """读取某网址的磁盘缓存页面数据（无缓存或为空时返回 None）"""
        try:
            path = os.path.join(LIST_CACHE_DIR, self._list_cache_key(url) + '.json')
            if not os.path.isfile(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not (data.get('items') or []):
                return None
            return data
        except Exception:
            return None

    def _list_show_cached(self, url: str, cached: Dict):
        """直接渲染磁盘缓存中的列表页数据（不重新联网抓取）"""
        self._list_token += 1
        self._list_busy = False
        self.list_url_entry_var.set(url)
        page_no = self._list_page_from_url(url)
        self.list_page_var.set(str(page_no) if page_no else '')
        self._sync_active_tab(url, page_no)
        self._list_next_url = cached.get('next_url')
        self._list_items = list(cached.get('items') or [])
        self._list_pagination = cached.get('pagination') or {}
        self._list_page_label_var.set(
            self._format_page_info(self._list_pagination, page_no))
        self._list_status_var.set(f"已从缓存载入（{len(self._list_items)} 个视频，"
                                  f"点“刷新”重新联网抓取）　{url}")
        self._render_list_items()

    def _list_cache_thumb_path(self, url: str) -> str:
        """缩略图的磁盘缓存文件路径（保留原扩展名，识别不出时用 .jpg）"""
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            ext = '.jpg'
        return os.path.join(LIST_CACHE_DIR, 't_' + self._list_cache_key(url) + ext)

    def _list_trim_cache(self):
        """缓存文件夹容量超过上限时，自动删除最旧的缓存文件

        上限取自 self._list_cache_max_bytes（由“本地设置 → 列表缓存上限(MB)”动态控制，
        默认 50MB）；修改后立即生效，下次写入缓存或打开列表层时按新值清理超容文件。
        """
        try:
            max_bytes = max(1 * 1024 * 1024, int(getattr(self, '_list_cache_max_bytes', 0)
                                                or LIST_CACHE_MAX_BYTES))
            files = []
            total = 0
            for fp in glob.glob(os.path.join(LIST_CACHE_DIR, '*')):
                try:
                    st = os.stat(fp)
                    if os.path.isfile(fp):
                        files.append((st.st_mtime, st.st_size, fp))
                        total += st.st_size
                except OSError:
                    pass
            if total <= max_bytes:
                return
            files.sort()       # 按修改时间从旧到新依次清理
            for _, size, fp in files:
                if total <= max_bytes:
                    break
                try:
                    os.remove(fp)
                    total -= size
                except OSError:
                    pass
            self._log(f"列表缓存已按上限 {max_bytes // (1024 * 1024)}MB 清理，当前约 {total // (1024 * 1024)}MB")
        except Exception:
            pass

    # ----- 详情页图片（主图/样例图）磁盘缓存：与列表页缩略图共用 LIST_CACHE_DIR -----
    def _detail_image_cache_path(self, url: str) -> str:
        """详情页图片的磁盘缓存路径（保留原扩展名；识别不出用 .jpg）"""
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            ext = '.jpg'
        return os.path.join(LIST_CACHE_DIR, 'd_' + self._list_cache_key(url) + ext)

    def _load_image_from_disk_cache(self, url: str) -> Optional[bytes]:
        """从磁盘缓存读取图片字节（无缓存或异常时返回 None）"""
        if not url:
            return None
        path = self._detail_image_cache_path(url)
        if os.path.isfile(path):
            try:
                with open(path, 'rb') as f:
                    return f.read()
            except Exception:
                pass
        return None

    def _save_image_to_disk_cache(self, url: str, data: bytes):
        """把图片字节写入磁盘缓存（仅首次写入；写后按上限清理超容文件）"""
        if not url or not data:
            return
        try:
            os.makedirs(LIST_CACHE_DIR, exist_ok=True)
            path = self._detail_image_cache_path(url)
            if not os.path.isfile(path):
                with open(path, 'wb') as f:
                    f.write(data)
                self._list_trim_cache()
        except Exception:
            pass

    def _fetch_image_cached(self, url: str) -> Optional[bytes]:
        """取图片字节：内存缓存 → 磁盘缓存 → 联网下载并落盘（三者按顺序尝试）

        返回的数据由调用方在“主线程”写入内存缓存（self._cache_put），
        本方法只在后台线程做“读内存/读磁盘/下载+落盘”，避免多线程写同一个字典。
        联网下载成功后立即写入磁盘缓存，使软件关闭再打开、或重复切换页签时
        无需重新联网，可直接从磁盘缓存读取详情页图片。
        """
        if not url:
            return None
        data = self._image_cache.get(url)           # 1) 内存缓存（最快）
        if data:
            return data
        data = self._load_image_from_disk_cache(url)  # 2) 磁盘缓存（跨会话/跨页签复用）
        if data:
            return data
        data = self.worker.download_image(url) if self.worker else None  # 3) 联网
        if data:
            self._save_image_to_disk_cache(url, data)
        return data

    def _list_open_url(self, url: Optional[str] = None, page: Optional[int] = None,
                       use_cache: bool = True):
        """打开（抓取）指定列表页地址

        :param use_cache: True 时优先使用磁盘缓存（不联网）；“刷新”按钮传 False 强制重新抓取
        """
        if not self._is_list_win_alive() and url is None:
            self._open_page_browser()
            return

        target = (url if url is not None else self.list_url_entry_var.get()).strip()
        if page is not None and target:
            resolved = self._list_url_for_page(target, page)
            if resolved:
                target = resolved
        target = self._normalize_url(target)
        if not target:
            messagebox.showwarning("警告", "请输入列表页网址")
            return

        # 通配符批量抓取：网址含 {#} 时按主窗口“批量 {#} 起止”展开成多个地址，
        # 按顺序逐个抓取（“位数”大于 0 时数字前面补 0，如位数 3、数字 7 → 007）
        wildcard_urls = self._list_expand_wildcard(target)
        if wildcard_urls is not None:
            if wildcard_urls:
                self._save_list_config()
                self._start_list_batch(target, wildcard_urls, use_cache=use_cache)
            return

        # 多页连续抓取：“显示页数”大于 1 时从当前页起自动翻页连续抓取
        multi_urls = self._list_multipage_urls(target)
        if multi_urls:
            self._save_list_config()
            self._start_list_batch(target, multi_urls, use_cache=use_cache)
            return

        # 磁盘缓存命中：直接显示，无需重新联网抓取
        if use_cache:
            cached = self._list_load_cached_page(target)
            if cached is not None:
                self._list_show_cached(target, cached)
                return

        if self._list_busy:
            self._log("列表页正在抓取中，请稍候")
            return

        self.list_url_entry_var.set(target)
        self._list_busy = True
        self._list_token += 1
        token = self._list_token
        self._list_status_var.set(f"正在抓取列表页: {target}")

        # 网址暂存：把“网址 + 页码”配套写入当前页签，下次打开可继续浏览同一页
        page_no = self._list_page_from_url(target)
        self.list_page_var.set(str(page_no) if page_no else '')
        self._sync_active_tab(target, page_no)

        # 同时写入旧的单值键（兼容其它逻辑读取）
        self.config_manager.set(self.CONFIG_SECTION, 'list_last_url', target)
        if page_no:
            self.config_manager.set(self.CONFIG_SECTION, 'list_last_page', page_no)

        worker = self._build_list_worker()
        self._list_worker = worker
        self._log(f"[列表页] 开始抓取: {target}")
        threading.Thread(target=self._list_fetch_thread,
                         args=(worker, target, token), daemon=True).start()

    def _list_expand_wildcard(self, pattern: str) -> Optional[List[str]]:
        """把列表页网址中的 {#} 按主窗口“批量 {#} 起止”展开为多个地址

        返回 None 表示网址不含 {#}；返回 [] 表示参数不合法（已提示用户）。
        “位数”大于 0 时数字前面补 0（如位数 3，数字 7 → 007）。
        """
        if '{#}' not in pattern:
            return None
        start_text = (self.range_start_var.get().strip()
                      if hasattr(self, 'range_start_var') else '')
        end_text = (self.range_end_var.get().strip()
                    if hasattr(self, 'range_end_var') else '')
        if not start_text or not end_text:
            messagebox.showwarning("警告", "网址中包含 {#}，请先在主窗口填写“批量 {#} 起止”数字")
            return []
        try:
            start, end = int(start_text), int(end_text)
        except ValueError:
            messagebox.showwarning("警告", "起始数与结束数必须是整数")
            return []
        if end < start:
            start, end = end, start          # 用户写反了也照常工作
        pad = 0
        pad_text = (self.list_pad_var.get() or '').strip()
        if pad_text:
            try:
                pad = max(0, int(pad_text))
            except ValueError:
                pad = 0
        count = end - start + 1
        if count > MAX_BATCH_PAGES and not messagebox.askyesno(
                "确认", f"将按顺序抓取 {count} 个网址，数量较多耗时较长。\n是否继续？"):
            return []
        urls = []
        for n in range(start, end + 1):
            num = str(n).zfill(pad) if pad > 0 else str(n)
            urls.append(self._normalize_url(pattern.replace('{#}', num)))
        self._log(f"[列表页] {{#}} 展开：{pattern} → {count} 个地址"
                  f"（{urls[0]} ~ {urls[-1]}，补零位数 {pad}）")
        return urls

    def _list_display_pages(self) -> int:
        """读取“显示页数”输入框（非法输入按 1 页处理，并限制上限）"""
        try:
            pages = int((self.list_pages_var.get() or '1').strip())
        except ValueError:
            pages = 1
        return max(1, min(pages, MAX_BATCH_PAGES))

    def _list_multipage_urls(self, target: str) -> Optional[List[str]]:
        """按“显示页数”从当前网址起生成连续多页地址

        返回 None 表示只抓当前页（显示页数为 1，或无法推算翻页地址）。
        页码地址推算复用 _list_url_for_page（支持 /page/{n}、子路径 /xx/{n}、?page={n}）。
        """
        pages = self._list_display_pages()
        if pages <= 1:
            return None
        pages = min(pages, MAX_BATCH_PAGES)
        start_page = self._list_page_from_url(target) or 1
        urls = []
        for page in range(start_page, start_page + pages):
            u = self._list_url_for_page(target, page)
            if not u:
                break
            urls.append(u)
        if len(urls) <= 1:
            self._log("[列表页] 无法推算翻页地址，仅抓取当前页"
                      "（可在“爬虫设置 → 视频列表页”里配置页码模板）")
            return None
        self._log(f"[列表页] 多页抓取：从第 {start_page} 页起连续抓取 {len(urls)} 页")
        return urls

    def _start_list_batch(self, base_url: str, urls: List[str], use_cache: bool = True):
        """批量抓取（{#} 展开 / 多页连续）：按顺序抓取，每页抓完立即累积显示

        :param use_cache: True 时优先用磁盘缓存（每个地址单独缓存；“确认为空”的地址
            也不再重复联网），只有缓存里没有的地址才联网抓取；False（“刷新”）时全部重抓
        """
        if self._list_busy:
            self._log("列表页正在抓取中，请稍候")
            return
        self.list_url_entry_var.set(base_url)
        self._list_busy = True
        self._list_token += 1
        token = self._list_token
        self._list_items = []
        self._list_next_url = None
        self._list_pagination = {}
        self._list_batch_last_page = None
        self._list_page_label_var.set("页码: -")
        # 网址暂存：把入口地址写入当前页签，下次打开可继续
        page_no = self._list_page_from_url(base_url)
        self.list_page_var.set(str(page_no) if page_no else '')
        self._sync_active_tab(base_url, page_no)

        # 先取缓存：已缓存的页直接显示，只补抓缓存里没有的页
        cached_items: List[Dict] = []
        pending = list(urls)
        # 只有分页列表才记录“最后页码”（{#} 占位网址里的数字不是页码，不参与翻页续接）
        self._list_batch_pages_mode = page_no is not None
        if use_cache:
            cached_items, pending, cached_next = self._list_batch_from_cache(urls)
            self._list_next_url = cached_next
            if self._list_batch_pages_mode:
                for u in urls:
                    p = self._list_page_from_url(u)
                    if p:
                        self._list_batch_last_page = p
                        if u not in pending:      # 该页来自缓存
                            self.list_page_var.set(str(p))
            if cached_items:
                self._list_items = list(cached_items)
                self._list_status_var.set(
                    f"已从缓存载入 {len(urls) - len(pending)}/{len(urls)} 个地址，"
                    f"共 {len(self._list_items)} 个视频")
        self._render_list_items()        # 先显示缓存内容（并清空旧内容），再补抓缺的页
        # 抓取器始终建好：全部命中缓存时，个别未缓存的缩略图也要能联网补下载
        worker = self._build_list_worker()
        self._list_worker = worker

        if not pending:
            # 全部命中缓存：无需联网
            self._list_busy = False
            self._list_page_label_var.set(
                self._format_page_info(self._list_pagination,
                                       self._list_batch_last_page or page_no))
            self._list_status_var.set(
                f"已从缓存载入（{len(urls)} 个地址共 {len(self._list_items)} 个视频，"
                f"点“刷新”重新联网抓取）")
            self._log(f"[列表页] 批量抓取全部命中缓存（{len(urls)} 个地址，"
                      f"{len(self._list_items)} 个视频），未联网")
            return

        # 已载入地址数（缓存命中的 + 后续每抓完一页 +1），用于状态栏进度
        self._list_batch_loaded = len(urls) - len(pending)
        self._list_status_var.set(
            f"准备批量抓取：{len(urls)} 个地址，其中 {len(pending)} 个需要联网")
        self._log(f"[列表页] 开始批量抓取（共 {len(urls)} 个地址，"
                  f"其中 {len(pending)} 个需要联网）")
        threading.Thread(target=self._list_batch_thread,
                         args=(worker, pending, token, len(urls)), daemon=True).start()

    def _list_batch_from_cache(self, urls: List[str]) -> Tuple[List[Dict], List[str], Optional[str]]:
        """从磁盘缓存取出批量地址里已缓存的页

        返回 (已缓存条目, 需要联网抓取的地址, 最后一个缓存页的“下一页”地址)。
        “确认为空”（主站正常但该地址没有视频）的地址也视为已缓存，不再重复联网。
        """
        items: List[Dict] = []
        pending: List[str] = []
        next_url: Optional[str] = None
        for url in urls:
            data = self._list_load_cached_page(url)
            if data:
                items.extend(data.get('items') or [])
                next_url = data.get('next_url') or next_url
                self._list_pagination = data.get('pagination') or self._list_pagination
                continue
            if self._list_cache_is_empty(url):
                continue                # 之前已确认该地址没有视频，跳过不联网
            pending.append(url)
        return items, pending, next_url

    def _list_batch_thread(self, worker: 'WebGrabWorker', urls: List[str], token: int,
                           total: int = 0):
        """后台线程：按顺序抓取批量地址，每页解析完立即交给主线程追加显示

        说明：每页抓完后主线程会立即渲染该页卡片；上一页的缩略图下载在后台线程池中
        继续进行，不会因翻到下一页而被丢弃或重复下载（见 _list_load_thumb 的等待机制）。
        """
        try:
            use_render = bool(self.list_render_var.get())
            cfg = self._list_parse_cfg()
            total = total or len(urls)
            for idx, url in enumerate(urls, 1):
                if token != self._list_token:
                    worker.stop()
                    return
                self._run_in_main_thread(
                    lambda i=idx, u=url: self._list_status_var.set(
                        f"正在抓取：{u}（第 {i}/{len(urls)} 个待抓地址，共 {total} 个）"))
                html = worker.fetch_page(url, use_render=use_render)
                items = parse_list_items(html or '', url, cfg) if html else []

                # 静态抓取不到条目时，自动用浏览器渲染重试一次（列表由 JS 生成的情况）
                if html and not items and not use_render:
                    rendered = worker.fetch_page(url, use_render=True)
                    if rendered:
                        html = rendered
                        items = parse_list_items(html, url, cfg)

                if not items:
                    # 没有缩略图数据：先检查主站是否可以连接。
                    # 主站正常 → 该地址对应的视频不存在，跳过（只在日志提示），
                    #   并记为“确认为空”，下次打开同一网址不再重复联网；
                    # 主站也不通 → 连接错误（不写缓存，下次仍会重试）
                    if self._list_site_reachable(worker, url):
                        self._list_cache_empty(url)
                        self._log(f"[列表页] {url} 无缩略图数据"
                                  f"（主站连接正常，该视频不存在），跳过")
                    else:
                        self._log(f"[列表页] {url} 连接失败（主站也无法连接）")
                    continue

                next_url = parse_next_page_url(html or '', url, self.list_next_var.get())
                pagination = parse_pagination(html or '', url)
                alert_urls = parse_alert_urls(html or '', url, self.list_alert_var.get())
                self._run_in_main_thread(
                    lambda it=list(items), u=url, nxt=next_url, pg=pagination,
                    au=alert_urls, done=idx: self._list_batch_append(
                        token, it, u, nxt, pg, au, done, total))
                # 上一页缩略图还在下载时先等一等再抓下一页，避免图片请求积压过多
                if idx < len(urls):
                    self._list_wait_thumbs_idle()
            self._run_in_main_thread(
                lambda t=token: self._list_batch_done(t, total))
        except Exception as e:
            self._log(f"[列表页] 批量抓取异常: {e}")
            self._run_in_main_thread(lambda t=token: self._list_batch_done(t, 0, error=True))

    def _list_batch_append(self, token: int, items: List[Dict], url: str,
                           next_url: Optional[str], pagination: Optional[Dict],
                           alert_urls: List[str], done: int, total: int):
        """主线程：把批量抓取到的一页条目追加进列表并立即刷新显示"""
        if token != self._list_token:
            return
        # 公告层网址自动补充（与单页抓取行为一致）
        if alert_urls and bool(self.list_auto_collect_var.get()):
            added = self._list_urls_add(alert_urls)
            if added:
                self._log(f"[列表页] 已从公告层自动补充 {added} 个网址到网址库")
        self._list_items.extend(items)
        self._list_next_url = next_url
        self._list_pagination = pagination or {}
        # 每页结果写入磁盘缓存，供下次打开同一网址时直接显示（不再联网）
        self._list_cache_page(url, items, next_url, pagination)
        page_no = self._list_page_from_url(url)
        if page_no and getattr(self, '_list_batch_pages_mode', False):
            self._list_batch_last_page = page_no       # 记录最后抓到的页码，供“下一页”续接
            self.list_page_var.set(str(page_no))
        self._list_page_label_var.set(
            self._format_page_info(self._list_pagination, page_no))
        # 进度按“已载入地址数”统计（缓存命中的页也计入，见 _start_list_batch）
        self._list_batch_loaded = getattr(self, '_list_batch_loaded', 0) + 1
        self._list_status_var.set(
            f"已载入 {self._list_batch_loaded}/{total} 个地址，"
            f"共 {len(self._list_items)} 个视频　{url}")
        # 卡片模式只追加新卡片（不重建已有卡片，避免打断已开始的缩略图下载）；
        # 列表模式无图片，直接整体重绘
        if getattr(self, '_list_view_mode', 'card') == 'card':
            self._list_append_cards(items)
        else:
            self._render_list_items()

    def _list_wait_thumbs_idle(self):
        """多页抓取时：上一页缩略图还在大量下载就稍等再抓下一页

        等待有上限（LIST_BATCH_THUMB_WAIT_SEC），不会一直卡住不翻页；
        缩略图隐藏或已缓存时没有等待中的下载，会立即返回。
        """
        time.sleep(0.6)        # 先给主线程一点时间渲染上一页卡片（下载任务随之入队）
        deadline = time.time() + LIST_BATCH_THUMB_WAIT_SEC
        while time.time() < deadline:
            if len(getattr(self, '_list_thumb_pending', ())) <= LIST_BATCH_THUMB_PENDING:
                return
            time.sleep(0.3)

    def _list_batch_done(self, token: int, total: int, error: bool = False):
        """批量抓取结束（主线程）：恢复空闲状态并汇总日志"""
        if token != self._list_token:
            return
        self._list_busy = False
        if error:
            self._list_status_var.set("批量抓取异常，已中止")
            return
        count = len(self._list_items)
        self._list_status_var.set(
            f"批量抓取完成：{total} 个地址共解析到 {count} 个视频")
        self._log(f"[列表页] 批量抓取完成：{total} 个地址，共 {count} 个视频")

    @staticmethod
    def _widget_alive(widget) -> bool:
        """控件是否仍然存在（卡片重建后旧 Label 会被销毁）"""
        try:
            return widget is not None and bool(widget.winfo_exists())
        except tk.TclError:
            return False

    def _list_append_cards(self, items: List[Dict]):
        """把新一页的卡片追加到已有卡片后面（不重建已有卡片）

        多页连续抓取时逐页调用：已有卡片（含正在下载的缩略图）保持不动，
        只有新抓到的这一页会新建卡片，避免每来一页就整屏重建、重复下载缩略图。
        """
        frame = getattr(self, '_list_items_frame', None)
        if frame is None:
            return
        if not self._list_cards:
            # 首次有数据：清掉“没有解析到视频”占位提示（该提示是 frame 的子控件）
            for child in frame.winfo_children():
                try:
                    child.destroy()
                except tk.TclError:
                    pass
        token = self._list_token
        for item in items:
            self._list_cards.append(self._create_list_card(item, token))
        self._list_relayout()

    def _list_cache_empty_path(self, url: str) -> str:
        """“确认为空”（主站正常但没有视频数据）标记的缓存文件路径"""
        return os.path.join(LIST_CACHE_DIR, 'e_' + self._list_cache_key(url) + '.json')

    def _list_cache_empty(self, url: str):
        """记录某地址已确认没有视频数据（下次打开同一网址不再重复联网）"""
        try:
            os.makedirs(LIST_CACHE_DIR, exist_ok=True)
            data = {'url': url, 'empty': True, 'time': time.time()}
            with open(self._list_cache_empty_path(url), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            self._list_trim_cache()
        except Exception:
            pass

    def _list_cache_is_empty(self, url: str) -> bool:
        """某地址是否已被确认没有视频数据"""
        try:
            return os.path.isfile(self._list_cache_empty_path(url))
        except Exception:
            return False

    def _list_site_reachable(self, worker: 'WebGrabWorker', url: str) -> bool:
        """检查网址主站（协议://域名/）是否能正常连接（复用抓取器的请求头/代理）"""
        host = self._host_of(url)
        if not host:
            return False
        try:
            req = urllib.request.Request(host + '/', headers=worker._build_headers())
            opener = worker._build_opener()
            with opener.open(req, timeout=10) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as e:
            return e.code < 500      # 能返回 HTTP 响应说明站点本身在线
        except Exception:
            return False

    def _list_fetch_thread(self, worker: 'WebGrabWorker', url: str, token: int):
        """后台线程：抓取列表页并解析（必要时自动切换网址库入口）"""
        try:
            use_render = bool(self.list_render_var.get())

            # 网址库入口：优先使用已记住的可连接入口
            target = self._resolve_grab_url(url)
            if target != url:
                self._log(f"[列表页] 使用网址库入口: {self._host_of(target)}")
                url = target

            page = worker.fetch_page(url, use_render=use_render)
            cfg = self._list_parse_cfg()
            items = parse_list_items(page or '', url, cfg)

            # 静态抓取不到条目时，自动用浏览器渲染重试一次（列表由 JS 生成的情况）
            if not items and not use_render:
                self._log("[列表页] 静态抓取未解析到条目，尝试用浏览器渲染重试...")
                try:
                    rendered = worker.fetch_page(url, use_render=True)
                    if rendered:
                        page = rendered
                        items = parse_list_items(page, url, cfg)
                except Exception as e:
                    self._log(f"[列表页] 浏览器渲染重试失败: {e}")

            # 入口失效（拿不到页面）：自动切换到网址库中的下一个入口重试一次
            if not page:
                next_host = self._list_switch_host(exclude=self._host_of(url))
                if next_host:
                    retry_url = self._apply_host(url, next_host)
                    self._log(f"[列表页] 当前入口无响应，切换到 {next_host} 重试...")
                    page = worker.fetch_page(retry_url, use_render=use_render)
                    if page:
                        url = retry_url
                        items = parse_list_items(page, url, cfg)

            # 识别页面的搜索框逻辑（输入框 id / 按钮 onclick / 搜索跳转路径）
            search_info = parse_search_action(page or '')
            if search_info:
                self._list_search_info = search_info
                if search_info.get('path'):
                    self._log(f"[列表页] 已识别搜索跳转路径: {search_info['path']}"
                              f"（输入框 {search_info.get('input_id', '')}，"
                              f"按钮 {search_info.get('js_func', '')}）")

            alert_urls = parse_alert_urls(page or '', url, self.list_alert_var.get())
            next_url = parse_next_page_url(page or '', url, self.list_next_var.get())
            # 页脚分页控件：当前第几页 / 共几页（用于翻页行右侧显示）
            pagination = parse_pagination(page or '', url)
            error = '' if page else '页面抓取失败（无响应内容）'
        except Exception as e:
            items, alert_urls, next_url, error = [], [], None, f"抓取异常: {e}"
            pagination = {}

        self._run_in_main_thread(
            lambda: self._on_list_loaded(token, url, items, next_url, alert_urls,
                                         error, pagination))

    def _on_list_loaded(self, token: int, url: str, items: List[Dict],
                        next_url: Optional[str], alert_urls: List[str], error: str,
                        pagination: Optional[Dict] = None):
        """列表页抓取完成（主线程刷新界面）"""
        if token != self._list_token:
            return                      # 已有新的抓取任务，丢弃本次结果
        self._list_busy = False

        # 自动把公告层里的网址补充到网址库
        if alert_urls and bool(self.list_auto_collect_var.get()):
            added = self._list_urls_add(alert_urls)
            if added:
                self._log(f"[列表页] 已从公告层自动补充 {added} 个网址到网址库")
                self._list_status_var.set(f"已从公告层补充 {added} 个网址到网址库")
                try:
                    if getattr(self, 'list_library_status_var', None) is not None:
                        self.list_library_status_var.set(
                            f"已自动补充 {added} 个网址（来自公告层）")
                except Exception:
                    pass

        self._list_next_url = next_url
        self._list_items = list(items)
        self._list_pagination = pagination or {}

        # 抓取成功：把页面解析结果写入磁盘缓存，供下次打开直接显示
        if items and not error:
            self._list_cache_page(url, self._list_items, next_url, self._list_pagination)

        page_no = self._list_page_from_url(url)
        self._list_page_label_var.set(self._format_page_info(self._list_pagination, page_no))

        if error:
            self._list_status_var.set(f"{error}（{url}）")
        else:
            self._list_status_var.set(f"共解析到 {len(items)} 个视频　{url}")

        self._render_list_items()

    @staticmethod
    def _format_page_info(pagination: Dict, page_no=None) -> str:
        """把分页信息格式化为翻页行右侧显示的短文本

        - 已知总页数：显示“当前/总数”（如 1/2）
        - 只知道页码区间（页面未显示总页数）：显示“起-止”（如 100-120）
        - 都没有：显示当前页码（取不到时为“页码: -”）
        """
        data = pagination or {}
        current = data.get('current')
        if current is None:
            current = page_no
        total = data.get('total')
        pages = data.get('pages') or []
        if current and total:
            return f"{current}/{total}"
        if len(pages) >= 2:
            return f"{min(pages)}-{max(pages)}"
        if current:
            return str(current)
        return "页码: -"

    def _render_list_items(self):
        """渲染列表页条目（瀑布流缩略图卡片 / 列表二选一）"""
        frame = self._list_items_frame
        if frame is None:
            return
        # 重新渲染（翻页/切换内容/批量追加新页）前，把缩略图区滚动条回到最上方
        try:
            self._list_canvas.yview_moveto(0)
        except Exception:
            pass
        for child in frame.winfo_children():
            child.destroy()
        self._list_cards = []
        self._list_photo_refs = []
        self._list_rows = []
        self._list_selected_row = None
        self._list_hide_title_tip()
        # 旧卡片已销毁：清掉等待队列里失效的 Label，只保留仍在下载中的地址
        for url, labels in list(self._list_thumb_waiters.items()):
            alive = [lb for lb in labels if self._widget_alive(lb)]
            if alive:
                self._list_thumb_waiters[url] = alive
            else:
                self._list_thumb_waiters.pop(url, None)

        if not self._list_items:
            tk.Label(frame, text="没有解析到视频。可检查“爬虫设置 → 视频列表页”里的选择器，"
                                 "或勾选“浏览器渲染”。",
                     background=LIST_PANEL_AREA_BG, foreground='#8a8a8a',
                     font=('Microsoft YaHei', 10)).grid(row=0, column=0, padx=20, pady=20,
                                                        sticky='w')
            return

        if getattr(self, '_list_view_mode', 'card') == 'list':
            self._render_list_table()
            return

        token = self._list_token
        for item in self._list_items:
            card = self._create_list_card(item, token)
            self._list_cards.append(card)
        self._list_relayout()

    def _render_list_table(self):
        """以列表形式展示当前条目：番号 / 标题 / 视频标志 / 日期（行间有分隔线）

        用行 Frame 替代 Treeview，与缩略图模式共用外层画布的唯一滚动条
        （行内容直接随画布滚动，不再自建滚动条）。
        交互：单击表头按该列排序（再点切换升/降序）；单击行选中高亮
        （处于“已翻译”状态时切换该行标题为中文，再次单击切回原文）；
        双击行把对应网址发送到主窗口抓取详情。
        """
        frame = self._list_items_frame
        if frame is None:
            return
        font = ('Microsoft YaHei', 9)
        head_font = ('Microsoft YaHei', 9, 'bold')

        # 列宽（像素）：表头与数据行共用同一组固定列宽，
        # 保证各行的番号/标志/日期列等宽，标题列竖向对齐在同一条线上
        col_widths = {'code': 130, 'title': 0, 'flag': 96, 'date': 118}

        def _apply_col_widths(container):
            for idx, col in enumerate(('code', 'title', 'flag', 'date')):
                w = col_widths[col]
                if w:
                    container.columnconfigure(idx, minsize=w)
                else:
                    container.columnconfigure(idx, weight=1)

        # 表头（可点击排序）
        sort_col = getattr(self, '_list_sort_col', None)
        sort_rev = bool(getattr(self, '_list_sort_reverse', False))
        header = tk.Frame(frame, background='#e8e8e8', height=30)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        for idx, col in enumerate(('code', 'title', 'flag', 'date')):
            text = self._list_heading_text(col)
            if col == sort_col:
                text += '  ▼' if sort_rev else '  ▲'
            head = tk.Label(header, text=text, background='#e8e8e8',
                            foreground='#333333', font=head_font,
                            anchor='w', padx=6, cursor='hand2')
            head.grid(row=0, column=idx, sticky='ew')
            head.bind('<Button-1>', lambda e, c=col: self._on_list_header_click(c))
        _apply_col_widths(header)
        tk.Frame(frame, height=1, background='#c8c8c8').pack(fill=tk.X)

        # 数据行：标题列完整显示（按可用宽度自动换行，不截断），
        # 行高随标题行数自动增大，行与行之间加 1px 分隔线
        for item in self._sorted_list_items():
            row = tk.Frame(frame, background='#ffffff')
            row.pack(fill=tk.X)
            code_l = tk.Label(row, text=item.get('code') or '', anchor='nw',
                              font=font, background='#ffffff',
                              foreground='#0066cc', padx=6, pady=5)
            code_l.grid(row=0, column=0, sticky='ew')
            title_l = tk.Label(row, text=self._list_plain_title(item.get('title')),
                               anchor='nw', justify=tk.LEFT, font=font,
                               background='#ffffff', foreground='#333333',
                               padx=6, pady=5)
            title_l.grid(row=0, column=1, sticky='ew')
            # 标题按列实际宽度自动换行，完整显示全部文字
            def _wrap_title(event, wdg=title_l):
                wdg.configure(wraplength=max(60, event.width - 12))
            title_l.bind('<Configure>', _wrap_title)
            flag_l = tk.Label(row, text=item.get('flag') or '', anchor='nw',
                              font=font, background='#ffffff',
                              foreground='#c62828' if item.get('flag') else '#333333',
                              padx=6, pady=5)
            flag_l.grid(row=0, column=2, sticky='ew')
            date_l = tk.Label(row, text=item.get('date') or '', anchor='nw',
                              font=font, background='#ffffff',
                              foreground='#333333', padx=6, pady=5)
            date_l.grid(row=0, column=3, sticky='ew')
            _apply_col_widths(row)

            sep = tk.Frame(frame, height=1, background='#dddddd')
            sep.pack(fill=tk.X)

            info = {'frame': row, 'title': title_l, 'item': item,
                    'labels': [row, code_l, title_l, flag_l, date_l]}
            self._list_rows.append(info)
            for wdg in (row, code_l, title_l, flag_l, date_l):
                wdg.bind('<Button-1>', lambda e, ri=info: self._on_list_row_click(ri))
                wdg.bind('<Double-Button-1>',
                         lambda e, it=item: self._on_list_row_double(it))
                self._bind_mousewheel(wdg, self._list_canvas)

        self._list_relayout()

    @staticmethod
    def _list_heading_text(col: str) -> str:
        """列表列名（排序状态以 ▲/▼ 标记在表头）"""
        base = {'code': '番号', 'title': '标题', 'flag': '视频标志',
                'date': '日期'}.get(col, col)
        return base

    def _sorted_list_items(self) -> List[Dict]:
        """按当前排序列/方向返回条目（无排序时保持原序）"""
        items = list(self._list_items)
        col = getattr(self, '_list_sort_col', None)
        if col:
            reverse = bool(getattr(self, '_list_sort_reverse', False))
            try:
                items = sorted(items,
                               key=lambda it: self._list_sort_key(it, col),
                               reverse=reverse)
            except Exception:
                pass
        return items

    def _on_list_header_click(self, col: str):
        """点击表头排序：首次为升序，再点切换为降序（仅作用于当前页）"""
        if getattr(self, '_list_sort_col', None) == col:
            self._list_sort_reverse = not getattr(self, '_list_sort_reverse', False)
        else:
            self._list_sort_col = col
            self._list_sort_reverse = False
        self._render_list_items()

    @staticmethod
    def _list_sort_key(item: Dict, col: str):
        """生成某列的排序键（保证跨类型稳定可比较）"""
        if col == 'code':
            code = item.get('code') or ''
            # 编号：优先按末尾数字自然序，其次按原字符串（英文/番号均可）
            m = re.search(r'(\d+)', code)
            num = int(m.group(1)) if m else -1
            return (num, code.lower(), code)
        if col == 'date':
            return WebGrabTab._parse_date_sort_key(item.get('date') or '')
        if col == 'flag':
            return (item.get('flag') or '', )
        # 标题：英文按字母序；中文按首字拼音声母（A-Z）再按原串，
        # 从而“英文”“文字首字拼音声母”两种排序意图均统一满足
        return WebGrabTab._title_sort_key(item.get('title') or '')

    @staticmethod
    def _title_sort_key(title: str):
        """标题排序键：首字拼音声母（英文则取首字母）→ 原串小写 → 原串"""
        initial = WebGrabTab._pinyin_initial(title)
        return (initial, title.lower(), title)

    @staticmethod
    def _pinyin_initial(text: str) -> str:
        """取文本首字符的拼音声母（A-Z）；非中文/英文回退为原字符大写。"""
        ch = (text or '').strip()[:1]
        if not ch:
            return '\uffff'          # 空串排最后
        if 'a' <= ch <= 'z' or 'A' <= ch <= 'Z':
            return ch.upper()
        if PYPINYIN_AVAILABLE:
            try:
                py = pypinyin.lazy_pinyin(ch, style=pypinyin.NORMAL)
                if py and py[0]:
                    return py[0][0].upper()
            except Exception:
                pass
        # 中文但取不到拼音：回退按 Unicode 码位（仍稳定可排序）
        return ch

    @staticmethod
    def _parse_date_sort_key(date: str):
        """日期排序键：尽量解析为 (年, 月, 日) 元组，否则回退原串。"""
        m = re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})', date or '')
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m2 = re.search(r'(\d{4})', date or '')
        if m2:
            return (int(m2.group(1)), 0, 0)
        return (9999, 0, 0) if not date else (date,)

    def _on_list_row_click(self, row_info: Dict):
        """单击列表行：选中高亮；处于“已翻译”状态时切换该行标题 中文/原文"""
        self._select_list_row(row_info)
        if not getattr(self, '_list_titles_translated', False):
            return
        item = row_info.get('item') or {}
        zh = self._list_title_zh.get(self._list_title_key(item)) or ''
        if not zh:
            return
        label = row_info.get('title')
        try:
            cur = label.cget('text')
        except tk.TclError:
            return
        original = self._list_plain_title(item.get('title'))
        try:
            label.configure(text=zh if cur != zh else original)
        except tk.TclError:
            pass

    def _select_list_row(self, row_info: Dict):
        """高亮选中行（取消上一行的选中底色）"""
        prev = getattr(self, '_list_selected_row', None)
        if prev is not None and prev is not row_info:
            for w in prev.get('labels', []):
                try:
                    w.configure(background='#ffffff')
                except tk.TclError:
                    pass
        self._list_selected_row = row_info
        for w in row_info.get('labels', []):
            try:
                w.configure(background='#cfe4ff')
            except tk.TclError:
                pass

    def _on_list_row_double(self, item: Dict):
        """双击列表行：与点击缩略图一致，加入主窗口抓取该视频详情"""
        self._list_hide_title_tip()
        self._list_add_to_main(item)

    # ---------- 列表层标题翻译 ----------
    @staticmethod
    def _list_plain_title(title: Any) -> str:
        """标题转纯文本：去掉 HTML 标签/实体，压缩空白（用于卡片与列表行显示）"""
        t = str(title or '')
        t = re.sub(r'<[^>]+>', ' ', t)
        for ent, ch in (('&amp;', '&'), ('&quot;', '"'), ('&#39;', "'"),
                        ('&lt;', '<'), ('&gt;', '>'), ('&nbsp;', ' ')):
            t = t.replace(ent, ch)
        return re.sub(r'\s+', ' ', t).strip()

    @staticmethod
    def _list_title_key(item: Dict) -> str:
        """条目标题翻译缓存键：优先详情页网址，其次番号+标题"""
        url = str((item or {}).get('url') or '').strip()
        if url:
            return url
        return f"{item.get('code') or ''}|{item.get('title') or ''}"

    def _list_full_display_title(self, item: Dict) -> str:
        """悬浮提示用的完整标题：已翻译时显示中文，否则显示原文"""
        title = self._list_plain_title(item.get('title'))
        if getattr(self, '_list_titles_translated', False):
            zh = self._list_title_zh.get(self._list_title_key(item)) or ''
            if zh:
                return zh
        return title

    def _list_show_title_tip(self, item: Dict, event=None):
        """鼠标悬浮缩略图：弹出 title 层显示完整标题（已翻译时为中文）"""
        self._list_hide_title_tip()
        text = self._list_full_display_title(item)
        if not text:
            return
        win = getattr(self, '_list_win', None)
        if win is None:
            return
        tip = tk.Toplevel(win)
        tip.overrideredirect(True)
        try:
            tip.attributes('-topmost', True)
        except Exception:
            pass
        label = tk.Label(tip, text=text, justify=tk.LEFT, anchor='w',
                         background='#ffffe0', foreground='#333333',
                         relief=tk.SOLID, borderwidth=1,
                         wraplength=420, padx=8, pady=5,
                         font=('Microsoft YaHei', 9))
        label.pack()
        tip.update_idletasks()
        x = (event.x_root + 14) if event is not None else win.winfo_rootx() + 80
        y = (event.y_root + 16) if event is not None else win.winfo_rooty() + 80
        try:
            sw = tip.winfo_screenwidth()
            sh = tip.winfo_screenheight()
            tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
            x = min(max(0, x), sw - tw - 8)
            y = min(max(0, y), sh - th - 8)
        except Exception:
            pass
        tip.geometry(f"+{x}+{y}")
        self._list_title_tip = tip

    def _list_hide_title_tip(self):
        """收起悬浮标题提示层（可重复调用）"""
        tip, self._list_title_tip = getattr(self, '_list_title_tip', None), None
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass

    def _list_on_translate_click(self):
        """列表层“翻译”按钮：把当前页所有标题翻译为中文（再次点击恢复原文）

        前提：主窗口已载入翻译模型。翻译结果按条目缓存（_list_title_zh），
        缩略图模式用于悬浮提示，列表模式用于单击行时的中文/原文切换。
        """
        if getattr(self, '_list_translating', False):
            return
        if getattr(self, '_list_titles_translated', False):
            # 再次点击：恢复原文显示（翻译缓存保留，便于再次切换）
            self._list_titles_translated = False
            self._refresh_list_translate_btn()
            self._render_list_items()
            self._list_status_var.set("标题已恢复原文")
            return

        translator = self._current_translator()
        if translator is None or not translator.is_loaded():
            messagebox.showwarning(
                "提示", "请先在主窗口载入翻译模型，再使用列表页的“翻译”功能")
            return

        # 收集尚未翻译过的标题（已缓存的直接复用）
        pending = []
        for item in self._list_items:
            title = self._list_plain_title(item.get('title'))
            key = self._list_title_key(item)
            if title and key not in self._list_title_zh:
                pending.append((key, title))
        if not pending:
            self._list_titles_translated = True
            self._refresh_list_translate_btn()
            self._render_list_items()
            self._list_status_var.set("标题已切换为中文")
            return

        self._list_translating = True
        btn = getattr(self, '_list_translate_btn', None)
        try:
            btn.configure(text="翻译中…", state='disabled')
        except tk.TclError:
            pass
        self._list_status_var.set(f"正在翻译标题 0/{len(pending)}")
        token = self._list_token

        def work():
            total = len(pending)
            for i, (key, title) in enumerate(pending, 1):
                if token != self._list_token:
                    return
                try:
                    zh = translator.translate(title)
                except Exception as e:
                    zh = ''
                    self._log(f"列表标题翻译失败: {e}")
                if zh:
                    self._list_title_zh[key] = zh
                self._run_in_main_thread(
                    lambda i=i, total=total, tk_=token:
                    self._list_translate_progress(tk_, i, total))
            self._run_in_main_thread(lambda tk_=token: self._list_translate_done(tk_))

        threading.Thread(target=work, daemon=True).start()

    def _list_translate_progress(self, token: int, done: int, total: int):
        """翻译进度（主线程）"""
        if token != self._list_token:
            return
        self._list_status_var.set(f"正在翻译标题 {done}/{total}")

    def _list_translate_done(self, token: int):
        """翻译完成（主线程）：切换为已翻译状态并刷新显示"""
        self._list_translating = False
        if token != self._list_token:
            return
        self._list_titles_translated = True
        self._refresh_list_translate_btn()
        self._render_list_items()
        self._list_status_var.set(
            f"标题翻译完成（{len(self._list_title_zh)} 条）：悬浮缩略图显示中文；"
            "列表模式单击行可切换 中文/原文")

    def _refresh_list_translate_btn(self):
        """按当前翻译状态刷新按钮文字与可用性"""
        btn = getattr(self, '_list_translate_btn', None)
        if btn is None:
            return
        try:
            if getattr(self, '_list_translating', False):
                btn.configure(text="翻译中…", state='disabled')
            elif getattr(self, '_list_titles_translated', False):
                btn.configure(text="恢复原文", state='normal')
            else:
                btn.configure(text="翻译", state='normal')
        except tk.TclError:
            pass

    def _create_list_card(self, item: Dict, token: int) -> tk.Frame:
        """创建单个视频卡片（缩略图 + 番号 + 2 行标题 + 日期/标志）

        卡片高度固定、宽度由网格按“一排 4 个”均分（见 _list_relayout），
        内部控件随卡片宽度拉伸；标题预留固定两行高度并按实际宽度换行截断，
        日期与标志行始终能显示完整，不会被标题挤掉。
        """
        card = tk.Frame(self._list_items_frame, height=LIST_CARD_H,
                        background='#ffffff', relief=tk.RIDGE, borderwidth=1)
        card.pack_propagate(False)

        # 缩略图占位框：用 Frame 固定像素高度（Label 的 width/height 对文字是字符单位，
        # 直接用 Label 会导致高度异常，从而把下面的番号/标题/日期挤出卡片）
        img_holder = tk.Frame(card, height=LIST_THUMB_IMG_H,
                              background='#f0f0f0', cursor='hand2')
        img_holder.pack(fill=tk.X, padx=2, pady=(2, 0))
        img_holder.pack_propagate(False)
        img_label = tk.Label(img_holder, text="…", background='#f0f0f0',
                             foreground='#999999', cursor='hand2')
        img_label.pack(expand=True)

        code = item.get('code') or ''
        code_label = tk.Label(card, text=code or '(无番号)', anchor='w',
                              background='#ffffff', foreground='#0066cc',
                              font=('Microsoft YaHei', 9, 'bold'), cursor='hand2')
        code_label.pack(fill=tk.X, padx=4)

        title = self._list_plain_title(item.get('title'))
        # height=2 + anchor='nw'：固定两行高度，超出部分自动裁切（不显示省略号），
        # 保证下方信息行始终显示完整；wraplength 随卡片实际宽度自适应
        title_label = tk.Label(card, text=title, anchor='nw', justify=tk.LEFT,
                               wraplength=180, height=2, background='#ffffff',
                               foreground='#444444', font=('Microsoft YaHei', 8),
                               cursor='hand2')
        title_label.pack(fill=tk.X, padx=4, pady=(1, 1))
        title_label.bind(
            '<Configure>',
            lambda e, wdg=title_label: wdg.configure(wraplength=max(60, e.width - 10)))

        date = item.get('date') or ''
        flag = item.get('flag') or ''
        meta_text = '　'.join(x for x in (date, flag) if x) or ' '
        meta_label = tk.Label(card, text=meta_text, anchor='w', background='#ffffff',
                              foreground='#c62828' if flag else '#888888',
                              font=('Microsoft YaHei', 8), cursor='hand2')
        meta_label.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 3))

        for widget in (card, img_holder, img_label, code_label, title_label, meta_label):
            widget.bind('<Button-1>',
                        lambda e, it=item: (self._list_hide_title_tip(),
                                            self._list_add_to_main(it)))
            self._bind_mousewheel(widget, self._list_canvas)

        # 悬浮缩略图框：弹出 title 层显示完整标题（已翻译时为中文）
        for wdg in (img_holder, img_label):
            wdg.bind('<Enter>', lambda e, it=item: self._list_show_title_tip(it, e))
            wdg.bind('<Leave>', lambda e: self._list_hide_title_tip())

        # 缩略图隐藏时：不下载也不显示原图，用空白占位（保留卡片布局与点击行为）
        if getattr(self, '_list_thumbs_visible', True):
            self._list_load_thumb(item, img_label, token)
        else:
            try:
                img_label.configure(image='', text='')
            except tk.TclError:
                pass
        return card

    def _list_load_thumb(self, item: Dict, label: tk.Label, token: int):
        """异步下载并显示列表页缩略图"""
        url = item.get('thumb') or ''
        if not url:
            try:
                label.configure(image='', text="无缩略图")
            except tk.TclError:
                pass
            return

        cached = self._image_cache.get(url)
        if cached:
            self._list_apply_thumb(label, cached, token)
            return

        # 磁盘缓存：之前下载过的缩略图直接读本地文件（不重新联网）
        disk_path = self._list_cache_thumb_path(url)
        if os.path.isfile(disk_path):
            try:
                with open(disk_path, 'rb') as f:
                    data = f.read()
                if data:
                    self._cache_put(url, data)
                    self._list_apply_thumb(label, data, token)
                    return
            except Exception:
                pass

        # 该地址已有下载任务在跑：不再重复提交，只把本卡片登记为等待者。
        # 多页连续抓取时每来一页就渲染一次，若每次都重新提交下载，上一页未完成的
        # 图片请求会被大量重复提交、且旧卡片被销毁后结果无人接收——登记等待者后，
        # 下载完成的图片会同时填进所有在等它的卡片（包括翻页后新建的同一批卡片）。
        waiters = self._list_thumb_waiters.setdefault(url, [])
        waiters.append(label)
        if url in self._list_thumb_pending:
            return

        self._list_thumb_pending.add(url)

        def load():
            data = None
            worker = getattr(self, '_list_worker', None)
            try:
                data = worker.download_image(url) if worker else None
            except Exception:
                data = None
            self._run_in_main_thread(lambda: self._list_thumb_ready(url, data, token))

        self._image_pool.submit(load)

    def _list_thumb_ready(self, url: str, img_data: Optional[bytes], token: int):
        """某地址缩略图下载完成（主线程）：填进所有等待该图片的卡片"""
        self._list_thumb_pending.discard(url)
        labels = self._list_thumb_waiters.pop(url, []) or []
        for label in labels:
            self._list_apply_thumb(label, img_data, token, url)

    def _list_apply_thumb(self, label: tk.Label, img_data: Optional[bytes],
                          token: int, url: str = ''):
        """把缩略图填进卡片（主线程执行）"""
        if token != self._list_token:
            return
        try:
            if not label.winfo_exists():
                return
        except tk.TclError:
            return

        if not img_data:
            try:
                label.configure(image='', text="加载失败")
            except tk.TclError:
                pass
            return

        if url:
            self._cache_put(url, img_data)
            # 同步写入磁盘缓存，供关闭层再打开时直接显示（不重新联网）
            try:
                path = self._list_cache_thumb_path(url)
                os.makedirs(LIST_CACHE_DIR, exist_ok=True)
                if not os.path.isfile(path):
                    with open(path, 'wb') as f:
                        f.write(img_data)
                    self._list_trim_cache()
            except Exception:
                pass
        try:
            img = Image.open(io.BytesIO(img_data))
            img.load()
            thumb = img.copy()
            thumb.thumbnail((LIST_THUMB_SIZE - 6, LIST_THUMB_IMG_H), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            label.configure(image=photo, text='')
            label.image = photo
            self._list_photo_refs.append(photo)
        except Exception as e:
            self._log(f"列表页缩略图处理失败: {e}")
            try:
                label.configure(image='', text="加载失败")
            except tk.TclError:
                pass

    def _list_relayout(self, event=None):
        """按画布宽高重排：卡片模式固定一排 4 个（等宽拉伸），列表模式行铺满宽度"""
        canvas = self._list_canvas
        frame = self._list_items_frame
        if canvas is None or frame is None:
            return
        try:
            width = max(canvas.winfo_width(), LIST_THUMB_SIZE)
        except (tk.TclError, AttributeError):
            width = LIST_THUMB_SIZE

        # 卡片固定一排 4 个：4 列等宽、随画布宽度拉伸（宽度变窄即“减少缩略图框宽度”）；
        # 画布过窄放不下 4 个时按最小宽度回退为更少列
        columns = 4 if width >= 680 else max(1, width // (LIST_THUMB_SIZE + 26))
        for col in range(columns):
            try:
                frame.columnconfigure(col, weight=1, uniform='listcard')
            except tk.TclError:
                pass
        for index, card in enumerate(self._list_cards):
            row, col = divmod(index, columns)
            try:
                card.grid(row=row, column=col, padx=8, pady=8, sticky='ew')
            except tk.TclError:
                pass
        try:
            frame.update_idletasks()
            # 两种模式高度都随内容自动撑开（若钉成画布高度，超出部分会被裁切、
            # scrollregion 等于可视区，导致鼠标滚轮“无法滚动”）。
            # 列表模式同样由外层画布滚动，共用唯一滚动条。
            canvas.itemconfigure(
                self._list_window,
                width=canvas.winfo_width(),
                height=max(1, frame.winfo_reqheight()))
            canvas.configure(scrollregion=canvas.bbox('all'))
        except tk.TclError:
            pass

    # ---------- 翻页 ----------
    def _list_prev_page(self):
        """上一页"""
        self._list_goto_page(delta=-1)

    def _list_next_page(self):
        """下一页（优先用页面上的“下一页”链接）"""
        self._list_goto_page(delta=1)

    def _list_goto_page(self, delta: Optional[int] = None):
        """按页码跳转：delta 为 +1/-1 时翻页，否则按输入框页码打开"""
        current_url = self.list_url_entry_var.get().strip()
        if not current_url:
            messagebox.showinfo("提示", "请先打开一个列表页")
            return

        if delta is not None:
            # 下一页优先使用页面上的 a#next 链接，其次按页码模板推算
            if delta > 0 and getattr(self, '_list_next_url', None):
                self._list_open_url(self._list_next_url)
                return
            # 批量抓取（多页/{#}）后：从最后抓到的那一页继续往后翻，
            # 例如“显示页数 5”已抓完 1~5 页，再点“下一页”就从第 6 页起再抓 5 页
            if delta > 0:
                last_page = getattr(self, '_list_batch_last_page', None)
                if last_page:
                    target = self._list_url_for_page(current_url, last_page + 1)
                    if target:
                        self._list_open_url(target)
                        return
            if self._list_page_from_url(current_url) is None:
                self._list_status_var.set(
                    "当前页签不是分页列表（演员/系列/搜索结果页），无法翻页")
                return
            current_page = self._list_page_from_url(current_url) or 1

            if delta < 0:
                # 上一页：与“下一页”对称地后退“显示页数”页（打开后同样会连读这么多页）；
                # 前面不足一屏时直接回到第 1 页；已经在第 1 页则不再往前翻
                pages = self._list_display_pages()
                if current_page <= 1:
                    self._list_status_var.set("已经是第 1 页")
                    return
                target_page = current_page - pages if pages > 1 else current_page - 1
                if target_page < 1:
                    target_page = 1
                self._log(f"[列表页] 上一页：从第 {current_page} 页退到第 {target_page} 页"
                          f"（显示页数 {pages}）")
                target = self._list_url_for_page(current_url, target_page)
                if not target:
                    messagebox.showinfo(
                        "提示", "无法推算页码地址，请在“爬虫设置 → 视频列表页”中配置页码模板"
                                "（含 {page} 占位符）")
                    return
                self._list_open_url(target)
                return

            target_page = current_page + delta
            if target_page < 1:
                return
            target = self._list_url_for_page(current_url, target_page)
            if not target:
                messagebox.showinfo(
                    "提示", "无法推算页码地址，请在“爬虫设置 → 视频列表页”中配置页码模板"
                            "（含 {page} 占位符）")
                return
            self._list_open_url(target)
            return

        text = self.list_page_var.get().strip()
        if not text.isdigit():
            messagebox.showwarning("警告", "请输入有效的页码（数字）")
            return
        if self._list_page_from_url(current_url) is None:
            self._list_status_var.set("当前页签不是分页列表，无法按页码跳转")
            return
        target = self._list_url_for_page(current_url, int(text))
        if not target:
            messagebox.showinfo(
                "提示", "无法推算页码地址，请先配置页码模板（含 {page} 占位符）")
            return
        self._list_open_url(target)

    # ---------- 加入主窗口抓取详情 ----------
    def _list_add_to_main(self, item: Dict):
        """点击列表页缩略图：把该视频加入主窗口做详情页抓取"""
        url = (item or {}).get('url') or ''
        if not url:
            messagebox.showwarning("警告", "该条目没有解析到详情页链接，请检查“详情链接”选择器")
            return

        label = (item.get('code') or item.get('title') or url)[:60]
        self._log(f"[列表页] 加入详情页抓取: {label} -> {url}")

        if getattr(self, 'multi_link_var', None) is not None and self.multi_link_var.get():
            self._enqueue_url(url, activate=True)
            self._list_status_var.set(f"已加入并抓取详情页: {label}")
        else:
            self.url_var.set(url)
            self._start_grab()
            self._list_status_var.set(f"已在主窗口抓取详情页: {label}")

    # ========== 缩略图显示 / 隐藏 ==========
    def _toggle_thumb_visibility(self):
        """切换缩略图显示/隐藏（默认隐藏）"""
        self._thumbs_visible = not getattr(self, '_thumbs_visible', False)
        self._draw_thumb_toggle_icon()
        self._apply_thumb_visibility()

    def _apply_thumb_visibility(self):
        """按当前显示状态重建缩略图区：
        显示时按 current_images 重建全部缩略图；隐藏时不创建任何缩略图，仅给出提示
        """
        if not hasattr(self, 'images_frame'):
            return
        for widget in self.images_frame.winfo_children():
            widget.destroy()
        self.image_labels.clear()
        self.thumb_cells.clear()
        self._thumb_loading.clear()   # 允许重新显示时重新下载缩略图

        if getattr(self, '_thumbs_visible', False):
            for item in self.current_images:
                cell, label = self._create_thumb_cell(item)
                self.thumb_cells.append(cell)
                self.image_labels.append(label)
                self._load_thumb_async(item, cell, label)
        else:
            if self.current_images:
                hint = (f"缩略图已隐藏：共 {len(self.current_images)} 张"
                        "（点击图区右上角图标可显示）")
            else:
                hint = "缩略图已隐藏（暂无图片）"
            tk.Label(self.images_frame, text=hint, background='#f0f0f0',
                     foreground='#8a8a8a', font=('Microsoft YaHei', 10)).grid(
                row=0, column=0, padx=24, pady=24, sticky='w')
        self._relayout_images()

    def _draw_thumb_toggle_icon(self, hover: bool = False):
        """绘制“缩略图显示/隐藏”图标按钮
        显示态：四格实心（表示缩略图可见）；隐藏态：四格空心 + 斜杠
        """
        canvas = getattr(self, 'thumb_toggle_canvas', None)
        if canvas is None:
            return
        try:
            canvas.delete('all')
        except tk.TclError:
            return

        visible = bool(getattr(self, '_thumbs_visible', False))
        size = THUMB_TOGGLE_BTN_SIZE
        # 按钮底板（图标之外的留白即点击区，整体略大于图标）
        canvas.create_rectangle(1, 1, size - 1, size - 1,
                                fill='#f7f7f7' if hover else '#ffffff',
                                outline='#c8c8c8')

        pad, gap = 7, 3
        box = (size - pad * 2 - gap) / 2
        color = '#333333' if visible else '#9a9a9a'
        for row in range(2):
            for col in range(2):
                x0 = pad + col * (box + gap)
                y0 = pad + row * (box + gap)
                canvas.create_rectangle(x0, y0, x0 + box, y0 + box,
                                        fill=color if visible else '',
                                        outline=color)
        if not visible:
            canvas.create_line(pad - 2, size - pad + 2, size - pad + 2, pad - 2,
                               fill='#666666', width=2)

    def _draw_page_panel_icon(self, hover: bool = False):
        """绘制“页面”图标按钮（与“显示/隐藏缩略图”图标按钮同尺寸同风格）

        图标含义：一张页面（上方标题条 + 下方多行列表），表示“视频列表页”。
        """
        canvas = getattr(self, 'page_panel_canvas', None)
        if canvas is None:
            return
        try:
            canvas.delete('all')
        except tk.TclError:
            return

        size = THUMB_TOGGLE_BTN_SIZE
        color = PAGE_BTN_COLOR_HOVER if hover else PAGE_BTN_COLOR

        # 按钮底板（与缩略图按钮一致）
        canvas.create_rectangle(1, 1, size - 1, size - 1,
                                fill=PAGE_BTN_BG_HOVER if hover else PAGE_BTN_BG,
                                outline=PAGE_BTN_BORDER)

        # 页面外框
        x0, y0, x1, y1 = 7, 6, size - 7, size - 6
        canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=1)
        # 页面标题条
        canvas.create_line(x0, y0 + 5, x1, y0 + 5, fill=color)
        # 列表行
        for index in range(3):
            y = y0 + 10 + index * 4
            if y > y1 - 3:
                break
            canvas.create_line(x0 + 3, y, x1 - 5, y, fill=color)
        # 右侧滚动条示意
        canvas.create_line(x1 - 3, y0 + 7, x1 - 3, y1 - 3, fill=color)

    # ========== 列表页浏览层：显示方式图标按钮 ==========
    def _draw_list_thumb_icon(self, hover: bool = False):
        """绘制列表层的“缩略图显示/隐藏”图标按钮（与主窗口图标同风格）

        显示态：四格实心；隐藏态：四格空心 + 斜杠（列表改用空白图占位）
        """
        canvas = getattr(self, '_list_thumb_canvas', None)
        if canvas is None:
            return
        try:
            canvas.delete('all')
        except tk.TclError:
            return

        visible = bool(getattr(self, '_list_thumbs_visible', True))
        size = THUMB_TOGGLE_BTN_SIZE
        canvas.create_rectangle(1, 1, size - 1, size - 1,
                                fill=PAGE_BTN_BG_HOVER if hover else PAGE_BTN_BG,
                                outline=PAGE_BTN_BORDER)
        pad, gap = 7, 3
        box = (size - pad * 2 - gap) / 2
        color = '#333333' if visible else '#9a9a9a'
        for row in range(2):
            for col in range(2):
                x0 = pad + col * (box + gap)
                y0 = pad + row * (box + gap)
                canvas.create_rectangle(x0, y0, x0 + box, y0 + box,
                                        fill=color if visible else '',
                                        outline=color)
        if not visible:
            canvas.create_line(pad - 2, size - pad + 2, size - pad + 2, pad - 2,
                               fill='#666666', width=2)

    def _draw_list_view_icon(self, hover: bool = False):
        """绘制列表层的“瀑布流卡片 / 列表”切换图标按钮

        列表态：三行横线（表示表格行）；瀑布流态：四格方块（表示卡片网格）
        """
        canvas = getattr(self, '_list_view_canvas', None)
        if canvas is None:
            return
        try:
            canvas.delete('all')
        except tk.TclError:
            return

        size = THUMB_TOGGLE_BTN_SIZE
        list_mode = getattr(self, '_list_view_mode', 'card') == 'list'
        # 图标固定为“列表图案”（三行横线），与右侧的四格缩略图图标区分开；
        # 当前处于列表模式时用深色实心表示激活状态，瀑布流模式用浅灰
        color = (PAGE_BTN_COLOR_HOVER if hover
                 else ('#333333' if list_mode else '#9a9a9a'))
        canvas.create_rectangle(1, 1, size - 1, size - 1,
                                fill=PAGE_BTN_BG_HOVER if hover else PAGE_BTN_BG,
                                outline=PAGE_BTN_BORDER)
        pad = 7
        # 三行横线（表格行示意）
        for index in range(3):
            y = pad + 2 + index * 5
            canvas.create_line(pad, y, size - pad, y, fill=color, width=2)

    def _toggle_list_thumbs(self):
        """列表层：切换缩略图显示/隐藏（隐藏时全部替换为空白图，不下载原图）"""
        self._list_thumbs_visible = not getattr(self, '_list_thumbs_visible', True)
        self._draw_list_thumb_icon()
        self._render_list_items()
        self._list_status_var.set(
            "缩略图已显示" if self._list_thumbs_visible else "缩略图已隐藏（用空白图占位）")

    def _toggle_list_view_mode(self):
        """列表层：在“瀑布流缩略图卡片”与“列表（番号/标题/标志/日期）”之间切换"""
        self._list_view_mode = (
            'list' if getattr(self, '_list_view_mode', 'card') == 'card' else 'card')
        self._draw_list_view_icon()
        self._render_list_items()
        self._list_status_var.set(
            "已切换为列表显示" if self._list_view_mode == 'list' else "已切换为瀑布流缩略图显示")

    # ========== 线程安全：回到主线程执行 ==========
    def _run_in_main_thread(self, func: Callable):
        """后台线程把任务交给主线程执行（子线程直接调用 Tk 会卡死）
        注意：这里只写队列，绝不触碰 Tk；轮询器在主线程启动
        """
        self._main_tasks.put(func)

    def _start_main_task_poller(self):
        """在主线程启动任务轮询器"""
        if self._main_task_poller_running:
            return
        self._main_task_poller_running = True
        self._poll_main_tasks()

    def _poll_main_tasks(self):
        """主线程轮询执行后台提交的任务"""
        try:
            while True:
                func = self._main_tasks.get_nowait()
                try:
                    func()
                except Exception as e:
                    self._log(f"后台任务执行失败: {e}")
        except queue.Empty:
            pass
        if not self._main_task_poller_running:
            return   # 清理后不再续排，避免窗口销毁后回调报错
        self._main_task_after_id = self.frame.after(100, self._poll_main_tasks)

    # ========== 大图缓存与异步预取 ==========
    def _cache_put(self, url: str, data: bytes):
        """写入大图缓存，超出上限时按写入顺序淘汰"""
        if not url or not data:
            return
        if url in self._image_cache:
            self._image_cache_size -= len(self._image_cache[url])
        self._image_cache[url] = data
        self._image_cache_size += len(data)
        while self._image_cache_size > IMAGE_CACHE_MAX_BYTES and self._image_cache:
            oldest = next(iter(self._image_cache))
            self._image_cache_size -= len(self._image_cache.pop(oldest))

    def _start_prefetch(self, image_info: Dict):
        """异步把大图读进缓存，点击时无需再等待下载"""
        url = image_info.get('url', '')
        if not url or not self.worker:
            return
        if url in self._image_cache or url in self._prefetching:
            return

        self._prefetching.add(url)

        def load():
            try:
                data = self._fetch_image_cached(url)
                if data:
                    self._run_in_main_thread(lambda: self._cache_put(url, data))
            except Exception:
                pass
            finally:
                self._prefetching.discard(url)

        self._image_pool.submit(load)

    def _prefetch_page_thumbs(self, images: List[Dict]):
        """抓取完成后把图片预读进缓存（只下载不渲染）；页签切换时直接复用，无需再次联网"""
        for item in images:
            for key in ('thumb', 'url'):
                u = item.get(key)
                if not u or u in self._image_cache or u in self._prefetching:
                    continue
                self._prefetching.add(u)

                def load(url=u):
                    try:
                        data = self._fetch_image_cached(url)
                        if data:
                            self._run_in_main_thread(lambda: self._cache_put(url, data))
                    except Exception:
                        pass
                    finally:
                        self._prefetching.discard(url)

                self._image_pool.submit(load)

    # ========== 图片浏览浮窗层（可左右切换） ==========
    def _is_viewer_alive(self) -> bool:
        """浏览层窗口是否还活着"""
        try:
            return self._viewer_win is not None and bool(self._viewer_win.winfo_exists())
        except Exception:
            return False

    def _show_original_image(self, image_info: Dict):
        """点击缩略图：打开浏览层；已打开时直接切到该张"""
        if not self.current_images:
            return

        index = 0
        for i, item in enumerate(self.current_images):
            if item is image_info or item.get('url') == image_info.get('url'):
                index = i
                break

        if self._is_viewer_alive():
            self._viewer_index = index
            self._render_viewer_image()
        else:
            self._create_image_viewer(index)

    def _create_image_viewer(self, index: int):
        """创建铺满主窗口的图片浏览层"""
        root = self.frame.winfo_toplevel()
        win = tk.Toplevel(root)
        win.overrideredirect(True)               # 无边框浮窗层
        win.configure(background=VIEWER_BG)
        try:
            win.attributes('-topmost', True)
        except Exception:
            pass

        try:
            root.update_idletasks()
            width = max(900, root.winfo_width())
            height = max(640, root.winfo_height())
            pos_x, pos_y = root.winfo_rootx(), root.winfo_rooty()
        except Exception:
            width, height, pos_x, pos_y = 1000, 700, 0, 0
        win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

        # 先贴底摆放信息栏，再让画布占满剩余空间：
        # 这样“标题 / 演员 / 图片信息”始终位于图片【下方】，不会压在图片上遮挡画面
        info_bar = tk.Frame(win, background=VIEWER_INFO_BG)
        info_bar.pack(side=tk.BOTTOM, fill=tk.X)
        info_bar.columnconfigure(0, weight=1)

        self._viewer_info_vars = {
            'line1': tk.StringVar(),   # 序号 + 标题
            'line2': tk.StringVar(),   # 演员
            'line3': tk.StringVar(),   # 图片信息 + 缩放比例 + 操作提示
        }
        self._viewer_info_labels = {}
        for row, (key, fg, size) in enumerate((('line1', VIEWER_INFO_FG, 10),
                                              ('line2', VIEWER_INFO_FG_DIM, 9),
                                              ('line3', VIEWER_INFO_FG_DIM, 9))):
            label = tk.Label(info_bar, textvariable=self._viewer_info_vars[key],
                             background=VIEWER_INFO_BG, foreground=fg,
                             font=('Microsoft YaHei', size),
                             anchor=tk.W, justify=tk.LEFT, wraplength=max(200, width - 24))
            label.grid(row=row, column=0, sticky='w', padx=12,
                       pady=(6, 0) if row == 0 else (0, 6 if row == 2 else 0))
            self._viewer_info_labels[key] = label

        def _sync_info_wrap(_event=None):
            """窗口宽度变化时同步信息栏自动换行宽度"""
            try:
                wrap = max(200, info_bar.winfo_width() - 24)
                for label in self._viewer_info_labels.values():
                    label.configure(wraplength=wrap)
            except Exception:
                pass

        info_bar.bind('<Configure>', _sync_info_wrap)

        canvas = tk.Canvas(win, background=VIEWER_BG, highlightthickness=0)
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._viewer_win = win
        self._viewer_canvas = canvas
        self._viewer_index = index
        self._viewer_photo = None
        self._viewer_token = 0
        self._viewer_pil = None
        self._viewer_zoom = 1.0
        self._viewer_pan_x = self._viewer_pan_y = 0.0
        self._viewer_drag_from = None
        self._viewer_drag_moved = False
        self._viewer_press_tags = ()
        self._viewer_size_text = ''

        # 点击/拖动/滚轮：滚轮缩放、按住拖动移位、点击切换或关闭
        canvas.bind('<ButtonPress-1>', self._on_viewer_press)
        canvas.bind('<B1-Motion>', self._on_viewer_drag)
        canvas.bind('<ButtonRelease-1>', self._on_viewer_release)
        canvas.bind('<MouseWheel>', self._on_viewer_wheel)
        # 部分系统/触控板只发 Button-4/5（滚轮上/下）
        canvas.bind('<Button-4>', lambda e: self._zoom_viewer(VIEWER_ZOOM_STEP, e.x, e.y))
        canvas.bind('<Button-5>', lambda e: self._zoom_viewer(1.0 / VIEWER_ZOOM_STEP, e.x, e.y))
        canvas.bind('<Configure>', self._on_viewer_canvas_resize)

        win.bind('<Escape>', lambda e: self._close_image_viewer())
        win.bind('<Left>', lambda e: self._viewer_step(-1))
        win.bind('<Right>', lambda e: self._viewer_step(1))
        win.focus_force()

        self._render_viewer_image()

    def _close_image_viewer(self, event=None):
        """关闭图片浏览层"""
        try:
            if self._viewer_win is not None:
                self._viewer_win.destroy()
        except Exception:
            pass
        self._viewer_win = None
        self._viewer_canvas = None
        self._viewer_photo = None
        self._viewer_pil = None
        self._viewer_zoom = 1.0
        self._viewer_pan_x = self._viewer_pan_y = 0.0
        self._viewer_drag_from = None
        self._viewer_drag_moved = False
        self._viewer_press_tags = ()
        self._viewer_size_text = ''
        self._viewer_info_vars = {}
        self._viewer_info_labels = {}

    def _viewer_step(self, delta: int):
        """切换上一张 / 下一张（不关闭窗口）"""
        if not self._is_viewer_alive() or not self.current_images:
            return
        total = len(self.current_images)
        self._viewer_index = (self._viewer_index + delta) % total
        self._render_viewer_image()

    # ---- 点击 / 拖动 / 缩放 ----
    def _on_viewer_press(self, event):
        """按下左键：记录起点，用于区分“点击”与“拖动图片移位”"""
        canvas = self._viewer_canvas
        if canvas is None:
            return
        items = canvas.find_withtag('current')
        self._viewer_press_tags = canvas.gettags(items[0]) if items else ()
        self._viewer_drag_from = (event.x, event.y)
        self._viewer_drag_pan = (self._viewer_pan_x, self._viewer_pan_y)
        self._viewer_drag_moved = False

    def _on_viewer_drag(self, event):
        """按住左键移动：拖动图片移位（放大后看局部）"""
        if self._viewer_drag_from is None or self._viewer_pil is None:
            return
        if any(tag in self._viewer_press_tags for tag in ('prev', 'next')):
            return                     # 从切换按钮上开始拖 → 不移动图片
        dx = event.x - self._viewer_drag_from[0]
        dy = event.y - self._viewer_drag_from[1]
        if not self._viewer_drag_moved and abs(dx) + abs(dy) < 4:
            return                     # 轻微抖动不算拖动，保留点击语义
        self._viewer_drag_moved = True
        self._viewer_pan_x = self._viewer_drag_pan[0] + dx
        self._viewer_pan_y = self._viewer_drag_pan[1] + dy
        self._clamp_viewer_pan()
        self._draw_viewer_photo()
        try:
            self._viewer_canvas.configure(cursor='fleur')
        except tk.TclError:
            pass

    def _on_viewer_release(self, event):
        """松开左键：未拖动过则按点击处理"""
        moved = self._viewer_drag_moved
        self._viewer_drag_from = None
        self._viewer_drag_moved = False
        try:
            self._viewer_canvas.configure(cursor='arrow')
        except (tk.TclError, AttributeError):
            pass
        if not moved:
            self._on_viewer_click(event)
        self._viewer_press_tags = ()

    def _on_viewer_click(self, event):
        """点击（未拖动）：箭头切换；点图片本身不关闭（可通过拖动浏览）；点空白处关闭"""
        tags = self._viewer_press_tags or ()
        if 'prev' in tags:
            self._viewer_step(-1)
        elif 'next' in tags:
            self._viewer_step(1)
        elif 'photo' in tags:
            return
        else:
            self._close_image_viewer()

    def _on_viewer_wheel(self, event):
        """鼠标滚轮：向上放大、向下缩小（以鼠标位置为锚点）"""
        delta = getattr(event, 'delta', 0) or 0
        if not delta:
            return 'break'
        steps = delta / 120.0          # Windows 每格 120；高精度滚轮会更小
        self._zoom_viewer(VIEWER_ZOOM_STEP ** steps, event.x, event.y)
        return 'break'

    def _zoom_viewer(self, factor: float, mx: Optional[int] = None,
                     my: Optional[int] = None):
        """缩放当前图片；factor>1 放大。以 (mx,my) 为锚点（默认窗口中心）"""
        if self._viewer_pil is None or not self._is_viewer_alive():
            return
        old_zoom = self._viewer_zoom
        new_zoom = max(VIEWER_ZOOM_MIN, min(VIEWER_ZOOM_MAX, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 1e-6:
            return

        canvas_w, canvas_h, fit = self._viewer_image_area()
        orig_w, orig_h = self._viewer_pil.size
        old_scale, new_scale = fit * old_zoom, fit * new_zoom
        if mx is None or my is None:
            mx, my = canvas_w // 2, canvas_h // 2

        # 保持鼠标指向的图片位置不动：先求出该点对应的原图坐标，再反算新的平移量
        pos_x = (canvas_w - orig_w * old_scale) / 2.0 + self._viewer_pan_x
        pos_y = (canvas_h - orig_h * old_scale) / 2.0 + self._viewer_pan_y
        src_x = (mx - pos_x) / old_scale if old_scale else 0.0
        src_y = (my - pos_y) / old_scale if old_scale else 0.0
        self._viewer_pan_x = mx - src_x * new_scale - (canvas_w - orig_w * new_scale) / 2.0
        self._viewer_pan_y = my - src_y * new_scale - (canvas_h - orig_h * new_scale) / 2.0

        self._viewer_zoom = new_zoom
        self._clamp_viewer_pan()
        self._draw_viewer_photo()
        self._set_viewer_info()

    def _clamp_viewer_pan(self):
        """限制平移范围：图片小于可视区时保持居中，大于可视区时最多拖到图片边缘对齐"""
        base = self._viewer_pil
        if base is None:
            self._viewer_pan_x = self._viewer_pan_y = 0.0
            return
        canvas_w, canvas_h, fit = self._viewer_image_area()
        orig_w, orig_h = base.size
        scale = fit * self._viewer_zoom
        allow_x = max(0.0, (orig_w * scale - canvas_w) / 2.0)
        allow_y = max(0.0, (orig_h * scale - canvas_h) / 2.0)
        self._viewer_pan_x = max(-allow_x, min(allow_x, self._viewer_pan_x))
        self._viewer_pan_y = max(-allow_y, min(allow_y, self._viewer_pan_y))

    def _on_viewer_canvas_resize(self, event=None):
        """画布尺寸变化：重绘图片与箭头（重新按新尺寸适应窗口）"""
        if self._viewer_pil is not None:
            self._draw_viewer_scene()
        else:
            self._draw_viewer_arrows()

    def _render_viewer_image(self):
        """渲染当前浏览的图片（缓存命中则立即显示，否则后台下载）"""
        if not self._is_viewer_alive() or not self.current_images:
            return

        self._viewer_win.update_idletasks()
        self._viewer_index = self._viewer_index % len(self.current_images)
        info = self.current_images[self._viewer_index]
        canvas = self._viewer_canvas
        canvas.delete('all')
        self._viewer_photo = None

        # 换图时复位缩放与平移，并按新图刷新信息栏
        self._viewer_pil = None
        self._viewer_zoom = 1.0
        self._viewer_pan_x = self._viewer_pan_y = 0.0
        self._viewer_size_text = ''
        self._set_viewer_info(info)

        cached = self._image_cache.get(info.get('url', ''))
        if cached:
            self._draw_viewer_image(info, cached)
            return

        width = canvas.winfo_width() or 900
        height = canvas.winfo_height() or 640
        canvas.create_text(width // 2, height // 2, text="加载中...",
                           fill='#cccccc', font=('Microsoft YaHei', 12))

        self._viewer_token += 1
        token = self._viewer_token
        threading.Thread(target=self._load_viewer_image, args=(info, token), daemon=True).start()

    def _load_viewer_image(self, image_info: Dict, token: int):
        """后台下载当前浏览的大图"""
        url = image_info.get('url', '')
        data = self.worker.download_image(url) if self.worker else None

        def draw():
            if token != self._viewer_token or not self._is_viewer_alive():
                return
            if not data:
                self._log(f"原图下载失败: {url}")
                canvas = self._viewer_canvas
                canvas.delete('all')
                canvas.create_text((canvas.winfo_width() or 900) // 2,
                                   (canvas.winfo_height() or 640) // 2,
                                   text="原图下载失败，点击空白处关闭",
                                   fill='#ff8888', font=('Microsoft YaHei', 12))
                self._set_viewer_info(image_info)
                return
            self._cache_put(url, data)
            self._draw_viewer_image(image_info, data)

        self._run_in_main_thread(draw)

    def _set_viewer_info(self, info: Optional[Dict] = None):
        """刷新图片下方信息栏：序号 + 标题 / 演员 / 图片尺寸与缩放比例及操作提示"""
        variables = getattr(self, '_viewer_info_vars', None) or {}
        if not variables:
            return
        if info is None:
            info = (self.current_images[self._viewer_index]
                    if self.current_images else {})

        total = max(1, len(self.current_images))
        title = str(info.get('title') or '').strip()
        if not title:
            title = os.path.basename(str(info.get('url') or '')) or ''
        variables['line1'].set(f"{self._viewer_index + 1}/{total}　{title}"
                               if title else f"{self._viewer_index + 1}/{total}")

        # 演员栏：取当前页签抓取到的演员；没有内容时隐藏该行，避免留白
        actors = ''
        try:
            actors = self.actors_var.get().strip()
        except Exception:
            actors = ''
        label = getattr(self, '_viewer_info_labels', {}).get('line2')
        if actors:
            variables['line2'].set(f"演员：{actors}")
            if label is not None:
                try:
                    label.grid()
                except Exception:
                    pass
        else:
            variables['line2'].set('')
            if label is not None:
                try:
                    label.grid_remove()
                except Exception:
                    pass

        parts = []
        if self._viewer_size_text:
            parts.append(self._viewer_size_text)
        parts.append(f"缩放 {int(round(self._viewer_zoom * 100))}%")
        parts.append("滚轮缩放　|　按住拖动移动　|　点击空白处关闭　|　← → 切换")
        variables['line3'].set('　|　'.join(parts))

    def _viewer_image_area(self) -> Tuple[int, int, float]:
        """返回 (画布宽, 画布高, 适应窗口的基准缩放倍率)

        信息栏在画布之外（图片下方），因此画布尺寸即为可用图片区域。
        """
        canvas = self._viewer_canvas
        canvas_w = canvas.winfo_width() or 900
        canvas_h = canvas.winfo_height() or 600
        base = self._viewer_pil
        orig_w, orig_h = base.size if base is not None else (1, 1)
        avail_w = max(50, canvas_w - VIEWER_MARGIN * 2)
        avail_h = max(50, canvas_h - VIEWER_MARGIN * 2)
        fit = min(1.0, avail_w / max(orig_w, 1), avail_h / max(orig_h, 1))
        return canvas_w, canvas_h, fit

    def _draw_viewer_scene(self):
        """整幅重绘：图片 + 左右切换箭头"""
        canvas = self._viewer_canvas
        if canvas is None:
            return
        canvas.delete('all')
        self._draw_viewer_photo()
        self._draw_viewer_arrows()

    def _draw_viewer_photo(self):
        """按当前缩放与平移把图片画到画布上

        只渲染“可见区域”对应的原图部分，因此放大到很大倍率时也不会生成超大位图。
        """
        canvas = self._viewer_canvas
        base = self._viewer_pil
        if canvas is None or base is None:
            return

        canvas_w, canvas_h, fit = self._viewer_image_area()
        orig_w, orig_h = base.size
        scale = fit * self._viewer_zoom
        shown_w, shown_h = orig_w * scale, orig_h * scale

        pos_x = (canvas_w - shown_w) / 2.0 + self._viewer_pan_x
        pos_y = (canvas_h - shown_h) / 2.0 + self._viewer_pan_y
        vis_l = max(0.0, pos_x)
        vis_t = max(0.0, pos_y)
        vis_r = min(float(canvas_w), pos_x + shown_w)
        vis_b = min(float(canvas_h), pos_y + shown_h)

        try:
            canvas.delete('photo')
        except tk.TclError:
            return
        self._viewer_photo = None
        if vis_r - vis_l < 2 or vis_b - vis_t < 2 or scale <= 0:
            return                     # 图片已被完全移出可视区

        left = int(max(0, min(orig_w - 1, (vis_l - pos_x) / scale)))
        top = int(max(0, min(orig_h - 1, (vis_t - pos_y) / scale)))
        right = int(max(left + 1, min(orig_w, (vis_r - pos_x) / scale + 0.5)))
        bottom = int(max(top + 1, min(orig_h, (vis_b - pos_y) / scale + 0.5)))
        region = base.crop((left, top, right, bottom))

        target_w = max(1, int(round(vis_r - vis_l)))
        target_h = max(1, int(round(vis_b - vis_t)))
        if region.size != (target_w, target_h):
            # 放大（目标更大）时用双线性更快；缩小用 LANCZOS 保画质
            resample = (Image.Resampling.BILINEAR if target_w >= region.width
                        else Image.Resampling.LANCZOS)
            region = region.resize((target_w, target_h), resample)

        try:
            photo = ImageTk.PhotoImage(region)
        except Exception as e:
            self._log(f"渲染图片失败: {e}")
            return
        self._viewer_photo = photo
        canvas.create_image(vis_l, vis_t, anchor=tk.NW, image=photo, tags=('photo',))
        # 切换箭头始终浮在图片之上
        canvas.tag_raise('prev')
        canvas.tag_raise('next')

    def _draw_viewer_image(self, image_info: Dict, img_data: bytes):
        """解码原图并绘制（缩放系数复位为“适应窗口”）"""
        try:
            img = Image.open(io.BytesIO(img_data))
            img.load()
        except Exception as e:
            self._log(f"打开原图失败: {e}")
            return

        self._viewer_pil = img
        self._viewer_zoom = 1.0
        self._viewer_pan_x = self._viewer_pan_y = 0.0
        self._viewer_size_text = f"{img.size[0]} x {img.size[1]}"
        self._draw_viewer_scene()
        self._set_viewer_info(image_info)

    def _draw_viewer_arrows(self):
        """绘制左右切换按钮：实心三角图标 + 稍大的点击区
        位置固定在窗口左右两侧的中线处，不随图片横竖尺寸/比例变化
        """
        canvas = self._viewer_canvas
        if canvas is None:
            return
        # 先清掉旧箭头，避免画布尺寸变化时重复叠加
        try:
            canvas.delete('prev')
            canvas.delete('next')
        except tk.TclError:
            return
        if len(self.current_images) < 2:
            return

        canvas_w = canvas.winfo_width() or 900
        canvas_h = canvas.winfo_height() or 640
        center_y = canvas_h // 2                      # 固定在窗口垂直中线
        half = VIEWER_ARROW_BTN_SIZE // 2
        left_x = VIEWER_ARROW_MARGIN_X + half         # 固定在窗口左侧
        right_x = canvas_w - VIEWER_ARROW_MARGIN_X - half   # 固定在窗口右侧

        for tag, center_x, direction in (('prev', left_x, -1), ('next', right_x, 1)):
            # 点击区（稍大于三角图标）：整块区域都可点击
            canvas.create_rectangle(center_x - half, center_y - half,
                                    center_x + half, center_y + half,
                                    fill=VIEWER_ARROW_BG, outline='',
                                    tags=(tag, f'{tag}_bg'))
            # 实心三角图标（参考图样式）
            icon_w = VIEWER_ARROW_ICON_W
            icon_h = VIEWER_ARROW_ICON_H
            if direction < 0:
                points = (center_x + icon_w // 2, center_y - icon_h // 2,
                          center_x - icon_w // 2, center_y,
                          center_x + icon_w // 2, center_y + icon_h // 2)
            else:
                points = (center_x - icon_w // 2, center_y - icon_h // 2,
                          center_x + icon_w // 2, center_y,
                          center_x - icon_w // 2, center_y + icon_h // 2)
            canvas.create_polygon(points, fill=VIEWER_ARROW_COLOR, outline='',
                                  tags=(tag, f'{tag}_icon'))
            canvas.tag_bind(tag, '<Enter>',
                            lambda e, t=tag: self._hover_viewer_arrow(t, True))
            canvas.tag_bind(tag, '<Leave>',
                            lambda e, t=tag: self._hover_viewer_arrow(t, False))

    def _hover_viewer_arrow(self, tag: str, hover: bool):
        """切换箭头按钮的悬停高亮"""
        canvas = self._viewer_canvas
        if canvas is None:
            return
        try:
            canvas.configure(cursor='hand2' if hover else 'arrow')
            canvas.itemconfigure(
                f'{tag}_bg',
                fill=VIEWER_ARROW_BG_HOVER if hover else VIEWER_ARROW_BG)
            canvas.itemconfigure(
                f'{tag}_icon',
                fill=VIEWER_ARROW_COLOR_HOVER if hover else VIEWER_ARROW_COLOR)
        except tk.TclError:
            pass

    def _build_grab_config(self, render_js: Optional[bool] = None) -> WebGrabConfig:
        """按界面设置与已保存设置构造抓取配置"""
        def get(key, default):
            return self.config_manager.get(self.CONFIG_SECTION, key, default)

        return WebGrabConfig(
            user_agent=get('user_agent', USER_AGENTS[0]),
            timeout=get('timeout', 30),
            proxy_mode=get('proxy_mode', 'direct'),
            proxy_url=get('proxy_url', ''),
            browser_mode=get('browser_mode', 'chrome'),
            connection_mode=get('connection_mode', 'http'),
            max_images=self.max_images_var.get(),
            auto_download=self.auto_download_var.get(),
            save_path=self.save_path_var.get(),
            custom_headers=get('custom_headers', {}),
            cookies=get('cookies', ''),
            retry_times=get('retry_times', 3),
            random_ua=get('random_ua', True),
            interval_min=get('interval_min', 0),
            interval_max=get('interval_max', 0),
            encoding=get('encoding', 'auto'),
            main_image_selector=get('main_image_selector', DEFAULT_MAIN_IMAGE_SELECTOR),
            sample_image_selector=get('sample_image_selector', DEFAULT_SAMPLE_CONTAINER_SELECTOR),
            sample_link_selector=get('sample_link_selector', DEFAULT_SAMPLE_LINK_SELECTOR),
            magnet_table_selector=get('magnet_table_selector', DEFAULT_MAGNET_TABLE_SELECTOR),
            auto_render_on_missing=bool(get('auto_render_on_missing', True)),
            magnet_ajax_enabled=bool(get('magnet_ajax_enabled', True)),
            field_rules=self._field_rules_from_config(),
            auto_save_json=bool(get('auto_save_json', False)),
            magnet_ajax_path=get('magnet_ajax_path', DEFAULT_MAGNET_AJAX_PATH),
            magnet_ajax_params=get('magnet_ajax_params', DEFAULT_MAGNET_AJAX_PARAMS),
            render_js=get('render_js', False) if render_js is None else render_js,
            render_engine=get('render_engine', 'playwright'),
            browser_type=get('browser_type', 'chromium'),
            headless=get('headless', True),
            browser_timeout=get('browser_timeout', 30),
            wait_for_selector=get('wait_for_selector', ''),
            block_images=get('block_images', True),
        )

    # ========== 测试爬取内容（查看完整页面源代码） ==========
    def _show_source_test_window(self):
        """打开窗口，抓取并显示完整页面源代码，用于检查 JS 动态内容是否读取正确"""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请先输入或拖入网页地址")
            return
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            self.url_var.set(url)

        dialog = tk.Toplevel(self.frame)
        dialog.title(f"测试爬取内容 - {url}")
        dialog.geometry("1050x740")
        dialog.transient(self.frame.winfo_toplevel())

        # 抓取信息
        info_var = tk.StringVar(value="正在抓取，请稍候...")
        ttk.Label(dialog, textvariable=info_var, foreground="#0066cc",
                  justify=tk.LEFT, wraplength=1000).pack(fill=tk.X, padx=8, pady=(8, 4))

        # 渲染选项
        opt_frame = ttk.Frame(dialog)
        opt_frame.pack(fill=tk.X, padx=8)
        render_var = tk.BooleanVar(
            value=bool(self.config_manager.get(self.CONFIG_SECTION, 'render_js', False)))
        ttk.Checkbutton(opt_frame,
                        text="用浏览器渲染 JS 后再取源码（用于对比静态源码是否缺少动态生成的内容）",
                        variable=render_var).pack(side=tk.LEFT)

        # 源代码显示区
        text_frame = ttk.Frame(dialog)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        text_widget = scrolledtext.ScrolledText(text_frame, wrap=tk.NONE, font=("Consolas", 10))
        h_scroll = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=text_widget.xview)
        text_widget.configure(xscrollcommand=h_scroll.set)
        text_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        h_scroll.pack(side=tk.TOP, fill=tk.X)
        text_widget.tag_config('found', background='#ffe680')

        # 整页源码 / 内容A 切换显示
        state = {'full': '', 'a': ''}
        show_a_var = tk.BooleanVar(value=False)

        def render_source():
            content = state['a'] if show_a_var.get() else state['full']
            try:
                text_widget.delete('1.0', tk.END)
                text_widget.insert('1.0', content or '（暂无内容）')
                text_widget.mark_set(tk.INSERT, '1.0')
                text_widget.see('1.0')
                keyword = (search_var.get() or '').strip()
                if keyword:
                    self._highlight_keyword(text_widget, keyword, count_var)
            except tk.TclError:
                pass

        ttk.Checkbutton(opt_frame, text="只显示内容A（body > div.container）",
                        variable=show_a_var, command=render_source).pack(side=tk.LEFT, padx=(10, 0))

        # 关键字查找
        search_frame = ttk.Frame(dialog)
        search_frame.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(search_frame, text="查找关键字:").pack(side=tk.LEFT)
        search_var = tk.StringVar(value="magnet:")
        ttk.Entry(search_frame, textvariable=search_var, width=28).pack(side=tk.LEFT, padx=4)
        count_var = tk.StringVar(value="")
        ttk.Button(search_frame, text="查找并标记",
                  command=lambda: self._highlight_keyword(
                      text_widget, search_var.get(), count_var)).pack(side=tk.LEFT)
        ttk.Label(search_frame, textvariable=count_var,
                  foreground="gray").pack(side=tk.LEFT, padx=8)
        ttk.Label(search_frame, text="（如 magnet: 、<img ，用于确认动态内容是否已被读取）",
                  foreground="gray").pack(side=tk.LEFT)

        # 操作按钮
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btn_frame, text="重新抓取",
                  command=lambda: self._run_source_test(
                      url, text_widget, info_var, render_var, search_var, count_var,
                      dialog)).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="保存为HTML",
                  command=lambda: self._save_source_html(text_widget, url)).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="复制全部",
                  command=lambda: self._copy_source_html(text_widget)).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT)

        self._run_source_test(url, text_widget, info_var, render_var, search_var, count_var,
                              dialog, state, render_source)

    def _run_source_test(self, url, text_widget, info_var, render_var,
                         search_var, count_var, dialog, state=None, render_cb=None):
        """后台抓取页面源码，完成后刷新显示"""
        render = bool(render_var.get())
        info_var.set(f"正在抓取（{'浏览器渲染' if render else '静态'}）: {url}")

        def task():
            html = None
            error = ""
            worker = None
            try:
                worker = WebGrabWorker(self._build_grab_config(render_js=render),
                                       log_callback=self._log)
                html = worker.fetch_page(url)
            except Exception as e:
                error = str(e)
            meta = dict(worker.last_meta) if worker else {}
            meta.setdefault('content_a', getattr(worker, 'content_a', '') if worker else '')
            self._apply_source_result(dialog, text_widget, info_var, search_var, count_var,
                                      url, html, error, meta, state, render_cb)

        threading.Thread(target=task, daemon=True).start()

    def _apply_source_result(self, dialog, text_widget, info_var, search_var, count_var,
                             url, html, error, meta, state=None, render_cb=None):
        """在主线程中显示抓取到的源代码"""
        def update():
            try:
                if not html:
                    info_var.set(f"抓取失败: {error or meta.get('error') or '未知错误'}（详见左侧日志）")
                    text_widget.delete('1.0', tk.END)
                    text_widget.insert('1.0', "（抓取失败，没有拿到页面源代码）")
                    return

                if state is not None:
                    state['full'] = html
                    state['a'] = meta.get('content_a') or html

                if render_cb is not None:
                    render_cb()
                else:
                    text_widget.delete('1.0', tk.END)
                    text_widget.insert('1.0', html)
                    text_widget.mark_set(tk.INSERT, '1.0')
                    text_widget.see('1.0')

                stats = (
                    f"img标签 {len(re.findall(r'<img\b', html, re.I))} 个, "
                    f"a标签 {len(re.findall(r'<a\b', html, re.I))} 个, "
                    f"script {len(re.findall(r'<script\b', html, re.I))} 个, "
                    f"magnet {len(re.findall(r'magnet:\?', html, re.I))} 个"
                )
                info_var.set(
                    f"地址: {meta.get('url', url)}\n"
                    f"引擎: {meta.get('engine', '-')} | 状态码: {meta.get('status', '-')} | "
                    f"编码: {meta.get('encoding', '-')} | 字节: {meta.get('bytes', '-')} | "
                    f"字符: {meta.get('chars', '-')} | 耗时: {meta.get('elapsed', '-')} 秒 | "
                    f"抓取时间: {meta.get('fetch_time', '-')}\n"
                    f"内容统计: {stats}"
                )

                keyword = (search_var.get() or '').strip()
                if keyword:
                    self._highlight_keyword(text_widget, keyword, count_var)
            except tk.TclError:
                pass  # 窗口已关闭

        self._run_in_main_thread(update)

    @staticmethod
    def _highlight_keyword(text_widget, keyword: str, count_var=None) -> int:
        """标记源码中所有匹配关键字的位置，返回匹配数量"""
        text_widget.tag_remove('found', '1.0', tk.END)
        keyword = (keyword or '').strip()
        if not keyword:
            if count_var is not None:
                count_var.set("")
            return 0

        total = 0
        first_idx = None
        idx = '1.0'
        while total < 2000:
            idx = text_widget.search(keyword, idx, stopindex=tk.END)
            if not idx:
                break
            total += 1
            if first_idx is None:
                first_idx = idx
            end = f"{idx}+{len(keyword)}c"
            text_widget.tag_add('found', idx, end)
            idx = end

        if first_idx:
            text_widget.see(first_idx)
        if count_var is not None:
            count_var.set(f"匹配 {total} 处" if total else "无匹配")
        return total

    def _save_source_html(self, text_widget, url: str):
        """把当前源代码保存为文件"""
        content = text_widget.get('1.0', tk.END)
        if not content.strip():
            messagebox.showinfo("提示", "没有可保存的内容")
            return

        file_path = filedialog.asksaveasfilename(
            title="保存页面源代码",
            defaultextension=".html",
            initialfile="page_source.html",
            filetypes=[("网页文件", "*.html"), ("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not file_path:
            return

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            disp_path = self._format_display_path(file_path)
            self._log(f"页面源代码已保存: {disp_path}")
            messagebox.showinfo("成功", f"已保存: {disp_path}")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def _copy_source_html(self, text_widget):
        """复制当前源代码到剪贴板"""
        content = text_widget.get('1.0', tk.END)
        if not content.strip():
            messagebox.showinfo("提示", "没有可复制的内容")
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(content)
        self._log("页面源代码已复制到剪贴板")

    def _copy_text(self, text: str, name: str):
        """把文本复制到系统剪贴板（标题/系列/发行商/制作商/演员等字段复用）

        传入的 text 即当前显示内容：点过“翻译”后显示的是中文译文，
        未翻译时显示原文，因此复制与日志输出都会自动跟随当前显示状态。
        复制成功后会在日志中完整输出该字段内容。
        """
        text = (text or '').strip()
        if not text:
            self._log(f"{name}为空，无需复制")
            return
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self._log(f"已复制{name}到剪贴板：{text}")
        except Exception as e:
            self._log(f"复制到剪贴板失败: {e}")

    # ========== 创建视频文件夹（封面 + JSON） ==========
    def _create_video_folder(self):
        """在保存目录下以标题前50字为名建文件夹，保存主图为 cover.jpg 并写出 {番号}.json"""
        url = self.active_url
        if not url or url not in self.pages:
            messagebox.showwarning("提示", "请先抓取或切换到要保存的页签")
            return
        page = self.pages.get(url, {})
        record = page.get('record') or {}
        title = str(record.get('title') or page.get('title') or '').strip()
        if not title:
            messagebox.showwarning("提示", "当前页签标题为空，无法创建文件夹")
            return
        base_dir = self.save_path_var.get().strip()
        if not base_dir:
            messagebox.showwarning("提示", "请先设置“保存目录”")
            return
        # 保存目录不存在时逐级创建（如 c:\a\b\c\d 会创建缺失的各级）
        if not self._ensure_save_dir(base_dir):
            messagebox.showwarning("提示", f"保存目录无法创建：\n{base_dir}")
            return
        # 手工输入（未经过“选择”按钮）的目录，只要点击过“创建视频文件夹”
        # 也记入历史，便于之后在下拉菜单中直接选用
        self._push_save_path_history(base_dir)
        # 文件夹名：标题前50字，剔除非法字符
        folder_name = re.sub(r'[\\/:*?"<>|\r\n]+', '_', title)[:50].strip('_')
        if not folder_name:
            folder_name = 'video'
        target_dir = os.path.join(base_dir, folder_name)
        # 后台执行，避免阻塞界面
        self._image_pool.submit(lambda: self._do_create_video_folder(target_dir, page, record))
        self._log(f"正在创建视频文件夹: {self._format_display_path(target_dir)}")

    def _do_create_video_folder(self, target_dir: str, page: Dict, record: Dict):
        """后台线程：创建文件夹、保存主图与元信息 JSON
        JSON 键名与数据库字段保持一致（code/date/actors/title/duration 等）
        """
        try:
            os.makedirs(target_dir, exist_ok=True)
            self._save_cover_image(page, target_dir)

            fields = record.get('fields') or {}
            code = str(record.get('code') or '').strip()
            # 类别：名称 + 对应的标签代码（网址末段，如 f / gt）+ 类别网址
            categories = self._record_categories(record)
            data = {
                'code': code,                                              # 番号
                'title': str(record.get('title') or ''),                   # 标题
                'actors': record.get('actors') or [],                      # 演员
                'date': str(fields.get('date') or record.get('date') or ''),   # 发行日期
                'duration': record.get('duration') or 0,                   # 时长（秒）
                'series': str(fields.get('series') or record.get('series') or ''),        # 系列
                'publisher': str(fields.get('publisher') or record.get('publisher') or ''),  # 发行商
                'maker': str(fields.get('maker') or record.get('maker') or ''),           # 制作商
                'categories': [{'name': str(c.get('name') or ''),          # 类别（名 + 代码 + 网址）
                                'code': str(c.get('code') or ''),
                                # 网址只保留路径部分（如 /genre/i5），去掉站点前缀，
                                # 站点失效后可用“正常站点 + 该路径”拼接访问
                                'url': self._category_path_only(c.get('url'))}
                               for c in categories],
                'category_names': [str(c.get('name') or '') for c in categories],   # 类别名列表
                'category_codes': [str(c.get('code') or '') for c in categories
                                   if str(c.get('code') or '')],          # 类别标签代码列表
            }
            json_name = re.sub(r'[\\/:*?"<>|\s]+', '_', (code or 'unknown')) + '.json'
            json_path = os.path.join(target_dir, json_name)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"已写入元信息 JSON: {os.path.basename(json_path)}")
            # 把新建文件夹（完整路径 + 视频信息）写入本地检索索引，避免后续重复收集
            self._add_folder_to_video_index(target_dir, data)
            self._run_in_main_thread(
                lambda: self._log(f"完成：文件夹已创建 {self._format_display_path(target_dir)}"))
        except Exception as e:
            self._log(f"创建视频文件夹失败: {e}")

    def _save_cover_image(self, page: Dict, target_dir: str):
        """把当前页签主图（首图）保存为 cover.jpg（非 jpg 自动转 jpg）"""
        images = page.get('images') or []
        if not images:
            self._log("当前页签没有图片，跳过主图保存")
            return
        first = images[0]
        url = first.get('url') or first.get('thumb') or ''
        if not url:
            self._log("未找到主图地址，跳过主图保存")
            return
        # 优先用内存/磁盘缓存（与图片缓存加速切换一致），否则回退联网下载
        data = self._fetch_image_cached(url) if hasattr(self, '_fetch_image_cached') else None
        if not data:
            self._log("主图获取失败，无法保存 cover.jpg")
            return
        cover_path = os.path.join(target_dir, 'cover.jpg')
        try:
            img = Image.open(io.BytesIO(data))
            img = img.convert('RGB')
            img.save(cover_path, 'JPEG')
            self._log(f"主图已保存为 cover.jpg")
        except Exception as e:
            self._log(f"保存 cover.jpg 失败: {e}")

    # ========== 多网页（多链接）与页签管理 ==========
    def _on_multi_link_toggle(self):
        """多链接选择框：勾选后可累积多个网页并并发抓取"""
        if self.multi_link_var.get():
            self._log("已开启多链接模式：每拖入或粘贴一个网页即立即抓取，多个网页并发进行")
            if hasattr(self, 'drop_hint_var'):
                self.drop_hint_var.set("多链接模式：可多次拖入图片/链接\n每拖入一个即立即抓取该页面")
        else:
            self._pending_urls.clear()
            if hasattr(self, 'drop_hint_var'):
                self.drop_hint_var.set(self._default_drop_hint())
            self._log("已关闭多链接模式（恢复单网页抓取）")
        self._save_to_config_manager()

    @staticmethod
    def _normalize_url(url: str) -> str:
        """补全协议头"""
        url = (url or '').strip()
        if not url:
            return ''
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        return url

    @staticmethod
    def _tab_name(url: str) -> str:
        """页签名称：取网页地址最后一个“/”之后的部分"""
        try:
            parsed = urllib.parse.urlparse(url)
            name = (parsed.path or '').rstrip('/').split('/')[-1]
            if not name:
                name = parsed.netloc or url
            if '.' in name:
                name = name.rsplit('.', 1)[0]
            return name or url[:24]
        except Exception:
            return url[:24]

    @staticmethod
    def _page_key(url: str) -> str:
        """页面唯一标识：忽略主机，只看“路径+参数”（同一路径视为同一网页）

        如 https://www.busfan.cyou/MFCS-2 与 https://www.javbus.com/MFCS-2
        的键都是 /mfcs-2 —— 抓取失败切换入口主机后仍是同一个页面。
        """
        try:
            parts = urllib.parse.urlsplit((url or '').strip())
            path = (parts.path or '').rstrip('/').lower()
            if not path:
                return (parts.netloc or url or '').strip().lower()
            return path + (('?' + parts.query) if parts.query else '')
        except Exception:
            return (url or '').strip().lower()

    def _canonical_page_url(self, url: str) -> str:
        """把任意网址归一到已存在页签的规范网址；没有匹配页签时原样返回"""
        raw = (url or '').strip()
        if not raw or raw in self.pages:
            return raw
        known = self._page_keys.get(self._page_key(raw))
        if known and known in self.pages:
            return known
        return raw

    def _retarget_page(self, old_url: str, new_url: str):
        """把已存在页签的网址变更为新网址（同一路径、主机不同）

        迁移页签缓存、页签控件、页签顺序、当前页签及运行中标记；
        各消息/回调入口都会先经 _canonical_page_url 归一，因此旧闭包中
        捕获的旧网址仍能正确路由到本页签。
        """
        old_url = (old_url or '').strip()
        new_url = (new_url or '').strip()
        if (not new_url or new_url == old_url
                or old_url not in self.pages or new_url in self.pages):
            return
        page = self.pages.pop(old_url)
        self.pages[new_url] = page
        record = self.page_records.pop(old_url, None)
        if record is not None:
            self.page_records[new_url] = record
        if old_url in self.page_order:
            self.page_order[self.page_order.index(old_url)] = new_url
        widgets = self._tab_widgets.pop(old_url, None)
        if widgets is not None:
            self._tab_widgets[new_url] = widgets
        if old_url in self._running_urls:
            self._running_urls.discard(old_url)
            self._running_urls.add(new_url)
        if old_url in self._host_retried:
            self._host_retried.discard(old_url)
            self._host_retried.add(new_url)
        if self._pending_urls and old_url in self._pending_urls:
            self._pending_urls[self._pending_urls.index(old_url)] = new_url
        if self.active_url == old_url:
            self.active_url = new_url
        self._page_keys[self._page_key(new_url)] = new_url
        self._update_tab_state(new_url)
        self._log(f"页签网址已变更: {self._tab_name(new_url)} -> {new_url}")

    def _enqueue_url(self, url: str, activate: bool = True):
        """建立页签并立即抓取对应页面（支持并发抓取多个网页）
        activate=False 时不切换当前页签（批量抓取时避免反复切换）
        """
        url = self._normalize_url(url)
        if not url:
            return
        # 同一路径、不同主机的变体网址（如入口切换后手动换域名重抓）：
        # 复用已有页签并把其网址变更为本次请求的网址，不再新建页签
        existing = self._canonical_page_url(url)
        if existing != url and existing in self.pages:
            self._retarget_page(existing, url)
        page = self.pages.get(url)
        if page is not None and page.get('status') == 'running':
            self._log(f"该地址正在抓取中: {url}")
            return
        self._ensure_page(url)
        self._start_single_grab(url, activate=activate)
        self._log(f"已加入并立即抓取（多链接）: {url}")

    def _ensure_page(self, url: str):
        """确保 URL 对应的页签与缓存存在（同路径变体网址归并到同一页签）"""
        url = self._canonical_page_url(url)
        if url in self.pages:
            return
        self.pages[url] = {
            'images': [], 'magnets': [], 'record': {}, 'title': '', 'actors': [],
            'log': [], 'progress': 0, 'progress_msg': '等待抓取', 'status': 'pending',
        }
        self._page_keys[self._page_key(url)] = url
        self.page_order.append(url)
        self._add_tab(url)
        if self.active_url is None or self.active_url not in self.pages:
            self._switch_page(url)

    def _log_page(self, url: str, message: str):
        """带页签前缀的日志（线程安全），同时写入该页缓存"""
        url = self._canonical_page_url(url)
        try:
            page = self.pages.get(url)
            if page is not None:
                page.setdefault('log', []).append(message)
        except Exception:
            pass
        self._log(f"[{self._tab_name(url)}] {message}")

    # ---------- 页签栏 ----------
    def _create_page_tabs(self, parent):
        """多网页页签栏（位于“抓取到的图片”上方）"""
        outer = tk.Frame(parent, height=32)
        outer.pack(fill=tk.X, padx=5, pady=(5, 0))
        outer.pack_propagate(False)

        self._tabs_canvas = tk.Canvas(outer, height=30, highlightthickness=0,
                                      background='#f5f5f5')
        self._tabs_canvas.pack(fill=tk.BOTH, expand=True)

        self._tabs_inner = tk.Frame(self._tabs_canvas, background='#f5f5f5')
        self._tabs_window = self._tabs_canvas.create_window((0, 0), window=self._tabs_inner,
                                                            anchor=tk.NW)
        self._tabs_inner.bind(
            '<Configure>',
            lambda e: self._tabs_canvas.configure(scrollregion=self._tabs_canvas.bbox('all')))
        # 页签过多时用鼠标滚轮横向滚动页签栏
        # 注意：鼠标通常停在页签（子控件）上，只绑画布收不到事件，
        # 因此画布 / 内框 / 每个页签控件都要绑定滚轮事件（见 _add_tab）
        self._bind_tabs_wheel(self._tabs_canvas)
        self._bind_tabs_wheel(self._tabs_inner)

    def _bind_tabs_wheel(self, widget):
        """给页签栏控件绑定鼠标滚轮（横向滚动页签栏）"""
        try:
            widget.bind('<MouseWheel>', self._on_tabs_wheel, add='+')
            # 部分系统/触控板只发 Button-4/5（上/下）
            widget.bind('<Button-4>', lambda e: self._scroll_tabs(-TAB_WHEEL_STEP_UNITS), add='+')
            widget.bind('<Button-5>', lambda e: self._scroll_tabs(TAB_WHEEL_STEP_UNITS), add='+')
        except tk.TclError:
            pass

    def _on_tabs_wheel(self, event):
        """滚轮上下滚动 → 页签栏左右滑动
        滚轮向下：页签栏向右移动（露出后面的页签）；滚轮向上：向左回到前面的页签
        """
        delta = getattr(event, 'delta', 0) or 0
        if not delta:
            return 'break'
        units = int(-delta / 120) or (-1 if delta > 0 else 1)
        self._scroll_tabs(units)
        return 'break'   # 阻止事件继续上传给其它控件

    def _scroll_tabs(self, units: int):
        """横向滚动页签栏（units>0 视图右移、<0 视图左移），到达两端则忽略"""
        canvas = getattr(self, '_tabs_canvas', None)
        if canvas is None:
            return
        try:
            x0, x1 = canvas.xview()
        except tk.TclError:
            return
        if units > 0 and x1 >= 1.0:      # 已在最右端
            return
        if units < 0 and x0 <= 0.0:      # 已在最左端
            return
        try:
            canvas.xview_scroll(units, 'units')
        except tk.TclError:
            pass

    def _ensure_tab_visible(self, url: str):
        """把指定页签滚动到可视区域内（新增/切换页签时调用）"""
        widgets = self._tab_widgets.get(url)
        canvas = getattr(self, '_tabs_canvas', None)
        if not widgets or canvas is None:
            return
        try:
            self._tabs_inner.update_idletasks()
            frame = widgets['frame']
            total = self._tabs_inner.winfo_reqwidth()
            view_w = canvas.winfo_width()
            if total <= 0 or view_w <= 0 or total <= view_w:
                return
            left = frame.winfo_x()
            right = left + frame.winfo_width()
            x0, x1 = canvas.xview()
            if left < x0 * total:
                canvas.xview_moveto(max(0.0, left / total))
            elif right > x1 * total:
                canvas.xview_moveto(min(1.0, max(0.0, (right - view_w) / total)))
        except (tk.TclError, ZeroDivisionError):
            pass

    def _add_tab(self, url: str):
        """新增一个页签（名称 + 关闭按钮）"""
        if url in self._tab_widgets or not hasattr(self, '_tabs_inner'):
            return

        frame = tk.Frame(self._tabs_inner, bd=1, relief=tk.SOLID, background='#e8e8e8')
        frame.pack(side=tk.LEFT, padx=2, pady=2)

        label = tk.Label(frame, text=self._tab_name(url), padx=8, pady=2,
                         background='#e8e8e8', foreground='#333333', cursor='hand2')
        label.pack(side=tk.LEFT)

        close = tk.Label(frame, text='×', padx=5, pady=2,
                         background='#e8e8e8', foreground='#888888', cursor='hand2')
        close.pack(side=tk.LEFT)

        for widget in (frame, label):
            widget.bind('<Button-1>', lambda e, u=url: self._switch_page(u))
        # 右键页签：弹出刷新等操作菜单
        for widget in (frame, label, close):
            widget.bind('<Button-3>', lambda e, u=url: self._show_tab_menu(u, e))
        close.bind('<Button-1>', lambda e, u=url: self._close_page(u))
        close.bind('<Enter>', lambda e, w=close: w.configure(foreground='#cc0000'))
        close.bind('<Leave>', lambda e, w=close: w.configure(foreground='#888888'))
        for widget in (frame, label, close):
            self._bind_tabs_wheel(widget)   # 鼠标停在页签上时同样能滚动页签栏

        self._tab_widgets[url] = {'frame': frame, 'label': label, 'close': close}
        self._update_tab_state(url)
        self._highlight_tab(self.active_url)
        self._ensure_tab_visible(url)       # 新增页签自动滚到可见位置

    def _highlight_tab(self, active_url: Optional[str]):
        """高亮当前页签"""
        for url, widgets in self._tab_widgets.items():
            active = (url == active_url)
            bg = '#4a90d9' if active else '#e8e8e8'
            fg = '#ffffff' if active else '#333333'
            try:
                widgets['frame'].configure(background=bg)
                widgets['label'].configure(background=bg, foreground=fg)
                widgets['close'].configure(background=bg)
            except tk.TclError:
                pass
        if active_url:
            self._ensure_tab_visible(active_url)

    def _update_tab_state(self, url: str):
        """按页面状态刷新页签文字（运行中 …  / 失败 !）"""
        url = self._canonical_page_url(url)
        widgets = self._tab_widgets.get(url)
        if not widgets:
            return
        status = self.pages.get(url, {}).get('status')
        name = self._tab_name(url)
        if status == 'running':
            name += ' …'
        elif status == 'failed':
            name += ' !'
        try:
            widgets['label'].configure(text=name)
        except tk.TclError:
            pass

    def _show_tab_menu(self, url: str, event=None):
        """页签右键菜单：对当前页签对应的网页重新抓取（刷新）"""
        url = self._canonical_page_url(url)
        if url not in self.pages or event is None:
            return
        menu = tk.Menu(self._tabs_inner, tearoff=0)
        if self.pages[url].get('status') == 'running':
            menu.add_command(label="正在抓取中…", state=tk.DISABLED)
        else:
            menu.add_command(label="刷新（重新抓取）",
                             command=lambda u=url: self._refresh_page(u))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _refresh_page(self, url: str):
        """右键刷新：清空该页签已抓取的数据（图片/磁力链/字段等）并重新抓取"""
        url = self._canonical_page_url(url)
        page = self.pages.get(url)
        if page is None:
            return
        if page.get('status') == 'running':
            self._log(f"该地址正在抓取中，无需刷新: {url}")
            return
        self._log(f"刷新页签（重新抓取）: {self._tab_name(url)}")
        # _start_single_grab 会先清空该页签缓存再启动新的抓取任务，
        # 并切换到该页签，图片/磁力链/字段等数据全部重新获取
        self._start_single_grab(url, activate=True)

    # ---------- 字段链接：点击后在“视频列表页”层新建页签打开 ----------
    FIELD_LINK_LABELS = {
        'series': '系列', 'actors': '演员',
        'publisher': '发行商', 'maker': '制作商',
    }

    def _field_links_of(self, field_id: str) -> List[Dict[str, str]]:
        """当前页签指定字段的链接列表 [{'name','url'}, ...]"""
        if not field_id:
            return []
        url = self.active_url or ''
        record = (self.pages.get(url) or {}).get('record') or self.page_records.get(url) or {}
        links = record.get('field_links') or {}
        value = links.get(field_id) or []
        return [x for x in value if isinstance(x, dict) and str(x.get('name') or '')]

    def _show_actors_menu(self):
        """弹出演员下拉菜单：选中某位演员即在“视频列表页”层新建页签打开其作品列表"""
        entries = self._field_links_of('actors')
        names: List[str] = []
        links: Dict[str, str] = {}
        for entry in entries:
            name = str(entry.get('name') or '').strip()
            if name:
                names.append(name)
                links[name] = str(entry.get('url') or '')
        if not names:
            # 没有抓到链接时，用文本框里的演员名兜底（可复制，无链接）
            names = [n.strip() for n in re.split(r'[、,，;；]', self.actors_var.get() or '')
                     if n.strip()]

        menu = tk.Menu(self.frame.winfo_toplevel(), tearoff=0)
        if not names:
            menu.add_command(label="（当前页签没有演员信息）", state=tk.DISABLED)
        for name in names:
            url = links.get(name, '')
            if url:
                menu.add_command(
                    label=f"{name}　→　打开作品列表",
                    command=lambda n=name, u=url: self._open_field_link(
                        'actors', {'name': n, 'url': u}))
            else:
                menu.add_command(
                    label=f"{name}　（未抓到链接）",
                    command=lambda n=name: self._copy_text(n, "演员"))

        btn = getattr(self, 'actors_menu_btn', None)
        try:
            if btn is not None and btn.winfo_exists():
                pos_x = btn.winfo_rootx()
                pos_y = btn.winfo_rooty() + btn.winfo_height()
            else:
                pos_x = self.frame.winfo_rootx() + 60
                pos_y = self.frame.winfo_rooty() + 200
        except Exception:
            pos_x, pos_y = 100, 100
        try:
            menu.tk_popup(pos_x, pos_y)
        finally:
            menu.grab_release()

    def _open_field_link(self, field_id: str, entry: Dict):
        """在“视频列表页”层新建页签并打开该字段对应的列表页"""
        url = str((entry or {}).get('url') or '').strip()
        name = str((entry or {}).get('name') or '').strip()
        if not url:
            self._log(f"该字段没有对应的链接：{name or field_id}")
            return
        prefix = self.FIELD_LINK_LABELS.get(field_id, field_id)
        self._log(f"[{prefix}] {name} → 在视频列表页新建页签打开: {url}")
        self._list_tab_open_url(url, name=(f"{prefix}-{name}" if name else prefix))

    def _add_meta_row(self, parent, name: str, var: tk.StringVar, copy_name: str,
                      field_id: Optional[str] = None):
        """构建一行“字段名 + 值”的元数据显示

        左键点击字段名（如“系列:”）：把字段内容复制到系统剪贴板。
        左键点击字段内容：该字段抓到了对应链接时，在“视频列表页”层新建页签打开
                         该链接；没有链接时退化为“复制到剪贴板”。
        右键点击任意位置：始终复制到剪贴板。
        """
        lbl = ttk.Label(parent, text=name, foreground="#0066cc", cursor="hand2")
        lbl.pack(side=tk.LEFT, padx=(0, 2))
        # 固定单行纯文本显示：不设 wraplength（避免按空格自动折行），
        # width=1 让标签不撑大所在行，超长内容在右边界自动裁切
        value_label = ttk.Label(parent, textvariable=var, width=1, anchor=tk.W,
                                justify=tk.LEFT, cursor="hand2")
        value_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        def on_left(event=None):
            entry = None
            if field_id:
                for item in self._field_links_of(field_id):
                    if str(item.get('url') or ''):
                        entry = item
                        break
            if entry:
                self._open_field_link(field_id, entry)
            else:
                self._copy_text(var.get(), copy_name)

        # 字段名（标题）左键：复制内容到剪贴板；字段内容左键：打开对应网页
        lbl.bind('<Button-1>', lambda e, v=var: self._copy_text(v.get(), copy_name))
        value_label.bind('<Button-1>', lambda e: on_left())
        for widget in (lbl, value_label):
            widget.bind('<Button-3>', lambda e, v=var: self._copy_text(v.get(), copy_name))

    # ---------- 类别（類別）行 ----------
    @staticmethod
    def _genre_code_of(url: str) -> str:
        """从类别网址取标签代码（.../genre/f -> f）"""
        path = urllib.parse.urlsplit(str(url or '')).path.rstrip('/')
        return path.rsplit('/', 1)[-1] if '/' in path or path else ''

    @staticmethod
    def _category_path_only(url: str) -> str:
        """把类别网址收敛为“仅路径”形式（去掉站点部分）

        例如 https://www.busfan.cyou/genre/i5 -> /genre/i5
        这样写进视频文件夹 JSON 的类别网址不再绑定具体站点；
        后续若站点失效，可用“正常站点 + 该路径”重新拼接访问。
        """
        url = str(url or '').strip()
        if not url:
            return ''
        try:
            parts = urllib.parse.urlsplit(url)
        except Exception:
            return url
        path = parts.path or ''
        # 查询串（? 之后）一并保留，便于拼接后仍指向同一类目
        if parts.query:
            path = f"{path}?{parts.query}"
        return path

    def _record_categories(self, record: Optional[Dict] = None) -> List[Dict[str, str]]:
        """当前页签的类别列表 [{'name','code','url'}, ...]

        优先用详情页精确抓取的结果（record['categories']）；取不到时依次用
        field_links / fields 里的类别名兜底（代码与链接尽量补齐）。
        """
        if not isinstance(record, dict) or not record:
            url = self.active_url or ''
            record = ((self.pages.get(url) or {}).get('record')
                      or self.page_records.get(url) or {})

        entries: List[Dict[str, str]] = []
        for item in (record.get('categories') or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            if not name:
                continue
            url = str(item.get('url') or '').strip()
            code = str(item.get('code') or '').strip() or self._genre_code_of(url)
            entries.append({'name': name, 'url': url, 'code': code})
        if entries:
            return entries

        # 兜底 1：字段链接（名称 + 网址）
        for item in ((record.get('field_links') or {}).get('categories') or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            if not name:
                continue
            url = str(item.get('url') or '').strip()
            entries.append({'name': name, 'url': url, 'code': self._genre_code_of(url)})
        if entries:
            return entries

        # 兜底 2：只有类别名
        names = (record.get('fields') or {}).get('categories') or []
        if isinstance(names, str):
            names = [names]
        return [{'name': str(n).strip(), 'url': '', 'code': ''}
                for n in names if str(n).strip()]

    def _refresh_genre_row(self):
        """按当前页签的类别重建“类别”行（每个类别一个可点击标签，自动折行）"""
        container = getattr(self, '_genre_container', None)
        if container is None:
            return
        entries = self._record_categories()
        self._genre_entries = entries
        self._genre_selection = set()
        self._genre_var.set('、'.join(str(e.get('name') or '') for e in entries))

        try:
            for child in container.winfo_children():
                child.destroy()
        except tk.TclError:
            return
        self._genre_pills = []
        self._genre_lines = []
        self._genre_last_width = 0

        if not entries:
            tk.Label(container, text="（未抓到类别）", foreground="#888888",
                     font=('Microsoft YaHei', 9)).pack(side=tk.LEFT)
            self._update_genre_controls()
            return

        bg = '#f0f0f0'
        try:
            bg = str(container.cget('background') or bg)
        except Exception:
            pass
        for index, entry in enumerate(entries):
            pill = tk.Label(container, text=str(entry.get('name') or ''),
                            background=bg, foreground="#0066cc",
                            font=('Microsoft YaHei', 9), cursor='hand2',
                            padx=4, pady=1)
            pill.bind('<Button-1>', lambda e, i=index: self._on_genre_click(i))
            pill.bind('<Button-3>',
                      lambda e, i=index: self._copy_text(
                          str(self._genre_entries[i].get('name') or ''), "类别"))
            # 这里只创建、不做任何布局：位置统一由 _reflow_genre_pills 用 place 安排。
            # 切勿再用 grid 把所有标签排成一行“占位”——grid 的列宽是全表共享的，
            # 折行后不同行的标签共用列，宽标签会把其它行的列撑宽，
            # 造成行内出现大空隙、整行实际宽度超出容器（末尾标签被裁切遮挡）。
            self._genre_pills.append(pill)

        self._reflow_genre_pills()
        self._update_genre_controls()

    def _on_genre_container_resize(self, event=None):
        """容器宽度变化时重排类别标签（宽度未变时不动作，避免无限重排）"""
        width = int(getattr(event, 'width', 0) or 0)
        if width and width == getattr(self, '_genre_last_width', 0):
            return
        self._reflow_genre_pills()

    def _reflow_genre_pills(self):
        """按容器宽度把类别标签折行排列（place 逐个定位，直接布局在容器内）

        之前用 grid 按 (行, 列) 摆放，但 grid 的列宽是全表共享的：
        某一行的宽标签（如“フルハイビジョン(FHD)”）会把其它行同列号的
        列一起撑宽，导致——
          1) 行内出现大段空白（列被别行撑宽，本行标签被顶到后面）；
          2) 整行实际渲染宽度超过贪心计算值，末尾标签被容器右边界裁切
             （看起来像被右侧“多选/确定/清空”按钮遮住）。
        改用 place 按“贪心分行的 x/y 坐标”逐个定位：每行宽度就是本行标签
        宽度之和，与计算值完全一致；容器高度用 configure(height=) 显式撑开
        （place 的子控件不参与请求尺寸传播，必须手动设置）。
        """
        container = getattr(self, '_genre_container', None)
        pills = getattr(self, '_genre_pills', None) or []
        if container is None or not pills or getattr(self, '_genre_reflowing', False):
            return
        self._genre_reflowing = True
        try:
            width = container.winfo_width()
            if width < 40:
                return
            self._genre_last_width = width

            # 贪心分行 + 累计坐标：x 超出容器宽度即换行
            x = 0          # 当前行已占用宽度
            y = 0          # 当前行顶部 y
            line_h = 0     # 当前行高度（含上下 pady）
            for pill in pills:
                try:
                    w = pill.winfo_reqwidth() + 4   # 右间距，对应原来 padx=(0, 4)
                    h = pill.winfo_reqheight() + 2  # 上下间距，对应 pady=1
                except tk.TclError:
                    continue
                if x > 0 and x + w > width:
                    x = 0
                    y += line_h
                    line_h = 0
                try:
                    pill.place(x=x, y=y + 1)
                except tk.TclError:
                    pass
                x += w
                if h > line_h:
                    line_h = h
            # place 的子控件不参与容器请求尺寸计算，行数变化后手动撑高容器，
            # 让 genre_row 随之变高（宽度仍由 pack fill=X 管理，不受影响）
            try:
                container.configure(height=y + line_h)
            except tk.TclError:
                pass
        finally:
            self._genre_reflowing = False

    def _on_genre_click(self, index: int):
        """点击类别标签

        单选模式：立即打开该类别页（再次点击同一个则取消选择）。
        多选模式：仅切换选中状态（高亮累计），不做任何打开动作；
                 全部选好后点“确定”按钮再打开组合网址。
        """
        entries = getattr(self, '_genre_entries', None) or []
        if index >= len(entries):
            return
        try:
            multi = bool(self._genre_multi_var.get())
        except Exception:
            multi = False
        if multi:
            self._genre_selection = set(self._genre_selection or set())
            if index in self._genre_selection:
                self._genre_selection.discard(index)
            else:
                self._genre_selection.add(index)
            self._update_genre_controls()
            return

        # 单选：再次点击同一个则取消选择
        self._genre_selection = set() if index in (self._genre_selection or set()) else {index}
        self._update_genre_controls()
        if not self._genre_selection:
            return
        self._open_genre_selection()

    def _genre_selected_entries(self) -> List[Dict[str, str]]:
        """当前选中的类别条目（按下标升序）"""
        entries = getattr(self, '_genre_entries', None) or []
        return [entries[i] for i in sorted(self._genre_selection or set())
                if i < len(entries)]

    def _open_genre_selection(self):
        """打开当前选中类别对应的网页（单个直接打开，多个组合成 /genre/代码-代码）"""
        selected = self._genre_selected_entries()
        if not selected:
            return
        url = self._genre_combined_url(selected)
        if not url:
            self._log("类别没有可用的网址")
            return
        names = '-'.join(str(e.get('name') or '') for e in selected)
        tab_name = f"類別-{names}"[:40]
        self._log(f"[類別] {names} → 在视频列表页新建页签打开: {url}")
        self._list_tab_open_url(url, name=tab_name)

    def _genre_confirm_selection(self):
        """多选模式“确定”按钮：把选好的类别组合成网址并打开"""
        selected = self._genre_selected_entries()
        if not selected:
            self._log("尚未选择任何类别")
            return
        self._open_genre_selection()

    def _genre_combined_url(self, entries: List[Dict[str, str]]) -> str:
        """多个类别组合成一个网址：https://站点/genre/代码-代码（如 f-gt）

        单个类别时直接用它自己的网址；取不到标签代码时退回第一个链接。
        """
        urls = [str(e.get('url') or '').strip() for e in entries if str(e.get('url') or '').strip()]
        if not urls:
            return ''
        if len(urls) == 1:
            return urls[0]
        codes = [str(e.get('code') or '').strip() or self._genre_code_of(str(e.get('url') or ''))
                 for e in entries]
        if not all(codes):
            return urls[0]
        parts = urllib.parse.urlsplit(urls[0])
        path = parts.path.rstrip('/')
        prefix = path.rsplit('/', 1)[0] if '/' in path else ''
        if not prefix:
            return urls[0]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, f"{prefix}/{'-'.join(codes)}",
             parts.query, parts.fragment))

    def _update_genre_controls(self):
        """刷新类别标签的选中高亮与“清空”按钮可用性"""
        selected = set(getattr(self, '_genre_selection', None) or set())
        bg_selected = '#cfe4ff'
        bg_normal = '#f0f0f0'
        container = getattr(self, '_genre_container', None)
        if container is not None:
            try:
                bg_normal = str(container.cget('background') or bg_normal)
            except Exception:
                pass
        for index, pill in enumerate(getattr(self, '_genre_pills', None) or []):
            try:
                pill.configure(background=bg_selected if index in selected else bg_normal)
            except tk.TclError:
                pass
        btn = getattr(self, '_genre_clear_btn', None)
        if btn is not None:
            try:
                btn.configure(state=('normal' if selected else 'disabled'))
            except tk.TclError:
                pass
        # “确定”按钮：仅多选模式且已选中至少一个类别时可用
        try:
            multi = bool(self._genre_multi_var.get())
        except Exception:
            multi = False
        ok_btn = getattr(self, '_genre_ok_btn', None)
        if ok_btn is not None:
            try:
                ok_btn.configure(state=('normal' if (multi and selected) else 'disabled'))
            except tk.TclError:
                pass

    def _genre_clear_selection(self):
        """清空类别选择（不改变已打开的页签）"""
        self._genre_selection = set()
        self._update_genre_controls()
        self._log("已清空类别选择")

    def _genre_on_multi_toggle(self):
        """切换单选/多选模式：切回单选时清空累计选择"""
        try:
            multi = bool(self._genre_multi_var.get())
        except Exception:
            multi = False
        if not multi:
            self._genre_selection = set()
            self._update_genre_controls()
        self._log("类别已切换为多选（同时满足多个类别）" if multi else "类别已切换为单选")

    def _switch_page(self, url: str):
        """切换页签：显示对应图片库与磁力链，并同步标题/演员"""
        url = self._canonical_page_url(url)
        if url not in self.pages:
            return
        self.active_url = url
        self._highlight_tab(url)
        self._reset_translation_state()   # 换页签后一律显示原文

        page = self.pages.get(url, {})
        self.page_title_var.set(_format_title_display(page.get('title', '')))
        actors = page.get('actors') or []
        self.actors_var.set('、'.join(str(a) for a in actors))
        record = page.get('record') or {}
        self.series_var.set(str(record.get('series') or ''))
        self.publisher_var.set(str(record.get('publisher') or ''))
        self.maker_var.set(str(record.get('maker') or ''))
        self._refresh_genre_row()

        self._rebuild_gallery(page.get('images', []))
        self._rebuild_magnets(page.get('magnets', []))
        self._render_page_log(page)

        self.progress_bar['value'] = page.get('progress', 0)
        self.progress_label.config(text=page.get('progress_msg', '准备就绪'))

        # 多链接模式：切换页签时同步拖放区显示，使其展示“当前页签”对应的网址，
        # 双击拖放区即可复制该页签网址（而非最后拖入的网址）
        if getattr(self, 'multi_link_var', None) is not None and self.multi_link_var.get() and url:
            if hasattr(self, 'drop_hint_var'):
                self.drop_hint_var.set(f"当前页签网址（双击可复制）:\n{url[:90]}")

    def _close_page(self, url: str):
        """关闭页签并移除其缓存"""
        url = self._canonical_page_url(url)
        widgets = self._tab_widgets.pop(url, None)
        if widgets:
            try:
                widgets['frame'].destroy()
            except tk.TclError:
                pass

        was_active = (url == self.active_url)
        self.pages.pop(url, None)
        self.page_records.pop(url, None)
        if self._page_keys.get(self._page_key(url)) == url:
            self._page_keys.pop(self._page_key(url), None)
        if url in self.page_order:
            self.page_order.remove(url)
        if url in self._pending_urls:
            self._pending_urls.remove(url)
        self._running_urls.discard(url)

        if was_active:
            self.active_url = None
            if self.page_order:
                self._switch_page(self.page_order[0])
            else:
                self._clear_display_only()
        self._highlight_tab(self.active_url)
        self._log(f"已关闭页签: {self._tab_name(url)}")

    # ---------- 从缓存重建显示 ----------
    def _rebuild_gallery(self, images: List[Dict]):
        """从缓存重建缩略图区（切换页签用，不重新抓取；遵循显示/隐藏状态）"""
        self._close_image_viewer()
        self.current_images.clear()
        self.current_images.extend(images)
        self._apply_thumb_visibility()   # 隐藏状态下不创建缩略图，仅显示提示
        for item in self.current_images:
            self._start_prefetch(item)

    def _rebuild_magnets(self, magnets: List[Dict]):
        """从缓存重建磁力链表"""
        for item in self.magnets_tree.get_children():
            self.magnets_tree.delete(item)
        self.current_magnets.clear()
        self.magnet_item_map.clear()
        for magnet in magnets:
            self._display_magnet(magnet)

    def _render_page_log(self, page: Dict):
        """把当前页签的日志显示到日志区"""
        if not hasattr(self, 'log_text'):
            return
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.delete('1.0', tk.END)
            for line in page.get('log', []):
                self.log_text.insert(tk.END, line + '\n')
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _clear_display_only(self):
        """只清空右侧显示与标题/演员（保留 pages 缓存）"""
        self._close_image_viewer()
        self.current_images.clear()
        self._apply_thumb_visibility()
        for item in self.magnets_tree.get_children():
            self.magnets_tree.delete(item)
        self.current_magnets.clear()
        self.magnet_item_map.clear()
        self.page_title_var.set('')
        self.actors_var.set('')
        self.series_var.set('')
        self.publisher_var.set('')
        self.maker_var.set('')
        if getattr(self, '_genre_var', None) is not None:
            self._genre_var.set('')
        self._genre_entries = []
        self._genre_selection = set()
        self._refresh_genre_row()
        self._reset_translation_state()
        self.progress_bar['value'] = 0
        self.progress_label.config(text='准备就绪')

    # ---------- 抓取流程 ----------
    def _start_grab(self):
        """开始抓取（单链接：替换结果；多链接：累积地址后并发抓取）"""
        entry = self.url_var.get().strip()

        # 通配符批量抓取：地址含 {#} 时按起止数字展开成多个页签
        if '{#}' in entry:
            urls = self._expand_wildcard_urls(entry)
            if urls is not None:
                self._start_batch_grab(urls)
                return

        if self.multi_link_var.get():
            self._start_multi_grab()
            return

        url = entry
        if not url:
            messagebox.showwarning("警告", "请输入或导入网页地址")
            return
        url = self._normalize_url(url)
        self.url_var.set(url)

        # 单链接：清空旧结果，重建唯一页签
        self._clear_results()
        self._start_single_grab(url, activate=True)
        self._update_ui()

    def _expand_wildcard_urls(self, pattern: str) -> Optional[List[str]]:
        """把含 {#} 的地址按起止数字展开为多个地址
        返回 None 表示不含通配符；返回 [] 表示参数不合法（已提示用户）"""
        if '{#}' not in pattern:
            return None

        start_text = self.range_start_var.get().strip() if hasattr(self, 'range_start_var') else ''
        end_text = self.range_end_var.get().strip() if hasattr(self, 'range_end_var') else ''
        if not start_text or not end_text:
            messagebox.showwarning("警告", "地址中包含 {#}，请填写起始数与结束数")
            return []
        try:
            start, end = int(start_text), int(end_text)
        except ValueError:
            messagebox.showwarning("警告", "起始数与结束数必须是整数")
            return []
        if end < start:
            start, end = end, start          # 用户写反了也照常工作
        count = end - start + 1
        if count > MAX_BATCH_PAGES:
            if not messagebox.askyesno(
                    "确认", f"将生成 {count} 个页签并并发抓取，数量较多可能导致卡顿。\n是否继续？"):
                return []

        urls = [self._normalize_url(pattern.replace('{#}', str(n)))
                for n in range(start, end + 1)]
        self._log(f"通配符展开：{pattern} → {count} 个地址（{urls[0]} ~ {urls[-1]}）")
        return urls

    def _start_batch_grab(self, urls: List[str]):
        """批量抓取（通配符展开后的多个地址）：建立多个页签并发抓取"""
        if not urls:
            return
        if not self.multi_link_var.get():
            self._clear_results()          # 非多链接模式：先清掉旧结果，避免页签混杂
        for url in urls:
            self._enqueue_url(url, activate=False)   # 逐个建立页签，不反复切换显示
        # 全部建好后切回第一个页签，显示从头开始
        if urls[0] in self.pages:
            self._switch_page(urls[0])
        self._update_ui()

    def _start_multi_grab(self):
        """多链接：把输入框地址加入并立即抓取；点击“抓取”按钮则重试尚未成功/失败的页签"""
        entry = self.url_var.get().strip()
        if entry:
            self._enqueue_url(entry)   # 立即抓取输入框地址
            self.url_var.set('')

        # 重新抓取所有尚未成功/待抓取的页签（如失败重试）
        targets = [u for u in self.page_order
                   if self.pages.get(u, {}).get('status') in (None, 'pending', 'failed')]
        if not targets and not entry:
            messagebox.showwarning("警告", "多链接模式：请先拖入或粘贴要抓取的网页地址")
            return

        for url in targets:
            self._start_single_grab(url)
        self._update_ui()

    def _start_single_grab(self, url: str, activate: bool = False):
        """按 URL 启动一个抓取任务（结果按 URL 路由回对应页签）"""
        url = self._canonical_page_url(url)
        page = self.pages.get(url)
        if page is not None and page.get('status') == 'running':
            return

        config = self._build_grab_config()

        self._ensure_page(url)
        page = self.pages.get(url)
        if page is not None:
            page['status'] = 'running'
            page['images'] = []
            page['magnets'] = []
            page['record'] = {}
            page['log'] = []
            page['progress'] = 0
            page['progress_msg'] = '正在初始化...'
        self._update_tab_state(url)
        # 立即切到该页签并刷新显示，保证拖入/粘贴的链接实时同步字段、图片与磁力链
        if activate:
            self._switch_page(url)

        def on_progress(progress, message, u=url):
            self.operation_queue.put(('progress', u, progress, message))

        def on_complete(success, message, images, magnets, u=url):
            self.operation_queue.put(('complete', u, success, message, images, magnets))

        def on_image(info, u=url):
            self.operation_queue.put(('image', u, info))

        def on_magnet(info, u=url):
            self.operation_queue.put(('magnet', u, info))

        def on_record(record, u=url):
            self.operation_queue.put(('record', u, record))

        worker = WebGrabWorker(
            config=config,
            log_callback=lambda message, u=url: self._log_page(u, message),
            progress_callback=on_progress,
            complete_callback=on_complete,
            image_found_callback=on_image,
            magnet_found_callback=on_magnet,
            record_callback=on_record,
        )
        self.workers.append(worker)
        self.worker = worker   # 保留最近的工作实例供缩略图/大图下载使用

        self._running_urls.add(url)
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        self._log(f"开始抓取: {url}")
        threading.Thread(target=self._run_worker, args=(worker, url), daemon=True).start()

        # 确保 UI 刷新循环已启动：多链接“拖入”走 _enqueue_url 不会经 _start_grab/
        # _start_multi_grab，必须在此自行启动轮询，否则消息队列不会被消费、显示不同步
        self._update_ui()

    def _run_worker(self, worker: 'WebGrabWorker', url: str):
        """后台线程（按 URL 路由结果回主线程）

        抓取前会把地址切换到网址库中当前可连接的入口；该入口失效时，
        由 _update_ui 的失败处理自动切换到下一个入口并重抓一次。
        """
        try:
            target = self._resolve_grab_url(url)
            if target != url:
                self._log_page(url, f"使用网址库入口: {self._host_of(target)}")
            worker.process_url(target)
        except Exception as e:
            self.operation_queue.put(('error', url, str(e)))
        finally:
            self.operation_queue.put(('finished', url))

    def _stop_grab(self):
        """停止所有抓取"""
        self.is_running = False
        for worker in list(self.workers):
            try:
                worker.stop()
            except Exception:
                pass
        self._running_urls.clear()
        self.progress_label.config(text="已停止")
        self._log("抓取已停止")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    # ========== 页面数据缓存（多网页）与 JSON 导出 ==========
    def _store_record(self, record: Dict, tab_url: str = ''):
        """缓存一条页面记录（按页签地址存入对应页签缓存），仅当前页签刷新界面

        :param tab_url: 发起抓取的页签地址。worker 实际抓取时可能改写主机
            （网址库入口切换）或被站点重定向，record['url'] 是最终地址；
            若用它当键，会为同一页面再建一个同名页签（重复抓取、浪费资源），
            因此页签定位一律优先用 tab_url，取不到时才回退 record['url']。
        """
        url = str(tab_url or '').strip() or str(record.get('url') or '')
        if not url:
            return
        url = self._canonical_page_url(url)

        self.page_records[url] = record

        # 实际抓取的最终地址与页签同路径但主机不同（网址库入口切换/重定向）：
        # 直接变更该页签的网址，而不是为同一页面另建页签
        final_url = str(record.get('url') or '').strip()
        if (final_url and final_url != url and url in self.pages
                and self._page_key(final_url) == self._page_key(url)):
            self._retarget_page(url, final_url)
            url = final_url

        # 确保该 URL 有对应的页签与缓存
        if url not in self.pages:
            self._ensure_page(url)
        page = self.pages.get(url)
        if page is None:
            return

        title = str(record.get('title') or '')
        actors = record.get('actors') or []
        if isinstance(actors, str):
            actors = [actors]
        page['record'] = record
        page['title'] = title
        page['actors'] = [str(a) for a in actors]
        page.pop('translation', None)   # 原文已更新，旧的翻译缓存作废

        # 仅当该页处于显示状态时才刷新界面，避免后台页签抢占显示
        if url == self.active_url:
            self._reset_translation_state()   # 新数据到达时回到原文显示
            self.page_title_var.set(_format_title_display(title))
            self.actors_var.set('、'.join(page['actors']))
            self.series_var.set(str(record.get('series') or ''))
            self.publisher_var.set(str(record.get('publisher') or ''))
            self.maker_var.set(str(record.get('maker') or ''))
            self._refresh_genre_row()

        # 仅对抓取成功的记录输出缓存日志与自动保存 JSON，避免失败/无数据页产生噪音与空文件
        if record.get('status') != 'ok':
            return

        self._log(f"已缓存页面数据[{self._tab_name(url)}]：标题 {record.get('title', '')}")

        # 需要时自动保存为 JSON（磁盘操作放到线程池）
        if self.auto_save_json_var.get() and self.save_path_var.get().strip():
            folder = self.save_path_var.get().strip()
            self._image_pool.submit(lambda r=record, f=folder: self._save_record_json(r, f))

    def _save_record_json(self, record: Dict, folder: str) -> Optional[str]:
        """把单条页面记录保存为 JSON 文件（后台线程执行）"""
        try:
            os.makedirs(folder, exist_ok=True)
            file_path = os.path.join(folder, self._record_filename(record))
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            self._log(f"页面数据已保存 JSON: {self._format_display_path(file_path)}")
            return file_path
        except Exception as e:
            self._log(f"保存 JSON 失败: {e}")
            return None

    @staticmethod
    def _record_filename(record: Dict) -> str:
        """JSON 文件名：优先用番号"""
        base = str(record.get('code') or '').strip()
        if not base:
            parsed = urllib.parse.urlparse(str(record.get('url') or ''))
            base = os.path.basename(parsed.path) or 'page'
        base = re.sub(r'[\\/:*?"<>|\s]+', '_', base).strip('_')
        return f"{base or 'page'}.json"

    def save_all_records_json(self, folder: Optional[str] = None) -> List[str]:
        """把所有缓存的页面记录保存为 JSON"""
        folder = folder or self.save_path_var.get().strip()
        if not folder:
            self._log("未设置保存目录，无法导出 JSON")
            return []

        paths = []
        for record in list(self.page_records.values()):
            path = self._save_record_json(record, folder)
            if path:
                paths.append(path)
        return paths

    def _update_ui(self):
        """更新UI（多网页：所有消息均按 URL 路由到对应页签缓存/显示）"""
        try:
            while not self.operation_queue.empty():
                try:
                    msg = self.operation_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if len(msg) > 1:
                        # 页签归一：同一路径的变体网址（入口切换/手动换域名）
                        # 一律路由到既有页签，避免重复建页签或路由丢失
                        msg = (msg[0], self._canonical_page_url(msg[1])) \
                            + tuple(msg[2:])
                    msg_type = msg[0]

                    if msg_type == 'progress':
                        _, url, progress, message = msg
                        page = self.pages.get(url)
                        if page is not None:
                            page['progress'] = progress
                            page['progress_msg'] = message
                        # 仅当前页签更新进度条
                        if url == self.active_url:
                            self.progress_bar['value'] = progress
                            self.progress_label.config(text=message)

                    elif msg_type == 'image':
                        _, url, image_info = msg
                        page = self.pages.get(url)
                        if page is not None:
                            page['images'].append(image_info)
                        if url == self.active_url:
                            self._display_image(image_info)

                    elif msg_type == 'magnet':
                        _, url, magnet_info = msg
                        page = self.pages.get(url)
                        if page is not None:
                            page['magnets'].append(magnet_info)
                        if url == self.active_url:
                            self._display_magnet(magnet_info)

                    elif msg_type == 'complete':
                        _, url, success, message, images, magnets = msg
                        page = self.pages.get(url)
                        if page is not None:
                            page['status'] = 'done' if success else 'failed'
                            page['progress'] = 100
                            page['progress_msg'] = message
                            if images:
                                page['images'] = list(images)
                            if magnets:
                                page['magnets'] = list(magnets)
                        self._update_tab_state(url)
                        # 抓取完成后立即把图片读进缓存，页签切换时无需再次联网
                        if images:
                            self._prefetch_page_thumbs(images)

                        if url == self.active_url:
                            self.progress_label.config(text=message)
                            self.progress_bar['value'] = 100

                        name = self._tab_name(url)
                        if success:
                            self._host_retried.discard(url)
                            self._log(f"[{name}] 抓取完成！图片: {len(images)} 张，"
                                      f"磁力链: {len(magnets)} 个")
                            if self.auto_download_var.get() and self.save_path_var.get():
                                self._download_images(images, name)
                        else:
                            self._log(f"[{name}] 抓取失败: {message}")
                            # 网址库入口失效：自动切换到下一个可连接入口并重抓一次
                            self._retry_with_next_host(url, message)

                    elif msg_type == 'record':
                        _, url, record = msg
                        # 传入页签地址：record['url'] 可能是入口切换/重定向后的
                        # 最终地址，与页签地址不同；用它建键会重复建页签
                        self._store_record(record, tab_url=url)

                    elif msg_type == 'error':
                        _, url, error_msg = msg
                        self._log(f"[{self._tab_name(url)}] [错误] {error_msg}")
                        messagebox.showerror("错误", f"抓取失败: {error_msg}")

                    elif msg_type == 'finished':
                        _, url = msg
                        self._running_urls.discard(url)
                        if not self._running_urls:
                            self.start_btn.config(state=tk.NORMAL)
                            self.stop_btn.config(state=tk.DISABLED)
                            self.is_running = False
                except Exception as e:
                    # 单条消息处理异常不应中断整个刷新循环
                    try:
                        self._log(f"处理更新消息出错: {e}")
                    except Exception:
                        pass

        except queue.Empty:
            pass

        if self.is_running:
            self.frame.after(100, self._update_ui)

    def _display_image(self, image_info: Dict):
        """登记图片并立即占位，缩略图在后台线程加载（不阻塞界面）
        缩略图处于隐藏状态时只登记数据，待点击显示按钮后统一重建
        """
        try:
            item = dict(image_info)
            self.current_images.append(item)

            # 缩略图即大图时不重复下载，否则后台预取大图，点击即可秒开
            self._start_prefetch(item)

            if getattr(self, '_thumbs_visible', False):
                cell, label = self._create_thumb_cell(item)
                self.thumb_cells.append(cell)
                self.image_labels.append(label)
                self._relayout_images()
                self._load_thumb_async(item, cell, label)

        except Exception as e:
            self._log(f"显示图片失败: {e}")

    def _create_thumb_cell(self, item: Dict) -> Tuple[tk.Frame, tk.Label]:
        """创建 1:1 缩略图方框（先占位，图片到位后再填充）"""
        cell = tk.Frame(self.images_frame, width=THUMB_SIZE, height=THUMB_SIZE,
                        relief=tk.RIDGE, borderwidth=1, background='#ffffff')
        cell.pack_propagate(False)

        label = tk.Label(cell, text="…", background='#ffffff', foreground='#999999',
                         cursor='hand2')
        label.pack(expand=True)

        for widget in (cell, label):
            widget.bind('<Button-1>', lambda e, info=item: self._show_original_image(info))
            self._bind_mousewheel(widget)

        return cell, label

    def _load_thumb_async(self, item: Dict, cell: tk.Frame, label: tk.Label):
        """后台线程下载缩略图，完成后回到主线程填充"""
        thumb_url = item.get('thumb') or item.get('url')
        if not thumb_url:
            return

        # 已缓存（大图缓存里可能已有同一地址）时直接用
        cached = self._image_cache.get(thumb_url)
        if cached:
            self._apply_thumb(item, cell, label, cached)
            return

        if thumb_url in self._thumb_loading:
            return
        self._thumb_loading.add(thumb_url)

        token = self._results_token

        def load():
            data = None
            try:
                data = self._fetch_image_cached(thumb_url)
            except Exception:
                data = None
            finally:
                self._thumb_loading.discard(thumb_url)
            self._run_in_main_thread(
                lambda: self._apply_thumb(item, cell, label, data, token))

        self._image_pool.submit(load)

    def _apply_thumb(self, item: Dict, cell: tk.Frame, label: tk.Label,
                     img_data: Optional[bytes], token: Optional[int] = None):
        """把缩略图填进方框（主线程执行）"""
        if token is not None and token != self._results_token:
            return  # 结果已清空，丢弃过期任务
        try:
            if not cell.winfo_exists() or not label.winfo_exists():
                return
        except tk.TclError:
            return

        if not img_data:
            try:
                label.configure(image='', text="加载失败")
            except tk.TclError:
                pass
            return

        # 缓存缩略图字节：切换页签时直接从缓存调出，避免重复联网读取
        thumb_url = item.get('thumb') or item.get('url')
        if thumb_url:
            self._cache_put(thumb_url, img_data)

        try:
            img = Image.open(io.BytesIO(img_data))
            img.load()
            thumb = img.copy()
            thumb.thumbnail((THUMB_SIZE - 10, THUMB_SIZE - 10), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)

            label.configure(image=photo, text='')
            label.image = photo  # 保持引用
        except Exception as e:
            self._log(f"缩略图处理失败: {e}")
            try:
                label.configure(image='', text="加载失败")
            except tk.TclError:
                pass

    def _display_magnet(self, magnet_info: Dict):
        """显示磁力链接（标题 / 文件大小 / 分享日期）
        标题中带无码/破解标记（-U / -UC / -UNC 等）时整行标红，便于一眼区分
        """
        name = magnet_info.get('name', '未命名')
        if magnet_info.get('hd'):
            name = f"[高清] {name}"

        tags = ()
        if self._uncensored_suffix(str(magnet_info.get('name') or '')):
            tags = (MAGNET_UNCENSORED_TAG,)

        item = self.magnets_tree.insert('', tk.END, values=(
            name,
            magnet_info.get('size', ''),
            magnet_info.get('date', ''),
        ), tags=tags)
        self.magnet_item_map[item] = magnet_info
        self.current_magnets.append(magnet_info)

    # ---------- 无码/破解标记识别（复用 pattern_matcher） ----------
    def _get_pattern_matcher(self):
        """惰性创建番号/后缀识别器（失败时返回 None，不影响其它功能）"""
        if self._pattern_matcher is None:
            try:
                self._pattern_matcher = PatternMatcher()
            except Exception as e:
                self._log(f"初始化番号匹配器失败（无码标记将不识别）: {e}")
                self._pattern_matcher = False   # False = 已知不可用，避免反复重试
        return self._pattern_matcher or None

    def _uncensored_suffix(self, title: str) -> Optional[str]:
        """识别标题中的无码/破解标记（-U / -UC / -UNC / -Uncensored 等）
        :return: 标准化后缀（如 '-UNC'），未识别到返回 None
        """
        text = (title or '').strip()
        if not text:
            return None
        matcher = self._get_pattern_matcher()
        if matcher is None:
            return None

        patterns = getattr(matcher, 'uncensored_patterns', [])
        # 1) 先按空白切词，逐词套用 pattern_matcher 的无码规则：
        #    规则里的标记要求位于词尾（或后接 . - _），而磁力标题常带后缀说明，
        #    如 "ABC-123-UC 无码版本"，整体匹配不到，切词后 "ABC-123-UC" 即可命中。
        #    同时不走 Path.stem，避免磁力标题里的 '.'（如 hhd800.com@...）截断后段标记。
        for chunk in [text] + text.split():
            for pattern, normalized in patterns:
                try:
                    if pattern.search(chunk):
                        return normalized
                except Exception:
                    continue
        return None

    def _log_magnet_title(self, magnet: Dict):
        """把磁力链完整标题写入操作日志（表格列宽有限，超长标题会被截断）"""
        title = str(magnet.get('name') or '').strip()
        if not title:
            self._log("该磁力链没有标题")
            return
        suffix = self._uncensored_suffix(title)
        mark = f"【无码标记 {suffix}】" if suffix else ""
        meta = ' / '.join(x for x in (str(magnet.get('size') or '').strip(),
                                      str(magnet.get('date') or '').strip()) if x)
        self._log(f"磁力标题{mark}：{title}" + (f"（{meta}）" if meta else ""))

    def _on_magnet_click(self, event):
        """单击任意列 → 完整标题写入日志；单击标题列时保留原有“调用外部程序打开”"""
        try:
            item = self.magnets_tree.identify_row(event.y)
            column = self.magnets_tree.identify_column(event.x)
        except Exception:
            return
        if not item:
            return
        magnet = self.magnet_item_map.get(item)
        if not magnet:
            return

        self._log_magnet_title(magnet)
        if column == '#1':  # 第 1 列为标题列（保持原有行为）
            self._open_magnet_external(magnet.get('url', ''))

    def _on_magnet_double_click(self, event):
        """双击任意位置 → 调用外部程序打开"""
        try:
            item = self.magnets_tree.identify_row(event.y)
        except Exception:
            return
        magnet = self.magnet_item_map.get(item or '')
        if magnet:
            self._open_magnet_external(magnet.get('url', ''))

    def _open_selected_magnet(self):
        """打开选中的磁力链接"""
        selection = self.magnets_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要打开的磁力链接")
            return

        for item in selection:
            magnet = self.magnet_item_map.get(item)
            if magnet:
                self._open_magnet_external(magnet.get('url', ''))

    def _open_magnet_external(self, magnet_url: str):
        """调用系统关联程序（如迅雷）打开磁力链接"""
        magnet_url = (magnet_url or '').strip()
        if not magnet_url:
            return

        try:
            if sys.platform.startswith('win'):
                os.startfile(magnet_url)  # 交给系统默认关联程序（迅雷 / uTorrent 等）
            else:
                webbrowser.open(magnet_url)
            self._log(f"已调用外部程序打开: {magnet_url[:70]}...")
        except Exception as e:
            self._log(f"调用外部程序打开失败: {e}")
            messagebox.showerror(
                "错误",
                f"调用外部程序打开失败: {e}\n可先复制链接，再到下载工具中新建任务。")

    def _download_images(self, images: List[Dict], page_name: str = ''):
        """下载图片（放到后台线程，避免阻塞界面）"""
        save_path = self.save_path_var.get()
        if not save_path:
            return

        def task():
            self._save_images_to_disk(images, save_path, page_name)

        self._image_pool.submit(task)

    def _save_images_to_disk(self, images: List[Dict], save_path: str, page_name: str = ''):
        """按顺序把图片保存到指定目录（后台线程执行；多页签时用页签名做前缀避免重名）"""
        os.makedirs(save_path, exist_ok=True)

        safe_name = re.sub(r'[\\/:*?"<>|\s]+', '_', page_name or '').strip('_')
        prefix = f"{safe_name}_" if safe_name else ''

        for idx, img in enumerate(images, 1):
            try:
                img_data = self.worker.download_image(img['url']) if self.worker else None
                if img_data:
                    # 从URL获取扩展名
                    ext = os.path.splitext(urllib.parse.urlparse(img['url']).path)[1]
                    if not ext:
                        ext = '.jpg'

                    filename = f"{prefix}image_{idx:03d}{ext}"
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
            magnet = self.magnet_item_map.get(item)
            if magnet and magnet.get('url'):
                urls.append(magnet['url'])

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
                        hd_mark = "[高清] " if magnet.get('hd') else ""
                        f.write(f"{hd_mark}{magnet.get('name', '未命名')}\n")
                        if magnet.get('size'):
                            f.write(f"大小: {magnet['size']}\n")
                        if magnet.get('date'):
                            f.write(f"分享日期: {magnet['date']}\n")
                        f.write(f"{magnet.get('url', '')}\n")
                        f.write("\n")

                self._log(f"已导出到: {self._format_display_path(file_path)}")
                messagebox.showinfo("成功", f"已导出 {len(self.current_magnets)} 个磁力链接")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")

    def _clear_results(self):
        """清空全部结果（关闭所有页签、清理缓存与显示）——仅供“开始抓取”等内部流程使用"""
        # 关闭可能打开的图片浏览层，并标记新批次（丢弃仍在下载的旧图）
        self._close_image_viewer()
        self._results_token += 1
        self._thumb_loading.clear()

        # 关闭所有页签
        for widgets in list(self._tab_widgets.values()):
            try:
                widgets['frame'].destroy()
            except tk.TclError:
                pass
        self._tab_widgets.clear()

        # 清空多网页缓存
        self.pages.clear()
        self.page_order.clear()
        self.page_records.clear()
        self._pending_urls.clear()
        self._running_urls.clear()
        self.active_url = None

        # 清空图片/磁力/标题/演员/进度
        self._clear_display_only()

        # 清空日志
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_current_tab(self):
        """“清空结果”按钮：只清空当前页签对应的视频在主窗口中的全部信息
        （页签、图片、磁力链、标题/演员等字段与视频检索结果），其它页签不受影响"""
        url = self.active_url
        if not url or url not in self.pages:
            self._log("[清空] 当前没有打开的页签，无需清空")
            return
        name = self._tab_name(url)

        # 关闭图片浏览层，并标记新批次（丢弃该页签仍在下载的旧图）
        self._close_image_viewer()
        self._results_token += 1
        self._thumb_loading.clear()

        # 移除该页签的控件与缓存数据（参照 _close_page 的清理范围）
        widgets = self._tab_widgets.pop(url, None)
        if widgets:
            try:
                widgets['frame'].destroy()
            except tk.TclError:
                pass
        self.pages.pop(url, None)
        self.page_records.pop(url, None)
        if self._page_keys.get(self._page_key(url)) == url:
            self._page_keys.pop(self._page_key(url), None)
        if url in self.page_order:
            self.page_order.remove(url)
        if url in self._pending_urls:
            self._pending_urls.remove(url)
        self._running_urls.discard(url)

        # 清空视频检索结果（文本框与“打开文件夹”的匹配列表均属当前页签）
        self._set_search_result('')
        self._last_search_folders = []

        # 还有其它页签：切换到第一个；没有：清空右侧显示区
        if self.page_order:
            self._switch_page(self.page_order[0])
        else:
            self.active_url = None
            self._clear_display_only()
        self._log(f"[清空] 已清空页签“{name}”的抓取与检索信息（其它页签不受影响）")

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