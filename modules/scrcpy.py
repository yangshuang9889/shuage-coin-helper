# -*- coding: utf-8 -*-
"""scrcpy 投屏相关常量与方法（自动拉起等，无任何手动设置/操作入口）。"""

import os
import subprocess
import tkinter.messagebox as messagebox

from utils import _find_bundled

SCRCPY_EXE = _find_bundled("scrcpy") or ""


class ScrcpyMixin:
    def _mark_scrcpy_launched(self, serial):
        """记录已拉起 scrcpy 投屏的设备 serial，避免同一设备重复拉起多个窗口"""
        if not hasattr(self, "_scrcpy_launched"):
            self._scrcpy_launched = set()
        self._scrcpy_launched.add(serial)

    def _already_scrcpy_launched(self, serial):
        return serial in getattr(self, "_scrcpy_launched", ())

    def launch_scrcpy(self):
        """手动打开投屏：对已勾选设备（无勾选则取列表第一台）启动 scrcpy，并自动将设备媒体音量静音"""
        serial = self._selected_serial()
        if not serial:
            self.log("没有可用设备，请先点击「检测设备」")
            return
        if self._already_scrcpy_launched(serial):
            self.log("设备 " + serial + " 的 scrcpy 投屏已启动，跳过重复打开")
            return
        exe = (self.config.get("scrcpy") or "").strip() or SCRCPY_EXE
        if not os.path.isfile(exe):
            self.log("⚠ 未找到 scrcpy.exe，请将 scrcpy.exe 放入程序目录 bin 或加入系统 PATH")
            return
        try:
            subprocess.Popen([exe, "-s", serial], cwd=os.path.dirname(exe) or None)
            self._mark_scrcpy_launched(serial)
            self.log("已打开 scrcpy 投屏（设备 " + serial + "）")
            self._mute_device(serial)
        except Exception as e:
            self.log("⚠ 启动 scrcpy 失败: " + str(e))

    def _mute_device(self, serial):
        """打开投屏后自动将设备媒体音量静音（音量 0）；静音失败不影响投屏"""
        from adb_utils import run_adb
        code, out, err = run_adb("-s", serial, "shell", "media", "volume",
                                 "--stream", "3", "--set", "0", timeout=10)
        if code == 0:
            self.log("已静音设备媒体音量（" + serial + "）")
        else:
            self.log("⚠ 静音失败（" + serial + "）: " + (err or out or "adb 调用失败"))

    def _update_start_btn_state(self):
        """「开始刷」不再依赖投屏：无论是否找到 scrcpy.exe，按钮均可用；
        运行中由 start/stop 管理按钮状态"""
        if self.running:
            return  # 运行中由 start/stop 管理按钮状态
        self.btn_start.config(state="normal")

