# -*- coding: utf-8 -*-
"""让本机进入睡眠 / 关机的可靠封装（多模块共用）

要点
====
Windows 的 ``SetSuspendState`` 需要 ``SE_SHUTDOWN_NAME`` 特权，而普通 Python
进程（以及它拉起的 ``rundll32``）默认并不持有该特权，导致直接执行
``rundll32.exe powrprof.dll,SetSuspendState 0,1,0`` 经常**静默失败**——
日志里写了“即将进入睡眠”，电脑却毫无反应（关机用 ``shutdown.exe`` 能成功，
是因为它自身会提权，形成“只有睡眠不灵”的假象）。

这里先给当前进程加上关机特权，再调用 ``SetSuspendState``；任何异常都回退到
``rundll32`` 方式，保证兼容性。
"""
import os
import sys


def enter_sleep() -> bool:
    """让本机进入睡眠（S3）。

    返回 ``True`` 表示已成功发起睡眠请求（电脑将转入睡眠）；
    ``False`` 表示发起失败（通常被系统策略 / 驱动阻止）。
    非 Windows 平台直接返回 ``False``。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        adv = ctypes.windll.advapi32
        k32 = ctypes.windll.kernel32

        tok = ctypes.c_void_p()
        # TOKEN_ADJUST_PRIVILEGES(0x20) | TOKEN_QUERY(0x8)
        if not k32.OpenProcessToken(k32.GetCurrentProcess(), 0x20 | 0x8,
                                    ctypes.byref(tok)):
            raise OSError("OpenProcessToken failed")

        luid = ctypes.c_ulonglong()
        adv.LookupPrivilegeValueA(
            None,
            ctypes.create_string_buffer(b"SeShutdownPrivilege"),
            ctypes.byref(luid),
        )

        class _LuidAttr(ctypes.Structure):
            _fields_ = [("Luid", ctypes.c_ulonglong), ("Attributes", ctypes.c_ulong)]

        class _TokenPrivileges(ctypes.Structure):
            _fields_ = [("PrivilegeCount", ctypes.c_ulong),
                        ("Privileges", _LuidAttr * 1)]

        # SE_PRIVILEGE_ENABLED(0x2)
        tp = _TokenPrivileges(1, (_LuidAttr(luid.value, 0x2),))
        if not adv.AdjustTokenPrivileges(tok, False, ctypes.byref(tp), 0, None, None):
            raise OSError("AdjustTokenPrivileges failed")

        # SetSuspendState(hibernate, forceCritical, disableWakeEvent)
        # 0,1,0 -> 睡眠（非休眠），强制，不禁用唤醒事件
        # 返回非 0 表示已发起睡眠；返回 0 表示调用失败（如仍缺特权）
        res = ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        return True if res else False
    except Exception:
        # 仅当提权/调用过程本身抛异常时，才回退到经典命令（无法确认结果）
        try:
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        except Exception:
            pass
        return True
