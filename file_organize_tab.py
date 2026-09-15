#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件整理选项卡模块 - 带统一配置管理
支持独立运行和通过main.py嵌入运行
功能：
1. 递归处理子文件夹（可选）
2. 根据文件夹名匹配番号重命名视频文件
3. 匹配成功才重命名图片为cover.jpg
4. webp/bmp转jpg
5. 删除临时文件
6. 显示不完整文件夹统计（无视频/无图片/无法识别/重命名失败）
7. 统一JSON配置管理（多Tab共享）
8. 排除文件夹功能 - 可设置多个排除目录，遍历时跳过
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import json
import shutil
import re
import threading
import queue
from pathlib import Path
from typing import Optional, Dict, List, Set, Callable, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from PIL import Image

# 导入番号匹配器
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    # 如果导入失败，创建一个基本的匹配器（独立运行时）
    class PatternMatcher:
        def extract_code(self, text: str) -> Optional[Dict]:
            import re
            patterns = [
                (re.compile(r'^([fF][cC]2?[_-]?[pP][pP][vV][_-]?(\d{6,}))'), 'fc2_ppv'),
                (re.compile(r'^([fF][cC]2?[_-]?(\d{6,}))'), 'fc2'),
                (re.compile(r'^([a-zA-Z]+\d*[_-]\d+)'), 'standard'),
                (re.compile(r'^(\d{3,}[_-]\d{3,})'), 'numeric'),
            ]
            for pattern_regex, ptype in patterns:
                match = pattern_regex.match(text)
                if match:
                    matched_text = match.group(1)
                    full_code = matched_text.replace('-', '').replace('_', '').lower()
                    letters = ''.join(re.findall(r'[a-zA-Z]+', full_code))
                    numbers = ''.join(re.findall(r'\d+', full_code))
                    return {
                        'full': full_code,
                        'original': matched_text,
                        'canonical': matched_text.replace('_', '-'),  # 添加canonical字段
                        'letters': letters,
                        'numbers': numbers,
                        'type': ptype,
                        'is_short_numeric': False
                    }
            letters = ''.join(re.findall(r'[a-zA-Z]+', text)).lower()
            numbers = ''.join(re.findall(r'\d+', text))
            if letters and numbers:
                full_code = letters + numbers
                original = text.split()[0] if text else text
                return {
                    'full': full_code,
                    'original': original,
                    'canonical': original.replace('_', '-'),  # 添加canonical字段
                    'letters': letters,
                    'numbers': numbers,
                    'type': 'generic',
                    'is_short_numeric': False
                }
            return None

# 常量定义
VIDEO_EXTENSIONS: Set[str] = {'.avi', '.mp4', '.ts', '.rmvb', '.rm', '.mkv', '.mov', '.wmv', '.flv'}
IMAGE_EXTENSIONS: Set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.tiff', '.webp', '.bmp'}
CONVERTIBLE_EXTENSIONS: Set[str] = {'.webp', '.bmp'}
DELETE_EXTENSIONS: Set[str] = {'.torrent', '.lnk'}
SUBTITLE_EXTENSIONS: Set[str] = {'.srt', '.ass', '.ssa', '.vtt', '.sub', '.idx'}

# 正则表达式
PATTERN_UNCENSORED = re.compile(r'uncensored|ai\s*remove|un|uc|-u|无码|破解', re.IGNORECASE)
PATTERN_FC2 = re.compile(r'fc2?|ppv', re.IGNORECASE)
PATTERN_UC = re.compile(r'-UC', re.IGNORECASE)
PATTERN_TILDE_SUFFIX = re.compile(r'~[0-9]+(?=\.)')

CONFIG_FILE_NAME = "app_config.json"


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
class OrganizeConfig:
    """文件整理配置"""
    source_dir: str = ""
    include_subfolders: bool = False
    rename_video: bool = True
    rename_picture: bool = True
    convert_webp: bool = True
    convert_bmp: bool = True
    delete_garbage: bool = True
    rename_subtitle: bool = True
    dry_run: bool = True
    excluded_dirs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'OrganizeConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ========== 文件处理器 ==========
class FileOrganizeWorker:
    """后台处理工作类"""

    def __init__(self, config: OrganizeConfig, 
                 log_callback: Optional[Callable] = None,
                 progress_callback: Optional[Callable] = None,
                 complete_callback: Optional[Callable] = None):
        self.config = config
        self.matcher = PatternMatcher()
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.complete_callback = complete_callback
        self.is_running = True

        # 规范化排除目录路径集合（用于快速判断）
        self.excluded_paths: Set[Path] = set()
        for d in config.excluded_dirs:
            if d:
                try:
                    self.excluded_paths.add(Path(d).resolve())
                except Exception:
                    pass

        self.stats = {
            'processed_dirs': 0,
            'converted': 0,
            'renamed_videos': 0,
            'renamed_pictures': 0,
            'renamed_subtitles': 0,
            'deleted': 0,
            'errors': 0,
            'skipped_no_code': 0,
            'skipped_excluded': 0
        }

        self.no_picture_folders: List[Path] = []      # 有视频无图片
        self.no_video_folders: List[Path] = []        # 有图片无视频
        self.unrecognized_folders: List[Path] = []    # 无法识别番号（新增）
        self.rename_failed_folders: List[Tuple[Path, str]] = []  # (路径, 错误类型)（新增）

        self.error_logs: List[str] = []

    def stop(self):
        self.is_running = False

    def log(self, message: str):
        if self.log_callback and self.is_running:
            self.log_callback(message)

    def update_progress(self, current: int, total: int, message: str = ""):
        if self.progress_callback and self.is_running and total > 0:
            progress = int((current / total) * 100)
            self.progress_callback(progress, message)

    def is_excluded(self, dir_path: Path) -> bool:
        """检查目录是否在排除列表中（包括子目录）"""
        try:
            resolved = dir_path.resolve()
        except Exception:
            return False
        # 直接匹配
        if resolved in self.excluded_paths:
            return True
        # 检查是否是排除目录的子目录（排除目录是当前目录的父目录）
        for excluded in self.excluded_paths:
            try:
                if excluded in resolved.parents:
                    return True
            except Exception:
                pass
        # 字符串路径检查（备用，处理符号链接等特殊情况）
        for excluded in self.excluded_paths:
            try:
                if str(resolved).startswith(str(excluded) + os.sep):
                    return True
            except Exception:
                pass
        return False

    def has_uc_suffix(self, filename: str) -> bool:
        return bool(PATTERN_UC.search(filename))

    def is_uncensored(self, filename: str) -> bool:
        return bool(PATTERN_UNCENSORED.search(filename))

    def remove_tilde_suffix(self, filename: str) -> str:
        return PATTERN_TILDE_SUFFIX.sub('', filename)

    def convert_to_jpg(self, input_path: Path, output_path: Path) -> bool:
        try:
            with Image.open(input_path) as im:
                if im.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', im.size, (255, 255, 255))
                    if im.mode == 'RGBA':
                        background.paste(im, mask=im.split()[-1])
                    elif im.mode == 'LA':
                        background.paste(im, mask=im.split()[-1])
                    else:
                        im_rgb = im.convert('RGBA')
                        background.paste(im_rgb, mask=im_rgb.split()[-1])
                    im = background
                elif im.mode != 'RGB':
                    im = im.convert('RGB')

                im.save(output_path, 'JPEG', quality=95, optimize=True)

            if not self.config.dry_run:
                input_path.unlink()

            self.log(f"  [转换] {input_path.name} -> {output_path.name}")
            self.stats['converted'] += 1
            return True

        except Exception as e:
            error_msg = f"转换失败 {input_path}: {e}"
            self.log(f"  [错误] {error_msg}")
            self.error_logs.append(error_msg)
            self.stats['errors'] += 1
            return False

    def rename_video(self, video_path: Path, code: str, index: int, total_in_group: int = 1) -> Optional[Path]:
        has_uc = self.has_uc_suffix(video_path.name)
        is_uncensored = self.is_uncensored(video_path.name)

        suffix = "-UC" if has_uc else ("-Uncensored" if is_uncensored else "")

        if total_in_group > 1:
            new_name = f"{code}{suffix}-{index}{video_path.suffix}"
        else:
            new_name = f"{code}{suffix}{video_path.suffix}"

        new_path = video_path.parent / new_name

        counter = index
        stem, suffix_ext = new_path.stem, new_path.suffix
        while new_path.exists() and new_path != video_path:
            counter += 1
            new_name = f"{code}{suffix}-{counter}{suffix_ext}"
            new_path = video_path.parent / new_name

        if self.config.dry_run:
            self.log(f"  [试运行] 将重命名: {video_path.name} -> {new_name}")
            return new_path

        try:
            video_path.rename(new_path)
            self.log(f"  [重命名] {video_path.name} -> {new_name}")
            self.stats['renamed_videos'] += 1
            return new_path
        except Exception as e:
            error_msg = f"重命名失败 {video_path}: {e}"
            self.log(f"  [错误] {error_msg}")
            self.error_logs.append(error_msg)
            self.stats['errors'] += 1
            return None

    def rename_subtitle(self, subtitle_path: Path, new_stem: str, index: int, total_in_group: int = 1) -> Optional[Path]:
        """重命名字幕文件，规则与视频文件一致"""
        if total_in_group > 1:
            new_name = f"{new_stem}-{index}{subtitle_path.suffix}"
        else:
            new_name = f"{new_stem}{subtitle_path.suffix}"

        new_path = subtitle_path.parent / new_name

        counter = index
        while new_path.exists() and new_path != subtitle_path:
            counter += 1
            new_name = f"{new_stem}-{counter}{subtitle_path.suffix}"
            new_path = subtitle_path.parent / new_name

        if self.config.dry_run:
            self.log(f"  [试运行] 将重命名字幕: {subtitle_path.name} -> {new_name}")
            return new_path

        try:
            subtitle_path.rename(new_path)
            self.log(f"  [重命名字幕] {subtitle_path.name} -> {new_name}")
            self.stats['renamed_subtitles'] += 1
            return new_path
        except Exception as e:
            error_msg = f"重命名字幕失败 {subtitle_path}: {e}"
            self.log(f"  [错误] {error_msg}")
            self.error_logs.append(error_msg)
            self.stats['errors'] += 1
            return None

    def rename_picture(self, pic_path: Path, index: int) -> Optional[Path]:
        new_name = "cover.jpg" if index == 1 else f"cover({index}).jpg"
        new_path = pic_path.parent / new_name

        temp_name = None
        if new_path.exists() and new_path != pic_path:
            temp_name = pic_path.parent / f"temp_cover_{index}.jpg"
            try:
                new_path.rename(temp_name)
            except Exception as e:
                error_msg = f"临时重命名失败: {e}"
                self.log(f"  [错误] {error_msg}")
                self.error_logs.append(error_msg)
                return None

        if self.config.dry_run:
            self.log(f"  [试运行] 将重命名图片: {pic_path.name} -> {new_name}")
            return new_path

        try:
            pic_path.rename(new_path)
            self.log(f"  [重命名图片] {pic_path.name} -> {new_name}")
            self.stats['renamed_pictures'] += 1
            return new_path
        except Exception as e:
            error_msg = f"重命名图片失败 {pic_path}: {e}"
            self.log(f"  [错误] {error_msg}")
            self.error_logs.append(error_msg)
            if temp_name and temp_name.exists():
                temp_name.rename(new_path)
            self.stats['errors'] += 1
            return None

    def delete_file(self, file_path: Path) -> bool:
        if self.config.dry_run:
            self.log(f"  [试运行] 将删除: {file_path.name}")
            return True

        try:
            file_path.unlink()
            self.log(f"  [删除] {file_path.name}")
            self.stats['deleted'] += 1
            return True
        except Exception as e:
            error_msg = f"删除失败 {file_path}: {e}"
            self.log(f"  [错误] {error_msg}")
            self.error_logs.append(error_msg)
            self.stats['errors'] += 1
            return False

    def process_directory(self, dir_path: Path) -> None:
        if not self.is_running:
            return

        # 检查是否在排除列表中
        if self.is_excluded(dir_path):
            self.log(f"[排除] {dir_path} - 在排除列表中，跳过处理")
            self.stats['skipped_excluded'] += 1
            return

        dir_name = dir_path.name
        self.stats['processed_dirs'] += 1

        code_info = self.matcher.extract_code(dir_name)
        has_valid_code = code_info is not None
        code = code_info['canonical'].upper() if has_valid_code else None

        # 记录无法识别番号的文件夹
        if not has_valid_code:
            self.stats['skipped_no_code'] += 1
            self.log(f"[跳过] {dir_path} - 无法识别番号")
            self.unrecognized_folders.append(dir_path)
        else:
            self.log(f"[处理] {dir_path} - 识别番号: {code}")

        try:
            files = list(dir_path.iterdir())
        except PermissionError:
            error_msg = f"无法访问目录: {dir_path}"
            self.log(f"[错误] {error_msg}")
            self.error_logs.append(error_msg)
            self.rename_failed_folders.append((dir_path, "权限拒绝无法访问"))
            return
        except Exception as e:
            error_msg = f"读取目录失败 {dir_path}: {e}"
            self.log(f"[错误] {error_msg}")
            self.error_logs.append(error_msg)
            self.rename_failed_folders.append((dir_path, f"读取失败: {str(e)}"))
            return

        videos: List[Path] = []
        subtitles: List[Path] = []
        pictures: List[Path] = []
        convertible_files: List[Path] = []
        to_delete: List[Path] = []

        for file_path in files:
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lower()

            if ext in VIDEO_EXTENSIONS:
                videos.append(file_path)
            elif ext in SUBTITLE_EXTENSIONS:
                subtitles.append(file_path)
            elif ext in CONVERTIBLE_EXTENSIONS:
                if (ext == '.webp' and self.config.convert_webp) or \
                   (ext == '.bmp' and self.config.convert_bmp):
                    convertible_files.append(file_path)
                else:
                    if ext in IMAGE_EXTENSIONS:
                        pictures.append(file_path)
            elif ext in IMAGE_EXTENSIONS:
                pictures.append(file_path)
            elif ext in DELETE_EXTENSIONS and self.config.delete_garbage:
                to_delete.append(file_path)

        # 跟踪当前文件夹是否有重命名失败
        has_rename_error = False

        # 按原始文件名排序视频和字幕，确保对应关系
        sorted_videos = sorted(videos, key=lambda x: x.name.lower())
        sorted_subtitles = sorted(subtitles, key=lambda x: x.name.lower())
        total_videos = len(sorted_videos)

        # 处理视频
        if videos and self.config.rename_video and has_valid_code:
            for idx, video_path in enumerate(sorted_videos, 1):
                if not self.is_running:
                    return
                result = self.rename_video(video_path, code, idx, total_videos)
                if result is None and not self.config.dry_run:
                    has_rename_error = True

        # 处理字幕文件（与视频同步重命名）
        if subtitles and self.config.rename_subtitle and has_valid_code:
            has_uc = False
            is_uncensored = False
            if videos:
                first_video = sorted_videos[0]
                has_uc = self.has_uc_suffix(first_video.name)
                is_uncensored = self.is_uncensored(first_video.name)

            suffix = "-UC" if has_uc else ("-Uncensored" if is_uncensored else "")
            new_stem = f"{code}{suffix}"

            for idx, subtitle_path in enumerate(sorted_subtitles, 1):
                if not self.is_running:
                    return
                result = self.rename_subtitle(subtitle_path, new_stem, idx, total_videos)
                if result is None and not self.config.dry_run:
                    has_rename_error = True

        # 转换图片格式
        should_convert = (self.config.convert_webp or self.config.convert_bmp) and convertible_files
        if should_convert:
            for conv_file in convertible_files:
                if not self.is_running:
                    return
                pic_name = f"cover({len(pictures)+1}).jpg" if pictures else "cover.jpg"
                output_path = dir_path / pic_name
                if self.convert_to_jpg(conv_file, output_path):
                    pictures.append(output_path)

        # 重命名图片
        if pictures and self.config.rename_picture and has_valid_code:
            sorted_pics = sorted(pictures, key=lambda x: x.name.lower())
            for idx, pic_path in enumerate(sorted_pics, 1):
                if not self.is_running:
                    return
                if PATTERN_FC2.search(pic_path.name):
                    upper_name = pic_path.stem.upper() + pic_path.suffix
                    if upper_name != pic_path.name:
                        new_path = pic_path.parent / upper_name
                        if not self.config.dry_run:
                            try:
                                pic_path.rename(new_path)
                                self.log(f"  [修正大小写] {pic_path.name} -> {upper_name}")
                                pic_path = new_path
                            except Exception as e:
                                self.log(f"  [错误] 大小写修正失败: {e}")
                                has_rename_error = True
                                continue
                result = self.rename_picture(pic_path, idx)
                if result is None and not self.config.dry_run:
                    has_rename_error = True
        elif pictures and self.config.rename_picture and not has_valid_code:
            self.log(f"  [跳过图片重命名] 文件夹未匹配到番号")

        # 删除垃圾文件
        if self.config.delete_garbage:
            for del_file in to_delete:
                if not self.is_running:
                    return
                self.delete_file(del_file)

        # 检查不完整文件夹
        has_video = len(videos) > 0
        has_picture = len(pictures) > 0

        if has_video and not has_picture:
            self.log(f"  [警告] 该文件夹有视频但无封面图片")
            self.no_picture_folders.append(dir_path)
        elif not has_video and has_picture:
            self.log(f"  [警告] 该文件夹有图片但无视频")
            self.no_video_folders.append(dir_path)

        # 记录重命名失败的文件夹（排除已经记录为无图片/无视频的情况，避免重复）
        if has_rename_error and has_valid_code:
            # 检查是否已存在于其他列表中
            if dir_path not in self.no_picture_folders and dir_path not in self.no_video_folders:
                self.rename_failed_folders.append((dir_path, "文件重命名失败"))

    def run(self) -> Dict:
        """执行处理"""
        self.log("=" * 60)
        self.log("文件整理工具启动")
        self.log(f"源目录: {self.config.source_dir}")
        self.log(f"包含子文件夹: {'是' if self.config.include_subfolders else '否'}")
        self.log(f"试运行模式: {'开启' if self.config.dry_run else '关闭'}")
        if self.config.excluded_dirs:
            self.log(f"排除目录: {len(self.config.excluded_dirs)} 个")
            for d in self.config.excluded_dirs:
                self.log(f"  - {d}")
        self.log("=" * 60)

        source_path = Path(self.config.source_dir) if self.config.source_dir else Path(".")

        if not source_path.exists():
            error_msg = f"源目录不存在: {source_path}"
            self.log(f"[错误] {error_msg}")
            self.error_logs.append(error_msg)
            return self.stats

        # 检查源目录本身是否在排除列表中
        if self.is_excluded(source_path):
            self.log(f"[错误] 源目录 {source_path} 在排除列表中，无法处理")
            self.error_logs.append(f"源目录在排除列表中: {source_path}")
            return self.stats

        dirs_to_process = []
        if self.config.include_subfolders:
            for root_dir in source_path.rglob('*'):
                if root_dir.is_dir():
                    dirs_to_process.append(root_dir)
            dirs_to_process.insert(0, source_path)
        else:
            dirs_to_process = [source_path]

        # 过滤掉排除目录及其子目录
        filtered_dirs = []
        for d in dirs_to_process:
            if not self.is_excluded(d):
                filtered_dirs.append(d)
            else:
                self.log(f"[排除] 跳过目录: {d}")
                self.stats['skipped_excluded'] += 1

        dirs_to_process = filtered_dirs
        total_dirs = len(dirs_to_process)
        self.log(f"发现 {total_dirs} 个目录需要处理（已排除 {self.stats['skipped_excluded']} 个）")

        for idx, dir_path in enumerate(dirs_to_process, 1):
            if not self.is_running:
                self.log("操作已停止")
                break
            self.update_progress(idx, total_dirs, f"处理: {dir_path.name}")
            self.process_directory(dir_path)

        self.log("=" * 60)
        self.log("处理完成统计:")
        self.log(f"  处理目录数: {self.stats['processed_dirs']}")
        self.log(f"  图片格式转换: {self.stats['converted']} (webp/bmp -> jpg)")
        self.log(f"  视频重命名: {self.stats['renamed_videos']}")
        self.log(f"  图片重命名: {self.stats['renamed_pictures']}")
        self.log(f"  字幕重命名: {self.stats['renamed_subtitles']}")
        self.log(f"  删除文件: {self.stats['deleted']}")
        self.log(f"  错误数: {self.stats['errors']}")
        self.log(f"  跳过(无番号): {self.stats['skipped_no_code']}")
        self.log(f"  跳过(排除目录): {self.stats['skipped_excluded']}")

        if self.unrecognized_folders:
            self.log(f"  无法识别番号: {len(self.unrecognized_folders)}个")
        if self.no_picture_folders:
            self.log(f"  无图片的视频文件夹: {len(self.no_picture_folders)}个")
        if self.no_video_folders:
            self.log(f"  无视频的图片文件夹: {len(self.no_video_folders)}个")
        if self.rename_failed_folders:
            self.log(f"  重命名失败: {len(self.rename_failed_folders)}个")
        self.log("=" * 60)

        if self.complete_callback:
            self.complete_callback(
                self.stats, 
                self.no_picture_folders, 
                self.no_video_folders, 
                self.error_logs,
                self.unrecognized_folders,
                self.rename_failed_folders
            )

        return self.stats


# ========== GUI选项卡类 ==========
class FileOrganizeTab:
    """文件整理选项卡"""

    CONFIG_SECTION = "file_organize"  # 配置分区标识

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

        # 排除目录列表
        self.excluded_dirs: List[str] = []

        # 创建界面
        self._create_widgets()
        self._load_from_config_manager()
        self._bind_events()

    def _create_widgets(self):
        """创建界面"""
        self.frame = ttk.Frame(self.parent)

        # 功能说明 - 可折叠
        self.info_expanded = tk.BooleanVar(value=False)
        info_frame = ttk.LabelFrame(self.frame, text="功能说明")
        info_frame.pack(fill=tk.X, padx=10, pady=2)

        info_header = ttk.Frame(info_frame)
        info_header.pack(fill=tk.X, padx=5, pady=2)

        self.info_toggle_btn = ttk.Button(info_header, text="▼ 展开", width=10,
                                          command=self._toggle_info)
        self.info_toggle_btn.pack(side=tk.LEFT)
        ttk.Label(info_header, text="点击展开查看功能说明", foreground="gray").pack(side=tk.LEFT, padx=10)

        self.info_content_frame = ttk.Frame(info_frame)
        # 默认不pack，折叠状态

        info_text = (
            "本工具用于整理视频和图片文件：\n"
            "1. 根据文件夹名称自动识别番号\n"
            "2. 重命名视频文件为番号格式（多文件自动添加-1,-2后缀）\n"
            "3. 同步重命名字幕文件（.srt/.ass/.ssa/.vtt/.sub/.idx），命名规则与视频一致\n"
            "4. 仅在识别到有效番号时，才将图片重命名为cover.jpg\n"
            "5. 自动转换webp、bmp格式为jpg\n"
            "6. 删除torrent、lnk等垃圾文件\n"
            "7. 统计并显示无视频、无图片、无法识别番号或重命名失败的文件夹\n"
            "8. 支持设置排除目录，排除的文件夹及其子文件夹不会被处理"
        )
        ttk.Label(self.info_content_frame, text=info_text, justify=tk.LEFT).pack(padx=10, pady=5, anchor=tk.W)

        # 源目录设置
        dir_frame = ttk.LabelFrame(self.frame, text="源目录设置")
        dir_frame.pack(fill=tk.X, padx=10, pady=5)

        dir_row = ttk.Frame(dir_frame)
        dir_row.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(dir_row, text="目录路径:").pack(side=tk.LEFT, padx=(0, 5))
        self.dir_var = tk.StringVar()
        ttk.Entry(dir_row, textvariable=self.dir_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(dir_row, text="浏览...", command=self._browse_directory).pack(side=tk.RIGHT)

        # ====== 排除目录设置 ======
        exclude_frame = ttk.LabelFrame(self.frame, text="排除目录设置（选中的文件夹及其子文件夹将不会被处理）")
        exclude_frame.pack(fill=tk.X, padx=10, pady=5)

        exclude_top = ttk.Frame(exclude_frame)
        exclude_top.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(exclude_top, text="添加排除文件夹...", command=self._add_exclude_dir).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(exclude_top, text="移除选中", command=self._remove_selected_exclude).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(exclude_top, text="清空全部", command=self._clear_all_exclude).pack(side=tk.LEFT)

        # 排除目录列表
        exclude_list_frame = ttk.Frame(exclude_frame)
        exclude_list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.exclude_listbox = tk.Listbox(exclude_list_frame, height=4, selectmode=tk.EXTENDED)
        self.exclude_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        exclude_scrollbar = ttk.Scrollbar(exclude_list_frame, orient=tk.VERTICAL, command=self.exclude_listbox.yview)
        self.exclude_listbox.configure(yscrollcommand=exclude_scrollbar.set)
        exclude_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 操作选项
        option_frame = ttk.LabelFrame(self.frame, text="操作选项")
        option_frame.pack(fill=tk.X, padx=10, pady=5)

        # 使用网格布局 - 4列2行，充分利用横向空间
        opt_container = ttk.Frame(option_frame)
        opt_container.pack(fill=tk.X, padx=10, pady=5)
        opt_container.columnconfigure(0, weight=1)
        opt_container.columnconfigure(1, weight=1)
        opt_container.columnconfigure(2, weight=1)
        opt_container.columnconfigure(3, weight=1)

        # 第一行
        self.include_subfolders_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_container, text="包含子文件夹（递归处理）",
                       variable=self.include_subfolders_var).grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)

        self.rename_video_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="重命名视频文件",
                       variable=self.rename_video_var).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        self.rename_subtitle_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="重命名字幕文件",
                       variable=self.rename_subtitle_var).grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)

        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="仅演示（不实际操作）",
                       variable=self.dry_run_var).grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)

        # 第二行
        self.rename_picture_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="重命名图片为cover.jpg",
                       variable=self.rename_picture_var).grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)

        self.convert_webp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="转换webp为jpg",
                       variable=self.convert_webp_var).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        self.convert_bmp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="转换bmp为jpg",
                       variable=self.convert_bmp_var).grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)

        self.delete_garbage_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_container, text="删除垃圾文件(torrent/lnk)",
                       variable=self.delete_garbage_var).grid(row=1, column=3, sticky=tk.W, padx=5, pady=2)

        # 进度条
        progress_frame = ttk.LabelFrame(self.frame, text="操作进度")
        progress_frame.pack(fill=tk.X, padx=10, pady=5)

        self.progress_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, 
                                           length=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)

        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack(pady=(0, 5))

        self.stats_var = tk.StringVar(value="待处理: 0 | 已处理: 0 | 错误: 0 | 跳过: 0")
        ttk.Label(progress_frame, textvariable=self.stats_var).pack(pady=(0, 5))

        # 结果表格 - 修改标题以反映新增功能
        # ========== 底部操作栏 ==========
        bottom_frame = ttk.Frame(self.frame)
        bottom_frame.pack(fill=tk.X, padx=10, pady=5)

        # 左侧：开始执行 / 停止
        left_btn_frame = ttk.Frame(bottom_frame)
        left_btn_frame.pack(side=tk.LEFT)

        self.start_btn = ttk.Button(left_btn_frame, text="开始执行",
                                   command=self._start_operation, width=15)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = ttk.Button(left_btn_frame, text="停止",
                                 command=self._stop_operation,
                                 width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))

        # 中间：清空列表
        ttk.Button(bottom_frame, text="清空列表",
                  command=self._clear_table).pack(side=tk.LEFT, padx=5)

        # 右侧：立即保存配置
        ttk.Button(bottom_frame, text="立即保存配置",
                  command=self._save_to_config_manager).pack(side=tk.RIGHT, padx=5)



        table_frame = ttk.LabelFrame(self.frame, text="文件夹检查结果（异常状态清单）")
        table_frame.pack(fill=tk.BOTH, padx=10, pady=5)

        columns = ('folder_path', 'status')
        self.result_tree = ttk.Treeview(table_frame, columns=columns,
                                       show='headings', height=16)

        self.result_tree.heading('folder_path', text='文件夹路径')
        self.result_tree.heading('status', text='状态说明')
        self.result_tree.column('folder_path', width=500)
        self.result_tree.column('status', width=150)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, 
                                 command=self.result_tree.yview)
        self.result_tree.configure(yscroll=scrollbar.set)

        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _bind_events(self):
        """绑定配置自动保存事件"""
        # 所有选项变更时自动保存到配置管理器
        self.dir_var.trace('w', lambda *args: self._save_to_config_manager())
        self.include_subfolders_var.trace('w', lambda *args: self._save_to_config_manager())
        self.rename_video_var.trace('w', lambda *args: self._save_to_config_manager())
        self.rename_subtitle_var.trace('w', lambda *args: self._save_to_config_manager())
        self.rename_picture_var.trace('w', lambda *args: self._save_to_config_manager())
        self.convert_webp_var.trace('w', lambda *args: self._save_to_config_manager())
        self.convert_bmp_var.trace('w', lambda *args: self._save_to_config_manager())
        self.delete_garbage_var.trace('w', lambda *args: self._save_to_config_manager())
        self.dry_run_var.trace('w', lambda *args: self._save_to_config_manager())

    def _add_exclude_dir(self):
        """添加排除目录"""
        initial_dir = self._get_last_browse_dir()
        directory = filedialog.askdirectory(
            title="选择要排除的文件夹",
            initialdir=initial_dir if initial_dir else None
        )
        if directory:
            # 检查是否已存在
            if directory in self.excluded_dirs:
                messagebox.showinfo("提示", "该目录已在排除列表中")
                return
            # 检查是否与源目录冲突
            source_dir = self.dir_var.get().strip()
            if source_dir and directory == source_dir:
                messagebox.showwarning("警告", "排除目录不能与源目录相同")
                return
            self.excluded_dirs.append(directory)
            self.exclude_listbox.insert(tk.END, directory)
            self._save_to_config_manager()

    def _remove_selected_exclude(self):
        """移除选中的排除目录"""
        selected = self.exclude_listbox.curselection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要移除的目录")
            return
        # 从后往前删除，避免索引变化
        for idx in reversed(selected):
            self.exclude_listbox.delete(idx)
            del self.excluded_dirs[idx]
        self._save_to_config_manager()

    def _clear_all_exclude(self):
        """清空所有排除目录"""
        if not self.excluded_dirs:
            return
        if messagebox.askyesno("确认", "确定要清空所有排除目录吗？"):
            self.excluded_dirs.clear()
            self.exclude_listbox.delete(0, tk.END)
            self._save_to_config_manager()

    def _save_to_config_manager(self):
        """保存当前设置到配置管理器"""
        config_data = {
            'source_dir': self.dir_var.get(),
            'include_subfolders': self.include_subfolders_var.get(),
            'rename_video': self.rename_video_var.get(),
            'rename_subtitle': self.rename_subtitle_var.get(),
            'rename_picture': self.rename_picture_var.get(),
            'convert_webp': self.convert_webp_var.get(),
            'convert_bmp': self.convert_bmp_var.get(),
            'delete_garbage': self.delete_garbage_var.get(),
            'dry_run': self.dry_run_var.get(),
            'excluded_dirs': self.excluded_dirs.copy()
        }
        # 使用配置管理器保存（带分区标识）
        self.config_manager.set_section(self.CONFIG_SECTION, config_data, auto_save=True)

    def _load_from_config_manager(self):
        """从配置管理器加载设置"""
        # 从配置管理器读取（带分区标识）
        section_config = self.config_manager.get_section(self.CONFIG_SECTION)

        if section_config:
            self.dir_var.set(section_config.get('source_dir', ''))
            self.include_subfolders_var.set(section_config.get('include_subfolders', False))
            self.rename_video_var.set(section_config.get('rename_video', True))
            self.rename_subtitle_var.set(section_config.get('rename_subtitle', True))
            self.rename_picture_var.set(section_config.get('rename_picture', True))
            self.convert_webp_var.set(section_config.get('convert_webp', True))
            self.convert_bmp_var.set(section_config.get('convert_bmp', True))
            self.delete_garbage_var.set(section_config.get('delete_garbage', True))
            self.dry_run_var.set(section_config.get('dry_run', True))
            # 加载排除目录
            self.excluded_dirs = section_config.get('excluded_dirs', [])
            if isinstance(self.excluded_dirs, str):
                self.excluded_dirs = [self.excluded_dirs] if self.excluded_dirs else []
            self.exclude_listbox.delete(0, tk.END)
            for d in self.excluded_dirs:
                if d:
                    self.exclude_listbox.insert(tk.END, d)
        else:
            # 兼容旧版配置
            old_config = self.config_manager.get_section('file_organize_tab')
            if old_config:
                self.dir_var.set(old_config.get('source_dir', old_config.get('directory', '')))

    def _get_last_browse_dir(self) -> str:
        """获取上次浏览的默认目录"""
        source_dir = self.dir_var.get().strip()
        if source_dir and os.path.exists(source_dir):
            return source_dir
        if source_dir:
            parent = os.path.dirname(source_dir)
            if parent and os.path.exists(parent):
                return parent
        last_browse = self.config_manager.get(self.CONFIG_SECTION, 'last_browse_dir', '')
        if last_browse and os.path.exists(last_browse):
            return last_browse
        return ''

    def _save_last_browse_dir(self, directory: str):
        """保存上次浏览的目录到配置"""
        self.config_manager.set(self.CONFIG_SECTION, 'last_browse_dir', directory, auto_save=True)

    def _browse_directory(self):
        """浏览选择目录"""
        initial_dir = self._get_last_browse_dir()
        directory = filedialog.askdirectory(
            title="选择源目录",
            initialdir=initial_dir if initial_dir else None
        )
        if directory:
            self.dir_var.set(directory)
            self._save_last_browse_dir(directory)
            # 如果排除列表中有与源目录相同的，自动移除
            if directory in self.excluded_dirs:
                idx = self.excluded_dirs.index(directory)
                self.excluded_dirs.pop(idx)
                self.exclude_listbox.delete(idx)
                self._save_to_config_manager()
            else:
                self._save_to_config_manager()  # 立即保存

    def _log(self, message: str):
        """记录日志"""
        if not self.is_standalone and self.controller and hasattr(self.controller, 'log'):
            self.controller.log(message)
        else:
            print(message)

    def _update_progress(self, progress: int, message: str):
        """更新进度条"""
        self.operation_queue.put(('progress', progress, message))

    def _on_complete(self, stats, no_picture_folders, no_video_folders, error_logs, unrecognized_folders=None, rename_failed_folders=None):
        """完成回调"""
        self.operation_queue.put(('complete', stats, no_picture_folders, no_video_folders, error_logs, 
                                 unrecognized_folders or [], rename_failed_folders or []))

    def _start_operation(self):
        """开始执行操作"""
        source_dir = self.dir_var.get().strip()
        if not source_dir:
            messagebox.showwarning("警告", "请选择源目录")
            return

        source_path = Path(source_dir)
        if not source_path.exists():
            messagebox.showwarning("警告", f"目录不存在: {source_dir}")
            return

        # 检查源目录是否在排除列表中
        if source_dir in self.excluded_dirs:
            messagebox.showwarning("警告", "源目录不能同时出现在排除列表中，已自动移除")
            idx = self.excluded_dirs.index(source_dir)
            self.excluded_dirs.pop(idx)
            self.exclude_listbox.delete(idx)
            self._save_to_config_manager()

        if not self.dry_run_var.get():
            if not messagebox.askyesno("确认", "您选择了实际执行模式（非演示），确定要继续吗？"):
                return

        self._clear_table()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.is_running = True

        self.progress_bar['value'] = 0
        self.progress_label.config(text="正在初始化...")
        self.stats_var.set("正在计算...")

        # 创建配置
        config = OrganizeConfig(
            source_dir=source_dir,
            include_subfolders=self.include_subfolders_var.get(),
            rename_video=self.rename_video_var.get(),
            rename_picture=self.rename_picture_var.get(),
            rename_subtitle=self.rename_subtitle_var.get(),
            convert_webp=self.convert_webp_var.get(),
            convert_bmp=self.convert_bmp_var.get(),
            delete_garbage=self.delete_garbage_var.get(),
            dry_run=self.dry_run_var.get(),
            excluded_dirs=self.excluded_dirs.copy()
        )

        self._log(f"\n{'='*60}")
        self._log("开始文件整理操作")
        self._log(f"源目录: {source_dir}")
        self._log(f"递归处理: {'是' if config.include_subfolders else '否'}")
        self._log(f"模式: {'演示' if config.dry_run else '实际执行'}")
        if config.excluded_dirs:
            self._log(f"排除目录数: {len(config.excluded_dirs)}")
        self._log(f"{'='*60}\n")

        # 启动工作线程
        self.worker = FileOrganizeWorker(
            config=config,
            log_callback=self._log,
            progress_callback=self._update_progress,
            complete_callback=self._on_complete
        )

        thread = threading.Thread(target=self._run_worker, daemon=True)
        thread.start()
        self._update_ui()

    def _run_worker(self):
        """后台线程"""
        try:
            self.worker.run()
        except Exception as e:
            self.operation_queue.put(('error', str(e)))
        finally:
            self.operation_queue.put(('finished', None))

    def _stop_operation(self):
        """停止操作"""
        self.is_running = False
        if self.worker:
            self.worker.stop()
        self.progress_label.config(text="操作已停止")
        self._log("操作已被用户停止")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

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

                elif msg_type == 'complete':
                    _, stats, no_pic_folders, no_vid_folders, error_logs, unrecognized_folders, rename_failed_folders = msg

                    self.stats_var.set(
                        f"完成 | 目录: {stats['processed_dirs']} | "
                        f"视频重命名: {stats['renamed_videos']} | "
                        f"图片重命名: {stats['renamed_pictures']} | "
                        f"错误: {stats['errors']} | 跳过: {stats['skipped_no_code']} | "
                        f"排除: {stats.get('skipped_excluded', 0)}"
                    )
                    self.progress_label.config(text="处理完成")
                    self.progress_bar['value'] = 100

                    # 按优先级插入表格：无法识别 -> 重命名失败 -> 无图片 -> 无视频
                    for folder in unrecognized_folders:
                        self.result_tree.insert('', tk.END, values=(str(folder), "无法识别番号"))

                    for folder, error_type in rename_failed_folders:
                        self.result_tree.insert('', tk.END, values=(str(folder), error_type))

                    for folder in no_pic_folders:
                        self.result_tree.insert('', tk.END, values=(str(folder), "有视频无图片"))

                    for folder in no_vid_folders:
                        self.result_tree.insert('', tk.END, values=(str(folder), "有图片无视频"))

                    # 计算总问题数
                    total_issues = (len(unrecognized_folders) + len(rename_failed_folders) + 
                                  len(no_pic_folders) + len(no_vid_folders))

                    if not self.dry_run_var.get():
                        msg_text = (f"操作完成！\n处理目录: {stats['processed_dirs']}\n"
                                   f"视频重命名: {stats['renamed_videos']}\n"
                                   f"字幕重命名: {stats.get('renamed_subtitles', 0)}\n"
                                   f"图片重命名: {stats['renamed_pictures']}\n"
                                   f"图片格式转换: {stats['converted']}\n"
                                   f"无法识别番号: {len(unrecognized_folders)}\n"
                                   f"重命名失败: {len(rename_failed_folders)}\n"
                                   f"文件缺失: {len(no_pic_folders) + len(no_vid_folders)}\n"
                                   f"排除目录: {stats.get('skipped_excluded', 0)}\n"
                                   f"异常文件夹总计: {total_issues}")
                        messagebox.showinfo("完成", msg_text)

                    self._log(f"\n异常统计：无法识别({len(unrecognized_folders)}), "
                             f"重命名失败({len(rename_failed_folders)}), "
                             f"无图片({len(no_pic_folders)}), 无视频({len(no_vid_folders)}), "
                             f"排除目录({stats.get('skipped_excluded', 0)})")
                    self._log("操作完成")

                elif msg_type == 'error':
                    _, error_msg = msg
                    self._log(f"[严重错误] {error_msg}")
                    messagebox.showerror("错误", f"操作失败: {error_msg}")

                elif msg_type == 'finished':
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self.is_running = False

        except queue.Empty:
            pass

        if self.is_running:
            self.frame.after(100, self._update_ui)

    def _clear_table(self):
        """清空结果表格"""
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)

    def _toggle_info(self):
        """折叠/展开功能说明"""
        if self.info_expanded.get():
            # 当前展开，执行折叠
            self.info_content_frame.pack_forget()
            self.info_toggle_btn.config(text="▼ 展开")
            self.info_expanded.set(False)
        else:
            # 当前折叠，执行展开
            self.info_content_frame.pack(fill=tk.X, padx=5, pady=2)
            self.info_toggle_btn.config(text="▲ 收起")
            self.info_expanded.set(True)

    def get_frame(self) -> ttk.Frame:
        """获取选项卡主框架"""
        return self.frame


# ========== 独立运行入口 ==========
if __name__ == "__main__":
    root = tk.Tk()
    root.title("文件整理工具 - 独立版")
    root.geometry("900x800")

    tab = FileOrganizeTab(root, controller=None)
    tab.get_frame().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    root.mainloop()