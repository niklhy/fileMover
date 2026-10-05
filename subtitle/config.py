#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字幕生成模块 - 配置管理与常量定义

模型文件不随程序拷贝，统一通过【设置】中的路径指向固定存放位置。
默认模型根目录 DEFAULT_MODEL_ROOT 指向原字幕工具项目的模型目录，
可在设置对话框中修改（配置项: model_root），也可对每个模型单独指定路径。
"""
import os
import json

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_PKG_DIR)

# ============================================================
# 离线运行补丁（仅在真正开始处理 / 独立启动时调用，避免影响主程序其它模块）
# ============================================================

def enable_offline_env():
    """禁用所有 HuggingFace 联网行为，保证模型全部从本地加载。"""
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    os.environ["HF_HUB_XET_TIMEOUT"] = "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


# ============================================================
# Windows 修复：NeMo transcribe 内部用 tempfile.TemporaryDirectory 存放
# 临时 manifest.json，结束时若文件句柄尚未释放，清理会抛
# WinError 267/32 导致识别结果被异常吞掉。这里让临时目录清理
# 失败时静默忽略（残留文件由系统 TEMP 清理机制处理）。
# ============================================================
import tempfile as _tempfile_mod

_OriginalTemporaryDirectory = _tempfile_mod.TemporaryDirectory


class _SafeTemporaryDirectory(_OriginalTemporaryDirectory):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ignore_cleanup_errors", True)
        super().__init__(*args, **kwargs)


_tempfile_mod.TemporaryDirectory = _SafeTemporaryDirectory


# ============================================================
# 模型根目录（不拷贝模型文件，只保存路径）
# ============================================================

# 默认模型存放位置：原字幕工具项目的模型目录（语音模型/、翻译模型/ 都在其下）
DEFAULT_MODEL_ROOT = r"D:\软件编程\python\练习\20260722-视频语音识别并翻译为字幕"

# 各模型在模型根目录下的相对位置
MODEL_SUBPATHS = {
    "translator": os.path.join("翻译模型", "NLLB-200", "nllb-200-distilled-600M"),
    "sakura": os.path.join("翻译模型", "SakuraLLM", "Sakura-14B-Qwen2.5-v1.0-GGUF",
                           "sakura-14b-qwen2.5-v1.0-q4km.gguf"),
    "sakura7b": os.path.join("翻译模型", "SakuraLLM", "Sakura-7B-Qwen2.5-v1.0-GGUF",
                             "sakura-7b-qwen2.5-v1.0-iq4xs.gguf"),
    "galtransl": os.path.join("翻译模型", "SakuraLLM", "Sakura-GalTransl-7B-v3.7",
                              "Sakura-Galtransl-7B-v3.7.gguf"),
    "kotoba": os.path.join("语音模型", "kotoba-whisper-v2.1"),
    "nemo": os.path.join("语音模型", "reazonspeech-nemo-v2", "reazonspeech-nemo-v2.nemo"),
    "whisper": os.path.join("语音模型", "faster-whisper"),
}


def get_model_root(config=None):
    """模型根目录：配置文件中的 model_root，否则使用默认目录。"""
    if config is None:
        config = load_config()
    root = (config.get("model_root") or "").strip()
    if root:
        return normalize_path(root)
    return normalize_path(DEFAULT_MODEL_ROOT)


def default_model_path(key, config=None):
    """按模型根目录推导某个模型的默认路径。"""
    sub = MODEL_SUBPATHS.get(key, "")
    return os.path.join(get_model_root(config), sub) if sub else ""


# ============================================================
# 模型清单与推荐（界面上的模型介绍）
# ============================================================

WHISPER_MODELS = {
    "tiny": {"size": "39MB", "speed": "最快", "accuracy": "一般", "vram": "~1GB"},
    "base": {"size": "74MB", "speed": "快", "accuracy": "较好", "vram": "~1GB"},
    "small": {"size": "244MB", "speed": "中等", "accuracy": "良好", "vram": "~2GB"},
    "medium": {"size": "769MB", "speed": "较慢", "accuracy": "很好", "vram": "~5GB"},
    "large-v3": {"size": "1.5GB", "speed": "最慢", "accuracy": "最佳", "vram": "~10GB"}
}

# 语音识别引擎（三选一）
ASR_ENGINES = {
    "faster-whisper": "通用模型，可选 tiny~large-v3 五种大小，兼容性好",
    "kotoba-whisper": "日语优化（约2.9GB）| 动漫/日剧口语识别更准 | 速度较快",
    "reazonspeech-nemo": "日语长音频优化（约2.4GB）| 1小时以上剧集时间戳不漂移 | 无内容过滤",
}

# 翻译引擎（四选一）
TRANSLATOR_ENGINES = {
    "NLLB-200": "轻量快速（600M）| 通用风格 | 每条<1秒 | 日常批量处理首选",
    "Sakura-7B": "轻小说风格，质量高（约4GB）| 实测加载约45秒 | 每条<1秒（全GPU）| 质量/速度均衡首选",
    "Sakura-14B": "轻小说风格，质量最高（约9GB）| 加载约2~3分钟 | 8GB显卡每条约20~30秒 | 适合精翻",
    "Sakura-GalTransl-7B": "Galgame专项翻译（约6.3GB）| 成人对话更直接 | 实测加载约1分钟 | 每条<1秒（全GPU）",
}

# 组合推荐（显示在界面上供参考）
ENGINE_RECOMMENDATION = (
    "组合建议：日常快速批量 → kotoba-whisper + NLLB-200 最均衡；"
    "质量与速度兼顾 → Sakura-7B；追求最高翻译质量 → Sakura-14B（较慢）；"
    "长时间剧集（1小时以上）→ reazonspeech-nemo 时间戳不漂移"
)

# 任务完成后操作选项
AFTER_FINISH_ACTIONS = {
    "none": "无操作",
    "sleep": "计算机进入睡眠",
    "shutdown": "关闭计算机",
}

REQUIRED_PACKAGES = {
    "faster-whisper": {
        "pip_name": "faster-whisper",
        "description": "语音识别引擎（faster-whisper 系列模型）",
        "size": "约50MB"
    },
    "transformers": {
        "pip_name": "transformers",
        "description": "NLLB-200 翻译 / kotoba-whisper 识别运行框架",
        "size": "约10MB"
    },
    "torch": {
        "pip_name": "torch",
        "description": "PyTorch深度学习框架（GPU 需 CUDA 版）",
        "size": "约2GB"
    },
    "sentencepiece": {
        "pip_name": "sentencepiece",
        "description": "文本分词库（NLLB 分词需要）",
        "size": "约2MB"
    },
    "srt": {
        "pip_name": "srt",
        "description": "SRT字幕文件处理库",
        "size": "约50KB"
    },
    "llama-cpp-python": {
        "pip_name": "llama-cpp-python",
        "description": "Sakura GGUF 翻译模型运行库（可选）",
        "size": "约40MB",
        "import_name": "llama_cpp"
    },
    "nemo_toolkit": {
        "pip_name": "nemo_toolkit[asr]",
        "description": "ReazonSpeech-NeMo 语音识别框架（可选）",
        "size": "约300MB",
        "import_name": "nemo"
    }
}

# Sakura 翻译模式（速度/质量选项，设置界面可切换）
SAKURA_MODES = {
    "速度优先": {"temperature": 0.1, "top_p": 0.3, "max_tokens": 256, "n_ctx": 1024,
               "desc": "最快，翻译简洁，适合批量粗翻"},
    "均衡（推荐）": {"temperature": 0.1, "top_p": 0.3, "max_tokens": 512, "n_ctx": 2048,
                 "desc": "速度与质量兼顾，适合大多数剧集"},
    "质量优先": {"temperature": 0.3, "top_p": 0.6, "max_tokens": 768, "n_ctx": 4096,
               "desc": "措辞更丰富、更少自我审查，速度最慢"},
}

# 配置文件路径（本模块独立配置，不写入主程序的 app_config.json）
CONFIG_FILE = os.path.join(_PKG_DIR, "subtitle_config.json")

# 配置文件中所有需要规范化（统一为正斜杠）的路径字段
_PATH_KEYS = (
    "ffmpeg_path", "model_root", "model_base_path", "translator_model_path",
    "sakura_model_path", "sakura7b_model_path", "galtransl_model_path",
    "kotoba_model_path", "nemo_model_path",
)

# FFmpeg 常见安装路径
COMMON_FFMPEG_PATHS = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    r"D:\ffmpeg\bin\ffmpeg.exe",
    r"D:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\tools\ffmpeg\bin\ffmpeg.exe",
    r"C:\msys64\mingw64\bin\ffmpeg.exe",
    r"C:\scoop\apps\ffmpeg\current\bin\ffmpeg.exe",
    r"C:\chocolatey\lib\ffmpeg\tools\ffmpeg.exe",
]

# 任务状态常量
STATUS_PENDING = "等待中"
STATUS_EXTRACTING = "音频提取中"
STATUS_LOADING_MODEL = "加载模型中"
STATUS_RECOGNIZING = "语音识别中"
STATUS_TRANSLATING = "文字翻译中"
STATUS_GENERATING = "生成字幕中"
STATUS_COMPLETED = "已完成"
STATUS_ERROR = "出错"
STATUS_CANCELLED = "已取消"

STATUS_COLORS = {
    STATUS_PENDING: "#999999",
    STATUS_EXTRACTING: "#3498db",
    STATUS_LOADING_MODEL: "#9b59b6",
    STATUS_RECOGNIZING: "#e67e22",
    STATUS_TRANSLATING: "#2ecc71",
    STATUS_GENERATING: "#1abc9c",
    STATUS_COMPLETED: "#27ae60",
    STATUS_ERROR: "#e74c3c",
    STATUS_CANCELLED: "#95a5a6",
}

# GPU 相关错误关键字：命中后自动回退 CPU 重试
GPU_ERROR_KEYWORDS = (
    "cuda", "cublas", "cudnn", "cufft", "curand", "nvrtc",
    "out of memory", "显存", "no kernel image", "device-side assert",
    "gpu is not supported", "unsupported gpu architecture",
)


# ============================================================
# 路径工具函数
# ============================================================

def normalize_path(path):
    if not path:
        return ""
    path = os.path.normpath(path)
    path = path.replace("\\", "/")
    path = path.rstrip("/")
    return path


def to_windows_path(path):
    if not path:
        return ""
    path = os.path.normpath(path)
    return path


# ============================================================
# 配置管理
# ============================================================

def _normalize_config(config):
    for key in _PATH_KEYS:
        if key in config:
            config[key] = normalize_path(config[key])
    if "model_paths" in config and isinstance(config["model_paths"], dict):
        config["model_paths"] = {k: normalize_path(v)
                                 for k, v in config["model_paths"].items()}
    return config


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return _normalize_config(json.load(f))
        except Exception:
            pass
    return {}


def save_config(config):
    _normalize_config(config)
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print("保存配置失败:", e)
        return False
