@echo off
chcp 65001 >nul
title 爽哥刷金币助手 V2（源码版启动器）
cd /d "%~dp0"
if not exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe" (
    echo [错误] 未找到 Python 3.12: C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
    echo 请先安装 Python 3.12，或修改本 bat 中的解释器路径。
    pause
    exit /b 1
)
start "" "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe" "%~dp0爽哥刷金币助手.py"
