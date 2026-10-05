#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心处理模块
"""
import os
import sys
import time
import threading
import subprocess
import re
from datetime import timedelta

from .config import (
    STATUS_PENDING, STATUS_EXTRACTING, STATUS_LOADING_MODEL,
    STATUS_RECOGNIZING, STATUS_TRANSLATING, STATUS_GENERATING,
    STATUS_COMPLETED, STATUS_ERROR, STATUS_CANCELLED,
    SAKURA_MODES, load_config, normalize_path, to_windows_path,
    enable_offline_env,
)
from .system_utils import (
    ensure_cuda_dll_path, release_gpu_memory,
    resolve_model_path, resolve_translator_path, resolve_sakura_path,
    resolve_kotoba_path, resolve_nemo_path,
    check_model_path_valid, check_translator_path_valid,
    check_sakura_path_valid, check_kotoba_path_valid, check_nemo_path_valid,
    get_ffmpeg_cmd,
)


def get_pip_command():
    for cmd in ["pip", "pip3", "python -m pip", "py -m pip"]:
        try:
            result = subprocess.run(cmd.split() + ["--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return cmd
        except:
            continue
    return None


def _safe_load_transformer(ModelClass, path, use_cuda, dtype=None):
    """加载 transformers 模型并放到目标设备。

    规避「Cannot copy out of meta tensor; no data!」错误：
    该错误来自 from_pretrained(..., torch_dtype=...) 在启用 low_cpu_mem_usage 时
    先以 meta 设备创建空权重，随后显式 .to(device) 拷贝——当部分子模块仍停留在
    meta 设备就会抛错。解决顺序：
      1) 优先用 device_map 让 transformers/accelerate 自行完成 meta->device 的权重落地；
      2) 否则关闭 low_cpu_mem_usage，在 CPU 实体化后再整体搬到目标设备；
      3) 再不行就普通加载。
    返回 (model, actually_on_cuda)。
    """
    import torch
    if dtype is None:
        dtype = torch.float16

    if use_cuda:
        # 方案1：device_map 直接上 CUDA（最稳妥，避免显式 .to() 触发 meta 拷贝错误）
        try:
            model = ModelClass.from_pretrained(path, torch_dtype=dtype, device_map="cuda")
            return model, bool(next(model.parameters()).is_cuda)
        except Exception:
            pass
        # 方案2：关闭低内存加载，权重直接实体化到 CPU 后再搬到 CUDA
        try:
            model = ModelClass.from_pretrained(path, low_cpu_mem_usage=False)
            return model.to("cuda", dtype=dtype), True
        except TypeError:
            model = ModelClass.from_pretrained(path)
            return model.to("cuda", dtype=dtype), True
        except Exception:
            pass

    # CPU 路径
    try:
        model = ModelClass.from_pretrained(path, low_cpu_mem_usage=False)
    except Exception:
        model = ModelClass.from_pretrained(path)
    return model, False


# ============================================================
# 任务数据类
# ============================================================

class TaskItem:
    def __init__(self, video_path, output_path=None):
        self.video_path = video_path
        self.video_name = os.path.basename(video_path)
        if output_path is None:
            self.output_path = os.path.splitext(video_path)[0] + ".srt"
        else:
            self.output_path = output_path
        self.status = STATUS_PENDING
        self.progress = 0
        self.status_detail = ""
        self.error_msg = ""
        self.start_time = None
        self.end_time = None
        self.elapsed = ""
        self.cancelled = False

    def set_status(self, status, detail=""):
        self.status = status
        self.status_detail = detail
        if status == STATUS_EXTRACTING and self.start_time is None:
            self.start_time = time.time()
        if status in [STATUS_COMPLETED, STATUS_ERROR, STATUS_CANCELLED]:
            self.end_time = time.time()
            if self.start_time:
                elapsed_sec = self.end_time - self.start_time
                self.elapsed = f"{int(elapsed_sec//60)}分{int(elapsed_sec%60)}秒"

    def update_progress(self, progress, detail=""):
        self.progress = progress
        if detail:
            self.status_detail = detail

    def cancel(self):
        self.cancelled = True


# ============================================================
# 核心处理模块 - 修复翻译器初始化
# ============================================================

class SubtitleGenerator:
    # NLLB-200 语言代码
    NLLB_SRC_LANG = "jpn_Jpan"  # 日语
    NLLB_TGT_LANG = "zho_Hans"  # 简体中文

    def __init__(self, model_path, device="cpu", compute_type="int8", translator_path=None,
                 asr_engine="faster-whisper", translator_engine="NLLB-200",
                 kotoba_path=None, nemo_path=None, sakura_path=None):
        # 全部模型均从本地加载，禁用 HuggingFace 联网（仅在真正开始处理时设置）
        enable_offline_env()
        self.model_path = model_path          # faster-whisper 模型目录
        self.device = device
        self.compute_type = compute_type
        self.asr_engine = asr_engine
        self.translator_engine = translator_engine
        self.translator_path = translator_path  # NLLB-200 模型目录
        self.kotoba_path = kotoba_path
        self.nemo_path = nemo_path
        self.sakura_path = sakura_path
        self.model = None               # 语音识别模型（三种引擎之一）
        self.kotoba_processor = None    # kotoba-whisper 的 processor
        self.translator = None          # NLLB 模型或 Sakura Llama 对象
        self.translator_tokenizer = None
        self._tgt_bos_token_id = None
        self._cancelled = False
        self._status_callback = None  # 状态回调（process_video 中赋值）
        self._translator_initialized = False  # 标记翻译器是否成功初始化
        self._translator_error = ""  # 记录翻译器初始化错误
        self._audio_proc = None  # 音频提取子进程（用于取消）

    # ========================================================
    # 语音识别模型加载（三种引擎）
    # ========================================================

    def load_model(self):
        if self.asr_engine == "kotoba-whisper":
            self._load_kotoba_model()
        elif self.asr_engine == "reazonspeech-nemo":
            self._load_nemo_model()
        else:
            self._load_faster_whisper_model()

    def _load_faster_whisper_model(self):
        from faster_whisper import WhisperModel
        if not self.model_path or not os.path.exists(self.model_path):
            raise FileNotFoundError("模型路径不存在: " + str(self.model_path))
        if self.device == "cuda":
            # 确保 ctranslate2 能找到 cuBLAS/cuDNN 运行库
            ensure_cuda_dll_path()
        self.model = WhisperModel(
            self.model_path,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=4,
            num_workers=1,
        )

    def _load_kotoba_model(self):
        """加载 kotoba-whisper-v2.1（transformers Whisper 架构）。"""
        try:
            from transformers import AutoProcessor, WhisperForConditionalGeneration
            import torch
        except ImportError as e:
            raise RuntimeError("缺少依赖库: " + str(e) + "（需要 transformers 和 torch）")

        path = self.kotoba_path or resolve_kotoba_path()
        if not path:
            raise FileNotFoundError("未找到 kotoba-whisper 模型，请在【设置】中指定模型目录")
        path = to_windows_path(path)
        valid, msg = check_kotoba_path_valid(path)
        if not valid:
            raise RuntimeError("kotoba-whisper 模型无效: " + msg)

        self.kotoba_processor = AutoProcessor.from_pretrained(path)
        use_cuda = self.device == "cuda" and torch.cuda.is_available()
        self.model, _ = _safe_load_transformer(
            WhisperForConditionalGeneration, path, use_cuda, torch.float16)
        self.model.eval()

    def _load_nemo_model(self):
        """加载 ReazonSpeech-NeMo-v2（.nemo 文件）。"""
        try:
            import nemo.collections.asr as nemo_asr
        except ImportError as e:
            raise RuntimeError("缺少 NeMo 框架: " + str(e) + "（请运行 一键安装GPU环境.bat 或 pip install nemo_toolkit[asr]）")

        path = self.nemo_path or resolve_nemo_path()
        if not path:
            raise FileNotFoundError("未找到 ReazonSpeech NeMo 模型，请在【设置】中指定 .nemo 文件")
        path = to_windows_path(path)
        valid, msg = check_nemo_path_valid(path)
        if not valid:
            raise RuntimeError("NeMo 模型无效: " + msg)

        if self.device == "cuda":
            ensure_cuda_dll_path()
        self.model = nemo_asr.models.ASRModel.restore_from(path)
        try:
            import torch
            if self.device == "cuda" and torch.cuda.is_available():
                self.model = self.model.cuda()
            else:
                self.model = self.model.cpu()
        except Exception:
            pass
        self.model.eval()

    # ========================================================
    # 语音识别执行（三种引擎统一返回 [(start, end, text), ...]）
    # ========================================================

    def run_asr(self, audio_path, progress_callback=None):
        if self.asr_engine == "kotoba-whisper":
            result = self._asr_kotoba(audio_path)
        elif self.asr_engine == "reazonspeech-nemo":
            result = self._asr_nemo(audio_path)
        else:
            result = self._asr_faster_whisper(audio_path, progress_callback)
        if result == "CANCELLED":
            return result
        return self._postprocess_segments(result)

    # ========================================================
    # 字幕段后处理（所有引擎统一生效）
    # ========================================================

    @staticmethod
    def _collapse_repeated_text(text):
        """折叠正文中连续重复的短语（任意位置、任意单元长度）：
        「はいはいはい…」→「はい」
        「这样做,你可能会乘坐火车,或者是乘坐火车,或者是乘坐火车,…」→ 前缀保留，重复组折叠
        「谢谢谢谢谢谢…」→「谢谢」（短单元保留两次以保留语气）
        识别/翻译模型在背景音乐或噪声段常产生同一短语的幻觉重复。"""
        if not text:
            return text
        MAX_UNIT = 24   # 重复单元最大长度（字符）
        MIN_REPS = 3    # 至少连续重复次数才折叠
        n = len(text)
        out = []
        i = 0
        while i < n:
            collapsed = False
            max_ulen = min(MAX_UNIT, (n - i) // MIN_REPS)
            for ulen in range(1, max_ulen + 1):
                unit = text[i:i + ulen]
                reps = 1
                while text[i + reps * ulen: i + (reps + 1) * ulen] == unit:
                    reps += 1
                if reps >= MIN_REPS:
                    # 超短单元（1~2 字）保留两次，避免「谢谢」变「谢」、「哈哈」变「哈」
                    keep = 2 if ulen <= 2 else 1
                    out.append(unit * keep)
                    i += reps * ulen
                    collapsed = True
                    break
            if not collapsed:
                out.append(text[i])
                i += 1
        result = "".join(out)
        # 折叠后可能留下连续分隔符（如「,、「），压缩为一个
        import re
        result = re.sub(r"([,，、…])\1+", r"\1", result)
        # 句中分隔符后紧跟句末标点时去掉分隔符（「,。」→「。」）
        result = re.sub(r"[,，、…]+([。！？!?])", r"\1", result)
        # 去掉结尾孤立的分隔符（「坐下来,」→「坐下来」），句末标点保留
        result = re.sub(r"[,，、…]+$", "", result)
        return result

    # ========================================================
    # 语气词清理 / 时间轴规范化
    # ========================================================

    # 日文句首口头填充词（只清理句首，不触碰句中有实义的用词）
    JA_FILLER_WORDS = ("あのー", "あのぅ", "あの", "えっとー", "えっと", "えーっと", "えーと",
                       "ええと", "えー", "ええー", "うーん", "うー", "んー", "んーん",
                       "あー", "ああー", "えっ")
    # 中文译文句首语气词字符
    ZH_FILLER_CHARS = "嗯呃啊哦噢唉哎唔哼嚯"

    @classmethod
    def _clean_leading_filler(cls, text, lang="ja"):
        """去掉文本开头的口头填充词，如「あの、」「えっと…」「嗯，」「啊——」。
        只处理句首（连同其后的停顿标点/破折号），不影响句子中间的用词。"""
        text = (text or "").strip()
        changed = True
        while changed and text:
            changed = False
            if lang == "ja":
                for w in cls.JA_FILLER_WORDS:
                    if text.startswith(w):
                        text = text[len(w):]
                        text = re.sub(r"^[、，,。…・\s]+", "", text)
                        changed = True
                        break
            else:
                while text and text[0] in cls.ZH_FILLER_CHARS:
                    text = text[1:]
                    changed = True
                if changed:
                    text = re.sub(r"^[、，,。…\s—–\-~～]+", "", text)
        return text.strip()

    @classmethod
    def _is_filler_only(cls, text):
        """判断文本是否只剩语气词和标点（没有实际内容的字幕）。"""
        if not text:
            return True
        stripped = re.sub(r"[" + re.escape(cls.ZH_FILLER_CHARS) + r"、，,。！？!?…—–\-~～\s]",
                          "", text)
        return stripped == ""

    @staticmethod
    def _normalize_timeline(segments, max_dur=12.0, min_gap=0.06, min_dur=0.5):
        """规范化字幕时间轴（所有引擎通用）：
        1) 按开始时间排序，修正非法区间（开始不早于 0，结束晚于开始）；
        2) 每条字幕的结束时间不越过下一条的开始时间（留 min_gap 空隙），
           避免上一句字幕一直残留到下一句出现才消失；
        3) 单条显示时长封顶 max_dur 秒，避免说话结束后字幕长时间挂着；
        4) 最短显示 min_dur 秒，保证可读性。"""
        segs = sorted((float(s), float(e), t) for (s, e, t) in segments)
        out = []
        for (s, e, t) in segs:
            s = max(0.0, s)
            e = max(e, s + min_dur)
            out.append([s, e, t])
        n = len(out)
        for i in range(n):
            limit = out[i + 1][0] - min_gap if i + 1 < n else float("inf")
            end = min(out[i][1], out[i][0] + max_dur, limit)
            out[i][1] = max(end, out[i][0] + min_dur)
        return [(s, e, t) for (s, e, t) in out]

    def _postprocess_segments(self, segments):
        """字幕段后处理：
        1) 折叠段内重复短语；2) 清理句首语气词，丢弃纯语气词（嗯/啊等）字幕段；
        3) 相邻相同文本段合并（延长显示时间）；
        4) 超过 20 秒的超长段按标点拆分（时间按字数比例分配）。"""
        MAX_DUR = 20.0
        PUNCT = "。、！？!?…"

        cleaned = []
        for (s, e, text) in segments:
            text = self._collapse_repeated_text((text or "").strip())
            text = self._clean_leading_filler(text, "ja")
            if not text or self._is_filler_only(text):
                continue
            cleaned.append((float(s), float(e), text))

        # 超长段按标点拆分
        split_out = []
        for (s, e, text) in cleaned:
            dur = e - s
            if dur <= MAX_DUR or len(text) < 8:
                split_out.append((s, e, text))
                continue
            # 按标点切成若干部分
            parts = []
            buf = ""
            for ch in text:
                buf += ch
                if ch in PUNCT:
                    parts.append(buf)
                    buf = ""
            if buf:
                parts.append(buf)
            if len(parts) <= 1:
                split_out.append((s, e, text))
                continue
            total_chars = sum(len(p) for p in parts)
            cur = s
            for p in parts:
                pdur = dur * len(p) / total_chars
                split_out.append((cur, cur + pdur, p))
                cur += pdur

        # 相邻相同文本合并，延长显示时间
        merged = []
        for (s, e, text) in split_out:
            if merged and merged[-1][2] == text and s - merged[-1][1] < 3.0:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e), text)
            else:
                merged.append((s, e, text))
        return merged

    def _asr_faster_whisper(self, audio_path, progress_callback=None):
        segments, info = self.model.transcribe(
            audio_path,
            language="ja",
            task="transcribe",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        result = []
        for seg in segments:
            if self._cancelled:
                return "CANCELLED"
            result.append((float(seg.start), float(seg.end), seg.text.strip()))
            if len(result) % 10 == 0 and progress_callback:
                progress_callback(35 + int((len(result) / 100) * 30), STATUS_RECOGNIZING)
        return result

    _WHISPER_TS_TOKEN = re.compile(r"<\|(\d+(?:\.\d+)?)\|>")

    def _split_whisper_timestamp_tokens(self, ids, offset_sec, chunk_end_sec):
        """把 whisper 模型输出的 token 序列按 <|秒数|> 时间戳切成
        [(start, end, text), ...]（已加 offset_sec）。
        模型未输出时间戳 token 时返回 None，由调用方退化为整段处理。"""
        tokenizer = getattr(self.kotoba_processor, "tokenizer", None)
        if tokenizer is None:
            return None
        try:
            tokens = tokenizer.convert_ids_to_tokens(ids)
        except Exception:
            return None
        segments = []
        buf = []
        prev_ts = None
        found_ts = False

        def flush(end_ts):
            if not buf:
                return
            text = tokenizer.decode(buf, skip_special_tokens=True).strip()
            if text and prev_ts is not None:
                segments.append((prev_ts + offset_sec, end_ts + offset_sec, text))
            buf.clear()

        for tid, tok in zip(ids, tokens):
            m = self._WHISPER_TS_TOKEN.fullmatch(str(tok).strip())
            if m:
                ts = float(m.group(1))
                found_ts = True
                if prev_ts is not None:
                    flush(ts)
                prev_ts = ts
            else:
                buf.append(tid)
        # 末尾文本挂到最后一个时间点与分块末尾之间
        if buf and prev_ts is not None:
            flush(max(chunk_end_sec, prev_ts))
        if not found_ts:
            return None
        return segments

    def _asr_kotoba(self, audio_path):
        """kotoba-whisper：按 30 秒分块识别。
        v2 模型自带词级时间戳（<|秒数|> 特殊 token），解析出真实说话时间，
        避免整段字幕从分块起点（视频开头）开始显示、并残留到分块结束。"""
        import torch
        import wave
        import numpy as np

        # 读取 16kHz 单声道 PCM16 WAV（由 extract_audio 生成）
        with wave.open(audio_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        processor = self.kotoba_processor
        chunk_samples = 30 * 16000
        result = []
        use_cuda = next(self.model.parameters()).is_cuda

        for start_sample in range(0, len(audio), chunk_samples):
            if self._cancelled:
                return "CANCELLED"
            chunk = audio[start_sample:start_sample + chunk_samples]
            offset_sec = start_sample / 16000.0
            chunk_sec = len(chunk) / 16000.0
            inputs = processor(chunk, sampling_rate=16000, return_tensors="pt")
            input_features = inputs.input_features
            if use_cuda:
                input_features = input_features.to("cuda", dtype=torch.float16)
            with torch.no_grad():
                predicted_ids = self.model.generate(
                    input_features,
                    language="ja",
                    task="transcribe",
                    return_timestamps=True,
                )
            pieces = self._split_whisper_timestamp_tokens(
                predicted_ids[0].tolist(), offset_sec, chunk_sec)
            if pieces is not None:
                result.extend(pieces)
            else:
                # 模型未输出时间戳：退化为整段（旧行为）
                text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()
                if text:
                    result.append((offset_sec, offset_sec + chunk_sec, text))
        return result

    def _nemo_transcribe_chunk(self, chunk_path):
        """识别单个音频分片，返回 [{'s_off','e_off','s','e','text'}, ...]（原始未校准值）。"""
        # num_workers=0：避免多进程 DataLoader 工作进程持有临时 manifest.json 文件句柄，
        # 导致 transcribe 结束时清理临时目录报 WinError 267 目录名称无效
        try:
            output = self.model.transcribe([chunk_path], timestamps=True, batch_size=1, num_workers=0)
        except TypeError:
            output = self.model.transcribe([chunk_path], batch_size=1, num_workers=0)

        result = []
        hyp = output[0]
        # NeMo Hypothesis 对象或字符串
        ts_dict = hyp.timestamp if hasattr(hyp, "timestamp") and isinstance(hyp.timestamp, dict) else {}
        segment_ts = ts_dict.get("segment") if ts_dict else None

        if segment_ts:
            for seg in segment_ts:
                text = (seg.get("segment") or "").strip()
                if text:
                    result.append({
                        "s_off": seg.get("start_offset"),
                        "e_off": seg.get("end_offset"),
                        "s": float(seg.get("start", 0.0)),
                        "e": float(seg.get("end", 0.0)),
                        "text": text,
                    })
            # 同时收集字符级时间戳，用于按语气停顿/标点重新断句
            chars = []
            for c in (ts_dict.get("char") or []):
                ch_val = c.get("char")
                if isinstance(ch_val, list):
                    ch_text = "".join(ch_val)
                else:
                    ch_text = str(ch_val or "")
                if ch_text.strip():
                    chars.append((c.get("start_offset"), c.get("end_offset"), ch_text))
            if result:
                result[0]["chars"] = chars
                # 字符可能分布在多个 segment 条目上，统一挂到第一个条目
                for extra in result[1:]:
                    extra.pop("chars", None)
        else:
            # 无时间戳时退化为整条一个字幕段
            text = hyp.text if hasattr(hyp, "text") else str(hyp)
            text = text.strip()
            if text:
                result.append({"s_off": None, "e_off": None, "s": 0.0, "e": None, "text": text})
        return result

    def _nemo_frame_stride(self):
        """NeMo 计算时间戳使用的单帧时长：window_stride × subsampling_factor。"""
        try:
            ws = float(self.model.cfg.preprocessor.window_stride)
            sf_factor = float(self.model.cfg.encoder.get("subsampling_factor", 1))
            return ws * sf_factor
        except Exception:
            return 0.08

    def _nemo_finish_segments(self, raw_items):
        """把原始 offset 时间转换为真实秒数，并用字符级时间戳重新断句。

        reazonspeech-nemo-v2 在 NeMo 下返回的 start/end 存在整数倍偏差
        （实测该模型 end_offset 可达理论帧数的 2 倍，即时间被放大 2 倍）。
        校准方法：找出所有 offset × 帧长 超出分片时长的最大倍数，
        四舍五入为整数作为偏差系数，统一除回去；无偏移时系数为 1。

        断句规则（解决单段字幕过长问题）：语气停顿 > 1 秒、句末标点
        （。！？）、单句最长 10 秒 / 最多 40 字，任一条件满足即换行。
        """
        stride = self._nemo_frame_stride()
        max_ratio = 1.0
        for item in raw_items:
            dur = item["chunk_dur"]
            if dur <= 0:
                continue
            if item["e_off"]:
                max_ratio = max(max_ratio, (item["e_off"] * stride) / dur)
            for (co_s, co_e, _ch) in item.get("chars", []):
                if co_e:
                    max_ratio = max(max_ratio, (co_e * stride) / dur)
        factor = int(round(max_ratio)) if max_ratio > 1.2 else 1

        # 收集全局字符时间轴（已校准 + 分片偏移）
        chars = []
        for item in raw_items:
            for (co_s, co_e, ch) in item.get("chars", []):
                if co_s is None or co_e is None:
                    continue
                s = co_s * stride / factor + item["chunk_start"]
                e = co_e * stride / factor + item["chunk_start"]
                chars.append((s, e, ch))
        chars.sort(key=lambda x: x[0])

        if chars:
            return self._group_chars_to_lines(chars)

        # 无字符级时间戳时退化为段级结果
        result = []
        for item in raw_items:
            if item["s_off"] is not None and item["e_off"] is not None:
                s = item["s_off"] * stride / factor
                e = item["e_off"] * stride / factor
            else:
                s = item["s"]
                e = item["e"] if item["e"] is not None else item["chunk_dur"]
            result.append((s + item["chunk_start"], e + item["chunk_start"], item["text"]))
        return result

    @staticmethod
    def _group_chars_to_lines(chars, max_dur=10.0, max_chars=40, gap=1.0):
        """把字符级时间轴切分为字幕行：停顿 > gap 秒、句末标点、
        单句最长 max_dur 秒 / 最多 max_chars 字，任一满足即换行。"""
        SENT_END = "。！？!?"
        PUNCT = SENT_END + "、…"

        lines = []
        cur = []
        prev_end = None
        for (s, e, ch) in chars:
            if cur:
                line_dur = s - cur[0][0]
                prev_ch = cur[-1][2][-1]
                if (s - prev_end > gap
                        or line_dur >= max_dur
                        or (prev_ch in SENT_END and len(cur) >= 6)
                        or (len(cur) >= max_chars and prev_ch in PUNCT)
                        or len(cur) >= max_chars * 1.5):
                    lines.append(cur)
                    cur = []
            cur.append((s, e, ch))
            prev_end = e
        if cur:
            lines.append(cur)

        return [(ln[0][0], ln[-1][1], "".join(c for _, _, c in ln).strip())
                for ln in lines]

    def _asr_nemo(self, audio_path):
        """ReazonSpeech-NeMo：长音频自动分片识别（整段推理显存随音频长度增长，
        25 分钟剧集整段推理需要 20GB+ 显存，8GB 显卡会 OOM）。
        按 60 秒分片逐段识别，时间戳统一校准后按分片偏移合并。"""
        import soundfile as sf
        import math
        import tempfile

        CHUNK_SEC = 60.0
        try:
            info = sf.info(audio_path)
            sr = int(info.samplerate)
            duration = float(info.duration)
        except Exception:
            duration = 0.0
            sr = 16000

        if duration <= CHUNK_SEC + 1:
            # 短音频：单分片处理
            raw = self._nemo_transcribe_chunk(audio_path)
            for item in raw:
                item["chunk_start"] = 0.0
                item["chunk_dur"] = duration if duration > 0 else 1e9
            return self._nemo_finish_segments(raw)

        raw_items = []
        n_chunks = int(math.ceil(duration / CHUNK_SEC))
        if self._status_callback:
            self._status_callback("长音频分片识别：共 " + str(n_chunks) + " 片（每片 60 秒）...")

        with tempfile.TemporaryDirectory() as chunk_dir:
            for i in range(n_chunks):
                if self._cancelled:
                    return "CANCELLED"
                start_sec = i * CHUNK_SEC
                frames = int(min(CHUNK_SEC, duration - start_sec) * sr)
                try:
                    data, _ = sf.read(audio_path, start=int(start_sec * sr), frames=frames, dtype="float32")
                except Exception:
                    break
                if len(data) == 0:
                    break
                chunk_path = os.path.join(chunk_dir, "chunk_%04d.wav" % i)
                sf.write(chunk_path, data, sr)

                if self._status_callback:
                    self._status_callback("分片识别中 " + str(i + 1) + "/" + str(n_chunks) + " ...")
                chunk_segs = self._nemo_transcribe_chunk(chunk_path)
                chunk_dur = len(data) / float(sr)
                for item in chunk_segs:
                    item["chunk_start"] = start_sec
                    item["chunk_dur"] = chunk_dur
                    raw_items.append(item)

                # 每片识别完主动释放缓存显存，避免累积 OOM
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
        return self._nemo_finish_segments(raw_items)

    def setup_translator(self):
        """按选择的翻译引擎初始化翻译器（完全离线，从本地加载）。"""
        if self.translator_engine.startswith("Sakura"):
            return self._setup_sakura_translator()
        return self._setup_nllb_translator()

    def _setup_nllb_translator(self):
        """
        初始化 NLLB-200 翻译器（完全离线，从本地目录加载）。
        """
        try:
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
            import torch
        except ImportError as e:
            self._translator_error = "缺少依赖库: " + str(e) + "（需要 transformers 和 torch）"
            self._translator_initialized = False
            return False

        model_path = self.translator_path or resolve_translator_path()
        if not model_path:
            self._translator_error = "未找到翻译模型，请在【设置】中指定 NLLB-200 模型目录"
            self._translator_initialized = False
            return False
        model_path = to_windows_path(model_path)
        valid, msg = check_translator_path_valid(model_path)
        if not valid:
            self._translator_error = "翻译模型无效: " + msg
            self._translator_initialized = False
            return False

        try:
            if hasattr(self, '_status_callback') and self._status_callback:
                self._status_callback("正在加载 NLLB-200 翻译模型...")

            tokenizer = AutoTokenizer.from_pretrained(model_path)
            # 目标语言的 BOS token id（新版/旧版 transformers 兼容写法）
            tgt_bos = None
            try:
                tgt_bos = tokenizer.convert_tokens_to_ids(self.NLLB_TGT_LANG)
            except Exception:
                pass
            if (tgt_bos is None or tgt_bos < 0) and hasattr(tokenizer, "lang_code_to_id"):
                tgt_bos = tokenizer.lang_code_to_id.get(self.NLLB_TGT_LANG)
            if tgt_bos is None or tgt_bos < 0:
                self._translator_error = "分词器不支持语言代码 " + self.NLLB_TGT_LANG
                self._translator_initialized = False
                return False

            # 翻译设备：跟随语音识别设备，失败时回退 CPU
            use_cuda = self.device == "cuda" and torch.cuda.is_available()
            model, _ = _safe_load_transformer(
                AutoModelForSeq2SeqLM, model_path, use_cuda, torch.float16)
            model.eval()

            # 验证翻译器是否可用
            tokenizer.src_lang = self.NLLB_SRC_LANG
            test_inputs = tokenizer("テスト", return_tensors="pt")
            # 以模型实际所在设备为准（GPU 不可用时已回退 CPU）
            if next(model.parameters()).is_cuda:
                test_inputs = {k: v.to("cuda") for k, v in test_inputs.items()}
            with torch.no_grad():
                test_out = model.generate(**test_inputs, forced_bos_token_id=tgt_bos, max_length=64)
            test_result = tokenizer.decode(test_out[0], skip_special_tokens=True)
            if test_result and test_result.strip():
                self.translator = model
                self.translator_tokenizer = tokenizer
                self._tgt_bos_token_id = tgt_bos
                self._translator_initialized = True
                self._translator_error = ""
                return True
            else:
                self._translator_error = "翻译器测试失败"
                self._translator_initialized = False
                return False

        except Exception as e:
            self._translator_error = "翻译器初始化失败: " + str(e)
            self._translator_initialized = False
            return False

    def _setup_sakura_translator(self):
        """
        初始化 Sakura GGUF 翻译器（llama-cpp-python，完全离线，支持 7B / 14B）。
        GPU 可用时自动将模型层卸载到显卡，显存不足自动回退 CPU。
        采样参数按【设置】中的 Sakura 翻译模式（速度/均衡/质量）调整。
        """
        try:
            # llama.dll 依赖 CUDA 运行库，必须在 import 前注册 DLL 路径
            ensure_cuda_dll_path()
            from llama_cpp import Llama
        except ImportError as e:
            self._translator_error = ("缺少依赖库: " + str(e) +
                                      "（请运行 一键安装GPU环境.bat 或 pip install llama-cpp-python）")
            self._translator_initialized = False
            return False

        engine = self.translator_engine
        gguf_path = self.sakura_path or resolve_sakura_path(engine=engine)
        if not gguf_path:
            self._translator_error = "未找到 " + engine + " GGUF 模型，请在【设置】中指定 .gguf 文件"
            self._translator_initialized = False
            return False
        gguf_path = to_windows_path(gguf_path)
        valid, msg = check_sakura_path_valid(gguf_path)
        if not valid:
            self._translator_error = engine + " 模型无效: " + msg
            self._translator_initialized = False
            return False

        # Sakura 翻译模式（速度优先 / 均衡 / 质量优先）
        cfg = load_config()
        mode_name = cfg.get("sakura_mode", "均衡（推荐）")
        mode = SAKURA_MODES.get(mode_name, SAKURA_MODES["均衡（推荐）"])
        self._sakura_mode = mode

        size_hint = {"Sakura-7B": "约 4GB", "Sakura-GalTransl-7B": "约 6.3GB"}.get(engine, "约 9GB")
        try:
            if hasattr(self, '_status_callback') and self._status_callback:
                self._status_callback("正在加载 " + engine + " 翻译模型（" + size_hint +
                                      "，模式: " + mode_name + "，请耐心等待）...")

            use_cuda = False
            try:
                import torch
                use_cuda = self.device == "cuda" and torch.cuda.is_available()
            except Exception:
                pass
            if use_cuda:
                ensure_cuda_dll_path()

            last_err = None
            # 依次尝试：全部层上 GPU -> 部分层上 GPU -> 纯 CPU
            gpu_layers_candidates = [-1, 20, 0] if use_cuda else [0]
            # 屏蔽 llama.cpp C 级别 stderr：模型训练上下文(131072)大于运行时 n_ctx 时
            # 会打印 "n_ctx_per_seq ... the full capacity ..." 提示，属正常信息、不影响功能，
            # 但该提示走 C 层标准错误直接输出，verbose=False 无法屏蔽，故重定向 fd 2。
            _saved_stderr = os.open(os.devnull, os.O_WRONLY)
            _old_stderr = os.dup(2)
            os.dup2(_saved_stderr, 2)
            try:
                for n_layers in gpu_layers_candidates:
                    try:
                        self.translator = Llama(
                            model_path=gguf_path,
                            n_ctx=mode["n_ctx"],
                            n_gpu_layers=n_layers,
                            verbose=False,
                        )
                        break
                    except Exception as e:
                        last_err = e
                        self.translator = None
            finally:
                os.dup2(_old_stderr, 2)
                os.close(_old_stderr)
                os.close(_saved_stderr)
            if self.translator is None:
                raise RuntimeError(engine + " 模型加载失败: " + str(last_err))

            # 验证翻译器是否可用
            test_result = self._translate_sakura("テスト")
            if test_result and "[翻译" not in test_result and test_result.strip():
                self._translator_initialized = True
                self._translator_error = ""
                return True
            else:
                self._translator_error = "Sakura 翻译器测试失败"
                self._translator_initialized = False
                return False

        except Exception as e:
            self._translator_error = "Sakura 翻译器初始化失败: " + str(e)
            self._translator_initialized = False
            return False

    def translate_text(self, text):
        """
        将日文翻译为简体中文（按选择的翻译引擎）。
        翻译失败时返回原文 + 错误提示，保证字幕仍能生成。
        """
        if not text or not text.strip():
            return ""

        # 如果翻译器未初始化，尝试初始化
        if not self._translator_initialized or self.translator is None:
            success = self.setup_translator()
            if not success:
                # 备用方案：返回原文 + 错误提示
                return text + "\n[翻译失败: " + self._translator_error + "]"

        if self.translator_engine.startswith("Sakura"):
            return self._translate_sakura(text.strip())
        return self._translate_nllb(text.strip())

    def _translate_sakura(self, text):
        """Sakura 系列（7B/14B）：轻小说风格日译中。
        Prompt 明确要求完整忠实翻译、保持原文全部语气（包括色情、娇喘、
        命令等表达），不回避、不美化、不省略，避免模型自我审查。
        采样参数按当前 Sakura 翻译模式（速度/均衡/质量）调整。"""
        mode = getattr(self, "_sakura_mode", SAKURA_MODES["均衡（推荐）"])
        try:
            response = self.translator.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个轻小说翻译模型，可以流畅通顺地以日本轻小说的风格"
                                   "将日文翻译成简体中文，并联系上下文正确使用人称代词，"
                                   "不擅自改变原文情节。"
                                   "你必须完整、忠实地翻译原文的全部内容，保持原文的语气、"
                                   "情感和表达方式，包括色情、娇喘、命令、辱骂、挑逗等一切表达，"
                                   "不得回避、不得美化、不得删减、不得替换敏感词汇，"
                                   "逐字逐句忠实呈现原文的含义与语气。"
                    },
                    {
                        "role": "user",
                        "content": "将下面的日文文本完整忠实地翻译成中文，保持原文语气，"
                                   "不要回避或美化任何内容：" + text
                    }
                ],
                temperature=mode["temperature"],
                top_p=mode["top_p"],
                max_tokens=mode["max_tokens"],
            )
            result = response["choices"][0]["message"]["content"].strip()
            if result:
                return result
            return text + "\n[翻译结果为空]"
        except Exception as e:
            return text + "\n[翻译错误: " + str(e) + "]"

    def _translate_nllb(self, text):
        """NLLB-200：通用日译中。"""
        try:
            import torch
            model = self.translator
            tokenizer = self.translator_tokenizer
            if model is None or tokenizer is None:
                return text + "\n[翻译器未就绪]"

            tokenizer.src_lang = self.NLLB_SRC_LANG
            inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
            if next(model.parameters()).is_cuda:
                inputs = {k: v.to("cuda") for k, v in inputs.items()}
            with torch.no_grad():
                output = model.generate(
                    **inputs,
                    forced_bos_token_id=self._tgt_bos_token_id,
                    max_length=512,
                    num_beams=4,
                )
            result = tokenizer.decode(output[0], skip_special_tokens=True)
            if result and result.strip():
                return result.strip()
            else:
                return text + "\n[翻译结果为空]"
        except Exception as e:
            return text + "\n[翻译错误: " + str(e) + "]"

    def extract_audio(self, video_path, output_audio=None):
        if output_audio is None:
            output_audio = os.path.splitext(video_path)[0] + "_temp_audio.wav"
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            ffmpeg_cmd, "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            output_audio
        ]
        # 使用 Popen 以便支持取消
        self._audio_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = self._audio_proc.communicate()
            if self._cancelled:
                raise RuntimeError("音频提取已取消")
            if self._audio_proc.returncode != 0:
                raise RuntimeError("音频提取失败: " + stderr.decode("utf-8", errors="replace"))
        except Exception:
            # 发生异常（包括取消）时确保子进程已终止
            if self._audio_proc and self._audio_proc.poll() is None:
                self._audio_proc.terminate()
                try:
                    self._audio_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._audio_proc.kill()
                    self._audio_proc.wait()
            self._audio_proc = None
            raise
        self._audio_proc = None
        return output_audio

    def kill_audio_proc(self):
        """强制终止正在运行的音频提取子进程。"""
        if self._audio_proc and self._audio_proc.poll() is None:
            self._audio_proc.terminate()
            try:
                self._audio_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._audio_proc.kill()
                self._audio_proc.wait()
        self._audio_proc = None

    def format_srt_time(self, seconds):
        td = timedelta(seconds=seconds)
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def generate_srt(self, segments, output_path, bilingual=False,
                       progress_callback=None, status_callback=None):
        srt_lines = []

        # 先初始化翻译器
        if status_callback:
            status_callback("正在初始化翻译引擎...")
        self.setup_translator()

        # 规范化时间轴：修正重叠、封顶显示时长、避免字幕提前出现/残留
        segments = self._normalize_timeline(segments)
        total = len(segments)
        out_idx = 0

        for i, segment in enumerate(segments, 1):
            if self._cancelled:
                return None

            seg_start, seg_end, ja_text = segment[0], segment[1], segment[2]
            ja_text = ja_text.strip()
            if not ja_text:
                continue

            if status_callback:
                status_callback("正在翻译第 " + str(i) + "/" + str(total) + " 条字幕...")

            # 翻译
            zh_text = self.translate_text(ja_text)
            # 折叠译文中的幻觉重复、清理句首语气词（翻译失败带错误提示的原文不处理）
            if "[翻译" not in zh_text and "[翻译器" not in zh_text:
                zh_text = self._collapse_repeated_text(zh_text.strip())
                zh_text = self._clean_leading_filler(zh_text, "zh")

            # 纯语气词字幕（嗯/啊等）不显示；译文异常为空时回退原文避免丢句
            if not zh_text or self._is_filler_only(zh_text):
                if self._is_filler_only(ja_text) or len(ja_text) <= 4:
                    continue
                zh_text = ja_text

            start_time = self.format_srt_time(seg_start)
            end_time = self.format_srt_time(seg_end)

            if bilingual:
                # 双语模式：日文在上，中文在下
                text = ja_text + "\n" + zh_text
            else:
                # 单语模式：只显示中文
                text = zh_text

            out_idx += 1
            srt_lines.append(str(out_idx))
            srt_lines.append(start_time + " --> " + end_time)
            srt_lines.append(text)
            srt_lines.append("")

            if progress_callback:
                progress_callback(int((i / total) * 100))

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(srt_lines))
        return output_path

    def process_video(self, video_path, output_srt=None, bilingual=False,
                      progress_callback=None, status_callback=None):
        """
        处理视频，支持详细的进度回调
        """
        self._cancelled = False
        self._status_callback = status_callback  # 保存状态回调用于翻译器初始化

        if output_srt is None:
            output_srt = os.path.splitext(video_path)[0] + ".srt"

        temp_audio = None
        try:
            # 1. 提取音频
            if status_callback:
                status_callback("正在从视频提取音频...")
            if progress_callback:
                progress_callback(5, STATUS_EXTRACTING)
            temp_audio = self.extract_audio(video_path)
            if progress_callback:
                progress_callback(15, STATUS_EXTRACTING)

            # 2. 加载模型
            if status_callback:
                status_callback("正在加载语音识别模型（" + self.asr_engine + "）...")
            if progress_callback:
                progress_callback(20, STATUS_LOADING_MODEL)
            if self.model is None:
                self.load_model()
            if progress_callback:
                progress_callback(30, STATUS_LOADING_MODEL)

            # 3. 语音识别
            if status_callback:
                status_callback("正在进行语音识别（日文）...")
            if progress_callback:
                progress_callback(35, STATUS_RECOGNIZING)

            segment_list = self.run_asr(temp_audio, progress_callback)
            if segment_list == "CANCELLED":
                return "CANCELLED"

            if status_callback:
                status_callback("识别完成！检测到 " + str(len(segment_list)) + " 条语音片段")
            if progress_callback:
                progress_callback(65, STATUS_RECOGNIZING)

            # 4. 生成字幕（包含翻译）
            if status_callback:
                status_callback("正在翻译并生成字幕...")
            if progress_callback:
                progress_callback(70, STATUS_GENERATING)
            result = self.generate_srt(
                segment_list, output_srt, bilingual,
                lambda p: progress_callback(70 + int(p * 0.28), STATUS_GENERATING) if progress_callback else None,
                status_callback
            )

            if result and not self._cancelled:
                if progress_callback:
                    progress_callback(100, STATUS_COMPLETED)
                return result
            elif self._cancelled:
                return "CANCELLED"
            else:
                return None

        finally:
            if temp_audio and os.path.exists(temp_audio):
                try:
                    os.remove(temp_audio)
                except:
                    pass

    def cancel(self):
        self._cancelled = True

    def release_resources(self):
        """
        释放语音识别模型和翻译模型占用的资源（含 GPU 显存）。
        任务结束后必须调用，避免显存持续占用。
        """
        self.model = None
        self.kotoba_processor = None
        self.translator = None
        self.translator_tokenizer = None
        self._tgt_bos_token_id = None
        self._translator_initialized = False
        release_gpu_memory()


# ============================================================
# 工作线程 - 处理单个任务
# ============================================================

class WorkerThread(threading.Thread):
    def __init__(self, task, model_path, device, compute_type, bilingual,
                 on_progress, on_status, on_complete, translator_path=None,
                 asr_engine="faster-whisper", translator_engine="NLLB-200",
                 kotoba_path=None, nemo_path=None, sakura_path=None):
        super().__init__(daemon=True)
        self.task = task
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.bilingual = bilingual
        self.translator_path = translator_path
        self.asr_engine = asr_engine
        self.translator_engine = translator_engine
        self.kotoba_path = kotoba_path
        self.nemo_path = nemo_path
        self.sakura_path = sakura_path
        self.on_progress = on_progress
        self.on_status = on_status
        self.on_complete = on_complete
        self.generator = None

    def run(self):
        try:
            self.generator = SubtitleGenerator(
                model_path=self.model_path,
                device=self.device,
                compute_type=self.compute_type,
                translator_path=self.translator_path,
                asr_engine=self.asr_engine,
                translator_engine=self.translator_engine,
                kotoba_path=self.kotoba_path,
                nemo_path=self.nemo_path,
                sakura_path=self.sakura_path
            )

            def progress_cb(progress, stage):
                self.task.update_progress(progress, stage)
                self.on_progress(self.task)

            def status_cb(msg):
                self.task.status_detail = msg
                self.on_status(self.task)

            result = self.generator.process_video(
                self.task.video_path,
                output_srt=self.task.output_path,
                bilingual=self.bilingual,
                progress_callback=progress_cb,
                status_callback=status_cb
            )

            if result == "CANCELLED":
                self.task.set_status(STATUS_CANCELLED, "用户取消")
            elif result:
                self.task.set_status(STATUS_COMPLETED, "字幕已生成")
            else:
                self.task.set_status(STATUS_ERROR, "生成失败")

        except RuntimeError as e:
            error_str = str(e)
            if "cublas" in error_str.lower() or "cuda" in error_str.lower():
                self.task.error_msg = error_str
                self.task.set_status(STATUS_ERROR, "CUDA/GPU 错误，请切换到 CPU 模式")
            else:
                self.task.error_msg = error_str
                self.task.set_status(STATUS_ERROR, error_str[:100])
        except Exception as e:
            self.task.error_msg = str(e)
            self.task.set_status(STATUS_ERROR, str(e)[:100])
        finally:
            # 任务结束（无论成功/失败/取消）立即释放模型与显存
            if self.generator:
                self.generator.release_resources()
                self.generator = None
            self.on_complete(self.task)

    def cancel(self):
        if self.generator:
            self.generator.cancel()
        self.task.cancel()

