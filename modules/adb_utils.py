# -*- coding: utf-8 -*-
"""adb 可执行文件路径与 run_adb / run_adb_binary 底层调用。"""

import subprocess

from utils import _find_bundled

ADB_PATH = _find_bundled("adb") or ""

def set_adb_path(path):
    """运行时切换 adb 路径（设置界面里可自定义）"""
    global ADB_PATH
    ADB_PATH = path

def run_adb(*args, timeout=15):
    """调用 adb 命令。subprocess 二进制模式 + decode('utf-8')，避免 GBK 编码错误"""
    cmd = [ADB_PATH] + list(args)
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        return result.returncode, out.strip(), err.strip()
    except Exception as e:
        return -1, "", str(e)

def run_adb_binary(*args, timeout=25):
    """调用 adb 并返回原始二进制输出（用于截屏）"""
    cmd = [ADB_PATH] + list(args)
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.returncode, result.stdout, result.stderr.decode("utf-8", errors="replace")
    except Exception as e:
        return -1, b"", str(e)
