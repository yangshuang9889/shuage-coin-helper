# -*- coding: utf-8 -*-
"""
爽哥刷金币助手（多设备版）- 基于 scrcpy 投屏 + adb 实现自动刷短视频 + 赚金币任务
- 自动检测所有已连接设备（无线/USB 均可），勾选哪几台刷哪几台
- 每台手机独立线程、独立随机节奏（互不干扰，更自然）
- 随机间隔自动上滑刷视频，按概率偶尔下滑回看
- 误触检测：离开目标应用自动按返回/重启恢复
- 赚金币任务：防息屏、自动签到、宝箱定时点击（坐标采集助手）
- 支持手动 adb connect 添加无线设备
操作人：杨爽
"""

import os, sys, threading
import tkinter as tk
from tkinter import ttk

# 编译模块统一存放于 modules 目录（开发运行时注入；打包后 pyd 已位于 _internal 无需此路径）
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules"))

from constants import APP_VERSION, APP_NOTICE_URL, APP_SPONSOR_MD, APP_SPONSOR_QR, APP_UPDATE_URL, _APP_ICON_16, _APP_ICON_32, _APP_ICON_64
from adb_utils import set_adb_path
from scrcpy import SCRCPY_EXE
from logger import LoggerMixin
from app_config import ConfigMixin
from devices import DevicesMixin
from swipe import SwipeMixin
from voice_config import VoiceConfigMixin
from speech import SpeechMixin
from scrcpy import ScrcpyMixin
from ui import UiMixin
class VideoSwiperApp(LoggerMixin, ConfigMixin, DevicesMixin, SwipeMixin, VoiceConfigMixin, SpeechMixin, ScrcpyMixin, UiMixin):
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("爽哥刷金币助手·多设备版 (scrcpy + adb) " + APP_VERSION)
        # 窗口尺寸按屏幕自适应，避免小屏超出显示范围
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win_w, win_h = min(860, sw - 60), min(800, sh - 90)
        self.root.geometry("%dx%d" % (win_w, win_h))
        self.root.minsize(min(800, win_w), min(760, win_h))
        self.root.resizable(True, True)

        self.running = False
        self.stop_event = threading.Event()
        self.pause_events = {}     # serial -> threading.Event（单台暂停标志，set=暂停）
        self.workers = {}          # serial -> Thread
        # 语音控制（离线 vosk）线程状态
        self.voice_thread = None          # 语音监听线程（daemon）
        self.voice_event = threading.Event()  # 语音监听退出标志
        self.voice_last_action = 0.0      # 语音命令去抖：上次触发动作时间戳
        # 语音提醒（设备上下线/异常）状态
        self._prev_devices = set()        # 上次检测到的设备集合（用于计算新上线/下线设备）
        self._device_detect_lock = threading.Lock()  # 设备检测串行锁（手动按钮与自动轮询共用）
        self._last_detect_sig = set()     # 最近一次检测的正常设备集合（自动轮询据此判断是否有变化）
        self._device_ips = {}             # serial -> USB 自动开启无线后获取的 IP（用于显示无线连接地址）
        self._auto_detect_thread = None   # 自动检测线程（daemon）
        self._scrcpy_launched = set()     # serial -> 已拉起 scrcpy 投屏（启动/开始刷/手动打开共用去重）
        self._swipe_fail_count = {}       # serial -> 连续滑动失败次数（达到阈值播报「刷视频异常」）
        self.device_notes = {}            # serial -> 备注名（设备备注，存配置 device_notes 键）
        self.counters = {}         # serial -> int
        self.device_widgets = {}   # serial -> {"var", "count", "state"}
        self.screen_sizes = {}     # serial -> (w, h)
        self.target_apps = {}      # serial -> 开始刷时的前台应用包名
        self.saved_timeouts = {}   # serial -> 原始息屏时间(毫秒)
        self.last_box_click = {}   # serial -> 上次点宝箱时间戳
        self.last_wake = {}        # serial -> 上次唤醒时间戳
        self.last_box_hint = 0     # 缺坐标提示节流
        self.last_img_check = {}   # serial -> 上次图像任务检查时间
        self.img_cooldown = {}     # (serial, 模板名) -> 命中冷却
        self.disappear_pos = {}    # (serial, 模板名) -> 等待消失模板最近出现位置 (cx, cy, 时间戳)
        self.last_wait_log = {}    # (serial, 模板名) -> 上次等待日志时间戳
        self.last_ui_check = {}    # serial -> 上次界面异常检查时间戳（节流）
        self.ui_correct_fail = {}  # serial -> 界面异常连续纠正失败次数
        self.ui_last_state = {}    # serial -> 最近一次界面状态（日志去重）
        self.ui_freeze = {}        # serial -> [上次画面签名, 连续相同次数]（画面停滞检测）
        self.ui_force_restart = {} # serial -> 连续强杀重启目标应用次数（超过上限才停线程）
        self.task_done_flags = {}  # serial -> 任务完成标志（识别到 task_done 模板后置 True，停止该设备线程）

        # 图像模板 {"模板名": {"pkg", "file", "kind"}}
        self.data_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.tpl_dir = os.path.join(self.data_dir, "templates")
        os.makedirs(self.tpl_dir, exist_ok=True)
        self._tpl_display = {}     # 下拉框显示文本 -> 模板名
        # 数据文件统一为单一「爽哥刷金币助手配置.json」（坐标+配置合并，V1.0.2.0 起）
        self.config_file = os.path.join(self.data_dir, "爽哥刷金币助手配置.json")
        # 启动迁移：旧 坐标.json（或旧 坐标.json + 配置.json）→ 合并写入配置.json，校验成功后才删旧文件
        self._unified_migrated = self._migrate_unified_config()
        # coords 与 config 均读写同一统一文件
        self.coords_file = self.config_file
        self.coords = {"sign": {}, "box": {}, "templates": {}}
        self._load_coords()
        # 模板按应用分目录：把散落在 templates 根目录的旧模板归入 templates/<应用名>/
        self._tpl_migrated = self._migrate_templates()
        self.config = {"scrcpy": "", "adb": "", "ui": {}, "notice_seen": ""}
        self._load_config()
        # 旧版投屏配置（qtscrcpy 键）迁移：旧值有值且同目录存在 scrcpy.exe → 迁移到 scrcpy 键；否则置空；旧键不再写出
        if "qtscrcpy" in self.config:
            qt_old = self.config.get("qtscrcpy") or ""
            cand = os.path.join(os.path.dirname(qt_old), "scrcpy.exe") if qt_old else ""
            if not self.config.get("scrcpy") and cand and os.path.isfile(cand):
                self.config["scrcpy"] = cand
            self.config.pop("qtscrcpy", None)
            self._save_config()
        # 设备备注（serial -> 备注名）：老配置缺键时默认空字典兼容迁移
        notes = self.config.get("device_notes")
        self.device_notes = notes if isinstance(notes, dict) else {}
        # 首次运行生成配置文件，方便查看/备份
        if not os.path.isfile(self.config_file):
            self._save_config()

        # 应用已保存的 adb 自定义路径
        adb_saved = self.config.get("adb", "")
        if adb_saved and os.path.isfile(adb_saved):
            set_adb_path(adb_saved)
        # 更新/公告/赞助二维码/赞助名单地址已硬编码写死（固定地址，不再从配置文件读取）
        self.update_url = APP_UPDATE_URL
        self.notice_url = APP_NOTICE_URL
        self.sponsor_qr = APP_SPONSOR_QR
        self.sponsor_md = APP_SPONSOR_MD

        self._build_ui()
        # 恢复上次保存的勾选项和参数
        self._restore_ui_state()
        # 上次开启过语音控制则启动自动恢复监听（缺失依赖/模型时仅日志提示并自动关闭，不弹窗）
        if self.var_voice_control.get():
            self._start_voice_listener(show_warning=False, welcome_text="欢迎使用爽哥刷金币助手")
        if self._tpl_migrated:
            self.log("已按应用整理模板目录：%d 个模板归档到 templates/<应用名>/ 子目录"
                     % self._tpl_migrated)
        if self._unified_migrated:
            self.log("配置统一迁移完成：坐标与配置已合并到 爽哥刷金币助手配置.json，旧文件已删除（数据完整保留）")
        # 关闭窗口前保存 UI 状态
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 启动检查更新（未配置更新地址则跳过）
        self.check_update(manual=False)
        # 启动加载公告（未配置公告地址则跳过；与上次内容不同才弹窗提醒）
        self.load_notice(manual=False)
        # 自动检测设备：启动即检测一次，此后后台每 3 秒轮询设备接入/拔出（无需手动点「检测设备」）
        self._start_auto_detect()

def main():
    root = tk.Tk()
    try:
        # 窗口/任务栏图标：与 exe 内嵌图标统一（内嵌 base64，运行时不依赖外部 ico 文件）
        _win_icons = [tk.PhotoImage(data=_APP_ICON_16),
                      tk.PhotoImage(data=_APP_ICON_32),
                      tk.PhotoImage(data=_APP_ICON_64)]
        root.iconphoto(True, *_win_icons)
    except Exception:
        pass
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    VideoSwiperApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
