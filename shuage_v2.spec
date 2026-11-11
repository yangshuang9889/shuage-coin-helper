# -*- mode: python ; coding: utf-8 -*-
"""爽哥刷金币助手 V2 打包配置（纯 Python 源码版，不依赖 Cython pyd）

与 V1 的差异：
- 不再 glob modules/*.pyd 打进 binaries，改为把 modules 加入 pathex 让分析器直接吃 .py 源码
- bin 目录（adb/scrcpy）随 [COLLECT] 输出目录附带，安装包负责分发
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# 让 PyInstaller 从 modules 目录解析 constants/adb_utils/app_config/... 等纯 py 模块
pathex = ['modules']

for pkg in ('vosk', 'sounddevice', 'win32com', 'win32api', 'pythoncom', 'cv2', 'numpy'):
    ret = collect_all(pkg)
    datas += ret[0]
    binaries += ret[1]
    hiddenimports += ret[2]

hiddenimports += [
    'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.simpledialog',
    'constants', 'adb_utils', 'app_config', 'devices', 'swipe',
    'ui', 'logger', 'scrcpy', 'speech', 'voice_config', 'utils',
]

a = Analysis(
    ['爽哥刷金币助手.py'],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='爽哥刷金币助手',
    debug=False,
    bootloader_ignore_signals=False,
    contents_directory='_lib',
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='版本信息.txt',
    icon=['appicon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ShuageCoinHelperV2',
)
