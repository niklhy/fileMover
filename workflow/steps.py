#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流串联 —— 步骤执行器

所有步骤都不重新实现业务逻辑，而是**直接复用现有模块**：

* 移动      : 复用 ``file_move_tab`` 的去重落盘规则（``unique_dst``）
* 剪辑转码  : 复用 ``video_grid_tab.VideoTranscodePanel`` 的 ffmpeg 命令构建与
              降级重试逻辑（用 ``HeadlessTranscoder`` 以无界面方式驱动其 ``_worker``）
* 生成字幕  : 复用 ``subtitle.core.SubtitleGenerator`` 与 ``subtitle.system_utils``
              的模型路径解析 / CUDA 可用性检查
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import ctypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import (STEP_MOVE, STEP_TRANSCODE, STEP_SUBTITLE, STEP_RENAME,
                     ENCODER_LABELS, SUBTITLE_MODE_LABELS, WorkflowItem,
                     VIDEO_EXTENSIONS)

try:  # 复用 file_move_tab 的“目标已存在则加序号”规则
    from file_move_tab import unique_dst
except Exception:  # pragma: no cover
    def unique_dst(dst: Path) -> Path:
        if not dst.exists():
            return dst
        stem, suffix = dst.stem, dst.suffix
        i = 1
        while True:
            cand = dst.parent / f"{stem}_{i}{suffix}"
            if not cand.exists():
                return cand
            i += 1


class StepStopped(Exception):
    """用户请求停止"""


class StepError(Exception):
    """步骤执行失败"""


class StepMergedSkip(Exception):
    """拼接任务中的非首个视频：已并入首段统一拼接，本条目后续步骤跳过"""


class StepContext:
    """步骤执行上下文：日志 / 进度 / 停止信号 / ffmpeg 路径 / 暂停控制"""

    def __init__(self, log: Optional[Callable[[str], None]] = None,
                 progress: Optional[Callable[[float, str], None]] = None,
                 is_stopped: Optional[Callable[[], bool]] = None,
                 ffmpeg_path: str = "",
                 notify: Optional[Callable[[], None]] = None):
        self._log = log or (lambda m: None)
        self._progress = progress or (lambda p, t=None: None)
        self._is_stopped = is_stopped or (lambda: False)
        self._notify = notify or (lambda: None)
        self.ffmpeg_path = ffmpeg_path or (shutil.which('ffmpeg') or "")
        # ---- 暂停控制 ----
        self._paused = False
        self._released = threading.Event()      # 暂停期间，协作式循环（如逐条翻译）在此等待
        self._released.set()                    # 初始为“已释放”，避免空等
        self._proc_provider: Optional[Callable[[], Optional[Any]]] = None
        self._stop_setter: Optional[Callable[[], None]] = None

    # ---- 暂停 / 恢复 ----
    def set_proc_provider(self, fn: Callable[[], Optional[Any]]):
        """注册一个返回“当前活动 ffmpeg 子进程”的回调（用于操作系统级挂起/恢复）"""
        self._proc_provider = fn

    def set_stop_setter(self, fn: Callable[[], None]):
        """注册一个“标记停止”的回调（用于暂停后停止时，让 _worker 判定为已停止而非失败）"""
        self._stop_setter = fn

    def mark_stopped(self):
        """立即标记底层步骤为已停止（供“暂停后停止”使用）"""
        if self._stop_setter:
            try:
                self._stop_setter()
            except Exception:
                pass

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self):
        """冻结当前步骤：挂起 ffmpeg 子进程（视频剪辑/音频转换），并标记暂停状态
        （字幕逐条翻译循环会在 check_pause() 处阻塞）。"""
        if self._paused:
            return
        self._paused = True
        self._released.clear()
        # 冻结当前 ffmpeg 子进程：其 stdout 随之停滞，执行线程自然阻塞，恢复后从原进度继续
        self._suspend_active_proc()

    def resume(self):
        """从暂停处恢复：恢复 ffmpeg 子进程，并唤醒协作式循环"""
        if not self._paused:
            return
        self._paused = False
        self._resume_active_proc()
        self._released.set()

    def check_pause(self):
        """协作式暂停点：供 Python 循环（如逐条翻译）调用；已暂停则阻塞到恢复"""
        if self._paused:
            self._released.wait()

    def _active_proc(self):
        try:
            if self._proc_provider:
                return self._proc_provider()
        except Exception:
            return None
        return None

    def _suspend_active_proc(self):
        proc = self._active_proc()
        if proc is not None and getattr(proc, 'poll', None) and proc.poll() is None:
            _suspend_process(proc)

    def _resume_active_proc(self):
        proc = self._active_proc()
        if proc is not None and getattr(proc, 'poll', None) and proc.poll() is None:
            _resume_process(proc)

    def terminate_active_proc(self):
        """终止当前活动子进程（用于“暂停后停止”：让 _worker 立即观察到进程结束）"""
        proc = self._active_proc()
        if proc is not None and getattr(proc, 'poll', None) and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def log(self, msg: str):
        try:
            self._log(str(msg))
        except Exception:
            pass

    def progress(self, pct: float, text: str = ""):
        try:
            self._progress(float(pct), text)
        except Exception:
            pass

    def notify_item(self):
        """条目状态在步骤内被修改后，请求界面刷新列表（执行器会节流）"""
        try:
            self._notify()
        except Exception:
            pass

    def stopped(self) -> bool:
        try:
            return bool(self._is_stopped())
        except Exception:
            return False

    def check_stop(self):
        if self.stopped():
            raise StepStopped("已停止")


# ==================================================================
# 进程挂起 / 恢复（Windows）
# ==================================================================
def _nt_suspend_resume(pid: int, suspend: bool):
    """Windows：调用 NtSuspendProcess / NtResumeProcess 冻结/恢复整个进程的所有线程。
    这样 ffmpeg 在“视频剪辑 / 音频转换”中途被挂起后，恢复时从完全相同的编码进度继续，
    画面与音频数据不会丢失、不会变坏。"""
    try:
        kernel32 = ctypes.windll.kernel32   # type: ignore[attr-defined]
        ntdll = ctypes.windll.ntdll          # type: ignore[attr-defined]
        PROCESS_SUSPEND_RESUME = 0x0800
        h = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h:
            return
        try:
            ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
            ntdll.NtSuspendProcess.restype = ctypes.c_long
            ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
            ntdll.NtResumeProcess.restype = ctypes.c_long
            if suspend:
                ntdll.NtSuspendProcess(h)
            else:
                ntdll.NtResumeProcess(h)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass


def _suspend_process(proc):
    if proc is None or proc.poll() is not None:
        return
    if os.name == 'nt':
        _nt_suspend_resume(proc.pid, suspend=True)
    # 非 Windows 无简单挂起手段：忽略（字幕循环仍可用协作式 check_pause 暂停）


def _resume_process(proc):
    if proc is None or proc.poll() is not None:
        return
    if os.name == 'nt':
        _nt_suspend_resume(proc.pid, suspend=False)


# ==================================================================
# 无界面转码器：复用 VideoTranscodePanel 的全部转码逻辑
# ==================================================================
class _Var:
    """tk 变量的极简替身（供无界面复用面板方法时使用）"""

    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class HeadlessTranscoder:
    """以无界面方式驱动 ``video_grid_tab.VideoTranscodePanel`` 的转码流程。

    做法：用 ``__new__`` 造出一个不构建任何控件的面板实例，补齐 ``_worker``
    所依赖的少量状态与回调，再直接调用 ``_worker(task)``。
    这样剪辑/缩放/字幕封装/编码器选择与降级重试全部沿用现有实现，无重复代码。
    """

    def __init__(self, ffmpeg_path: str = "", log: Optional[Callable[[str], None]] = None,
                 on_stop: Optional[Callable[[], bool]] = None):
        # 延迟导入：避免工作流模块被导入时就拉起重量级依赖
        from video_grid_tab import VideoTranscodePanel, TranscodeTask  # noqa: WPS433

        self.TranscodeTask = TranscodeTask
        self._PanelCls = VideoTranscodePanel
        self._log = log or (lambda m: None)
        self._on_stop = on_stop or (lambda: False)

        p = VideoTranscodePanel.__new__(VideoTranscodePanel)
        # --- _worker 依赖的状态 ---
        p._lock = threading.Lock()
        p._procs = []
        p._done = p._success = p._fail = p._cancel = 0
        p._total = 0
        p._failures = []
        p.ffmpeg_var = _Var(ffmpeg_path or "")
        p.encoder_var = _Var(ENCODER_LABELS["auto"])
        p.del_src_var = _Var(False)     # 源文件的删除由工作流自己决定
        p.del_srt_var = _Var(False)
        # --- 回调替换（实例属性优先于类方法） ---
        p._log = lambda msg, level="info": self._log(str(msg))
        p._set_task_status = lambda task, text, progress=None: None
        p._update_status_bar = lambda prefix="": None
        p._update_overall_progress = lambda: None
        p._all_done = lambda: None
        p._finish_one = self._finish
        # 输出路径由工作流按 {uid} 模板决定
        p._resolve_output = lambda task: Path(task.out_path)
        p.ffmpeg_path = ffmpeg_path or (shutil.which('ffmpeg') or "")
        self._p = p
        self._result: Dict[str, Any] = {"status": "fail", "detail": "", "out": ""}

    # ---- 结果收集 ----
    def _finish(self, task, status: str, detail: str):
        self._result = {
            "status": status,                       # success / fail / skipped
            "detail": detail or "",
            "out": getattr(task, 'out_path', '') or "",
        }

    # ---- 媒体探测 ----
    def probe(self, path: str):
        """复用面板的探测逻辑（OpenCV + ffprobe 兜底：分辨率/帧率/时长/码率）

        OpenCV 读不了 rm/rmvb、WMV9/VC-1 等格式时，`_probe_video` 内部会用
        ffprobe 补齐宽高/帧率/时长，避免“加入队列后只输出 0.01 秒”的空片段。
        """
        item = self._p._probe_video(path)
        if not getattr(item, 'end', 0):
            item.end = item.duration
        return item

    # ---- 执行单个任务 ----
    def run(self, task, is_stopped: Optional[Callable[[], bool]] = None,
            on_progress: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
        p = self._p
        p.stop_requested = False
        p._total = 1
        p._done = p._success = p._fail = p._cancel = 0
        p._failures = []
        self._result = {"status": "fail", "detail": "", "out": ""}

        def _stopped() -> bool:
            if is_stopped and is_stopped():
                p.stop_requested = True
            return p.stop_requested

        p._stop_check = _stopped

        def _status(_task, _text, progress=None):
            if on_progress and progress is not None:
                try:
                    on_progress(float(progress))
                except Exception:
                    pass

        p._set_task_status = _status

        # 编码器与实际输出路径（含 {uid} 模板）已在调用方设定
        p.encoder_var.set(getattr(task, '_encoder_label', ENCODER_LABELS['auto']))
        p._active_encoder = p._resolve_encoder()
        p._use_gpu_pipeline = (p._active_encoder == 'nvenc' and p._has_cuda_filters())

        # 明确告知本次实际使用的编码方式（GPU 硬件加速 / CPU 软编码），避免日志歧义
        enc_name = ENCODER_LABELS.get(p._active_encoder, p._active_encoder)
        if p._active_encoder == 'cpu':
            self._log(f"[编码] 使用 CPU 软编码（libx265，未使用显卡加速）")
        elif p._use_gpu_pipeline:
            self._log(f"[编码] 使用显卡硬件加速：{enc_name}"
                      f"（全显存流水线：CUDA 解码 → scale_cuda/pad_cuda → NVENC 编码）")
        else:
            self._log(f"[编码] 使用显卡硬件加速：{enc_name}"
                      f"（CPU 解码/缩放 + 显卡编码）")

        # 后台心跳：把外部停止信号同步给 _worker
        stopper = None
        if is_stopped:
            def _beat():
                while not getattr(p, '_beat_done', False):
                    if is_stopped():
                        p.stop_requested = True
                        return
                    time.sleep(0.3)
            p._beat_done = False
            stopper = threading.Thread(target=_beat, daemon=True)
            stopper.start()

        try:
            p._worker(task)
        finally:
            p._beat_done = True
        return dict(self._result)


# ==================================================================
# 步骤一：移动到文件夹
# ==================================================================
def run_move(item: WorkflowItem, params: Dict[str, Any], ctx: StepContext) -> str:
    src = Path(item.path)
    if not src.exists():
        raise StepError(f"源文件不存在: {src}")

    target_dir = (params.get('target_dir') or '').strip()
    if not target_dir:
        raise StepError("未设置移动目标文件夹")

    dest_dir = Path(item.render(target_dir))
    if params.get('uid_subdir'):
        dest_dir = dest_dir / item.uid
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise StepError(f"无法创建目标文件夹 {dest_dir}: {e}")

    dest = unique_dst(dest_dir / src.name)
    ctx.log(f"[移动] {src.name} → {dest}")
    try:
        shutil.move(str(src), str(dest))
    except Exception as e:
        raise StepError(f"移动失败: {e}")

    # 同名附属文件（字幕/封面等）一并移动，避免后续阶段匹配不到
    if params.get('with_companions'):
        exts = [e.strip().lower() for e in
                str(params.get('companion_exts') or '').split(',') if e.strip()]
        for ext in exts:
            side = src.with_suffix(ext)
            if not side.exists() or side == src:
                continue
            try:
                side_dst = unique_dst(dest_dir / side.name)
                shutil.move(str(side), str(side_dst))
                ctx.log(f"[移动附属] {side.name}")
                if ext == '.srt':
                    item.subtitle = str(side_dst)
            except Exception as e:
                ctx.log(f"[警告] 附属文件 {side.name} 移动失败: {e}")

    return str(dest)


# ==================================================================
# 步骤二：剪辑与重编码
# ==================================================================
def _resolve_trim(item: WorkflowItem, params: Dict[str, Any],
                  duration: float) -> (float, float):
    """计算剪辑起止时间（秒）"""
    if item.use_custom_trim and item.trim_end > item.trim_start:
        return max(0.0, item.trim_start), min(item.trim_end, duration or item.trim_end)
    if item.use_custom_trim and item.trim_end <= 0 and duration:
        return max(0.0, item.trim_start), duration

    mode = params.get('trim_mode', 'none')
    dur = duration if duration > 0 else 0.0
    if mode == 'range':
        s = float(params.get('start') or 0.0)
        e = float(params.get('end') or 0.0)
        if e <= 0:
            e = dur
        return max(0.0, s), max(0.0, min(e, dur) if dur else e)
    if mode == 'head':
        s = float(params.get('head') or 0.0)
        return max(0.0, s), max(s, dur)
    if mode == 'tail':
        t = float(params.get('tail') or 0.0)
        return 0.0, max(0.0, dur - t)
    return 0.0, dur          # none / custom（未单独设置）


def run_transcode(item: WorkflowItem, params: Dict[str, Any], ctx: StepContext) -> str:
    src = Path(item.path)
    if not src.exists():
        raise StepError(f"源文件不存在: {src}")
    if not ctx.ffmpeg_path:
        raise StepError("未找到 ffmpeg，请安装或在本模块设置中指定路径")

    tc = HeadlessTranscoder(ffmpeg_path=ctx.ffmpeg_path, log=ctx.log,
                            on_stop=ctx.stopped)
    # 把当前 ffmpeg 子进程暴露给 ctx，便于暂停时操作系统级挂起/恢复
    # （HeadlessTranscoder 内部会把活动中的 ffmpeg 进程追加到 self._p._procs，取最后一个即为当前转码）
    def _proc_provider():
        procs = getattr(getattr(tc, '_p', None), '_procs', None)
        if procs:
            return procs[-1]
        return None
    ctx.set_proc_provider(_proc_provider)
    # 暂停后停止时，需要立即标记底层 _worker 为停止，避免被误判为失败
    ctx.set_stop_setter(lambda: setattr(getattr(tc, '_p', None), 'stop_requested', True))
    info = tc.probe(str(src))
    item.duration = float(getattr(info, 'duration', 0) or 0)
    item.fps = float(getattr(info, 'fps', 0) or 0)
    item.width = int(getattr(info, 'width', 0) or 0)
    item.height = int(getattr(info, 'height', 0) or 0)

    # ---- 拼接：勾选后，同一任务内的多个视频合并成一个成片 ----
    if bool(params.get('tc_merge')):
        task_items = list(getattr(ctx, 'task_items', None) or [])
        if len(task_items) > 1:
            if item is not task_items[0]:
                # 非首段视频：由第一个视频统一拼接输出，本条目到此完成
                ctx.log(f"[拼接] {src.name}: 已并入本任务第一个视频统一拼接，跳过单独转码")
                raise StepMergedSkip(src.name)
            return _run_merge_transcode(task_items, params, ctx, tc)
        ctx.log("[提示] 已勾选“拼接为一个视频”，但本任务只有 1 个视频，按单视频转码执行")

    start, end = _resolve_trim(item, params, item.duration)
    if end <= start:
        end = item.duration if item.duration > start else start + 0.04
    ctx.log(f"[剪辑] {src.name}: {start:.2f}s ~ {end:.2f}s（时长 {item.duration:.2f}s）")

    # 输出位置与文件名
    if params.get('save_mode') == 'custom' and (params.get('save_dir') or '').strip():
        out_dir = Path(item.render(params['save_dir']))
    else:
        out_dir = src.parent
    ext = (params.get('out_ext') or 'mp4').lstrip('.') or 'mp4'
    # 改名职责已移至“修改名称”步骤：转码输出固定沿用当前文件名（只换容器扩展名），
    # 不再读取输出名模板 —— 旧版本配置残留的 name_template（如 {uid_norm}-C）曾与
    # “修改名称”步骤叠加执行，导致名称被追加两次（ROYD-273-Uncensored-C-C.mp4）
    out_name = f"{src.stem}.{ext}"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise StepError(f"无法创建输出文件夹 {out_dir}: {e}")
    intended = out_dir / out_name
    # 输出文件名与原文件相同（如用 {name} 且同一文件夹）：ffmpeg 不能对同名文件同时
    # 读写，因此先输出到临时名，转码成功后再删除原文件、把临时成片改回原名（原地替换）
    inplace = os.path.normcase(str(intended)) == os.path.normcase(str(src))
    # 转码输出到原文件夹且后续有“修改名称”步骤：绝不原地替换/删除原始视频——
    # 始终写到不冲突的名字，最终命名交给改名步骤（组合意图是生成新文件而非覆盖原片）。
    if getattr(ctx, 'rename_after_transcode', False):
        inplace = False
    if inplace:
        out = out_dir / (".wf_tmp_" + out_name)
        if out.exists():
            out = unique_dst(out)
    else:
        out = unique_dst(intended)

    # 字幕封装：优先用工作流里已生成的字幕，其次找视频同目录同名 srt
    sub_mode = params.get('subtitle_mode') or 'none'
    keep_quality = bool(params.get('keep_quality'))
    # 逻辑修正：「软字幕·快速附加（不重编码）」只用流拷贝（-c copy），无法按起止时间
    # 精确剪辑。若本步骤实际设置了剪辑（start>0 或 end<整段）且未勾选“保留原画质”
    # （流拷贝剪辑分支能同时裁剪并挂字幕），则自动升级为「软字幕·封装（随转码）」，
    # 否则剪辑会被静默忽略（与用户“剪辑并附加字幕”的意图冲突）。
    if (not keep_quality and sub_mode == 'soft_fast' and
            (start > 0.01 or (item.duration > 0 and end < item.duration - 0.01))):
        ctx.log(f"[字幕] {src.name}: “软字幕·快速附加（不重编码）”与剪辑冲突——"
                f"不重编码无法按起止时间精确裁剪，已自动改用“软字幕·封装（随转码）”"
                f"以同时保留剪辑与字幕。")
        sub_mode = 'soft'
    if keep_quality:
        ctx.log(f"[流拷贝剪辑] {src.name}: 保留原分辨率/码率，仅按起止时间裁剪（-c copy）")
    srt = ''
    if sub_mode != 'none':
        if item.subtitle and os.path.isfile(item.subtitle):
            srt = item.subtitle
        else:
            cand = Path(src).with_suffix('.srt')
            if cand.exists():
                srt = str(cand)
        if not srt:
            ctx.log(f"[提示] 未找到可封装的字幕，本次按“不添加字幕”处理")
            sub_mode = 'none'

    task = tc.TranscodeTask(
        src=str(src), start=start, end=end,
        src_w=item.width, src_h=item.height, fps=item.fps,
        resolution=params.get('resolution') or '1280x720',
        aspect=params.get('aspect') or '保持原比例',
        bitrate=int(params.get('bitrate') or 2500),
        save_mode='custom', save_dir=str(out_dir),
        out_ext=ext, subtitle_mode=sub_mode, subtitle=srt,
        keep_quality=keep_quality,
        keep_bitrate=bool(params.get('keep_bitrate', False)),   # 沿用原片码率
        overwrite_src=False,
        # 竖屏自动适配：横屏/竖屏混合时，竖屏源自动改用竖屏目标分辨率
        auto_orient=bool(params.get('auto_orient', True)),
        portrait_res=params.get('portrait_res') or '自动(按源比例)',
    )
    task.out_path = str(out)
    task._encoder_label = params.get('encoder') or ENCODER_LABELS['auto']  # noqa: SLF001

    ctx.log(f"[转码] {src.name} → {out.name}")
    # 重资源操作（NVENC/CUDA 全显存流水线）与语音识别、翻译共享同一把“重资源锁”，
    # 保证多线程下任意时刻只有一个重资源操作在跑，避免多个视频同时转码/识别/翻译抢占
    # 显存与硬件编码器导致崩溃或爆显存。文件类步骤（移动/改名）不抢此锁，仍可并行。
    res_lock = getattr(ctx, 'resource_lock', None)
    if res_lock is not None:
        res_lock.acquire()
    try:
        result = tc.run(task, is_stopped=ctx.stopped,
                        on_progress=lambda pct: ctx.progress(pct, f"转码 {src.name}"))
    finally:
        if res_lock is not None:
            res_lock.release()
    if result.get('status') != 'success':
        if result.get('status') == 'skipped':
            raise StepStopped("已停止")
        raise StepError(result.get('detail') or "转码失败")

    produced = Path(result.get('out') or out)
    if not produced.exists():
        raise StepError(f"转码输出文件不存在: {produced}")

    # 原地替换：转码成功后删除原文件，把临时成片改回原始文件名
    if inplace:
        try:
            if src.exists() and os.path.normcase(os.path.abspath(str(src))) != \
                    os.path.normcase(os.path.abspath(str(produced))):
                os.remove(str(src))
            os.replace(str(produced), str(intended))
            produced = intended
            ctx.log(f"[替换原文件] {src.name}（成片已原地替换）")
        except Exception as e:
            raise StepError(f"替换原文件失败（临时成片位于 {produced}）: {e}")

    # 转码成功且勾选时删除转码源文件（绝不删除刚生成的成片；原地替换时已删除）
    if params.get('del_src') and os.path.normcase(os.path.abspath(str(src))) != \
            os.path.normcase(os.path.abspath(str(produced))):
        try:
            os.remove(str(src))
            ctx.log(f"[已删除源文件] {src.name}")
        except Exception as e:
            ctx.log(f"[警告] 删除源文件失败 {src.name}: {e}")

    # 软字幕封装（soft / soft_fast）且勾选时，删除已并入成片的原始 .srt
    if params.get('del_srt') and sub_mode != 'none' and srt and os.path.isfile(srt):
        # 只删独立字幕文件，成片内已是封装好的字幕，互不影响
        try:
            os.remove(srt)
            ctx.log(f"[已删除字幕] {os.path.basename(srt)}")
            if os.path.normcase(os.path.abspath(str(item.subtitle))) == \
                    os.path.normcase(os.path.abspath(srt)):
                item.subtitle = ""
        except Exception as e:
            ctx.log(f"[警告] 删除字幕失败 {srt}: {e}")

    return str(produced)


# ==================================================================
# 步骤二·拼接：任务内多个视频按顺序合并成一个成片
# ==================================================================
def _run_merge_transcode(items: List[WorkflowItem], params: Dict[str, Any],
                         ctx: StepContext, tc: "HeadlessTranscoder") -> str:
    """把同一任务内的全部视频按清单顺序拼接为一个成片。

    每个视频保留各自设置的剪辑时间（双击视频行设置 / 掐头去尾 / 统一时间段），
    全部按统一参数重新编码后无损拼接；各视频的 srt 字幕按拼接顺序整体对齐后封装。
    成片名为“第一个视频名_合并”，输出位置沿用“输出位置”设置。
    """
    ext = (params.get('out_ext') or 'mp4').lstrip('.') or 'mp4'
    if params.get('save_mode') == 'custom' and (params.get('save_dir') or '').strip():
        out_dir = Path(items[0].render(params['save_dir']))
    else:
        out_dir = Path(items[0].path).parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise StepError(f"无法创建输出文件夹 {out_dir}: {e}")
    sub_mode = params.get('subtitle_mode') or 'none'

    # 逐段探测 + 收集每段独立剪辑与字幕
    segments: List[Dict[str, Any]] = []
    for idx, it in enumerate(items, 1):
        seg_src = Path(it.path)
        if not seg_src.exists():
            raise StepError(f"拼接源文件不存在: {seg_src}")
        ctx.log(f"[拼接] 探测第 {idx}/{len(items)} 段: {seg_src.name}")
        info = tc.probe(str(seg_src))
        dur = float(getattr(info, 'duration', 0) or 0)
        start, end = _resolve_trim(it, params, dur)
        if end <= start:
            end = dur if dur > start else start + 0.04
        srt = ''
        if sub_mode != 'none':
            if it.subtitle and os.path.isfile(it.subtitle):
                srt = it.subtitle
            else:
                cand = seg_src.with_suffix('.srt')
                if cand.exists():
                    srt = str(cand)
            if not srt:
                ctx.log(f"[拼接] {seg_src.name}: 未找到字幕，按无字幕段处理")
        segments.append({
            "src": str(seg_src), "start": start, "end": end,
            "src_w": int(getattr(info, 'width', 0) or 0),
            "src_h": int(getattr(info, 'height', 0) or 0),
            "fps": float(getattr(info, 'fps', 0) or 0),
            "srt": srt,
        })

    if params.get('keep_quality'):
        ctx.log("[拼接] “保留原画质”与拼接互斥——拼接要求各段编码规格一致，"
                "已按统一参数重新编码")
    # 码率：拼接各段共用一套参数；勾选“不修改码率”时取首段原片码率统一使用
    bitrate = int(params.get('bitrate') or 2500)
    if params.get('keep_bitrate', False):
        try:
            src_br = tc._p._probe_stream_bitrate_kbps(segments[0]["src"])
            if src_br >= 100:
                bitrate = int(src_br)
                ctx.log(f"[拼接] 不修改码率：按首段原片码率 {bitrate} kbps 统一编码")
        except Exception:
            pass

    merge_name = f"{Path(segments[0]['src']).stem}_合并"
    out = unique_dst(out_dir / f"{merge_name}.{ext}")
    task = tc.TranscodeTask(
        src=segments[0]["src"], start=segments[0]["start"], end=segments[0]["end"],
        src_w=segments[0]["src_w"], src_h=segments[0]["src_h"], fps=segments[0]["fps"],
        resolution=params.get('resolution') or '1280x720',
        aspect=params.get('aspect') or '保持原比例',
        bitrate=bitrate,
        save_mode='custom', save_dir=str(out_dir),
        out_ext=ext, subtitle_mode=('soft' if sub_mode != 'none' else 'none'),
        subtitle=segments[0]["srt"],
        keep_quality=False, keep_bitrate=False,
        merge=True, merge_name=merge_name, segments=segments,
        auto_orient=bool(params.get('auto_orient', True)),
        portrait_res=params.get('portrait_res') or '自动(按源比例)')
    task.out_path = str(out)
    task._encoder_label = params.get('encoder') or ENCODER_LABELS['auto']  # noqa: SLF001

    ctx.log(f"[拼接] 共 {len(segments)} 段 → {out.name}"
            f"（总时长约 {sum(s['end'] - s['start'] for s in segments):.1f}s）")
    res_lock = getattr(ctx, 'resource_lock', None)
    if res_lock is not None:
        res_lock.acquire()
    try:
        result = tc.run(task, is_stopped=ctx.stopped,
                        on_progress=lambda pct: ctx.progress(pct, "拼接转码"))
    finally:
        if res_lock is not None:
            res_lock.release()
    if result.get('status') != 'success':
        if result.get('status') == 'skipped':
            raise StepStopped("已停止")
        raise StepError(result.get('detail') or "拼接转码失败")
    produced = Path(result.get('out') or out)
    if not produced.exists():
        raise StepError(f"拼接输出文件不存在: {produced}")

    # 可选清理：删除各段源文件 / 已封装进成片的原始字幕
    if params.get('del_src'):
        for s in segments:
            p = Path(s['src'])
            if os.path.normcase(os.path.abspath(str(p))) == \
                    os.path.normcase(os.path.abspath(str(produced))):
                continue
            try:
                if p.exists():
                    os.remove(str(p))
                    ctx.log(f"[已删除源文件] {p.name}")
            except Exception as e:
                ctx.log(f"[警告] 删除源文件失败 {p.name}: {e}")
    if params.get('del_srt') and sub_mode != 'none':
        for s in segments:
            srt = s.get('srt') or ''
            if srt and os.path.isfile(srt):
                try:
                    os.remove(srt)
                    ctx.log(f"[已删除字幕] {os.path.basename(srt)}")
                except Exception as e:
                    ctx.log(f"[警告] 删除字幕失败 {srt}: {e}")

    ctx.log(f"[拼接] 完成：{len(segments)} 段已合并为 {produced.name}")
    return str(produced)


# ==================================================================
# 字幕模型协调器：运行期内共享 / 复用语音与翻译模型，避免每条视频重复加载
# ==================================================================
class SubtitleCoordinator:
    """工作流运行期共享字幕模型（语音 / 翻译）协调器。

    - 同一次运行只加载一次，跨视频 / 跨任务复用，省去每条视频重复加载大模型的时间；
    - 用锁串行化模型使用（torch / llama_cpp 非线程安全，且单模型更省显存）；
    - 当剩余工作不再需要某模型时，立即释放其显存（由执行器通过 on_item_done 判定）。
    """

    def __init__(self, on_item_done: Optional[Callable], log: Optional[Callable[[str], None]],
                 resource_lock: Optional[threading.Lock] = None):
        self._gen = None
        self._gen_key = None
        # 默认自带锁；若执行器传入共享的“重资源锁”，则与之共用，从而与转码等重资源操作互斥排队
        self._lock = resource_lock or threading.Lock()
        self._on_item_done = on_item_done
        self._log = log or (lambda m: None)
        self._released_asr = False
        self._released_translate = False

    @staticmethod
    def _build_key(kw: Dict[str, Any]) -> tuple:
        return tuple(sorted((k, str(v)) for k, v in kw.items()))

    def _ensure_generator(self, kw: Dict[str, Any]):
        from subtitle.core import SubtitleGenerator          # noqa: WPS433
        key = self._build_key(kw)
        if self._gen is None or self._gen_key != key:
            if self._gen is not None:
                try:
                    self._gen.release_resources()
                except Exception:
                    pass
            self._gen = SubtitleGenerator(**kw)
            self._gen_key = key
        return self._gen

    def use(self, uid, needs_asr, needs_translate, build_kwargs):
        """进入模型使用区：获取（或重建）共享生成器并持锁；退出时扣减计数、按需释放显存"""
        self._lock.acquire()
        gen = self._ensure_generator(build_kwargs)
        return _SubtitleUse(self, gen, uid, needs_asr, needs_translate)

    def notify_done(self, uid, needs_asr, needs_translate):
        """未实际使用模型（如跳过已存在 srt）时，仅扣减计数"""
        if self._on_item_done:
            try:
                self._on_item_done(uid, needs_asr, needs_translate, None)
            except Exception:
                pass

    def finalize(self):
        """运行结束：释放仍被持有的生成器，确保显存回收"""
        if self._gen is not None:
            try:
                self._gen.release_resources()
            except Exception:
                pass
            self._gen = None
            self._gen_key = None


class _SubtitleUse:
    def __init__(self, coord, gen, uid, needs_asr, needs_translate):
        self._coord = coord
        self._gen = gen
        self._uid = uid
        self._needs_asr = needs_asr
        self._needs_translate = needs_translate

    def __enter__(self):
        return self._gen

    def __exit__(self, *exc):
        try:
            if self._coord._on_item_done:
                self._coord._on_item_done(self._uid, self._needs_asr,
                                          self._needs_translate, self._gen)
        finally:
            self._coord._lock.release()


# ==================================================================
# 步骤三：生成字幕
# ==================================================================
def run_subtitle(item: WorkflowItem, params: Dict[str, Any], ctx: StepContext) -> str:
    from subtitle.config import load_config                      # noqa: WPS433
    from subtitle.core import SubtitleGenerator                  # noqa: WPS433
    from subtitle.system_utils import (                          # noqa: WPS433
        resolve_model_path, resolve_translator_path, resolve_sakura_path,
        resolve_kotoba_path, resolve_nemo_path,
        check_cuda_available, check_cublas, check_cudnn, find_ffmpeg,
    )

    src = Path(item.path)
    if not src.exists():
        raise StepError(f"源文件不存在: {src}")

    cfg = load_config()
    ff_ok, ff_path, _ = find_ffmpeg(cfg)
    if not ff_ok:
        raise StepError("未找到 ffmpeg，无法提取音频")

    out_srt = src.with_suffix('.srt')

    asr_engine = params.get('asr_engine') or 'kotoba-whisper'
    translator_engine = params.get('translator_engine') or 'NLLB-200'

    model_path = kotoba_path = nemo_path = None
    if asr_engine == 'faster-whisper':
        model_path = resolve_model_path(params.get('model_size') or 'medium', cfg)
        if not model_path:
            raise StepError("未找到 faster-whisper 模型，请在字幕设置中指定模型路径")
    elif asr_engine == 'kotoba-whisper':
        kotoba_path = resolve_kotoba_path(cfg)
        if not kotoba_path:
            raise StepError("未找到 kotoba-whisper 模型，请在字幕设置中指定模型路径")
    else:
        nemo_path = resolve_nemo_path(cfg)
        if not nemo_path:
            raise StepError("未找到 ReazonSpeech NeMo 模型，请在字幕设置中指定")

    translator_path = sakura_path = None
    if translator_engine.startswith('Sakura'):
        sakura_path = resolve_sakura_path(cfg, engine=translator_engine)
        if not sakura_path:
            raise StepError(f"未找到 {translator_engine} 翻译模型，请在字幕设置中指定")
    else:
        translator_path = resolve_translator_path(cfg)
        if not translator_path:
            raise StepError("未找到 NLLB-200 翻译模型，请在字幕设置中指定")

    # GPU 不可用时自动降级 CPU（不弹窗，保证无人值守）
    device = params.get('device') or 'cuda'
    if device == 'cuda':
        cuda_ok, cuda_msg = check_cuda_available()
        cublas_ok, _ = check_cublas()
        cudnn_ok, _ = check_cudnn()
        if not (cuda_ok and cublas_ok and cudnn_ok):
            device = 'cpu'
            ctx.log(f"[提示] CUDA/GPU 不可用，本次改用 CPU 处理（{cuda_msg}）")

    ctx.log(f"[字幕] {src.name}: 语音={asr_engine} "
            f"{('/' + str(params.get('model_size'))) if model_path else ''}"
            f" 翻译={translator_engine} 设备={device}")

    # 本次字幕步骤所需模型：语音识别恒需要；翻译取决于是否双语（bilingual）
    build_kwargs = dict(
        model_path=model_path, device=device,
        compute_type=params.get('compute_type') or 'int8',
        translator_path=translator_path, asr_engine=asr_engine,
        translator_engine=translator_engine, kotoba_path=kotoba_path,
        nemo_path=nemo_path, sakura_path=sakura_path,
    )
    needs_asr = True
    needs_translate = bool(params.get('bilingual', True))

    coord = getattr(ctx, 'subtitle_coordinator', None)
    # 已存在同名 srt 且开启“跳过”时，不加载模型，仅通知协调器扣减计数
    if params.get('skip_existing') and out_srt.exists():
        if coord is not None:
            coord.notify_done(item.uid, needs_asr, needs_translate)
        ctx.log(f"[字幕] 已存在同名 srt，跳过: {out_srt.name}")
        item.subtitle = str(out_srt)
        return item.path

    # 运行期共享模型：同一次工作流只加载一次，跨视频 / 任务复用
    use_ctx = coord.use(item.uid, needs_asr, needs_translate, build_kwargs) if coord is not None else None
    if use_ctx is None:
        gen = SubtitleGenerator(**build_kwargs)
    try:
        def _progress(p, stage=""):
            ctx.progress(float(p), f"{src.name} {stage}")

        _TRANS_RE = re.compile(r'正在翻译第\s*(\d+)\s*/\s*(\d+)\s*条字幕')

        def _status(m):
            m = str(m)
            mm = _TRANS_RE.search(m)
            if mm:
                # 逐条翻译进度不写日志（量大刷屏），改为显示在视频清单“状态”列
                item.status = f"翻译 {mm.group(1)}/{mm.group(2)}"
                ctx.notify_item()
                return
            ctx.log(f"  · {m}")

        if use_ctx is not None:
            with use_ctx as gen:
                result = gen.process_video(
                    str(src), output_srt=str(out_srt),
                    bilingual=needs_translate,
                    progress_callback=_progress,
                    status_callback=_status,
                    pause_callback=ctx.check_pause,   # 暂停时，逐条翻译循环在此协作式阻塞
                )
        else:
            result = gen.process_video(
                str(src), output_srt=str(out_srt),
                bilingual=needs_translate,
                progress_callback=_progress,
                status_callback=_status,
                pause_callback=ctx.check_pause,
            )
    finally:
        # 仅非协调器路径（如独立测试）才在此释放；协调器统一在运行结束时释放
        if use_ctx is None:
            try:
                gen.release_resources()
            except Exception:
                pass

    if ctx.stopped():
        raise StepStopped("已停止")
    if not result or not out_srt.exists():
        raise StepError("字幕生成失败（未产生 srt 文件）")

    item.status = "进行中"   # 复位逐条翻译期间显示的“翻译 N/N”
    ctx.log(f"[字幕完成] {out_srt.name}")
    item.subtitle = str(out_srt)
    return item.path


# ==================================================================
# 步骤四：修改名称（就地改名）
# ==================================================================
_ILLEGAL_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _sanitize_filename(name: str) -> str:
    """清理 Windows 下非法的文件名字符（\\ / : * ? " < > | 及换行/制表）"""
    cleaned = _ILLEGAL_NAME.sub('_', name).strip()
    return cleaned or "file"


def run_rename(item: WorkflowItem, params: Dict[str, Any], ctx: StepContext) -> str:
    """修改名称：把当前文件（item.path）在原所在文件夹就地改名。

    - 模板变量同移动/转码（{uid}/{stem}/{src_stem}/{ext}/{name} 等），详见
      ``WorkflowItem.template_vars``；模板若已含视频扩展名则沿用，否则沿用当前文件扩展名。
    - 可选同步改名同名附属文件（字幕/封面等），保持后续阶段可正确匹配。
    - 改名后返回新路径，执行器据此推进 item.current（V1→V2→…）。
    """
    src = Path(item.path)
    if not src.exists():
        raise StepError(f"源文件不存在，无法改名: {src}")

    tpl = (params.get('name_template') or '{stem}').strip() or '{stem}'
    rendered = item.render(tpl).strip()
    if not rendered:
        rendered = src.stem
    _, cur_ext = os.path.splitext(src.name)
    # 模板已带视频扩展名则沿用，否则补上当前文件扩展名
    if os.path.splitext(rendered)[1].lower() in VIDEO_EXTENSIONS:
        new_name = rendered
    else:
        new_name = rendered + cur_ext
    new_name = _sanitize_filename(new_name)

    dest = src.parent / new_name
    # 改名不得覆盖原始视频文件（多任务“转码到原文件夹 + 改名”的组合意图是生成新
    # 文件而非覆盖原片）：若目标名与原始视频文件同名，自动加数字后缀，而不是替换原片。
    origin = Path(item.origin) if item.origin else None
    if (origin and os.path.normcase(os.path.abspath(str(dest))) !=
            os.path.normcase(os.path.abspath(str(src))) and
            os.path.normcase(os.path.abspath(str(dest))) ==
            os.path.normcase(os.path.abspath(str(origin)))):
        dest = unique_dst(dest)
        ctx.log(f"[改名] 目标名与原始视频文件同名，已自动加序号：{dest.name}")
    elif os.path.normcase(os.path.abspath(str(dest))) == os.path.normcase(os.path.abspath(str(src))):
        ctx.log(f"[改名] {src.name}：模板渲染后名称未变化，跳过")
        return str(src)
    if dest.exists():
        dest = unique_dst(dest)
        ctx.log(f"[改名] 目标名冲突，已自动加序号：{dest.name}")

    try:
        os.replace(str(src), str(dest))
    except Exception:
        # 跨盘等情况 os.replace 不支持，退回 shutil.move
        shutil.move(str(src), str(dest))
    ctx.log(f"[改名] {src.name} → {dest.name}")

    # 同名附属文件一并改名（字幕/封面等），避免后续阶段匹配不到
    if params.get('with_companions', True):
        exts = [e.strip().lower() for e in
                str(params.get('companion_exts')
                    or ".srt,.ass,.ssa,.jpg,.jpeg,.png,.webp,.nfo,.json").split(',')
                if e.strip()]
        for ext in exts:
            side = src.with_suffix(ext)
            if not side.exists() or side == src:
                continue
            new_side = dest.with_suffix(ext)
            if new_side.exists():
                new_side = unique_dst(new_side)
            try:
                os.replace(str(side), str(new_side))
                ctx.log(f"[改名附属] {side.name} → {new_side.name}")
                if ext == '.srt':
                    item.subtitle = str(new_side)
            except Exception as e:
                ctx.log(f"[警告] 附属文件改名失败 {side.name}: {e}")
    return str(dest)


# ==================================================================
# 注册表
# ==================================================================
STEP_HANDLERS = {
    STEP_MOVE: run_move,
    STEP_TRANSCODE: run_transcode,
    STEP_SUBTITLE: run_subtitle,
    STEP_RENAME: run_rename,
}
