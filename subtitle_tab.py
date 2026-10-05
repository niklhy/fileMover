#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕生成选项卡（日语视频 -> 音频 -> 识别 -> 翻译 -> SRT）

双模式运行：
  1. 嵌入主程序：main.py 中 `SubtitleTab(self.notebook, controller=self)`
  2. 独立运行：`python subtitle_tab.py`

功能：
  · 选择语音模型 / 翻译模型，界面显示所选模型的介绍与组合推荐
  · 任务列表支持拖放（文件夹可递归多级子目录）、多选、移除选中、转换选中、一键清空
  · GPU / CPU 切换，CUDA 不可用或 GPU 处理失败时自动改用 CPU
  · 双语字幕（日文 + 中文）
  · 任务完成后可选：无操作 / 计算机进入睡眠 / 关闭计算机
  · 所有状态提示均为右下角浮出提示（Toast），不弹对话框
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

# 保证独立运行时也能 import 同目录的 subtitle 包
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from subtitle.panel import SubtitlePanel          # noqa: E402
from subtitle.config import enable_offline_env    # noqa: E402
from subtitle.system_utils import ensure_cuda_dll_path  # noqa: E402

try:
    from subtitle import __version__ as __SUBTITLE_VERSION__
except Exception:
    __SUBTITLE_VERSION__ = "3.4.1"


class SubtitleTab:
    """字幕生成选项卡（供主程序 Notebook 使用）"""

    def __init__(self, parent_notebook, controller=None, config_manager=None):
        self.parent = parent_notebook
        self.controller = controller
        self.config_manager = config_manager
        self.panel = SubtitlePanel(parent_notebook, controller=controller)
        self.frame = self.panel.frame

    def get_frame(self):
        return self.frame

    def log(self, message: str):
        """供主程序/其它选项卡调用的日志入口"""
        self.panel._log(message)

    def cleanup(self):
        """关闭主窗口时释放模型与显存"""
        self.panel.cleanup()


def main():
    """独立运行入口"""
    # Windows 控制台切换 UTF-8，避免日文/中文输出乱码
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass

    try:
        from subtitle.ui_utils import (enable_high_dpi_awareness, setup_ui_scaling,
                                       setup_global_fonts, sc)
        enable_high_dpi_awareness()
    except Exception:
        sc = None

    ensure_cuda_dll_path()
    enable_offline_env()

    # 拖放支持：安装 tkinterdnd2 时使用其 Tk，否则退回普通窗口
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()

    root.title("日语视频转中文字幕工具 v" + str(__SUBTITLE_VERSION__))

    try:
        setup_ui_scaling(root)
        setup_global_fonts(root)
    except Exception:
        pass

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    width = min(1180, screen_w - 60)
    height = min(900, screen_h - 80)
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2)
    root.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))

    panel = SubtitlePanel(root, controller=None)
    panel.frame.pack(fill="both", expand=True)

    root.mainloop()


if __name__ == "__main__":
    main()
