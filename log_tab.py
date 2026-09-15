#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 操作日志选项卡

"""操作日志选项卡模块"""

import tkinter as tk
from tkinter import ttk

class LogTab:
    """操作日志选项卡"""
    
    def __init__(self, parent_notebook, controller):
        """
        初始化日志选项卡
        
        Args:
            parent_notebook: 父级Notebook控件
            controller: 主控制器（主窗体），提供跨tab通信接口
        """
        self.controller = controller
        self.frame = ttk.Frame(parent_notebook)
        
        self.create_ui()
        
        # 注册到notebook
        parent_notebook.add(self.frame, text="操作日志")
    
    def create_ui(self):
        """创建界面"""
        # 日志文本框
        log_frame = ttk.Frame(self.frame)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # 清空按钮
        ttk.Button(self.frame, text="清空日志", command=self.clear_log).pack(side=tk.BOTTOM, pady=5)
    
    def get_frame(self):
        """获取选项卡框架"""
        return self.frame
    
    def log(self, message: str):
        """添加日志（线程安全，通过controller调度）"""
        # 使用after确保在主线程执行
        self.frame.after(0, self._append_log, message)
    
    def _append_log(self, message: str):
        """实际添加日志到文本框"""
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
    
    def clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)