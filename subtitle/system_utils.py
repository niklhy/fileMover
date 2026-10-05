#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统工具：FFmpeg、CUDA、模型路径
"""
import os
import sys
import subprocess
import glob as _glob

from .config import (
    COMMON_FFMPEG_PATHS, REQUIRED_PACKAGES,
    default_model_path, get_model_root,
    load_config, save_config, normalize_path, to_windows_path,
)

# ============================================================
# 配置管理（统一由 config 模块提供，此处转发以便外部调用）
# ============================================================

def _resolve_default(key, config=None, is_file=False):
    """按模型根目录推导默认路径（模型文件不随程序拷贝，只按路径引用）。"""
    path = default_model_path(key, config)
    if not path:
        return None
    win_path = to_windows_path(path)
    if is_file:
        return normalize_path(path) if os.path.exists(win_path) else None
    return normalize_path(path) if os.path.isdir(win_path) else None


# ============================================================
# FFmpeg 管理
# ============================================================

def find_ffmpeg_in_path():
    for cmd in ["ffmpeg", "ffmpeg.exe"]:
        try:
            result = subprocess.run([cmd, "-version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                try:
                    which_result = subprocess.run(["where", cmd], capture_output=True, text=True, timeout=5)
                    if which_result.returncode == 0:
                        path = which_result.stdout.strip().split("\n")[0].strip()
                        return normalize_path(path)
                except:
                    pass
                return cmd
        except:
            pass
    return None


def find_ffmpeg_common_paths():
    for path in COMMON_FFMPEG_PATHS:
        if os.path.exists(path):
            try:
                result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return normalize_path(path)
            except:
                pass
    return None


def find_ffmpeg(config=None):
    if config is None:
        config = load_config()
    custom_path = config.get("ffmpeg_path", "")
    if custom_path:
        win_path = to_windows_path(custom_path)
        if os.path.exists(win_path):
            try:
                result = subprocess.run([win_path, "-version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return True, custom_path, result.stdout.split("\n")[0]
            except:
                pass
    path_result = find_ffmpeg_in_path()
    if path_result:
        try:
            result = subprocess.run([to_windows_path(path_result), "-version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return True, path_result, result.stdout.split("\n")[0]
        except:
            pass
    common_result = find_ffmpeg_common_paths()
    if common_result:
        try:
            result = subprocess.run([to_windows_path(common_result), "-version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return True, common_result, result.stdout.split("\n")[0]
        except:
            pass
    return False, None, None


def get_ffmpeg_cmd():
    found, path, _ = find_ffmpeg()
    if found and path:
        return to_windows_path(path)
    return "ffmpeg"


# ============================================================
# CUDA 检测
# ============================================================

def check_nvidia_driver():
    """检测显卡驱动：优先 nvidia-smi，失败时回退检查系统目录中的驱动文件。"""
    smi_candidates = ["nvidia-smi"]
    if sys.platform == "win32":
        smi_candidates.append(r"C:\Windows\System32\nvidia-smi.exe")
    for cmd in smi_candidates:
        try:
            result = subprocess.run(
                [cmd, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                return True, result.stdout.strip().split("\n")[0].strip()
        except Exception:
            pass
    # 回退：驱动核心文件存在即视为驱动已安装
    if sys.platform == "win32":
        for dll in (r"C:\Windows\System32\nvcuda.dll", r"C:\Windows\SysWOW64\nvcuda.dll"):
            if os.path.exists(dll):
                return True, "NVIDIA 驱动已安装（检测到 nvcuda.dll）"
    return False, ""


def _cuda_dll_dirs():
    """收集所有可能包含 CUDA 运行库 DLL 的目录。"""
    import glob as _glob
    dirs = []

    def add(p):
        if p and os.path.isdir(p) and p not in dirs:
            dirs.append(p)

    # 1. 环境变量指向的 CUDA Toolkit
    for env_name in ("CUDA_PATH", "CUDA_HOME", "CUDA_ROOT"):
        base = os.environ.get(env_name)
        if base:
            add(os.path.join(base, "bin"))
    # 2. 默认安装位置的 CUDA Toolkit（扫描所有版本）
    for base in _glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*"):
        add(os.path.join(base, "bin"))
    # 3. PyTorch 自带的 DLL 目录
    try:
        import torch
        add(os.path.join(os.path.dirname(torch.__file__), "lib"))
    except Exception:
        pass
    # 4. pip 安装的 nvidia-* 包自带的 DLL 目录
    try:
        import nvidia  # type: ignore
        nvidia_dir = os.path.dirname(nvidia.__file__)
        for sub in ("cublas", "cudnn", "cuda_runtime", "cufft", "curand", "cusolver", "cusparse", "nvjitlink"):
            add(os.path.join(nvidia_dir, sub, "bin"))
    except Exception:
        pass
    return dirs


def ensure_cuda_dll_path():
    """把 CUDA DLL 目录注册进 DLL 搜索路径，使 ctranslate2/torch 能找到它们。"""
    if sys.platform != "win32":
        return
    for d in _cuda_dll_dirs():
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
        path_env = os.environ.get("PATH", "")
        if d.lower() not in path_env.lower():
            os.environ["PATH"] = d + os.pathsep + path_env


def _load_dll_from_dirs(names):
    """依次尝试从系统路径和已知 CUDA 目录加载 DLL，返回成功信息或 None。"""
    import ctypes
    dirs = _cuda_dll_dirs()
    for name in names:
        try:
            ctypes.CDLL(name)
            return name + " 已找到（系统路径）"
        except OSError:
            pass
        for d in dirs:
            full = os.path.join(d, name)
            if os.path.exists(full):
                try:
                    ctypes.CDLL(full)
                    return name + " 已找到: " + full
                except OSError:
                    pass
    return None


def check_cuda_available():
    driver_ok, driver_info = check_nvidia_driver()
    try:
        import torch
    except ImportError:
        if driver_ok:
            return False, "PyTorch 未安装 | 显卡驱动正常: " + driver_info
        return False, "PyTorch 未安装"
    except Exception as e:
        return False, "PyTorch 导入失败: " + str(e)
    try:
        cuda_build = getattr(torch.version, "cuda", None)
        if not cuda_build:
            msg = "当前安装的是 CPU 版 PyTorch，不支持 CUDA"
            if driver_ok:
                msg += (" | 驱动正常（" + driver_info + "），请安装 CUDA 版 PyTorch，例如: "
                        "pip install torch --index-url https://download.pytorch.org/whl/cu121")
            return False, msg
        ensure_cuda_dll_path()
        if torch.cuda.is_available():
            return True, "CUDA 可用 | 设备: " + torch.cuda.get_device_name(0) + " | CUDA " + str(cuda_build)
        if driver_ok:
            return False, ("驱动正常（" + driver_info + "），但 PyTorch 无法初始化 CUDA，"
                           "可能是 CUDA 运行库缺失或版本不匹配")
        return False, "PyTorch 支持 CUDA，但未检测到 NVIDIA 显卡驱动"
    except Exception as e:
        return False, "检测失败: " + str(e)


def check_cublas():
    result = _load_dll_from_dirs(["cublas64_12.dll", "cublas64_11.dll", "cublas64_10.dll"])
    if result:
        return True, result
    return False, "cublas 库未找到（GPU 模式需要 CUDA Toolkit 或 nvidia-cublas 包）"


def check_cudnn():
    result = _load_dll_from_dirs([
        "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn64_8.dll", "cudnn_ops_infer64_8.dll"
    ])
    if result:
        return True, result
    return False, "cudnn 库未找到（faster-whisper GPU 模式需要 cuDNN）"


def release_gpu_memory():
    """
    释放 GPU 显存：垃圾回收 + 清空 torch CUDA 缓存。
    同时会触发 ctranslate2（faster-whisper）模型对象的析构。
    """
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            try:
                torch.cuda.ipc_collect()
            except Exception:
                pass
    except Exception:
        pass


# ============================================================
# 模型路径管理
# ============================================================

def resolve_model_path(model_name, config=None):
    if config is None:
        config = load_config()
    model_paths = config.get("model_paths", {})
    if model_name in model_paths and model_paths[model_name]:
        path = to_windows_path(model_paths[model_name])
        if os.path.exists(path):
            return path
    base_path = config.get("model_base_path", "") or default_model_path("whisper", config)
    if base_path:
        possible_names = [
            "faster-whisper-" + model_name,
            model_name,
            "whisper-" + model_name,
        ]
        for name in possible_names:
            full_path = to_windows_path(base_path + "/" + name)
            if os.path.exists(full_path):
                return full_path
    return find_hf_snapshot_path(model_name)


def get_model_cache_dir():
    return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def find_hf_snapshot_path(model_name):
    repo_id = "Systran/faster-whisper-" + model_name
    safe_name = repo_id.replace("/", "--")
    cache_path = os.path.join(get_model_cache_dir(), f"models--{safe_name}")
    if not os.path.exists(cache_path):
        return None
    snapshots_dir = os.path.join(cache_path, "snapshots")
    if not os.path.exists(snapshots_dir):
        return None
    snapshot_dirs = []
    for item in os.listdir(snapshots_dir):
        item_path = os.path.join(snapshots_dir, item)
        if os.path.isdir(item_path):
            snapshot_dirs.append(item_path)
    if not snapshot_dirs:
        return None
    for snap_path in snapshot_dirs:
        has_config = os.path.exists(os.path.join(snap_path, "config.json"))
        has_model = any(
            os.path.exists(os.path.join(snap_path, f))
            for f in ["model.bin", "pytorch_model.bin", "model.safetensors"]
        )
        if has_config or has_model:
            return snap_path
    return snapshot_dirs[0] if snapshot_dirs else None


def check_model_path_valid(model_path):
    if not model_path or not os.path.exists(model_path):
        return False, "路径不存在"
    if not os.path.isdir(model_path):
        return False, "路径不是文件夹"
    has_config = os.path.exists(os.path.join(model_path, "config.json"))
    has_model = any(
        os.path.exists(os.path.join(model_path, f))
        for f in ["model.bin", "pytorch_model.bin", "model.safetensors"]
    )
    has_tokenizer = any(
        os.path.exists(os.path.join(model_path, f))
        for f in ["tokenizer.json", "vocabulary.txt"]
    )
    if not has_config:
        return False, "缺少 config.json"
    if not has_model and not has_tokenizer:
        return False, "缺少模型权重文件或分词器文件"
    return True, "模型文件完整"


# ============================================================
# 翻译模型（NLLB-200）路径管理
# ============================================================

def resolve_translator_path(config=None):
    """解析翻译模型路径：优先配置文件，否则使用工作目录下的默认模型。"""
    if config is None:
        config = load_config()
    custom = config.get("translator_model_path", "")
    if custom:
        path = to_windows_path(custom)
        if os.path.exists(path):
            return path
    return _resolve_default("translator", config)


def check_translator_path_valid(model_path):
    """检查 NLLB-200 翻译模型目录是否完整。"""
    if not model_path or not os.path.exists(model_path):
        return False, "路径不存在"
    if not os.path.isdir(model_path):
        return False, "路径不是文件夹"
    has_config = os.path.exists(os.path.join(model_path, "config.json"))
    has_model = any(
        os.path.exists(os.path.join(model_path, f))
        for f in ["pytorch_model.bin", "model.safetensors"]
    )
    has_tokenizer = any(
        os.path.exists(os.path.join(model_path, f))
        for f in ["tokenizer.json", "sentencepiece.bpe.model"]
    )
    if not has_config:
        return False, "缺少 config.json"
    if not has_model:
        return False, "缺少模型权重文件 (pytorch_model.bin / model.safetensors)"
    if not has_tokenizer:
        return False, "缺少分词器文件 (tokenizer.json / sentencepiece.bpe.model)"
    return True, "翻译模型文件完整"


def resolve_sakura_path(config=None, engine="Sakura-14B"):
    """解析 Sakura GGUF 模型文件路径（按引擎区分 7B / 14B / GalTransl）。"""
    if config is None:
        config = load_config()
    if engine == "Sakura-7B":
        custom = config.get("sakura7b_model_path", "")
        if custom and os.path.exists(to_windows_path(custom)):
            return normalize_path(custom)
        return _resolve_default("sakura7b", config, is_file=True)
    if engine == "Sakura-GalTransl-7B":
        custom = config.get("galtransl_model_path", "")
        if custom and os.path.exists(to_windows_path(custom)):
            return normalize_path(custom)
        return _resolve_default("galtransl", config, is_file=True)
    custom = config.get("sakura_model_path", "")
    if custom and os.path.exists(to_windows_path(custom)):
        return normalize_path(custom)
    return _resolve_default("sakura", config, is_file=True)


def check_sakura_path_valid(path):
    if not path or not os.path.exists(path):
        return False, "文件不存在"
    if not path.lower().endswith(".gguf"):
        return False, "不是 .gguf 模型文件"
    return True, "Sakura GGUF 模型文件存在"


def resolve_kotoba_path(config=None):
    """解析 kotoba-whisper 模型目录。"""
    if config is None:
        config = load_config()
    custom = config.get("kotoba_model_path", "")
    if custom:
        path = to_windows_path(custom)
        if os.path.exists(path):
            return normalize_path(path)
    return _resolve_default("kotoba", config)


def check_kotoba_path_valid(path):
    if not path or not os.path.exists(path):
        return False, "路径不存在"
    if not os.path.isdir(path):
        return False, "路径不是文件夹"
    if not os.path.exists(os.path.join(path, "config.json")):
        return False, "缺少 config.json"
    if not os.path.exists(os.path.join(path, "model.safetensors")):
        return False, "缺少 model.safetensors"
    return True, "kotoba-whisper 模型文件完整"


def resolve_nemo_path(config=None):
    """解析 ReazonSpeech NeMo 模型文件路径。"""
    if config is None:
        config = load_config()
    custom = config.get("nemo_model_path", "")
    if custom:
        path = to_windows_path(custom)
        if os.path.exists(path):
            return normalize_path(path)
    return _resolve_default("nemo", config, is_file=True)


def check_nemo_path_valid(path):
    if not path or not os.path.exists(path):
        return False, "文件不存在"
    if not path.lower().endswith(".nemo"):
        return False, "不是 .nemo 模型文件"
    return True, "NeMo 模型文件存在"


def check_package(package_name):
    try:
        info = REQUIRED_PACKAGES.get(package_name, {})
        import_name = info.get("import_name") or package_name.replace("-", "_")
        __import__(import_name)
        return True
    except ImportError:
        return False



def get_pip_command():
    for cmd in ["pip", "pip3", "python -m pip", "py -m pip"]:
        try:
            result = subprocess.run(cmd.split() + ["--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return cmd
        except:
            continue
    return None
