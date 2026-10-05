# AGENTS.md - 文件移动工具 (File Mover Tool)

本文档供 AI 编程助手阅读，用于快速了解本项目架构和开发规范。

## 项目概述

这是一个基于 **Python 3 + tkinter** 开发的桌面 GUI 应用程序，主要用于视频文件管理和整理。该工具采用模块化选项卡（Tab）架构，每个功能模块独立实现，通过主控制器协调工作。

### 核心功能

1. **文件夹下移动** (`file_move_tab.py`): 根据番号匹配将视频/所有文件移动到对应文件夹；支持只移动视频文件或移动所有文件，并可通过"保存设置"按钮持久化配置
2. **移动新视频** (`move_videos_tab.py`): 扫描并移动新下载的视频文件到目标位置
3. **文件名整理** (`file_organize_tab.py`): 根据文件夹番号重命名视频、转换图片格式、清理临时文件
4. **视频缩略图** (`video_grid_tab.py`): 生成视频宫格缩略图（多帧拼接）
5. **视频文件检查** (`video_check_tab.py`): 检查文件夹名与视频文件名番号匹配情况
6. **操作日志** (`log_tab.py`): 集中显示所有操作日志

## 技术栈

| 组件 | 用途 |
|------|------|
| Python 3 | 编程语言 |
| tkinter | GUI 框架 |
| PIL/Pillow | 图像处理（格式转换、缩略图生成） |
| OpenCV (cv2) | 视频帧提取 |
| numpy | 图像数组处理 |
| json | 配置持久化 |
| re | 番号模式匹配 |
| pathlib | 跨平台路径处理 |
| threading | 后台任务处理 |

## 项目结构

```
file_mover/
├── main.py                 # 主程序入口，主窗体类 FileMoverGUI
├── config.py               # 全局常量配置（视频扩展名、配置文件路径）
├── models.py               # 数据模型（MoveRecord, FolderIndex, VideoConfig）
├── pattern_matcher.py      # 番号提取与匹配器（核心算法）
│
├── file_move_tab.py        # Tab: 文件夹下移动
├── move_videos_tab.py      # Tab: 移动新视频
├── file_organize_tab.py    # Tab: 文件名整理
├── video_grid_tab.py       # Tab: 视频缩略图
├── video_check_tab.py      # Tab: 视频文件检查
├── subtitle_tab.py         # Tab: 视频语音识别并翻译为字幕（也可独立运行）
├── log_tab.py              # Tab: 操作日志
├── new_feature_tab.py      # 示例：如何添加新选项卡的模板
│
├── toast.py                # 公共组件：右下角浮出式提醒（Toast，全程序复用）
├── subtitle/               # 字幕生成模块（由「视频语音识别并翻译为字幕」项目移植）
│   ├── config.py           #   常量、模型清单、配置读写（subtitle/subtitle_config.json）
│   ├── system_utils.py     #   FFmpeg / CUDA / 模型路径解析与校验
│   ├── core.py             #   TaskItem / SubtitleGenerator / WorkerThread
│   ├── dialogs.py          #   设置对话框 / 依赖检查对话框
│   ├── panel.py            #   SubtitlePanel 面板（可嵌入 Notebook 或独立窗口）
│   └── ui_utils.py         #   DPI 缩放 / 字体 / 窗口居中
│
├── app_config.json         # 应用配置文件（运行时生成）
└── AGENTS.md               # 本文件
```

## 架构设计

### 1. 主控制器模式

`FileMoverGUI` (main.py) 作为主控制器：
- 创建 tkinter Notebook 容器管理所有选项卡
- 提供配置管理接口 (`save_config`, `get_config`)
- 提供跨选项卡日志接口 (`log` 方法)
- 协调各选项卡之间的通信

```python
# 添加新选项卡的步骤（参见 new_feature_tab.py）：
# 1. 创建新文件 xxx_tab.py，实现 Tab 类
# 2. 在 main.py 中导入
# 3. 在 _init_tabs() 中实例化并注册
```

### 2. 选项卡基类约定

每个选项卡类应遵循以下接口约定：

```python
class SomeTab:
    def __init__(self, parent_notebook, controller):
        self.controller = controller
        self.frame = ttk.Frame(parent_notebook)
        # 创建 UI...
        parent_notebook.add(self.frame, text="选项卡标题")
    
    def get_frame(self):
        return self.frame
```

### 3. 配置管理

采用 **单例模式** 的 `ConfigManager` 类管理配置：

```python
# 配置存储在 app_config.json，按选项卡分区
{
  "video_grid": { ... },
  "file_organize": { ... },
  "move_videos": { ... }
}

# 使用方式
from file_organize_tab import ConfigManager
config = ConfigManager()
section = config.get_section("section_name", default={})
config.set_section("section_name", data, auto_save=True)
```

**重要**：每个选项卡应使用独立的配置分区（`CONFIG_SECTION`），避免配置冲突。

**注意**：`file_move_tab.py` 的配置通过界面上的"保存设置"按钮手动保存到 `file_mover_config.json` 的 `file_move_tab` 分区，下次启动时自动恢复。

### 4. 番号匹配系统

`PatternMatcher` (pattern_matcher.py) 是核心算法模块：

- **提取番号**: 从文件名/文件夹名中提取日本 AV 番号格式
  - 标准格式: `XXX-123` (如 SSIS-001, IPX-123)
  - FC2 格式: `FC2-PPV-123456`, `FC2-123456`
  - 数字前缀: `300MIUM-1185`, `259LUXU-1416`
- **匹配算法**: 支持严格匹配、弱匹配、包含匹配等多级匹配策略
- **返回值**: 包含 `full`, `letters`, `numbers`, `canonical` 等标准化字段

### 5. 后台任务处理

耗时操作（文件移动、视频处理）必须在后台线程执行，避免阻塞 GUI：

```python
import threading
import queue

# 创建任务队列
self.task_queue = queue.Queue()

# 启动后台线程
threading.Thread(target=self._process_queue, daemon=True).start()

# 更新 GUI 使用 after() 确保线程安全
def _append_log(self, message):
    self.frame.after(0, self._update_ui, message)
```

### 6. 字幕生成模块（subtitle_tab.py + subtitle/）

把「20260722-视频语音识别并翻译为字幕」项目（v3.4.0）的功能整体移植为一个选项卡：

- `SubtitleTab`：选项卡外壳，`main.py` 中注册为「视频转字幕(subtitle_tab)」；
  `python subtitle_tab.py` 可独立启动完整窗口（内部使用 `SubtitlePanel`）。
- `SubtitlePanel`：真正的界面面板（`ttk.Frame`），既可放进 Notebook，也可放进独立 `Tk` 窗口。
- 处理链路：视频 → ffmpeg 抽 16kHz 音频 → 语音识别（faster-whisper / kotoba-whisper / ReazonSpeech-NeMo）
  → 翻译（NLLB-200 / Sakura-7B / Sakura-14B / Sakura-GalTransl-7B）→ 生成 SRT（可选双语）。
- **模型文件不随程序拷贝**：只保存路径，默认指向 `subtitle/subtitle_config.json` 中的
  `model_root`（原字幕工具项目的模型目录），可在【设置】中修改，也可对每个模型单独指定。
  配置独立于主程序，读写 `subtitle/subtitle_config.json`，不写入 `app_config.json`。
- GPU/CPU：界面单选；CUDA 不可用自动切 CPU；单个视频在 GPU 上失败（CUDA/显存/不支持的格式）
  会自动改用 CPU 重试一次（关键字见 `config.GPU_ERROR_KEYWORDS`）。
- 任务完成后动作：`none` / `sleep` / `shutdown`（在【完成后】下拉框选择，设置对话框同样可改）。
- **不使用 messagebox 报告任务状态**：统一调用 `toast.show_toast(self.frame, ...)` 在右下角浮出提示。
- 任务列表支持拖放（文件夹可递归多级子目录，由“包含子文件夹”控制）、Ctrl/Shift 多选、
  移除选中 / 只处理选中 / 一键清空。拖放依赖 `tkinterdnd2`，未安装时自动降级（仅按钮添加）。

## 开发规范

### 代码风格

1. **文件头**: 每个文件必须包含标准头
   ```python
   #!/usr/bin/env python3
   # -*- coding: utf-8 -*-
   # 功能描述...
   """模块文档字符串"""
   ```

2. **注释**: 使用中文注释，关键逻辑需详细说明

3. **导入顺序**:
   - 标准库 (os, sys, json, re, threading 等)
   - 第三方库 (PIL, cv2, numpy 等)
   - 本地模块 (from pattern_matcher import ...)

4. **类型注解**: 鼓励使用类型提示
   ```python
   from typing import Optional, Dict, List, Tuple
   def extract_code(self, text: str) -> Optional[Dict]:
   ```

### 错误处理

- 文件操作必须加 `try-except`
- 配置读写失败应使用默认值，避免崩溃
- 使用 `messagebox.showerror()` 向用户显示错误

### 模块导入兼容性

支持独立运行和嵌入运行两种模式：

```python
try:
    from pattern_matcher import PatternMatcher
except ImportError:
    # 独立运行时的备用实现
    class PatternMatcher: ...
```

## 运行和测试

### 启动应用

```bash
# 主程序入口
python main.py

# 独立运行某个 Tab（部分支持）
python file_move_tab.py
python file_organize_tab.py

# 独立运行字幕生成工具（完整窗口，与主程序共用 subtitle/ 模块）
python subtitle_tab.py
```

### 依赖安装

项目依赖以下第三方库：

#### 1. Pillow (PIL Fork)
- **用途**: 图像处理（格式转换、缩略图生成、图像绘制）
- **使用位置**: 
  - `file_organize_tab.py`: 图片格式转换 (`Image.open()`, `Image.save()`)
  - `video_grid_tab.py`: 缩略图拼接、添加文字水印 (`ImageDraw`, `ImageFont`)
- **安装命令**:
  ```bash
  pip install Pillow
  ```
- **PyPI**: https://pypi.org/project/Pillow/
- **文档**: https://pillow.readthedocs.io/

#### 2. OpenCV-Python (cv2)
- **用途**: 视频帧提取、视频信息读取（分辨率、帧率、时长）
- **使用位置**: 
  - `video_grid_tab.py`: 视频解码、帧提取、视频信息获取
- **安装命令**:
  ```bash
  pip install opencv-python
  ```
- **注意**: 如果只需要核心功能（不需要视频缩略图），可以不安装。程序会检测并提示
- **PyPI**: https://pypi.org/project/opencv-python/
- **文档**: https://docs.opencv.org/

#### 3. NumPy
- **用途**: 图像数组处理，与 OpenCV 配合使用
- **使用位置**: 
  - `video_grid_tab.py`: 图像数组转换、矩阵操作 (`np.array()`, `np.zeros()`)
- **安装命令**:
  ```bash
  pip install numpy
  ```
- **PyPI**: https://pypi.org/project/numpy/
- **文档**: https://numpy.org/doc/

#### 一键安装所有依赖
```bash
pip install Pillow opencv-python numpy
```

#### 可选依赖（根据系统环境）
- **Windows**: 以上库提供预编译 wheel，直接安装即可
- **Linux**: 可能需要安装系统级依赖（如 `libgl1-mesa-glx` for OpenCV）

#### 依赖检查
项目没有 requirements.txt 或 pyproject.toml，依赖需手动安装。运行前请确保已安装上述库，否则相关功能将显示警告或不可用。

### 测试策略

- 项目**没有自动化测试框架**
- 测试通过手动运行各选项卡功能完成
- 建议修改后测试：
  1. 配置读写是否正常
  2. 番号匹配是否正确
  3. 文件操作是否符合预期（先开启 dry_run 模式）

## 安全注意事项

1. **文件删除操作**: 
   - `file_organize_tab.py` 会删除 `.torrent`, `.lnk` 等文件
   - 提供 `dry_run` 模式预览变更，默认开启

2. **路径遍历**:
   - 使用 `pathlib.Path` 处理路径，避免字符串拼接
   - 文件操作前验证路径存在性

3. **线程安全**:
   - GUI 更新必须通过 `frame.after(0, callback)`
   - 共享数据加锁保护

## 常见任务指南

### 添加新选项卡

1. 复制 `new_feature_tab.py` 为 `xxx_tab.py`
2. 修改类名和界面
3. 在 `main.py` 中添加导入和初始化代码
4. 如需配置持久化，使用 `ConfigManager`

### 修改番号匹配规则

编辑 `pattern_matcher.py`：
- `self.patterns`: 从开头匹配的模式（主匹配）
- `self.search_patterns`: 任意位置搜索的模式（备用匹配）
- `extract_code()`: 提取逻辑
- `is_strict_match()`: 匹配强度判断

### 添加新的视频格式支持

编辑 `config.py`：
```python
VIDEO_EXTENSIONS = {'.avi', '.mp4', '.ts', '.rmvb', '.rm', 
                    '.mkv', '.mov', '.wmv', '.flv', '.m4v', 
                    '.mpg', '.mpeg', '.m2ts', '.new_ext'}
```

## 限制和已知问题

1. **Windows 环境**: 项目路径显示为 Windows 格式（`Z:\`, `Y:\`），主要面向 Windows 用户
2. **中文环境**: 界面和日志均为中文，代码文件使用 UTF-8 编码
3. **无打包脚本**: 没有 PyInstaller 或其他打包配置，需手动运行源码
4. **硬编码配置**: 部分默认路径硬编码在代码中（如下载文件夹路径）

---

*最后更新: 2026-07-01*
