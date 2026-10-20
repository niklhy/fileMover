#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作流串联模块（多任务串联）

把已有的「文件移动」「视频剪辑转码」「字幕生成」三个功能打通为可编排的工作流：

    A 文件夹 →(移动)→ B 文件夹 →(剪辑转码)→ →(移动)→ C 文件夹 →(生成字幕)→ 完成

对外主要接口：
    workflow.models     数据模型（唯一标识 UID、步骤、任务）
    workflow.steps      各步骤的执行器（复用现有模块，不重复实现）
    workflow.executor   无人值守执行引擎
"""

__version__ = "1.0.0"
