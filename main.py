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
import sys

# 导入配置和模型
from config import get_config_path, CONFIG_FILE_NAME
from models import VideoConfig

# 导入选项卡（新增选项卡只需在这里添加导入）
from move_videos_tab import MoveVideosTab
from log_tab import LogTab
from file_move_tab import FileMoveTab
from file_organize_tab import FileOrganizeTab  # 文件操作tab（内含“文件名整理”与“批量改名”两个子功能）
from video_grid_tab import VideoGridTab # 视频缩略图tab
from folder_check_tab import FolderCheckTab  # 文件夹检查/文件比对合并tab（含原 video_check 与 file_compare 功能）
from web_grab_tab import (  # 网页图片抓取tab
    WebGrabTab, ensure_cuda_dll_path, translation_env_report,
)
from subtitle_tab import SubtitleTab  # 视频语音识别并翻译为字幕tab（可独立运行：python subtitle_tab.py）
from workflow_tab import WorkflowTab  # 多任务串联工作流tab（可独立运行：python workflow_tab.py）
# 顶部外部工具按钮区（可在外部工具管理窗口中修改脚本路径）
from external_tool_launcher import ExternalToolLauncher

class FileMoverGUI:
    """主窗体类 - 选项卡容器和控制器"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("文件移动工具 - 模块化版本")
        # 默认窗口尺寸：高度按屏幕自适应加高（不超过屏幕高度），保证底部操作按钮打开即可见
        # 宽度加宽以容纳「工作流串联」选项卡的左右分栏（清单 + 流程/参数区）
        screen_h = self.root.winfo_screenheight()
        screen_w = self.root.winfo_screenwidth()
        win_h = min(1100, max(950, screen_h - 100))
        win_w = min(1520, max(1300, screen_w - 60))
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(1280, 860)
        
        # 配置存储
        self.config = self._load_config()
        
        # 创建Notebook（选项卡容器）
        self.notebook = ttk.Notebook(self.root)

        # 顶部按钮区：外部 Python 工具（脚本路径可在“管理脚本”窗口中修改）
        self.toolbar = ExternalToolLauncher(self.root, controller=self)
        self.toolbar.frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        # Notebook 放在按钮区下方
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 存储选项卡实例（便于后续扩展访问）
        self.tabs = {}
        
        # 初始化各选项卡（新增选项卡只需在这里添加）
        self._init_tabs()
        
        # 加载配置到各选项卡
        self._load_config_to_tabs()

        # 关闭主窗口前先清理各选项卡（如释放已载入的翻译模型），再销毁窗口
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
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
        self.notebook.add(file_move_tab.frame, text="文件夹下移动(file_move_tab)")
    
        # 2. 移动新视频选项卡（核心功能）
        move_tab = MoveVideosTab(self.notebook, controller=self)
        self.tabs['move_videos'] = move_tab
    
        # 3. 文件整理 Tab（内含“文件名整理”与“批量改名”两个子功能）
        organize_tab = FileOrganizeTab(self.notebook, controller=self)
        self.tabs['file_organize'] = organize_tab
        self.notebook.add(organize_tab.frame, text="文件名整理/批量改名(file_organize)")

        # 视频宫格缩略图 Tab
        grid_tab = VideoGridTab(self.notebook, controller=self)
        self.tabs['video_grid'] = grid_tab
        self.notebook.add(grid_tab.frame, text="视频缩略图(video_grid)")        
        
        # 文件夹检查 / 文件比对 合并 Tab（合并原 video_check 与 file_compare 两个功能）
        folder_check_tab = FolderCheckTab(self.notebook, controller=self)
        self.tabs['folder_check'] = folder_check_tab
        self.notebook.add(folder_check_tab.get_frame(), text="文件夹检查/比对(folder_check)")

        # 网页图片抓取 Tab
        web_grab_tab = WebGrabTab(self.notebook, controller=self)
        self.tabs['web_grab'] = web_grab_tab
        self.notebook.add(web_grab_tab.frame, text="网页抓取(web_grab)")

        # 视频语音识别并翻译为字幕 Tab（原「日剧语音转中文字幕工具」移植）
        subtitle_tab = SubtitleTab(self.notebook, controller=self)
        self.tabs['subtitle'] = subtitle_tab
        self.notebook.add(subtitle_tab.get_frame(), text="视频转字幕(subtitle_tab)")

        # 多任务串联工作流：把移动/剪辑转码/字幕生成编排成一条可自动执行的流水线
        workflow_tab = WorkflowTab(self.notebook, controller=self)
        self.tabs['workflow'] = workflow_tab
        self.notebook.add(workflow_tab.get_frame(), text="工作流串联(workflow_tab)")

        # 4. 操作日志选项卡（必须最后初始化，因为其他tab可能调用它记录日志）
        log_tab = LogTab(self.notebook, controller=self)
        self.tabs['log'] = log_tab

    def _load_config_to_tabs(self):
        """加载配置到各选项卡"""
        # 各选项卡自行管理配置加载，这里可以添加额外的全局配置同步
        pass

    def _on_close(self):
        """关闭主窗口：先释放各选项卡占用的资源（如翻译模型显存），再销毁窗口"""
        try:
            for name, tab in self.tabs.items():
                cleanup = getattr(tab, 'cleanup', None)
                if callable(cleanup):
                    try:
                        cleanup()
                    except Exception as e:
                        print(f"清理选项卡[{name}]资源失败: {e}")
        except Exception as e:
            print("清理资源失败:", e)
        self.root.destroy()

def prepare_translation_env():
    """准备本地翻译模型的运行环境

    Windows 下 ctranslate2 / llama.dll 需要 cublas、cudnn 等 CUDA 运行库，
    必须在导入推理库之前把相关目录注册进 DLL 搜索路径，否则 GPU 模式会加载失败。
    同时输出环境摘要，便于确认 Sakura / NLLB 能否正常载入。
    """
    try:
        ensure_cuda_dll_path()
        report = translation_env_report()
        print("翻译环境: " + report)
        if 'llama_cpp=未安装' in report:
            print("警告: 当前解释器缺少 llama-cpp-python，Sakura 系列模型无法载入。"
                  f"当前解释器: Python {sys.version.split()[0]}（{sys.executable}）；"
                  "请改用已安装该包的解释器启动，例如 py -3.12 main.py")
    except Exception as e:
        print("翻译环境准备失败:", e)


def main():
    """程序入口"""
    prepare_translation_env()
    root = tk.Tk()
    app = FileMoverGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()