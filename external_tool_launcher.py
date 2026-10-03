#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 外部 Python 工具启动器：主窗口 tab 上方的按钮区 + 脚本配置窗口

"""
外部工具启动器
------------------------------------------------------------------
功能：
1. 在主窗口选项卡(tab)上方生成一条按钮区，每个按钮对应一个外部 Python 脚本
   （默认：D:\\软件编程\\python\\练习\\20260722-视频语音识别并翻译为字幕\\run.py）
2. 点击按钮即以后台子进程方式启动该脚本（窗体程序照常弹出）
3. 点击“管理脚本”打开配置窗口，可增删改：
   工具名称 / Python 解释器 / 脚本路径 / 工作目录 / 启动参数 / 是否显示控制台

配置保存在同目录 app_config.json 的 external_tools 分区。
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, Dict, List, Optional

CONFIG_FILE_NAME = "app_config.json"
CONFIG_SECTION = "external_tools"

# 默认外部脚本（语音识别并翻译为字幕工具的启动入口）
DEFAULT_SUBTITLE_SCRIPT = r"D:\软件编程\python\练习\20260722-视频语音识别并翻译为字幕\run.py"

CREATE_NEW_CONSOLE = 0x00000010  # Windows：新建控制台窗口，便于查看脚本输出与报错


# ---------------------------------------------------------------------------
# 配置读写（与 file_organize_tab / video_grid_tab 等共享 app_config.json，
# 写盘时先读取磁盘最新内容再只覆盖 external_tools 分区，避免覆盖其他模块配置）
# ---------------------------------------------------------------------------
def get_launcher_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE_NAME)


def _read_app_config() -> Dict[str, Any]:
    path = get_launcher_config_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[外部工具] 读取配置失败: {e}")
    return {}


def normalize_tool(item: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    """补全工具配置的字段，保证字段齐全（python 留空表示使用当前程序的解释器）"""
    name = str(item.get("name") or "").strip() or f"工具{index + 1}"
    return {
        "id": str(item.get("id") or f"tool_{index + 1}"),
        "name": name,
        "python": str(item.get("python") or ""),
        "script": str(item.get("script") or ""),
        "cwd": str(item.get("cwd") or ""),
        "args": str(item.get("args") or ""),
        "console": bool(item.get("console", True)),
    }


def _default_tools() -> List[Dict[str, Any]]:
    return [
        normalize_tool(
            {
                "id": "subtitle_tool",
                "name": "语音识别字幕",
                "python": "",  # 留空则使用当前程序的解释器
                "script": DEFAULT_SUBTITLE_SCRIPT,
                "cwd": "",
                "args": "",
                "console": True,
            },
            0,
        )
    ]


def load_tools() -> List[Dict[str, Any]]:
    """读取工具列表，没有配置时返回默认项"""
    section = _read_app_config().get(CONFIG_SECTION) or {}
    tools = section.get("tools") if isinstance(section, dict) else None
    if isinstance(tools, list) and tools:
        result = [normalize_tool(t, i) for i, t in enumerate(tools) if isinstance(t, dict)]
        if result:
            return result
    return _default_tools()


def save_tools(tools: List[Dict[str, Any]]) -> bool:
    """写回工具列表（只覆盖 external_tools 分区）"""
    try:
        data = _read_app_config()
        data[CONFIG_SECTION] = {"tools": tools}
        with open(get_launcher_config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        messagebox.showerror("错误", f"保存外部工具配置失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 顶部按钮区
# ---------------------------------------------------------------------------
class ExternalToolLauncher:
    """主窗口 tab 上方的外部工具按钮区"""

    def __init__(self, parent, controller=None):
        self.parent = parent
        self.controller = controller
        self.tools: List[Dict[str, Any]] = load_tools()
        self.running: Dict[str, subprocess.Popen] = {}
        self.buttons: Dict[str, ttk.Button] = {}
        self._dialog: Optional["ExternalToolConfigDialog"] = None

        self.frame = ttk.Frame(parent)
        self.status_var = tk.StringVar(value="")
        self._build()

    # ---------- 构建界面 ----------
    def _build(self):
        ttk.Label(self.frame, text="外部工具:").pack(side=tk.LEFT, padx=(0, 6))

        # 工具按钮容器（重建按钮时只清空这里）
        self.button_box = ttk.Frame(self.frame)
        self.button_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(self.frame, textvariable=self.status_var, foreground="#666666").pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        ttk.Button(self.frame, text="管理脚本…", command=self.open_dialog).pack(side=tk.RIGHT)

        self._refresh_buttons()

    def _refresh_buttons(self):
        for child in self.button_box.winfo_children():
            child.destroy()
        self.buttons.clear()

        if not self.tools:
            ttk.Label(self.button_box, text="（暂无工具，点击“管理脚本”添加）",
                      foreground="#888888").pack(side=tk.LEFT)
            return

        for tool in self.tools:
            btn = ttk.Button(
                self.button_box,
                text=tool.get("name", "工具"),
                command=lambda t=tool: self.launch(t),
            )
            btn.pack(side=tk.LEFT, padx=(0, 6))
            self.buttons[tool["id"]] = btn
            # 已在该会话中启动且仍在运行的，恢复“运行中”状态
            proc = self.running.get(tool["id"])
            if proc is not None and proc.poll() is None:
                self._set_running(tool, True)

    # ---------- 工具方法 ----------
    def log(self, message: str):
        try:
            if self.controller is not None and hasattr(self.controller, "log"):
                self.controller.log(message)
                return
        except Exception:
            pass
        print(message)

    def reload(self):
        """配置变更后重新载入并刷新按钮"""
        self.tools = load_tools()
        self._refresh_buttons()

    def _set_running(self, tool: Dict[str, Any], running: bool):
        btn = self.buttons.get(tool.get("id"))
        if btn is None:
            return
        name = tool.get("name", "工具")
        if running:
            btn.config(text=f"{name}(运行中)", state=tk.DISABLED)
        else:
            btn.config(text=name, state=tk.NORMAL)

    def launch(self, tool: Dict[str, Any]):
        """启动外部 Python 脚本（子进程，不阻塞本程序）"""
        tid = tool.get("id", "")
        proc = self.running.get(tid)
        if proc is not None and proc.poll() is None:
            messagebox.showinfo("提示", f"[{tool.get('name', '工具')}] 正在运行，无需重复启动")
            return

        script = (tool.get("script") or "").strip()
        if not script or not os.path.isfile(script):
            if messagebox.askyesno(
                "脚本不存在",
                f"未找到脚本：\n{script or '(未设置)'}\n\n是否现在打开配置窗口修改路径？",
            ):
                self.open_dialog()
            return

        python_exe = (tool.get("python") or "").strip() or sys.executable
        if not os.path.isfile(python_exe):
            messagebox.showerror("错误", f"Python 解释器不存在：\n{python_exe}")
            return

        # 启动参数
        args_text = (tool.get("args") or "").strip()
        try:
            extra_args = shlex.split(args_text, posix=(os.sep == "/"))
        except ValueError:
            extra_args = args_text.split()

        cmd = [python_exe, "-u", script] + extra_args

        # 工作目录：未指定时用脚本所在目录
        cwd = (tool.get("cwd") or "").strip() or os.path.dirname(os.path.abspath(script))
        if not os.path.isdir(cwd):
            cwd = os.path.dirname(os.path.abspath(script))

        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        creationflags = 0
        if tool.get("console", True) and sys.platform == "win32":
            creationflags = CREATE_NEW_CONSOLE

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                creationflags=creationflags,
                close_fds=True,
            )
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动 {script}\n{e}")
            self.log(f"[外部工具] 启动失败: {e}")
            return

        self.running[tid] = proc
        self._set_running(tool, True)
        self.status_var.set(f"已启动：{tool.get('name', '工具')} (PID {proc.pid})")
        self.log(f"[外部工具] 已启动 {tool.get('name', '工具')}：{script} (PID {proc.pid})")

        threading.Thread(target=self._watch_process, args=(tid, proc), daemon=True).start()

    def _watch_process(self, tid: str, proc: subprocess.Popen):
        """后台等待子进程结束，再回到主线程恢复按钮状态"""
        try:
            returncode = proc.wait()
        except Exception:
            returncode = -1
        self.frame.after(0, lambda: self._on_process_exit(tid, returncode))

    def _on_process_exit(self, tid: str, returncode: int):
        tool = next((t for t in self.tools if t.get("id") == tid), None)
        if tool is not None:
            self._set_running(tool, False)
        self.status_var.set(f"已退出，返回码 {returncode}")

    def open_dialog(self):
        """打开外部工具配置窗口（已打开则置前）"""
        if self._dialog is not None:
            try:
                if self._dialog.winfo_exists():
                    self._dialog.lift()
                    self._dialog.focus_force()
                    return self._dialog
            except Exception:
                pass
        self._dialog = ExternalToolConfigDialog(self.parent, self)
        return self._dialog

    def set_tools(self, tools: List[Dict[str, Any]]):
        """保存并刷新（供配置窗口调用）"""
        if save_tools(tools):
            self.reload()
            self.status_var.set("配置已保存")


# ---------------------------------------------------------------------------
# 脚本配置窗口
# ---------------------------------------------------------------------------
class ExternalToolConfigDialog(tk.Toplevel):
    """外部脚本管理窗口：增删改工具配置"""

    def __init__(self, master, launcher: "ExternalToolLauncher"):
        super().__init__(master)
        self.launcher = launcher
        self.tools: List[Dict[str, Any]] = [dict(t) for t in launcher.tools]
        self.current_index: Optional[int] = None
        self.dirty = False
        self._loading = False  # True 时忽略表单变化回写，避免界面赋值被当成用户修改

        self.title("外部工具管理")
        self.geometry("880x520")
        self.minsize(760, 460)
        self.transient(master)

        self._create_vars()
        self._build_ui()
        self._fill_tree()
        if self.tools:
            self._select_index(0)
        self.grab_set()
        self.focus_force()

    # ---------- 变量 ----------
    def _create_vars(self):
        self.name_var = tk.StringVar()
        self.python_var = tk.StringVar()
        self.script_var = tk.StringVar()
        self.cwd_var = tk.StringVar()
        self.args_var = tk.StringVar()
        self.console_var = tk.BooleanVar(value=True)

        for var in (self.name_var, self.python_var, self.script_var,
                    self.cwd_var, self.args_var, self.console_var):
            var.trace_add("write", self._on_field_changed)

    # ---------- 界面 ----------
    def _build_ui(self):
        container = ttk.Frame(self, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        # 上：工具列表 + 增删按钮
        list_frame = ttk.LabelFrame(container, text="工具列表", padding=6)
        list_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "script", "python")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)
        self.tree.heading("name", text="名称")
        self.tree.heading("script", text="脚本路径")
        self.tree.heading("python", text="Python 解释器")
        self.tree.column("name", width=130, anchor=tk.W)
        self.tree.column("script", width=430, anchor=tk.W)
        self.tree.column("python", width=220, anchor=tk.W)

        yscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # 下：详细设置
        form = ttk.LabelFrame(container, text="工具设置", padding=8)
        form.pack(fill=tk.X, pady=(10, 6))
        form.columnconfigure(1, weight=1)

        self._add_row(form, 0, "名称：", self.name_var, None)
        self._add_row(form, 1, "Python 解释器：", self.python_var, self._browse_python)
        self._add_row(form, 2, "脚本路径：", self.script_var, self._browse_script)
        self._add_row(form, 3, "工作目录：", self.cwd_var, self._browse_cwd)
        self._add_row(form, 4, "启动参数：", self.args_var, None)

        ttk.Checkbutton(
            form,
            text="启动时显示控制台窗口（便于查看输出与报错）",
            variable=self.console_var,
        ).grid(row=5, column=1, sticky=tk.W, pady=(4, 0))

        ttk.Label(
            form,
            text="提示：工作目录留空时自动使用脚本所在目录；Python 解释器留空时使用当前程序的解释器。",
            foreground="#888888",
        ).grid(row=6, column=1, sticky=tk.W, pady=(4, 0))

        # 底部按钮
        bottom = ttk.Frame(container)
        bottom.pack(fill=tk.X)

        ttk.Button(bottom, text="新增", command=self._add_tool).pack(side=tk.LEFT)
        ttk.Button(bottom, text="复制", command=self._copy_tool).pack(side=tk.LEFT, padx=6)
        ttk.Button(bottom, text="删除", command=self._delete_tool).pack(side=tk.LEFT)

        ttk.Button(bottom, text="关闭", command=self._on_close).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="保存", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(bottom, text="立即启动", command=self._launch_current).pack(side=tk.RIGHT, padx=(0, 6))

    def _add_row(self, parent, row, label_text, var, browse_cmd):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        line = ttk.Frame(parent)
        line.grid(row=row, column=1, sticky=tk.EW, pady=3)
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=var).grid(row=0, column=0, sticky=tk.EW)
        if browse_cmd is not None:
            ttk.Button(line, text="浏览…", width=8, command=browse_cmd).grid(row=0, column=1, padx=(6, 0))

    # ---------- 数据 <-> 界面 ----------
    def _fill_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for tool in self.tools:
            self.tree.insert("", tk.END, iid=tool["id"], values=self._row_values(tool))

    @staticmethod
    def _row_values(tool: Dict[str, Any]):
        return (
            tool.get("name", ""),
            tool.get("script") or "(未设置)",
            tool.get("python") or "(跟随主程序)",
        )

    def _sync_tree_item(self, tool: Dict[str, Any]):
        if self.tree.exists(tool["id"]):
            self.tree.item(tool["id"], values=self._row_values(tool))

    def _unique_id(self, base: str = "tool") -> str:
        existing = {t["id"] for t in self.tools}
        i = 1
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        index = next((i for i, t in enumerate(self.tools) if t["id"] == sel[0]), None)
        if index is None:
            return
        self.current_index = index
        tool = self.tools[index]
        # 防止 trace 回调把当前编辑值又写回，先解绑再绑定
        self._set_fields_silent(tool)

    def _set_fields_silent(self, tool: Dict[str, Any]):
        """把工具数据写入表单（不触发回写）"""
        self._loading = True
        try:
            self.name_var.set(tool.get("name", ""))
            self.python_var.set(tool.get("python", ""))
            self.script_var.set(tool.get("script", ""))
            self.cwd_var.set(tool.get("cwd", ""))
            self.args_var.set(tool.get("args", ""))
            self.console_var.set(bool(tool.get("console", True)))
        finally:
            self._loading = False

    def _on_field_changed(self, *_args):
        if self._loading:
            return
        self._apply_fields()

    def _apply_fields(self):
        """表单变化即时写回内存列表"""
        idx = self.current_index
        if idx is None or not (0 <= idx < len(self.tools)):
            return
        tool = self.tools[idx]
        tool["name"] = self.name_var.get().strip() or "未命名工具"
        tool["python"] = self.python_var.get().strip()
        tool["script"] = self.script_var.get().strip()
        tool["cwd"] = self.cwd_var.get().strip()
        tool["args"] = self.args_var.get().strip()
        tool["console"] = bool(self.console_var.get())
        self.dirty = True
        self._sync_tree_item(tool)

    def _select_index(self, index: int):
        if not (0 <= index < len(self.tools)):
            return
        self.current_index = index
        self.tree.selection_set(self.tools[index]["id"])
        self.tree.focus(self.tools[index]["id"])
        self._set_fields_silent(self.tools[index])

    # ---------- 列表操作 ----------
    def _add_tool(self):
        tool = normalize_tool(
            {"id": self._unique_id(), "name": "新工具", "python": sys.executable,
             "script": "", "cwd": "", "args": "", "console": True},
            len(self.tools),
        )
        self.tools.append(tool)
        self._fill_tree()
        self._select_index(len(self.tools) - 1)
        self.dirty = True

    def _copy_tool(self):
        idx = self.current_index
        if idx is None or not (0 <= idx < len(self.tools)):
            messagebox.showinfo("提示", "请先选择一个工具")
            return
        src = dict(self.tools[idx])
        src["id"] = self._unique_id()
        src["name"] = src.get("name", "工具") + "_副本"
        self.tools.append(normalize_tool(src, len(self.tools)))
        self._fill_tree()
        self._select_index(len(self.tools) - 1)
        self.dirty = True

    def _delete_tool(self):
        idx = self.current_index
        if idx is None or not (0 <= idx < len(self.tools)):
            messagebox.showinfo("提示", "请先选择一个工具")
            return
        name = self.tools[idx].get("name", "工具")
        if not messagebox.askyesno("确认", f"确定删除工具“{name}”吗？"):
            return
        self.tools.pop(idx)
        self.current_index = None
        self._fill_tree()
        if self.tools:
            self._select_index(min(idx, len(self.tools) - 1))
        self.dirty = True

    # ---------- 浏览 ----------
    def _browse_python(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 Python 解释器",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if path:
            self.python_var.set(path)

    def _browse_script(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 Python 脚本",
            filetypes=[("Python 脚本", "*.py *.pyw"), ("所有文件", "*.*")],
        )
        if path:
            self.script_var.set(path)

    def _browse_cwd(self):
        path = filedialog.askdirectory(parent=self, title="选择工作目录")
        if path:
            self.cwd_var.set(path)

    # ---------- 底部动作 ----------
    def _current_tool(self) -> Optional[Dict[str, Any]]:
        self._apply_fields()
        idx = self.current_index
        if idx is None or not (0 <= idx < len(self.tools)):
            messagebox.showinfo("提示", "请先选择一个工具")
            return None
        return self.tools[idx]

    def _launch_current(self):
        tool = self._current_tool()
        if tool is None:
            return
        script = tool.get("script", "").strip()
        if not script or not os.path.isfile(script):
            messagebox.showerror("错误", f"脚本不存在或未选择：\n{script or '(未设置)'}")
            return
        self.launcher.running.pop(tool.get("id"), None)
        self.launcher.launch(dict(tool))

    def _save(self):
        self._apply_fields()
        if not self.tools:
            messagebox.showwarning("提示", "工具列表为空，无法保存")
            return
        missing = [t["name"] for t in self.tools if not t.get("script")]
        if missing and not messagebox.askyesno(
            "确认", f"以下工具尚未设置脚本路径：\n{', '.join(missing)}\n\n仍要保存吗？"
        ):
            return
        # 重新分配为主列表时的顺序（列表顺序即按钮顺序）
        ordered = []
        for iid in self.tree.get_children():
            tool = next((t for t in self.tools if t["id"] == iid), None)
            if tool is not None:
                ordered.append(tool)
        for t in self.tools:
            if t not in ordered:
                ordered.append(t)

        self.tools = ordered
        if self.launcher.set_tools(self.tools):
            self.dirty = False
            messagebox.showinfo("完成", "外部工具配置已保存")

    def _on_close(self):
        if self.dirty:
            ans = messagebox.askyesnocancel("提示", "当前有未保存的修改，是否保存后关闭？")
            if ans is None:
                return
            if ans and not self._confirm_save_silently():
                return
        self.destroy()

    def _confirm_save_silently(self) -> bool:
        self._apply_fields()
        return self.launcher.set_tools(self.tools)


def main():
    """独立调试入口"""
    root = tk.Tk()
    root.title("外部工具启动器 - 调试")
    root.geometry("900x200")
    launcher = ExternalToolLauncher(root)
    launcher.frame.pack(fill=tk.X, padx=10, pady=10)
    root.mainloop()


if __name__ == "__main__":
    main()
