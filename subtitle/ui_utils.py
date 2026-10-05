#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DPI 缩放与窗口尺寸工具
"""
import sys
import tkinter as tk
from tkinter import ttk

# ============================================================
# DPI 缩放与窗口尺寸工具（适配 4K 高分辨率显示器）
# ============================================================
UI_SCALE = 1.0


def enable_high_dpi_awareness():
    """在创建任何 Tk 窗口之前启用 DPI 感知，必须在程序启动时调用。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def setup_ui_scaling(root):
    """根据显示器实际 DPI 设置 Tk 缩放比例。"""
    global UI_SCALE
    try:
        dpi = root.winfo_fpixels("1i")
        UI_SCALE = max(1.0, dpi / 96.0)
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        UI_SCALE = 1.0


def sc(value):
    """按 DPI 比例缩放像素尺寸。"""
    return int(round(value * UI_SCALE))


def setup_global_fonts(root):
    """全局放大 Tk/ttk 默认字体，让菜单、按钮、标签等全部变大。"""
    import tkinter.font as tkfont
    font_map = {
        "TkDefaultFont": ("Microsoft YaHei", 12),
        "TkTextFont": ("Microsoft YaHei", 12),
        "TkMenuFont": ("Microsoft YaHei", 12),
        "TkHeadingFont": ("Microsoft YaHei", 13, "bold"),
        "TkCaptionFont": ("Microsoft YaHei", 12),
        "TkSmallCaptionFont": ("Microsoft YaHei", 11),
        "TkIconFont": ("Microsoft YaHei", 12),
        "TkTooltipFont": ("Microsoft YaHei", 11),
        "TkFixedFont": ("Consolas", 11),
    }
    for name, spec in font_map.items():
        try:
            f = tkfont.nametofont(name)
            f.configure(family=spec[0], size=spec[1],
                        weight=spec[2] if len(spec) > 2 else "normal")
        except Exception:
            pass
    try:
        style = ttk.Style(root)
        style.configure(".", font=("Microsoft YaHei", 12))
    except Exception:
        pass


def fit_and_center(win, parent, min_width, min_height):
    """
    将窗口尺寸调整到足以显示全部内部控件，并居中于父窗口范围内。
    """
    win.update_idletasks()
    width = max(sc(min_width), win.winfo_reqwidth() + sc(20))
    height = max(sc(min_height), win.winfo_reqheight() + sc(20))
    try:
        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
    except Exception:
        x, y = 100, 100
    win.geometry(str(width) + "x" + str(height) + "+" + str(max(0, x)) + "+" + str(max(0, y)))
    win.minsize(min(width, sc(min_width)), min(height, sc(min_height)))

