#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 如何添加新选项卡（示例）：
# 创建新文件 new_feature_tab.py


import tkinter as tk
from tkinter import ttk

class NewFeatureTab:
    def __init__(self, parent_notebook, controller):
        self.controller = controller
        self.frame = ttk.Frame(parent_notebook)
        
        # 创建界面
        ttk.Label(self.frame, text="新功能").pack()
        
        # 注册到notebook
        parent_notebook.add(self.frame, text="新功能")
    
    def get_frame(self):
        return self.frame