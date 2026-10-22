#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流串联模块的自测脚本（端到端跑通整个业务流程）

运行：``python test_workflow.py``

覆盖：
  1. 唯一标识 UID 与变量模板
  2. 真实流程 A→(移动)B→(剪辑转码)→(移动)C→(生成字幕，用桩替代大模型)
  3. 另一条路径 A→(移动)B→(生成字幕)→(转码并打包软字幕)→(移动)C
  4. 步骤顺序调整、失败中断、停止信号

转码使用系统 ffmpeg 真实执行；字幕步骤用桩函数替代（避免依赖大模型文件）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:  # Windows 控制台默认 GBK，统一改为 UTF-8 输出
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workflow.models import (WorkflowJob, WorkflowStep, WorkflowItem,  # noqa: E402
                             STEP_MOVE, STEP_TRANSCODE, STEP_SUBTITLE, STEP_RENAME,
                             make_uid, build_items, scan_videos)
from workflow.executor import WorkflowExecutor                          # noqa: E402
from workflow.steps import StepContext, StepStopped                      # noqa: E402

FFMPEG = shutil.which('ffmpeg') or r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
PASS, FAIL = "✔", "✖"
_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append((name, bool(cond), detail))
    print(f"  {PASS if cond else FAIL} {name}" + (f"  —— {detail}" if detail else ""))
    return bool(cond)


def make_video(path: Path, seconds: int = 6) -> bool:
    """用 ffmpeg 生成一个带音频的测试视频"""
    cmd = [FFMPEG, '-y', '-nostdin', '-loglevel', 'error',
           '-f', 'lavfi', '-i', f'testsrc=size=320x240:rate=25',
           '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
           '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
           '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(path)]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode == 0 and path.exists()


def fake_subtitle(item: WorkflowItem, params: dict, ctx: StepContext) -> str:
    """字幕步骤的桩实现：直接写一个同名 srt，模拟大模型产出"""
    srt = Path(item.path).with_suffix('.srt')
    srt.write_text(
        "1\n00:00:00,500 --> 00:00:03,000\nこれはテスト字幕です\n\n"
        "2\n00:00:03,200 --> 00:00:05,000\n中文测试字幕\n\n",
        encoding='utf-8')
    item.subtitle = str(srt)
    ctx.log(f"[桩·字幕] 已生成 {srt.name}")
    return item.path


def run_job(job: WorkflowJob, items, log_lines, handlers=None, ffmpeg=FFMPEG):
    ex = WorkflowExecutor(job, items, on_log=log_lines.append,
                          handlers=handlers, ffmpeg_path=ffmpeg)
    ex.start()
    ex.join(timeout=600)
    return ex


# ======================================================================
def test_models():
    print("\n[1] 数据模型：UID / 模板 / 步骤编排")
    check("番号识别为 UID", make_uid("SSIS-001.mp4").upper().startswith("SSIS"),
          make_uid("SSIS-001.mp4"))
    check("FC2 番号识别", "FC2" in make_uid("FC2-PPV-123456.mp4"),
          make_uid("FC2-PPV-123456.mp4"))
    check("无番号时回退文件名", make_uid("我的视频.mkv") == "我的视频",
          make_uid("我的视频.mkv"))

    items = build_items(["/a/SSIS-001.mp4", "/b/SSIS-001.mp4", "/c/other.mp4"])
    check("重复 UID 自动去重", items[0].uid != items[1].uid,
          f"{items[0].uid} / {items[1].uid}")

    it = WorkflowItem(uid="SSIS-001", origin=r"D:\src\aaa.mp4", current=r"D:\out\aaa.mp4")
    check("{uid} 模板渲染", it.render(r"D:\dst\{uid}") == r"D:\dst\SSIS-001",
          it.render(r"D:\dst\{uid}"))
    check("{stem}/{ext} 模板渲染", it.render("{stem}_成片.{ext}") == "aaa_成片.mp4",
          it.render("{stem}_成片.{ext}"))
    check("{src_dir} 模板渲染", it.render("{src_dir}") == r"D:\src", it.render("{src_dir}"))

    job = WorkflowJob(steps=[WorkflowStep(STEP_MOVE), WorkflowStep(STEP_TRANSCODE)])
    job.steps.insert(2, WorkflowStep(STEP_SUBTITLE))
    check("步骤可调整顺序", job.describe().count("→") == 2, job.describe())
    check("步骤参数含默认值", job.steps[0].params.get('target_dir') is not None)
    # 序列化/反序列化
    job2 = WorkflowJob.from_dict(job.to_dict())
    check("配置可保存/读取", [s.type for s in job2.steps] == [s.type for s in job.steps])


# ======================================================================
def test_flow_a_move_transcode_move_subtitle():
    """场景一：A → 移动B → 剪辑转码 → 移动C → 生成字幕"""
    print("\n[2] 真实链路：A→B→剪辑转码→C→生成字幕")
    root = Path(tempfile.mkdtemp(prefix="wf_test_"))
    a, b, c = root / "A", root / "B", root / "C"
    for d in (a, b, c):
        d.mkdir()
    names = ["SSIS-001.mp4", "ABP-999.mp4"]
    made = True
    for n in names:
        made &= make_video(a / n)
    check("ffmpeg 生成测试视频", made, f"ffmpeg={FFMPEG}")
    if not made:
        shutil.rmtree(root, ignore_errors=True)
        return

    files = scan_videos(str(a), recursive=True, only_video=True)
    items = build_items(files)
    check("扫描到视频", len(items) == 2, str([i.name for i in items]))

    job = WorkflowJob(name="A→B→转码→C→字幕", source_dir=str(a))
    s_move_b = WorkflowStep(STEP_MOVE, params={"target_dir": str(b)})
    s_trim = WorkflowStep(STEP_TRANSCODE, params={
        "trim_mode": "head", "head": 1.0,
        "resolution": "320x240", "aspect": "保持原比例", "bitrate": 600,
        "encoder": "CPU（libx265 软编码）", "save_mode": "source",
        "out_ext": "mp4", "subtitle_mode": "none",
    })
    s_move_c = WorkflowStep(STEP_MOVE, params={"target_dir": str(c), "uid_subdir": True})
    s_sub = WorkflowStep(STEP_SUBTITLE, params={})
    job.steps = [s_move_b, s_trim, s_move_c, s_sub]

    logs: list = []
    handlers = {STEP_MOVE: None}  # 占位，下面用默认 + 字幕桩
    from workflow import steps as wf_steps
    handlers = dict(wf_steps.STEP_HANDLERS)
    handlers[STEP_SUBTITLE] = fake_subtitle

    ex = run_job(job, items, logs, handlers=handlers)
    time.sleep(0.2)

    for line in logs:
        print("      " + line)

    ok = True
    for it in items:
        ok &= check(f"{it.uid} 最终状态为已完成", it.status == "已完成",
                    f"{it.status} {it.message}")
        ok &= check(f"{it.uid} 落在 C 下的 UID 子目录",
                    it.path.startswith(str(c / it.uid)), it.path)
        ok &= check(f"{it.uid} 成片为转码后缀名（{it.uid}_转码.mp4，旧文件共存）",
                    Path(it.path).name == f"{it.uid}_转码.mp4", Path(it.path).name)
        ok &= check(f"{it.uid} 字幕已生成", bool(it.subtitle) and os.path.isfile(it.subtitle),
                    it.subtitle or "无")
        ok &= check(f"{it.uid} 记录了 4 个阶段轨迹", len(it.history) == 4,
                    " → ".join(h['to'].split(os.sep)[-1] for h in it.history))
        # 转码时长应为 5 秒（去掉片头 1 秒）
        dur = _probe_duration(it.path)
        ok &= check(f"{it.uid} 剪辑生效（约5秒，原6秒）", 3.5 < dur < 6.5, f"{dur:.2f}s")

    left_a = list(a.glob("*.mp4"))
    check("A 文件夹已清空（文件被移走）", len(left_a) == 0, str(left_a))
    shutil.rmtree(root, ignore_errors=True)
    return ok


def _probe_duration(path: str) -> float:
    try:
        import subprocess as sp
        fp = shutil.which('ffprobe') or r"C:\Program Files\ffmpeg\bin\ffprobe.exe"
        r = sp.run([fp, '-v', 'error', '-show_entries', 'format=duration',
                    '-of', 'csv=p=0', str(path)], stdout=sp.PIPE,
                   stderr=sp.DEVNULL, text=True, timeout=30)
        return float((r.stdout or '0').strip().split(',')[-1] or 0)
    except Exception:
        return -1.0


# ======================================================================
def test_flow_b_subtitle_then_transcode():
    """场景二：A→B→生成字幕→剪辑转码并打包软字幕→C"""
    print("\n[3] 真实链路：A→B→创建srt字幕→剪辑转码并打包软字幕→C")
    root = Path(tempfile.mkdtemp(prefix="wf_test2_"))
    a, b, c = root / "A", root / "B", root / "C"
    for d in (a, b, c):
        d.mkdir()
    if not make_video(a / "IPX-123.mp4", seconds=6):
        check("生成测试视频", False)
        shutil.rmtree(root, ignore_errors=True)
        return

    items = build_items([str(a / "IPX-123.mp4")])
    job = WorkflowJob(name="A→B→字幕→转码打包→C")
    job.steps = [
        WorkflowStep(STEP_MOVE, params={"target_dir": str(b)}),
        WorkflowStep(STEP_SUBTITLE, params={}),
        WorkflowStep(STEP_TRANSCODE, params={
            "trim_mode": "range", "start": 1.0, "end": 5.0,
            "resolution": "320x240", "bitrate": 600,
            "encoder": "CPU（libx265 软编码）",
            "save_mode": "custom", "save_dir": str(root / "TMPOUT"),
            "out_ext": "mkv",
            "subtitle_mode": "soft",
        }),
        WorkflowStep(STEP_MOVE, params={"target_dir": str(c)}),
    ]

    from workflow import steps as wf_steps
    handlers = dict(wf_steps.STEP_HANDLERS)
    handlers[STEP_SUBTITLE] = fake_subtitle

    logs: list = []
    ex = run_job(job, items, logs, handlers=handlers)
    for line in logs:
        print("      " + line)

    it = items[0]
    check("条目完成", it.status == "已完成", f"{it.status} {it.message}")
    check("成片落在 C", Path(it.path).parent == c, it.path)
    check("成片沿用原文件名（仅换扩展名）", Path(it.path).name == f"{it.uid}.mkv", Path(it.path).name)
    stream_ok, detail = _has_subtitle_stream(it.path)
    check("成片内已打包软字幕流", stream_ok, detail)
    # 字幕随转码平移：片段 1~5 秒，原字幕第 1 条 0.5~3.0 应被保留
    check("阶段轨迹完整", len(it.history) == 4,
          " → ".join(os.path.basename(h['to']) for h in it.history))
    shutil.rmtree(root, ignore_errors=True)


def _has_subtitle_stream(path: str):
    try:
        fp = shutil.which('ffprobe') or r"C:\Program Files\ffmpeg\bin\ffprobe.exe"
        r = subprocess.run([fp, '-v', 'error', '-show_entries', 'stream=index:stream=codec_type',
                            '-of', 'csv=p=0', str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=30)
        blob = (r.stdout or '')
        return ('subtitle' in blob.lower()), blob.strip().replace('\n', ' | ')[:120]
    except Exception as e:
        return False, str(e)


# ======================================================================
def test_error_and_stop():
    print("\n[4] 失败中断 / 停止信号 / 出错继续")
    root = Path(tempfile.mkdtemp(prefix="wf_test3_"))
    a = root / "A"
    a.mkdir()
    make_video(a / "SSIS-777.mp4", seconds=3)

    # ---- 目标文件夹不存在时自动创建，移动仍应成功 ----
    make_video(a / "SSIS-777.mp4", seconds=3)
    items = build_items([str(a / "SSIS-777.mp4")])
    job = WorkflowJob(name="测试：目标自动创建")
    job.steps = [WorkflowStep(STEP_MOVE, params={"target_dir": str(root / "NOPE")})]
    logs: list = []
    run_job(job, items, logs)
    check("目标文件夹不存在时自动创建并移动成功",
          items[0].status == "已完成" and (root / "NOPE" / "SSIS-777.mp4").exists(),
          f"{items[0].status}: {items[0].message}")

    # ---- 非法参数（未设置目标文件夹）应被捕获为失败而非崩溃 ----
    make_video(a / "SSIS-888.mp4", seconds=3)
    items2 = build_items([str(a / "SSIS-888.mp4")])
    job2 = WorkflowJob(name="测试：非法参数")
    job2.steps = [WorkflowStep(STEP_MOVE, params={"target_dir": ""})]
    logs2: list = []
    ex2 = WorkflowExecutor(job2, items2, on_log=logs2.append)
    ex2.start(); ex2.join(timeout=60)
    check("非法参数被捕获为失败", items2[0].status == "失败", items2[0].status)
    check("失败信息写入 message", "未设置移动目标文件夹" in items2[0].message,
          items2[0].message)
    check("失败后文件仍在原地", (a / "SSIS-888.mp4").exists())

    # ---- 停止信号：转码一个较长视频，中途停止 ----
    make_video(a / "SSIS-999.mp4", seconds=120)
    items3 = build_items([str(a / "SSIS-999.mp4")])
    job3 = WorkflowJob(name="测试：停止信号")
    job3.steps = [WorkflowStep(STEP_TRANSCODE, params={
        "trim_mode": "none", "resolution": "320x240", "bitrate": 800,
        "encoder": "CPU（libx265 软编码）", "save_mode": "source",
        "out_ext": "mp4"})]
    logs3: list = []
    ex3 = WorkflowExecutor(job3, items3, on_log=logs3.append)
    ex3.start()
    time.sleep(1.0)
    ex3.stop()
    ex3.join(timeout=180)
    check("停止信号生效（状态=已停止）", items3[0].status == "已停止", items3[0].status)
    check("停止后不残留半截成片",
          not (a / ".wf_tmp_SSIS-999.mp4").exists(), str(list(a.glob('.wf_tmp*'))))
    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_rename_step():
    print("\n[5] 修改名称步骤（rename）")
    root = Path(tempfile.mkdtemp(prefix="wf_test4_"))
    a = root / "A"
    a.mkdir(parents=True, exist_ok=True)
    vid = a / "SSIS-777.mp4"
    make_video(vid, seconds=2)
    srt = a / "SSIS-777.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    items = build_items([str(vid)])
    items[0].subtitle = str(srt)

    # 改名 + 同步改名同名字幕
    job = WorkflowJob(name="改名测试")
    job.steps = [WorkflowStep(STEP_RENAME, params={
        "name_template": "{uid}_NEW", "with_companions": True})]
    logs: list = []
    run_job(job, items, logs)
    time.sleep(0.2)
    it = items[0]
    check("条目完成", it.status == "已完成", f"{it.status} {it.message}")
    newp = Path(it.path)
    check("文件按 {uid}_NEW 改名", newp.name == "SSIS-777_NEW.mp4", newp.name)
    check("原文件已不存在", not vid.exists(), str(vid))
    check("同名字幕一并改名", bool(it.subtitle) and it.subtitle.endswith("SSIS-777_NEW.srt")
          and os.path.isfile(it.subtitle), it.subtitle or "无")

    # 名称未变化场景：模板渲染后同名应跳过且不报错
    job2 = WorkflowJob(name="改名-不变")
    job2.steps = [WorkflowStep(STEP_RENAME, params={"name_template": "{stem}"})]
    run_job(job2, items, [])
    time.sleep(0.2)
    check("名称未变时跳过且仍完成", items[0].status == "已完成", items[0].status)

    # 改名后接转码：最终文件名应沿用 rename 之后的名字（验证步骤衔接）
    job3 = WorkflowJob(name="改名→转码")
    job3.steps = [
        WorkflowStep(STEP_RENAME, params={"name_template": "{uid}_cut"}),
        WorkflowStep(STEP_TRANSCODE, params={
            "trim_mode": "none", "resolution": "320x240", "bitrate": 600,
            "encoder": "CPU（libx265 软编码）", "save_mode": "source",
            "out_ext": "mp4", "subtitle_mode": "none"}),
    ]
    # 该视频已改名，需要重新基于当前文件构造条目
    items2 = build_items([str(Path(it.path))])
    run_job(job3, items2, [])
    time.sleep(0.2)
    check("改名→转码（不删源）→ 新文件加后缀共存（SSIS-777_cut_转码.mp4）",
          Path(items2[0].path).name == "SSIS-777_cut_转码.mp4", Path(items2[0].path).name)

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_pause():
    print("\n[6] 暂停 / 恢复")
    # ---- A) StepContext 对 ffmpeg 类子进程的挂起/恢复（Windows 真实子进程）----
    if os.name == 'nt':
        counter = tempfile.mktemp(suffix=".txt")
        script = ("import time,pathlib\n"
                  "p=pathlib.Path(%r)\n"
                  "for i in range(200):\n"
                  "    p.write_text(str(i))\n"
                  "    time.sleep(0.1)\n") % counter
        proc = subprocess.Popen([sys.executable, "-c", script])
        try:
            ctx = StepContext()
            ctx.set_proc_provider(lambda: proc)
            time.sleep(0.5)
            ctx.pause()
            t1 = int((Path(counter).read_text() or "0"))
            time.sleep(0.8)
            t2 = int((Path(counter).read_text() or "0"))
            check("暂停后子进程被冻结（计数停止增长）", t1 == t2 and t1 > 0,
                  f"{t1} -> {t2}")
            ctx.resume()
            time.sleep(0.6)
            t3 = int((Path(counter).read_text() or "0"))
            check("恢复后子进程继续运行（计数继续增长）", t3 > t2, f"{t2} -> {t3}")
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
    else:
        print("  （非 Windows 环境，跳过进程级挂起真实验证）")

    # ---- B) 执行引擎 pause()/resume()：暂停时冻结当前步骤，恢复后从原位置继续 ----
    from workflow.executor import WorkflowExecutor
    observed: list = []
    started = threading.Event()

    def fake_handler(item, params, ctx):
        started.set()
        for i in range(30):
            ctx.check_pause()          # 协作式暂停点；暂停时在此阻塞
            if ctx.stopped():
                raise StepStopped("已停止")
            observed.append(i)
            time.sleep(0.04)
        return item.path

    items = [WorkflowItem(uid="PAUSE1", origin="x.mp4")]
    job = WorkflowJob(name="暂停测试")
    job.steps = [WorkflowStep(STEP_MOVE, params={})]   # 用 move 类型承载桩 handler
    ex = WorkflowExecutor(job, items, handlers={STEP_MOVE: fake_handler},
                          tasks=[(job, items)])
    ex.start()
    started.wait(5)
    time.sleep(0.3)                    # 让循环先积累若干次
    ex.pause()
    frozen1 = len(observed)
    time.sleep(0.6)                    # 暂停期间不应再有新的 append
    frozen2 = len(observed)
    check("暂停期间当前步骤被冻结（循环不再推进）",
          frozen1 == frozen2 and frozen1 > 0, f"{frozen1} -> {frozen2}")
    ex.resume()
    ex.join(15)
    check("恢复后执行完成且结果未被破坏", ex.stats.get("done") == 1, str(ex.stats))
    check("恢复后循环从原位置继续到结束", len(observed) == 30, str(len(observed)))

    # ---- C) 暂停后再停止：解除冻结并干净退出，不卡死、状态正确 ----
    observed3: list = []
    started3 = threading.Event()

    def h_stop(item, params, ctx):
        started3.set()
        for i in range(100):
            ctx.check_pause()
            if ctx.stopped():
                raise StepStopped("已停止")
            observed3.append(i)
            time.sleep(0.05)
        return item.path

    items_c = [WorkflowItem(uid="STOP1", origin="y.mp4")]
    job_c = WorkflowJob(name="暂停后停止")
    job_c.steps = [WorkflowStep(STEP_MOVE, params={})]
    ex_c = WorkflowExecutor(job_c, items_c, handlers={STEP_MOVE: h_stop},
                            tasks=[(job_c, items_c)])
    ex_c.start()
    started3.wait(5)
    time.sleep(0.3)
    ex_c.pause()
    time.sleep(0.3)
    ex_c.stop()
    ex_c.join(15)
    check("暂停后停止仍能在超时内结束（未卡死）", not ex_c.is_alive(),
          f"alive={ex_c.is_alive()}")
    check("暂停后停止：步骤被判定为已停止而非失败",
          items_c[0].status == "已停止", items_c[0].status)


# ======================================================================
def test_parallel():
    print("\n[7] 并行处理（多线程同时处理多个视频 / 任务）")
    from workflow.executor import WorkflowExecutor
    from workflow.models import STEP_MOVE

    events: list = []
    ev_lock = threading.Lock()
    base = time.monotonic()

    def handler(item, params, ctx):
        s = time.monotonic() - base
        time.sleep(0.25)
        e = time.monotonic() - base
        with ev_lock:
            events.append((s, e))
        return item.path

    def max_overlap(evts):
        pts = []
        for s, e in evts:
            pts.append((s, 1))
            pts.append((e, -1))
        pts.sort()
        cur = mx = 0
        for _, d in pts:
            cur += d
            mx = max(mx, cur)
        return mx

    items = [WorkflowItem(uid=f"P{i}", origin=f"v{i}.mp4") for i in range(5)]
    job = WorkflowJob(name="并行测试")
    job.steps = [WorkflowStep(STEP_MOVE, params={})]
    ex = WorkflowExecutor(job, items, handlers={STEP_MOVE: handler},
                          tasks=[(job, items)], max_workers=3)
    t0 = time.monotonic()
    ex.start()
    ex.join(30)
    elapsed = time.monotonic() - t0

    check("全部 5 个视频完成", ex.stats.get("done") == 5, str(ex.stats))
    check("并行生效（耗时明显小于串行）", elapsed < 0.9, f"{elapsed:.2f}s（串行约 1.25s）")
    check("存在并发重叠（线程数>1 确实并行）", max_overlap(events) >= 2,
          f"max_overlap={max_overlap(events)}")

    # 默认 max_workers=1 应退化为串行（耗时接近 5*0.25）
    events.clear()
    items1 = [WorkflowItem(uid=f"S{i}", origin=f"s{i}.mp4") for i in range(5)]
    ex1 = WorkflowExecutor(job, items1, handlers={STEP_MOVE: handler},
                           tasks=[(job, items1)], max_workers=1)
    t1 = time.monotonic()
    ex1.start()
    ex1.join(30)
    serial_elapsed = time.monotonic() - t1
    check("max_workers=1 时为串行", serial_elapsed >= 0.9, f"{serial_elapsed:.2f}s")
    check("串行时无并发重叠", max_overlap(events) == 1, f"max_overlap={max_overlap(events)}")


# ======================================================================
def test_subtitle_model_reuse():
    print("\n[8] 字幕模型复用（一次加载，按剩余需求释放）")
    import workflow.steps as steps_mod                      # noqa: E402
    import subtitle.core as core_mod                          # noqa: E402
    from workflow.models import (STEP_SUBTITLE, WorkflowItem,  # noqa: E402
                                 WorkflowJob, WorkflowStep)

    loads = {"n": 0}
    releases = {"asr": 0, "translate": 0}

    class FakeGen:
        def __init__(self, **kw):
            loads["n"] += 1
        def process_video(self, *a, **k):
            return "x.srt"
        def release_asr(self):
            releases["asr"] += 1
        def release_translator(self):
            releases["translate"] += 1
        def release_resources(self):
            releases["asr"] += 1
            releases["translate"] += 1

    orig = core_mod.SubtitleGenerator
    core_mod.SubtitleGenerator = FakeGen
    try:
        def fake_sub_handler(item, params, ctx):
            coord = getattr(ctx, 'subtitle_coordinator', None)
            if coord is not None:
                # 模拟字幕步骤：复用运行期共享模型（语音+翻译均需要）
                with coord.use(item.uid, True, True, {}) as gen:
                    gen.process_video(item.path)
            return item.path

        items = [WorkflowItem(uid=f"M{i}", origin=f"m{i}.mp4") for i in range(3)]
        job = WorkflowJob(name="字幕复用")
        job.steps = [WorkflowStep(STEP_SUBTITLE, params={"bilingual": True})]
        ex = WorkflowExecutor(job, items, handlers={STEP_SUBTITLE: fake_sub_handler},
                              tasks=[(job, items)], max_workers=1)
        ex.start()
        ex.join(30)
        check("多个视频同一次运行只加载一次字幕模型", loads["n"] == 1, f"loads={loads['n']}")
        check("全部字幕条目完成", ex.stats.get("done") == 3, str(ex.stats))
        check("运行结束释放语音模型", releases["asr"] >= 1, f"rel_asr={releases['asr']}")
        check("运行结束释放翻译模型", releases["translate"] >= 1, f"rel_tr={releases['translate']}")

        # 场景2：任务A（双语，需翻译）+ 任务B（仅语音，不需翻译）
        # 验证“后续不再需要翻译时”在任务A结束后即释放翻译模型，而语音模型保留到任务B结束
        loads.clear(); loads["n"] = 0
        releases.clear(); releases["asr"] = 0; releases["translate"] = 0
        itemsA = [WorkflowItem(uid=f"A{i}", origin=f"a{i}.mp4") for i in range(2)]
        itemsB = [WorkflowItem(uid=f"B{i}", origin=f"b{i}.mp4") for i in range(1)]
        jobA = WorkflowJob(name="任务A-双语")
        jobA.steps = [WorkflowStep(STEP_SUBTITLE, params={"bilingual": True})]
        jobB = WorkflowJob(name="任务B-仅语音")
        jobB.steps = [WorkflowStep(STEP_SUBTITLE, params={"bilingual": False})]
        snap = {}
        orig_done = WorkflowExecutor._subtitle_item_done

        def tracking_done(self, uid, na, nt, gen):
            orig_done(self, uid, na, nt, gen)
            if uid == "A1":   # 任务A最后一个条目处理完
                snap["translate_released_after_A"] = self._released_translate
                snap["asr_released_after_A"] = self._released_asr

        WorkflowExecutor._subtitle_item_done = tracking_done
        try:
            ex2 = WorkflowExecutor(jobA, [], handlers={STEP_SUBTITLE: fake_sub_handler},
                                   tasks=[(jobA, itemsA), (jobB, itemsB)], max_workers=1)
            ex2.start()
            ex2.join(30)
        finally:
            WorkflowExecutor._subtitle_item_done = orig_done
        check("多任务下仍只加载一次模型", loads["n"] == 1, f"loads={loads['n']}")
        check("任务A结束后即释放翻译模型（后续任务不再需要）",
              snap.get("translate_released_after_A") is True, str(snap))
        check("任务A结束后语音模型仍保留（任务B仍需）",
              snap.get("asr_released_after_A") is False, str(snap))
        check("运行结束释放语音模型", releases["asr"] >= 1, f"rel_asr={releases['asr']}")
    finally:
        core_mod.SubtitleGenerator = orig


# ======================================================================
def test_heavy_resource_serialized():
    print("\n[9] 多线程下重资源操作（转码/语音/翻译）串行排队，不并发抢占显存")
    import subtitle.core as core_mod                          # noqa: E402
    from workflow.models import (STEP_MOVE, STEP_SUBTITLE,     # noqa: E402
                                 WorkflowItem, WorkflowJob, WorkflowStep)

    class FakeGen:
        def __init__(self, **kw):
            pass
        def process_video(self, *a, **k):
            return "x.srt"
        def release_asr(self):
            pass
        def release_translator(self):
            pass
        def release_resources(self):
            pass

    orig = core_mod.SubtitleGenerator
    core_mod.SubtitleGenerator = FakeGen
    try:
        overlap = {"cur": 0, "max": 0}
        ol = threading.Lock()

        def heavy(kind):
            with ol:
                overlap["cur"] += 1
                overlap["max"] = max(overlap["max"], overlap["cur"])
            time.sleep(0.2)
            with ol:
                overlap["cur"] -= 1

        def transcode_handler(item, params, ctx):
            rl = getattr(ctx, 'resource_lock', None)
            if rl is not None:
                rl.acquire()
            try:
                heavy("transcode")      # 模拟 NVENC/CUDA 全显存流水线
            finally:
                if rl is not None:
                    rl.release()
            return item.path

        def subtitle_handler(item, params, ctx):
            coord = getattr(ctx, 'subtitle_coordinator', None)
            if coord is not None:
                # 字幕步骤经协调器锁（即共享重资源锁）串行使用语音/翻译模型
                with coord.use(item.uid, True, True, {}) as gen:
                    heavy("subtitle")
            return item.path

        itemsT = [WorkflowItem(uid="T0", origin="t.mp4")]
        itemsS = [WorkflowItem(uid="S0", origin="s.mp4")]
        jobT = WorkflowJob(name="转码任务")
        jobT.steps = [WorkflowStep(STEP_MOVE, params={})]       # 用 STEP_MOVE 承载转码 handler
        jobS = WorkflowJob(name="字幕任务")
        jobS.steps = [WorkflowStep(STEP_SUBTITLE, params={"bilingual": True})]
        ex = WorkflowExecutor(jobT, itemsT,
                              handlers={STEP_MOVE: transcode_handler,
                                        STEP_SUBTITLE: subtitle_handler},
                              tasks=[(jobT, itemsT), (jobS, itemsS)], max_workers=2)
        ex.start()
        ex.join(30)

        check("多线程下转码与字幕重资源操作不并发（max_overlap<=1）",
              overlap["max"] <= 1, f"max_overlap={overlap['max']}")
        check("两个任务均完成", ex.stats.get("done") == 2, str(ex.stats))

        # max_workers=1 时自然串行，且 max_workers=4 也应被重资源锁收敛为不并发
        overlap["cur"] = overlap["max"] = 0
        itemsT2 = [WorkflowItem(uid=f"T{i}", origin=f"t{i}.mp4") for i in range(2)]
        itemsS2 = [WorkflowItem(uid=f"S{i}", origin=f"s{i}.mp4") for i in range(2)]
        jobT2 = WorkflowJob(name="转码任务2")
        jobT2.steps = [WorkflowStep(STEP_MOVE, params={})]
        jobS2 = WorkflowJob(name="字幕任务2")
        jobS2.steps = [WorkflowStep(STEP_SUBTITLE, params={"bilingual": True})]
        ex2 = WorkflowExecutor(jobT2, itemsT2,
                               handlers={STEP_MOVE: transcode_handler,
                                         STEP_SUBTITLE: subtitle_handler},
                               tasks=[(jobT2, itemsT2), (jobS2, itemsS2)], max_workers=4)
        ex2.start()
        ex2.join(30)
        check("max_workers=4 下重资源操作仍不并发", overlap["max"] <= 1,
              f"max_overlap={overlap['max']}")
        check("4 个视频均完成", ex2.stats.get("done") == 4, str(ex2.stats))
    finally:
        core_mod.SubtitleGenerator = orig


# ======================================================================
def test_legacy_name_template_cleaned():
    print("\n[10] 旧配置残留的转码输出名模板自动清除（避免与改名步骤叠加 -C-C）")
    legacy = {
        "name": "旧配置", "source_dir": "", "recursive": True, "only_video": True,
        "steps": [
            {"type": STEP_TRANSCODE, "enabled": True,
             "params": {"trim_mode": "none", "name_template": "{uid_norm}-C"}},
            {"type": STEP_RENAME, "enabled": True,
             "params": {"name_template": "{uid_norm}-C"}},
        ],
    }
    job = WorkflowJob.from_dict(legacy)
    tr, rn = job.steps[0], job.steps[1]
    check("转码步骤不再含输出名模板（加载时自动清除旧残留）",
          'name_template' not in tr.params, str(tr.params.get('name_template')))
    check("修改名称步骤的文件名模板正常保留",
          rn.params.get('name_template') == '{uid_norm}-C', str(rn.params.get('name_template')))

    # 改名步骤对原始文件只追加一次 -C
    it = WorkflowItem(uid="ROYD-273", origin="X:/v/ROYD-273-Uncensored.mp4")
    check("改名模板渲染：ROYD-273-Uncensored → ROYD-273-Uncensored-C",
          it.render(rn.params['name_template']) == 'ROYD-273-Uncensored-C',
          it.render(rn.params['name_template']))

    # 幂等性：对已含 -C 的当前文件名再执行同一改名模板，不会追加第二个 -C
    # （uid_norm 会去掉已归一的后缀再渲染；旧 bug 的第二次 -C 来自转码步骤残留模板，
    #   现已随 name_template 清除而阻断）
    it2 = WorkflowItem(uid="ROYD-273", origin="X:/v/ROYD-273-Uncensored.mp4",
                       current="X:/v/ROYD-273-Uncensored-C.mp4")
    check("（幂等）已含 -C 的文件名再执行改名模板不会变成 -C-C",
          it2.render('{uid_norm}-C') == 'ROYD-273-Uncensored-C',
          it2.render('{uid_norm}-C'))


# ======================================================================
def test_uid_learner_generalization():
    """[11] UID 学习库：从人工修正泛化出提取方法（同类型文件名自动识别）"""
    from workflow.uid_learner import UidLearner
    tmp = Path(tempfile.mkdtemp(prefix="wf_uid_learn_")) / "uid_learn.json"
    L = UidLearner(path=tmp).load()

    # 1) 精确记忆：同名文件直接用修正结果（调用方传入的是去扩展名的 stem）
    L.learn("abc-123", "abc123")
    check("精确记忆命中", L.suggest("abc-123.mp4") == ("abc123", "learned"),
          str(L.suggest("abc-123.mp4")))

    # 2) 方法归纳：用户修正一次「高梨真緒 ABN-129 Uncensored」→ ABN-129
    #    应归纳出 (字形[字母-数字] + 空格定界) 的提取方法，
    #    并对*从没见过*的同类型新文件名泛化提取（SSIS/IPX/HND 等不同字母数同结构）
    L.learn("高梨真緒 ABN-129 Uncensored", "ABN-129")
    suggest_same_type = L.suggest("山岸逢花 SSIS-001 Uncensored.mp4")
    check("方法归纳：同类型新文件名泛化提取 SSIS-001",
          suggest_same_type == ("SSIS-001", "learned_template"), str(suggest_same_type))
    suggest_same_type2 = L.suggest("深田えいみ IPX-590 Uncensored.mp4")
    check("方法归纳：另一同类型文件名提取 IPX-590",
          suggest_same_type2 == ("IPX-590", "learned_template"), str(suggest_same_type2))

    # 3) 不应误命中：无番号结构的中文名不返回伪 UID
    check("无番号文件名不被模板误命中",
          L.suggest("我的家庭录像.mp4") == ("", ""), str(L.suggest("我的家庭录像.mp4")))

    # 4) 垃圾前后缀归纳仍有效（整体水印，不依赖 UID 字形）
    L2 = UidLearner(path=Path(tempfile.mkdtemp(prefix="wf_uid2_")) / "u.json").load()
    L2.learn("489155.com@xyz-789.mp4", "XYZ-789")
    s2 = L2.suggest("489155.com@abc-321.mp4")
    check("垃圾前缀归纳：剥离水印后用主匹配器提取",
          s2[0].upper() in ("XYZ-789", "ABC-321") and s2[1] == "learned_pattern", str(s2))

    # 5) 同一类结构被多次修正后 support 提升（统计反映模板已学到）
    st = L.stats()
    check("学习库已归纳出模板", st.get("uid_templates", 0) >= 1, str(st))

    # 6) 持久化：重新加载仍能泛化
    L3 = UidLearner(path=tmp).load()
    check("重载后模板持久化仍可泛化",
          L3.suggest("某演员 SSIS-002 Uncensored.mp4") == ("SSIS-002", "learned_template"),
          str(L3.suggest("某演员 SSIS-002 Uncensored.mp4")))

    shutil.rmtree(tmp.parent, ignore_errors=True)
    shutil.rmtree(L2.path.parent, ignore_errors=True)


# ======================================================================
def _frame_diff_yavg(out_path: Path, ref_path: Path, ss: float, w=320, h=240) -> float:
    """输出首帧 与 源在 ss 处帧 的平均灰度差（0=同一画面；差异越大画面越不同）"""
    fc = (f"[0:v]scale={w}:{h},format=gray[a];"
          f"[1:v]scale={w}:{h},format=gray[b];"
          f"[a][b]blend=all_mode=difference,signalstats,metadata=mode=print")
    r = subprocess.run([FFMPEG, '-loglevel', 'info', '-i', str(out_path),
                        '-ss', f'{ss:.3f}', '-i', str(ref_path),
                        '-filter_complex', fc, '-frames:v', '1',
                        '-f', 'null', '-'], capture_output=True, text=True, timeout=120)
    m = re.findall(r'YAVG=([\d.]+)', (r.stderr or '') + (r.stdout or ''))
    return float(m[-1]) if m else -1.0


def _probe_duration(path: Path) -> float:
    """用 ffprobe 读时长（秒）"""
    fp = shutil.which('ffprobe') or os.path.join(os.path.dirname(FFMPEG), 'ffprobe')
    r = subprocess.run([fp, '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, timeout=60)
    try:
        return float((r.stdout or '0').strip().split(',')[0])
    except (ValueError, IndexError):
        return -1.0


def test_trim_precision_reencode():
    """[12] 剪辑起点必须精确到 0.1 秒（转码/改分辨率/挂字幕同样适用）"""
    print("\n[12] 剪辑起止精度：重编码（转码+缩放+字幕）与保留原画质均精确到帧")
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_trim_"))
    src = root / "src.mp4"
    # 20 秒、关键帧约 10 秒一个（libx264 默认 GOP 250 帧 @25fps）：
    # 起点 7.3s 落在 GOP 中间，正是“提前到上一个关键帧” bug 的典型触发点
    if not make_video(src, seconds=20):
        check("生成测试视频", False)
        shutil.rmtree(root, ignore_errors=True)
        return
    srt = root / "src.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:19,900\n全程字幕\n", encoding="utf-8")

    START, END = 7.3, 10.0
    outdir = root / "out"
    outdir.mkdir()

    # ---- 场景一：重编码（改分辨率 + 封装软字幕 + 剪辑）----
    out1 = outdir / "reenc.mp4"
    t1 = TranscodeTask(
        src=str(src), start=START, end=END, src_w=320, src_h=240, fps=25.0,
        resolution="320x240", aspect="保持原比例", bitrate=600,
        save_mode="custom", save_dir=str(outdir), out_ext="mp4",
        subtitle=str(srt), subtitle_mode="soft", keep_quality=False)
    t1.out_path = str(out1)
    t1._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
    logs1: list = []
    res1 = HeadlessTranscoder(FFMPEG, log=logs1.append).run(t1)
    check("重编码剪辑成功", res1.get('status') == 'success', str(res1.get('detail'))[:200])
    check("重编码剪辑改用 CPU 解码以保证精度（日志含[剪辑精度]）",
          any("[剪辑精度]" in m for m in logs1),
          next((m for m in logs1 if "剪辑精度" in m), "无"))
    if out1.exists():
        d_ok = _frame_diff_yavg(out1, src, START)
        d_bad = _frame_diff_yavg(out1, src, START - 3.0)
        check("重编码起点与源 7.3s 处画面一致（精确到 0.1 秒）",
              0 <= d_ok < 1.5, f"画面差 YAVG={d_ok:.2f}（同一画面应≈0）")
        check("（对照）若提前 3 秒画面会明显不同 —— 证明该断言能检出此 bug",
              d_bad > 5.0, f"提前 3 秒的 YAVG={d_bad:.2f}（应远大于正确值）")
        dur = _probe_duration(out1)
        check("重编码时长精确（结束也准）", dur > 0 and abs(dur - (END - START)) <= 0.3,
              f"时长 {dur:.3f}s（期望 {END - START:.1f}s）")

    # ---- 场景二：保留原画质（智能剪切）----
    out2 = outdir / "keep.mp4"
    t2 = TranscodeTask(
        src=str(src), start=START, end=END, src_w=320, src_h=240, fps=25.0,
        resolution="保持原分辨率", bitrate=600,
        save_mode="custom", save_dir=str(outdir), out_ext="mp4",
        subtitle_mode="none", keep_quality=True)
    t2.out_path = str(out2)
    t2._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
    res2 = HeadlessTranscoder(FFMPEG, log=logs1.append).run(t2)
    check("保留原画质剪辑成功", res2.get('status') == 'success', str(res2.get('detail'))[:200])
    if out2.exists():
        d_ok2 = _frame_diff_yavg(out2, src, START)
        check("保留原画质起点与源 7.3s 处画面一致（精确到 0.1 秒）",
              0 <= d_ok2 < 1.5, f"画面差 YAVG={d_ok2:.2f}")
        dur2 = _probe_duration(out2)
        check("保留原画质时长精确", dur2 > 0 and abs(dur2 - (END - START)) <= 0.3,
              f"时长 {dur2:.3f}s（期望 {END - START:.1f}s）")

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def _stream_bitrate_kbps(path: Path, kind: str = "video") -> float:
    """用 ffprobe 读指定流的码率（kbps）。注意 -select_streams 只认 v/a/s 简写。"""
    fp = shutil.which('ffprobe') or os.path.join(os.path.dirname(FFMPEG), 'ffprobe')
    spec = {'video': 'v', 'audio': 'a'}.get(kind, kind)
    r = subprocess.run([fp, '-v', 'error', '-select_streams', f'{spec}:0',
                        '-show_entries', 'stream=bit_rate', '-of', 'csv=p=0', str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, timeout=60)
    try:
        return float((r.stdout or '0').strip().split(',')[0]) / 1000.0
    except (ValueError, IndexError):
        return -1.0


def test_keep_bitrate():
    """[13] 剪辑转码“不修改码率”：按原片码率编码，无需逐个视频调整"""
    print("\n[13] 不修改码率：沿用原片码率编码")
    import video_grid_tab as vgt
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_br_"))
    src = root / "src.mp4"
    # 生成一个明确码率（约 1200 kbps）的 8 秒源视频
    gen = [FFMPEG, '-y', '-nostdin', '-loglevel', 'error',
           '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25',
           '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
           '-t', '8', '-c:v', 'libx264', '-b:v', '1200k', '-maxrate', '1500k',
           '-bufsize', '2400k', '-pix_fmt', 'yuv420p', '-preset', 'ultrafast',
           '-c:a', 'aac', '-shortest', str(src)]
    if subprocess.run(gen, capture_output=True).returncode != 0:
        check("生成指定码率测试视频", False)
        shutil.rmtree(root, ignore_errors=True)
        return
    src_br = _stream_bitrate_kbps(src)
    check("源视频码率可读且明显低于兜底值 2500", 200 <= src_br <= 1600,
          f"{src_br:.0f} kbps")

    outdir = root / "out"
    outdir.mkdir()
    logs: list = []

    # 捕获真实 ffmpeg 命令，直接验证 -b:v 取值（x265 对简单内容会欠投，
    # 看成片码率不可靠，故以命令参数为准）
    real_popen = vgt.subprocess.Popen
    captured: list = []

    class _SpyPopen:
        def __init__(self, cmd, *a, **k):
            try:
                if 'ffmpeg' in os.path.basename(str(cmd[0])).lower():
                    captured.append([str(x) for x in cmd])
            except Exception:
                pass
            self._p = real_popen(cmd, *a, **k)

        def __getattr__(self, name):
            return getattr(self._p, name)

        def __enter__(self):
            self._p.__enter__()
            return self

        def __exit__(self, *exc):
            return self._p.__exit__(*exc)

    def _bv_of(cmds):
        """取 ffmpeg 命令里的 -b:v 值（如 '2500k'）"""
        for c in cmds:
            for i, a in enumerate(c):
                if a == '-b:v' and i + 1 < len(c):
                    return c[i + 1]
        return None

    vgt.subprocess.Popen = _SpyPopen
    try:
        # 勾选“不修改码率”：界面给的是 2500 的兜底值，实际应按原片码率编码
        out1 = outdir / "keepbr.mp4"
        t1 = TranscodeTask(src=str(src), start=1.0, end=6.0, src_w=320, src_h=240,
                           fps=25.0, resolution="320x240", aspect="保持原比例",
                           bitrate=2500, save_mode="custom", save_dir=str(outdir),
                           out_ext="mp4", subtitle_mode="none", keep_quality=False,
                           keep_bitrate=True)
        t1.out_path = str(out1)
        t1._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
        captured.clear()
        res1 = HeadlessTranscoder(FFMPEG, log=logs.append).run(t1)
        check("不修改码率转码成功", res1.get('status') == 'success',
              str(res1.get('detail'))[:200])
        check("日志记录沿用原片码率",
              any("不修改码率" in m and "沿用原片码率" in m for m in logs),
              next((m for m in logs if "不修改码率" in m), "无"))
        bv1 = _bv_of(captured)
        check("ffmpeg 实际按原片码率编码", bv1 == f"{int(src_br)}k",
              f"-b:v={bv1}（原片 {src_br:.0f}k / 兜底 2500k）")

        # 对照组：不勾选时按界面设定的 2500 kbps
        out2 = outdir / "setbr.mp4"
        t2 = TranscodeTask(src=str(src), start=1.0, end=6.0, src_w=320, src_h=240,
                           fps=25.0, resolution="320x240", aspect="保持原比例",
                           bitrate=2500, save_mode="custom", save_dir=str(outdir),
                           out_ext="mp4", subtitle_mode="none", keep_quality=False,
                           keep_bitrate=False)
        t2.out_path = str(out2)
        t2._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
        captured.clear()
        res2 = HeadlessTranscoder(FFMPEG, log=logs.append).run(t2)
        check("对照组转码成功", res2.get('status') == 'success',
              str(res2.get('detail'))[:200])
        bv2 = _bv_of(captured)
        check("不勾选时按设定码率 2500 编码", bv2 == "2500k", f"-b:v={bv2}")
    finally:
        vgt.subprocess.Popen = real_popen

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_input_formats_to_hevc():
    """[14] 输入格式兼容性：avi / wmv(asf) / mkv / flv 均可转成 H.265 mp4"""
    print("\n[14] 输入格式 → H.265 mp4（rm/rmvb 由 ffmpeg 原生 rv40/cook 解码 + ffprobe 兜底）")
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_fmt_"))
    outdir = root / "out"
    outdir.mkdir()
    cases = {
        "src.avi": ["-c:v", "mpeg4", "-c:a", "libmp3lame"],
        "src.asf": ["-c:v", "wmv2", "-c:a", "wmav2"],
        "src.mkv": ["-c:v", "libx264", "-c:a", "aac"],
        "src.flv": ["-c:v", "flv", "-c:a", "aac"],
    }
    for name, codec in cases.items():
        src = root / name
        gen = [FFMPEG, '-y', '-nostdin', '-loglevel', 'error',
               '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
               '-t', '6'] + codec + [str(src)]
        if subprocess.run(gen, capture_output=True).returncode != 0:
            check(f"生成 {name}", False)
            continue
        out = outdir / f"{src.stem}.mp4"
        t = TranscodeTask(src=str(src), start=1.0, end=5.0, src_w=320, src_h=240,
                          fps=25.0, resolution="320x240", aspect="保持原比例",
                          bitrate=800, save_mode="custom", save_dir=str(outdir),
                          out_ext="mp4", subtitle_mode="none", keep_quality=False)
        t.out_path = str(out)
        t._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
        res = HeadlessTranscoder(FFMPEG, log=lambda m: None).run(t)
        codec_name = ""
        if out.exists():
            r = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error',
                                '-select_streams', 'v:0', '-show_entries',
                                'stream=codec_name', '-of', 'csv=p=0', str(out)],
                               capture_output=True, text=True)
            codec_name = (r.stdout or '').strip()
        check(f"{name} → H.265 mp4",
              res.get('status') == 'success' and codec_name == 'hevc',
              f"status={res.get('status')} codec={codec_name}")
    shutil.rmtree(root, ignore_errors=True)


def test_merge_segments_with_subtitle_align():
    """[15] 多视频合并：统一参数 + 每段独立前后剪辑 + 字幕整体对齐"""
    print("\n[15] 多视频合并（拼接）：每段独立剪辑，字幕按累计偏移对齐")
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_merge_"))
    specs = [("a.mp4", ["-c:v", "libx264", "-c:a", "aac"], 6),
             ("b.mkv", ["-c:v", "libx264", "-c:a", "aac"], 8),
             ("c.avi", ["-c:v", "mpeg4", "-c:a", "libmp3lame"], 5)]
    srcs = []
    for name, codec, dur in specs:
        p = root / name
        gen = [FFMPEG, '-y', '-nostdin', '-loglevel', 'error',
               '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
               '-t', str(dur), '-pix_fmt', 'yuv420p'] + codec + [str(p)]
        if subprocess.run(gen, capture_output=True).returncode != 0:
            check(f"生成 {name}", False)
            shutil.rmtree(root, ignore_errors=True)
            return
        srcs.append(p)
        (root / f"{p.stem}.srt").write_text(
            "1\n00:00:00,000 --> 00:00:59,000\nSEG\n", encoding="utf-8")

    # 每段独立剪辑：段1 去掉开头 1 秒、段2 去掉结尾 1 秒、段3 两端各去 0.5 秒
    segs = [
        {"src": str(srcs[0]), "start": 1.0, "end": 5.0, "src_w": 320, "src_h": 240,
         "fps": 25.0, "srt": str(root / "a.srt")},
        {"src": str(srcs[1]), "start": 0.0, "end": 7.0, "src_w": 320, "src_h": 240,
         "fps": 25.0, "srt": str(root / "b.srt")},
        {"src": str(srcs[2]), "start": 0.5, "end": 4.5, "src_w": 320, "src_h": 240,
         "fps": 25.0, "srt": str(root / "c.srt")},
    ]
    outdir = root / "out"
    outdir.mkdir()
    out = outdir / "合并测试.mp4"
    task = TranscodeTask(
        src=segs[0]["src"], start=1.0, end=5.0, src_w=320, src_h=240, fps=25.0,
        resolution="320x240", aspect="保持原比例", bitrate=800,
        save_mode="custom", save_dir=str(outdir), out_ext="mp4",
        subtitle_mode="soft", subtitle=segs[0]["srt"], keep_quality=False,
        merge=True, merge_name="合并测试", segments=segs)
    task.out_path = str(out)
    logs: list = []
    res = HeadlessTranscoder(FFMPEG, log=logs.append).run(task)

    check("合并任务成功", res.get('status') == 'success', str(res.get('detail'))[:200])
    check("成片已生成", out.exists() and out.stat().st_size > 0)
    if not out.exists():
        shutil.rmtree(root, ignore_errors=True)
        return
    # 时长 = 4 + 7 + 4 = 15 秒（各段独立剪辑后相加）
    dur = _probe_duration(out)
    check("成片时长为各段剪辑后之和（15s）", dur > 0 and abs(dur - 15.0) <= 0.6,
          f"{dur:.2f}s")
    # 编码为 H.265
    r = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error',
                        '-select_streams', 'v:0', '-show_entries', 'stream=codec_name',
                        '-of', 'csv=p=0', str(out)], capture_output=True, text=True)
    check("成片为 H.265 编码", (r.stdout or '').strip() == 'hevc', (r.stdout or '').strip())
    # 字幕整体对齐：0→4、4→11、11→15
    r2 = subprocess.run([FFMPEG, '-loglevel', 'error', '-i', str(out), '-map', '0:s:0',
                         '-f', 'srt', '-'], capture_output=True)
    srt_text = (r2.stdout or b'').decode('utf-8', 'replace')
    times = re.findall(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})', srt_text)
    got = [(a[:8], b[:8]) for a, b in times]
    check("三段字幕按累计偏移整体对齐（0-4 / 4-11 / 11-15）",
          got == [("00:00:00", "00:00:04"), ("00:00:04", "00:00:11"),
                  ("00:00:11", "00:00:15")], str(got))
    check("无临时片段残留", not list(outdir.glob('.合并测试*')),
          str([p.name for p in outdir.glob('.合并测试*')]))
    shutil.rmtree(root, ignore_errors=True)


def test_subtitle_first_segment_alignment():
    """[16] 首句字幕对齐到对话起始时间（能量法语音起点检测）"""
    print("\n[16] 首句字幕对齐：首段起点收敛到真实对话起点")
    import wave

    import numpy as np
    from subtitle.core import SubtitleGenerator

    root = Path(tempfile.mkdtemp(prefix="wf_onset_"))
    wav = root / "a.wav"
    sr = 16000
    silence = np.zeros(int(3.0 * sr), dtype=np.float32)
    t = np.arange(int(5.0 * sr), dtype=np.float32) / sr
    tone = (0.35 * np.sin(2 * np.pi * 220 * t) *
            (0.55 + 0.45 * np.sin(2 * np.pi * 3.5 * t))).astype(np.float32)
    pcm = np.concatenate([silence, tone])
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())

    gen = SubtitleGenerator.__new__(SubtitleGenerator)   # 只测试检测/对齐逻辑
    onset = gen._detect_speech_onset(str(wav))
    check("检测到语音起点 ≈3.0s", onset is not None and abs(onset - 3.0) <= 0.15,
          f"{onset}")

    segs = [(0.0, 4.5, "こんにちは"), (5.0, 8.0, "次に行く")]
    fixed = gen._align_first_segment_to_speech(segs, str(wav), log=lambda m: None)
    check("首段起点从 0.00s 收敛到 3.00s（结束时间不变）",
          abs(fixed[0][0] - 3.0) <= 0.15 and fixed[0][1] == 4.5,
          str([(round(s, 2), round(e, 2)) for s, e, _ in fixed]))
    check("后续字幕不受影响", len(fixed) == 2 and fixed[1][0] == 5.0)

    # 首段整段位于静音中 → 丢弃
    segs2 = [(0.0, 1.5, "ノイズ"), (4.0, 7.0, "本編")]
    fixed2 = gen._align_first_segment_to_speech(segs2, str(wav), log=lambda m: None)
    check("整段在静音中的首段被丢弃", len(fixed2) == 1 and fixed2[0][2] == "本編",
          str([t for _, _, t in fixed2]))

    # 已经是语音起点之后的首段不应被改动
    segs3 = [(3.2, 6.0, "本編")]
    fixed3 = gen._align_first_segment_to_speech(segs3, str(wav), log=lambda m: None)
    check("起点已晚于语音 onset 时保持不动", fixed3[0][0] == 3.2, str(fixed3[0][0]))
    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_workflow_merge_step():
    """[17] 工作流拼接：转码步骤勾选拼接，任务内多个视频合并为一个成片"""
    print("\n[17] 工作流“拼接为一个视频”：任务内 3 个视频合并 + 字幕整体对齐")
    root = Path(tempfile.mkdtemp(prefix="wf_merge_step_"))
    outdir = root / "OUT"
    outdir.mkdir()

    srcs = []
    for i, name in enumerate(["AAA-001.mp4", "BBB-002.mp4", "CCC-003.mp4"], 1):
        p = root / name
        if not make_video(p, seconds=6):
            check(f"生成 {name}", False)
            shutil.rmtree(root, ignore_errors=True)
            return
        srcs.append(p)

    items = build_items([str(p) for p in srcs])
    # 每个视频设置各自的剪辑时间：1~5s / 0~4s / 2~6s（拼接后总长 4+4+4=12s）
    trims = [(1.0, 5.0), (0.0, 4.0), (2.0, 6.0)]
    for it, (s, e) in zip(items, trims):
        it.use_custom_trim = True
        it.trim_start = s
        it.trim_end = e

    job = WorkflowJob(name="拼接任务")
    job.steps = [WorkflowStep(STEP_TRANSCODE, params={
        "resolution": "320x240", "aspect": "保持原比例", "bitrate": 600,
        "encoder": "CPU（libx265 软编码）",
        "save_mode": "custom", "save_dir": str(outdir),
        "out_ext": "mp4", "subtitle_mode": "none",
        "tc_merge": True,
    })]

    logs: list = []
    ex = run_job(job, items, logs)
    for line in logs:
        if '拼接' in line or '合并' in line or '失败' in line or '异常' in line:
            print("      " + line)

    ok = True
    for it in items:
        ok &= check(f"{it.uid} 状态=已完成", it.status == "已完成",
                    f"{it.status} {it.message}")
    # 首条目最终指向成片
    merged = outdir / "AAA-001_合并.mp4"
    ok &= check("成片已生成（首段名_合并.mp4）", merged.exists(),
                str(list(outdir.glob('*'))))
    if merged.exists():
        dur = _probe_duration(merged)
        ok &= check("成片时长为各段剪辑后之和（约12s）", 11.0 <= dur <= 13.0, f"{dur:.2f}s")
        r = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error',
                            '-select_streams', 'v:0', '-show_entries',
                            'stream=codec_name', '-of', 'csv=p=0', str(merged)],
                           capture_output=True, text=True)
        ok &= check("成片为 H.265 编码", (r.stdout or '').strip() == 'hevc',
                    (r.stdout or '').strip())
    ok &= check("非首段视频不再单独输出转码文件",
                not (outdir / "BBB-002.mp4").exists() and not (outdir / "CCC-003.mp4").exists(),
                str([p.name for p in outdir.iterdir()]))
    ok &= check("无临时片段残留",
                not [p for p in outdir.iterdir() if p.name.startswith('.')],
                str([p.name for p in outdir.iterdir()]))
    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def _probe_size(path) -> tuple:
    """读取输出视频的宽高"""
    fp = shutil.which('ffprobe') or 'ffprobe'
    r = subprocess.run([fp, '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                        str(path)], capture_output=True, text=True)
    try:
        w, h = (r.stdout or '').strip().split(',')
        return int(w), int(h)
    except Exception:
        return (-1, -1)


def _crop_yavg(path, x_frac: float, w_frac: float) -> float:
    """取画面某一竖条的亮度均值（用于判断黑边/是否有画面内容）"""
    # signalstats 需配合 metadata=mode=print 才会把 YAVG 打印出来（且为 info 级）
    r = subprocess.run([FFMPEG, '-loglevel', 'info', '-i', str(path),
                        '-vf', f'crop=iw*{w_frac}:ih:iw*{x_frac}:0,signalstats,'
                               f'metadata=mode=print',
                        '-f', 'null', '-'], capture_output=True, text=True, timeout=120)
    m = re.findall(r'YAVG=([\d.]+)', (r.stderr or '') + (r.stdout or ''))
    return float(m[-1]) if m else -1.0


def test_auto_orient_resolution():
    """[19] 竖屏自动适配分辨率 + 按原比例缩放后自动补黑边"""
    print("\n[19] 横屏/竖屏自动适配分辨率（黑边填充）")
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_orient_"))
    outdir = root / "out"
    outdir.mkdir()

    def gen(name, w, h, dur=4):
        p = root / name
        subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', f'testsrc=size={w}x{h}:rate=25', '-t', str(dur),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(p)],
                       capture_output=True, check=True)
        return p

    land = gen("land.mp4", 1920, 1080)
    port = gen("port.mp4", 1080, 1920)
    s43 = gen("s43.mp4", 640, 480)

    def run(src, w, h, resolution="1280x720", auto_orient=True,
            portrait_res="自动(按源比例)", aspect="保持原比例", name=None):
        out = outdir / (name or (src.stem + ".mp4"))
        t = TranscodeTask(src=str(src), start=0.0, end=3.0,
                          src_w=w, src_h=h, fps=25.0,
                          resolution=resolution, aspect=aspect, bitrate=600,
                          save_mode="custom", save_dir=str(outdir), out_ext="mp4",
                          subtitle_mode="none", keep_quality=False,
                          auto_orient=auto_orient, portrait_res=portrait_res)
        t.out_path = str(out)
        t._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
        res = HeadlessTranscoder(FFMPEG, log=lambda m: None).run(t)
        return res, out

    # 1) 横屏：沿用设定分辨率 1280x720
    res, out = run(land, 1920, 1080)
    check("横屏源沿用设定分辨率 1280x720",
          res.get('status') == 'success' and _probe_size(out) == (1280, 720),
          f"{res.get('status')} {_probe_size(out)}")

    # 2) 竖屏 1080x1920（9:16）：按源比例取竖屏 720P → 720x1280，无需黑边
    res, out = run(port, 1080, 1920, name="port_auto.mp4")
    check("竖屏 9:16 源按源比例自动取 720x1280",
          res.get('status') == 'success' and _probe_size(out) == (720, 1280),
          f"{res.get('status')} {_probe_size(out)}")
    if out.exists():
        check("720x1280 时左右无黑边（画面填满）",
              _crop_yavg(out, 0.0, 0.03) > 40,
              f"左边缘亮度 {_crop_yavg(out, 0.0, 0.03):.1f}")

    # 3) 竖屏目标固定 720x1280（与源一致，无黑边）
    res, out = run(port, 1080, 1920, portrait_res="720x1280", name="port_1280.mp4")
    check("竖屏目标固定 720x1280",
          res.get('status') == 'success' and _probe_size(out) == (720, 1280),
          f"{res.get('status')} {_probe_size(out)}")

    # 4) 竖屏目标固定 720x1080（2:3 框，9:16 源 → 左右补黑边）
    res, out = run(port, 1080, 1920, portrait_res="720x1080", name="port_1080.mp4")
    check("竖屏目标固定 720x1080（9:16 源左右补黑边）",
          res.get('status') == 'success' and _probe_size(out) == (720, 1080),
          f"{res.get('status')} {_probe_size(out)}")
    if out.exists():
        check("720x1080 时左右补黑边", 0 <= _crop_yavg(out, 0.0, 0.03) <= 25,
              f"左边缘 YAVG={_crop_yavg(out, 0.0, 0.03):.1f}")

    # 5) 关掉开关：回到固定 1280x720（旧行为）
    res, out = run(port, 1080, 1920, auto_orient=False, name="port_off.mp4")
    check("关闭开关后沿用 1280x720",
          res.get('status') == 'success' and _probe_size(out) == (1280, 720),
          f"{res.get('status')} {_probe_size(out)}")

    # 6) 4:3 源 → 1280x720：左右补黑边，画面不被拉伸
    res, out = run(s43, 640, 480, name="s43.mp4")
    check("4:3 源输出 1280x720（左右补黑边）",
          res.get('status') == 'success' and _probe_size(out) == (1280, 720),
          f"{res.get('status')} {_probe_size(out)}")
    if out.exists():
        left = _crop_yavg(out, 0.0, 0.03)
        center = _crop_yavg(out, 0.42, 0.16)
        check("左右边缘为黑边（亮度≈0）", 0 <= left <= 25, f"左边缘 YAVG={left:.1f}")
        check("中间是画面内容（非黑）", center > 40, f"中间 YAVG={center:.1f}")

    # 7) 横宽比优先（需求3）：先按比例缩放，再按分辨率规则定尺寸
    # 7a) 竖屏源 + 横宽比 1:1 → 先按 1:1 限定，再取竖屏 720P → 720x720
    res, out = run(port, 1080, 1920, aspect="1:1", name="port_1x1.mp4")
    check("竖屏源+横宽比1:1 → 720x720",
          res.get('status') == 'success' and _probe_size(out) == (720, 720),
          f"{res.get('status')} {_probe_size(out)}")
    # 7b) 横屏源 + 横宽比 9:16 → 先按 9:16 限定，再取 720P → 约 405x720
    #     （ffmpeg 会把奇数宽取整为偶数，故允许 404/405）
    res, out = run(land, 1920, 1080, aspect="9:16", name="land_9x16.mp4")
    _sz = _probe_size(out)
    check("横屏源+横宽比9:16 → 约405x720（内容按 9:16 比例，再限定 720P）",
          res.get('status') == 'success' and _sz[1] == 720 and 395 <= _sz[0] <= 410,
          f"{res.get('status')} {_sz}")
    # 7c) 竖屏源 + 横宽比 9:16（与源一致）→ 720x1280
    res, out = run(port, 1080, 1920, aspect="9:16", name="port_9x16.mp4")
    check("竖屏源+横宽比9:16 → 720x1280",
          res.get('status') == 'success' and _probe_size(out) == (720, 1280),
          f"{res.get('status')} {_probe_size(out)}")

    # 8) 工作流步骤路径：竖屏源 → 自动 720x1280
    from workflow.models import WorkflowItem
    it = WorkflowItem(origin=str(port), uid="TEST-001")
    from workflow.steps import StepContext
    logs: list = []
    ctx = StepContext(log=logs.append, ffmpeg_path=FFMPEG)
    try:
        from workflow.steps import run_transcode
        produced = run_transcode(it, {
            "resolution": "1280x720", "aspect": "保持原比例", "bitrate": 600,
            "encoder": ENCODER_LABELS['cpu'], "save_mode": "custom",
            "save_dir": str(root / "WFOUT"), "out_ext": "mp4",
            "subtitle_mode": "none", "auto_orient": True,
            "portrait_res": "自动(按源比例)",
        }, ctx)
        check("工作流步骤同样自动适配为 720x1280",
              _probe_size(produced) == (720, 1280), str(_probe_size(produced)))
        check("日志记录实际输出分辨率",
              any("720x1280" in m for m in logs), str([m for m in logs if '分辨率' in m]))
    except Exception as e:
        check("工作流步骤路径", False, f"{type(e).__name__}: {e}")
    shutil.rmtree(root, ignore_errors=True)


def test_custom_resolution_and_orient():
    """[20] 自定义分辨率（手动宽高）+ 竖屏自动转换（如 1440x1080 ↔ 1080x1440）"""
    print("\n[20] 自定义分辨率 + 竖屏自动转换")
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_customres_"))
    outdir = root / "out"
    outdir.mkdir()

    def gen(name, w, h, dur=4):
        p = root / name
        subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', f'testsrc=size={w}x{h}:rate=25', '-t', str(dur),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(p)],
                       capture_output=True, check=True)
        return p

    land = gen("land.mp4", 1920, 1080)
    port = gen("port.mp4", 1080, 1440)   # 竖屏 1080x1440

    def run(src, w, h, resolution, auto_orient=True, name=None):
        out = outdir / (name or (src.stem + ".mp4"))
        t = TranscodeTask(src=str(src), start=0.0, end=3.0,
                          src_w=w, src_h=h, fps=25.0,
                          resolution=resolution, aspect="保持原比例", bitrate=600,
                          save_mode="custom", save_dir=str(outdir), out_ext="mp4",
                          subtitle_mode="none", keep_quality=False,
                          auto_orient=auto_orient, portrait_res="自动(按源比例)")
        t.out_path = str(out)
        t._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
        res = HeadlessTranscoder(FFMPEG, log=lambda m: None).run(t)
        return res, out

    # 1) 自定义 1440x1080 + 竖屏源 1080x1440 → 自动转为竖屏 1080x1440（正好吻合，无变形/黑边）
    res, out = run(port, 1080, 1440, "1440x1080", name="custom_port.mp4")
    check("自定义1440x1080 + 竖屏源 → 自动转 1080x1440",
          res.get('status') == 'success' and _probe_size(out) == (1080, 1440),
          f"{res.get('status')} {_probe_size(out)}")

    # 2) 自定义 1440x1080 + 横屏源 1920x1080 → 沿用 1440x1080
    res, out = run(land, 1920, 1080, "1440x1080", name="custom_land.mp4")
    check("自定义1440x1080 + 横屏源 → 1440x1080",
          res.get('status') == 'success' and _probe_size(out) == (1440, 1080),
          f"{res.get('status')} {_probe_size(out)}")

    # 3) 关闭适配：竖屏源仍按 1440x1080（非1:1源会有黑边，但尺寸正确）
    res, out = run(port, 1080, 1440, "1440x1080", auto_orient=False, name="custom_off.mp4")
    check("关闭适配后竖屏源仍按 1440x1080",
          res.get('status') == 'success' and _probe_size(out) == (1440, 1080),
          f"{res.get('status')} {_probe_size(out)}")

    # 4) 工作流步骤路径：自定义分辨率透传
    from workflow.models import WorkflowItem
    from workflow.steps import StepContext, run_transcode
    it = WorkflowItem(origin=str(port), uid="TEST-020")
    logs: list = []
    ctx = StepContext(log=logs.append, ffmpeg_path=FFMPEG)
    produced = run_transcode(it, {
        "resolution": "1440x1080", "aspect": "保持原比例", "bitrate": 600,
        "encoder": ENCODER_LABELS['cpu'], "save_mode": "custom",
        "save_dir": str(root / "WFOUT2"), "out_ext": "mp4",
        "subtitle_mode": "none", "auto_orient": True,
        "portrait_res": "自动(按源比例)",
    }, ctx)
    check("工作流步骤自定义分辨率自动转竖屏 1080x1440",
          _probe_size(produced) == (1080, 1440), str(_probe_size(produced)))
    shutil.rmtree(root, ignore_errors=True)


def test_append_uid_collision():
    """[21] 拖入/追加同名（同番号）视频时 UID 不与已有条目撞号，列表不崩溃"""
    print("\n[21] 追加导入 UID 去重（避免列表 iid 重复崩溃）")
    from workflow.models import dedup_uids
    from workflow_tab import WorkflowTab

    # 1) 纯函数：追加时基于已有 uid 集合去重
    out = dedup_uids(["ABC-001", "ABC-001"], existing={"ABC-001"})
    check("dedup_uids 追加去重（base 已被占用 → -2/-3）",
          out == ["ABC-001-2", "ABC-001-3"], str(out))

    # 2) 真实控件：先放入 DTW-030.mp4（dir1），再追加同名 DTW-030.mp4（dir2）
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.withdraw()

    class _Ctrl:
        def save_config(self, *a, **k): pass
        def get_config(self, *a, **k): return {}
        def log(self, m): pass

    tab = WorkflowTab(ttk.Notebook(root), _Ctrl())
    d1 = os.path.join(tempfile.gettempdir(), "wf_append_a")
    d2 = os.path.join(tempfile.gettempdir(), "wf_append_b")
    os.makedirs(d1, exist_ok=True); os.makedirs(d2, exist_ok=True)
    f1 = os.path.join(d1, "DTW-030.mp4")
    f2 = os.path.join(d2, "DTW-030.mp4")
    for f in (f1, f2):
        with open(f, "wb") as fh:
            fh.write(b"\x00\x00\x00\x00")  # 占位文件，无需真实视频

    try:
        tab._set_items([f1])                      # 首次导入
        uids1 = [i.uid for i in tab.items]
        tab._set_items([f2], append=True)         # 追加（同名 → 应去重，不崩溃）
        uids2 = [i.uid for i in tab.items]
        check("首次导入得到 DTW-030", uids1 == ["DTW-030"], str(uids1))
        check("追加后两条 uid 唯一", len(set(uids2)) == 2 and uids2[1] != "DTW-030",
              str(uids2))
        # 列表 iid 必须唯一（否则 insert 会抛 TclError）
        check("列表行 iid 无重复", len(set(tab.tree.get_children())) == len(tab.items))
    finally:
        root.destroy()
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


def test_resolution_color_tags():
    """[22] 清单“分辨率”列按标准/特殊着色（标准绿、特殊红）"""
    print("\n[22] 分辨率着色标签")
    import tkinter as tk
    from tkinter import ttk
    from workflow.models import WorkflowItem
    from workflow_tab import WorkflowTab, STANDARD_RESOLUTIONS

    check("STANDARD_RESOLUTIONS 含 1920x1080 与 1280x720",
          (1920, 1080) in STANDARD_RESOLUTIONS and (1280, 720) in STANDARD_RESOLUTIONS,
          str(len(STANDARD_RESOLUTIONS)))

    root = tk.Tk(); root.withdraw()

    class _Ctrl:
        def save_config(self, *a, **k): pass
        def get_config(self, *a, **k): return {}
        def log(self, m): pass

    tab = WorkflowTab(ttk.Notebook(root), _Ctrl())
    # 手工构造不同分辨率的条目（绕过 ffprobe 探测）
    tab.items = [
        WorkflowItem(uid="S1", origin=r"c:\a\v1.mp4", current=r"c:\a\v1.mp4",
                     width=1920, height=1080),   # 标准（横）
        WorkflowItem(uid="S2", origin=r"c:\a\v2.mp4", current=r"c:\a\v2.mp4",
                     width=1080, height=1920),   # 标准（竖）
        WorkflowItem(uid="X1", origin=r"c:\a\v3.mp4", current=r"c:\a\v3.mp4",
                     width=960, height=1264),    # 特殊/裁剪
        WorkflowItem(uid="N1", origin=r"c:\a\v4.mp4", current=r"c:\a\v4.mp4",
                     width=0, height=0),         # 未知（未探测）
    ]
    tab._refresh_tree()
    tags = {uid: tab.tree.item(uid, "tags") for uid in ("S1", "S2", "X1", "N1")}
    check("标准横 1920x1080 → res_std", "res_std" in tags["S1"], str(tags["S1"]))
    check("标准竖 1080x1920 → res_std", "res_std" in tags["S2"], str(tags["S2"]))
    check("特殊 960x1264 → res_special", "res_special" in tags["X1"], str(tags["X1"]))
    check("未知分辨率 → 无着色标签",
          "res_std" not in tags["N1"] and "res_special" not in tags["N1"],
          str(tags["N1"]))
    root.destroy()


def test_stall_watchdog():
    """[23] 转码进程卡死（无输出挂起）时看门狗应终止进程并判定失败，不永久阻塞"""
    print("\n[23] 卡死看门狗（避免进度卡 99% 不动）")
    import threading
    from video_grid_tab import VideoTranscodePanel

    p = VideoTranscodePanel.__new__(VideoTranscodePanel)
    p._lock = threading.Lock()
    p._procs = []
    p.stop_requested = False
    p.STALL_TIMEOUT = 2          # 测试用极短阈值
    logs = []
    p._log = lambda m, lv="info": logs.append((lv, str(m)))
    p._set_task_status = lambda *a, **k: None

    # 伪造的“ffmpeg”：先打印几条进度，随后永久挂起（模拟 NVENC/解码器卡死）
    hang = os.path.join(tempfile.gettempdir(), "wf_hang.py")
    with open(hang, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys, time\n"
            "for i in range(3):\n"
            "    print('out_time_ms=%d' % (i * 1000000)); sys.stdout.flush()\n"
            "time.sleep(1000)\n")
    ok = os.path.join(tempfile.gettempdir(), "wf_ok.py")
    with open(ok, "w", encoding="utf-8") as fh:
        fh.write("print('out_time_ms=1000000')\n")

    py = sys.executable

    class _Task:
        src = "fake.mp4"

    t0 = time.monotonic()
    res_hang = p._run_ffmpeg_stage([py, hang], 0.0, 100.0, 10.0, "测试-挂起", _Task())
    dt = time.monotonic() - t0
    check("卡死命令被看门狗终止并返回 False", res_hang is False, str(res_hang))
    check("看门狗在阈值内（<6s）终止，未永久阻塞", dt < 6.0, f"{dt:.1f}s")
    check("看门狗输出错误日志", any(lv == "error" for lv, _ in logs), str(logs[:2]))
    alive = any(proc.poll() is None for proc in p._procs)
    check("被终止的进程已退出（无残留）", not alive, str([pr.poll() for pr in p._procs]))

    res_ok = p._run_ffmpeg_stage([py, ok], 0.0, 100.0, 10.0, "测试-正常", _Task())
    check("正常命令返回 True", res_ok is True, str(res_ok))

    for f in (hang, ok):
        try:
            os.remove(f)
        except Exception:
            pass


def test_resolution_modes():
    """[24] 分辨率四种模式：预设/自定义/固定宽高/固定长边短边 的 UI 序列化与计算"""
    print("\n[24] 分辨率四种模式")
    import tkinter as tk
    from tkinter import ttk
    from workflow.models import WorkflowStep, STEP_TRANSCODE
    from workflow_tab import WorkflowTab
    from video_grid_tab import VideoTranscodePanel, TranscodeTask

    # ---- UI 层：apply/load 往返 ----
    root = tk.Tk(); root.withdraw()

    class _Ctrl:
        def save_config(self, *a, **k): pass
        def get_config(self, *a, **k): return {}
        def log(self, m): pass

    tab = WorkflowTab(ttk.Notebook(root), _Ctrl())
    step = WorkflowStep(type=STEP_TRANSCODE, params={})
    tab._current_step = step
    tab._loading = False

    def apply():
        tab._apply_params()
        return step.params['resolution']

    # ① 预设固定分辨率
    tab.tc_mode.set("预设固定分辨率"); tab.tc_preset.set("1920x1080")
    check("预设 → '1920x1080'", apply() == "1920x1080", apply())
    # ② 自定义（含自动一侧）
    tab.tc_mode.set("自定义分辨率")
    tab.tc_custom_w.set("自动"); tab.tc_custom_h.set("1280")
    check("自定义 高=1280 宽=自动 → 'auto x 1280'", apply() == "auto x 1280", apply())
    tab.tc_custom_w.set("1920"); tab.tc_custom_h.set("自动")
    check("自定义 宽=1920 高=自动 → '1920 x auto'", apply() == "1920 x auto", apply())
    tab.tc_custom_w.set("1440"); tab.tc_custom_h.set("1080")
    check("自定义 宽高都填 → '1440x1080'", apply() == "1440x1080", apply())
    tab.tc_custom_w.set("自动"); tab.tc_custom_h.set("自动")
    check("自定义 宽高都自动 → 回退 '1280x720'", apply() == "1280x720", apply())
    # ③ 固定宽或高
    tab.tc_mode.set("固定宽或高"); tab.tc_fixed_side.set("宽"); tab.tc_fixed_val.set("1280")
    check("固定宽=1280 → '1280 x auto'", apply() == "1280 x auto", apply())
    tab.tc_fixed_side.set("高"); tab.tc_fixed_val.set("720")
    check("固定高=720 → 'auto x 720'", apply() == "auto x 720", apply())
    # ④ 固定长边/短边
    tab.tc_mode.set("固定长边/短边"); tab.tc_edge.set("长边"); tab.tc_edge_val.set("1280")
    check("固定长边=1280 → 'long:1280'", apply() == "long:1280", apply())
    tab.tc_edge.set("短边"); tab.tc_edge_val.set("720")
    check("固定短边=720 → 'short:720'", apply() == "short:720", apply())
    check("_parse_custom_dim 解析 '自动'/''/非法 → None",
          (WorkflowTab._parse_custom_dim("自动") is None
           and WorkflowTab._parse_custom_dim("") is None
           and WorkflowTab._parse_custom_dim("abc") is None), "")

    # load 回显：四种模式
    def load_back(res, expect):
        step.params['resolution'] = res
        tab._load_params(step)
        check(f"load '{res}' → 模式/值正确", expect(tab),
              f"mode={tab.tc_mode.get()} "
              f"w={tab.tc_custom_w.get()}/{tab.tc_custom_h.get()} "
              f"side={tab.tc_fixed_side.get()}/{tab.tc_fixed_val.get()} "
              f"edge={tab.tc_edge.get()}/{tab.tc_edge_val.get()}")
    load_back("auto x 1280",
              lambda t: t.tc_mode.get() == "固定宽或高" and t.tc_fixed_side.get() == "高"
              and t.tc_fixed_val.get() == "1280")
    load_back("1920 x auto",
              lambda t: t.tc_mode.get() == "固定宽或高" and t.tc_fixed_side.get() == "宽"
              and t.tc_fixed_val.get() == "1920")
    load_back("long:1280",
              lambda t: t.tc_mode.get() == "固定长边/短边" and t.tc_edge.get() == "长边"
              and t.tc_edge_val.get() == "1280")
    load_back("short:720",
              lambda t: t.tc_mode.get() == "固定长边/短边" and t.tc_edge.get() == "短边"
              and t.tc_edge_val.get() == "720")
    load_back("1440x1080",
              lambda t: t.tc_mode.get() == "自定义分辨率"
              and t.tc_custom_w.get() == "1440" and t.tc_custom_h.get() == "1080")
    load_back("1920x1080",
              lambda t: t.tc_mode.get() == "预设固定分辨率" and t.tc_preset.get() == "1920x1080")
    root.destroy()

    # ---- 计算层：_resolve_target_size ----
    p = VideoTranscodePanel.__new__(VideoTranscodePanel)

    def resolve(src_w, src_h, resolution):
        t = TranscodeTask(src="x", src_w=src_w, src_h=src_h,
                          resolution=resolution, aspect="保持原比例")
        return p._resolve_target_size(t)

    # 固定宽/高（原片比例）
    check("auto x 720（源1920x1080）→ (1280,720)",
          resolve(1920, 1080, "auto x 720") == (1280, 720),
          str(resolve(1920, 1080, "auto x 720")))
    check("1280 x auto（源1080x1920）→ (1280,2276)",
          resolve(1080, 1920, "1280 x auto") == (1280, 2276),
          str(resolve(1080, 1920, "1280 x auto")))
    # 固定长边/短边：方向随原片横竖自动取宽或高（含竖屏适配）
    check("long:1280（源1920x1080 横屏）→ (1280,720)",
          resolve(1920, 1080, "long:1280") == (1280, 720),
          str(resolve(1920, 1080, "long:1280")))
    check("long:1280（源1080x1920 竖屏）→ (720,1280)",
          resolve(1080, 1920, "long:1280") == (720, 1280),
          str(resolve(1080, 1920, "long:1280")))
    check("short:720（源1920x1080 横屏）→ (1280,720)",
          resolve(1920, 1080, "short:720") == (1280, 720),
          str(resolve(1920, 1080, "short:720")))
    check("short:720（源1080x1920 竖屏）→ (720,1280)",
          resolve(1080, 1920, "short:720") == (720, 1280),
          str(resolve(1080, 1920, "short:720")))
    # 标准分辨率不受影响
    check("1280x720 仍返回 (1280,720)",
          resolve(1920, 1080, "1280x720") == (1280, 720),
          str(resolve(1920, 1080, "1280x720")))


# ======================================================================
def test_keyframe_aligned_seek():
    """[25] AVI 索引不可靠（非关键帧被标成关键帧）时，开头约 1 秒花屏的修复"""
    print("\n[25] 关键帧对齐 seek（AVI 坏索引 → 开头花屏修复）")
    from PIL import Image, ImageChops
    from workflow.steps import HeadlessTranscoder
    from video_grid_tab import VideoTranscodePanel, TranscodeTask
    from workflow.models import ENCODER_LABELS

    root = Path(tempfile.mkdtemp(prefix="wf_kfseek_"))
    outdir = root / "out"
    outdir.mkdir()

    def mean_diff(a: Path, b: Path) -> float:
        """两幅图平均像素差（0~255）；花屏（绿块）通常 >80，正常内容 <20"""
        ia = Image.open(a).convert('RGB')
        ib = Image.open(b).convert('RGB')
        hist = ImageChops.difference(ia, ib).histogram()
        total = sum(v * (i % 256) for i, v in enumerate(hist))
        return total / (ia.size[0] * ia.size[1] * 3)

    def grab(src: Path, t: float, out: Path):
        subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-ss', str(t),
                        '-i', str(src), '-frames:v', '1', str(out)],
                       capture_output=True, check=True)

    p = VideoTranscodePanel.__new__(VideoTranscodePanel)
    p._ffprobe_path = shutil.which('ffprobe')     # 免去 tk 变量依赖，直接给探测缓存
    normal = root / "normal.mp4"
    subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'testsrc=size=320x240:rate=25', '-t', '6',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-preset', 'ultrafast', '-g', '250', str(normal)],
                   capture_output=True, check=True)

    # ---- 单元：_keyframe_aligned_seek ----
    coarse, fine = p._keyframe_aligned_seek(str(normal), 2.0)
    check("正常 mp4 start=2.0（GOP 内）→ 输入 seek 对齐 0.0 + 输出侧补 2.0",
          abs(coarse) < 0.04 and fine is not None and abs(fine - 2.0) <= 0.25,
          f"coarse={coarse:.3f} fine={fine}")
    coarse2, fine2 = p._keyframe_aligned_seek(str(normal), 0.0)
    check("start=0 → 不需要两级 seek", abs(coarse2) < 0.04 and fine2 is None,
          f"coarse={coarse2:.3f} fine={fine2}")

    # 造一个“索引撒谎”的 AVI：全部视频帧被标成关键帧（真实世界 Xvid AVI 常见）
    avi_ok = root / "xvid_ok.avi"
    subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'testsrc2=size=320x240:rate=25', '-t', '8',
                    '-c:v', 'mpeg4', '-q:v', '4', '-g', '25', str(avi_ok)],
                   capture_output=True, check=True)
    data = bytearray(avi_ok.read_bytes())
    i = data.find(b'idx1')
    pos = i + 8
    n = 0
    while pos + 16 <= len(data):
        if bytes(data[pos:pos + 4]) == b'00dc':
            flags = int.from_bytes(data[pos + 4:pos + 8], 'little')
            data[pos + 4:pos + 8] = (flags | 0x10).to_bytes(4, 'little')
            n += 1
        pos += 16
    avi = root / "xvid_badidx.avi"
    avi.write_bytes(bytes(data))
    check("已伪造 AVI 索引（全部视频帧标为关键帧）", n > 100, f"{n} 帧")

    coarse3, fine3 = p._keyframe_aligned_seek(str(avi), 3.5)
    check("坏索引 AVI start=3.5 → 输入 seek 对齐真实关键帧 ~3.0",
          fine3 is not None and abs(coarse3 - 3.0) <= 0.25
          and abs(coarse3 + fine3 - 3.5) < 1e-6,
          f"coarse={coarse3:.3f} fine={fine3}")

    # ---- 端到端：重编码路径 ----
    # 复现：单级输入 seek 按坏索引落在 GOP 中间 → 成片开头花屏；
    # 修复后：两级 seek 从真实关键帧解码 → 首帧即设定起点画面
    grab(avi_ok, 3.5, root / "ref35.png")
    out = outdir / "reenc.mp4"
    t = TranscodeTask(src=str(avi), start=3.5, end=7.0,
                      src_w=320, src_h=240, fps=25.0,
                      resolution="320x240", aspect="保持原比例", bitrate=600,
                      save_mode="custom", save_dir=str(outdir), out_ext="mp4",
                      subtitle_mode="none", keep_quality=False)
    t.out_path = str(out)
    t._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
    res = HeadlessTranscoder(FFMPEG, log=lambda m: None).run(t)
    ok = res.get('status') == 'success' and out.exists()
    check("坏索引 AVI 重编码成功", ok, str(res.get('status')))
    if ok:
        grab(out, 0.0, root / "got0.png")
        d = mean_diff(root / "got0.png", root / "ref35.png")
        check("输出首帧 = 设定起点 3.5s 的画面（无花屏）", d < 30, f"像素差 {d:.1f}")

    # ---- 端到端：流拷贝路径（保持原分辨率）----
    # 索引撒谎时按索引 seek 会从 GOP 中间拷贝 → 开头花屏；
    # 修复后拷贝起点对齐到真实关键帧 3.0s，首帧干净
    out2 = outdir / "copy.mp4"
    t2 = TranscodeTask(src=str(avi), start=3.5, end=7.0,
                       src_w=320, src_h=240, fps=25.0,
                       resolution="保持原分辨率", aspect="保持原比例", bitrate=600,
                       save_mode="custom", save_dir=str(outdir), out_ext="mp4",
                       subtitle_mode="none", keep_quality=True)
    t2.out_path = str(out2)
    t2._encoder_label = ENCODER_LABELS['cpu']          # noqa: SLF001
    res2 = HeadlessTranscoder(FFMPEG, log=lambda m: None).run(t2)
    ok2 = res2.get('status') == 'success' and out2.exists()
    check("坏索引 AVI 流拷贝成功", ok2, str(res2.get('status')))
    if ok2:
        grab(avi_ok, 3.0, root / "ref30.png")
        grab(out2, 0.0, root / "gotc.png")
        d2 = mean_diff(root / "gotc.png", root / "ref30.png")
        check("流拷贝首帧 = 真实关键帧 3.0s 的画面（无花屏）", d2 < 30,
              f"像素差 {d2:.1f}")


# ======================================================================
def test_transcode_source_dir_rename_safety():
    """[26] 转码输出到原文件夹 + 后续改名步骤：不得覆盖/删除原始视频文件"""
    print("\n[26] 转码到原文件夹 + 改名（防覆盖原片）")
    from workflow.models import (WorkflowItem, WorkflowStep, WorkflowJob,
                                  ENCODER_LABELS)
    from workflow.steps import StepContext, run_transcode, run_rename
    from workflow.executor import WorkflowExecutor

    root = Path(tempfile.mkdtemp(prefix="wf_safe_"))
    folder = root / "src"
    folder.mkdir()
    orig = folder / "SSIS-026.mp4"
    make_video(orig, seconds=3)          # 原始视频（mp4）

    # 转码参数：输出到原文件夹、扩展名同原片（最易触发原地替换），软编码、整段输出
    tc_params = {
        "trim_mode": "none", "resolution": "320x240", "bitrate": 600,
        "encoder": ENCODER_LABELS['cpu'], "save_mode": "source",
        "out_ext": "mp4", "subtitle_mode": "none", "keep_quality": False,
    }

    # ---- A) 输出到原文件夹、不删除源文件、无改名步骤：原片保留，新文件加后缀共存 ----
    it_a = WorkflowItem(origin=str(orig), current=str(orig), uid="SSIS-026")
    ctx_a = StepContext(log=lambda m: None, ffmpeg_path=FFMPEG)
    ctx_a.rename_after_transcode = False
    prod_a = run_transcode(it_a, dict(tc_params), ctx_a)   # tc_params 未设 del_src → False
    check("不删除源文件时同文件夹转码：原始视频保留（未被覆盖/删除）",
          orig.exists(), f"orig.exists={orig.exists()}")
    check("不删除源文件时新文件按原文件名加后缀共存（SSIS-026_转码.mp4）",
          Path(prod_a).name == "SSIS-026_转码.mp4", f"prod={Path(prod_a).name}")
    # 清理本次产物，避免影响后续子测试
    try:
        Path(prod_a).unlink()
    except OSError:
        pass

    # 重置：再生成一个原始片用于后续测试
    make_video(orig, seconds=3)

    # ---- B) 新组合（转码到原文件夹 + 后续改名步骤）：原片必须保留 ----
    it_b = WorkflowItem(origin=str(orig), current=str(orig), uid="SSIS-026")
    ctx_b = StepContext(log=lambda m: None, ffmpeg_path=FFMPEG)
    ctx_b.rename_after_transcode = True     # 由执行器在转码步前标记
    prod_b = run_transcode(it_b, dict(tc_params), ctx_b)
    check("转码到原文件夹+后续改名：原始视频未被删除",
          orig.exists(), f"orig.exists={orig.exists()}")
    check("转码到原文件夹+后续改名：成片为独立新文件（非原地替换）",
          Path(prod_b).name != "SSIS-026.mp4" and prod_b != str(orig),
          f"prod={Path(prod_b).name}")
    # 再用改名步骤按新规则命名，确认原片全程不被覆盖
    it_b.current = str(prod_b)
    it_b.origin = str(orig)
    new_b = run_rename(it_b, {"name_template": "{uid}_cut", "with_companions": False}, ctx_b)
    check("改名后原片仍存在（未被覆盖）", orig.exists(), f"orig.exists={orig.exists()}")
    check("改名结果按新规则（{uid}_cut）命名",
          Path(new_b).name == "SSIS-026_cut.mp4", Path(new_b).name)

    # ---- C) 改名结果恰好等于原始文件名：自动加数字后缀，而非覆盖原片 ----
    other = folder / "OTHER-001.mp4"
    make_video(other, seconds=3)
    it_c = WorkflowItem(origin=str(orig), current=str(other), uid="C026")
    ctx_c = StepContext(log=lambda m: None, ffmpeg_path=FFMPEG)
    # 模板渲染为“SSIS-026”（与原片同名）→ 应与原始文件冲突，加序号而非替换
    new_c = run_rename(it_c, {"name_template": "SSIS-026", "with_companions": False}, ctx_c)
    check("改名结果与原片同名 → 自动加数字后缀",
          Path(new_c).name in ("SSIS-026_1.mp4", "SSIS-026_2.mp4"), Path(new_c).name)
    check("原片未被改名覆盖", orig.exists() and not Path(new_c).samefile(orig),
          f"orig={orig.exists()} new={Path(new_c).name}")

    # ---- D) 端到端：执行器串联（转码→原文件夹 + 改名）→ 原片保留 ----
    make_video(orig, seconds=3)
    eo = folder / "EO-026.mp4"
    make_video(eo, seconds=3)
    items = build_items([str(eo)])
    job = WorkflowJob(name="安全测试", steps=[
        WorkflowStep(STEP_TRANSCODE, params=tc_params),
        WorkflowStep(STEP_RENAME, params={"name_template": "{uid}_final",
                                          "with_companions": False}),
    ])
    # 用真实执行器跑（串行），验证 rename_after_transcode 标记生效
    exe = WorkflowExecutor(job, items, ffmpeg_path=FFMPEG,
                           handlers={STEP_TRANSCODE: run_transcode,
                                     STEP_RENAME: run_rename})
    exe.run()
    check("端到端：原片 EO-026.mp4 在处理后仍然存在",
          eo.exists(), f"eo.exists={eo.exists()}")
    check("端到端：生成了按改名规则命名的新文件",
          (folder / "EO-026_final.mp4").exists(), str(list(folder.glob('*'))))

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_item_status_live_updates():
    """[28] 运行中视频清单“状态”列实时显示阶段（剪辑中/移动中/改名中/翻译 N/M）"""
    print("\n[28] 视频清单实时状态显示")
    from workflow.models import ENCODER_LABELS
    from workflow.steps import run_transcode, run_move, run_rename

    root = Path(tempfile.mkdtemp(prefix="wf_status_"))
    folder = root / "src"
    folder.mkdir()
    outdir = root / "out"
    outdir.mkdir()

    def collect(job_steps, handlers, file):
        src = folder / file
        make_video(src, seconds=3)
        got = []
        items = build_items([str(src)])
        job = WorkflowJob(name="状态测试", steps=job_steps)
        ex = WorkflowExecutor(job, items, ffmpeg_path=FFMPEG,
                              on_log=lambda m: None,
                              on_item=lambda it: got.append(it.status),
                              on_progress=lambda p, t="": None,
                              handlers=handlers)
        ex.run()
        return got

    # A) 移动 + 改名：应实时出现“移动中”“改名中”，最终“已完成”
    got = collect(
        [WorkflowStep(STEP_MOVE, params={"target_dir": str(outdir)}),
         WorkflowStep(STEP_RENAME, params={"name_template": "{uid}_ren",
                                           "with_companions": False})],
        {STEP_MOVE: run_move, STEP_RENAME: run_rename}, "ST-028.mp4")
    check("移动步骤实时显示“移动中”", "移动中" in got, str(got))
    check("改名步骤实时显示“改名中”", "改名中" in got, str(got))
    check("全部步骤完成后状态为“已完成”", bool(got) and got[-1] == "已完成", str(got))

    # B) 剪辑转码：应出现“剪辑中”起始阶段（及/或“剪辑 N%”），最终“已完成”
    tc_params = {"trim_mode": "none", "resolution": "320x240", "bitrate": 600,
                 "encoder": ENCODER_LABELS['cpu'], "save_mode": "custom",
                 "save_dir": str(outdir), "out_ext": "mp4",
                 "subtitle_mode": "none", "keep_quality": False}
    got2 = collect([WorkflowStep(STEP_TRANSCODE, params=tc_params)],
                   {STEP_TRANSCODE: run_transcode}, "TC-028.mp4")
    check("转码步骤实时显示“剪辑中”（或剪辑百分比）",
          any(s == "剪辑中" or (s or "").startswith("剪辑 ") for s in got2), str(got2))
    check("转码完成后状态复位为“已完成”", bool(got2) and got2[-1] == "已完成", str(got2))

    # C) ctx.status 通道（字幕逐条翻译同款机制）：桩 handler 写入“翻译 3/20”
    def fake_sub(item, params, ctx):
        ctx.status("翻译 3/20")
        return item.path

    got3 = collect([WorkflowStep(STEP_SUBTITLE, params={})],
                   {STEP_SUBTITLE: fake_sub}, "SUB-028.mp4")
    check("ctx.status 写入的“翻译 3/20”可到达清单状态列", "翻译 3/20" in got3, str(got3))

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_stop_interrupts_current_step():
    """[29] 任务进行中点“停止”：立即触发活动步骤的停止钩子并中断（不再等整步跑完）"""
    print("\n[29] 运行中停止立即中断当前步骤")
    flag = {"entered": False, "cancelled": 0}

    def slow_handler(item, params, ctx):
        # 模拟字幕步骤：注册停止钩子（生成器 cancel 同款机制）+ 协作式检查点循环
        ctx.set_stop_setter(lambda: flag.__setitem__("cancelled", flag["cancelled"] + 1))
        flag["entered"] = True
        deadline = time.time() + 10
        while not ctx.stopped() and time.time() < deadline:
            time.sleep(0.02)
        if ctx.stopped():
            raise StepStopped("已停止")
        return item.path

    root = Path(tempfile.mkdtemp(prefix="wf_stop_"))
    folder = root / "src"
    folder.mkdir()
    src = folder / "STOP-029.mp4"
    make_video(src, seconds=2)

    got = []
    items = build_items([str(src)])
    job = WorkflowJob(name="停止测试", steps=[WorkflowStep(STEP_MOVE, params={})])
    ex = WorkflowExecutor(job, items, ffmpeg_path=FFMPEG,
                          on_log=lambda m: None,
                          on_item=lambda it: got.append(it.status),
                          on_progress=lambda p, t="": None,
                          handlers={STEP_MOVE: slow_handler})
    th = threading.Thread(target=ex.run, daemon=True)
    th.start()
    deadline = time.time() + 10
    while not flag["entered"] and time.time() < deadline:
        time.sleep(0.02)
    check("慢步骤已开始执行", flag["entered"], f"entered={flag['entered']}")
    time.sleep(0.15)          # 确保 worker 已进入检查点循环
    ex.stop()
    th.join(timeout=15)
    check("停止钩子被立即调用（此前仅暂停中的 ctx 才会标记停止）",
          flag["cancelled"] == 1, f"cancelled={flag['cancelled']}")
    check("worker 线程已退出（未等 10s 超时）", not th.is_alive(), f"alive={th.is_alive()}")
    check("条目最终状态为“已停止”", bool(got) and got[-1] == "已停止", str(got))
    check("停止计数入账", ex.stats.get("stopped") == 1, str(ex.stats))

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_bitrate_below_keep():
    """[27] “低于设置值不修改”：原片码率高于设置值降到设置值，低于设置值保持原片"""
    print("\n[27] 低于设置值不修改（码率封顶/保持原片）")
    from workflow.models import WorkflowItem, ENCODER_LABELS
    from workflow.steps import StepContext, run_transcode

    fp = shutil.which('ffprobe') or FFMPEG.replace('ffmpeg', 'ffprobe')

    def probe_dur(p: str) -> float:
        out = subprocess.run([fp, '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'csv=p=0', str(p)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=30).stdout.decode('utf-8', 'replace').strip()
        try:
            return float(out)
        except ValueError:
            return 0.0

    def avg_br(p: str) -> int:
        """用「文件大小 / 时长」算平均码率（kbps），避免 ffprobe 码率标签与实测不符"""
        d = probe_dur(p)
        if d <= 0:
            return 0
        return int(Path(p).stat().st_size * 8 / (d * 1000))

    def make_src(name, br_k):
        # mandelbrot 细节极高，x264 实际码率会随 -b:v 明显变化（静帧 testsrc 则不会）
        p = root / name
        subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', 'mandelbrot=size=320x240:rate=25', '-t', '4',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-b:v',
                        f'{br_k}k', str(p)],
                       capture_output=True, check=True)
        return p

    root = Path(tempfile.mkdtemp(prefix="wf_belowkeep_"))
    folder = root / "src"
    folder.mkdir()
    hi = make_src("hi.mp4", 4000)     # 高码率源（远高于设置值 1500）
    lo = make_src("lo.mp4", 400)      # 低码率源（远低于设置值 1500）
    hi_br0, lo_br0 = avg_br(hi), avg_br(lo)
    check("源高码率明显 > 源低码率", hi_br0 > lo_br0 * 2, f"hi={hi_br0} lo={lo_br0}")
    check("源高码率 > 设置值", hi_br0 > 1500, f"hi={hi_br0}")
    check("源低码率 < 设置值", lo_br0 < 1500, f"lo={lo_br0}")

    setting = 1500
    base = dict(trim_mode="none", resolution="320x240", bitrate=setting,
                encoder=ENCODER_LABELS['cpu'], save_mode="custom",
                save_dir=str(folder), out_ext="mp4", subtitle_mode="none",
                keep_quality=False)

    def run_one(src, br_below):
        it = WorkflowItem(origin=str(src), current=str(src), uid="BK")
        ctx = StepContext(log=lambda m: None, ffmpeg_path=FFMPEG)
        params = dict(base)
        params['bitrate_below_keep'] = br_below
        produced = run_transcode(it, params, ctx)
        return avg_br(produced)

    # 勾选“低于设置值不修改”
    hi_out = run_one(hi, True)
    lo_out = run_one(lo, True)
    check("高码率源：勾选后降到设置值（封顶）",
          hi_out <= setting * 1.6 and hi_out < hi_br0 * 0.7,
          f"src={hi_br0} out={hi_out} set={setting}")
    check("低码率源：勾选后保持原片码率（未被拉高）",
          lo_out < setting * 0.9 and lo_out > 0,
          f"src={lo_br0} out={lo_out} set={setting}")

    # 不勾选（对照）：低码率源被强行拉到设置值
    lo_ctrl = run_one(lo, False)
    check("低码率源：不勾选时被拉到设置值（验证选项生效）",
          lo_ctrl > lo_br0 * 2, f"src={lo_br0} ctrl={lo_ctrl} set={setting}")

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def test_transcode_source_del_src_strategy():
    """[30] 转码输出到原文件夹：删除源→原地替换(文件名不变)；不删源→原文件名加后缀共存"""
    print("\n[30] 转码到原文件夹的两种文件名策略（删源 / 不删源）")
    from workflow.models import WorkflowItem, ENCODER_LABELS
    from workflow.steps import StepContext, run_transcode

    root = Path(tempfile.mkdtemp(prefix="wf_tcsrc_"))
    folder = root / "src"
    folder.mkdir()
    base = dict(trim_mode="none", resolution="320x240", bitrate=600,
                encoder=ENCODER_LABELS['cpu'], save_mode="source",
                out_ext="mp4", subtitle_mode="none", keep_quality=False)

    # ---- 不删除源文件：原片保留，新文件按原文件名加“_转码”后缀共存 ----
    orig1 = folder / "NC-030.mp4"
    make_video(orig1, seconds=3)
    it1 = WorkflowItem(origin=str(orig1), current=str(orig1), uid="NC-030")
    ctx1 = StepContext(log=lambda m: None, ffmpeg_path=FFMPEG)
    prod1 = run_transcode(it1, dict(base, del_src=False), ctx1)
    check("不删源：原始视频保留（未被覆盖/删除）",
          orig1.exists(), f"orig.exists={orig1.exists()}")
    check("不删源：新文件为原文件名加后缀（NC-030_转码.mp4）",
          Path(prod1).name == "NC-030_转码.mp4", Path(prod1).name)
    check("不删源：新文件与旧文件是两个不同文件",
          not Path(prod1).samefile(orig1), Path(prod1).name)

    # ---- 删除源文件：原地替换，文件名不变，旧文件被替换（无共存后缀文件）----
    orig2 = folder / "DL-030.mp4"
    make_video(orig2, seconds=3)
    it2 = WorkflowItem(origin=str(orig2), current=str(orig2), uid="DL-030")
    ctx2 = StepContext(log=lambda m: None, ffmpeg_path=FFMPEG)
    prod2 = run_transcode(it2, dict(base, del_src=True), ctx2)
    check("删源：成片沿用原文件名（DL-030.mp4）",
          Path(prod2).name == "DL-030.mp4", Path(prod2).name)
    check("删源：不存在带后缀的共存文件（已原地替换而非共存）",
          not (folder / "DL-030_转码.mp4").exists(),
          str(sorted(p.name for p in folder.glob('*'))))
    check("删源：成片即原始路径（原地替换）",
          os.path.normcase(os.path.abspath(str(prod2))) ==
          os.path.normcase(os.path.abspath(str(orig2))), Path(prod2).name)

    shutil.rmtree(root, ignore_errors=True)


# ======================================================================
def main():
    print("=" * 72)
    print("工作流串联模块自测")
    print("=" * 72)
    test_models()
    test_flow_a_move_transcode_move_subtitle()
    test_flow_b_subtitle_then_transcode()
    test_error_and_stop()
    test_rename_step()
    test_pause()
    test_parallel()
    test_subtitle_model_reuse()
    test_heavy_resource_serialized()
    test_legacy_name_template_cleaned()
    test_uid_learner_generalization()
    test_trim_precision_reencode()
    test_keep_bitrate()
    test_input_formats_to_hevc()
    test_merge_segments_with_subtitle_align()
    test_subtitle_first_segment_alignment()
    test_workflow_merge_step()
    test_auto_orient_resolution()
    test_custom_resolution_and_orient()
    test_append_uid_collision()
    test_resolution_color_tags()
    test_stall_watchdog()
    test_resolution_modes()
    test_keyframe_aligned_seek()
    test_transcode_source_dir_rename_safety()
    test_transcode_source_del_src_strategy()
    test_item_status_live_updates()
    test_stop_interrupts_current_step()
    test_bitrate_below_keep()

    total = len(_results)
    failed = [r for r in _results if not r[1]]
    print("\n" + "=" * 72)
    print(f"结果：{total - len(failed)}/{total} 项通过")
    for name, ok, detail in failed:
        print(f"  {FAIL} {name} —— {detail}")
    print("=" * 72)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
