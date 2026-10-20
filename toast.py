#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""右下角浮出式提醒（Toast 通知）—— 全程序共用

统一放在本模块中，避免各选项卡各自实现一份（原先只在 folder_check_tab
内部定义，其它选项卡想复用会牵连大量无关导入）。

用法:
    from toast import ToastNotifier
    ToastNotifier().notify(self.frame, "处理完成", "success")

level: 'info' | 'success' | 'warning' | 'error'
"""

import threading
import tkinter as tk


class ToastNotifier:
    """右下角浮出式提醒管理器（单例）

    以无边框、置顶的小窗口在主窗口右下角浮出提醒信息，自动消失；
    多条提醒会自下而上堆叠，用于替代频繁弹出的 messagebox 提示窗口。
    """
    _instance = None
    _lock = threading.Lock()

    _COLORS = {
        'info':    ('#2b2b2b', '#f2f2f2', '#4a90d9'),  # (背景, 文字, 边框/图标)
        'success': ('#1f5130', '#eafbe7', '#37a862'),
        'warning': ('#5a3d00', '#fff4d6', '#e0a200'),
        'error':   ('#5a1d1d', '#ffe3e3', '#e04848'),
    }
    _ICON = {'info': 'i', 'success': '√', 'warning': '!', 'error': 'x'}

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._toasts = []
        return cls._instance

    def notify(self, ref_widget, message, level='info', duration=3200):
        colors = self._COLORS.get(level, self._COLORS['info'])
        bg, fg, border = colors
        root = None
        try:
            root = ref_widget.winfo_toplevel()
        except Exception:
            root = None
        win = tk.Toplevel(root)
        try:
            win.withdraw()                       # 先隐藏，避免在屏幕左上角闪现
            win.wm_overrideredirect(True)
            try:
                # 注意：不用全局 '-topmost'，否则会盖住其它软件窗口；
                # 只需保持在主窗口之上（见 _keep_above）
                win.wm_attributes('-alpha', 0.96)
            except Exception:
                pass
            inner = tk.Frame(win, bg=bg, highlightbackground=border, highlightthickness=2)
            inner.pack(padx=0, pady=0)
            icon = self._ICON.get(level, self._ICON['info'])
            tk.Label(inner, text=icon, bg=bg, fg=border,
                     font=('Microsoft YaHei UI', 12, 'bold')).pack(
                     side='left', padx=(12, 6), pady=10)
            # 注意：padx/pady 必须传给 pack()，不能作为 Label 的控件选项（否则报 TclError）
            tk.Label(inner, text=message, bg=bg, fg=fg, justify='left',
                     font=('Microsoft YaHei UI', 10), wraplength=300).pack(
                     side='left', padx=(0, 12), pady=10)
            win.update_idletasks()
            self._toasts.append((win, root))
            self._relayout()
            win.deiconify()
            try:
                win.transient(root)      # 归属主窗口，不单独出现在任务栏
                win.lift(root)           # 只压在主窗口之上，不置顶全局
            except Exception:
                pass
            self._keep_above(win, root)
            win.after(duration, lambda w=win: self._close(w))
        except Exception:
            # 任何异常都不能留下无法关闭的“僵尸”浮窗
            try:
                win.destroy()
            except Exception:
                pass
            raise

    def _keep_above(self, win, root):
        """周期性把 toast 维持在主窗口之上（但不会盖住其它应用窗口）。

        主窗口被其它窗口覆盖时 toast 同样被覆盖；主窗口被点击/刷新后
        toast 重新浮到主窗口上方。不使用全局 -topmost，也不抢焦点。
        """

        def _tick():
            if not win.winfo_exists():
                return
            try:
                if root is not None and root.winfo_exists() \
                        and root.state() == 'normal':
                    win.lift(root)
            except Exception:
                pass
            win.after(500, _tick)

        win.after(500, _tick)

    def _relayout(self):
        """在主窗口右下角自下而上堆叠所有 toast，并保证不超出窗口边界"""
        try:
            offset = 14
            for win, root in self._toasts:
                if not win.winfo_exists():
                    continue
                # 使用请求尺寸（窗口可能尚未映射，winfo_width 会返回 1）
                w = win.winfo_reqwidth()
                h = win.winfo_reqheight()
                if root is not None and root.winfo_exists():
                    # 以主窗口在屏幕坐标系中的矩形为锚点
                    right = root.winfo_rootx() + root.winfo_width()
                    bottom = root.winfo_rooty() + root.winfo_height()
                    top = root.winfo_rooty()
                else:
                    # 回退：以屏幕为锚点
                    right = win.winfo_screenwidth()
                    bottom = win.winfo_screenheight()
                    top = 0
                x = right - w - 18
                y = bottom - h - 18 - offset
                # 不超出窗口上边界（堆叠过多时停靠在窗口顶部下方）
                if y < top + 18:
                    y = top + 18
                win.geometry(f"+{x}+{y}")
                offset += h + 8
        except Exception:
            pass

    def _close(self, win):
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass
        self._toasts = [(t, r) for (t, r) in self._toasts
                        if t is not win and t.winfo_exists()]
        self._relayout()


def show_toast(ref_widget, message, level='info', duration=None):
    """便捷函数：失败时静默降级（不抛异常打断业务流程）。"""
    if duration is None:
        duration = 5000 if level == 'error' else 3200
    try:
        ToastNotifier().notify(ref_widget, message, level, duration)
        return True
    except Exception:
        return False
