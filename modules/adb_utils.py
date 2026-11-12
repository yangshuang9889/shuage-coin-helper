# -*- coding: utf-8 -*-
"""adb 可执行文件路径与 run_adb / run_adb_binary / screencap_bgr 底层调用。"""

import struct
import subprocess
import time

import cv2
import numpy as np

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


def _raw_to_bgr(raw):
    """解析 screencap 的 raw 输出。

    Android 13+ 的 raw 是 16 字节头（宽、高、像素格式、色彩空间）+ RGBA_8888 数据；
    没有头或长度对不上一律返回 None，由调用方回退 PNG（老机型兼容）。
    """
    if len(raw) < 16:
        return None
    w, h, _fmt, _cs = struct.unpack("<4I", raw[:16])
    if not w or not h or w * h > 40000000:
        return None
    body = len(raw) - 16
    if body == w * h * 4:
        arr = np.frombuffer(raw, np.uint8, count=w * h * 4, offset=16).reshape(h, w, 4)[:, :, :3]
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if body == w * h * 3:
        arr = np.frombuffer(raw, np.uint8, count=w * h * 3, offset=16).reshape(h, w, 3)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return None


# 每台设备最近一次截图耗时（raw/png 各记一次），用于自动选更快的那种方式
_SHOT_STAT = {}


def screencap_bgr(serial, timeout=25):
    """截屏并直接解码成 BGR 图像，返回 (code, img, err)。

    raw 方式省掉设备端的 PNG 编码（实测无线约 1.2 秒 vs 2.8 秒），
    但个别设备/链路反而 PNG 更快，所以按最近实测耗时自动择优，失败自动换另一种。
    """
    stat = _SHOT_STAT.setdefault(serial, {"raw": None, "png": None})
    order = ["raw", "png"]
    if stat["raw"] is not None and stat["png"] is not None and stat["png"] < stat["raw"]:
        order = ["png", "raw"]
    last_err = ""
    for mode in order:
        t0 = time.time()
        try:
            if mode == "raw":
                code, data, err = run_adb_binary("-s", serial, "exec-out", "screencap", timeout=timeout)
                if code != 0 or not data:
                    last_err = err or "raw 截图无数据"
                    continue
                img = _raw_to_bgr(data)
                if img is None:
                    last_err = "raw 格式无法识别（回退 PNG）"
                    continue
            else:
                code, data, err = run_adb_binary("-s", serial, "exec-out", "screencap", "-p", timeout=timeout)
                if code != 0 or not data:
                    last_err = err or "截屏无数据"
                    continue
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    last_err = "PNG 解码失败"
                    continue
        except Exception as e:  # 超时等异常不应中断刷视频主循环
            last_err = str(e)
            continue
        stat[mode] = time.time() - t0
        return 0, img, ""
    return -1, None, last_err or "截屏失败"
