#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 主程序入口和窗体（负责整合所有模块）

"""
文件移动工具 - 主程序入口
负责创建主窗体、管理配置、协调各选项卡
"""

import tkinter as tk
from tkinter import ttk, messagebox
import json
import os

# 导入配置和模型
from config import get_config_path, CONFIG_FILE_NAME
from models import VideoConfig

# 导入选项卡（新增选项卡只需在这里添加导入）
from move_videos_tab import MoveVideosTab
from log_tab import LogTab
from file_move_tab import FileMoveTab
from file_organize_tab import FileOrganizeTab  # 文件操作tab
from video_grid_tab import VideoGridTab # 视频缩略图tab
from video_check_tab import VideoCheckTab   # 视频文件检查
from file_compare_tab import FileCompareTab # 文件比对tab

class FileMoverGUI:
    """主窗体类 - 选项卡容器和控制器"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("文件移动工具 - 模块化版本")
        self.root.geometry("1200x950")
        
        # 配置存储
        self.config = self._load_config()
        
        # 创建Notebook（选项卡容器）
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 存储选项卡实例（便于后续扩展访问）
        self.tabs = {}
        
        # 初始化各选项卡（新增选项卡只需在这里添加）
        self._init_tabs()
        
        # 加载配置到各选项卡
        self._load_config_to_tabs()
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        config_path = get_config_path()
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"加载配置失败: {e}")
        
        return {
            "video_folder": "",
            "target_folder": "",
            "include_subfolders": False
        }
    
    def save_config(self, config: dict) -> bool:
        """
        保存配置到文件
        供各选项卡调用
        """
        try:
            # 更新内存中的配置
            self.config.update(config)
            
            # 写入文件
            with open(get_config_path(), 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {e}")
            return False
    
    def get_config(self) -> dict:
        """
        获取当前配置
        供各选项卡调用
        """
        return self.config.copy()
    
    def log(self, message: str):
        """
        记录日志（跨选项卡）
        将消息发送到日志选项卡
        """
        if 'log' in self.tabs:
            self.tabs['log'].log(message)
    
    def _init_tabs(self):
        """
        初始化所有选项卡
        新增选项卡时，只需在此方法中添加实例化代码
        """

        # 1. 文件移动 Tab（嵌入模式）
        file_move_tab = FileMoveTab(self.notebook, controller=self, show_log_panel=False)
        self.tabs['file_move'] = file_move_tab
        self.notebook.add(file_move_tab.frame, text="文件夹下移动")
    
        # 2. 移动新视频选项卡（核心功能）
        move_tab = MoveVideosTab(self.notebook, controller=self)
        self.tabs['move_videos'] = move_tab
    
        # 3. 文件整理 Tab（新增功能）
        organize_tab = FileOrganizeTab(self.notebook, controller=self)
        self.tabs['file_organize'] = organize_tab
        self.notebook.add(organize_tab.frame, text="文件名整理(file_organize)")

        # 视频宫格缩略图 Tab
        grid_tab = VideoGridTab(self.notebook, controller=self)
        self.tabs['video_grid'] = grid_tab
        self.notebook.add(grid_tab.frame, text="视频缩略图")        
        
        # 视频文件检查 Tab
        video_check_tab = VideoCheckTab(self.notebook, controller=self)
        self.tabs['video_check'] = video_check_tab
        self.notebook.add(video_check_tab.frame, text="视频文件检查(video_check)")

        # 文件比对 Tab
        file_compare_tab = FileCompareTab(self.notebook, controller=self)
        self.tabs['file_compare'] = file_compare_tab
        self.notebook.add(file_compare_tab.frame, text="文件比对(file_compare)")        

        # 4. 操作日志选项卡（必须最后初始化，因为其他tab可能调用它记录日志）
        log_tab = LogTab(self.notebook, controller=self)
        self.tabs['log'] = log_tab

    def _load_config_to_tabs(self):
        """加载配置到各选项卡"""
        # 各选项卡自行管理配置加载，这里可以添加额外的全局配置同步
        pass

def main():
    """程序入口"""
    root = tk.Tk()
    app = FileMoverGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()