@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 翻译模型依赖（llama-cpp-python / ctranslate2 / sentencepiece）装在 Python 3.12 环境，
rem 必须用 3.12 启动，默认的 Python 3.14 缺少 llama-cpp-python 且无预编译 wheel。
py -3.12 main.py
pause
