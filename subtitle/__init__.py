#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕生成模块（日语视频 -> 音频 -> 识别 -> 翻译 -> SRT）

本包由「20260722-视频语音识别并翻译为字幕」项目（v3.4.0）移植而来，
作为「20260915-视频文件管理工具」的一个选项卡使用，也可独立运行。

模块说明：
    config       - 常量、模型清单、配置读写
    ui_utils     - DPI 缩放 / 字体 / 窗口居中
    system_utils - FFmpeg、CUDA、模型路径解析与校验
    core         - TaskItem / SubtitleGenerator / WorkerThread
    dialogs      - 设置对话框 / 依赖检查对话框
    panel        - 可嵌入的主界面面板（SubtitlePanel）
"""

__version__ = "3.4.1"
