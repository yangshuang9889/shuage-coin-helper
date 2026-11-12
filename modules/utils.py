# -*- coding: utf-8 -*-
"""通用小工具函数。"""

import os
import sys

def _find_on_path(name):
    """在系统 PATH 中查找可执行文件完整路径（支持 .exe 后缀），找不到返回空串"""
    try:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            for cand in (os.path.join(d, name), os.path.join(d, name + ".exe")):
                if os.path.isfile(cand):
                    return cand
    except Exception:
        pass
    return ""

def _find_bundled(name):
    """优先在程序目录 bin 子目录查找自带的 adb/scrcpy.exe（安装包内置），再回退到系统 PATH"""
    roots = []
    try:
        if getattr(sys, "frozen", False):
            roots.append(os.path.dirname(os.path.abspath(sys.executable)))
        else:
            roots.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    except Exception:
        pass
    try:
        # 兜底：模块文件所在目录的上一级（源码运行时无论从哪个目录启动都能找到 bin）
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except Exception:
        pass
    for base in roots:
        try:
            cand = os.path.join(base, "bin", name + ".exe")
            if os.path.isfile(cand):
                return cand
        except Exception:
            continue
    return _find_on_path(name)
