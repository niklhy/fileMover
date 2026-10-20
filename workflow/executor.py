#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流串联 —— 无人值守执行引擎

按「条目 → 步骤顺序」两级循环推进：

    for 每个视频条目（UID 绑定）:
        for 流程中的每个步骤（可在界面上调整顺序）:
            调用对应 handler，成功则把 item.current 推进到新文件（V1→V2→V3）

全过程不弹任何对话框，失败只写日志，保证可以整夜无人值守跑完。
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from .models import WorkflowItem, WorkflowJob, STEP_SUBTITLE, STEP_TRANSCODE, STEP_RENAME
from . import steps as step_mod
from .steps import (StepContext, StepError, StepMergedSkip, StepStopped,
                    SubtitleCoordinator)


class WorkflowExecutor(threading.Thread):
    """工作流执行线程"""

    def __init__(self, job: WorkflowJob, items: List[WorkflowItem],
                 on_log: Optional[Callable[[str], None]] = None,
                 on_item: Optional[Callable[[WorkflowItem], None]] = None,
                 on_progress: Optional[Callable[[float, str], None]] = None,
                 on_finish: Optional[Callable[[Dict[str, Any]], None]] = None,
                 handlers: Optional[Dict[str, Callable]] = None,
                 ffmpeg_path: str = "",
                 tasks: Optional[List] = None,
                 overall_finish_action: Optional[str] = None,
                 resolve_finish_action: Optional[Callable[[], str]] = None,
                 max_workers: int = 1):
        """``tasks`` 为 [(WorkflowJob, [WorkflowItem]), ...] 的有序列表；
        不传时由 job+items 退化为单个任务（向后兼容）。

        ``overall_finish_action`` 是全部任务结束后的统一动作（关机/睡眠/无操作）；
        单个任务自身的 finish_action 在作为任务清单一员时一律视为 'none'，
        避免每个任务结束都触发一次关机/睡眠。

        ``resolve_finish_action`` 为可选回调：若存在，则在全部完成时才调用它读取
        最终的“完成后操作”（支持运行途中随时修改，以完成前一刻的值为准）；
        不传时退化为启动时刻快照 ``overall_finish_action``。
        """
        super().__init__(daemon=True)
        self.job = job
        self.items = list(items)
        if tasks is None:
            tasks = [(job, list(items))]
        self.tasks = list(tasks)
        self.overall_finish_action = (
            overall_finish_action if overall_finish_action is not None
            else (job.finish_action if job else "none"))
        # 运行时实时解析（优先）；UI 层在启动与每次下拉框变更时刷新持有器，
        # executor 在全部完成的瞬间才读取该值——故运行中改“无操作”即不再关机/睡眠
        self._resolve_finish_action = resolve_finish_action
        self.on_log = on_log or (lambda m: None)
        self.on_item = on_item or (lambda it: None)
        self.on_progress = on_progress or (lambda p, t="": None)
        self.on_finish = on_finish or (lambda stat: None)
        self.handlers = dict(handlers or step_mod.STEP_HANDLERS)
        self.ffmpeg_path = ffmpeg_path
        # 并行线程数：1 表示串行（与旧行为完全一致）；>1 时多个 worker 同时处理
        # 不同视频/不同任务（每个 worker 内部仍按步骤顺序处理自己那条视频）。
        self._max_workers = max(1, int(max_workers)) if max_workers else 1
        # 重资源锁：转码（NVENC/CUDA 全显存流水线）、语音识别、翻译等吃显存/硬件编码器的
        # 操作共享同一把锁，保证多线程下任意时刻只有一个重资源操作在跑，避免多个视频同时
        # 做转码/识别/翻译抢占显存与硬件编码器导致崩溃或爆显存。文件类步骤（移动/改名）不抢此锁。
        self._heavy_lock = threading.Lock()

        self._stop_event = threading.Event()
        self.stats: Dict[str, Any] = {"total": 0, "done": 0, "failed": 0, "stopped": 0}
        self._paused = False
        # 跨 worker 的共享状态（需加锁）
        self._lock = threading.Lock()                       # 保护 stats / _stop_jobs
        self._prog_lock = threading.Lock()                 # 保护进度聚合
        self._ctx_lock = threading.Lock()                  # 保护 _active_ctxs
        self._active_ctxs: set = set()                     # 当前所有正在执行的步骤上下文
        self._pause_event = threading.Event()              # 门闸：暂停时新条目在此等待
        self._stop_jobs: set = set()                       # 出错即停的任务（同任务剩余条目跳过）
        self._item_progress: Dict[int, float] = {}         # id(item) -> 当前进度（0~100）
        self._total_items = 0

    # ---------------- 控制 ----------------
    def stop(self):
        self._stop_event.set()
        # 若处于暂停（子进程被冻结），需先标记停止并解除冻结、终止所有活动子进程，
        # 否则 worker 线程会一直卡在冻结的 stdout / 协作式等待上无法退出；
        # 同时唤醒门闸处等待的 worker，使其检出停止信号后退出。
        with self._ctx_lock:
            ctxs = list(self._active_ctxs)
        for ctx in ctxs:
            try:
                if ctx.paused:
                    ctx.mark_stopped()
                    ctx.resume()
                    ctx.terminate_active_proc()
            except Exception:
                pass
        self._pause_event.clear()
        self.log("[停止] 正在请求停止，当前步骤完成后退出…")

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def pause(self):
        """暂停所有并行任务：冻结正在进行的转码/剪辑（挂起 ffmpeg 进程）或字幕逐条翻译，
        恢复后从当前位置继续，不影响任何已生成的转码/字幕结果。"""
        if self._paused:
            return
        self._paused = True
        self._pause_event.set()          # 新开始的条目在门闸处等待
        with self._ctx_lock:
            ctxs = list(self._active_ctxs)
        for ctx in ctxs:
            try:
                ctx.pause()
            except Exception:
                pass
        self.log("[暂停] 已请求暂停，所有并行任务将在当前位置冻结…")

    def resume(self):
        """从暂停处恢复执行"""
        if not self._paused:
            return
        self._paused = False
        with self._ctx_lock:
            ctxs = list(self._active_ctxs)
        for ctx in ctxs:
            try:
                ctx.resume()
            except Exception:
                pass
        self._pause_event.clear()        # 唤醒门闸处等待的 worker
        self.log("[恢复] 已恢复执行")

    @property
    def paused(self) -> bool:
        return self._paused

    def log(self, msg: str):
        try:
            self.on_log(str(msg))
        except Exception:
            pass

    def _emit_item(self, item: WorkflowItem, force: bool = False):
        """通知界面刷新该条目；节流 0.3s，防止高频状态变化（如逐条翻译进度）刷屏"""
        now = time.monotonic()
        if not force and (now - getattr(self, '_last_emit_ts', 0.0)) < 0.3:
            return
        self._last_emit_ts = now
        try:
            self.on_item(item)
        except Exception:
            pass

    # ---------------- 主循环（工作池：条目级并行） ----------------
    def run(self):
        tasks = [(j, list(its)) for j, its in self.tasks]
        self._total_items = sum(len(its) for _, its in tasks)
        self._item_progress = {id(it): 0.0 for _, its in tasks for it in its}
        self._active_ctxs = set()
        self._pause_event = threading.Event()
        self._stop_jobs = set()
        self.stats = {"total": self._total_items, "done": 0, "failed": 0, "stopped": 0}
        if self._total_items == 0:
            self.log("[错误] 任务清单中没有可处理的视频")
            self._done(finish_action=False)
            return

        started = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log("=" * 70)
        self.log(f"开始多任务工作流  {started}")
        self.log(f"任务数：{len(tasks)}，视频总数：{self._total_items}，"
                 f"并行线程数：{self._max_workers}")
        self.log("=" * 70)

        # 字幕模型复用：先分析任务清单，统计运行期内仍需语音 / 翻译模型的条目，
        # 使同一次运行只加载一次，并在后续不再需要时立即释放显存
        pending_asr = set()
        pending_translate = set()
        for job, items in tasks:
            for it in items:
                for st in job.enabled_steps():
                    if st.type == STEP_SUBTITLE:
                        pending_asr.add(it.uid)
                        if bool(st.params.get('bilingual', True)):
                            pending_translate.add(it.uid)
                        break
        self._pending_asr = pending_asr
        self._pending_translate = pending_translate
        self._released_asr = False
        self._released_translate = False
        self._coordinator = SubtitleCoordinator(on_item_done=self._subtitle_item_done,
                                                log=self.log, resource_lock=self._heavy_lock)

        q = queue.Queue()
        for t_idx, (job, items) in enumerate(tasks):
            steps = job.enabled_steps()
            self.log("\n" + "#" * 70)
            self.log(f"# 任务 {t_idx + 1}/{len(tasks)}：{job.name or '（未命名）'}")
            self.log(f"# 流程：{job.describe() or '（空流程）'}    视频数：{len(items)}")
            self.log("#" * 70)
            if not steps:
                self.log("[跳过] 该任务没有启用任何步骤，标记失败")
                for it in items:
                    it.status = "失败"
                    it.message = "未启用任何步骤"
                    with self._lock:
                        self.stats["failed"] += 1
                    self._set_item_progress(it, 100.0)
                    self._emit_item(it, force=True)
                continue
            for it in items:
                q.put((job, it, items))

        workers = []
        for _ in range(self._max_workers):
            t = threading.Thread(target=self._worker_loop, args=(q,), daemon=True)
            workers.append(t)
            t.start()
        for t in workers:
            t.join()

        # 停止时，队列中尚未开始处理的视频标记为“已停止”
        if self.stopped:
            while True:
                try:
                    job, item, _titems = q.get_nowait()
                except queue.Empty:
                    break
                item.status = "已停止"
                item.message = "用户停止"
                with self._lock:
                    self.stats["stopped"] += 1
                self._set_item_progress(item, 100.0)
                self._emit_item(item, force=True)

        # 运行结束统一释放仍被持有的字幕模型，确保显存回收
        try:
            self._coordinator.finalize()
        except Exception:
            pass

        self.on_progress(100.0, "全部结束")
        self._done(finish_action=not self.stopped)

    def _worker_loop(self, q):
        while True:
            if self.stopped:
                break
            try:
                job, item, task_items = q.get_nowait()
            except queue.Empty:
                break
            # 同任务前序失败且未勾选“出错时继续” => 该任务剩余条目跳过
            with self._lock:
                job_stopped = id(job) in self._stop_jobs
            if job_stopped:
                item.status = "已停止"
                item.message = "因同任务前序失败而跳过"
                with self._lock:
                    self.stats["stopped"] += 1
                self._set_item_progress(item, 100.0)
                self._emit_item(item, force=True)
                try:
                    q.task_done()
                except Exception:
                    pass
                continue
            try:
                self._process_item(job, item, task_items=task_items)
            finally:
                try:
                    q.task_done()
                except Exception:
                    pass

    def _process_item(self, job, item, task_items: Optional[List[WorkflowItem]] = None):
        # 已完成条目直接计入（与旧逻辑一致：跳过已完成）
        if item.status == "已完成":
            self.log(f"[跳过] {item.uid}: 已完成")
            with self._lock:
                self.stats["done"] += 1
            self._set_item_progress(item, 100.0)
            self._emit_item(item, force=True)
            return

        item.status = "进行中"
        item.message = ""
        self._emit_item(item, force=True)
        self.log(f"\n--- UID={item.uid}  {item.name} ---")
        self.log(f"    起点: {item.origin}")

        ctx = StepContext(
            log=self.log,
            progress=lambda pct, text="", _it=item: self._set_item_progress(_it, pct),
            is_stopped=lambda: self.stopped,   # 必须是可调用对象，才能实时感知停止
            ffmpeg_path=self.ffmpeg_path,
            notify=lambda _item=item: self._emit_item(_item),
        )
        ctx.subtitle_coordinator = self._coordinator
        ctx.resource_lock = self._heavy_lock
        # 拼接任务：把本任务内的全部条目交给步骤（转码步骤据此把多个视频合并为一个成片）
        ctx.task_items = list(task_items) if task_items else [item]
        with self._ctx_lock:
            self._active_ctxs.add(ctx)

        # 门闸：全局暂停时，新开始的条目在此阻塞（此时 ctx 已在活动集合中，pause()
        # 会对其调用 ctx.pause()），直到恢复后才创建子进程。
        if self._paused:
            self._pause_event.wait()
        if self.stopped:
            item.status = "已停止"
            item.message = "用户停止"
            with self._lock:
                self.stats["stopped"] += 1
            self._set_item_progress(item, 100.0)
            self._emit_item(item, force=True)
            with self._ctx_lock:
                self._active_ctxs.discard(ctx)
            return

        failed = False
        steps = job.enabled_steps()
        for si, step in enumerate(steps):
            if self.stopped:
                failed = True
                break
            handler = self.handlers.get(step.type)
            if handler is None:
                self.log(f"[跳过] 未知步骤类型: {step.type}")
                continue

            item.stage = f"{si + 1}/{len(steps)} {step.label}"
            self._emit_item(item)
            self.log(f"  ▶ 步骤 {si + 1}/{len(steps)}：{step.label}（{step.summary()}）")
            ctx.progress(0, step.label)

            # 转码输出到原文件夹且后续跟“修改名称”步骤：标记非原地替换，
            # 避免转码删除/覆盖原始视频（最终命名交给改名步骤）。组合意图是
            # “生成新文件而非覆盖原片”，见多任务模块需求。
            if step.type == STEP_TRANSCODE:
                rename_follows = any(s.type == STEP_RENAME for s in steps[si + 1:])
                ctx.rename_after_transcode = bool(
                    rename_follows and step.params.get('save_mode') != 'custom')
            else:
                ctx.rename_after_transcode = False

            before = item.path
            try:
                new_path = handler(item, step.params, ctx)
            except StepMergedSkip:
                # 拼接任务的非首段视频：已并入首段统一拼接，直接视为完成
                self.log("  ✔ 已并入拼接视频（由本任务第一个视频统一拼接输出）")
                break
            except StepStopped:
                self.log("  ⏹ 已停止")
                failed = True
                break
            except StepError as e:
                item.status = "失败"
                item.message = str(e)
                failed = True
                self.log(f"  ✖ 失败: {e}")
                break
            except Exception as e:
                item.status = "失败"
                item.message = f"{type(e).__name__}: {e}"
                failed = True
                self.log(f"  ✖ 异常: {type(e).__name__}: {e}")
                self.log(traceback.format_exc(limit=3))
                break

            if new_path and os.path.normcase(os.path.abspath(str(new_path))) != \
                    os.path.normcase(os.path.abspath(str(before))):
                item.current = str(new_path)
                item.record(step.label, str(before), str(new_path), True)
                self.log(f"  ✔ 完成 → {os.path.basename(str(new_path))}  "
                         f"(V{len(item.history) + 1}: {new_path})")
            else:
                item.record(step.label, str(before), str(before), True, "路径未变化")
                self.log(f"  ✔ 完成（当前文件: {item.name}）")
            self._emit_item(item, force=True)

        with self._ctx_lock:
            self._active_ctxs.discard(ctx)
        self._set_item_progress(item, 100.0)

        if not failed:
            item.status = "已完成"
            item.stage = "全部步骤完成"
            item.message = f"最终文件: {item.name}"
            with self._lock:
                self.stats["done"] += 1
        elif item.status == "失败":
            with self._lock:
                self.stats["failed"] += 1
            if not job.continue_on_error:
                self.log(f"[中断] {item.uid} 失败，已停止本任务剩余条目"
                         f"（如需继续请勾选“出错时继续下一个”）")
                with self._lock:
                    self._stop_jobs.add(id(job))
                self._emit_item(item, force=True)
                return
        else:
            # failed 但状态未置为“失败” => 是被停止信号打断
            item.status = "已停止"
            item.message = "用户停止"
            with self._lock:
                self.stats["stopped"] += 1
        self._emit_item(item, force=True)

    def _subtitle_item_done(self, uid, needs_asr, needs_translate, gen):
        """字幕步骤结束（或跳过）时由协调器回调：扣减剩余需求，不再需要时立即释放显存"""
        with self._ctx_lock:
            if needs_asr:
                self._pending_asr.discard(uid)
            if needs_translate:
                self._pending_translate.discard(uid)
            if needs_asr and not self._pending_asr and not self._released_asr:
                self._released_asr = True
                if gen is not None:
                    try:
                        gen.release_asr()
                    except Exception:
                        pass
                self.log("[字幕] 后续任务均不再需要语音模型，已释放其显存")
            if needs_translate and not self._pending_translate and not self._released_translate:
                self._released_translate = True
                if gen is not None:
                    try:
                        gen.release_translator()
                    except Exception:
                        pass
                self.log("[字幕] 后续任务均不再需要翻译模型，已释放其显存")

    def _set_item_progress(self, item, pct):
        # 进度聚合：每个条目的 pct 为 0~100 百分比，整体进度 = 各条目进度的平均值
        # （同样为 0~100，直接上报；此前再乘 100 导致进度条提前虚标 100%，
        #   与日志的实际执行阶段严重不符）
        with self._prog_lock:
            # 以 id(item) 为键：不同任务里同番号（uid 相同）的视频互不覆盖
            self._item_progress[id(item)] = max(0.0, min(100.0, float(pct)))
            total = max(1, self._total_items)
            overall = sum(self._item_progress.values()) / total
        self.on_progress(overall, "")

    # ---------------- 收尾 ----------------

    def _done(self, finish_action: bool):
        s = self.stats
        self.log("=" * 70)
        self.log(f"工作流结束：共 {s['total']} 条，完成 {s['done']}，"
                 f"失败 {s['failed']}，停止 {s['stopped']}")
        for item in self.items:
            if item.status == "失败":
                self.log(f"  · 失败 {item.uid}: {item.message}")
        self.log("=" * 70)

        # 释放执行过程中可能遗留的文件句柄（探测/转码/字幕阶段的临时对象），
        # 确保任务结束后视频文件不再被 Python 占用，资源管理器可正常移动/删除
        try:
            import gc
            gc.collect()
        except Exception:
            pass

        if finish_action:
            # 全部完成时才解析最终的“完成后操作”：优先用运行时实时持有器，
            # 退回启动快照——保证运行中修改（如 sleep→none）以完成前一刻为准
            action = "none"
            try:
                if self._resolve_finish_action is not None:
                    action = self._resolve_finish_action() or "none"
                else:
                    action = self.overall_finish_action or "none"
            except Exception:
                action = "none"
            if action and str(action).lower() not in ("none", "", "null"):
                self._do_finish(str(action))
        try:
            self.on_finish(dict(s))
        except Exception:
            pass

    def _do_finish(self, action: str):
        """完成后操作（与 file_move_tab 一致：不弹窗，只写日志）"""
        label = "关机" if action == "shutdown" else "进入睡眠"
        self.log(f"[完成后操作] 工作流已完成，即将{label}")
        try:
            if action == "shutdown":
                os.system("shutdown /s /f /t 30")
            elif action == "sleep":
                from system_power import enter_sleep
                if not enter_sleep():
                    self.log("[完成后操作] 发起睡眠失败（可能被系统/驱动阻止）。"
                             "可检查：①快速启动是否开启；②网卡/鼠标/键盘的“允许此设备唤醒"
                             "计算机”；③计划任务唤醒定时器（powercfg /waketimers，需管理员）")
        except Exception as e:
            self.log(f"[完成后操作] 执行{label}失败: {e}")
