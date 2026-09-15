#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 数据模型

"""数据模型模块"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict

@dataclass
class MoveRecord:
    """移动记录数据类"""
    source_path: Path
    dest_path: Path
    filename: str
    foldername: str
    item_id: str

@dataclass
class FolderIndex:
    """文件夹索引类"""
    full_code: str
    original_name: str
    path: Path
    letters: str
    numbers: str
    pattern_type: str
    is_short_numeric: bool = False

@dataclass
class VideoConfig:
    """视频移动配置"""
    video_folder: str = ""
    target_folder: str = ""
    include_subfolders: bool = False