#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 文件夹检查 / 文件比对 合并模块
#
# 本模块将两个功能相似的模块（原 video_check_tab.py 与 file_compare_tab.py）合并为统一的
# 标签页，提供两种工作模式：
#   1. 单文件夹检查（mode="check"）：遍历一个文件夹（可选递归），检查文件夹名与内部
#      视频文件名中的番号/编号是否匹配，并检测缺失的视频 / 封面 / 字幕文件。
#   2. 双文件夹比对（mode="compare"）：对比"原视频文件夹(A)"与"已转码文件夹(B)"，
#      检查转码完成情况。匹配规则（按优先级）：
#         · 文件名完全相同           -> 已转码
#         · B 侧文件名带数字后缀     -> 已转码（如 A: xxx.mp4 / B: xxx-1.mp4、xxx~2.mkv）
#         · 前置文件名 + 番号相同     -> 已转码（番号由 pattern_matcher 抽取）
#         · 仅番号相同               -> 已转码
#         · 其余 A 中有、B 中没有的  -> 未转码，列入"未转码文件"列表
#      未转码列表按 Windows 资源管理器的列显示（文件名/文件类型/修改日期/大小/
#      帧宽度/帧高度/数据速率），支持单选、多选，并可直接拖到其它软件中。
#
# 两种模式涉及番号或编号识别时，统一使用 pattern_matcher 模块进行匹配识别。
#
# 双模式运行：
#   1. 作为模块被主程序调用：from folder_check_tab import FolderCheckTab
#      （调用方负责将 tab.frame 添加到 Notebook）
#   2. 独立运行：直接执行本文件，会创建一个简单的测试窗口

import os
import json
import re
import csv
import struct
import threading
import queue
import subprocess
import shutil
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime

# ============ 番号匹配器导入 ============
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    PatternMatcher = None
    print("[FolderCheckTab] 警告: 无法导入 pattern_matcher，番号匹配功能将不可用")

# ============ 右下角浮出式提醒（Toast 通知） ============
# 实现已抽取到公共模块 toast.py，供所有选项卡复用（右下角浮出、自动消失、可堆叠）
from toast import ToastNotifier, show_toast  # noqa: E402,F401


# ============ 配置管理器导入（与 file_compare_tab 保持兼容的单例） ============
try:
    from file_organize_tab import ConfigManager
except ImportError:
    class ConfigManager:
        """统一配置管理器 - 单例模式（兼容版，独立运行时使用）"""
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
                    self.show_toast(f"保存配置失败: {e}", "error")
                return False

        def get_section(self, section: str, default: Optional[Dict] = None) -> Dict:
            return self._config.get(section, default or {})

        def set_section(self, section: str, data: Dict, auto_save: bool = True):
            self._config[section] = data
            if auto_save:
                self.save_config()


# =====================================================================
# 文件属性读取工具
# =====================================================================
# 用途：为"未转码文件"列表提供与 Windows 资源管理器一致的列数据
#   · 文件类型     -> Shell API(SHGetFileInfoW)，与资源管理器"类型"列一致
#   · 帧宽度/帧高度/数据速率 -> Windows 属性系统(System.Video.FrameWidth /
#                    FrameHeight / EncodingBitrate)，即资源管理器中的同名属性
#                    "数据速率"只统计视频流（TotalBitrate 才是视频+音频），
#                    与视频转码模块的"视频码率"设置口径保持一致
# 实现：全部通过 ctypes 调用系统 API（不依赖 pywin32）；
#       任何一步失败都会安全降级（改用 cv2 读取，再不行留空），不会崩溃。
# =====================================================================

_IS_WINDOWS = (os.name == 'nt')

if _IS_WINDOWS:  # pragma: no cover - 以下实现仅用于 Windows
    import ctypes
    from ctypes import wintypes

    # ---- Shell 常量 ----
    _SHGFI_TYPENAME = 0x000000400           # 获取"类型"描述
    _SHGFI_USEFILEATTRIBUTES = 0x000000010  # 不打开文件内容，仅按属性/扩展名判断
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_NORMAL = 0x00000080

    # ---- PROPVARIANT 数值类型 ----
    _VT_I4 = 3
    _VT_R8 = 5
    _VT_UI4 = 19
    _VT_I8 = 20
    _VT_UI8 = 21

    _shell32 = _ole32 = _propsys = None
    try:
        _shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        _ole32 = ctypes.WinDLL('ole32', use_last_error=True)
        _propsys = ctypes.WinDLL('propsys', use_last_error=True)
    except Exception:
        _shell32 = _ole32 = _propsys = None

    class _SHFILEINFOW(ctypes.Structure):
        """SHFILEINFOW：用于 SHGetFileInfoW 返回文件类型描述"""
        _fields_ = [
            ('hIcon', ctypes.c_void_p),
            ('iIcon', ctypes.c_int),
            ('dwAttributes', wintypes.DWORD),
            ('szDisplayName', ctypes.c_wchar * 260),
            ('szTypeName', ctypes.c_wchar * 80),
        ]

    class _GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

    class _PROPERTYKEY(ctypes.Structure):
        _fields_ = [('fmtid', _GUID), ('pid', wintypes.DWORD)]

    class _PROPVARIANT(ctypes.Structure):
        # 64 位下：vt(2) + 3*WORD(6) + union(16) = 24 字节
        _fields_ = [('vt', wintypes.WORD), ('wReserved1', wintypes.WORD),
                    ('wReserved2', wintypes.WORD), ('wReserved3', wintypes.WORD),
                    ('data', ctypes.c_ubyte * 16)]

    def _make_guid(d1: int, d2: int, d3: int, b8) -> '_GUID':
        g = _GUID()
        g.Data1, g.Data2, g.Data3 = d1, d2, d3
        g.Data4 = (ctypes.c_ubyte * 8)(*b8)
        return g

    # IID_IPropertyStore = {886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}
    _IID_IPropertyStore = _make_guid(0x886D8EEB, 0x8CF2, 0x4446,
                                     (0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99))
    # IID_IShellItem2 = {7E9FB0D3-919F-4307-AB2E-9B1860310C93}
    _IID_IShellItem2 = _make_guid(0x7E9FB0D3, 0x919F, 0x4307,
                                  (0xAB, 0x2E, 0x9B, 0x18, 0x60, 0x31, 0x0C, 0x93))
    # 视频属性集 {64440491-4C8B-11D1-8B70-080036B11A03}：
    #   pid 3 = System.Video.FrameWidth   pid 4 = System.Video.FrameHeight
    #   pid 8  = System.Video.EncodingBitrate（资源管理器"数据速率 / Data rate"，仅视频，bps）
    #   pid 43 = System.Video.TotalBitrate（资源管理器"总比特率"＝视频+音频，bps）
    #   本工具按"视频码率"统一口径，优先取 pid 8
    _FMTID_VIDEO = _make_guid(0x64440491, 0x4C8B, 0x11D1,
                              (0x8B, 0x70, 0x08, 0x00, 0x36, 0xB1, 0x1A, 0x03))
    _PKEY_FRAME_WIDTH = _PROPERTYKEY(_FMTID_VIDEO, 3)
    _PKEY_FRAME_HEIGHT = _PROPERTYKEY(_FMTID_VIDEO, 4)
    _PKEY_VIDEO_BITRATE = _PROPERTYKEY(_FMTID_VIDEO, 8)
    _PKEY_TOTAL_BITRATE = _PROPERTYKEY(_FMTID_VIDEO, 43)

    def _com_init() -> bool:
        """在工作线程中初始化 COM（读取属性前必须调用）"""
        if _ole32 is None:
            return False
        try:
            _ole32.CoInitializeEx.restype = ctypes.c_long
            _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            hr = _ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            return hr in (0, 1)  # S_OK / S_FALSE（已初始化）
        except Exception:
            return False

    def _com_uninit():
        if _ole32 is None:
            return
        try:
            _ole32.CoUninitialize()
        except Exception:
            pass

    def _propvariant_to_number(pv: '_PROPVARIANT'):
        """把数值型 PROPVARIANT 转为 Python 数字，非数值返回 None"""
        try:
            raw = bytes(pv.data)
            if pv.vt == _VT_UI4:
                return struct.unpack('<I', raw[:4])[0]
            if pv.vt == _VT_I4:
                return struct.unpack('<i', raw[:4])[0]
            if pv.vt in (_VT_UI8, _VT_I8):
                return struct.unpack('<Q', raw[:8])[0]
            if pv.vt == _VT_R8:
                return int(struct.unpack('<d', raw[:8])[0])
        except Exception:
            return None
        return None

    def shell_file_type_name(path: str, is_dir: bool = False) -> str:
        """读取 Windows Shell 的"类型"描述（与资源管理器"类型"列一致）"""
        if _shell32 is None:
            return ''
        try:
            fn = _shell32.SHGetFileInfoW
            fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                           ctypes.POINTER(_SHFILEINFOW), wintypes.UINT, wintypes.UINT]
            fn.restype = ctypes.c_size_t
            info = _SHFILEINFOW()
            attrs = _FILE_ATTRIBUTE_DIRECTORY if is_dir else _FILE_ATTRIBUTE_NORMAL
            flags = _SHGFI_TYPENAME | _SHGFI_USEFILEATTRIBUTES
            if fn(str(path), attrs, ctypes.byref(info), ctypes.sizeof(info), flags):
                return (info.szTypeName or '').strip()
        except Exception:
            pass
        return ''

    def _vtable(obj: ctypes.c_void_p, size: int):
        """取 COM 对象的虚表（返回 c_void_p 数组视图）"""
        addr = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p)).contents.value
        return (ctypes.c_void_p * size).from_address(addr)

    def shell_media_props(path: str) -> Dict[str, Optional[int]]:
        """通过 Windows 属性系统读取视频属性，返回 width/height/bitrate(bps)

        路径：SHCreateItemFromParsingName -> IShellItem2::GetPropertyStore
              -> IPropertyStore::GetValue
        """
        result: Dict[str, Optional[int]] = {}
        if _shell32 is None:
            return result
        item = ctypes.c_void_p()
        store = ctypes.c_void_p()
        try:
            create = _shell32.SHCreateItemFromParsingName
            create.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                               ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
            create.restype = ctypes.c_long
            if create(str(path), None, ctypes.byref(_IID_IShellItem2),
                      ctypes.byref(item)) != 0 or not item.value:
                return result

            # IShellItem2 虚表：0..2 IUnknown / 3..7 IShellItem / 8 GetPropertyStore ...
            item_vt = _vtable(item, 24)
            get_property_store = ctypes.cast(item_vt[8], ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)))
            # flags = 0：允许读取"慢速属性"（视频属性需要打开文件内容）
            if get_property_store(item, 0, ctypes.byref(_IID_IPropertyStore),
                                  ctypes.byref(store)) != 0 or not store.value:
                return result

            # IPropertyStore 虚表：0..2 IUnknown / 3 GetCount / 4 GetAt /
            #                     5 GetValue / 6 SetValue / 7 Commit
            store_vt = _vtable(store, 8)
            get_value = ctypes.cast(store_vt[5], ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p,
                ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)))
            release = ctypes.cast(store_vt[2], ctypes.WINFUNCTYPE(
                ctypes.c_ulong, ctypes.c_void_p))

            for key_name, key in (('width', _PKEY_FRAME_WIDTH),
                                  ('height', _PKEY_FRAME_HEIGHT),
                                  ('video_bitrate', _PKEY_VIDEO_BITRATE),
                                  ('bitrate', _PKEY_TOTAL_BITRATE)):
                pv = _PROPVARIANT()
                try:
                    if get_value(store, ctypes.byref(key), ctypes.byref(pv)) == 0:
                        val = _propvariant_to_number(pv)
                        if val:
                            result[key_name] = val
                except Exception:
                    continue
                finally:
                    try:
                        _ole32.PropVariantClear(ctypes.byref(pv))
                    except Exception:
                        pass
            release(store)
        except Exception:
            pass
        finally:
            if item.value:
                try:
                    item_vt = _vtable(item, 24)
                    ctypes.cast(item_vt[2], ctypes.WINFUNCTYPE(
                        ctypes.c_ulong, ctypes.c_void_p))(item)
                except Exception:
                    pass
        return result

else:  # 非 Windows：全部降级为空实现
    ctypes = None

    def shell_file_type_name(path: str, is_dir: bool = False) -> str:
        return ''

    def shell_media_props(path: str) -> Dict[str, Optional[int]]:
        return {}


def _cv2_media_props(path: str, file_size: int = 0):
    """回退方案：用 OpenCV 读取视频尺寸，并按 文件大小/时长 估算数据速率

    Returns:
        (width, height, bitrate_bps)，取不到时对应项为 None
    """
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            return None, None, None
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        bitrate = None
        if fps > 0 and frames > 0 and file_size > 0:
            duration = frames / fps
            if duration > 0:
                bitrate = int(file_size * 8 / duration)
        return (width or None), (height or None), bitrate
    except Exception:
        return None, None, None


def get_media_properties(path: str, file_size: int = 0):
    """读取视频媒体的 帧宽度 / 帧高度 / 数据速率(bps)

    优先使用 Windows 属性系统（与资源管理器一致），失败时回退 OpenCV。

    Returns:
        (width, height, bitrate_bps)
    """
    width = height = bitrate = None
    if _IS_WINDOWS:
        props = shell_media_props(path)
        width = props.get('width')
        height = props.get('height')
        # 统一按"视频码率"口径：优先 System.Video.EncodingBitrate（资源管理器"数据速率"），
        # 取不到才退回 System.Video.TotalBitrate（视频+音频）
        bitrate = props.get('video_bitrate') or props.get('bitrate')
    if width is None or height is None or bitrate is None:
        w2, h2, b2 = _cv2_media_props(path, file_size)
        width = width if width is not None else w2
        height = height if height is not None else h2
        bitrate = bitrate if bitrate is not None else b2
    return width, height, bitrate


def format_file_size(size: int) -> str:
    """按资源管理器风格格式化文件大小"""
    try:
        size = int(size)
    except Exception:
        return ''
    if size < 1024:
        return f"{size} 字节"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.2f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def format_bitrate(bps) -> str:
    """数据速率：资源管理器以 kbps 显示"""
    if not bps:
        return ''
    try:
        return f"{int(bps) // 1000} kbps"
    except Exception:
        return ''


def format_timestamp(ts: float) -> str:
    """按资源管理器风格格式化修改日期：2026/09/30 10:23"""
    if not ts:
        return ''
    try:
        return datetime.fromtimestamp(ts).strftime('%Y/%m/%d %H:%M')
    except Exception:
        return ''


# 转码/复制常见的文件名后缀：name-1、name_2、name ~3、name(1)、name.1、name - 副本(2)
_SUFFIX_PATTERNS = [
    re.compile(r'^(?P<base>.+?)[ \t]*\(\s*\d{1,3}\s*\)$'),
    re.compile(r'^(?P<base>.+?)[ \t]*（\s*\d{1,3}\s*）$'),
    re.compile(r'^(?P<base>.+?)[ \t]*[-_~.][ \t]*\d{1,3}$'),
    re.compile(r'^(?P<base>.+?)[ \t]*[-–—][ \t]*(?:副本|复本|拷贝|copy)'
               r'(?:[ \t]*[（(]\s*\d{1,3}\s*[)）])?$', re.IGNORECASE),
]


def strip_numeric_suffix_chain(name: str, max_layers: int = 3) -> List[str]:
    """逐层去掉尾部的数字/副本后缀，返回每一层的中间名称（由浅到深）

    例如：
        "xxx-1"        -> ["xxx"]
        "xxx(1)~1"     -> ["xxx(1)", "xxx"]      （先剥 ~1，再剥 (1)）
        "xxx - 副本(2)" -> ["xxx - 副本", "xxx"]
    返回空列表表示无法去除。
    """
    chain: List[str] = []
    current = name
    for _ in range(max_layers):   # 最多剥三层，处理 "xxx - 副本 (2)" 这类组合后缀
        nxt = current
        for pattern in _SUFFIX_PATTERNS:
            m = pattern.match(nxt)
            if m:
                base = m.group('base').strip()
                if base:
                    nxt = base
                    break
        if nxt == current:
            break
        current = nxt
        chain.append(current)
    return chain


def strip_numeric_suffix(name: str) -> str:
    """去掉文件名（不含扩展名）尾部的数字/副本后缀（一直剥到不能再剥）

    例如：xxx-1 -> xxx、xxx~2 -> xxx、xxx (1) -> xxx、xxx - 副本(2) -> xxx
    无法去除时原样返回。
    """
    chain = strip_numeric_suffix_chain(name)
    return chain[-1] if chain else name


# 前置文件名中常见的分隔符 / 站点括号（比较前统一去掉）
_PREFIX_NOISE_RE = re.compile(r'[\s\[\]{}()（）【】〔〕<>@._\-~—–"\'`]+')


def normalize_prefix_text(text: str) -> str:
    """归一化“前置文件名”：去掉空格与常见分隔符/站点括号，便于比较

    例如 "[Thz.la]" 与 "[thz la]" 都会归一化为 "thzla"。
    """
    return _PREFIX_NOISE_RE.sub('', text).lower()


def extract_name_parts(name: str, matcher) -> Tuple[str, str]:
    """把文件名（不含扩展名）拆成 (归一化前置文件名, 番号)

    番号为空表示未识别到可用于匹配的可靠番号。
    使用 pattern_matcher.PatternMatcher 识别番号；generic / short_numeric
    属于兜底猜测，不作为匹配依据。
    """
    if matcher is None:
        return normalize_prefix_text(name), ''
    try:
        code = matcher.extract_code(name)
    except Exception:
        return normalize_prefix_text(name), ''
    if not code:
        return normalize_prefix_text(name), ''
    if (code.get('type') or '') in ('generic', 'short_numeric'):
        return normalize_prefix_text(name), ''
    full = (code.get('full') or '').lower()
    if not full:
        return normalize_prefix_text(name), ''
    prefix = name
    original = code.get('original') or ''
    if original:
        pos = name.find(original)
        if pos >= 0:
            prefix = name[:pos]
    return normalize_prefix_text(prefix), full


class HistoryPopupEntry(ttk.Entry):
    """带历史下拉的输入框

    点击输入框时，在其正下方弹出一个无边框列表，列出最近使用过的路径（最多 5 条），
    点击其中一条即可填入。弹出层不抢占焦点，因此键盘手动输入、光标编辑都不受影响。

    历史数据由 history_provider() 提供（按“由新到旧”排序）；选中回调 on_choose(value)。
    """

    MAX_ITEMS = 5

    def __init__(self, master, textvariable, history_provider=None,
                 on_choose=None, **kwargs):
        super().__init__(master, textvariable=textvariable, **kwargs)
        self.var = textvariable
        self.history_provider = history_provider
        self.on_choose = on_choose
        self._popup = None
        self._listbox = None
        self._global_bind_id = None

        self.bind('<Button-1>', self._on_click, add='+')
        self.bind('<Escape>', lambda e: self._hide_popup(), add='+')
        self.bind('<FocusOut>', lambda e: self.after(180, self._hide_popup), add='+')
        self.bind('<Destroy>', lambda e: self._hide_popup(), add='+')

    # ---------- 历史数据 ----------
    def _history_items(self):
        try:
            items = list(self.history_provider() or []) if self.history_provider else []
        except Exception:
            items = []
        out = [str(i) for i in items if str(i).strip()]
        if self.var.get().strip():        # 当前值也作为候选，方便快速切回
            cur = self.var.get().strip()
            out = [i for i in out if i.lower() != cur.lower()]
        return out[:self.MAX_ITEMS]

    # ---------- 弹出层 ----------
    def _on_click(self, _event=None):
        self.after_idle(self._show_popup)

    def _show_popup(self):
        items = self._history_items()
        if not items:
            return
        self._hide_popup()
        pop = tk.Toplevel(self)
        pop.wm_overrideredirect(True)
        try:
            pop.attributes('-topmost', True)
        except Exception:
            pass
        lb = tk.Listbox(pop, activestyle='none', highlightthickness=0,
                        borderwidth=1, relief=tk.SOLID,
                        font=('Microsoft YaHei', 9), selectbackground='#cfe3ff',
                        selectforeground='black')
        for it in items:
            lb.insert(tk.END, it)
        lb.pack(fill=tk.BOTH, expand=True)
        pop.update_idletasks()
        width = max(self.winfo_width(), 200)
        height = lb.winfo_reqheight()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        pop.wm_geometry(f"{width}x{height}+{x}+{y}")

        lb.bind('<ButtonPress-1>', self._on_pick)
        self._popup, self._listbox = pop, lb
        # 点击窗口其它位置时自动关闭
        self._global_bind_id = self.winfo_toplevel().bind(
            '<Button-1>', self._on_global_click, add='+')

    def _on_global_click(self, event):
        if event.widget is not self:
            self._hide_popup()

    def _on_pick(self, event):
        lb = self._listbox
        if lb is None:
            return
        idx = lb.nearest(event.y)
        if 0 <= idx < lb.size():
            value = lb.get(idx)
            self.var.set(value)
            self.icursor(tk.END)
            if callable(self.on_choose):
                try:
                    self.on_choose(value)
                except Exception:
                    pass
        self._hide_popup()

    def _hide_popup(self):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None
            self._listbox = None
        if self._global_bind_id is not None:
            try:
                self.winfo_toplevel().unbind('<Button-1>', self._global_bind_id)
            except Exception:
                pass
            self._global_bind_id = None


class FolderCheckTab:
    """文件夹检查 / 文件比对 合并标签页

    提供两种模式：
      - "check"    : 单文件夹检查
      - "compare"  : 双文件夹比对（转码前后）
    """

    CONFIG_SECTION = "folder_check"

    # ============ 文件类型定义（取两个原模块的并集，保证兼容） ============
    VIDEO_EXTENSIONS: Set[str] = {
        '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.m4v',
        '.mpeg', '.mpg', '.ts', '.rmvb', '.rm', '.m2ts'
    }
    IMAGE_EXTENSIONS: Set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif'}
    SUBTITLE_EXTENSIONS: Set[str] = {'.srt'}

    # ============ 视频文件比对：左右两列按相同的属性行对齐 ============
    VIDEO_INFO_FIELDS: Tuple[Tuple[str, str], ...] = (
        ('type', '类型'),
        ('size', '大小'),
        ('location', '文件位置'),
        ('modified', '修改日期'),
        ('duration', '时长'),
        ('width', '帧宽度'),
        ('height', '帧高度'),
        ('fps', '帧速率'),
        ('data_rate', '数据速率'),
        ('total_bitrate', '总比特率'),
    )

    # 需要跳过的文件夹名标记
    SKIP_FOLDER_MARKERS = ['(个人)', '(不好看)']

    # 自带字幕的视频文件名后缀标记（不区分大小写检查）
    BUILTIN_SUBTITLE_SUFFIXES = ['-C', '-c', '-UNC', 'UC']

    # 文件夹输入框保留的历史路径条数
    HISTORY_MAX = 5

    # ============ 未转码文件列表的列（参考 Windows 资源管理器） ============
    # 完整路径列保存但不显示，仅供拖拽 / 右键菜单 / 导出使用
    UNMATCHED_COLUMNS = ('name', 'file_type', 'modified', 'size',
                         'frame_width', 'frame_height', 'bitrate', 'path')
    UNMATCHED_COLUMN_CONFIG = {
        'name': ('文件名', 320, tk.W),
        'file_type': ('文件类型', 130, tk.W),
        'modified': ('修改日期', 130, tk.W),
        'size': ('大小', 110, tk.E),
        'frame_width': ('帧宽度', 90, tk.E),
        'frame_height': ('帧高度', 90, tk.E),
        'bitrate': ('数据速率', 110, tk.E),
        'path': ('完整路径', 400, tk.W),
    }

    def __init__(self, parent_notebook, controller=None):
        """
        初始化标签页

        Args:
            parent_notebook: 父级 Notebook 容器
            controller: 主控制器对象（FileMoverGUI 实例），可为 None（独立运行时）
        """
        self.parent = parent_notebook
        self.controller = controller

        # 初始化番号匹配器
        if PatternMatcher is not None:
            self.matcher = PatternMatcher()
        else:
            self.matcher = None

        # 获取配置管理器
        if controller and hasattr(controller, 'config_manager'):
            self.config_manager = controller.config_manager
        else:
            self.config_manager = ConfigManager()

        # 当前模式
        self.mode_var = tk.StringVar(value="check")

        # 创建主框架
        self.frame = ttk.Frame(parent_notebook)

        # ---------- 单文件夹检查模式的数据 ----------
        self.check_results: List[Dict] = []
        self.sort_reverse = False
        self.sort_column = None

        # ---------- 双文件夹比对模式的数据 ----------
        self.compare_results: List[Dict] = []
        self.matched_files: List[Dict] = []
        self.unmatched_files: List[Path] = []
        self.source_folder: Optional[Path] = None
        self.target_folder: Optional[Path] = None
        self.is_running = False
        self.operation_thread = None
        self.progress_queue = queue.Queue()

        # 未转码列表：item_id -> 文件属性字典（供排序 / 拖拽 / 导出使用）
        self.unmatched_rows: Dict[str, Dict] = {}
        # A/B 文件夹输入框的历史路径（由新到旧，最多 HISTORY_MAX 条）
        self.source_history: List[str] = []
        self.target_history: List[str] = []
        self.compare_video_only_var = tk.BooleanVar(value=True)
        self.compare_recursive_var = tk.BooleanVar(value=False)
        # 视频属性在后台线程读取：generation 变化即代表旧线程应停止
        self.props_generation = 0
        self.props_running = False
        self.unmatched_sort_col = 'name'
        self.unmatched_sort_reverse = False
        self.drag_enabled = False
        self._drag_cmd_name = None

        # ---------- 视频文件比对模式的数据 ----------
        self.video_sides: Dict[str, Dict] = {}
        # 后台线程写入、(side, gen) -> (path, info)，由主线程轮询取用（避免跨线程 after）
        self.video_probe_results: Dict = {}
        self._EmbeddedPlayerCls = None
        self._ffprobe_path = None
        self._vlc_path = None

        self._init_ui()
        self._load_config()

        # 启动进度检查（供比对模式的后台线程使用）
        self._check_progress()

    # =====================================================================
    # 通用工具
    # =====================================================================
    def get_frame(self):
        """获取选项卡框架（供调用方添加到 Notebook）"""
        return self.frame

    def log(self, message: str):
        """通过 controller 记录日志到日志选项卡，无 controller 时打印"""
        if self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

    # =====================================================================
    # UI 初始化
    # =====================================================================
    def _init_ui(self):
        """初始化用户界面"""
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(5, weight=1)  # 结果区域可扩展

        # ========== 0. 模式选择区域 ==========
        mode_frame = ttk.LabelFrame(self.frame, text="模式选择", padding=10)
        mode_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))

        ttk.Radiobutton(
            mode_frame, text="单文件夹检查（视频命名/缺失检查）",
            variable=self.mode_var, value="check", command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=(0, 24))
        ttk.Radiobutton(
            mode_frame, text="双文件夹比对（转码前后对比）",
            variable=self.mode_var, value="compare", command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=(0, 24))
        ttk.Radiobutton(
            mode_frame, text="视频文件比对（画面/属性对比）",
            variable=self.mode_var, value="video", command=self._on_mode_change
        ).pack(side=tk.LEFT)

        # ========== 1. 单文件夹检查 - 配置区域 ==========
        self.check_config_frame = ttk.LabelFrame(self.frame, text="单文件夹检查配置", padding=10)
        self._init_check_config_ui(self.check_config_frame)
        self.check_config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        # ========== 2. 双文件夹比对 - 配置区域（初始隐藏） ==========
        self.compare_config_frame = ttk.LabelFrame(self.frame, text="双文件夹比对配置", padding=10)
        self._init_compare_config_ui(self.compare_config_frame)
        self.compare_config_frame.grid_remove()

        # ========== 3. 操作按钮区域 ==========
        btn_frame = ttk.Frame(self.frame)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)

        self.save_btn = ttk.Button(btn_frame, text="💾 保存配置",
                                   command=lambda: self._save_config(show_tip=True))
        self.save_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.run_btn = ttk.Button(btn_frame, text="▶ 开始执行", command=self._run)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = ttk.Button(btn_frame, text="⏹ 停止", command=self._stop_compare,
                                   width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.export_btn = ttk.Button(btn_frame, text="📄 导出结果", command=self._export_results,
                                     state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=(0, 10))

        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(btn_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        # ========== 4. 统计信息区域 ==========
        stats_frame = ttk.LabelFrame(self.frame, text="统计信息", padding=5)
        stats_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        self.stats_label = ttk.Label(stats_frame, text="就绪")
        self.stats_label.pack(side=tk.LEFT)

        # ========== 5. 结果区域（内部 Notebook 区分两种模式的展示） ==========
        self.result_notebook = ttk.Notebook(self.frame)
        self.result_notebook.grid(row=5, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.result_notebook.rowconfigure(0, weight=1)
        self.result_notebook.columnconfigure(0, weight=1)

        # --- 模式一：检查结果 ---
        self.check_result_frame = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.check_result_frame, text="检查结果")
        self._init_check_result_ui(self.check_result_frame)

        # --- 模式二：比对结果（内部再分两个子页） ---
        self.compare_result_frame = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.compare_result_frame, text="比对结果")
        self._init_compare_result_ui(self.compare_result_frame)

        self.check_result_tab = self.result_notebook.tabs()[0]
        self.compare_result_tab = self.result_notebook.tabs()[1]

        # ========== 6. 视频文件比对面板（独立于结果页签，初始隐藏） ==========
        self.video_compare_frame = ttk.Frame(self.frame)
        self._init_video_compare_ui(self.video_compare_frame)
        self.video_compare_frame.grid_remove()

        # 根据初始模式应用界面（显示正确的结果页签、隐藏无关页签与配置区）
        self._on_mode_change()

    # ---------------------------------------------------------------------
    # 单文件夹检查 - 配置 UI
    # ---------------------------------------------------------------------
    def _init_check_config_ui(self, parent: ttk.LabelFrame):
        parent.columnconfigure(1, weight=1)

        # 功能说明
        desc_frame = ttk.Frame(parent)
        desc_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        desc_frame.columnconfigure(0, weight=1)
        description = (
            "遍历指定文件夹（可选包含子文件夹），检查文件夹名称与内部视频文件名中的番号/编号"
            "是否匹配，\n检测文件夹中是否缺失视频文件、封面图片或字幕文件。"
            "可设置排除文件夹，跳过不需要检查的目录。"
        )
        self.desc_text = tk.Text(desc_frame, height=3, wrap=tk.WORD, bg='#f0f0f0', relief=tk.FLAT)
        self.desc_text.grid(row=0, column=0, sticky="ew")
        self.desc_text.insert('1.0', description)
        self.desc_text.config(state=tk.DISABLED)

        # 基础文件夹
        ttk.Label(parent, text="基础文件夹:").grid(row=1, column=0, sticky="w", padx=(0, 5))
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(parent, textvariable=self.folder_var)
        self.folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5))
        ttk.Button(parent, text="浏览...", command=self._browse_folder).grid(row=1, column=2, sticky="e")

        tip_label = ttk.Label(parent, text="支持本地路径 (C:\\...) 或网络共享 (\\server\\share)",
                              foreground="gray", font=('Microsoft YaHei', 8))
        tip_label.grid(row=2, column=1, sticky="w", pady=(2, 0))

        # 选项
        options_frame = ttk.Frame(parent)
        options_frame.grid(row=3, column=1, sticky="w", pady=(5, 0))
        self.include_subfolders_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="包含子文件夹 (递归遍历所有子目录)",
                        variable=self.include_subfolders_var).pack(side=tk.LEFT, padx=(0, 20))
        self.check_subtitle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="检查是否有字幕文件",
                        variable=self.check_subtitle_var).pack(side=tk.LEFT)

        # 排除文件夹
        exclude_frame = ttk.LabelFrame(parent, text="排除文件夹 (这些文件夹及其子目录将被跳过)", padding=5)
        exclude_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        exclude_frame.columnconfigure(0, weight=1)

        self.excluded_listbox = tk.Listbox(exclude_frame, height=4, selectmode=tk.EXTENDED)
        self.excluded_listbox.grid(row=0, column=0, sticky="ew", rowspan=3)
        exclude_sb = ttk.Scrollbar(exclude_frame, orient="vertical", command=self.excluded_listbox.yview)
        self.excluded_listbox.config(yscrollcommand=exclude_sb.set)
        exclude_sb.grid(row=0, column=1, sticky="ns", rowspan=3)

        exclude_btn_frame = ttk.Frame(exclude_frame)
        exclude_btn_frame.grid(row=0, column=2, sticky="ns", padx=(5, 0))
        ttk.Button(exclude_btn_frame, text="➕ 添加", command=self._add_excluded_folder, width=12).pack(side=tk.TOP, pady=(0, 2))
        ttk.Button(exclude_btn_frame, text="➖ 移除", command=self._remove_excluded_folder, width=12).pack(side=tk.TOP, pady=2)
        ttk.Button(exclude_btn_frame, text="🗑️ 清空", command=self._clear_all_excluded, width=12).pack(side=tk.TOP, pady=2)

        ttk.Label(exclude_frame, text="提示：设置新的基础文件夹时，排除列表将自动清空",
                  foreground="gray", font=('Microsoft YaHei', 8)).grid(row=3, column=0, sticky="w", pady=(2, 0))

    # ---------------------------------------------------------------------
    # 单文件夹检查 - 结果 UI
    # ---------------------------------------------------------------------
    def _init_check_result_ui(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        columns = ("folder_path", "file_name", "folder_code", "file_code", "issue_type", "detail")
        self.check_tree = ttk.Treeview(parent, columns=columns, show='headings', selectmode='extended')

        column_config = {
            "folder_path": ("文件夹路径", 300),
            "file_name": ("文件名", 200),
            "folder_code": ("文件夹番号", 100),
            "file_code": ("文件番号", 100),
            "issue_type": ("问题类型", 100),
            "detail": ("详细说明", 250)
        }
        for col, (text, width) in column_config.items():
            self.check_tree.heading(col, text=text, command=lambda c=col: self._sort_tree(c))
            self.check_tree.column(col, width=width, minwidth=50, stretch=False, anchor='w')

        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.check_tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=self.check_tree.xview)
        self.check_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.check_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.context_menu = tk.Menu(self.check_tree, tearoff=0)
        self.context_menu.add_command(label="复制路径", command=self._copy_path)
        self.context_menu.add_command(label="复制文件名", command=self._copy_filename)
        self.context_menu.add_command(label="复制文件夹番号", command=self._copy_folder_code)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="在网址抓取中搜索番号", command=self._search_code_in_webgrab)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="在资源管理器中打开", command=self._open_in_explorer)
        self.check_tree.bind("<Button-3>", self._show_context_menu)

        # 标签颜色
        self.check_tree.tag_configure('mismatch', background='#ffcccc')
        self.check_tree.tag_configure('warning', background='#ffffcc')
        self.check_tree.tag_configure('missing', background='#ccddff')
        self.check_tree.tag_configure('error', background='#ffaaaa')

    # ---------------------------------------------------------------------
    # 双文件夹比对 - 配置 UI
    # ---------------------------------------------------------------------
    def _init_compare_config_ui(self, parent: ttk.LabelFrame):
        parent.columnconfigure(1, weight=1)

        info_text = (
            "本功能用于对比原视频文件夹(A)和已转码文件夹(B)，检查转码完成情况。\n"
            "• 文件名完全相同  -> 已转码\n"
            "• B 中文件名仅多出数字后缀（xxx-1、xxx~2、xxx (1)、xxx - 副本(2) 等）-> 已转码\n"
            "• 前置文件名 + 番号相同，或仅番号相同（用 pattern_matcher 抽取）-> 已转码\n"
            "  例：A“[站]abc-123 标题” 对 B“[站]abc-123.mp4” / “abc-123.mp4”\n"
            "• 其余 A 中有、B 中没有的文件 -> 未转码，列入下方\"未转码文件\"列表\n"
            "未转码列表按资源管理器样式显示属性，可多选，并可直接把文件拖到播放器/转码软件等其它程序。"
        )
        ttk.Label(parent, text=info_text, justify=tk.LEFT, wraplength=1100).grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        # 扫描选项
        opt_frame = ttk.Frame(parent)
        opt_frame.grid(row=3, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 5))
        ttk.Checkbutton(opt_frame, text="包含子文件夹（递归扫描 A、B）",
                        variable=self.compare_recursive_var).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Checkbutton(opt_frame, text="仅比对视频文件",
                        variable=self.compare_video_only_var).pack(side=tk.LEFT)

        # 原视频文件夹（A）—— 点击输入框可下拉选择最近用过的路径
        ttk.Label(parent, text="原视频文件夹(A):", width=15).grid(row=1, column=0, sticky="w", padx=5)
        self.source_var = tk.StringVar()
        self.source_entry = HistoryPopupEntry(
            parent, self.source_var,
            history_provider=lambda: self.source_history,
            on_choose=lambda _v: self._remember_compare_path('source', self.source_var.get()))
        self.source_entry.grid(row=1, column=1, sticky="ew", padx=5)
        self.source_entry.bind(
            '<Return>',
            lambda e: self._remember_compare_path('source', self.source_var.get()), add='+')
        ttk.Button(parent, text="浏览...", command=self._browse_source_folder).grid(row=1, column=2, sticky="e")

        # 已转码文件夹（B）—— 同样支持历史下拉
        ttk.Label(parent, text="已转码文件夹(B):", width=15).grid(row=2, column=0, sticky="w", padx=5)
        self.target_var = tk.StringVar()
        self.target_entry = HistoryPopupEntry(
            parent, self.target_var,
            history_provider=lambda: self.target_history,
            on_choose=lambda _v: self._remember_compare_path('target', self.target_var.get()))
        self.target_entry.grid(row=2, column=1, sticky="ew", padx=5)
        self.target_entry.bind(
            '<Return>',
            lambda e: self._remember_compare_path('target', self.target_var.get()), add='+')
        ttk.Button(parent, text="浏览...", command=self._browse_target_folder).grid(row=2, column=2, sticky="e")

        # 一键选择按钮
        sel_frame = ttk.Frame(parent)
        sel_frame.grid(row=4, column=0, columnspan=3, sticky="w", padx=5, pady=5)
        ttk.Button(sel_frame, text="一键选择已转码文件", command=self._select_matched_files, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(sel_frame, text="一键选择未转码文件", command=self._select_unmatched_files, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(sel_frame, text="清空对比结果", command=self._clear_compare_results, width=15).pack(side=tk.LEFT, padx=5)

    # ---------------------------------------------------------------------
    # 双文件夹比对 - 结果 UI
    # ---------------------------------------------------------------------
    def _init_compare_result_ui(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.compare_inner = inner = ttk.Notebook(parent)
        inner.grid(row=0, column=0, sticky="nsew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        # Tab 1: 匹配结果
        matched_frame = ttk.Frame(inner)
        inner.add(matched_frame, text="匹配结果")
        matched_cols = ('source_file', 'target_file', 'status', 'note')
        self.matched_tree = ttk.Treeview(matched_frame, columns=matched_cols, show='headings')
        self.matched_tree.heading('source_file', text='原视频文件名(A)')
        self.matched_tree.heading('target_file', text='已转码文件名(B)')
        self.matched_tree.heading('status', text='状态')
        self.matched_tree.heading('note', text='备注')
        self.matched_tree.column('source_file', width=350)
        self.matched_tree.column('target_file', width=350)
        self.matched_tree.column('status', width=100, anchor=tk.CENTER)
        self.matched_tree.column('note', width=260)
        matched_scroll = ttk.Scrollbar(matched_frame, orient=tk.VERTICAL, command=self.matched_tree.yview)
        self.matched_tree.configure(yscrollcommand=matched_scroll.set)
        matched_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.matched_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.matched_tree.tag_configure('perfect', foreground='green')
        self.matched_tree.tag_configure('suffix', foreground='#b36b00')
        self.matched_tree.tag_configure('code', foreground='#1565c0')

        # Tab 2: 未转码文件（A 中有、B 中无）—— 资源管理器式列 + 多选 + 可拖拽
        unmatched_frame = ttk.Frame(inner)
        inner.add(unmatched_frame, text="未转码文件（A有B无）")
        unmatched_frame.columnconfigure(0, weight=1)
        unmatched_frame.rowconfigure(1, weight=1)

        # 工具栏：将选中的未转码视频加入"视频剪辑转码"的待转码列表
        unmatched_toolbar = ttk.Frame(unmatched_frame)
        unmatched_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        self.add_to_transcode_btn = ttk.Button(
            unmatched_toolbar, text="➕ 添加选中到转码列表",
            command=self._add_selected_to_transcode)
        self.add_to_transcode_btn.pack(side=tk.LEFT, padx=2)
        self.add_all_to_transcode_btn = ttk.Button(
            unmatched_toolbar, text="📥 添加全部文件",
            command=self._add_all_to_transcode)
        self.add_all_to_transcode_btn.pack(side=tk.LEFT, padx=2)
        ttk.Label(unmatched_toolbar, text="（选中后可批量加入转码队列；“添加全部文件”加入当前所有未转码文件）",
                  foreground="gray").pack(side=tk.LEFT, padx=4)

        self.unmatched_tree = ttk.Treeview(
            unmatched_frame,
            columns=self.UNMATCHED_COLUMNS,
            displaycolumns=self.UNMATCHED_COLUMNS[:7],  # 完整路径列不显示，仅内部使用
            show='headings',
            selectmode='extended',                       # 支持 Ctrl / Shift 多选
        )
        for col in self.UNMATCHED_COLUMNS:
            text, width, anchor = self.UNMATCHED_COLUMN_CONFIG[col]
            self.unmatched_tree.heading(
                col, text=text, command=lambda c=col: self._sort_unmatched(c))
            self.unmatched_tree.column(col, width=width, minwidth=60,
                                       stretch=False, anchor=anchor)
        unmatched_vsb = ttk.Scrollbar(unmatched_frame, orient=tk.VERTICAL,
                                      command=self.unmatched_tree.yview)
        unmatched_hsb = ttk.Scrollbar(unmatched_frame, orient=tk.HORIZONTAL,
                                      command=self.unmatched_tree.xview)
        self.unmatched_tree.configure(yscrollcommand=unmatched_vsb.set,
                                      xscrollcommand=unmatched_hsb.set)
        self.unmatched_tree.grid(row=1, column=0, sticky="nsew")
        unmatched_vsb.grid(row=1, column=1, sticky="ns")
        unmatched_hsb.grid(row=2, column=0, sticky="ew")
        self.unmatched_tree.tag_configure('unmatched', foreground='#c00000')
        self._init_unmatched_context_menu()
        # 等主窗口就绪后再注册拖拽源（tkdnd 需要主窗口已创建）
        self.frame.after(300, self._enable_drag_source)

        self.progress_label = ttk.Label(
            parent,
            text="准备就绪：未转码列表支持 Ctrl/Shift 多选，按住左键可直接把文件拖到其它软件",
            foreground="gray")
        self.progress_label.grid(row=1, column=0, sticky="w", padx=5, pady=(2, 0))

    # =====================================================================
    # 模式切换
    # =====================================================================
    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "check":
            self.compare_config_frame.grid_remove()
            self.check_config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
            self.run_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.stats_label.config(text="就绪")
        elif mode == "compare":
            self.check_config_frame.grid_remove()
            self.compare_config_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
            self.run_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.stats_label.config(text="A文件夹: 0个文件 | B文件夹: 0个文件 | 已转码: 0 | 未转码: 0")
        else:  # video
            self.check_config_frame.grid_remove()
            self.compare_config_frame.grid_remove()
            self.run_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)
            self.stats_label.config(
                text="视频文件比对：将两个视频分别拖入左右区域，进行属性对比与同步播放")

        # 显示区切换：视频比对使用独立面板，其余模式使用结果页签
        if mode == "video":
            self.result_notebook.grid_remove()
            self.video_compare_frame.grid(
                row=5, column=0, sticky="nsew", padx=10, pady=(5, 10))
        else:
            self.video_compare_frame.grid_remove()
            self.result_notebook.grid(
                row=5, column=0, sticky="nsew", padx=10, pady=(5, 10))
            # 只显示当前模式对应的结果页签，隐藏无关的页签，保持界面清晰
            self._apply_result_tabs(mode)

        # 离开视频比对模式时停止隐藏的播放器，避免后台继续播放
        if mode != "video":
            self._stop_all_players()

        # 切换模式时重置进度条
        self.progress_var.set(0)
        self.export_btn.config(state=tk.DISABLED)

    def _apply_result_tabs(self, mode: str):
        """根据当前模式仅显示对应的“结果”页签，隐藏另一个

        - 单文件夹检查 -> 仅显示“检查结果”
        - 双文件夹比对 -> 仅显示“比对结果”（其内部再分“匹配结果/未转码文件”）
        """
        nb = self.result_notebook
        if mode == "check":
            nb.tab(self.compare_result_frame, state='hidden')
            nb.tab(self.check_result_frame, state='normal')
            nb.select(self.check_result_frame)
        else:
            nb.tab(self.check_result_frame, state='hidden')
            nb.tab(self.compare_result_frame, state='normal')
            nb.select(self.compare_result_frame)

    # =====================================================================
    # 配置加载 / 保存
    # =====================================================================
    def _load_config(self):
        """从 app_config.json 加载两类模式的配置"""
        section = self.config_manager.get_section(self.CONFIG_SECTION, {})

        # 单文件夹检查配置
        self.folder_var.set(section.get("check_folder", ""))
        self.include_subfolders_var.set(section.get("check_include_subfolders", False))
        self.check_subtitle_var.set(section.get("check_check_subtitle", False))

        excluded_folders = section.get("check_excluded_folders", [])
        self.excluded_listbox.delete(0, tk.END)
        base_folder = section.get("check_folder", "")
        for path in excluded_folders:
            if os.path.exists(path):
                if base_folder and os.path.normpath(path) == os.path.normpath(base_folder):
                    continue
                self.excluded_listbox.insert(tk.END, path)

        # 双文件夹比对配置
        source_path = section.get("compare_source_folder", "")
        target_path = section.get("compare_target_folder", "")
        self.source_var.set(source_path)
        self.target_var.set(target_path)
        self.compare_recursive_var.set(section.get("compare_recursive", False))
        self.compare_video_only_var.set(section.get("compare_video_only", True))
        # A/B 文件夹输入框的历史记录
        self.source_history = [str(p) for p in (section.get("compare_source_history", []) or [])
                               if str(p).strip()][:self.HISTORY_MAX]
        self.target_history = [str(p) for p in (section.get("compare_target_history", []) or [])
                               if str(p).strip()][:self.HISTORY_MAX]
        # 当前路径也补进历史，保证下拉里能看到最近一次使用值
        if source_path:
            self._remember_compare_path('source', source_path, save=False)
        if target_path:
            self._remember_compare_path('target', target_path, save=False)
        if source_path:
            self.source_folder = Path(source_path)
        if target_path:
            self.target_folder = Path(target_path)
        self._vlc_path = section.get("vlc_path") or None

    def _save_config(self, show_tip: bool = False):
        """保存两类模式的配置到 app_config.json

        show_tip: 仅当用户主动点击“保存配置”按钮时才显示提示；
                  执行/对比等流程中的自动保存不弹提示（避免重复打扰）。
        """
        config = {
            "check_folder": self.folder_var.get(),
            "check_include_subfolders": self.include_subfolders_var.get(),
            "check_check_subtitle": self.check_subtitle_var.get(),
            "check_excluded_folders": list(self.excluded_listbox.get(0, tk.END)),
            "compare_source_folder": self.source_var.get(),
            "compare_target_folder": self.target_var.get(),
            "compare_recursive": self.compare_recursive_var.get(),
            "compare_video_only": self.compare_video_only_var.get(),
            "compare_source_history": list(self.source_history)[:self.HISTORY_MAX],
            "compare_target_history": list(self.target_history)[:self.HISTORY_MAX],
            "vlc_path": self._vlc_path or "",
        }
        self.config_manager.set_section(self.CONFIG_SECTION, config, auto_save=True)
        self.log("[文件夹检查] 配置已保存")
        if show_tip:
            self.show_toast("配置已保存", "success")

    # =====================================================================
    # 统一入口
    # =====================================================================
    def show_toast(self, message: str, level: str = 'info', duration: Optional[int] = None):
        """在屏幕右下角浮出提醒信息（替代频繁弹出的 messagebox）

        level: 'info' | 'success' | 'warning' | 'error'
        """
        if duration is None:
            duration = 5000 if level == 'error' else 3200
        try:
            ToastNotifier().notify(self.frame, message, level, duration)
        except Exception:
            self.log(f"[{level}] {message}")

    def _run(self):
        """根据当前模式执行对应逻辑"""
        mode = self.mode_var.get()
        if mode == "check":
            self._run_check()
        elif mode == "compare":
            self._start_compare()
        # video 模式通过拖入文件后自动读取，无需“开始执行”

    # =====================================================================
    # 单文件夹检查模式 - 逻辑（移植自 video_check_tab.py）
    # =====================================================================
    def _browse_folder(self):
        folder = filedialog.askdirectory(title="选择要检查的文件夹")
        if folder:
            self.folder_var.set(folder)
            self.excluded_listbox.delete(0, tk.END)
            self.log("[单文件夹检查] 基础文件夹已更改，排除列表已自动清空")

    def _add_excluded_folder(self):
        initial_dir = self.folder_var.get().strip()
        if initial_dir and os.path.isdir(initial_dir):
            folder = filedialog.askdirectory(title="选择要排除的文件夹", initialdir=initial_dir)
        else:
            folder = filedialog.askdirectory(title="选择要排除的文件夹")
        if not folder:
            return
        folder_normalized = os.path.normpath(folder)
        existing = list(self.excluded_listbox.get(0, tk.END))
        if folder_normalized in existing:
            self.show_toast("该文件夹已在排除列表中", "info")
            return
        base_folder = self.folder_var.get().strip()
        if base_folder:
            base_normalized = os.path.normpath(base_folder)
            if folder_normalized == base_normalized:
                self.show_toast("不能排除基础文件夹本身", "warning")
                return
            if not folder_normalized.startswith(base_normalized + os.sep):
                if not messagebox.askyesno("确认",
                    f"选择的文件夹:\n{folder_normalized}\n\n不在基础文件夹内:\n{base_normalized}\n\n确定要添加吗？"):
                    return
        self.excluded_listbox.insert(tk.END, folder_normalized)

    def _remove_excluded_folder(self):
        selected = self.excluded_listbox.curselection()
        if not selected:
            self.show_toast("请先选择要移除的文件夹", "info")
            return
        for index in reversed(selected):
            self.excluded_listbox.delete(index)

    def _clear_all_excluded(self):
        if self.excluded_listbox.size() == 0:
            return
        if messagebox.askyesno("确认", "确定要清空所有排除的文件夹吗？"):
            self.excluded_listbox.delete(0, tk.END)

    def _validate_path(self, path: str) -> bool:
        if not path:
            return False
        try:
            return os.path.exists(path)
        except Exception:
            return False

    def _get_all_folders(self, base_path: str, include_subfolders: bool) -> List[str]:
        folders = []
        base_path = os.path.normpath(base_path)
        try:
            exclude_tuple = self.excluded_listbox.get(0, tk.END)
            excluded_paths = [os.path.normpath(p) for p in exclude_tuple if p] if exclude_tuple else []
        except Exception as e:
            self.log(f"[单文件夹检查] 获取排除列表失败: {e}")
            excluded_paths = []

        for excl in excluded_paths:
            if base_path == excl or base_path.startswith(excl + os.sep):
                return []

        if not include_subfolders:
            if os.path.isdir(base_path):
                folders.append(base_path)
        else:
            try:
                for root, dirs, files in os.walk(base_path):
                    root_normalized = os.path.normpath(root)
                    is_excluded = False
                    for excl in excluded_paths:
                        if root_normalized == excl or root_normalized.startswith(excl + os.sep):
                            is_excluded = True
                            break
                    if is_excluded:
                        dirs[:] = []
                        continue
                    dirs[:] = [d for d in dirs if d and not d.startswith('.') and not d.startswith('$')]
                    folders.append(root)
            except Exception as e:
                self.log(f"[单文件夹检查] 遍历文件夹失败: {e}")
        return folders

    def _is_skip_folder(self, folder_name: str) -> bool:
        for marker in self.SKIP_FOLDER_MARKERS:
            if marker in folder_name:
                return True
        return False

    def _has_builtin_subtitle(self, video_filename: str) -> bool:
        name_no_ext = os.path.splitext(video_filename)[0]
        upper_name = name_no_ext.upper()
        for suffix in self.BUILTIN_SUBTITLE_SUFFIXES:
            if upper_name.endswith(suffix.upper()):
                return True
        return False

    def _check_folder(self, folder_path: str) -> List[Dict]:
        """检查单个文件夹，返回问题列表（使用 pattern_matcher 识别番号/编号）"""
        issues = []
        folder_name = os.path.basename(folder_path)

        if self._is_skip_folder(folder_name):
            self.log(f"[单文件夹检查] 跳过标记文件夹: {folder_path}")
            return []

        if not os.path.isdir(folder_path):
            return [{
                "folder_path": folder_path, "file_name": "", "folder_code": "未识别",
                "file_code": "", "issue_type": "访问错误", "detail": "路径不存在或不是文件夹"
            }]

        all_items = os.listdir(folder_path)
        if all_items is None:
            all_items = []
        visible_items = [item for item in all_items
                         if not item.startswith('.') and not item.startswith('$')]
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

        # 分类文件夹：无任何文件，且至少有一个子文件夹
        if file_count == 0 and subdir_count >= 1:
            self.log(f"[单文件夹检查] 跳过分类文件夹: {folder_path}")
            return []

        # 获取文件夹番号（使用 pattern_matcher）
        if self.matcher is not None:
            folder_code_info = self.matcher.extract_code(folder_name)
            folder_code = folder_code_info['full'] if folder_code_info else None
            folder_code_display = folder_code_info['canonical'] if folder_code_info else "未识别"
        else:
            folder_code = None
            folder_code_display = "未识别"

        try:
            files = os.listdir(folder_path)
            if files is None:
                files = []
        except PermissionError:
            return [{
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "访问错误", "detail": "无法访问该文件夹（权限不足）"
            }]
        except Exception as e:
            return [{
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "访问错误", "detail": f"读取文件夹失败: {str(e)}"
            }]

        video_files, image_files, subtitle_files = [], [], []
        for file in files:
            if file is None or file.startswith('.'):
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in self.VIDEO_EXTENSIONS:
                video_files.append(file)
            elif ext in self.IMAGE_EXTENSIONS:
                image_files.append(file)
            elif ext in self.SUBTITLE_EXTENSIONS:
                subtitle_files.append(file)

        if not video_files:
            issues.append({
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "缺失视频",
                "detail": f"文件夹中未找到视频文件（支持格式: {', '.join(self.VIDEO_EXTENSIONS)}）"
            })

        if not image_files:
            issues.append({
                "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                "file_code": "", "issue_type": "缺失图片",
                "detail": f"文件夹中未找到封面图片（支持格式: {', '.join(self.IMAGE_EXTENSIONS)}）"
            })

        if self.check_subtitle_var.get():
            videos_need_subtitle_check, builtin_subtitle_videos = [], []
            for video_file in video_files:
                if self._has_builtin_subtitle(video_file):
                    builtin_subtitle_videos.append(video_file)
                else:
                    videos_need_subtitle_check.append(video_file)
            if videos_need_subtitle_check and not subtitle_files:
                skipped_info = ""
                if builtin_subtitle_videos:
                    skipped_names = ", ".join(builtin_subtitle_videos)
                    skipped_info = f"；以下视频因文件名含字幕标记已跳过检查: {skipped_names}"
                issues.append({
                    "folder_path": folder_path, "file_name": "", "folder_code": folder_code_display,
                    "file_code": "", "issue_type": "缺失字幕",
                    "detail": f"文件夹中有 {len(videos_need_subtitle_check)} 个视频需要字幕，但未找到字幕文件（.srt）{skipped_info}"
                })

        for video_file in video_files:
            file_name_no_ext = os.path.splitext(video_file)[0]
            file_code_info = self.matcher.extract_code(file_name_no_ext) if self.matcher else None

            if not file_code_info:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": folder_code_display, "file_code": "未识别",
                    "issue_type": "命名不规范", "detail": "视频文件名未能识别出番号/编号"
                })
                continue

            file_code = file_code_info['full']
            file_code_display = file_code_info['canonical']

            if not folder_code:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": "未识别", "file_code": file_code_display,
                    "issue_type": "命名不匹配",
                    "detail": f"文件夹名未识别番号，但视频文件识别为 {file_code_display}"
                })
                continue

            if self.matcher is not None:
                match_level = self.matcher.is_strict_match(file_code_info, folder_code_info)
            else:
                match_level = 0

            if match_level == 0:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": folder_code_display, "file_code": file_code_display,
                    "issue_type": "番号不匹配",
                    "detail": f"文件夹番号 {folder_code_display} 与文件番号 {file_code_display} 不匹配"
                })
            elif match_level == 1:
                issues.append({
                    "folder_path": folder_path, "file_name": video_file,
                    "folder_code": folder_code_display, "file_code": file_code_display,
                    "issue_type": "疑似不匹配",
                    "detail": f"文件夹番号 {folder_code_display} 与文件番号 {file_code_display} 部分匹配但不完全一致"
                })
        return issues

    def _run_check(self):
        base_folder = self.folder_var.get().strip()
        if not base_folder:
            self.show_toast("请先选择要检查的文件夹", "warning")
            return
        if not self._validate_path(base_folder):
            self.show_toast(f"路径不存在或无法访问:\n{base_folder}", "error")
            return

        excluded_paths = list(self.excluded_listbox.get(0, tk.END))
        invalid_excluded = [p for p in excluded_paths if not os.path.exists(p)]
        if invalid_excluded:
            if messagebox.askyesno("路径不存在", f"以下排除路径不存在，是否从列表中移除？\n\n" + "\n".join(invalid_excluded)):
                for path in invalid_excluded:
                    idx = list(self.excluded_listbox.get(0, tk.END)).index(path)
                    self.excluded_listbox.delete(idx)
            else:
                return

        for item in self.check_tree.get_children():
            self.check_tree.delete(item)
        self.check_results.clear()
        self.export_btn.config(state=tk.DISABLED)
        self.sort_column = None

        self.run_btn.config(state=tk.DISABLED, text="检查中...")
        self.save_btn.config(state=tk.DISABLED)
        self.stats_label.config(text="正在扫描文件夹...")
        self.frame.update()

        try:
            include_sub = self.include_subfolders_var.get()
            folders = self._get_all_folders(base_folder, include_sub)
            total = len(folders)
            if total == 0:
                self.show_toast("未找到需要检查的文件夹", "info")
                self.stats_label.config(text="就绪")
                return

            all_issues = []
            for idx, folder in enumerate(folders, 1):
                progress = (idx / total) * 100
                self.progress_var.set(progress)
                self.stats_label.config(text=f"正在检查: {idx}/{total} - {os.path.basename(folder)}")
                self.frame.update()
                all_issues.extend(self._check_folder(folder))

            self.check_results = all_issues
            self._display_check_results(all_issues)

            issue_count = len(all_issues)
            folder_with_issues = len(set(i['folder_path'] for i in all_issues))
            excluded_count = len(excluded_paths)
            self.stats_label.config(
                text=f"检查完成: 共 {total} 个文件夹（已排除 {excluded_count} 个），发现 {issue_count} 个问题（涉及 {folder_with_issues} 个文件夹）"
            )
            if all_issues:
                self.export_btn.config(state=tk.NORMAL)
            else:
                self.show_toast("恭喜！未发现命名不匹配或文件缺失的问题。", "success")
        except Exception as e:
            self.show_toast(f"检查过程中发生错误:\n{str(e)}", "error")
            self.stats_label.config(text=f"检查失败: {str(e)}")
        finally:
            self.run_btn.config(state=tk.NORMAL, text="▶ 开始执行")
            self.save_btn.config(state=tk.NORMAL)
            self.progress_var.set(0)

    def _sort_tree(self, col: str):
        data = [(self.check_tree.set(child, col), child) for child in self.check_tree.get_children('')]
        try:
            float(data[0][0])
            data.sort(key=lambda x: float(x[0]), reverse=self.sort_reverse)
        except (ValueError, IndexError):
            data.sort(key=lambda x: x[0].lower(), reverse=self.sort_reverse)
        for index, (_, item) in enumerate(data):
            self.check_tree.move(item, '', index)
        self.sort_reverse = not self.sort_reverse
        self.sort_column = col
        for column in self.check_tree["columns"]:
            text = self.check_tree.heading(column, "text").replace(" ▲", "").replace(" ▼", "")
            if column == col:
                text += " ▼" if self.sort_reverse else " ▲"
            self.check_tree.heading(column, text=text)

    def _display_check_results(self, results: List[Dict]):
        priority = {"番号不匹配": 1, "疑似不匹配": 2, "命名不规范": 3,
                   "缺失视频": 4, "缺失图片": 5, "缺失字幕": 6, "访问错误": 7}
        sorted_results = sorted(results, key=lambda x: priority.get(x['issue_type'], 99))
        for issue in sorted_results:
            tag = ''
            if issue['issue_type'] in ['番号不匹配']:
                tag = 'mismatch'
            elif issue['issue_type'] in ['疑似不匹配', '命名不规范']:
                tag = 'warning'
            elif issue['issue_type'] in ['缺失视频', '缺失图片', '缺失字幕']:
                tag = 'missing'
            elif issue['issue_type'] == '访问错误':
                tag = 'error'
            self.check_tree.insert('', tk.END, values=(
                issue['folder_path'], issue['file_name'], issue['folder_code'],
                issue['file_code'], issue['issue_type'], issue['detail']
            ), tags=(tag,))

    # =====================================================================
    # 单文件夹检查模式 - 导出 / 右键菜单
    # =====================================================================
    def _export_check_csv(self):
        if not self.check_results:
            return False
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialfile=f"视频检查结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not file_path:
            return False
        try:
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['文件夹路径', '文件名', '文件夹番号', '文件番号', '问题类型', '详细说明'])
                for issue in self.check_results:
                    writer.writerow([
                        issue['folder_path'], issue['file_name'], issue['folder_code'],
                        issue['file_code'], issue['issue_type'], issue['detail']
                    ])
            self.show_toast(f"结果已导出到:\n{file_path}", "success")
            return True
        except Exception as e:
            self.show_toast(f"导出失败:\n{str(e)}", "error")
            return False

    def _export_compare_csv(self):
        if not self.compare_results:
            return False
        # 按当前查看的子页签导出：未转码文件（含完整属性）或匹配结果
        export_unmatched = bool(self.unmatched_rows)
        try:
            current = self.compare_inner.select()
            title = self.compare_inner.tab(current, 'text')
            export_unmatched = ('未转码' in title) and bool(self.unmatched_rows)
        except Exception:
            pass

        prefix = "未转码文件" if export_unmatched else "文件比对结果"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialfile=f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not file_path:
            return False
        try:
            with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if export_unmatched:
                    writer.writerow(['文件名', '文件类型', '修改日期', '大小',
                                     '帧宽度', '帧高度', '数据速率', '完整路径'])
                    records = self._selected_records() or list(self.unmatched_rows.values())
                    for rec in records:
                        writer.writerow([
                            rec.get('name', ''),
                            rec.get('file_type', ''),
                            format_timestamp(rec.get('modified', 0)),
                            format_file_size(rec.get('size', 0)),
                            rec.get('frame_width') or '',
                            rec.get('frame_height') or '',
                            format_bitrate(rec.get('bitrate')),
                            rec.get('path', ''),
                        ])
                else:
                    writer.writerow(['原视频文件名(A)', '已转码文件名(B)', '状态', '备注', '原文件路径'])
                    for r in self.compare_results:
                        writer.writerow([
                            r.get('source_name', ''),
                            r.get('target_name', '') or '-',
                            '已转码' if r.get('status') in ('perfect', 'suffix', 'code')
                            else '未转码',
                            r.get('note', ''),
                            str(r.get('source_file', ''))
                        ])
            self.show_toast(f"结果已导出到:\n{file_path}", "success")
            return True
        except Exception as e:
            self.show_toast(f"导出失败:\n{str(e)}", "error")
            return False

    def _export_results(self):
        if self.mode_var.get() == "check":
            self._export_check_csv()
        else:
            self._export_compare_csv()

    def _show_context_menu(self, event):
        item = self.check_tree.identify_row(event.y)
        if not item:
            return
        # 右键已选中的多行时保留当前多选；否则仅选中该行
        selection = self.check_tree.selection()
        if item not in selection:
            self.check_tree.selection_set(item)
        self.context_menu.post(event.x_root, event.y_root)

    def _copy_path(self):
        selected = self.check_tree.selection()
        if selected:
            path = self.check_tree.item(selected[0])['values'][0]
            self.frame.clipboard_clear()
            self.frame.clipboard_append(path)
            self.frame.update()

    def _copy_filename(self):
        selected = self.check_tree.selection()
        if selected:
            filename = self.check_tree.item(selected[0])['values'][1]
            if filename:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(filename)
                self.frame.update()

    def _copy_folder_code(self):
        selected = self.check_tree.selection()
        if selected:
            folder_code = self.check_tree.item(selected[0])['values'][2]
            if folder_code:
                self.frame.clipboard_clear()
                self.frame.clipboard_append(folder_code)
                self.frame.update()

    def _open_in_explorer(self):
        selected = self.check_tree.selection()
        if selected:
            path = self.check_tree.item(selected[0])['values'][0]
            if os.path.exists(path):
                os.startfile(path)
            else:
                self.show_toast("路径不存在或无法访问", "warning")

    def _get_webgrab_tab(self):
        """获取 web_grab 模块实例（不存在则返回 None）"""
        controller = self.controller
        if not controller:
            return None
        return getattr(controller.tabs, 'get', lambda k: None)('web_grab')

    def _search_code_in_webgrab(self):
        """把选中行（可多选）的番号发送到“网址抓取”模块，分别创建搜索页签（不切换主页面签）

        番号优先取“文件夹番号”，为空时回退到“文件番号”；多个不同番号会创建多个搜索页签。
        """
        selected = self.check_tree.selection()
        if not selected:
            return
        webgrab = self._get_webgrab_tab()
        if webgrab is None or not hasattr(webgrab, 'search_list_by_keyword'):
            self.show_toast("未找到“网址抓取”模块，无法创建搜索页签", "error")
            return

        # 收集番号（文件夹番号优先，回退到文件番号），去重并保持顺序
        codes: List[str] = []
        seen = set()
        for item in selected:
            values = self.check_tree.item(item)['values']
            code = (str(values[2] or '').strip() or str(values[3] or '').strip())
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
        if not codes:
            self.show_toast("选中行没有可用的番号（文件夹番号/文件番号为空）", "warning")
            return

        ok = 0
        for code in codes:
            try:
                if webgrab.search_list_by_keyword(code):
                    ok += 1
                else:
                    self.log(f"[文件夹检查] 网址抓取缺少基础网址，未创建搜索页签：{code}")
            except Exception as e:
                self.log(f"[文件夹检查] 在网址抓取创建搜索页签失败：{code} - {e}")
        if ok == 0:
            self.show_toast("未能创建搜索页签：请先在“网址抓取”列表层打开一个站点", "warning")
        else:
            self.show_toast(f"已在“网址抓取”为 {ok} 个番号创建搜索页签", "success")

    # =====================================================================
    # 双文件夹比对模式 - 逻辑（移植自 file_compare_tab.py）
    # =====================================================================
    def _remember_compare_path(self, kind: str, path: str, save: bool = True):
        """把路径记入 A/B 文件夹的历史（去重、最新在前、最多 HISTORY_MAX 条）

        kind: 'source'（原视频文件夹 A）/ 'target'（已转码文件夹 B）
        """
        path = (path or '').strip()
        if not path:
            return
        history = self.source_history if kind == 'source' else self.target_history
        low = path.lower()
        history[:] = [h for h in history if str(h).strip() and str(h).lower() != low]
        history.insert(0, path)
        del history[self.HISTORY_MAX:]
        if save:
            self._save_config()

    def _browse_source_folder(self):
        directory = filedialog.askdirectory(title="选择原视频文件夹(A)")
        if directory:
            self.source_var.set(directory)
            self.source_folder = Path(directory)
            self._remember_compare_path('source', directory)

    def _browse_target_folder(self):
        directory = filedialog.askdirectory(title="选择已转码文件夹(B)")
        if directory:
            self.target_var.set(directory)
            self.target_folder = Path(directory)
            self._remember_compare_path('target', directory)

    def _start_compare(self):
        source_path_str = self.source_var.get().strip()
        target_path_str = self.target_var.get().strip()
        if not source_path_str or not target_path_str:
            self.show_toast("请设置原视频文件夹和已转码文件夹", "warning")
            return
        # 手动输入或浏览得到的路径都记入历史
        self._remember_compare_path('source', source_path_str)
        self._remember_compare_path('target', target_path_str)
        self.source_folder = Path(source_path_str)
        self.target_folder = Path(target_path_str)
        if not self.source_folder.exists() or not self.target_folder.exists():
            self.show_toast("文件夹路径不存在", "warning")
            return

        self._clear_compare_results()
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.is_running = True

        self.operation_thread = threading.Thread(target=self._compare_thread, daemon=True)
        self.operation_thread.start()

    def _stop_compare(self):
        self.is_running = False
        self.props_running = False
        self.log("[双文件夹比对] 正在停止...")
        self.stop_btn.config(state=tk.DISABLED)

    def _compare_thread(self):
        try:
            self.log("=" * 60)
            self.log("[双文件夹比对] 开始对比")
            self.log(f"原视频文件夹(A): {self.source_folder}")
            self.log(f"已转码文件夹(B): {self.target_folder}")
            self.log("=" * 60)

            recursive = self.compare_recursive_var.get()
            video_only = self.compare_video_only_var.get()
            scope = "视频文件" if video_only else "全部文件"

            self._update_progress(10, f"正在扫描原视频文件夹(A) - {scope}...")
            source_files = self._collect_files(self.source_folder, recursive, video_only)
            source_count = len(source_files)
            if not self.is_running:
                return
            self.log(f"[双文件夹比对] A文件夹发现 {source_count} 个文件")

            self._update_progress(30, f"正在扫描已转码文件夹(B) - {scope}...")
            target_files = self._collect_files(self.target_folder, recursive, video_only)
            target_count = len(target_files)
            if not self.is_running:
                return
            self.log(f"[双文件夹比对] B文件夹发现 {target_count} 个文件")

            self._update_progress(50, f"扫描完成 A:{source_count} B:{target_count}，开始匹配...")

            index = self._build_target_index(target_files)
            used: Set[str] = set()          # 已被匹配占用的 B 文件，保证一对一
            perfect = 0                     # 文件名完全相同
            suffix = 0                      # B 侧带数字后缀
            code_matched = 0                # 番号 / 前置文件名+番号 匹配
            unmatched = 0                   # A 中有、B 中无
            total = max(len(source_files), 1)

            for idx, source_file in enumerate(source_files, 1):
                if not self.is_running:
                    break
                if idx % 20 == 0 or idx == len(source_files):
                    self._update_progress(
                        50 + int(idx / total * 45),
                        f"正在匹配 {idx}/{len(source_files)}: {source_file.name[:40]}...")
                result = self._match_single_file(source_file, index, used)
                self.compare_results.append(result)
                if result['status'] == 'perfect':
                    perfect += 1
                    self.matched_files.append(result)
                elif result['status'] == 'suffix':
                    suffix += 1
                    self.matched_files.append(result)
                elif result['status'] == 'code':
                    code_matched += 1
                    self.matched_files.append(result)
                else:
                    unmatched += 1
                    self.unmatched_files.append(source_file)
                if idx % 10 == 0 or idx == len(source_files):
                    self._update_stats(source_count, target_count, perfect, suffix,
                                       code_matched, unmatched)

            self._update_progress(100, "匹配完成")
            self.frame.after(0, self._after_compare_ui)
            self.log("\n" + "=" * 60)
            self.log(f"[双文件夹比对] 对比完成: 完全匹配 {perfect}, 数字后缀匹配 {suffix}, "
                     f"番号匹配 {code_matched}, 未匹配 {unmatched}")
            self.log("=" * 60)
            self.frame.after(0, lambda: self._compare_completed(perfect, suffix,
                                                                 code_matched, unmatched))
        except Exception as e:
            self.log(f"[双文件夹比对] 错误: {e}")
            import traceback
            self.log(traceback.format_exc())
            self.frame.after(0, lambda: self.show_toast(f"对比过程出错: {e}", "error"))
            self.frame.after(0, self._reset_compare_ui)

    def _collect_files(self, folder: Path, recursive: bool, video_only: bool) -> List[Path]:
        """扫描文件夹中的文件（可选递归 / 仅视频）"""
        files: List[Path] = []
        exts = self.VIDEO_EXTENSIONS if video_only else None
        try:
            iterator = folder.rglob('*') if recursive else folder.iterdir()
            for item in iterator:
                try:
                    if not item.is_file():
                        continue
                    name = item.name
                    if name.startswith('.') or name.startswith('~$'):
                        continue
                    if exts is not None and item.suffix.lower() not in exts:
                        continue
                    files.append(item)
                except (OSError, PermissionError):
                    continue
        except Exception as e:
            self.log(f"[双文件夹比对] 访问文件夹失败 {folder}: {e}")
        files.sort(key=lambda p: str(p).lower())
        return files

    def _build_target_index(self, files: List[Path]) -> Tuple[Dict[str, Path], ...]:
        """为 B 文件夹建立五级索引

        Returns:
            (按完整文件名, 按主文件名(不含扩展名), 按逐层去数字后缀后的主名,
             按番号, 按「前置文件名+番号」)
        """
        by_full: Dict[str, Path] = {}
        by_stem: Dict[str, Path] = {}
        by_base: Dict[str, Path] = {}
        by_code: Dict[str, Path] = {}
        by_prefix_code: Dict[Tuple[str, str], Path] = {}
        for f in files:
            full = f.name.lower()
            stem = f.stem.lower()
            by_full.setdefault(full, f)
            by_stem.setdefault(stem, f)
            # 逐层剥离得到的每一种中间名都要建索引。
            # 只索引“剥到底”的结果会漏配：例如 B 侧 "[x]arbb-041(1)~1" 会被
            # 一路剥成 "[x]arbb"，与 A 侧 "[x]arbb-041(1)" 对不上而误判为未转码。
            for base in strip_numeric_suffix_chain(stem):
                if base != stem:
                    by_base.setdefault(base, f)
            # 番号 / 前置文件名 + 番号
            prefix, code = extract_name_parts(stem, self.matcher)
            if code:
                by_code.setdefault(code, f)
                by_prefix_code.setdefault((prefix, code), f)
        return by_full, by_stem, by_base, by_code, by_prefix_code

    @staticmethod
    def _take_candidate(candidate: Optional[Path], used: Set[str]) -> bool:
        """尝试占用 B 侧文件（保证一个 B 文件只匹配一个 A 文件）"""
        if candidate is None:
            return False
        key = str(candidate).lower()
        if key in used:
            return False
        used.add(key)
        return True

    def _match_single_file(self, source_file: Path, index, used: Set[str]) -> Dict:
        """按规则匹配单个 A 文件

        1. 文件名完全相同（含大小写不敏感）
        2. 主文件名相同（仅扩展名/封装格式不同）
        3. B 侧文件名只多出数字后缀（xxx-1、xxx~2、xxx (1) 等）
        4. 前置文件名 + 番号 相同（用 pattern_matcher 抽取番号）
        5. 仅番号相同（如 A: [站]abc-123 标题 / B: abc-123.mp4）
        """
        result = {
            'source_file': source_file, 'target_file': None, 'status': 'unmatched',
            'note': 'B 文件夹中未找到对应文件',
            'source_name': source_file.name, 'target_name': None
        }
        full = source_file.name.lower()
        stem = source_file.stem.lower()
        by_full, by_stem, by_base, by_code, by_prefix_code = index

        candidate = by_full.get(full)
        if self._take_candidate(candidate, used):
            result.update(target_file=candidate, target_name=candidate.name,
                          status='perfect', note='文件名完全相同')
            return result

        candidate = by_stem.get(stem)
        if self._take_candidate(candidate, used):
            result.update(target_file=candidate, target_name=candidate.name,
                          status='perfect',
                          note=f'文件名相同（格式不同: {candidate.suffix}）')
            return result

        candidate = by_base.get(stem)
        if self._take_candidate(candidate, used):
            result.update(target_file=candidate, target_name=candidate.name,
                          status='suffix',
                          note=f'数字后缀匹配（B: {candidate.name}）')
            return result

        # 4. 前置文件名 + 番号 都相同（最强的番号级匹配）
        a_prefix, a_code = extract_name_parts(stem, self.matcher)
        if a_code:
            candidate = by_prefix_code.get((a_prefix, a_code))
            if self._take_candidate(candidate, used):
                result.update(target_file=candidate, target_name=candidate.name,
                              status='code',
                              note=f'番号+前缀匹配（番号 {a_code}，前缀“{a_prefix or "无"}”）')
                return result

            # 5. 仅番号相同
            candidate = by_code.get(a_code)
            if self._take_candidate(candidate, used):
                result.update(target_file=candidate, target_name=candidate.name,
                              status='code',
                              note=f'番号匹配（{a_code}，B: {candidate.name}）')
                return result

        return result

    def _update_compare_result_ui(self):
        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)
        for item in self.unmatched_tree.get_children():
            self.unmatched_tree.delete(item)
        self.unmatched_rows.clear()
        for result in self.matched_files:
            status = result['status']
            if status == 'perfect':
                tags, status_text = ('perfect',), "✓ 已转码"
            elif status == 'code':
                tags, status_text = ('code',), "✓ 已转码 · 番号匹配"
            else:
                tags, status_text = ('suffix',), "✓ 已转码 · 名称带后缀"
            self.matched_tree.insert('', tk.END, values=(
                result['source_name'],
                result['target_name'] if result['target_name'] else '-',
                status_text, result['note']
            ), tags=tags)
        for source_file in self.unmatched_files:
            record = self._build_file_record(source_file)
            item_id = self.unmatched_tree.insert(
                '', tk.END, values=self._record_to_values(record), tags=('unmatched',))
            self.unmatched_rows[item_id] = record

    # ---------------------------------------------------------------------
    # 文件记录（列表行数据）
    # ---------------------------------------------------------------------
    def _build_file_record(self, path: Path) -> Dict:
        """收集一个文件的展示属性（媒体属性稍后由后台线程补全）"""
        try:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
        except Exception:
            size, mtime = 0, 0
        return {
            'path': str(path),
            'name': path.name,
            'file_type': shell_file_type_name(str(path)) or self._fallback_file_type(path),
            'modified': mtime,
            'size': size,
            'frame_width': None,
            'frame_height': None,
            'bitrate': None,
        }

    def _fallback_file_type(self, path: Path) -> str:
        """Shell 取不到类型时按扩展名给出描述"""
        ext = path.suffix.lower()
        if not ext:
            return '文件'
        tag = ext[1:].upper()
        if ext in self.VIDEO_EXTENSIONS:
            return f'{tag} 视频'
        if ext in self.IMAGE_EXTENSIONS:
            return f'{tag} 图像'
        if ext in self.SUBTITLE_EXTENSIONS:
            return f'{tag} 字幕'
        return f'{tag} 文件'

    def _record_to_values(self, record: Dict) -> Tuple:
        return (
            record['name'],
            record['file_type'],
            format_timestamp(record['modified']),
            format_file_size(record['size']),
            record['frame_width'] if record['frame_width'] else '',
            record['frame_height'] if record['frame_height'] else '',
            format_bitrate(record['bitrate']),
            record['path'],
        )

    # ---------------------------------------------------------------------
    # 视频属性后台加载（帧宽度 / 帧高度 / 数据速率）
    # ---------------------------------------------------------------------
    def _after_compare_ui(self):
        """比对结束后刷新列表（有未转码文件时切到该页），并开始加载视频属性"""
        self._update_compare_result_ui()
        if self.unmatched_rows:
            try:
                self.compare_inner.select(1)  # 未转码文件（A有B无）
            except Exception:
                pass
        self._start_props_loading()

    def _start_props_loading(self):
        if not self.unmatched_rows:
            return
        self.props_generation += 1
        self.props_running = True
        threading.Thread(target=self._props_thread,
                         args=(self.props_generation,), daemon=True).start()

    def _props_thread(self, generation: int):
        if _IS_WINDOWS:
            _com_init()
        try:
            items = list(self.unmatched_rows.items())
            total = len(items)
            for idx, (item_id, record) in enumerate(items, 1):
                if generation != self.props_generation or not self.props_running:
                    break
                width, height, bitrate = get_media_properties(record['path'], record['size'])
                self.progress_queue.put(
                    ('props', generation, item_id, width, height, bitrate, idx, total))
        except Exception as e:
            self.log(f"[双文件夹比对] 读取视频属性失败: {e}")
        finally:
            if generation == self.props_generation:
                self.progress_queue.put(('props_done', generation))
            if _IS_WINDOWS:
                _com_uninit()

    def _apply_props(self, item_id: str, width, height, bitrate):
        if not self.unmatched_tree.exists(item_id):
            return
        record = self.unmatched_rows.get(item_id)
        if record is None:
            return
        record['frame_width'] = width
        record['frame_height'] = height
        record['bitrate'] = bitrate
        self.unmatched_tree.set(item_id, 'frame_width', width if width else '')
        self.unmatched_tree.set(item_id, 'frame_height', height if height else '')
        self.unmatched_tree.set(item_id, 'bitrate', format_bitrate(bitrate))

    def _update_progress(self, value: int, message: str):
        self.progress_queue.put(('progress', value, message))

    def _update_stats(self, source_count: int, target_count: int,
                      perfect: int, suffix: int, code_matched: int, unmatched: int):
        self.progress_queue.put(('stats', source_count, target_count, perfect,
                                 suffix, code_matched, unmatched))

    def _check_progress(self):
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                msg_type = msg[0]
                if msg_type == 'progress':
                    _, value, message = msg
                    self.progress_var.set(value)
                    if hasattr(self, 'progress_label'):
                        self.progress_label.config(text=message)
                elif msg_type == 'stats':
                    (_, source_count, target_count, perfect, suffix,
                     code_matched, unmatched) = msg
                    self.stats_label.config(text=
                        f"A文件夹: {source_count}个文件 | B文件夹: {target_count}个文件 | "
                        f"已转码: {perfect + suffix + code_matched}"
                        f"（完全匹配 {perfect} / 数字后缀 {suffix} / 番号 {code_matched}）| "
                        f"未转码: {unmatched}"
                    )
                elif msg_type == 'props':
                    _, generation, item_id, width, height, bitrate, idx, total = msg
                    if generation == self.props_generation:
                        self._apply_props(item_id, width, height, bitrate)
                        if hasattr(self, 'progress_label'):
                            self.progress_label.config(text=f"正在读取视频属性 {idx}/{total}")
                elif msg_type == 'props_done':
                    if hasattr(self, 'progress_label'):
                        self.progress_label.config(text="视频属性读取完成")
        except queue.Empty:
            pass
        # 仅当存在（比对模式已构建）时才继续轮询
        if hasattr(self, 'progress_label'):
            self.frame.after(100, self._check_progress)

    def _compare_completed(self, perfect: int, suffix: int,
                           code_matched: int, unmatched: int):
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False
        if self.compare_results:
            self.export_btn.config(state=tk.NORMAL)
        msg = (f"对比完成！\n\n已转码: {perfect + suffix + code_matched}\n"
               f"  文件名完全相同: {perfect}\n"
               f"  数字后缀匹配: {suffix}\n"
               f"  番号/前缀匹配: {code_matched}\n"
               f"未转码(待处理): {unmatched}")
        self.show_toast(msg, "success")

    def _reset_compare_ui(self):
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.is_running = False

    def _clear_compare_results(self):
        # 让正在后台读取属性的线程自然退出
        self.props_generation += 1
        self.props_running = False
        self.compare_results.clear()
        self.matched_files.clear()
        self.unmatched_files.clear()
        self.unmatched_rows.clear()
        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)
        for item in self.unmatched_tree.get_children():
            self.unmatched_tree.delete(item)
        self.progress_var.set(0)
        if hasattr(self, 'progress_label'):
            self.progress_label.config(text="准备就绪")
        self.stats_label.config(text="A文件夹: 0个文件 | B文件夹: 0个文件 | 已转码: 0 | 未转码: 0")
        self.log("[双文件夹比对] 结果已清空")

    def _select_files_in_explorer(self, files: List[Path], folder_path: Path, description: str):
        if not files:
            self.show_toast(f"没有{description}可选中", "warning")
            return
        existing_files = [f for f in files if f.exists()]
        if not existing_files:
            self.show_toast(f"{description}文件不存在", "warning")
            return
        try:
            if len(existing_files) == 1:
                cmd = f'explorer /select,"{existing_files[0]}"'
                subprocess.Popen(cmd, shell=True)
                self.log(f"[双文件夹比对] 已选中 {description}: {existing_files[0].name}")
            else:
                subprocess.Popen(f'explorer "{folder_path}"', shell=True)
                batch_lines = ['@echo off', 'echo 正在选中文件...', 'timeout /t 1 /nobreak >nul']
                for f in existing_files[:10]:
                    batch_lines.append(f'explorer /select,"{f}"')
                    batch_lines.append('timeout /t 1 /nobreak >nul')
                with tempfile.NamedTemporaryFile(mode='w', suffix='.bat', delete=False, encoding='gbk') as f:
                    f.write('\n'.join(batch_lines))
                    batch_file = f.name
                subprocess.Popen(batch_file, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
                self.frame.after(10000, lambda p=batch_file: os.unlink(p) if os.path.exists(p) else None)
                self.log(f"[双文件夹比对] 已打开文件夹并尝试选中 {len(existing_files)} 个{description}: {folder_path}")
                if len(existing_files) > 10:
                    self.show_toast(
                        f"由于Windows限制，已打开文件夹: {folder_path}\n"
                        f"并尝试选中前10个文件。其余文件请手动选择。\n总共 {len(existing_files)} 个文件", "info")
        except Exception as e:
            self.log(f"[双文件夹比对] 选中文件失败: {e}")
            try:
                subprocess.Popen(f'explorer "{folder_path}"', shell=True)
                self.log(f"[双文件夹比对] 已打开文件夹: {folder_path}")
            except Exception:
                pass
            self.show_toast(f"选中文件失败: {e}\n已尝试打开文件夹", "error")

    def _select_matched_files(self):
        if not self.matched_files:
            self.show_toast("没有已匹配的文件，请先执行对比操作", "warning")
            return
        target_files = [r['target_file'] for r in self.matched_files
                        if r['target_file'] and r['target_file'].exists()]
        if not target_files:
            self.show_toast("匹配的文件在B文件夹中不存在", "warning")
            return
        self._select_files_in_explorer(target_files, self.target_folder, "已转码文件")

    def _select_unmatched_files(self):
        if not self.unmatched_files:
            self.show_toast("没有未转码的文件，请先执行对比操作", "warning")
            return
        # 列表中已选中文件时只处理选中的，否则处理全部未转码文件
        selected = self._selected_records()
        files = [Path(r['path']) for r in selected] if selected else self.unmatched_files
        self._select_files_in_explorer(files, self.source_folder, "未转码文件")

    # =====================================================================
    # 把选中的未转码视频加入 video_grid_tab 的视频剪辑转码面板
    # =====================================================================
    def _get_transcode_panel(self):
        """获取 video_grid_tab 中的 VideoTranscodePanel（不存在则返回 None）"""
        controller = self.controller
        if not controller:
            return None
        grid_tab = getattr(controller, 'tabs', {}).get('video_grid')
        return getattr(grid_tab, 'transcode_panel', None)

    def _add_selected_to_transcode(self):
        """将"未转码文件"列表中选中的视频加入"视频剪辑转码"的待转码列表"""
        records = self._selected_records()
        if not records:
            self.show_toast(
                "请先在\"未转码文件\"列表中勾选要转码的视频（Ctrl/Shift 可多选）", "warning")
            return
        panel = self._get_transcode_panel()
        if panel is None or not hasattr(panel, 'add_videos'):
            self.show_toast("未找到\"视频剪辑转码\"面板，无法添加", "error")
            return
        paths = [r['path'] for r in records]
        added = panel.add_videos(paths)  # 非视频文件会被面板自动过滤
        if added == 0:
            self.show_toast(
                "所选文件均已存在于转码列表，或没有可添加的（视频）文件", "info")
            return
        # 跳转到转码面板，方便用户继续设置并开始转码
        try:
            grid_tab = self.controller.tabs['video_grid']
            self.controller.notebook.select(grid_tab.frame)
        except Exception:
            pass
        self.show_toast(
            f"已将 {added} 个视频加入\"视频剪辑转码\"的待转码列表", "success")
        self.log(f"[文件夹检查/比对] 已把 {added} 个未转码视频加入转码列表")

    def _add_all_to_transcode(self):
        """将"未转码文件"列表中的全部视频加入"视频剪辑转码"的待转码列表"""
        records = list(self.unmatched_rows.values())
        if not records:
            self.show_toast("没有未转码的文件，请先执行对比操作", "warning")
            return
        panel = self._get_transcode_panel()
        if panel is None or not hasattr(panel, 'add_videos'):
            self.show_toast("未找到\"视频剪辑转码\"面板，无法添加", "error")
            return
        paths = [r['path'] for r in records]
        added = panel.add_videos(paths)  # 非视频文件会被面板自动过滤
        if added == 0:
            self.show_toast(
                "全部文件均已存在于转码列表，或没有可添加的（视频）文件", "info")
            return
        # 跳转到转码面板，方便用户继续设置并开始转码
        try:
            grid_tab = self.controller.tabs['video_grid']
            self.controller.notebook.select(grid_tab.frame)
        except Exception:
            pass
        self.show_toast(
            f"已将 {added} 个视频加入\"视频剪辑转码\"的待转码列表", "success")
        self.log(f"[文件夹检查/比对] 已把全部 {added} 个未转码视频加入转码列表")

    # =====================================================================
    # 未转码列表：选择 / 排序 / 右键菜单 / 拖拽
    # =====================================================================
    def _selected_records(self) -> List[Dict]:
        """当前列表中选中的文件记录（保持选中顺序）"""
        records = []
        for item_id in self.unmatched_tree.selection():
            record = self.unmatched_rows.get(item_id)
            if record:
                records.append(record)
        return records

    def _sort_unmatched(self, col: str):
        """点击列标题排序（数值列按数值、文本列按字符串）"""
        items = list(self.unmatched_tree.get_children(''))

        def sort_key(item_id: str):
            record = self.unmatched_rows.get(item_id, {})
            value = record.get(col)
            if value is None:
                value = record.get('name', '')
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (0, value, '')
            return (1, 0, str(value).lower())

        items.sort(key=sort_key, reverse=self.unmatched_sort_reverse)
        for index, item_id in enumerate(items):
            self.unmatched_tree.move(item_id, '', index)

        self.unmatched_sort_col = col
        self.unmatched_sort_reverse = not self.unmatched_sort_reverse
        for column in self.UNMATCHED_COLUMNS:
            text = self.UNMATCHED_COLUMN_CONFIG[column][0]
            if column == col:
                text += " ▼" if self.unmatched_sort_reverse else " ▲"
            self.unmatched_tree.heading(column, text=text)

    def _init_unmatched_context_menu(self):
        self.unmatched_menu = tk.Menu(self.frame, tearoff=0)
        self.unmatched_menu.add_command(label="复制完整路径", command=self._copy_unmatched_paths)
        self.unmatched_menu.add_command(label="复制文件名", command=self._copy_unmatched_names)
        self.unmatched_menu.add_separator()
        self.unmatched_menu.add_command(label="在资源管理器中显示",
                                        command=self._reveal_in_explorer)
        self.unmatched_menu.add_separator()
        self.unmatched_menu.add_command(label="全选", command=self._select_all_unmatched)
        self.unmatched_menu.add_command(label="清空选择", command=self._clear_unmatched_selection)
        self.unmatched_tree.bind("<Button-3>", self._show_unmatched_menu)

    def _show_unmatched_menu(self, event):
        item = self.unmatched_tree.identify_row(event.y)
        if item and item not in self.unmatched_tree.selection():
            self.unmatched_tree.selection_set(item)
        try:
            self.unmatched_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.unmatched_menu.grab_release()

    def _copy_unmatched_paths(self):
        records = self._selected_records()
        if not records:
            return
        self._to_clipboard("\n".join(r['path'] for r in records))

    def _copy_unmatched_names(self):
        records = self._selected_records()
        if not records:
            return
        self._to_clipboard("\n".join(r['name'] for r in records))

    def _to_clipboard(self, text: str):
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self.frame.update()

    def _reveal_in_explorer(self):
        records = self._selected_records()
        if not records:
            self.show_toast("请先在列表中选择文件", "info")
            return
        paths = [Path(r['path']) for r in records if os.path.exists(r['path'])]
        if not paths:
            self.show_toast("所选文件不存在", "warning")
            return
        self._select_files_in_explorer(paths, Path(paths[0]).parent, "所选文件")

    def _select_all_unmatched(self):
        self.unmatched_tree.selection_set(self.unmatched_tree.get_children())

    def _clear_unmatched_selection(self):
        self.unmatched_tree.selection_remove(self.unmatched_tree.selection())

    # ---------------------------------------------------------------------
    # 拖拽源：把列表中的文件拖到其它软件（依赖 tkinterdnd2 -> tkdnd）
    # ---------------------------------------------------------------------
    def _enable_drag_source(self):
        """把"未转码文件"列表注册为拖拽源（CF_HDROP），可直接拖到播放器/转码软件"""
        if self.drag_enabled:
            return
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
        except Exception as e:
            self.log(f"[双文件夹比对] 未安装 tkinterdnd2，无法启用拖拽（pip install tkinterdnd2）: {e}")
            return
        try:
            root = self.unmatched_tree.winfo_toplevel()
            TkinterDnD._require(root)  # 为已有主窗口加载 tkdnd
            self.unmatched_tree.drag_source_register(1, DND_FILES)
            # 直接绑定 Tcl 脚本：回调返回完整的 Tcl 列表（避免 Python 元组转义问题）
            self._drag_cmd_name = self.unmatched_tree._register(self._drag_init_data)
            self.unmatched_tree.tk.call(
                'bind', str(self.unmatched_tree), '<<DragInitCmd>>',
                f'{self._drag_cmd_name} %W %x %y')
            self.drag_enabled = True
            self.log(f"[双文件夹比对] 拖拽已启用（tkdnd {TkinterDnD.TkdndVersion}）："
                     f"可直接把列表中的文件拖到其它软件")
        except Exception as e:
            self.drag_enabled = False
            self.log(f"[双文件夹比对] 拖拽源注册失败: {e}")

    def _drag_init_data(self, widget=None, x='0', y='0'):
        """<<DragInitCmd>> 回调：返回 tkdnd 需要的 {操作 类型 数据} 三元组

        返回 'refuse_drop' 表示不发起拖拽（例如点在表头上或没有选中文件）。
        """
        try:
            tree = self.unmatched_tree
            # 点在表头/分隔线上时不触发拖拽，避免影响列宽调整
            if tree.identify_region(int(float(x)), int(float(y))) in ('heading', 'separator'):
                return 'refuse_drop'

            row = tree.identify_row(int(float(y)))
            selection = list(tree.selection())
            if not row:
                rows = selection
            elif row in selection:
                rows = selection
            else:
                rows = [row]

            paths = [rec['path'] for rec in (self.unmatched_rows.get(r) for r in rows)
                     if rec and os.path.exists(rec['path'])]
            if not paths:
                return 'refuse_drop'

            data = tree.tk.call('list', *paths)          # 文件列表（Tcl 列表）
            return tree.tk.call('list', 'copy', 'DND_Files', data)
        except Exception:
            return 'refuse_drop'

    # =====================================================================
    # 视频文件比对模式：拖入两个视频 -> 属性对比 + 双播放器同步播放
    # =====================================================================
    def _get_embedded_player_class(self):
        """延迟加载 video_grid_tab 里的内嵌播放器（缺少依赖时返回 None）"""
        cached = self.__dict__.get('_EmbeddedPlayerCls')
        if cached is not None:
            return cached
        try:
            from video_grid_tab import EmbeddedPlayer
            cached = EmbeddedPlayer
        except Exception as e:
            self.log(f"[视频文件比对] 无法加载内嵌播放器（缺少 opencv/Pillow？）: {e}")
            cached = None
        self._EmbeddedPlayerCls = cached
        return cached

    def _init_video_compare_ui(self, parent: ttk.Frame):
        """构建视频文件比对面板：左右对称的拖放区 / 播放器 / 属性列表"""
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=0)
        parent.rowconfigure(2, weight=1)

        # ---------- 顶部操作条 ----------
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(2, 4))

        # ---------- 全局同步进度条（以时长较短的视频为准） ----------
        gframe = ttk.Frame(parent)
        gframe.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 2))
        self.global_scale_var = tk.DoubleVar(value=0.0)
        self.global_time_var = tk.StringVar(value="00:00:00 / 00:00:00")
        self.global_dragging = False
        self.global_was_playing = {}
        self.global_updating = False
        ttk.Label(gframe, text="同步进度:").pack(side=tk.LEFT, padx=(0, 4))
        gscale = ttk.Scale(gframe, from_=0.0, to=1.0,
                           variable=self.global_scale_var, orient=tk.HORIZONTAL)
        gscale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        gscale.configure(command=lambda v: self._on_global_scale_move(v))
        gscale.bind('<ButtonPress-1>', lambda e: self._on_global_scale_press())
        gscale.bind('<ButtonRelease-1>', lambda e: self._on_global_scale_release())
        ttk.Label(gframe, textvariable=self.global_time_var, width=24,
                  anchor='w').pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(
            top,
            text="把两个视频分别拖入左右区域（或点击区域选择文件）；两列属性按相同行对齐，点击任一属性行可在左右两侧同步高亮对比。",
            foreground="gray").pack(side=tk.LEFT)
        self.video_settings_btn = ttk.Button(
            top, text="⚙ 播放器设置", command=self._open_player_settings)
        self.video_settings_btn.pack(side=tk.RIGHT, padx=4)
        self.video_pick_btn = ttk.Button(
            top, text="📁 文件选择", command=self._open_video_folder_window)
        self.video_pick_btn.pack(side=tk.RIGHT, padx=4)
        self.video_play_check_btn = ttk.Button(
            top, text="▶ 播放检查", command=self._toggle_play_check)
        self.video_play_check_btn.pack(side=tk.RIGHT, padx=4)
        self.video_stop_btn = ttk.Button(
            top, text="⏹ 全部停止", command=self._stop_all_players)
        self.video_stop_btn.pack(side=tk.RIGHT, padx=4)
        self.video_clear_btn = ttk.Button(
            top, text="🗑 清空", command=self._clear_video_compare)
        self.video_clear_btn.pack(side=tk.RIGHT, padx=4)

        player_cls = self._get_embedded_player_class()
        self.video_sides = {}

        # ---------- 左右两个对称区域 ----------
        for idx, side in enumerate(('A', 'B')):
            title = "视频 A（左）" if side == 'A' else "视频 B（右）"
            frame = ttk.LabelFrame(parent, text=title, padding=4)
            frame.grid(row=2, column=idx, sticky="nsew",
                       padx=(4, 2) if idx == 0 else (2, 4), pady=2)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(2, weight=1, minsize=160)

            # 拖放区（也可点击选择文件）
            drop = tk.Label(
                frame, text="⬇ 将视频文件拖到这里\n（或点击此处选择文件）",
                relief=tk.GROOVE, bd=2, bg="#eef4fb", fg="#334455",
                height=2, cursor="hand2", justify="center")
            drop.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 2))
            drop.bind('<Button-1>', lambda e, s=side: self._browse_video_side(s))

            name_var = tk.StringVar(value="（未选择文件）")
            # 文件名改为可编辑文本框，右侧“更名”按钮按文本框内容改名
            name_frame = ttk.Frame(frame)
            name_frame.grid(row=1, column=0, sticky="ew", padx=2)
            name_frame.columnconfigure(0, weight=1)
            name_entry = ttk.Entry(name_frame, textvariable=name_var)
            name_entry.grid(row=0, column=0, sticky="ew")
            ttk.Button(
                name_frame, text="更名", width=6,
                command=lambda s=side: self._rename_video_side(s)).grid(
                    row=0, column=1, padx=(4, 0))

            # 播放器容器（首帧即缩略图，播放时同样在此渲染，自动适配尺寸）
            holder = ttk.Frame(frame, height=180)
            holder.grid(row=2, column=0, sticky="nsew", padx=2, pady=2)
            holder.pack_propagate(False)

            player = None
            if player_cls is not None:
                try:
                    player = player_cls(
                        holder,
                        on_time=lambda t, s=side: self._on_video_time(s, t),
                        on_state=lambda st, s=side: self._on_video_state(s, st))
                except Exception as e:
                    self.log(f"[视频文件比对] 创建内嵌播放器失败: {e}")
            if player is None:
                ttk.Label(holder, text="内嵌播放器不可用\n（缺少 opencv / Pillow）",
                          foreground="red", justify="center").pack(expand=True)

            # 可拖动进度条（0~1，按视频时长映射）
            scale_var = tk.DoubleVar(value=0.0)
            scale = ttk.Scale(frame, from_=0.0, to=1.0,
                              variable=scale_var, orient=tk.HORIZONTAL)
            scale.grid(row=3, column=0, sticky="ew", padx=2)
            scale.configure(command=lambda v, s=side: self._on_scale_move(s, v))
            scale.bind('<ButtonPress-1>', lambda e, s=side: self._on_scale_press(s))
            scale.bind('<ButtonRelease-1>', lambda e, s=side: self._on_scale_release(s))

            # 时间 + 单独播放 / VLC 原尺寸
            ctrl = ttk.Frame(frame)
            ctrl.grid(row=4, column=0, sticky="ew", padx=2, pady=(2, 2))
            time_var = tk.StringVar(value="00:00:00 / 00:00:00")
            ttk.Label(ctrl, textvariable=time_var).pack(side=tk.LEFT)
            play_btn = ttk.Button(ctrl, text="▶ 播放", width=8,
                                  command=lambda s=side: self._toggle_side_play(s))
            play_btn.pack(side=tk.RIGHT, padx=2)
            ttk.Button(ctrl, text="删除视频",
                       command=lambda s=side: self._delete_video_side(s)).pack(
                           side=tk.RIGHT, padx=2)
            ttk.Button(ctrl, text="VLC 原尺寸",
                       command=lambda s=side: self._open_side_in_vlc(s)).pack(
                           side=tk.RIGHT, padx=2)

            # 属性列表（两侧行顺序完全一致，天然对齐）
            # selectmode='none'：不用系统“当前焦点”选中样式，改用自定义 tag 高亮，
            # 这样左右两列能显示完全相同的激活样式（系统选中样式只在获得焦点的一侧显示）。
            info = ttk.Treeview(frame, columns=('attr', 'value'),
                                show='headings', height=10, selectmode='none')
            info.heading('attr', text='属性')
            info.heading('value', text='值')
            info.column('attr', width=84, minwidth=70, anchor='w', stretch=False)
            info.column('value', width=220, minwidth=120, anchor='w', stretch=True)
            info.tag_configure('active', background='#2a7fd4', foreground='white')
            info.grid(row=5, column=0, sticky='ew', padx=2, pady=(0, 2))
            info.bind('<Button-1>', lambda e, s=side: self._on_info_click(e, s))
            for key, label in self.VIDEO_INFO_FIELDS:
                info.insert('', tk.END, iid=key, values=(label, ''))

            self.video_sides[side] = {
                'path': None,
                'player': player,
                'play_btn': play_btn,
                'scale': scale,
                'scale_var': scale_var,
                'time_var': time_var,
                'name_var': name_var,
                'info': info,
                'drop': drop,
                'duration': 0.0,
                'updating_scale': False,
                'dragging': False,
                'was_playing': False,
                'gen': 0,
            }

            # 等主窗口就绪后再注册拖放（tkdnd 需要主窗口已创建）
            self.frame.after(350, lambda w=drop, s=side: self._register_video_drop(w, s))

    # ---------- 拖放 ----------
    def _register_video_drop(self, widget, side: str) -> bool:
        """把拖放区注册为 tkdnd 拖放目标（接收资源管理器拖入的文件）"""
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            root = widget.winfo_toplevel()
            if not getattr(root, '_tkdnd_required', False):
                TkinterDnD._require(root)
                root._tkdnd_required = True
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', lambda e, s=side: self._on_video_drop(e, s))
            widget.dnd_bind('<<DragEnter>>',
                            lambda e, s=side: self._set_drop_zone_active(s, True))
            widget.dnd_bind('<<DragLeave>>',
                            lambda e, s=side: self._set_drop_zone_active(s, False))
            return True
        except Exception as e:
            self.log(f"[视频文件比对] 拖放注册失败: {e}")
            return False

    def _set_drop_zone_active(self, side: str, active: bool):
        st = self.video_sides.get(side)
        if not st:
            return
        try:
            st['drop'].configure(bg="#cfe6ff" if active else "#eef4fb")
        except Exception:
            pass

    def _on_video_drop(self, event, side: str):
        """处理拖入：取第一个有效的视频文件载入对应区域"""
        self._set_drop_zone_active(side, False)
        try:
            paths = list(self.frame.tk.splitlist(event.data))
        except Exception:
            paths = [str(getattr(event, 'data', ''))]
        for p in paths:
            if p and os.path.isfile(p):
                self._load_video_side(side, p)
                break
        return getattr(event, 'action', None)

    def _browse_video_side(self, side: str):
        exts = ' '.join(f"*{e}" for e in sorted(self.VIDEO_EXTENSIONS))
        path = filedialog.askopenfilename(
            title=f"选择视频 {side}",
            filetypes=[("视频文件", exts), ("所有文件", "*.*")])
        if path:
            self._load_video_side(side, path)

    # ---------- 载入 / 属性读取 ----------
    def _load_video_side(self, side: str, path: str):
        st = self.video_sides.get(side)
        if not st:
            return
        ext = Path(path).suffix.lower()
        if ext not in self.VIDEO_EXTENSIONS:
            self.show_toast(f"不支持的文件类型: {ext or '未知'}", "warning")
            return
        if not os.path.isfile(path):
            self.show_toast("文件不存在或无法访问", "warning")
            return

        st['gen'] += 1
        gen = st['gen']
        st['path'] = path
        st['name_var'].set(Path(path).name)
        st['duration'] = 0.0
        st['dragging'] = False
        st['was_playing'] = False
        st['updating_scale'] = True
        try:
            st['scale_var'].set(0.0)
        finally:
            st['updating_scale'] = False
        st['time_var'].set("读取中…")
        self._clear_info_highlight()
        for key, _label in self.VIDEO_INFO_FIELDS:
            try:
                st['info'].set(key, 'value', '')
            except Exception:
                pass

        # 换文件时先释放旧播放器，避免继续读取旧文件
        if st['player'] is not None:
            try:
                st['player'].release()
            except Exception:
                pass

        threading.Thread(
            target=self._probe_video_info_thread,
            args=(side, path, gen), daemon=True).start()
        # 主线程轮询后台结果（跨线程直接调用 after 在部分环境不可靠）
        self.frame.after(60, lambda: self._poll_video_probe(side, gen))

    def _probe_video_info_thread(self, side: str, path: str, gen: int):
        try:
            info = self._probe_video_info(path)
        except Exception as e:
            self.log(f"[视频文件比对] 读取视频信息失败: {path} - {e}")
            info = {}
        # 只写入缓存，由主线程 _poll_video_probe 取用并刷新界面
        self.video_probe_results[(side, gen)] = (path, info)

    def _poll_video_probe(self, side: str, gen: int):
        """主线程轮询：后台读取完成后刷新属性表并加载播放器"""
        st = self.video_sides.get(side)
        if not st or st['gen'] != gen:
            self.video_probe_results.pop((side, gen), None)
            return
        item = self.video_probe_results.pop((side, gen), None)
        if item is None:
            self.frame.after(60, lambda: self._poll_video_probe(side, gen))
            return
        path, info = item
        self._apply_video_info(side, path, info, gen)

    def _resolve_ffprobe(self) -> str:
        cached = getattr(self, '_ffprobe_path', None)
        if cached is not None:
            return cached
        fp = shutil.which('ffprobe') or ''
        self._ffprobe_path = fp
        return fp

    def _probe_video_info(self, path: str) -> Dict:
        """读取视频全部展示属性（ffprobe 优先，OpenCV / 估算兜底）"""
        p = Path(path)
        info: Dict = {'path': str(p), 'name': p.name}
        try:
            st = p.stat()
            info['size'] = st.st_size
            info['modified'] = st.st_mtime
        except Exception:
            info['size'] = 0
            info['modified'] = 0
        info['location'] = str(p.parent)
        try:
            info['type'] = shell_file_type_name(str(p)) or self._fallback_file_type(p)
        except Exception:
            info['type'] = self._fallback_file_type(p)

        dur = w = h = fps = vbr = tbr = 0.0
        fp = self._resolve_ffprobe()
        if fp:
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
            try:
                res = subprocess.run(
                    [fp, '-v', 'error', '-show_entries',
                     'format=duration,bit_rate:'
                     'stream=codec_type,width,height,r_frame_rate,avg_frame_rate,bit_rate',
                     '-of', 'json', path],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, timeout=30, creationflags=flags)
                data = json.loads(res.stdout or '{}')
                fmt = data.get('format', {}) or {}
                try:
                    dur = float(fmt.get('duration') or 0)
                except Exception:
                    dur = 0.0
                try:
                    tbr = float(fmt.get('bit_rate') or 0)
                except Exception:
                    tbr = 0.0
                for s in data.get('streams', []) or []:
                    if s.get('codec_type') != 'video' or w:
                        continue
                    w = int(s.get('width') or 0)
                    h = int(s.get('height') or 0)
                    rate = s.get('r_frame_rate') or s.get('avg_frame_rate') or '0/1'
                    num, _, den = str(rate).partition('/')
                    try:
                        fps = float(num) / float(den or 1)
                    except Exception:
                        fps = 0.0
                    br = str(s.get('bit_rate') or '')
                    if br.isdigit():
                        vbr = float(br)
            except Exception as e:
                self.log(f"[视频文件比对] ffprobe 读取失败: {p.name} - {e}")

        # OpenCV 兜底：补齐宽高 / 帧率 / 时长
        if dur <= 0 or w <= 0 or fps <= 0:
            try:
                import cv2
                cap = cv2.VideoCapture(str(p))
                if cap.isOpened():
                    if w <= 0:
                        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    if h <= 0:
                        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    cap_fps = cap.get(cv2.CAP_PROP_FPS) or 0
                    if fps <= 0:
                        fps = cap_fps
                    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    if dur <= 0 and cap_fps > 0 and frames > 0:
                        dur = frames / cap_fps
                    cap.release()
            except Exception:
                pass

        # 码率兜底：缺失时按 文件大小/时长 估算
        if vbr <= 0 and dur > 0 and info['size'] > 0:
            vbr = info['size'] * 8 / dur
        if tbr <= 0 and dur > 0 and info['size'] > 0:
            tbr = info['size'] * 8 / dur

        info.update(duration=dur, width=int(w or 0), height=int(h or 0),
                    fps=float(fps or 0), video_bitrate=float(vbr or 0),
                    total_bitrate=float(tbr or 0))
        return info

    def _apply_video_info(self, side: str, path: str, info: Dict, gen: int):
        """主线程：刷新属性表并载入播放器（首帧即缩略图）"""
        st = self.video_sides.get(side)
        if not st or st['gen'] != gen:
            return
        if info:
            self._fill_video_info(st, info)

        player = st['player']
        duration = float(info.get('duration') or 0)
        if player is not None:
            try:
                if player.load(path):
                    pd = float(getattr(player, 'duration', 0) or 0)
                    if pd > 0:
                        duration = pd
                else:
                    self.show_toast("该视频无法用内嵌播放器解码，仍可查看属性", "warning")
            except Exception as e:
                self.log(f"[视频文件比对] 播放器加载失败: {e}")

        st['duration'] = duration
        st['updating_scale'] = True
        try:
            st['scale_var'].set(0.0)
        finally:
            st['updating_scale'] = False
        st['time_var'].set(f"00:00:00 / {self._format_hms(duration)}")
        self.log(f"[视频文件比对] 已载入 {side}: {Path(path).name}")
        self._update_global_scale()

    def _fill_video_info(self, st: Dict, info: Dict):
        """把属性字典写入 Treeview（行 iid 即属性 key）"""
        def put(key, value):
            try:
                st['info'].set(key, 'value', value)
            except Exception:
                pass

        put('type', info.get('type') or '')
        put('size', format_file_size(info.get('size') or 0))
        put('location', info.get('location') or '')
        put('modified', format_timestamp(info.get('modified') or 0))
        put('duration', self._format_hms(info.get('duration') or 0))
        w = int(info.get('width') or 0)
        h = int(info.get('height') or 0)
        put('width', str(w) if w else '')
        put('height', str(h) if h else '')
        fps = float(info.get('fps') or 0)
        put('fps', f"{fps:.2f} 帧/秒" if fps else '')
        vbr = float(info.get('video_bitrate') or 0)
        put('data_rate', f"{int(round(vbr / 1000))}kbps" if vbr else '')
        tbr = float(info.get('total_bitrate') or 0)
        put('total_bitrate', f"{int(round(tbr / 1000))}kbps" if tbr else '')

    def _on_info_click(self, event, side: str):
        """点击任一侧属性行：左右两列同时高亮同一属性，便于逐项对比"""
        st = self.video_sides.get(side)
        if not st:
            return
        iid = st['info'].identify_row(event.y)
        # 点中表头/分隔线时 identify_row 返回空，忽略
        if not iid:
            return
        self._highlight_info_row(iid)

    def _highlight_info_row(self, key: str):
        """在左右两个属性列表中以相同样式高亮同一属性行（key 即行 iid）"""
        for st in self.video_sides.values():
            tree = st.get('info')
            if tree is None:
                continue
            for iid in tree.get_children():
                tags = tree.item(iid, 'tags') or ()
                if iid == key:
                    tree.item(iid, tags=('active',))
                elif 'active' in tags:
                    tree.item(iid, tags=())
            # 两侧同步滚动到该行，保证同一属性都在可视区域内
            try:
                tree.see(key)
            except Exception:
                pass

    def _clear_info_highlight(self):
        """清除左右两侧属性列表的激活行"""
        for st in getattr(self, 'video_sides', {}).values():
            tree = st.get('info')
            if tree is None:
                continue
            for iid in tree.get_children():
                tags = tree.item(iid, 'tags') or ()
                if 'active' in tags:
                    tree.item(iid, tags=())

    @staticmethod
    def _format_hms(seconds) -> str:
        """秒 -> HH:MM:SS"""
        try:
            seconds = float(seconds)
        except Exception:
            seconds = 0.0
        if seconds < 0:
            seconds = 0.0
        total = int(seconds)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    # ---------- 播放控制 ----------
    def _on_video_time(self, side: str, t: float):
        st = self.video_sides.get(side)
        if not st:
            return
        player = st['player']
        dur = float(getattr(player, 'duration', 0) or 0) or float(st.get('duration') or 0)
        if dur <= 0:
            return
        st['updating_scale'] = True
        try:
            st['scale_var'].set(max(0.0, min(1.0, float(t) / dur)))
        finally:
            st['updating_scale'] = False
        st['time_var'].set(f"{self._format_hms(t)} / {self._format_hms(dur)}")
        if not getattr(self, 'global_dragging', False):
            self._update_global_scale()

    def _on_video_state(self, side: str, playing: bool):
        st = self.video_sides.get(side)
        if not st:
            return
        try:
            st['play_btn'].configure(text="⏸ 暂停" if playing else "▶ 播放")
        except Exception:
            pass
        self._sync_play_check_button()

    def _sync_play_check_button(self):
        players = [st['player'] for st in getattr(self, 'video_sides', {}).values()
                   if st.get('player') is not None]
        playing = any(getattr(p, 'playing', False) for p in players)
        try:
            self.video_play_check_btn.configure(
                text="⏸ 暂停检查" if playing else "▶ 播放检查")
        except Exception:
            pass

    def _on_scale_press(self, side: str):
        st = self.video_sides.get(side)
        if not st:
            return
        st['dragging'] = True
        player = st['player']
        st['was_playing'] = bool(player and player.playing)
        if player and player.playing:
            player.pause()

    def _on_scale_move(self, side: str, value):
        st = self.video_sides.get(side)
        if not st or st.get('updating_scale'):
            return
        dur = float(st.get('duration') or 0)
        if dur <= 0:
            return
        try:
            t = float(value) * dur
        except Exception:
            return
        # 拖动过程中只更新时间预览，松手后再真正 seek（避免频繁解码卡顿）
        st['time_var'].set(f"{self._format_hms(t)} / {self._format_hms(dur)}")

    def _on_scale_release(self, side: str):
        st = self.video_sides.get(side)
        if not st:
            return
        st['dragging'] = False
        player = st['player']
        dur = float(st.get('duration') or 0)
        if player is None or dur <= 0:
            return
        try:
            t = float(st['scale_var'].get()) * dur
        except Exception:
            return
        player.seek(t)   # seek 内部会暂停
        if st.get('was_playing'):
            player.play()
        st['was_playing'] = False

    # ---------- 全局同步进度条 ----------
    def _get_min_duration(self) -> float:
        ds = [float(st.get('duration') or 0) for st in self.video_sides.values()
              if st.get('player') and st['player'].cap is not None
              and float(st.get('duration') or 0) > 0]
        return min(ds) if ds else 0.0

    def _update_global_scale(self):
        """根据两视频中较短的时长刷新同步进度条（参考侧取时长最短的一侧）"""
        if not hasattr(self, 'global_scale_var'):
            return
        loaded = [(s, st) for s, st in self.video_sides.items()
                  if st.get('player') and st['player'].cap is not None
                  and float(st.get('duration') or 0) > 0]
        if not loaded:
            self.global_updating = True
            try:
                self.global_scale_var.set(0.0)
            finally:
                self.global_updating = False
            self.global_time_var.set("00:00:00 / 00:00:00")
            return
        min_dur = min(float(st['duration']) for _, st in loaded)
        ref = min(loaded, key=lambda x: float(x[1]['duration']))[1]
        pos = float(getattr(ref['player'], 'current_time', 0) or 0)
        if pos > min_dur:
            pos = min_dur
        self.global_updating = True
        try:
            self.global_scale_var.set(pos / min_dur if min_dur > 0 else 0.0)
        finally:
            self.global_updating = False
        self.global_time_var.set(
            f"{self._format_hms(pos)} / {self._format_hms(min_dur)}")

    def _on_global_scale_move(self, value):
        if getattr(self, 'global_updating', False):
            return
        min_dur = self._get_min_duration()
        if min_dur <= 0:
            return
        t = float(value) * min_dur
        self.global_time_var.set(
            f"{self._format_hms(t)} / {self._format_hms(min_dur)}")

    def _on_global_scale_press(self):
        self.global_dragging = True
        self.global_was_playing = {}
        for side, st in self.video_sides.items():
            p = st.get('player')
            if p and p.cap is not None:
                playing = bool(p.playing)
                self.global_was_playing[side] = playing
                if playing:
                    p.pause()

    def _on_global_scale_release(self):
        """松手后按同一秒数同时定位左右两区（以较短时长为上限）"""
        self.global_dragging = False
        min_dur = self._get_min_duration()
        if min_dur <= 0:
            return
        try:
            value = float(self.global_scale_var.get())
        except Exception:
            return
        t = value * min_dur
        for side, st in self.video_sides.items():
            p = st.get('player')
            if p and p.cap is not None:
                p.seek(t)   # seek 内部会暂停
                if self.global_was_playing.get(side):
                    p.play()
        self._update_global_scale()

    def _toggle_side_play(self, side: str):
        st = self.video_sides.get(side)
        if not st or st['player'] is None or st['player'].cap is None:
            self.show_toast("请先在对应区域放入可播放的视频文件", "warning")
            return
        player = st['player']
        if player.playing:
            player.pause()
        else:
            dur = float(getattr(player, 'duration', 0) or 0)
            if dur > 0 and player.current_time >= dur - 0.05:
                player.seek(0.0)
            player.play()

    def _toggle_play_check(self):
        """同时播放/暂停左右两个播放器（播放检查）"""
        players = [st['player'] for st in self.video_sides.values()
                   if st.get('player') is not None and st['player'].cap is not None]
        if len(players) < 2:
            self.show_toast("请先在左右两侧各放入一个视频文件", "warning")
            return
        if any(p.playing for p in players):
            for p in players:
                p.pause()
            self.video_play_check_btn.configure(text="▶ 播放检查")
            return
        for p in players:
            dur = float(getattr(p, 'duration', 0) or 0)
            if dur > 0 and p.current_time >= dur - 0.05:
                p.seek(0.0)
            p.play()
        self.video_play_check_btn.configure(text="⏸ 暂停检查")

    def _stop_all_players(self):
        for st in self.video_sides.values():
            p = st.get('player')
            if p is None:
                continue
            try:
                p.pause()
                if p.cap is not None:
                    p.seek(0.0)
            except Exception:
                pass
        try:
            self.video_play_check_btn.configure(text="▶ 播放检查")
        except Exception:
            pass

    def _reset_video_side(self, side: str):
        """复位单个视频区域：释放播放器句柄、清空显示与缓存（不清空文件名文本框占位）"""
        st = self.video_sides.get(side)
        if not st:
            return
        player = st['player']
        if player is not None:
            try:
                player.pause()
                player.release()          # 关闭 cv2 句柄，解除文件占用
                if hasattr(player, '_canvas'):
                    player._canvas.delete('all')
            except Exception:
                pass
        st['path'] = None
        st['gen'] += 1                   # 让任何在途的探测结果作废
        st['duration'] = 0.0
        st['dragging'] = False
        st['was_playing'] = False
        st['updating_scale'] = True
        try:
            st['scale_var'].set(0.0)
        finally:
            st['updating_scale'] = False
        st['time_var'].set("00:00:00 / 00:00:00")
        st['name_var'].set("（未选择文件）")
        try:
            st['drop'].configure(bg="#eef4fb")
        except Exception:
            pass
        for key, _label in self.VIDEO_INFO_FIELDS:
            try:
                st['info'].set(key, 'value', '')
            except Exception:
                pass
        for k in list(self.video_probe_results.keys()):
            if k[0] == side:
                self.video_probe_results.pop(k, None)
        self._clear_info_highlight()
        self._update_global_scale()

    def _clear_video_compare(self):
        """清空左右两个视频对比区（释放句柄、复位界面），供“清空”按钮调用"""
        self._reset_video_side('A')
        self._reset_video_side('B')
        try:
            self.video_play_check_btn.configure(text="▶ 播放检查")
        except Exception:
            pass
        self.log("[视频文件比对] 已清空左右两个视频对比区")

    def _rename_video_side(self, side: str):
        """按文件名文本框内容对当前视频重命名；目标已存在时让用户选择覆盖或追加数字后缀"""
        st = self.video_sides.get(side)
        if not st:
            return
        path = st.get('path')
        if not path or not os.path.isfile(path):
            self.show_toast("请先在该区域放入视频文件", "warning")
            return
        new_name = (st['name_var'].get() or '').strip()
        if not new_name:
            self.show_toast("文件名不能为空", "warning")
            return
        cur = Path(path)
        parent = cur.parent
        # 用户输入不含扩展名时，保留原扩展名
        if Path(new_name).suffix == '' and cur.suffix:
            new_name = new_name + cur.suffix
        target = parent / new_name
        if target.resolve() == cur.resolve():
            self.show_toast("文件名未发生变化", "info")
            return
        if target.exists():
            choice = messagebox.askyesnocancel(
                "文件名已存在",
                f"目标文件已存在：\n{new_name}\n\n“是”=覆盖原文件；“否”=追加数字后缀；“取消”=不操作")
            if choice is None:
                return
            if choice is True:                # 覆盖
                try:
                    os.remove(target)
                except Exception as e:
                    self.show_toast(f"无法覆盖：{e}", "error")
                    return
            else:                             # 追加数字后缀
                base = target.stem
                ext = target.suffix
                i = 1
                while True:
                    cand = parent / f"{base}_{i}{ext}"
                    if not cand.exists():
                        target = cand
                        break
                    i += 1
        # 先释放句柄再改名，避免 Windows 下占用导致失败；改名后重新载入播放器
        try:
            if st['player'] is not None:
                st['player'].release()
            os.rename(cur, target)
            st['path'] = str(target)
            st['name_var'].set(target.name)
            self.log(f"[视频文件比对] 已重命名: {cur.name} -> {target.name}")
            self.show_toast(f"已重命名：{target.name}", "success")
            win = getattr(self, '_folder_pick_window', None)
            if win is not None and win.winfo_exists():
                win.mark_renamed(str(cur), str(target))
            if st['player'] is not None:
                st['player'].load(str(target))
                st['scale_var'].set(0.0)
                st['time_var'].set(f"00:00:00 / {self._format_hms(st['duration'])}")
        except Exception as e:
            self.show_toast(f"重命名失败：{e}", "error")

    def _delete_video_side(self, side: str):
        """删除当前区域的视频文件（带确认提示），并复位该区域"""
        st = self.video_sides.get(side)
        if not st:
            return
        path = st.get('path')
        if not path or not os.path.isfile(path):
            self.show_toast("请先在该区域放入视频文件", "warning")
            return
        name = Path(path).name
        if not messagebox.askyesno(
                "删除确认", f"确定要删除该视频文件吗？\n\n{name}\n\n此操作不可撤销。"):
            return
        try:
            if st['player'] is not None:
                st['player'].release()      # 释放句柄后才能删除
            os.remove(path)
            self.log(f"[视频文件比对] 已删除视频: {path}")
            self.show_toast(f"已删除：{name}", "success")
            win = getattr(self, '_folder_pick_window', None)
            if win is not None and win.winfo_exists():
                win.mark_removed(path)
            self._reset_video_side(side)
        except Exception as e:
            self.show_toast(f"删除失败：{e}", "error")

    # ---------- VLC 原尺寸播放 ----------
    def _open_player_settings(self):
        """播放器设置：指定 VLC 可执行文件路径（用于“VLC 原尺寸”播放），留空则自动查找"""
        win = getattr(self, '_player_settings_win', None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        root = self.frame.winfo_toplevel()
        win = tk.Toplevel(root)
        self._player_settings_win = win
        win.title("播放器设置")
        win.transient(root)
        win.resizable(False, False)

        f = ttk.Frame(win, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(f, text="VLC 播放器路径：").grid(
            row=0, column=0, sticky='w', pady=(0, 4))
        var = tk.StringVar(value=self._find_vlc() or '')
        ttk.Entry(f, textvariable=var, width=52).grid(
            row=1, column=0, columnspan=2, sticky='ew', padx=(0, 4))

        def browse():
            p = filedialog.askopenfilename(
                title="选择 VLC 可执行文件",
                filetypes=[("VLC 可执行文件", "vlc.exe"), ("所有文件", "*.*")])
            if p:
                var.set(p)

        ttk.Button(f, text="浏览...", command=browse).grid(
            row=1, column=2, padx=(4, 0))
        ttk.Label(f, text="留空则自动在常见安装路径中查找 VLC",
                  foreground="gray").grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(2, 10))

        def save():
            path = var.get().strip()
            if path and not os.path.isfile(path):
                self.show_toast("该路径不是有效的文件", "warning")
                return
            self._vlc_path = path
            self._save_config()
            self.show_toast("播放器设置已保存", "success")
            win.destroy()

        ttk.Button(f, text="确定", command=save).grid(
            row=3, column=1, padx=4, pady=2)
        ttk.Button(f, text="取消", command=win.destroy).grid(
            row=3, column=2, padx=4, pady=2)

        win.update_idletasks()
        rw, rh = root.winfo_width(), root.winfo_height()
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{rx + max(0, (rw - w) // 2)}+{ry + max(0, (rh - h) // 2)}")

    def _find_vlc(self) -> str:
        cached = getattr(self, '_vlc_path', None)
        if cached and os.path.isfile(cached):
            return cached
        candidates = []
        w = shutil.which('vlc')
        if w:
            candidates.append(w)
        for base in (os.environ.get('ProgramFiles'),
                     os.environ.get('ProgramFiles(x86)'),
                     os.environ.get('ProgramW6432')):
            if base:
                candidates.append(os.path.join(base, 'VideoLAN', 'VLC', 'vlc.exe'))
        found = next((c for c in candidates if c and os.path.isfile(c)), '')
        self._vlc_path = found
        return found

    def _open_side_in_vlc(self, side: str):
        """用独立 VLC 播放器以原始尺寸播放该侧视频（找不到 VLC 时回退默认播放器）"""
        st = self.video_sides.get(side)
        path = st.get('path') if st else None
        if not path or not os.path.isfile(path):
            self.show_toast("请先在对应区域放入视频文件", "warning")
            return
        try:
            vlc = self._find_vlc()
            if vlc:
                subprocess.Popen([vlc, path])
                self.log(f"[视频文件比对] 已用 VLC 原尺寸打开: {path}")
                self.show_toast("已用 VLC 打开视频", "success")
            else:
                if hasattr(os, 'startfile'):
                    os.startfile(path)
                else:
                    subprocess.Popen(['xdg-open', path])
                self.show_toast("未找到 VLC，已用系统默认播放器打开", "info")
        except Exception as e:
            self.show_toast(f"打开失败: {e}", "error")


    def _open_video_folder_window(self):
        """打开“文件夹选择”窗口；若之前关闭过，则恢复其内容、文件夹与滚动位置"""
        win = getattr(self, '_folder_pick_window', None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.restore_scroll_positions()
            win.lift()
            win.focus_force()
            return
        self._folder_pick_window = VideoFolderPickWindow(self)


class VideoFolderPickWindow(tk.Toplevel):
    """文件夹选择窗口：左右两栏分别选择文件夹，以缩略图列出媒体文件，
    双击缩略图将文件加入主界面视频比对的对应（左=A，右=B）区域。"""

    THUMB_SIZES = [64, 96, 128, 160, 200]

    def __init__(self, parent):
        root = parent.frame.winfo_toplevel()
        super().__init__(root)
        self.parent = parent
        self.title("选择文件夹中的视频进行对比")
        self._state = {}
        self._content_hidden = False
        self.size_var = tk.StringVar(value="128")
        self._build()
        self.transient(root)
        self._center()
        self.lift()
        # 关闭只隐藏，保留已读取的文件夹、缩略图与滚动位置，下次打开可恢复
        self.protocol("WM_DELETE_WINDOW", self._hide_window)

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(top, text="缩略图尺寸:").pack(side=tk.LEFT)
        cb = ttk.Combobox(top, textvariable=self.size_var, width=6,
                          values=[str(s) for s in self.THUMB_SIZES],
                          state="readonly")
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._reload_all())
        ttk.Label(top, text="双击缩略图可加入对应侧的视频对比区").pack(side=tk.LEFT, padx=10)
        self.close_btn = ttk.Button(top, text="关闭窗口", command=self._hide_window)
        self.close_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self.hide_btn = ttk.Button(top, text="隐藏内容", command=self._toggle_hide_content)
        self.hide_btn.pack(side=tk.RIGHT, padx=4)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._state['left'] = self._build_panel(body, 'left', 0)
        self._state['right'] = self._build_panel(body, 'right', 1)

    def _build_panel(self, body, side, col):
        title = "左侧文件夹（双击加入 A 区）" if side == 'left' else "右侧文件夹（双击加入 B 区）"
        outer = ttk.LabelFrame(body, text=title)
        outer.grid(row=0, column=col, sticky="nsew", padx=4)
        frow = ttk.Frame(outer)
        frow.pack(fill=tk.X, padx=4, pady=4)
        pv = tk.StringVar()
        entry = ttk.Entry(frow, textvariable=pv)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.bind("<Return>", lambda e, s=side: self._open_folder(s))
        ttk.Button(frow, text="选择文件夹",
                   command=lambda s=side: self._pick_folder(s)).pack(side=tk.LEFT, padx=2)
        ttk.Button(frow, text="打开",
                   command=lambda s=side: self._open_folder(s)).pack(side=tk.LEFT, padx=2)
        sv = tk.StringVar(value="未选择文件夹")
        ttk.Label(outer, textvariable=sv).pack(fill=tk.X, padx=4)
        canvas = tk.Canvas(outer, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=4)
        inner = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e, s=side: self._on_canvas_configure(s, e))
        # 老板键隐藏内容时的占位提示（默认不布局）
        hide_tip = ttk.Label(outer, text="内容已隐藏\n点击“显示内容”恢复",
                             anchor='center', justify='center',
                             foreground="#888888")
        return {'path_var': pv, 'folder': None, 'files': [], 'idx': 0,
                'photos': [], 'tiles': [], 'fpaths': [], 'labels': [],
                'canvases': [], 'canvas_items': [], 'loaded': [],
                'deleted_map': {}, 'done': 0, 'thread_active': False,
                'load_id': 0, 'result_q': None,
                'canvas': canvas, 'inner': inner, 'win_id': win_id,
                'status': sv, 'scroll': scroll, 'hide_tip': hide_tip,
                'yview': None, 'side': side}

    def _hide_window(self):
        """关闭窗口只隐藏，保留已读取的文件夹、缩略图与滚动位置"""
        self._save_scroll_positions()
        self.withdraw()

    def _save_scroll_positions(self):
        """记录左右两栏当前滚动位置（内容隐藏时不覆盖已保存值）"""
        if getattr(self, '_content_hidden', False):
            return
        for side in ('left', 'right'):
            st = self._state.get(side)
            if not st:
                continue
            try:
                st['yview'] = st['canvas'].yview()
            except Exception:
                st['yview'] = None

    def restore_scroll_positions(self):
        """恢复左右两栏的滚动位置（窗口重新显示后调用）"""
        self.update_idletasks()
        for side in ('left', 'right'):
            st = self._state.get(side)
            if not st:
                continue
            yv = st.get('yview')
            if not yv:
                continue
            try:
                st['canvas'].yview_moveto(yv[0])
            except Exception:
                pass

    def _toggle_hide_content(self):
        """老板键：一键隐藏/恢复下方两个文件夹显示区域的内容"""
        hiding = not getattr(self, '_content_hidden', False)
        if hiding:
            self._save_scroll_positions()   # 隐藏前先记录滚动位置
        self._content_hidden = hiding
        for side in ('left', 'right'):
            st = self._state.get(side)
            if not st:
                continue
            if hiding:
                st['canvas'].pack_forget()
                st['scroll'].pack_forget()
                st['hide_tip'].pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            else:
                st['hide_tip'].pack_forget()
                st['canvas'].pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                                  padx=(4, 0), pady=4)
                st['scroll'].pack(side=tk.RIGHT, fill=tk.Y, pady=4)
        self.hide_btn.configure(text="显示内容" if hiding else "隐藏内容")
        if not hiding:
            self.update_idletasks()
            for side in ('left', 'right'):
                self._reflow(side)
            self.restore_scroll_positions()

    def _pick_folder(self, side):
        path = filedialog.askdirectory(title="选择文件夹")
        if path:
            self._state[side]['path_var'].set(path)
            self._open_folder(side)

    def _open_folder(self, side):
        """读取文件夹并立即铺满占位单元，缩略图由后台线程逐步生成（首屏即时可见）"""
        st = self._state[side]
        folder = st['path_var'].get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("提示", "文件夹路径无效，请重新选择或粘贴正确路径后点击“打开”。")
            return
        st['folder'] = folder
        st['load_id'] = st.get('load_id', 0) + 1
        try:
            names = os.listdir(folder)
        except Exception as e:
            messagebox.showerror("错误", f"无法读取文件夹：{e}")
            return
        exts = set(self.parent.VIDEO_EXTENSIONS) | set(self.parent.IMAGE_EXTENSIONS)
        files = sorted(n for n in names
                       if os.path.isfile(os.path.join(folder, n))
                       and os.path.splitext(n)[1].lower() in exts)
        st['files'] = files
        size = int(self.size_var.get())
        self._clear_tiles(side)
        # 立即为所有文件创建占位单元，首屏即时可见（灰色缩略图 + 文件名）
        for name in files:
            self._add_tile(side, os.path.join(folder, name), name, size)
        st['status'].set(f"共 {len(files)} 个媒体文件，正在生成缩略图…")
        self._reflow(side)
        if files:
            self._start_thumb_loader(side)

    def _clear_tiles(self, side):
        st = self._state[side]
        st['thread_active'] = False          # 终止可能仍在运行的后台线程
        for w in list(st['inner'].children.values()):
            w.destroy()
        st['photos'] = []
        st['tiles'] = []
        st['fpaths'] = []
        st['labels'] = []
        st['canvases'] = []
        st['canvas_items'] = []
        st['loaded'] = []
        st['deleted_map'] = {}
        st['idx'] = 0
        st['done'] = 0
        st['status'].set(f"共 {len(st['files'])} 个媒体文件" if st['folder'] else "未选择文件夹")

    def _on_canvas_configure(self, side, event):
        """画布尺寸变化时，让内容宽度跟随并重新排列缩略图"""
        st = self._state[side]
        try:
            st['canvas'].itemconfigure(st['win_id'], width=event.width)
        except Exception:
            pass
        self._reflow(side)

    def _reflow(self, side):
        """按当前可用宽度计算列数，将缩略图以网格方式排列（仅竖向滚动）"""
        st = self._state[side]
        size = int(self.size_var.get())
        cell_w = size + 24
        try:
            avail = st['canvas'].winfo_width()
        except Exception:
            avail = 0
        if avail <= 1:
            avail = 640
        cols = max(1, avail // cell_w)
        for c in range(cols):
            st['inner'].columnconfigure(c, minsize=cell_w, weight=1)
        for i, tile in enumerate(st['tiles']):
            tile.grid(row=i // cols, column=i % cols, padx=2, pady=6, sticky='n')

    def _start_thumb_loader(self, side):
        """后台线程生成缩略图（PIL），主线程轮询队列仅做廉价插入，避免卡顿"""
        import threading, queue
        st = self._state[side]
        load_id = st['load_id']
        folder = st['folder']
        size = int(self.size_var.get())
        files = st['files']
        result_q = queue.Queue()
        st['result_q'] = result_q
        st['thread_active'] = True
        st['done'] = 0

        def worker():
            for idx, name in enumerate(files):
                if st['load_id'] != load_id or not st['thread_active']:
                    return
                fpath = os.path.join(folder, name)
                is_video = os.path.splitext(fpath)[1].lower() in self.parent.VIDEO_EXTENSIONS
                try:
                    pil = self._compose_thumbnail(fpath, size, is_video)
                except Exception:
                    try:
                        from PIL import Image
                        pil = Image.new('RGB', (size, size), (70, 74, 82))
                    except Exception:
                        pil = None
                result_q.put((idx, pil))

        threading.Thread(target=worker, daemon=True).start()
        self._poll_thumb_queue(side, load_id)

    def _poll_thumb_queue(self, side, load_id):
        st = self._state[side]
        if st['load_id'] != load_id or not st['files']:
            return
        q = st.get('result_q')
        if q is not None:
            for _ in range(5):
                if q.empty():
                    break
                try:
                    idx, pil = q.get_nowait()
                except Exception:
                    break
                self._apply_thumb(side, idx, pil)
            st['status'].set(f"已生成缩略图 {st['done']}/{len(st['files'])}")
        if st['thread_active'] or (q is not None and not q.empty()):
            self.after(40, lambda: self._poll_thumb_queue(side, load_id))
        else:
            self._reflow(side)
            st['status'].set(f"共 {len(st['files'])} 个媒体文件")

    def _apply_thumb(self, side, idx, pil):
        st = self._state[side]
        if idx >= len(st['canvases']):
            return
        cv = st['canvases'][idx]
        from PIL import ImageTk
        try:
            if pil is not None:
                photo = ImageTk.PhotoImage(pil)
                st['photos'][idx] = photo
            else:
                photo = st['photos'][idx]
            cv.itemconfig(st['canvas_items'][idx], image=photo)
        except Exception:
            pass
        st['loaded'][idx] = True
        st['done'] += 1

    def _add_tile(self, side, fpath, name, size):
        """新增占位缩略图单元：缩略图在上、文件名在下，缩略图稍后由线程填充"""
        st = self._state[side]
        from PIL import Image, ImageTk
        placeholder = ImageTk.PhotoImage(Image.new('RGB', (size, size), (58, 62, 70)))
        tile = ttk.Frame(st['inner'])
        cv = tk.Canvas(tile, width=size, height=size, bd=0,
                       highlightthickness=0, bg="#2b2f36")
        img_item = cv.create_image(size // 2, size // 2, image=placeholder)
        cv.pack(side=tk.TOP)
        lbl = ttk.Label(tile, text=name, wraplength=size, justify='center',
                        anchor='center')
        lbl.pack(side=tk.TOP, fill=tk.X, pady=(3, 0))
        st['photos'].append(placeholder)
        st['tiles'].append(tile)
        st['fpaths'].append(fpath)
        st['labels'].append(lbl)
        st['canvases'].append(cv)
        st['canvas_items'].append(img_item)
        st['loaded'].append(False)
        for w in (tile, cv, lbl):
            w.bind("<Double-1>", lambda e, p=fpath: self._on_tile_double(side, p))

    def _on_tile_double(self, side, fpath):
        norm = os.path.normcase(os.path.abspath(fpath))
        if norm in self._state[side].get('deleted_map', {}):
            self.parent.show_toast("该文件已被删除，无法加入对比", "warning")
            return
        target = 'A' if side == 'left' else 'B'
        try:
            self.parent._load_video_side(target, fpath)
            self.parent.show_toast(f"已加入对比{target}：{os.path.basename(fpath)}", "success")
        except Exception as e:
            self.parent.show_toast(f"加入失败：{e}", "error")

    def _reload_all(self):
        for side in ('left', 'right'):
            st = self._state[side]
            if st['folder']:
                self._open_folder(side)

    def mark_renamed(self, old_path, new_path):
        """主窗口更名后，同步更新左右两侧列表中该文件的文件名显示"""
        old = os.path.normcase(os.path.abspath(old_path))
        new_name = os.path.basename(new_path)
        for side in ('left', 'right'):
            st = self._state.get(side)
            fpaths = st.get('fpaths') if st else None
            if not fpaths:
                continue
            labels = st.get('labels', [])
            for idx, fp in enumerate(fpaths):
                if os.path.normcase(os.path.abspath(fp)) == old:
                    fpaths[idx] = new_path
                    try:
                        labels[idx].configure(text=new_name)
                    except Exception:
                        pass
            st['status'].set(f"已同步更名：{new_name}")

    def mark_removed(self, path):
        """主窗口删除后，左右两侧列表中匹配的文件打上“已删除”标记（保留位置便于对照）"""
        norm = os.path.normcase(os.path.abspath(path))
        updated = False
        for side in ('left', 'right'):
            st = self._state.get(side)
            fpaths = st.get('fpaths') if st else None
            if not fpaths:
                continue
            st.setdefault('deleted_map', {})[norm] = True
            for idx, fp in enumerate(fpaths):
                if os.path.normcase(os.path.abspath(fp)) == norm:
                    cv = st['canvases'][idx]
                    try:
                        size = cv.winfo_width() or int(cv.cget('width'))
                        cv.create_rectangle(0, 0, size, size,
                                            fill='#1a1a1a', stipple='gray50',
                                            outline='')
                        cv.create_text(size // 2, size // 2, text='已删除',
                                       fill='#ff5252',
                                       font=('Arial', max(10, size // 8)))
                    except Exception:
                        pass
                    updated = True
        if updated:
            for side in ('left', 'right'):
                st = self._state.get(side)
                if st:
                    st['status'].set("列表已同步：有文件被删除")

    def _center(self):
        root = self.parent.frame.winfo_toplevel()
        self.update_idletasks()
        rw, rh = root.winfo_width(), root.winfo_height()
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(1320, sw - 80)
        h = min(840, sh - 120)
        x = rx + max(0, (rw - w) // 2)
        y = ry + max(0, (rh - h) // 2)
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _make_thumbnail(self, fpath, size, is_video):
        """生成方形缩略图（Tk 可显示的 PhotoImage）"""
        from PIL import ImageTk
        return ImageTk.PhotoImage(self._compose_thumbnail(fpath, size, is_video))

    def _compose_thumbnail(self, fpath, size, is_video):
        """合成缩略图：视频加胶片边框+黄色播放角标，图片加蓝色边框+照片角标"""
        from PIL import Image, ImageDraw
        ext = os.path.splitext(fpath)[1].lower()
        im = None
        try:
            if ext in self.parent.IMAGE_EXTENSIONS:
                im = Image.open(fpath).convert('RGB')
            else:
                import cv2
                cap = cv2.VideoCapture(fpath)
                try:
                    ret, frame = cap.read()
                finally:
                    cap.release()
                if ret and frame is not None:
                    im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        except Exception:
            im = None

        W = H = size
        bg = Image.new('RGB', (W, H), (38, 42, 50))
        if im is not None:
            pad = 10 if is_video else 4
            inner_w, inner_h = W - 2 * pad, H - 2 * pad
            im = im.copy()
            im.thumbnail((inner_w, inner_h))
            bg.paste(im, (pad + (inner_w - im.width) // 2,
                          pad + (inner_h - im.height) // 2))

        d = ImageDraw.Draw(bg)
        if is_video:
            # 胶片边框 + 左右齿孔
            d.rectangle([0, 0, W - 1, H - 1], outline=(16, 16, 16), width=3)
            hole_w = max(3, W // 24)
            hole_h = max(4, H // 18)
            step = hole_h + max(4, H // 24)
            y = 7
            while y + hole_h < H - 5:
                for x in (3, W - 3 - hole_w):
                    d.rounded_rectangle([x, y, x + hole_w, y + hole_h],
                                        radius=2, fill=(235, 235, 235))
                y += step
            # 黄色播放角标
            bw = max(26, int(W * 0.30))
            bh = max(18, int(H * 0.20))
            bx, by = W - bw - 4, H - bh - 4
            d.rounded_rectangle([bx, by, bx + bw, by + bh],
                                radius=5, fill=(255, 196, 0))
            cx, cy, s = bx + bw * 0.40, by + bh / 2.0, bh * 0.28
            d.polygon([(cx - s, cy - s), (cx - s, cy + s), (cx + s * 1.3, cy)],
                      fill=(35, 35, 35))
        else:
            # 图片：蓝色边框 + 照片角标
            d.rectangle([0, 0, W - 1, H - 1], outline=(86, 150, 214), width=2)
            bw = max(26, int(W * 0.26))
            bh = max(16, int(H * 0.17))
            bx, by = W - bw - 4, H - bh - 4
            d.rounded_rectangle([bx, by, bx + bw, by + bh],
                                radius=4, fill=(70, 140, 210))
            ix0, iy0 = bx + bw * 0.20, by + bh * 0.25
            ix1, iy1 = bx + bw * 0.80, by + bh * 0.75
            d.rectangle([ix0, iy0, ix1, iy1], outline=(255, 255, 255), width=2)
            d.polygon([(ix0 + 2, iy1 - 2),
                       (ix0 + (ix1 - ix0) * 0.40, iy0 + (iy1 - iy0) * 0.45),
                       (ix0 + (ix1 - ix0) * 0.65, iy1 - 2)],
                      fill=(255, 255, 255))
        return bg


# ==================== 独立运行入口 ====================
class SimpleFolderCheckApp:
    """独立运行时的简单主窗口（用于测试和单独使用）"""

    def __init__(self, root):
        self.root = root
        self.root.title("文件夹检查 / 文件比对工具")
        self.root.geometry("1200x850")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tab = FolderCheckTab(self.notebook, controller=None)
        self.notebook.add(self.tab.get_frame(), text="文件夹检查/比对")

        self.status_bar = ttk.Label(root, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        print("[SimpleFolderCheckApp] 独立运行模式已启动")


def main():
    """独立运行入口"""
    root = tk.Tk()
    app = SimpleFolderCheckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
