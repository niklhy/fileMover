#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置常量模块"""

import os
from pathlib import Path

# 视频文件扩展名
VIDEO_EXTENSIONS = {'.avi', '.mp4', '.ts', '.rmvb', '.rm', '.mkv', '.mov', '.wmv', '.flv', '.m4v', '.mpg', '.mpeg'}

# 配置文件名
CONFIG_FILE_NAME = "file_mover_config.json"

# 获取配置文件路径
def get_config_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 
        CONFIG_FILE_NAME
    )