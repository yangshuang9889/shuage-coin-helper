@echo off
chcp 65001 >nul
title 爽哥刷金币助手 V2（源码版启动器）
cd /d "%~dp0"
rem 解释器查找顺序：PATH 中的 pythonw → Python 3.12 默认安装目录 → 下面的自定义路径
set "PYW="

for %%I in (pythonw.exe) do if not "%%~$PATH:I"=="" set "PYW=%%~$PATH:I"

if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"

rem 若上面都没找到，可在此手动指定解释器绝对路径后取消下一行注释
rem set "PYW=D:\Python312\pythonw.exe"

if not defined PYW (
    echo [错误] 未找到 pythonw.exe
    echo 请安装 Python 3.12 并加入 PATH，或编辑本文件为 PYW 指定解释器路径。
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0爽哥刷金币助手.py"
