#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话框
"""
import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from .config import (
    WHISPER_MODELS, SAKURA_MODES,
    AFTER_FINISH_ACTIONS,
    default_model_path, get_model_root,
    load_config, save_config, normalize_path, to_windows_path,
    REQUIRED_PACKAGES,
)
from .ui_utils import fit_and_center, sc
from .system_utils import (
    find_ffmpeg, check_cuda_available, check_cublas, check_cudnn,
    check_translator_path_valid, check_sakura_path_valid,
    check_kotoba_path_valid, check_nemo_path_valid,
    check_package, get_pip_command,
)

# ============================================================
# 设置对话框
# ============================================================

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, on_saved=None):
        super().__init__(parent)
        self.title("软件设置")
        self.transient(parent)
        self.config = load_config()
        self.result_saved = False
        self.on_saved = on_saved      # 保存后的回调（用于弹右下角浮窗，避免弹对话框）
        self._build_ui()
        # 计算居中位置，并限制最大高度为屏幕的 80%
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        max_h = int(sh * 0.8)
        req_w = max(sc(920), self.winfo_reqwidth() + sc(20))
        req_h = min(max_h, max(sc(660), self.winfo_reqheight() + sc(20)))
        x = max(0, (sw - req_w) // 2)
        y = max(0, (sh - req_h) // 2)
        self.geometry(f"{req_w}x{req_h}+{x}+{y}")
        self.minsize(sc(600), sc(400))
        self.grab_set()

    def _build_ui(self):
        # ---------- 1) 底部按钮（先 pack，固定在下方）----------
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side="bottom", fill="x", pady=15)
        ttk.Button(btn_frame, text="保存", command=self._save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side="left", padx=5)

        # ---------- 2) 可滚动内容区域 ----------
        container = ttk.Frame(self)
        container.pack(side="top", fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮支持：递归绑定到所有子控件，确保无论鼠标在哪都能滚动
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            for child in widget.winfo_children():
                _bind_mousewheel(child)

        _bind_mousewheel(self.scroll_frame)
        canvas.bind("<MouseWheel>", _on_mousewheel)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        self.scroll_frame.bind("<MouseWheel>", _on_mousewheel)

        # ---------- 3) 所有设置内容放到 scroll_frame 中 ----------
        sf = self.scroll_frame

        ttk.Label(sf, text="软件设置", font=("Microsoft YaHei", 18, "bold")).pack(pady=15)

        ffmpeg_frame = ttk.LabelFrame(sf, text="FFmpeg 设置", padding="10")
        ffmpeg_frame.pack(fill="x", padx=15, pady=5)
        found, path, version = find_ffmpeg(self.config)
        if found:
            status_text = "已找到: " + str(path)
            if version:
                status_text += " | " + version[:50]
            status_color = "#27ae60"
        else:
            status_text = "未找到 FFmpeg"
            status_color = "#e74c3c"
        ttk.Label(ffmpeg_frame, text="当前状态:", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w")
        ttk.Label(ffmpeg_frame, text=status_text, foreground=status_color).pack(anchor="w")
        path_row = ttk.Frame(ffmpeg_frame)
        path_row.pack(fill="x", pady=5)
        ttk.Label(path_row, text="ffmpeg.exe 路径:").pack(side="left")
        self.ffmpeg_var = tk.StringVar(value=self.config.get("ffmpeg_path", ""))
        ttk.Entry(path_row, textvariable=self.ffmpeg_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(path_row, text="浏览...", command=self._browse_ffmpeg).pack(side="right")
        detect_row = ttk.Frame(ffmpeg_frame)
        detect_row.pack(fill="x", pady=5)
        ttk.Button(detect_row, text="自动检测", command=self._auto_detect_ffmpeg).pack(side="left")
        self.ffmpeg_detect_var = tk.StringVar(value="")
        ttk.Label(detect_row, textvariable=self.ffmpeg_detect_var, foreground="#666",
                  wraplength=sc(620)).pack(side="left", padx=10)

        cuda_frame = ttk.LabelFrame(sf, text="CUDA/GPU 状态", padding="10")
        cuda_frame.pack(fill="x", padx=15, pady=5)
        cuda_ok, cuda_msg = check_cuda_available()
        cublas_ok, cublas_msg = check_cublas()
        cudnn_ok, cudnn_msg = check_cudnn()
        cuda_color = "#27ae60" if cuda_ok else "#e74c3c"
        ttk.Label(cuda_frame, text="PyTorch CUDA: " + cuda_msg, foreground=cuda_color, wraplength=sc(780)).pack(anchor="w")
        cublas_color = "#27ae60" if cublas_ok else "#e74c3c"
        ttk.Label(cuda_frame, text="cuBLAS 库: " + cublas_msg, foreground=cublas_color, wraplength=sc(780)).pack(anchor="w", pady=(5,0))
        cudnn_color = "#27ae60" if cudnn_ok else "#e74c3c"
        ttk.Label(cuda_frame, text="cuDNN 库: " + cudnn_msg, foreground=cudnn_color, wraplength=sc(780)).pack(anchor="w", pady=(5,0))
        if not cuda_ok or not cublas_ok or not cudnn_ok:
            ttk.Label(cuda_frame, text="提示: GPU 加速不可用，请使用 CPU 模式运行", foreground="#e67e22").pack(anchor="w", pady=(5,0))

        # 模型根目录：语音/翻译模型统一存放位置（模型文件不随程序拷贝，只记录路径）
        root_frame = ttk.LabelFrame(sf, text="模型根目录（语音模型 / 翻译模型统一存放位置）", padding="10")
        root_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(root_frame, text="设置模型总目录后，下方各项留空时会自动在其子目录中查找对应模型。",
                  wraplength=sc(720)).pack(anchor="w")
        root_row = ttk.Frame(root_frame)
        root_row.pack(fill="x", pady=5)
        ttk.Label(root_row, text="模型根目录:").pack(side="left")
        self.model_root_var = tk.StringVar(value=get_model_root(self.config))
        ttk.Entry(root_row, textvariable=self.model_root_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(root_row, text="浏览...", command=self._browse_model_root).pack(side="right")

        trans_frame = ttk.LabelFrame(sf, text="翻译模型设置", padding="10")
        trans_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(trans_frame, text="NLLB-200: 指定 nllb-200-distilled-600M 模型文件夹；留空则使用模型根目录下 翻译模型/NLLB-200/nllb-200-distilled-600M。", wraplength=sc(720)).pack(anchor="w")
        trans_row = ttk.Frame(trans_frame)
        trans_row.pack(fill="x", pady=5)
        ttk.Label(trans_row, text="NLLB-200 目录:").pack(side="left")
        default_trans = self.config.get("translator_model_path", "") or normalize_path(default_model_path("translator", self.config))
        self.translator_var = tk.StringVar(value=default_trans)
        ttk.Entry(trans_row, textvariable=self.translator_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(trans_row, text="浏览...", command=self._browse_translator).pack(side="right")
        ttk.Label(trans_frame, text="Sakura-14B: 指定 .gguf 模型文件；留空则使用工作目录下 翻译模型/SakuraLLM 中的模型。", wraplength=sc(720)).pack(anchor="w", pady=(10,0))
        sakura_row = ttk.Frame(trans_frame)
        sakura_row.pack(fill="x", pady=5)
        ttk.Label(sakura_row, text="Sakura-14B GGUF:").pack(side="left")
        default_sakura = self.config.get("sakura_model_path", "") or normalize_path(default_model_path("sakura", self.config))
        self.sakura_var = tk.StringVar(value=default_sakura)
        ttk.Entry(sakura_row, textvariable=self.sakura_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(sakura_row, text="浏览...", command=self._browse_sakura).pack(side="right")

        sakura7b_row = ttk.Frame(trans_frame)
        sakura7b_row.pack(fill="x", pady=5)
        ttk.Label(sakura7b_row, text="Sakura-7B GGUF:").pack(side="left")
        default_sakura7b = self.config.get("sakura7b_model_path", "") or normalize_path(default_model_path("sakura7b", self.config))
        self.sakura7b_var = tk.StringVar(value=default_sakura7b)
        ttk.Entry(sakura7b_row, textvariable=self.sakura7b_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(sakura7b_row, text="浏览...", command=self._browse_sakura7b).pack(side="right")

        gal_row = ttk.Frame(trans_frame)
        gal_row.pack(fill="x", pady=5)
        ttk.Label(gal_row, text="GalTransl GGUF:").pack(side="left")
        default_gal = self.config.get("galtransl_model_path", "") or normalize_path(default_model_path("galtransl", self.config))
        self.galtransl_var = tk.StringVar(value=default_gal)
        ttk.Entry(gal_row, textvariable=self.galtransl_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(gal_row, text="浏览...", command=self._browse_galtransl).pack(side="right")

        # Sakura 翻译模式（速度/质量选项）
        mode_row = ttk.Frame(trans_frame)
        mode_row.pack(fill="x", pady=(10, 5))
        ttk.Label(mode_row, text="Sakura 翻译模式:").pack(side="left")
        self.sakura_mode_var = tk.StringVar(value=self.config.get("sakura_mode", "均衡（推荐）"))
        mode_combo = ttk.Combobox(mode_row, textvariable=self.sakura_mode_var,
                                  values=list(SAKURA_MODES.keys()),
                                  state="readonly", width=14)
        mode_combo.pack(side="left", padx=5)
        mode_combo.bind("<<ComboboxSelected>>", self._on_sakura_mode_change)
        self.sakura_mode_desc_var = tk.StringVar(value=SAKURA_MODES.get(self.sakura_mode_var.get(), {}).get("desc", ""))
        ttk.Label(mode_row, textvariable=self.sakura_mode_desc_var, foreground="#2c5f8a").pack(side="left", padx=10)
        self.translator_status_var = tk.StringVar(value="")
        ttk.Label(trans_frame, textvariable=self.translator_status_var, foreground="#666", wraplength=sc(720)).pack(anchor="w", pady=5)
        ttk.Button(trans_frame, text="检测翻译模型", command=self._check_translator).pack(anchor="w", pady=5)
        self._check_translator()

        model_frame = ttk.LabelFrame(sf, text="语音模型上级目录设置", padding="10")
        model_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(model_frame, text="设置模型文件的上级目录，程序会自动查找子文件夹。", wraplength=sc(720)).pack(anchor="w")
        ttk.Label(model_frame, text="例如: D:/models/ 下有 faster-whisper-small/、faster-whisper-medium/", font=("Consolas", 11), foreground="#666").pack(anchor="w", pady=(5,0))
        base_row = ttk.Frame(model_frame)
        base_row.pack(fill="x", pady=5)
        ttk.Label(base_row, text="模型上级目录:").pack(side="left")
        self.model_base_var = tk.StringVar(value=self.config.get("model_base_path", ""))
        ttk.Entry(base_row, textvariable=self.model_base_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(base_row, text="浏览...", command=self._browse_model_base).pack(side="right")
        self.detected_models_var = tk.StringVar(value="点击检测按钮查看目录中的模型")
        ttk.Label(model_frame, textvariable=self.detected_models_var, foreground="#666", wraplength=sc(720)).pack(anchor="w", pady=5)
        ttk.Button(model_frame, text="检测目录中的模型", command=self._detect_models).pack(anchor="w", pady=5)

        other_asr_frame = ttk.LabelFrame(sf, text="其他语音模型设置（kotoba / NeMo）", padding="10")
        other_asr_frame.pack(fill="x", padx=15, pady=5)
        kotoba_row = ttk.Frame(other_asr_frame)
        kotoba_row.pack(fill="x", pady=5)
        ttk.Label(kotoba_row, text="kotoba-whisper 目录:").pack(side="left")
        default_kotoba = self.config.get("kotoba_model_path", "") or normalize_path(default_model_path("kotoba", self.config))
        self.kotoba_var = tk.StringVar(value=default_kotoba)
        ttk.Entry(kotoba_row, textvariable=self.kotoba_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(kotoba_row, text="浏览...", command=self._browse_kotoba).pack(side="right")
        nemo_row = ttk.Frame(other_asr_frame)
        nemo_row.pack(fill="x", pady=5)
        ttk.Label(nemo_row, text="ReazonSpeech .nemo 文件:").pack(side="left")
        default_nemo = self.config.get("nemo_model_path", "") or normalize_path(default_model_path("nemo", self.config))
        self.nemo_var = tk.StringVar(value=default_nemo)
        ttk.Entry(nemo_row, textvariable=self.nemo_var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(nemo_row, text="浏览...", command=self._browse_nemo).pack(side="right")
        self.other_asr_status_var = tk.StringVar(value="")
        ttk.Label(other_asr_frame, textvariable=self.other_asr_status_var, foreground="#666", wraplength=sc(720)).pack(anchor="w", pady=5)
        ttk.Button(other_asr_frame, text="检测 kotoba / NeMo 模型", command=self._check_other_asr).pack(anchor="w", pady=5)
        self._check_other_asr()

        adv_frame = ttk.LabelFrame(sf, text="单独模型路径（高级设置）", padding="10")
        adv_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(adv_frame, text="如果某个模型不在上级目录的子文件夹中，可以单独指定。", wraplength=sc(720)).pack(anchor="w")
        self.model_paths = self.config.get("model_paths", {})
        self.model_path_vars = {}
        for model_name in ["tiny", "base", "small", "medium", "large-v3"]:
            row = ttk.Frame(adv_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=model_name + ":", width=12).pack(side="left")
            var = tk.StringVar(value=self.model_paths.get(model_name, ""))
            self.model_path_vars[model_name] = var
            ttk.Entry(row, textvariable=var, font=("Consolas", 11)).pack(side="left", fill="x", expand=True, padx=5)
            ttk.Button(row, text="浏览", command=lambda m=model_name: self._browse_model(m)).pack(side="right")

        # 任务完成后操作
        finish_frame = ttk.LabelFrame(sf, text="任务完成后操作", padding="10")
        finish_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(finish_frame, text="所有任务结束后自动执行:").pack(side="left")
        self.after_finish_var = tk.StringVar(value=self.config.get("after_finish_action", "none"))
        finish_combo = ttk.Combobox(finish_frame, textvariable=self.after_finish_var,
                                    values=list(AFTER_FINISH_ACTIONS.keys()),
                                    state="readonly", width=16)
        finish_combo.pack(side="left", padx=5)
        def _update_finish_desc(event=None):
            action = self.after_finish_var.get()
            desc = AFTER_FINISH_ACTIONS.get(action, action)
            self.after_finish_desc_var.set(desc)
        self.after_finish_desc_var = tk.StringVar(value=AFTER_FINISH_ACTIONS.get(self.after_finish_var.get(), ""))
        ttk.Label(finish_frame, textvariable=self.after_finish_desc_var, foreground="#666").pack(side="left", padx=5)
        finish_combo.bind("<<ComboboxSelected>>", _update_finish_desc)

    def _browse_ffmpeg(self):
        path = filedialog.askopenfilename(title="选择 ffmpeg.exe", filetypes=[("FFmpeg", "ffmpeg.exe"), ("所有文件", "*.*")])
        if path:
            self.ffmpeg_var.set(normalize_path(path))

    def _auto_detect_ffmpeg(self):
        found, path, version = find_ffmpeg()
        if found and path:
            self.ffmpeg_var.set(path)
            self.ffmpeg_detect_var.set("已找到 FFmpeg: " + str(path))
        else:
            self.ffmpeg_detect_var.set("未找到 FFmpeg，请手动指定路径。")

    def _browse_model_root(self):
        path = filedialog.askdirectory(title="选择模型根目录（其下包含 语音模型 / 翻译模型）")
        if path:
            self.model_root_var.set(normalize_path(path))

    def _browse_model_base(self):
        path = filedialog.askdirectory(title="选择模型上级目录")
        if path:
            self.model_base_var.set(normalize_path(path))
            self._detect_models()

    def _detect_models(self):
        base = self.model_base_var.get().strip()
        if not base:
            self.detected_models_var.set("请先设置模型上级目录")
            return
        win_base = to_windows_path(base)
        if not os.path.exists(win_base):
            self.detected_models_var.set("目录不存在: " + base)
            return
        found = []
        for model_name in WHISPER_MODELS.keys():
            possible_names = ["faster-whisper-" + model_name, model_name, "whisper-" + model_name]
            for name in possible_names:
                full_path = os.path.join(win_base, name)
                if os.path.exists(full_path):
                    valid, msg = check_model_path_valid(full_path)
                    if valid:
                        found.append(model_name + " -> " + name)
                        break
        if found:
            self.detected_models_var.set("检测到以下模型:\n" + "\n".join(found))
        else:
            self.detected_models_var.set("未检测到有效模型。请确认目录结构正确。")

    def _browse_model(self, model_name):
        path = filedialog.askdirectory(title="选择 " + model_name + " 模型文件夹")
        if path:
            self.model_path_vars[model_name].set(normalize_path(path))

    def _browse_translator(self):
        path = filedialog.askdirectory(title="选择 NLLB-200 翻译模型文件夹")
        if path:
            self.translator_var.set(normalize_path(path))
            self._check_translator()

    def _check_translator(self):
        messages = []
        path = self.translator_var.get().strip()
        valid, msg = check_translator_path_valid(to_windows_path(path)) if path else (False, "路径为空")
        messages.append(("✔ " if valid else "✘ ") + "NLLB-200: " + msg)
        spath = self.sakura_var.get().strip()
        svalid, smsg = check_sakura_path_valid(to_windows_path(spath)) if spath else (False, "路径为空")
        messages.append(("✔ " if svalid else "✘ ") + "Sakura-14B: " + smsg)
        s7path = self.sakura7b_var.get().strip()
        s7valid, s7msg = check_sakura_path_valid(to_windows_path(s7path)) if s7path else (False, "路径为空")
        messages.append(("✔ " if s7valid else "✘ ") + "Sakura-7B: " + s7msg)
        gpath = self.galtransl_var.get().strip()
        gvalid, gmsg = check_sakura_path_valid(to_windows_path(gpath)) if gpath else (False, "路径为空")
        messages.append(("✔ " if gvalid else "✘ ") + "GalTransl-7B: " + gmsg)
        self.translator_status_var.set("\n".join(messages))

    def _browse_sakura(self):
        path = filedialog.askopenfilename(
            title="选择 Sakura-14B GGUF 模型文件",
            filetypes=[("GGUF 模型", "*.gguf"), ("所有文件", "*.*")])
        if path:
            self.sakura_var.set(normalize_path(path))
            self._check_translator()

    def _browse_sakura7b(self):
        path = filedialog.askopenfilename(
            title="选择 Sakura-7B GGUF 模型文件",
            filetypes=[("GGUF 模型", "*.gguf"), ("所有文件", "*.*")])
        if path:
            self.sakura7b_var.set(normalize_path(path))
            self._check_translator()

    def _browse_galtransl(self):
        path = filedialog.askopenfilename(
            title="选择 Sakura-GalTransl-7B GGUF 模型文件",
            filetypes=[("GGUF 模型", "*.gguf"), ("所有文件", "*.*")])
        if path:
            self.galtransl_var.set(normalize_path(path))
            self._check_translator()

    def _on_sakura_mode_change(self, event=None):
        desc = SAKURA_MODES.get(self.sakura_mode_var.get(), {}).get("desc", "")
        self.sakura_mode_desc_var.set(desc)

    def _browse_kotoba(self):
        path = filedialog.askdirectory(title="选择 kotoba-whisper 模型文件夹")
        if path:
            self.kotoba_var.set(normalize_path(path))
            self._check_other_asr()

    def _browse_nemo(self):
        path = filedialog.askopenfilename(
            title="选择 ReazonSpeech NeMo 模型文件",
            filetypes=[("NeMo 模型", "*.nemo"), ("所有文件", "*.*")])
        if path:
            self.nemo_var.set(normalize_path(path))
            self._check_other_asr()

    def _check_other_asr(self):
        messages = []
        kpath = self.kotoba_var.get().strip()
        kvalid, kmsg = check_kotoba_path_valid(to_windows_path(kpath)) if kpath else (False, "路径为空")
        messages.append(("✔ " if kvalid else "✘ ") + "kotoba-whisper: " + kmsg)
        npath = self.nemo_var.get().strip()
        nvalid, nmsg = check_nemo_path_valid(to_windows_path(npath)) if npath else (False, "路径为空")
        messages.append(("✔ " if nvalid else "✘ ") + "ReazonSpeech-NeMo: " + nmsg)
        self.other_asr_status_var.set("\n".join(messages))

    def _save(self):
        ffmpeg_path = self.ffmpeg_var.get().strip()
        self.config["ffmpeg_path"] = ffmpeg_path
        self.config["model_root"] = self.model_root_var.get().strip()
        self.config["model_base_path"] = self.model_base_var.get().strip()
        self.config["translator_model_path"] = self.translator_var.get().strip()
        self.config["sakura_model_path"] = self.sakura_var.get().strip()
        self.config["sakura7b_model_path"] = self.sakura7b_var.get().strip()
        self.config["galtransl_model_path"] = self.galtransl_var.get().strip()
        self.config["sakura_mode"] = self.sakura_mode_var.get()
        self.config["kotoba_model_path"] = self.kotoba_var.get().strip()
        self.config["nemo_model_path"] = self.nemo_var.get().strip()
        new_model_paths = {}
        for model_name, var in self.model_path_vars.items():
            path = var.get().strip()
            if path:
                new_model_paths[model_name] = path
        self.config["model_paths"] = new_model_paths
        self.config["after_finish_action"] = self.after_finish_var.get()
        ok = save_config(self.config)
        self.result_saved = True
        if callable(self.on_saved):
            try:
                self.on_saved(bool(ok))
            except Exception:
                pass
        self.destroy()


# ============================================================
# 依赖安装对话框
# ============================================================

class DependencyDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("依赖检查与安装")
        self.transient(parent)
        self.missing_packages = []
        self._build_ui()
        fit_and_center(self, parent, 840, 680)
        self.grab_set()
        self._check_all()

    def _build_ui(self):
        ttk.Label(self, text="依赖检查与安装向导", font=("Microsoft YaHei", 18, "bold")).pack(pady=15)
        self.ffmpeg_frame = ttk.LabelFrame(self, text="FFmpeg 状态", padding="10")
        self.ffmpeg_frame.pack(fill="x", padx=15, pady=5)
        self.ffmpeg_status_var = tk.StringVar(value="检测中...")
        ttk.Label(self.ffmpeg_frame, textvariable=self.ffmpeg_status_var, font=("Microsoft YaHei", 12), wraplength=sc(680)).pack(anchor="w")
        cuda_frame = ttk.LabelFrame(self, text="CUDA/GPU 状态", padding="10")
        cuda_frame.pack(fill="x", padx=15, pady=5)
        cuda_ok, cuda_msg = check_cuda_available()
        cublas_ok, cublas_msg = check_cublas()
        cudnn_ok, cudnn_msg = check_cudnn()
        cuda_color = "#27ae60" if cuda_ok else "#e74c3c"
        ttk.Label(cuda_frame, text="PyTorch CUDA: " + cuda_msg, foreground=cuda_color, wraplength=sc(680)).pack(anchor="w")
        cublas_color = "#27ae60" if cublas_ok else "#e74c3c"
        ttk.Label(cuda_frame, text="cuBLAS 库: " + cublas_msg, foreground=cublas_color, wraplength=sc(680)).pack(anchor="w", pady=(5,0))
        cudnn_color = "#27ae60" if cudnn_ok else "#e74c3c"
        ttk.Label(cuda_frame, text="cuDNN 库: " + cudnn_msg, foreground=cudnn_color, wraplength=sc(680)).pack(anchor="w", pady=(5,0))
        if not cuda_ok or not cublas_ok or not cudnn_ok:
            ttk.Label(cuda_frame, text="提示: GPU 加速不可用，建议使用 CPU 模式运行", foreground="#e67e22").pack(anchor="w", pady=(5,0))
        ttk.Label(self, text="Python 包状态:", font=("Microsoft YaHei", 13, "bold")).pack(anchor="w", padx=15, pady=(10,0))

        # 按钮区域固定在窗口底部（必须先 pack side="bottom"，再放置列表区域）
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side="bottom", pady=15)
        self.btn_install = ttk.Button(btn_frame, text="一键安装缺失包", command=self._install_all, state="disabled")
        self.btn_install.pack(side="left", padx=5)
        ttk.Button(btn_frame, text="关闭", command=self.destroy).pack(side="left", padx=5)

        # 包列表区域：独立的容器内放画布和滚动条
        list_container = ttk.Frame(self)
        list_container.pack(fill="both", expand=True)
        canvas = tk.Canvas(list_container)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)
        self.scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw", width=sc(760))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=10)
        scrollbar.pack(side="right", fill="y")

    def _check_all(self):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        self.missing_packages = []
        row = 0
        found, path, version = find_ffmpeg()
        if found:
            self.ffmpeg_status_var.set("已找到: " + str(path) + " | " + str(version)[:80])
        else:
            self.ffmpeg_status_var.set("未找到 FFmpeg。请在【设置】菜单中指定路径。")
        for name, info in REQUIRED_PACKAGES.items():
            is_installed = check_package(name)
            self._add_row(row, info["pip_name"], is_installed, info["description"], info["size"])
            if not is_installed:
                self.missing_packages.append(info["pip_name"])
            row += 1
        if self.missing_packages:
            self.btn_install.config(state="normal")
        else:
            self.btn_install.config(state="disabled")

    def _add_row(self, row, name, installed, desc, size):
        frame = ttk.Frame(self.scroll_frame)
        frame.pack(fill="x", pady=3, padx=10)
        color = "#27ae60" if installed else "#e74c3c"
        status = "已安装" if installed else "未安装"
        ttk.Label(frame, text=name, font=("Microsoft YaHei", 12, "bold"), width=20).pack(side="left")
        ttk.Label(frame, text=status, foreground=color, font=("Microsoft YaHei", 12)).pack(side="left", padx=10)
        ttk.Label(frame, text=desc, wraplength=sc(380)).pack(side="left", padx=10)
        ttk.Label(frame, text="[" + size + "]").pack(side="right", padx=10)
        ttk.Separator(self.scroll_frame, orient="horizontal").pack(fill="x", padx=5)

    def _install_all(self):
        if not self.missing_packages:
            messagebox.showinfo("提示", "所有包已安装！", parent=self)
            return
        pip_cmd = get_pip_command()
        if not pip_cmd:
            messagebox.showerror("错误", "未找到可用的 pip 命令", parent=self)
            return
        install_win = tk.Toplevel(self)
        install_win.title("安装进度")
        install_win.transient(self)
        text = scrolledtext.ScrolledText(install_win, wrap=tk.WORD, font=("Consolas", 11))
        text.pack(fill="both", expand=True, padx=10, pady=10)
        fit_and_center(install_win, self, 640, 440)
        install_win.grab_set()
        def install_thread():
            for pkg in self.missing_packages:
                text.insert("end", "\n正在安装 " + pkg + "...\n")
                text.see("end")
                install_win.update()
                try:
                    result = subprocess.run(pip_cmd.split() + ["install", pkg], capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        text.insert("end", "安装成功！\n")
                    else:
                        text.insert("end", "安装失败: " + result.stderr[:200] + "\n")
                except Exception as e:
                    text.insert("end", "安装出错: " + str(e) + "\n")
                text.see("end")
                install_win.update()
            text.insert("end", "\n安装完成！请重新打开本窗口检查。\n")
            text.see("end")
        threading.Thread(target=install_thread, daemon=True).start()


# ============================================================
# 主窗口 - 批量处理版
# ============================================================
