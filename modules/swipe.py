# -*- coding: utf-8 -*-
"""刷视频核心（滑动循环/坐标/模板/界面异常纠正/签到/启动停止）。"""

import hashlib
import os
import random
import re
import shutil
import threading
import time
from tkinter import filedialog
from tkinter import messagebox
from tkinter import simpledialog
import tkinter as tk
from tkinter import ttk

from adb_utils import run_adb, run_adb_binary, screencap_bgr
from app_config import app_dir_name
from constants import APP_NAMES, APP_VERSION, LAUNCHER_PKGS, MATCH_THRESHOLD, SWIPE_FAIL_SPEAK_THRESHOLD, TPL_COMMON_DIR, UI_CHECK_INTERVAL, UI_CORRECT_MAX_FAIL, UI_FORCE_RESTART_LIMIT, UI_FREEZE_MAX_FAIL, UI_RECOVER_MAX_ATTEMPTS, WAIT_DISAPPEAR_COOLDOWN, WAIT_DISAPPEAR_POLL, WAIT_DISAPPEAR_TIMEOUT

try:
    import cv2
    import numpy as np
    CV_OK = True
except ImportError:
    CV_OK = False


def _region_name_of(meta):
    """模板区域显示名：优先取自定义名称 region_name；旧数字配置（region）兼容为「区域N」"""
    return (meta.get("region_name") or "").strip() or ("区域%s" % meta.get("region", 1))


class SwipeMixin:
    _TPL_KIND_NAMES = {"task": "任务按钮", "close": "广告关闭按钮×",
                       "wait_appear": "等待元素出现", "wait_disappear": "等待元素消失",
                       "live": "直播间", "wait_back": "等待元素出现（出现即按返回键）",
                       "captcha": "验证码",
                       "slider_gap": "滑块缺口", "slider_btn": "滑块按钮",
                       "task_done": "任务完成（识别到即停止刷）"}
    # 模板类型下拉选项：value 为内部英文 key（存储/匹配不变），显示为中文说明
    _TPL_KIND_CHOICES = [("task", "任务按钮（出现即点）"), ("close", "广告关闭按钮×（通用）"),
                         ("wait_appear", "等待元素出现"), ("wait_disappear", "等待元素消失（盯到它消失再点原位置）"),
                         ("live", "直播间（命中即返回键退出）"),
                         ("wait_back", "等待元素出现（出现即按返回键）"),
                         ("captcha", "验证码（命中即暂停设备提醒）"),
                         ("slider_gap", "滑块缺口（滑块验证码需滑到的位置）"),
                         ("slider_btn", "滑块按钮（滑块验证码底部拖柄）"),
                         ("task_done", "任务完成（识别到即停止刷）")]
    _TPL_KIND_DISPLAY = dict(_TPL_KIND_CHOICES)          # key -> 中文
    _TPL_KIND_REVERSE = {n: k for k, n in _TPL_KIND_CHOICES}  # 中文 -> key

    def _selected_serial(self):
        """返回第一台已勾选设备的 serial；没有勾选时取列表第一台（设备行按检测顺序排列）"""
        if getattr(self, "device_widgets", None):
            for s, w in self.device_widgets.items():
                if w["var"].get():
                    return s
            return next(iter(self.device_widgets), None)
        return None

    def _read_screen_size(self, serial):
        code, out, _ = run_adb("-s", serial, "shell", "wm size")
        size = ""
        if code == 0:
            if "Override size" in out:
                size = out.split("Override size:")[1].strip()
            elif "Physical size" in out:
                size = out.split("Physical size:")[1].strip()
        try:
            w, h = size.split("x")
            self.screen_sizes[serial] = (int(w), int(h))
        except Exception:
            self.screen_sizes[serial] = (1080, 2400)
            self.log(serial + " 无法解析分辨率，使用默认 1080x2400")
        return self.screen_sizes[serial]

    def _get_foreground(self, serial):
        """获取当前前台应用包名，失败返回 None"""
        code, out, _ = run_adb("-s", serial, "shell",
                               "dumpsys window | grep mCurrentFocus", timeout=15)
        if code != 0 or not out:
            return None
        m = re.search(r"([a-zA-Z0-9_.]+)/", out)
        return m.group(1) if m else None

    def _launch_app(self, serial, pkg):
        """显式拉起目标应用：优先 resolve-activity + am start，失败再 monkey 兜底。返回是否成功拉起"""
        # 1) resolve-activity 拿到主 Activity 后显式 am start（比 monkey 更稳，冷启动/崩溃后恢复快）
        code, out, _ = run_adb("-s", serial, "shell",
                               "cmd package resolve-activity --brief %s" % pkg, timeout=15)
        if code == 0 and out:
            m = re.search(r"([a-zA-Z0-9_.]+/[a-zA-Z0-9_.]+)$", out.strip().splitlines()[-1])
            if m:
                target = m.group(1)
                run_adb("-s", serial, "shell",
                        "am start -W -n %s" % target, timeout=25)
                time.sleep(2.5)
                fg = self._get_foreground(serial)
                if fg == pkg:
                    return True
        # 2) monkey 兜底（部分机型 resolve 异常时仍可拉起）
        code, _, err = run_adb("-s", serial, "shell",
                               "monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg,
                               timeout=20)
        if code != 0:
            self.log("%s ❌ 无法启动 %s: %s" % (serial, pkg, err))
            return False
        time.sleep(3)
        fg = self._get_foreground(serial)
        return fg == pkg

    def _force_restart_target(self, serial):
        """强杀+重启目标应用：应对应用崩溃/白屏/永久卡死。返回 True=已回到目标应用"""
        target = self.target_apps.get(serial)
        if not target:
            return False
        self.log_warning("%s 强制重启目标应用 %s …" % (serial, target))
        run_adb("-s", serial, "shell", "am force-stop %s" % target, timeout=15)
        time.sleep(1.5)
        return self._launch_app(serial, target)

    def _recover(self, serial, target_pkg):
        """误触弹出其他应用时自动恢复：先按返回键，不行就显式重启目标应用"""
        for i in range(2):
            if self.stop_event.is_set():
                return False
            run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)  # 返回键
            self.log("%s 已按返回键尝试关闭弹窗 (%d/2)" % (serial, i + 1))
            time.sleep(1.5)
            fg = self._get_foreground(serial)
            if fg is not None and fg == target_pkg:
                self.log("%s ✅ 已返回刷视频界面" % serial)
                return True
        # 返回键无效，显式重启目标应用（resolve-activity + am start，monkey 兜底）
        self.log("%s 返回键无效，正在重新拉起目标应用 %s …" % (serial, target_pkg))
        return self._launch_app(serial, target_pkg)

    def _wait_on_target(self, serial):
        """确保手机停在目标应用上；离开则自动恢复。返回 True=可以继续刷"""
        target = self.target_apps.get(serial)
        if not target:
            return True
        fg = self._get_foreground(serial)
        if fg is None or fg == target:
            return True

        self.log("%s ⚠️ 检测到离开刷视频界面（当前: %s）" % (serial, fg))
        self.set_status("%s 被弹窗/其他应用挡住，正在恢复…" % serial)
        w = self.device_widgets.get(serial)
        if w:
            self.root.after(0, lambda: w["state"].config(text="恢复中", foreground="orange"))

        attempts = 0  # 恢复循环轮次计数（超限强制重启目标应用，避免永久卡死）
        while not self.stop_event.is_set():
            # 单台暂停期间不执行恢复操作：阻塞等待继续或全局停止
            pe = self.pause_events.get(serial)
            if pe is not None and pe.is_set():
                w = self.device_widgets.get(serial)
                if w:
                    self.root.after(0, lambda: w["state"].config(text="已暂停", foreground="orange"))
                while pe.is_set() and not self.stop_event.is_set():
                    time.sleep(0.2)
                if self.stop_event.is_set():
                    return False
                if w:
                    self.root.after(0, lambda: w["state"].config(text="恢复中", foreground="orange"))
                break  # 恢复后重新检查前台是否回到目标应用
            if self.var_recover.get():
                if self._recover(serial, target):
                    break
            else:
                # 未开启自动恢复：只等待用户手动处理，每 5 秒复查
                time.sleep(5)
                fg = self._get_foreground(serial)
                if fg is not None and fg == target:
                    break
            # 恢复循环轮次上限：多轮仍拉不回说明应用已崩溃/无法恢复，强制重启再给机会
            attempts = attempts + 1
            if attempts >= UI_RECOVER_MAX_ATTEMPTS:
                self.log_warning("%s 连续 %d 轮未能回到 %s，强制重启目标应用…"
                                 % (serial, UI_RECOVER_MAX_ATTEMPTS, target))
                if self._force_restart_target(serial):
                    attempts = 0
                    continue
                # 强杀重启也失败：累计到上限则停止该设备线程，避免永久卡死
                self.ui_force_restart[serial] = self.ui_force_restart.get(serial, 0) + 1
                if self.ui_force_restart[serial] >= UI_FORCE_RESTART_LIMIT:
                    self._ui_correct_give_up(serial)
                    return False
                attempts = 0

        if self.stop_event.is_set():
            return False
        if w:
            self.root.after(0, lambda: w["state"].config(text="运行中", foreground="green"))
        self.set_status("运行中… %s 已恢复" % serial)
        return True

    def _enable_stayon(self, serial):
        if not self.var_stayon.get():
            return
        code, out, _ = run_adb("-s", serial, "shell", "settings get system screen_off_timeout")
        if code == 0 and out.isdigit():
            self.saved_timeouts[serial] = int(out)
        run_adb("-s", serial, "shell", "settings put system screen_off_timeout 1800000")
        run_adb("-s", serial, "shell", "input keyevent 224")  # 唤醒
        self.last_wake[serial] = time.time()
        self.log("%s 防息屏已开启（30 分钟内不息屏，停止后自动还原）" % serial)

    def _disable_stayon(self, serial):
        if serial in self.saved_timeouts:
            run_adb("-s", serial, "shell",
                    "settings put system screen_off_timeout %d" % self.saved_timeouts[serial])
            self.log("%s 息屏时间已还原" % serial)
        self.last_wake.pop(serial, None)

    def _keep_awake(self, serial):
        """每 10 分钟唤醒一次，防止长时间挂机息屏"""
        if not self.var_stayon.get():
            return
        if time.time() - self.last_wake.get(serial, 0) > 600:
            run_adb("-s", serial, "shell", "input keyevent 224")
            self.last_wake[serial] = time.time()

    def capture_coords(self):
        devices = self._checked_devices() or list(self.device_widgets.keys())
        if not devices:
            messagebox.showwarning("提示", "请先连接并检测设备")
            return
        serial = devices[0]
        self.log("正在截取 " + serial + " 的屏幕…")
        code, png, err = run_adb_binary("-s", serial, "exec-out", "screencap", "-p")
        if code != 0 or not png:
            messagebox.showerror("错误", "截屏失败: " + (err or "无数据"))
            return
        png_path = os.path.join(self.data_dir, "screenshot_tmp.png")
        try:
            with open(png_path, "wb") as f:
                f.write(png)
        except Exception as e:
            messagebox.showerror("错误", "保存截图失败: " + str(e))
            return

        fg = self._get_foreground(serial) or "unknown"
        self._show_capture_window(png_path, fg)

    def rename_template_dir(self):
        """把选中模板移到别的分类目录（可输入新名字 = 自定义分类）"""
        disp = self.cmb_tpl.get()
        name = getattr(self, "_tpl_display", {}).get(disp, disp)
        meta = self.coords["templates"].get(name)
        if not isinstance(meta, dict):
            messagebox.showwarning("提示", "请先选择要改分类的模板")
            return
        old_dir = (meta.get("dir") or "").strip() or app_dir_name(meta.get("pkg", ""))
        new = simpledialog.askstring(
            "修改模板分类",
            "模板「%s」当前分类: %s\n\n输入新的分类目录名（可自定义任意名字）："
            % (name, old_dir),
            initialvalue=old_dir, parent=self.root)
        if new is None:
            return
        new = re.sub(r'[\\/:*?"<>|（）]', "_", new).strip()
        if not new or new == old_dir:
            return
        src = self._tpl_path(meta)
        target = os.path.join(self.tpl_dir, new, os.path.basename(src)) if src else ""
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if src and os.path.isfile(src) and \
                    os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(target)):
                shutil.move(src, target)
        except Exception as e:
            messagebox.showerror("错误", "移动模板文件失败: " + str(e))
            return
        meta["dir"] = new
        if target:
            meta["file"] = os.path.relpath(target, self.data_dir).replace("\\", "/")
        self._save_coords()
        self._refresh_tpl_list()
        # 旧分类目录空了就顺手清掉（只删空目录，安全）
        old_path = os.path.join(self.tpl_dir, old_dir)
        try:
            if os.path.isdir(old_path) and not os.listdir(old_path):
                os.rmdir(old_path)
        except Exception:
            pass
        self.log("📁 模板「%s」分类已改为: %s" % (name, new))

    def open_tpl_dir(self):
        """打开模板目录（内含按应用命名的子目录），方便查看/备份/手工整理"""
        try:
            os.makedirs(self.tpl_dir, exist_ok=True)
            os.startfile(self.tpl_dir)
        except Exception as e:
            messagebox.showwarning("提示", "打开模板目录失败: " + str(e))

    def _refresh_tpl_list(self):
        """模板下拉框按应用分组显示：应用名 · 模板名"""
        items = []
        for name, meta in self.coords["templates"].items():
            m = meta if isinstance(meta, dict) else {}
            items.append((m.get("dir") or app_dir_name(m.get("pkg", "")), name))
        items.sort(key=lambda x: (x[0], x[1]))
        self._tpl_display = {}
        for sub, name in items:
            disp = "%s · %s" % (sub, name)
            while disp in self._tpl_display:   # 极端重名兜底
                disp += " "
            self._tpl_display[disp] = name
        self.cmb_tpl["values"] = list(self._tpl_display.keys())
        if self._tpl_display:
            self.cmb_tpl.current(0)
        else:
            self.cmb_tpl.set("")
        cnt = {}
        for sub, _n in items:
            cnt[sub] = cnt.get(sub, 0) + 1
        if items:
            detail = " / ".join("%s %d" % (k, v) for k, v in sorted(cnt.items()))
            self.lbl_tpl.config(text="共 %d 个模板（%s）" % (len(items), detail))
        else:
            self.lbl_tpl.config(text="暂无模板，模板文件按应用存放在 templates/<应用名>/")

    def delete_template(self):
        disp = self.cmb_tpl.get()
        name = getattr(self, "_tpl_display", {}).get(disp, disp)
        if not name or name not in self.coords["templates"]:
            messagebox.showwarning("提示", "没有可删除的模板")
            return
        meta = self.coords["templates"].pop(name, None)
        path = self._tpl_path(meta) if isinstance(meta, dict) else ""
        if path and os.path.isfile(path):
            try:
                os.remove(path)
                # 顺手清掉空的子目录（只删空目录，安全）
                d = os.path.dirname(path)
                if os.path.normcase(d) != os.path.normcase(self.tpl_dir) and not os.listdir(d):
                    os.rmdir(d)
            except Exception:
                pass
        self._save_coords()
        self._refresh_tpl_list()
        self.log("已删除图像模板: " + name)

    def edit_template(self):
        """编辑选中模板：模板名/应用包名/类型/识别阈值/分类/点击偏移/出现后操作/备注/更换图片"""
        disp = self.cmb_tpl.get()
        name = getattr(self, "_tpl_display", {}).get(disp, disp)
        if not name or name not in self.coords["templates"]:
            messagebox.showwarning("提示", "请先在左侧下拉框选中要编辑的模板")
            return
        self._open_template_editor(name=name)

    def add_template_local(self):
        """新增模板：直接选一张本地图片，填参数保存（无需手机截图）"""
        path = filedialog.askopenfilename(
            title="选择模板图片（新增模板）",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")])
        if not path:
            return
        self._open_template_editor(name=None, image_path=path)

    def _open_template_editor(self, name=None, image_path=None):
        """模板编辑窗口：name 为空 = 新增（image_path 为所选本地图片）；否则编辑已有模板"""
        is_new = name is None
        meta = {} if is_new else dict(self.coords["templates"].get(name) or {})
        cur_img = "" if is_new else self._tpl_path(meta)
        win = tk.Toplevel(self.root)
        win.title(("新增模板" if is_new else "编辑模板：%s" % name) + " — " + APP_VERSION)
        win.transient(self.root)
        win.grab_set()
        win.geometry("540x700")  # 加「最长等待秒数」一行后调高，避免底部按钮被挤出窗口
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        # 中间列可伸缩，右侧提示列限宽换行，避免挤压输入框/下拉箭头
        frm.grid_columnconfigure(1, weight=1, minsize=210)
        frm.grid_columnconfigure(2, weight=0)

        row = 0
        ttk.Label(frm, text="模板名称 *").grid(row=row, column=0, sticky="e", pady=3)
        var_name = tk.StringVar(value=name or "")
        ttk.Entry(frm, textvariable=var_name, width=30).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Label(frm, text="改名会同步重命名图片", foreground="#888",
                  wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(frm, text="应用包名").grid(row=row, column=0, sticky="e", pady=3)
        pkg_now = meta.get("pkg", "")
        pkg_choices = [""] + ["%s（%s）" % (p, APP_NAMES.get(p, p)) for p in APP_NAMES]
        var_pkg = tk.StringVar(value=("%s（%s）" % (pkg_now, APP_NAMES.get(pkg_now, pkg_now)) if pkg_now else ""))
        ttk.Combobox(frm, textvariable=var_pkg, width=30, values=pkg_choices).grid(
            row=row, column=1, sticky="we", padx=6)
        ttk.Label(frm, text="留空=通用（所有应用匹配）", foreground="#888",
                  wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(frm, text="模板类型").grid(row=row, column=0, sticky="e", pady=3)
        kind_choices = self._TPL_KIND_CHOICES
        kind_display = self._TPL_KIND_DISPLAY
        kind_reverse = self._TPL_KIND_REVERSE
        var_kind = tk.StringVar(value=kind_display.get(meta.get("kind", "task"), "任务按钮"))
        cmb_kind = ttk.Combobox(frm, textvariable=var_kind, width=30,
                                values=[n for _k, n in kind_choices], state="readonly")
        cmb_kind.grid(row=row, column=1, sticky="we", padx=6)
        lbl_kind = ttk.Label(frm, text=kind_display.get(meta.get("kind", "task"), ""),
                             foreground="#888", wraplength=170, justify="left")
        lbl_kind.grid(row=row, column=2, sticky="w")

        def _set_op_state(widget, state):
            # 递归切换出现后操作区块内所有控件状态（含嵌套子 Frame）
            for w in widget.winfo_children():
                try:
                    w.configure(state=state)
                except Exception:
                    pass
                _set_op_state(w, state)

        def on_kind_changed(*_a):
            lbl_kind.config(text=var_kind.get())
            # 出现后操作区块只对「等待元素出现」显示/启用
            state = "normal" if kind_reverse.get(var_kind.get(), "") == "wait_appear" else "disabled"
            for w in (lbl_op, lbl_op_note):
                w.configure(state=state)
            _set_op_state(frm_op, state)

        var_kind.trace_add("write", on_kind_changed)
        row += 1

        ttk.Label(frm, text="任务区域").grid(row=row, column=0, sticky="e", pady=3)
        # 自由文本区域名（如「50个红包」「20看广告」），留空=默认区域；旧数字配置自动显示为「区域N」
        var_region = tk.StringVar(value=_region_name_of(meta))
        ttk.Entry(frm, width=20, textvariable=var_region).grid(row=row, column=1, sticky="w", padx=6)
        ttk.Label(frm, text="给区域起个名字，如：50个红包、20看广告；\n留空=默认区域。运行时按配置顺序分区域完成：\n先做完当前区域全部任务，再做下一个区域", foreground="#888",
                  wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        # 出现后操作（仅「等待元素出现」类型生效）——整体位于「模板类型」正下方作为子区块
        op = meta.get("op")
        var_op = tk.StringVar(value="back" if op == "back" else ("custom" if isinstance(op, (list, tuple)) else "center"))
        lbl_op = ttk.Label(frm, text="出现后操作")
        lbl_op.grid(row=row, column=0, sticky="e", pady=3)
        frm_op = ttk.Frame(frm)
        frm_op.grid(row=row, column=1, sticky="w", padx=6)
        # 第一行：点击元素中心 / 按返回键
        frm_op_rb = ttk.Frame(frm_op)
        frm_op_rb.grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(frm_op_rb, text="点击元素中心", value="center", variable=var_op).pack(
            side="left", padx=(0, 10))
        ttk.Radiobutton(frm_op_rb, text="按返回键", value="back", variable=var_op).pack(
            side="left", padx=10)
        # 第二行：点击位置X/Y偏移（完整文案 + 坐标输入，避免文字截断）
        frm_op_xy = ttk.Frame(frm_op)
        frm_op_xy.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Radiobutton(frm_op_xy, text="点击位置X/Y偏移:", value="custom", variable=var_op).pack(
            side="left", padx=(0, 6))
        op0 = op if isinstance(op, (list, tuple)) and len(op) >= 2 else [0.5, 0.5]
        var_opx = tk.StringVar(value=str(int(float(op0[0]) * 100)))
        var_opy = tk.StringVar(value=str(int(float(op0[1]) * 100)))
        ttk.Spinbox(frm_op_xy, from_=0, to=100, increment=1, width=4, textvariable=var_opx).pack(
            side="left", padx=(2, 1))
        ttk.Label(frm_op_xy, text="%,").pack(side="left", padx=(0, 1))
        ttk.Spinbox(frm_op_xy, from_=0, to=100, increment=1, width=4, textvariable=var_opy).pack(
            side="left", padx=(1, 2))
        ttk.Label(frm_op_xy, text="%（屏幕比例）").pack(side="left")
        lbl_op_note = ttk.Label(frm, text="仅「等待元素出现」类型生效", foreground="#888",
                                wraplength=170, justify="left")
        lbl_op_note.grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(frm, text="识别阈值").grid(row=row, column=0, sticky="e", pady=3)
        var_thr = tk.StringVar(value=str(meta.get("threshold", MATCH_THRESHOLD)))
        ttk.Spinbox(frm, from_=0.50, to=0.98, increment=0.01, width=6,
                    textvariable=var_thr).grid(row=row, column=1, sticky="w", padx=6)
        ttk.Label(frm, text="0.50~0.98，越高越严格（默认 %.2f）" % MATCH_THRESHOLD,
                  foreground="#888", wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(frm, text="最长等待秒数").grid(row=row, column=0, sticky="e", pady=3)
        var_wait = tk.StringVar(value=str(int(meta.get("wait_timeout", WAIT_DISAPPEAR_TIMEOUT))))
        spn_wait = ttk.Spinbox(frm, from_=3, to=120, increment=1, width=6, textvariable=var_wait)
        spn_wait.grid(row=row, column=1, sticky="w", padx=6)
        ttk.Label(frm, text="仅「等待元素消失」生效：命中后\n连续盯防这么久等它消失，超时\n放弃（默认 %d 秒）" % WAIT_DISAPPEAR_TIMEOUT,
                  foreground="#888", wraplength=170, justify="left").grid(row=row, column=2, sticky="w")

        def _sync_wait_state(*_a):
            """最长等待秒数只对「等待元素消失」可选，其它类型置灰"""
            ok = self._TPL_KIND_REVERSE.get(var_kind.get(), "") == "wait_disappear"
            try:
                spn_wait.configure(state="normal" if ok else "disabled")
            except Exception:
                pass

        var_kind.trace_add("write", _sync_wait_state)
        _sync_wait_state()
        row += 1

        ttk.Label(frm, text="分类目录").grid(row=row, column=0, sticky="e", pady=3)
        dir_now = meta.get("dir") or app_dir_name(meta.get("pkg", ""))
        existing_dirs = sorted({m.get("dir") for m in self.coords["templates"].values()
                                if isinstance(m, dict) and (m.get("dir") or "").strip()})
        var_dir = tk.StringVar(value=dir_now)
        ttk.Combobox(frm, textvariable=var_dir, width=30,
                     values=existing_dirs or [dir_now]).grid(row=row, column=1, sticky="we", padx=6)
        row += 1

        ttk.Label(frm, text="点击偏移 %").grid(row=row, column=0, sticky="e", pady=3)
        off = meta.get("click_offset") or []
        try:
            dx0 = int(float(off[0]) * 100) if off and len(off) >= 2 else 0
            dy0 = int(float(off[1]) * 100) if off and len(off) >= 2 else 0
        except Exception:
            dx0 = dy0 = 0
        frm_off = ttk.Frame(frm)
        frm_off.grid(row=row, column=1, sticky="w", padx=6)
        ttk.Label(frm_off, text="X").pack(side="left")
        var_dx = tk.StringVar(value=str(dx0))
        ttk.Spinbox(frm_off, from_=-100, to=100, increment=1, width=5,
                    textvariable=var_dx).pack(side="left", padx=(2, 8))
        ttk.Label(frm_off, text="Y").pack(side="left")
        var_dy = tk.StringVar(value=str(dy0))
        ttk.Spinbox(frm_off, from_=-100, to=100, increment=1, width=5,
                    textvariable=var_dy).pack(side="left", padx=(2, 0))
        ttk.Label(frm, text="相对模板宽高 %，0,0=模板中心", foreground="#888",
                  wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(frm, text="备注").grid(row=row, column=0, sticky="e", pady=3)
        var_note = tk.StringVar(value=meta.get("note", ""))
        ttk.Entry(frm, textvariable=var_note, width=30).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Label(frm, text="可选，仅作说明", foreground="#888",
                  wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Label(frm, text="绑定操作 App").grid(row=row, column=0, sticky="e", pady=3)
        var_bind = tk.StringVar(value=meta.get("bind_app", ""))
        ttk.Entry(frm, textvariable=var_bind, width=30).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Label(frm, text="可空；填写包名后该模板视为绑定\n操作 App（清理后台任务白名单），\n看广告跳转其他 App 时自动清理",
                  foreground="#888", wraplength=170, justify="left").grid(row=row, column=2, sticky="w")
        row += 1

        # 模板图片区（路径过长自动换行，右侧提示独立列完整可见，不超出窗口）
        ttk.Label(frm, text="模板图片").grid(row=row, column=0, sticky="ne", pady=3)
        chosen = {"path": image_path or cur_img}
        lbl_img = ttk.Label(frm, text=chosen["path"], wraplength=250,
                            justify="left", anchor="w", foreground="#06c")
        lbl_img.grid(row=row, column=1, sticky="we", padx=6)

        def on_choose_img():
            p = filedialog.askopenfilename(
                title="选择模板图片（更换）",
                filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")])
            if p:
                chosen["path"] = p
                lbl_img.config(text=p)

        if is_new:
            ttk.Label(frm, text="所选图片将复制进 templates\\分类目录\\", foreground="#888",
                      wraplength=170, justify="left").grid(row=row, column=2, sticky="w", pady=3)
        else:
            ttk.Button(frm, text="更换图片…", command=on_choose_img).grid(row=row, column=2, sticky="w")
        row += 1

        frm_btn = ttk.Frame(frm)
        frm_btn.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(frm_btn, text="保存", width=12,
                   command=lambda: self._save_template_from_editor(
                       win, old_name=name, is_new=is_new, chosen_img=chosen["path"],
                       var_name=var_name, var_pkg=var_pkg, var_kind=var_kind, var_thr=var_thr,
                       var_dir=var_dir, var_dx=var_dx, var_dy=var_dy,
                       var_op=var_op, var_opx=var_opx, var_opy=var_opy, var_note=var_note,
                       var_bind=var_bind, var_region=var_region, var_wait=var_wait)
                   ).pack(side="left", padx=(0, 10))
        ttk.Button(frm_btn, text="取消", width=12, command=win.destroy).pack(side="left", padx=(10, 0))
        row += 1
        lbl_examples = ttk.Label(
            frm,
            text=("模板类型示例：任务按钮＝日常点击的按钮；广告关闭×＝关闭弹窗/开屏广告；"
                  "等待元素出现＝元素出现后点击；等待元素消失＝盯住它，消失瞬间点原位置（可设最长等待秒数）；"
                  "直播间＝命中即按返回键退出；等待出现即返回＝出现后按返回键退出；"
                  "验证码＝命中即暂停设备提醒；滑块缺口＝滑块验证码需滑到的缺口位置；"
                  "滑块按钮＝滑块验证码底部拖柄。"),
            foreground="#888", justify="left", wraplength=480, anchor="w")
        lbl_examples.grid(row=row, column=0, columnspan=3, sticky="w", padx=6, pady=(8, 0))
        on_kind_changed()  # 初始同步类型提示与操作行状态

    def _save_template_from_editor(self, win, old_name, is_new, chosen_img,
                                   var_name, var_pkg, var_kind, var_thr, var_dir,
                                   var_dx, var_dy, var_op, var_opx, var_opy, var_note,
                                   var_bind, var_region, var_wait=None):
        """从编辑窗口保存模板：写图片 + 更新统一 配置.json（缺省字段不写，兼容旧配置）"""
        name = (var_name.get() or "").strip()
        if not name:
            messagebox.showwarning("提示", "模板名称不能为空", parent=win)
            return
        existing = self.coords["templates"]
        if name in existing and not (not is_new and old_name == name):
            messagebox.showwarning("提示", "已有同名模板「%s」，请换一个名称" % name, parent=win)
            return
        safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "tpl"
        # 包名解析：兼容下拉框 “com.xxx（中文名）” 显示格式
        pkg = (var_pkg.get() or "").strip()
        if "（" in pkg and pkg.split("（", 1)[0].strip():
            pkg = pkg.split("（", 1)[0].strip()
        kind_raw = (var_kind.get() or "").strip()
        kind = self._TPL_KIND_REVERSE.get(kind_raw, "task")
        if kind not in ("task", "close", "wait_appear", "wait_disappear", "live",
                "wait_back", "captcha", "slider_gap", "slider_btn", "task_done"):
            kind = "task"
        try:
            threshold = float((var_thr.get() or "").strip() or MATCH_THRESHOLD)
        except Exception:
            threshold = MATCH_THRESHOLD
        threshold = max(0.50, min(0.98, threshold))
        dirname = re.sub(r'[\\/:*?"<>|（）]', "_", (var_dir.get() or "").strip())
        dirname = dirname or app_dir_name("" if kind == "close" else pkg)

        def _pct(v, default=0):
            try:
                return int(float((v.get() or "").strip() or default))
            except Exception:
                return default
        dx, dy = _pct(var_dx), _pct(var_dy)

        img_src = (chosen_img or "").strip()
        old_meta = existing.get(old_name) if (not is_new and old_name) else None
        old_img = self._tpl_path(old_meta) if isinstance(old_meta, dict) else ""
        if not img_src or not os.path.isfile(img_src):
            messagebox.showwarning("提示", "模板图片不存在，请重新选择", parent=win)
            return
        if not is_new and img_src == old_img and not old_img:
            messagebox.showwarning("提示", "找不到原模板图片，请点「更换图片」重新选择", parent=win)
            return
        if not is_new and img_src != old_img and old_img:
            # 更换图片：程序生成的旧图（模板名_时间戳.png）删除，手工放置的保留
            if re.match(r"^.+_\d{10,}\.(png|jpg|jpeg|bmp|webp)$", os.path.basename(old_img), re.I):
                try:
                    os.remove(old_img)
                except Exception:
                    pass
        # 图片落盘：新增=复制；编辑=按需重命名/移动/更换
        sub_dir = os.path.join(self.tpl_dir, dirname)
        try:
            os.makedirs(sub_dir, exist_ok=True)
        except Exception:
            sub_dir = self._tpl_dir_for(pkg)
            dirname = os.path.basename(sub_dir)
        if is_new:
            fname = os.path.join(sub_dir, "%s_%d.png" % (safe, int(time.time() * 1000)))
            try:
                shutil.copyfile(img_src, fname)
            except Exception as e:
                messagebox.showerror("错误", "复制模板图片失败: " + str(e), parent=win)
                return
        elif img_src != old_img:
            # 已更换新图：复制到（新）分类目录
            fname = os.path.join(sub_dir, "%s_%d.png" % (safe, int(time.time() * 1000)))
            try:
                shutil.copyfile(img_src, fname)
            except Exception as e:
                messagebox.showerror("错误", "复制模板图片失败: " + str(e), parent=win)
                return
        else:
            # 未换图：改名/改分类时，程序生成的图片跟随重命名/移动；手工放置的只更新引用
            fname = old_img
            old_base = os.path.basename(old_img)
            m = re.match(r"^(.+)_(\d{10,})\.(png|jpg|jpeg|bmp|webp)$", old_base, re.I)
            old_safe = re.sub(r'[\\/:*?"<>|（）]', "_", old_name).strip()
            if m and m.group(1).strip() == old_safe and (old_name != name or (old_meta or {}).get("dir") != dirname):
                new_base = "%s_%s.png" % (safe, m.group(2))
                fname = os.path.join(sub_dir, new_base)
                if os.path.normcase(fname) != os.path.normcase(old_img):
                    try:
                        if os.path.isfile(fname):
                            os.remove(fname)
                        os.rename(old_img, fname)
                    except Exception as e:
                        messagebox.showerror("错误", "模板图片移动失败: " + str(e), parent=win)
                        return
        meta = {
            "pkg": pkg,  # close 类型也保留原包名：留空=通用，指定=仅该应用（兼容旧配置）
            "file": os.path.relpath(fname, self.data_dir).replace("\\", "/"),
            "dir": dirname,
            "kind": kind,
        }
        # 任务区域：自由文本名称（如「50个红包」），strip 后非空才写入，留空=默认区域不写字段
        region_name = (var_region.get() or "").strip()
        if region_name:
            meta["region_name"] = region_name
        if abs(threshold - MATCH_THRESHOLD) > 1e-9:
            meta["threshold"] = threshold
        if dx or dy:
            meta["click_offset"] = [dx / 100.0, dy / 100.0]
        if kind == "wait_appear":
            op = var_op.get()
            if op == "back":
                meta["op"] = "back"
            elif op == "custom":
                try:
                    ox = max(0.0, min(1.0, int(float(var_opx.get() or 50)) / 100.0))
                    oy = max(0.0, min(1.0, int(float(var_opy.get() or 50)) / 100.0))
                except Exception:
                    ox, oy = 0.5, 0.5
                meta["op"] = [round(ox, 4), round(oy, 4)]
        if kind == "wait_disappear" and var_wait is not None:
            # 最长等待秒数：仅「等待元素消失」用；与默认值一致则不写字段，保持配置干净、旧配置无感
            try:
                wt = int(float((var_wait.get() or "").strip() or WAIT_DISAPPEAR_TIMEOUT))
            except Exception:
                wt = WAIT_DISAPPEAR_TIMEOUT
            wt = max(3, min(120, wt))
            if wt != WAIT_DISAPPEAR_TIMEOUT:
                meta["wait_timeout"] = wt
        note = (var_note.get() or "").strip()
        if note:
            meta["note"] = note
        bind = (var_bind.get() or "").strip()
        if bind:
            meta["bind_app"] = bind
        elif "bind_app" in meta:
            meta.pop("bind_app", None)  # 清空绑定：缺省不写，兼容旧配置
        if not is_new and old_name and old_name in existing and old_name != name:
            existing.pop(old_name, None)
        existing[name] = meta
        self._save_coords()
        self._refresh_tpl_list()
        win.destroy()
        kind_name = self._TPL_KIND_NAMES.get(kind, kind)
        tail = "，点击偏移 %d%%,%d%%" % (dx, dy) if (dx or dy) else ""
        self.log("%s模板「%s」（%s，阈值 %.2f%s）"
                 % ("新增" if is_new else "已编辑", name, kind_name, threshold, tail))

    def capture_template(self):
        if not CV_OK:
            messagebox.showerror("错误", "缺少 OpenCV，无法使用图像识别")
            return
        devices = self._checked_devices() or list(self.device_widgets.keys())
        if not devices:
            messagebox.showwarning("提示", "请先连接并检测设备")
            return
        serial = devices[0]
        self.log("正在截取 " + serial + " 的屏幕（用于图像模板）…")
        code, png, err = run_adb_binary("-s", serial, "exec-out", "screencap", "-p")
        if code != 0 or not png:
            messagebox.showerror("错误", "截屏失败: " + (err or "无数据"))
            return
        png_path = os.path.join(self.data_dir, "screenshot_tmp.png")
        with open(png_path, "wb") as f:
            f.write(png)
        fg = self._get_foreground(serial) or "unknown"
        self._show_capture_window(png_path, fg, mode="region")

    def _imread_any(self, path):
        """cv2.imread 不支持中文路径，用 fromfile+imdecode 代替"""
        try:
            return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None

    def _show_capture_window(self, png_path, pkg, mode="point"):
        win = tk.Toplevel(self.root)
        if mode == "point":
            win.title("采集坐标 — 点击图中按钮位置（应用: %s）" % APP_NAMES.get(pkg, pkg))
        else:
            win.title("采集图像模板 — 拖拽框选按钮区域（应用: %s）" % APP_NAMES.get(pkg, pkg))
        win.grab_set()

        img = self._imread_any(png_path)
        if img is None:
            messagebox.showerror("错误", "截图读取失败", parent=win)
            win.destroy()
            return
        img_h, img_w = img.shape[:2]
        scale = min(300 / img_w, 600 / img_h, 1.0)
        disp = cv2.resize(img, (int(img_w * scale), int(img_h * scale)))
        # BGR -> RGB 转 PhotoImage
        disp_rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        photo = tk.PhotoImage(data=cv2.imencode(".png", disp_rgb)[1].tobytes())
        disp_w, disp_h = photo.width(), photo.height()

        ttk.Label(win, text="应用: %s (%s)\n%s"
                  % (APP_NAMES.get(pkg, pkg), pkg,
                     "请点击目标按钮的位置（签到按钮或宝箱）" if mode == "point"
                     else "请在图上按住鼠标拖拽，框选目标按钮区域")).pack(padx=10, pady=6)
        canvas = tk.Canvas(win, width=disp_w, height=disp_h,
                           highlightthickness=1, highlightbackground="#999")
        canvas.pack(padx=10, pady=6)
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo  # 保持引用

        lbl_tip = ttk.Label(win, text="", foreground="#06c")
        lbl_tip.pack(pady=4)

        if mode == "point":
            var_kind = tk.StringVar(value="sign")
            frm = ttk.Frame(win)
            frm.pack(pady=4)
            ttk.Radiobutton(frm, text="保存为签到坐标", value="sign",
                            variable=var_kind).pack(side="left", padx=8)
            ttk.Radiobutton(frm, text="保存为宝箱坐标", value="box",
                            variable=var_kind).pack(side="left", padx=8)

            def on_click(event):
                px, py = event.x / disp_w, event.y / disp_h
                kind = var_kind.get()
                self.coords[kind][pkg] = [round(px, 4), round(py, 4)]
                self._save_coords()
                lbl_tip.config(text="✅ 已保存 %s 坐标 (%.1f%%, %.1f%%)"
                               % ("签到" if kind == "sign" else "宝箱", px * 100, py * 100))
                self.lbl_coords.config(text=self._coords_summary())
            canvas.bind("<Button-1>", on_click)
        else:
            rect = None
            var_tkind = tk.StringVar(value="task")
            frm_tk = ttk.Frame(win)
            frm_tk.pack(pady=2)
            ttk.Radiobutton(frm_tk, text="任务按钮（出现即点）", value="task",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk, text="广告关闭按钮×（通用）", value="close",
                            variable=var_tkind).pack(side="left", padx=8)
            frm_tk2 = ttk.Frame(win)
            frm_tk2.pack(pady=2)
            ttk.Radiobutton(frm_tk2, text="等待元素出现", value="wait_appear",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk2, text="等待元素消失", value="wait_disappear",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk2, text="直播间（命中即返回键退出）", value="live",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk2, text="等待元素出现（出现即按返回键）", value="wait_back",
                            variable=var_tkind).pack(side="left", padx=8)
            frm_tk3 = ttk.Frame(win)
            frm_tk3.pack(pady=2)
            ttk.Radiobutton(frm_tk3, text="验证码（命中即暂停设备提醒）", value="captcha",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk3, text="滑块缺口（滑块验证码需滑到的位置）", value="slider_gap",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk3, text="滑块按钮（滑块验证码底部拖柄）", value="slider_btn",
                            variable=var_tkind).pack(side="left", padx=8)
            ttk.Radiobutton(frm_tk3, text="任务完成（识别到即停止刷）", value="task_done",
                            variable=var_tkind).pack(side="left", padx=8)
            # 分类目录：默认按应用名，可下拉选已有分类或输入自定义名字
            existing_dirs = sorted({m["dir"] for m in self.coords["templates"].values()
                                    if isinstance(m, dict) and (m.get("dir") or "").strip()})
            var_tdir = tk.StringVar(value=app_dir_name(pkg))

            def on_tkind_change():
                # 广告关闭×默认归「通用」，其余默认按应用名（仍可自由改）
                var_tdir.set(TPL_COMMON_DIR if var_tkind.get() == "close"
                             else app_dir_name(pkg))

            for rb in frm_tk.winfo_children():
                try:
                    rb.configure(command=on_tkind_change)
                except Exception:
                    pass
            frm_dir = ttk.Frame(win)
            frm_dir.pack(pady=2)
            ttk.Label(frm_dir, text="保存到分类目录:").pack(side="left", padx=(0, 4))
            ttk.Combobox(frm_dir, textvariable=var_tdir, width=16,
                         values=existing_dirs or [app_dir_name(pkg), TPL_COMMON_DIR]).pack(side="left")
            # 任务区域：自由文本名称（如「50个红包」），默认留空=默认区域，运行时按配置顺序分区域完成
            frm_reg = ttk.Frame(win)
            frm_reg.pack(pady=2)
            ttk.Label(frm_reg, text="任务区域:").pack(side="left", padx=(0, 4))
            var_tregion = tk.StringVar(value="")
            ttk.Entry(frm_reg, width=20, textvariable=var_tregion).pack(side="left")
            # 第二步操作选择：等待出现模板需要指定"出现后"的动作
            step = [1]  # 1=框选判断区域 2=选择操作位置
            pending = [None]  # 第二步待设置的模板名

            def finish_op(op_val, tip_text):
                meta = self.coords["templates"].get(pending[0])
                if not meta:
                    return
                meta["op"] = op_val
                self._save_coords()
                lbl_tip.config(text=tip_text)
                btn_back_op.config(state="disabled")
                step[0] = 1
                pending[0] = None

            def on_back_op():
                finish_op("back", "✅ 已设置：元素出现后 → 按返回键")

            btn_back_op = ttk.Button(win, text="✋ 出现后按返回键（代替点击）",
                                     state="disabled", command=on_back_op)
            btn_back_op.pack(pady=3)

            def on_press(event):
                nonlocal rect
                if step[0] == 2:
                    # 第二步：点击"出现后要操作的位置"
                    px, py = event.x / disp_w, event.y / disp_h
                    canvas.delete("sel")
                    canvas.create_oval(event.x - 6, event.y - 6, event.x + 6, event.y + 6,
                                       outline="#ff5555", width=2, tags="sel")
                    finish_op([round(px, 4), round(py, 4)],
                              "✅ 已设置：元素出现后 → 点击该位置 (%.1f%%, %.1f%%)"
                              % (px * 100, py * 100))
                    return
                rect = [event.x, event.y, event.x, event.y]

            def on_drag(event):
                nonlocal rect
                if rect:
                    rect[2], rect[3] = event.x, event.y
                    canvas.delete("sel")
                    canvas.create_rectangle(rect[0], rect[1], rect[2], rect[3],
                                            outline="#ff5555", width=2, tags="sel")

            def on_release(event):
                nonlocal rect
                if not rect:
                    return
                x1, x2 = sorted([rect[0], event.x])
                y1, y2 = sorted([rect[1], event.y])
                rect = None
                if x2 - x1 < 8 or y2 - y1 < 8:
                    lbl_tip.config(text="框选太小，请框住完整按钮")
                    return
                # 换算回原图坐标并裁剪保存
                ox1, oy1 = int(x1 / disp_w * img_w), int(y1 / disp_h * img_h)
                ox2, oy2 = int(x2 / disp_w * img_w), int(y2 / disp_h * img_h)
                crop = img[oy1:oy2, ox1:ox2]
                name = simpledialog.askstring("模板名称", "给这个按钮模板起个名字（如: 快手宝箱可开）:",
                                              parent=win)
                if not name:
                    return
                # 模板保存到分类目录（默认按应用名，可自定义）：templates/<分类>/模板名_时间戳.png
                tkind = var_tkind.get()
                # 关闭按钮×样式各应用大同小异，标记为通用（pkg 置空），所有广告场景都拿来匹配
                tpl_pkg = "" if tkind == "close" else pkg
                custom = re.sub(r'[\\/:*?"<>|（）]', "_", var_tdir.get()).strip() or app_dir_name(tpl_pkg)
                sub_dir = os.path.join(self.tpl_dir, custom)
                try:
                    os.makedirs(sub_dir, exist_ok=True)
                except Exception:
                    sub_dir = self._tpl_dir_for(tpl_pkg)
                    custom = os.path.basename(sub_dir)
                safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "tpl"
                fname = os.path.join(sub_dir, "%s_%d.png" % (safe, int(time.time() * 1000)))
                ok, buf = cv2.imencode(".png", crop)
                if not ok:
                    lbl_tip.config(text="模板保存失败")
                    return
                # 同名模板覆盖：先移除旧图片，避免残留孤儿文件
                old = self.coords["templates"].get(name)
                if isinstance(old, dict):
                    op = self._tpl_path(old)
                    if op and os.path.isfile(op):
                        try:
                            os.remove(op)
                        except Exception:
                            pass
                buf.tofile(fname)
                tpl_meta = {
                    "pkg": tpl_pkg,
                    "file": os.path.relpath(fname, self.data_dir).replace("\\", "/"),
                    "dir": custom, "kind": tkind}
                # 任务区域：自由文本名称（如「50个红包」），strip 后非空才写入，留空=默认区域不写字段
                tregion_name = (var_tregion.get() or "").strip()
                if tregion_name:
                    tpl_meta["region_name"] = tregion_name
                self.coords["templates"][name] = tpl_meta
                self._save_coords()
                self._refresh_tpl_list()
                if tkind == "wait_appear":
                    # 进入第二步：选择"出现后"的操作位置或返回键
                    step[0] = 2
                    pending[0] = name
                    btn_back_op.config(state="normal")
                    lbl_tip.config(text="① 判断区域已保存 ✓\n② 请点击「出现后要操作的位置」"
                                         "（点别处按钮/关闭×均可），或点下方「按返回键」")
                else:
                    lbl_tip.config(text="✅ 模板「%s」已保存 (%dx%d 像素, %s)"
                                   % (name, ox2 - ox1, oy2 - oy1,
                                      self._TPL_KIND_NAMES.get(tkind, tkind)))

            canvas.bind("<Button-1>", on_press)
            canvas.bind("<B1-Motion>", on_drag)
            canvas.bind("<ButtonRelease-1>", on_release)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _solve_slider_captcha(self, serial, img):
        """滑块验证码自动处理：命中「滑块缺口」+「滑块按钮」模板后计算水平位移并模拟拖拽。

        思路：缺口模板中心 X 与按钮模板中心 X 的差值即需拖动距离；从按钮位置起，
        用两段 input swipe（先快 70%、后慢 30%）模拟人手拖拽；滑完重截屏复检，
        仍命中则重试（最多 SLIDER_MAX_TRY 次，每次带随机微调），全部失败降级为
        「暂停设备提醒人工」与 captcha 一致。返回 True=已处理本轮（命中或已暂停），
        False=未命中滑块模板，交后续逻辑处理。
        """
        gap_meta = btn_meta = None
        for name, meta in list(self.coords["templates"].items()):
            k = meta.get("kind", "task")
            if k == "slider_gap" and gap_meta is None:
                gap_meta = meta
            elif k == "slider_btn" and btn_meta is None:
                btn_meta = meta
        if gap_meta is None or btn_meta is None:
            return False
        target = self.target_apps.get(serial)

        def _hit(meta):
            """命中返回 (按钮中心X, 中心Y, 模板宽高)；未命中返回 None"""
            if target and meta.get("pkg") and meta["pkg"] != target:
                return None
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                return None
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                return None
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)
            if maxv < threshold:
                return None
            th, tw = tpl.shape[:2]
            return (maxloc[0] + tw // 2, maxloc[1] + th // 2, tw, th), maxv

        g = _hit(gap_meta)
        b = _hit(btn_meta)
        if g is None or b is None:
            return False
        (gcx, gcy, gtw, gth), gv = g
        (bcx, bcy, btw, bth), bv = b
        dx = gcx - bcx
        if abs(dx) < 5:
            # 位移过小视为已就位（可能模板本身框到同一处），不滑动，留给复检
            return True
        dy = gcy - bcy  # 通常同水平线，保留差值便于个别验证码有偏移
        self.log("%s 滑块验证码命中（缺口 %.0f%% / 按钮 %.0f%%），位移 dx=%d dy=%d，开始模拟拖拽"
                 % (serial, gv * 100, bv * 100, dx, dy))
        max_try = getattr(self, "SLIDER_MAX_TRY", 3)
        import random as _rnd
        for attempt in range(1, max_try + 1):
            jitter = _rnd.randint(-3, 3)
            final_dx = dx + jitter
            start_x, start_y = bcx, bcy
            mid_x = start_x + int(final_dx * 0.7)
            end_x = start_x + final_dx
            end_y = bcy + dy
            # 第一段：快拖 70%（短时），第二段：慢拖 30%（长时）贴近缺口
            run_adb("-s", serial, "shell",
                    "input swipe %d %d %d %d %d" % (start_x, start_y, mid_x, bcy, 180),
                    timeout=15)
            time.sleep(0.06)
            run_adb("-s", serial, "shell",
                    "input swipe %d %d %d %d %d" % (mid_x, bcy, end_x, end_y, 420),
                    timeout=15)
            time.sleep(1.6)
            # 复检：缺口/按钮任一仍命中说明未通过
            code, img2, _ = screencap_bgr(serial, timeout=25)
            if code != 0 or img2 is None:
                continue
            still = False
            for meta in (gap_meta, btn_meta):
                tpl = self._imread_any(self._tpl_path(meta))
                if tpl is None or tpl.shape[0] > img2.shape[0] or tpl.shape[1] > img2.shape[1]:
                    continue
                res = cv2.matchTemplate(img2, tpl, cv2.TM_CCOEFF_NORMED)
                _, maxv, _, _ = cv2.minMaxLoc(res)
                if maxv >= meta.get("threshold", MATCH_THRESHOLD):
                    still = True
                    break
            if not still:
                self.log("%s 滑块验证码自动滑动成功（第 %d 次尝试）" % (serial, attempt))
                return True
            self.log_warning("%s 滑块验证码第 %d 次未通过，%s"
                             % (serial, attempt, "继续重试" if attempt < max_try else "降级人工处理"))
            time.sleep(0.8)
        # 全部失败：降级为人工暂停（与 captcha 一致的暂停逻辑）
        pe = self.pause_events.get(serial)
        if pe is not None and not pe.is_set():
            pe.set()
        self.log_warning("%s 滑块验证码自动处理失败，已暂停本设备，请在手机手动拖动验证后点「▶ 继续」" % serial)
        self.set_status("%s 检测到滑块验证码，自动失败，已暂停（请手动处理）" % serial)
        w = self.device_widgets.get(serial)
        if w:
            self.root.after(0, lambda: w["state"].config(text="验证码", foreground="red"))
        self.root.after(0, lambda s=serial: self._speak_event(s, "滑块验证码自动处理失败，请手动处理"))
        try:
            self._update_pause_btn(serial)
        except Exception:
            pass
        if pe is not None:
            while pe.is_set() and not self.stop_event.is_set():
                time.sleep(0.2)
            if w:
                self.root.after(0, lambda: w["state"].config(text="运行中", foreground="green"))
        return True

    def _image_tasks(self, serial):
        """截屏做模板匹配，按钮出现就点击（用于到时间才能点的任务）"""
        if not self.var_img.get() or not CV_OK or not self.coords["templates"]:
            return
        interval = max(5, self.var_img_interval.get())
        now = time.time()
        if now - self.last_img_check.get(serial, 0) < interval:
            return
        self.last_img_check[serial] = now

        code, img, _ = screencap_bgr(serial, timeout=25)
        if code != 0 or img is None:
            return
        target = self.target_apps.get(serial)
        # 滑块验证码自动处理优先：命中缺口+按钮模板即模拟拖拽，多次失败再降级人工暂停
        if self._solve_slider_captcha(serial, img):
            return
        # task_done 任务完成模板优先级最高：命中即停止该设备刷机并语音/日志提醒，不点击、不等待（先于其它类型处理）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "task_done":
                continue
            if target and meta.get("pkg") and meta["pkg"] != target:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            if now - self.img_cooldown.get((serial, name), 0) < 60:
                continue  # 命中冷却 60 秒
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)  # 每模板可单独调阈值（缺省用全局 0.80）
            if maxv < threshold:
                continue
            self.img_cooldown[(serial, name)] = now
            self.task_done_flags[serial] = True  # 通知主循环停止该设备线程（其它设备不受影响）
            self.log("%s ✅ 检测到任务完成画面「%s」（置信度 %.0f%%），已停止该设备刷机"
                     % (serial, name, maxv * 100))
            self.set_status("%s 任务完成，已停止该设备" % serial)
            w = self.device_widgets.get(serial)
            if w:
                self.root.after(0, lambda: w["state"].config(text="任务完成", foreground="green"))
            # 语音提醒（var_voice_control 由 _speak_event 内部判断；缺 pywin32 自动跳过；经 root.after 回主线程安全调用）
            self.root.after(0, lambda s=serial: self._speak_event(s, "任务完成"))
            return  # 每轮只处理一个命中（task_done 优先）
        # captcha 验证码模板优先级最高：命中即暂停该设备并语音/日志提醒，不点击、不等待（先于其它类型处理）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "captcha":
                continue
            if target and meta.get("pkg") and meta["pkg"] != target:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            if now - self.img_cooldown.get((serial, name), 0) < 60:
                continue  # 命中冷却 60 秒
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)  # 每模板可单独调阈值（缺省用全局 0.80）
            if maxv < threshold:
                continue
            self.img_cooldown[(serial, name)] = now
            pe = self.pause_events.get(serial)
            if pe is not None and not pe.is_set():
                pe.set()  # 暂停本设备：阻塞刷视频线程等待用户手动处理
            self.log_warning("%s 检测到验证码「%s」（置信度 %.0f%%），已暂停本设备，请在手机完成验证后点「▶ 继续」"
                             % (serial, name, maxv * 100))
            self.set_status("%s 检测到验证码，已暂停（请手动处理）" % serial)
            w = self.device_widgets.get(serial)
            if w:
                self.root.after(0, lambda: w["state"].config(text="验证码", foreground="red"))
            # 语音提醒（var_voice_control 由 _speak_event 内部判断；经 root.after 回主线程安全调用）
            self.root.after(0, lambda s=serial: self._speak_event(s, "检测到验证码，请手动处理"))
            try:
                self._update_pause_btn(serial)  # 同步暂停按钮为「▶ 继续」
            except Exception:
                pass
            # 阻塞等待用户手动处理验证码后点「继续」或全局停止（与 _swipe_loop 暂停逻辑一致）
            if pe is not None:
                while pe.is_set() and not self.stop_event.is_set():
                    time.sleep(0.2)
                if w:
                    self.root.after(0, lambda: w["state"].config(text="运行中", foreground="green"))
            return  # 每轮只处理一个命中（captcha 优先）
        # live 直播间模板优先级最高：命中即按返回键退出，不点击、不等待消失（先于其它类型处理）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "live":
                continue
            if target and meta.get("pkg") and meta["pkg"] != target:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            if now - self.img_cooldown.get((serial, name), 0) < 60:
                continue  # 命中冷却 60 秒
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)  # 每模板可单独调阈值（缺省用全局 0.80）
            if maxv < threshold:
                continue
            run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
            self.log("%s 检测到直播间画面「%s」（置信度 %.0f%%），已按返回键退出"
                     % (serial, name, maxv * 100))
            self.img_cooldown[(serial, name)] = now
            return  # 每轮只处理一个命中（live 优先）
        # wait_back 等待元素出现后按返回键：命中即按一次返回键（区别于 live 的持续退出，用于「等某元素出现后返回」场景）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "wait_back":
                continue
            if target and meta.get("pkg") and meta["pkg"] != target:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            if now - self.img_cooldown.get((serial, name), 0) < 60:
                continue  # 命中冷却 60 秒
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)  # 每模板可单独调阈值（缺省用全局 0.80）
            if maxv < threshold:
                continue
            run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
            self.log("%s 等待的「%s」已出现（置信度 %.0f%%），已按一次返回键"
                     % (serial, name, maxv * 100))
            self.img_cooldown[(serial, name)] = now
            return  # 每轮只处理一个命中（wait_back 优先于其它常规模板）
        # 常规循环：收集本帧全部命中的常规任务模板（task / wait_appear / wait_disappear），
        # 按「配置文件中模板首次出现的区域顺序」分组（区域序号小的先完成），
        # 同区域内再按屏幕纵坐标 y 从上到下排序后依次完成，先做完当前区域全部命中任务，再做下一个区域
        # 区域名 = meta 的 region_name（自由文本，如「50个红包」「20看广告」）；旧数字配置兼容为「区域N」
        region_order = {}
        for _nm, _mt in list(self.coords["templates"].items()):
            if _mt.get("kind", "task") == "live":
                continue  # 直播间由上方 live 前置循环处理，不参与常规分组
            _rn = _region_name_of(_mt)
            if _rn not in region_order:
                region_order[_rn] = len(region_order)
        pending = []  # (region_name, y, kind, name, action, maxv, where)  region_name=任务区域名（分组首要键） y=屏幕纵坐标
        wait_items = []  # 本帧仍在画面、需要转入高频盯防等它消失的「等待元素消失」模板 [(name, meta)]
        for name, meta in list(self.coords["templates"].items()):
            kind = meta.get("kind", "task")
            region_name = _region_name_of(meta)  # 任务区域名（自由文本，旧数字配置显示为「区域N」）
            if kind == "live":
                continue  # 直播间模板已由上方 live 前置循环处理
            if target and meta.get("pkg") and meta["pkg"] != target:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            # 命中冷却：普通模板 60 秒防重复点击；「等待元素消失」只压 3 秒——
            # 倒计时/弹层这类元素消失后往往很快又重新出现，压 60 秒会导致整轮都不再盯防
            _cool = WAIT_DISAPPEAR_COOLDOWN if kind == "wait_disappear" else 60
            if now - self.img_cooldown.get((serial, name), 0) < _cool:
                continue
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)  # 每模板可单独调阈值（缺省用全局 0.80）
            found = maxv >= threshold
            th, tw = tpl.shape[:2]
            off = meta.get("click_offset") or []
            offx = float(off[0]) if isinstance(off, (list, tuple)) and len(off) >= 2 else 0.0
            offy = float(off[1]) if isinstance(off, (list, tuple)) and len(off) >= 2 else 0.0
            cx, cy = (maxloc[0] + tw // 2 + int(tw * offx),
                      maxloc[1] + th // 2 + int(th * offy))

            if kind == "wait_disappear":
                # 等待元素消失：还在 → 记下最后位置并转入「高频盯防」；已不在 → 直接点它刚才的位置
                if found:
                    self.disappear_pos[(serial, name)] = (cx, cy, now)
                    wait_items.append((name, meta))
                    if now - self.last_wait_log.get((serial, name), 0) > 30:
                        self.last_wait_log[(serial, name)] = now
                        self.log("%s ⏳ 「%s」仍在画面（置信度 %.0f%%），进入盯防等它消失…"
                                 % (serial, name, maxv * 100))
                else:
                    pos = self.disappear_pos.pop((serial, name), None)
                    # 位置时效校验：太久以前看到的元素，位置可能已经变了，不用它
                    if pos and now - (pos[2] if len(pos) > 2 else 0) <= self._wait_timeout_of(meta) + 10:
                        pending.append((region_name, pos[1], "wait_disappear", name,
                                        (pos[0], pos[1]), maxv, None))
                continue

            # task / wait_appear：出现就触发（进入待执行列表）
            if found:
                if kind == "wait_appear":
                    op = meta.get("op")
                    if op == "back":
                        pending.append((region_name, cy, "wait_appear", name, None, maxv, "back"))
                        continue
                    if isinstance(op, (list, tuple)) and len(op) == 2:
                        w, h = self.screen_sizes.get(serial, (1080, 2400))
                        x, y = int(w * op[0]), int(h * op[1])
                        where = "操作位置 (%d,%d)" % (x, y)
                    else:
                        x, y = cx, cy  # 未设置操作位置则点元素中心
                        where = "元素中心 (%d,%d)" % (x, y)
                    pending.append((region_name, y, "wait_appear", name, (x, y), maxv, where))
                else:
                    pending.append((region_name, cy, "task", name, (cx, cy), maxv, None))

        # 按「配置文件中区域首次出现顺序」升序、同区域内按屏幕纵坐标 y 从上到下排序，依次完成全部命中任务
        pending.sort(key=lambda it: (region_order.get(it[0], 10 ** 9), it[1]))
        for _reg, _y, kind, name, act, maxv, where in pending:
            self.img_cooldown[(serial, name)] = now
            if kind == "wait_disappear":
                code, _, _ = run_adb("-s", serial, "shell",
                                     "input tap %d %d" % act, timeout=15)
                self.log("%s 🖼 「%s」已消失，已点击原位置 %s"
                         % (serial, name, act))
            elif kind == "wait_appear" and act is None:
                # op == "back"：出现即按返回键
                run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
                self.log("%s 🖼 等待的「%s」已出现（%.0f%%），已按返回键"
                         % (serial, name, maxv * 100))
            elif kind == "wait_appear":
                code, _, _ = run_adb("-s", serial, "shell",
                                     "input tap %d %d" % act, timeout=15)
                self.log("%s 🖼 等待的「%s」已出现（置信度 %.0f%%），已点击%s"
                         % (serial, name, maxv * 100, where))
            else:  # task
                code, _, _ = run_adb("-s", serial, "shell",
                                     "input tap %d %d" % act, timeout=15)
                self.log("%s 🖼 匹配到「%s」置信度 %.0f%%，已点击 (%d,%d)"
                         % (serial, name, maxv * 100, act[0], act[1]))

        # 「等待元素消失」：命中后立刻转入高频盯防，抓消失那一帧再点它刚才的位置。
        # 放在常规任务点击之后，不抢普通任务的点击时机。
        for _wname, _wmeta in wait_items:
            if self.stop_event.is_set():
                break
            self._await_disappear(serial, _wname, _wmeta)

    def _wait_timeout_of(self, meta):
        """模板的「最长等待秒数」：优先取模板自定义 wait_timeout，缺省用全局默认，夹在 3~120 秒"""
        try:
            v = float(meta.get("wait_timeout", WAIT_DISAPPEAR_TIMEOUT))
        except Exception:
            v = WAIT_DISAPPEAR_TIMEOUT
        return max(3.0, min(120.0, v))

    def _await_disappear(self, serial, name, meta):
        """等待元素消失（高频盯防版）。

        旧做法只在主巡检节拍（5~15 秒一次）里看它还在不在，倒计时/红包雨这类元素
        往往几秒内就没了，慢一拍就永远等不到「消失」那帧，看起来就是功能不好使。
        这里改成：命中后按 WAIT_DISAPPEAR_POLL 间隔连续截屏盯防，元素消失的瞬间
        立即点击它最后出现的位置；超过 wait_timeout 秒还在就放弃，等下次命中重来。
        """
        tpl = self._imread_any(self._tpl_path(meta))
        if tpl is None:
            self.log("%s 「%s」模板图片读不到，等待元素消失跳过" % (serial, name))
            return False
        timeout = self._wait_timeout_of(meta)
        threshold = meta.get("threshold", MATCH_THRESHOLD)
        off = meta.get("click_offset") or []
        offx = float(off[0]) if isinstance(off, (list, tuple)) and len(off) >= 2 else 0.0
        offy = float(off[1]) if isinstance(off, (list, tuple)) and len(off) >= 2 else 0.0
        t0 = time.time()
        last_log = t0
        pos = self.disappear_pos.get((serial, name))
        _frame_cost = 0.0
        _frames = 0
        while time.time() - t0 < timeout:
            if self.stop_event.is_set():
                return False
            _pause = self.pause_events.get(serial)
            if _pause is not None and _pause.is_set():
                return False  # 暂停中：放弃本次盯防，恢复后下一轮重新识别
            # 截图本身就要 1~3 秒，够慢的就不额外等待；只有设备快到飞起时才补间隔，别把设备打满
            if WAIT_DISAPPEAR_POLL > _frame_cost:
                time.sleep(WAIT_DISAPPEAR_POLL - _frame_cost)
            _t_frame = time.time()
            code, img, _ = screencap_bgr(serial, timeout=25)
            _frame_cost = time.time() - _t_frame
            if code != 0 or img is None:
                continue
            _frames += 1
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                return False
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            th, tw = tpl.shape[:2]
            if maxv >= threshold:  # 还在：刷新它当前的位置，继续盯
                cx = maxloc[0] + tw // 2 + int(tw * offx)
                cy = maxloc[1] + th // 2 + int(th * offy)
                pos = (cx, cy, time.time())
                self.disappear_pos[(serial, name)] = pos
                if time.time() - last_log > 10:
                    last_log = time.time()
                    self.log("%s ⏳ 等待「%s」消失中（已盯 %.0f 秒 / 最多 %.0f 秒，第 %d 帧，每帧约 %.1f 秒）…"
                             % (serial, name, time.time() - t0, timeout, _frames, _frame_cost))
                continue
            # 检测不到 = 已消失，点它最后出现的位置
            if not pos:
                self.log("%s 「%s」已消失，但没有记录到原位置，跳过点击" % (serial, name))
                return False
            x, y = int(pos[0]), int(pos[1])
            run_adb("-s", serial, "shell", "input tap %d %d" % (x, y), timeout=15)
            self.disappear_pos.pop((serial, name), None)
            self.img_cooldown[(serial, name)] = time.time()
            self.log("%s 🖼 「%s」已消失（盯了 %.1f 秒 / %d 帧，每帧约 %.1f 秒），已点击原位置 (%d,%d)"
                     % (serial, name, time.time() - t0, _frames, _frame_cost, x, y))
            return True
        self.disappear_pos.pop((serial, name), None)
        # 超时后压 30 秒再试：避免元素长期常驻画面时每轮都白盯一段，拖垮刷视频节奏
        self.img_cooldown[(serial, name)] = time.time() + 27
        self.log("%s ⏰ 等待「%s」消失超时（%.0f 秒内仍在画面），本次放弃，30 秒后重新识别"
                 % (serial, name, timeout))
        return False

    def _on_ui_correct_toggle(self):
        """界面异常自动纠正开关变化：仅保存状态（纠正逻辑在各设备线程内按开关判断）"""
        try:
            self._save_ui_state()
        except Exception:
            pass
        self.log("界面异常自动纠正: %s" % ("开启" if self.var_ui_correct.get() else "关闭"))

    def _on_clean_other_toggle(self):
        """清理后台任务开关变化：仅保存状态（清理逻辑在界面纠正 other_app 分支按开关判断）"""
        try:
            self._save_ui_state()
        except Exception:
            pass
        self.log("清理后台任务: %s（前台非绑定操作 App 且非刷视频目标时自动清理）"
                 % ("开启" if self.var_clean_other.get() else "关闭"))

    def _on_bind_app_changed(self, *args):
        """绑定操作 App 输入变化即保存到配置 ui 键 var_bind_app"""
        try:
            self._save_ui_state()
        except Exception:
            pass

    def _on_bind_app_committed(self, *args):
        """绑定操作 App 输入框失焦时静默保存"""
        try:
            self._save_ui_state()
        except Exception:
            pass

    def _classify_ui_state(self, serial, img, fg):
        """画面级分类：判断当前截图是否为正常刷视频界面。

        复用现有 templates 配置（kind: live/close/wait_disappear/wait_appear + op/click_offset/wait_back）：
          - task_done 模板命中（优先级最高） → "task_done"（任务完成，需停止该设备刷机）
          - captcha 模板命中（优先级最高） → "captcha"（验证码，需暂停设备提醒用户手动处理）
          - live 模板命中（优先级最高） → "live"（直播间画面，需按返回键退出）
          - close 模板命中           → "ad"（广告/弹窗，返回关闭按钮点击位置）
          - wait_disappear 仍出现    → "popup"（本应消失的弹窗元素还在）
          - wait_appear + op=back 出现 → "popup"（出现即应返回的弹窗）
          - wait_back 命中            → "popup"（出现即应返回）
          - task / wait_appear(非 back) 命中 → 正常任务元素，不判异常
          - 模板均未命中             → "normal"（正常刷视频画面）
        """
        # task_done 任务完成模板优先级最高：先单独检查，命中即判定为刷机完成（不点击、不返回）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "task_done":
                continue
            if fg and meta.get("pkg") and meta["pkg"] != fg:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)
            if maxv < threshold:
                continue
            return "task_done", "检测到任务完成画面「%s」（%.0f%%）" % (name, maxv * 100), None
        # slider 滑块验证码优先：命中缺口/按钮模板即判定为滑块验证码画面（自动拖拽处理，见 _correct_ui_state）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") not in ("slider_gap", "slider_btn"):
                continue
            if fg and meta.get("pkg") and meta["pkg"] != fg:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)
            if maxv < threshold:
                continue
            return "slider", "检测到滑块验证码「%s」（%.0f%%）" % (name, maxv * 100), None
        # captcha 验证码模板优先级最高：先单独检查，命中即判定为需暂停设备（不点击、不返回，避免误关）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "captcha":
                continue
            if fg and meta.get("pkg") and meta["pkg"] != fg:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)
            if maxv < threshold:
                continue
            return "captcha", "检测到验证码「%s」（%.0f%%）" % (name, maxv * 100), None
        # live 直播间模板优先级最高：先单独检查，命中即判定为需退出画面（只退出、不点击、不等待）
        for name, meta in list(self.coords["templates"].items()):
            if meta.get("kind", "task") != "live":
                continue
            if fg and meta.get("pkg") and meta["pkg"] != fg:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, _ = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)
            if maxv < threshold:
                continue
            return "live", "检测到直播间画面「%s」（%.0f%%）" % (name, maxv * 100), None
        close_pos = None
        for name, meta in list(self.coords["templates"].items()):
            kind = meta.get("kind", "task")
            if fg and meta.get("pkg") and meta["pkg"] != fg:
                continue  # 模板属于其他应用（通用模板 pkg 为空，任何应用都匹配）
            tpl = self._imread_any(self._tpl_path(meta))
            if tpl is None:
                continue
            if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            threshold = meta.get("threshold", MATCH_THRESHOLD)
            if maxv < threshold:
                continue
            th, tw = tpl.shape[:2]
            off = meta.get("click_offset") or []
            offx = float(off[0]) if isinstance(off, (list, tuple)) and len(off) >= 2 else 0.0
            offy = float(off[1]) if isinstance(off, (list, tuple)) and len(off) >= 2 else 0.0
            cx, cy = (maxloc[0] + tw // 2 + int(tw * offx),
                      maxloc[1] + th // 2 + int(th * offy))
            if kind == "close":
                close_pos = (cx, cy)
                return "ad", "匹配到广告关闭按钮「%s」（%.0f%%）" % (name, maxv * 100), close_pos
            if kind == "wait_disappear":
                return "popup", "弹窗元素「%s」仍出现在画面（%.0f%%）" % (name, maxv * 100), None
            if kind == "wait_appear" and meta.get("op") == "back":
                return "popup", "等待元素「%s」出现且应返回（%.0f%%）" % (name, maxv * 100), None
            if kind == "wait_back":
                return "popup", "等待元素「%s」出现即应返回（%.0f%%）" % (name, maxv * 100), None
        return "normal", "", None

    def _get_bind_apps(self, serial):
        """返回绑定操作 App 包名集合（清理其他 App 时的白名单）：
        自动绑定：本设备刷机目标应用 self.target_apps[serial]（开刷时锁定，无需手填即生效）；
        补充白名单：设置页 var_bind_app（全局） + 全部模板 bind_app（模板级，缺省不写键）。
        返回空集合 = 未绑定任何 App：此时清理功能只清非桌面前台的其他 App。
        """
        binds = set()
        try:
            t = self.target_apps.get(serial)
            if t:
                binds.add(t)
        except Exception:
            pass
        try:
            v = (self.var_bind_app.get() or "").strip()
            if v:
                binds.add(v)
        except Exception:
            pass
        try:
            for meta in (self.coords.get("templates") or {}).values():
                if isinstance(meta, dict):
                    b = (meta.get("bind_app") or "").strip()
                    if b:
                        binds.add(b)
        except Exception:
            pass
        return binds

    def _clean_other_app(self, serial, fg_pkg):
        """清理跳转到的其他 App：先按返回键退出；仍在前台则 am force-stop 强杀。
        由界面纠正 other_app 分支调用（仅当开启了清理后台任务），完成后由上层拉起目标应用。
        """
        self.log("%s 清理其他应用 %s：按返回键退出…" % (serial, fg_pkg))
        run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
        time.sleep(0.8)
        cur = self._get_foreground(serial)
        if cur is not None and cur == fg_pkg and cur not in LAUNCHER_PKGS:
            self.log("%s 应用 %s 未随返回键退出，执行 am force-stop 强制清理" % (serial, fg_pkg))
            run_adb("-s", serial, "shell", "am force-stop %s" % fg_pkg, timeout=15)
            time.sleep(0.5)

    def _correct_ui_state(self, serial, state, close_pos):
        """按界面状态分层纠正（adb 均带 -s serial，线程安全，只影响本设备）。

        分层策略：
          captcha 验证码        → 暂停本设备，提醒用户手动处理（不点击/不返回，避免误关）
          live 直播间        → 按返回键退出直播间（只退出、不点击、不等待消失）
          ad 广告/弹窗      → 优先点击关闭按钮位置，失败/无位置则按返回键
          popup/error/unknown → 按返回键重试
          home 回到桌面     → monkey 重新拉起目标应用
          other_app 其他应用 → 开启「清理后台任务」时先清理非绑定 App（返回键/force-stop），再拉起目标应用（广告落地页/误触）
        纠正后等待 2 秒并验证是否恢复，返回 True=已恢复 / False=仍异常。
        """
        target = self.target_apps.get(serial)
        if state == "slider":
            # 滑块验证码：优先自动拖拽处理（失败内部降级为人工暂停等待）
            code, img, _ = screencap_bgr(serial, timeout=25)
            if code == 0 and img is not None:
                if self._solve_slider_captcha(serial, img):
                    return True
            # 截屏失败或未命中滑块模板：按人工验证码暂停兜底
            pe = self.pause_events.get(serial)
            if pe is not None and not pe.is_set():
                pe.set()
            self.log_warning("%s 检测到滑块验证码，已暂停本设备，请在手机完成验证后点「▶ 继续」" % serial)
            self.set_status("%s 检测到滑块验证码，已暂停（请手动处理）" % serial)
            w = self.device_widgets.get(serial)
            if w:
                self.root.after(0, lambda: w["state"].config(text="验证码", foreground="red"))
            self.root.after(0, lambda s=serial: self._speak_event(s, "检测到滑块验证码，请手动处理"))
            try:
                self._update_pause_btn(serial)
            except Exception:
                pass
            if pe is not None:
                while pe.is_set() and not self.stop_event.is_set():
                    time.sleep(0.2)
                if w:
                    self.root.after(0, lambda: w["state"].config(text="运行中", foreground="green"))
            return True
        if state == "captcha":
            # 验证码需要人工处理：暂停本设备（阻塞刷线程），语音/日志提醒后等待用户手动完成
            pe = self.pause_events.get(serial)
            if pe is not None and not pe.is_set():
                pe.set()
            self.log_warning("%s 检测到验证码，已暂停本设备，请在手机完成验证后点「▶ 继续」" % serial)
            self.set_status("%s 检测到验证码，已暂停（请手动处理）" % serial)
            w = self.device_widgets.get(serial)
            if w:
                self.root.after(0, lambda: w["state"].config(text="验证码", foreground="red"))
            self.root.after(0, lambda s=serial: self._speak_event(s, "检测到验证码，请手动处理"))
            try:
                self._update_pause_btn(serial)  # 同步暂停按钮为「▶ 继续」
            except Exception:
                pass
            if pe is not None:
                while pe.is_set() and not self.stop_event.is_set():
                    time.sleep(0.2)
                if w:
                    self.root.after(0, lambda: w["state"].config(text="运行中", foreground="green"))
            return True
        if state == "live":
            run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
            self.log("%s 检测到直播间画面，已按返回键退出" % serial)
        elif state == "ad":
            if close_pos:
                code, _, _ = run_adb("-s", serial, "shell",
                                     "input tap %d %d" % close_pos, timeout=15)
                if code == 0:
                    self.log("%s 界面纠正[广告弹窗] 已点击关闭按钮 %s" % (serial, close_pos))
                else:
                    run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
                    self.log("%s 界面纠正[广告弹窗] 关闭按钮点击失败，已改按返回键" % serial)
            else:
                run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
                self.log("%s 界面纠正[广告弹窗] 未匹配到关闭按钮，已按返回键" % serial)
        elif state in ("popup", "error", "unknown"):
            run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
            self.log("%s 界面纠正[%s] 已按返回键重试" % (serial, state))
        elif state in ("home", "other_app"):
            if state == "other_app" and self.var_clean_other.get():
                # 清理后台任务：前台为非绑定操作 App（且非桌面）时先清理（返回键/force-stop），再拉起目标
                cur = self._get_foreground(serial)
                binds = self._get_bind_apps(serial)
                if cur and cur not in binds and cur not in LAUNCHER_PKGS:
                    self._clean_other_app(serial, cur)
            if target:
                self.log("%s 界面纠正[%s] 正在重新拉起目标应用 %s …" % (serial, state, target))
                self._launch_app(serial, target)
            else:
                run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)
                self.log("%s 界面纠正[%s] 无目标应用，已按返回键" % (serial, state))
        time.sleep(2)
        return self._verify_ui_recovered(serial)

    def _verify_ui_recovered(self, serial):
        """纠正后验证：前台已回到目标应用，且（有模板时）画面不再命中异常模板"""
        target = self.target_apps.get(serial)
        fg = self._get_foreground(serial)
        if fg is None:
            return False
        if target and fg != target:
            return False
        if CV_OK and self.coords["templates"]:
            code, img, _ = screencap_bgr(serial, timeout=25)
            if code != 0 or img is None:
                return False
            state, _detail, _cp = self._classify_ui_state(serial, img, fg)
            if state != "normal":
                return False
        return True

    def _ui_correct_give_up(self, serial):
        """连续纠正失败：停止该设备线程（_swipe_loop 返回）并语音提醒「界面异常无法恢复」"""
        self.log_error("%s 界面异常连续 %d 次纠正失败，已停止该设备刷视频（请手动检查手机画面）"
                       % (serial, UI_CORRECT_MAX_FAIL))
        self.set_status("%s 界面异常无法恢复，已停止" % serial)
        w = self.device_widgets.get(serial)
        if w:
            self.root.after(0, lambda: w["state"].config(text="界面异常", foreground="red"))
        # 语音控制开关（var_voice_control，含语音提醒播报）由 _speak_event 内部判断；经 root.after 回主线程安全调用
        self.root.after(0, lambda: self._speak_event(serial, "界面异常无法恢复"))

    def _check_and_correct_ui(self, serial):
        """刷视频界面异常自动纠正主入口（在 workers[serial] 线程内调用）。

        定期（UI_CHECK_INTERVAL 秒节流）判断当前画面是否正常刷视频界面：
          - 前台已离开目标应用 → home/other_app，显式拉起目标应用
          - 前台在目标应用内 → 截屏模板匹配分类（ad/popup/unknown）
          - 前台在目标应用内且画面连续停滞（签名不变）→ 判定卡死，强杀重启目标应用
        非正常画面执行分层纠正；连续纠正失败先强杀重启目标应用，超过
        UI_FORCE_RESTART_LIMIT 次仍失败才停止本设备线程并语音提醒。
        返回 True=可继续刷 / False=本设备线程应退出。
        """
        if not self.var_ui_correct.get():
            return True
        now = time.time()
        if now - self.last_ui_check.get(serial, 0) < UI_CHECK_INTERVAL:
            return True
        self.last_ui_check[serial] = now

        target = self.target_apps.get(serial)
        if not target:
            return True
        fg = self._get_foreground(serial)
        if fg is None:
            return True  # adb 抖动/无法获取前台：本轮跳过，不误纠正

        close_pos = None
        if fg != target:
            state = "home" if fg in LAUNCHER_PKGS else "other_app"
            self.log_warning("%s 界面异常[%s] 当前前台 %s（应为 %s）"
                             % (serial, state, fg, target))
        else:
            if not CV_OK or not self.coords["templates"]:
                return True  # 无图像能力/无模板时仅靠前台判断（已有 _wait_on_target 处理）
            code, img, _ = screencap_bgr(serial, timeout=25)
            if code != 0 or img is None:
                return True
            state, detail, close_pos = self._classify_ui_state(serial, img, fg)
            self.ui_last_state[serial] = state
            if state == "task_done":
                # 任务完成：停止本设备线程（不当作界面异常纠正；其它设备继续刷）
                self.task_done_flags[serial] = True
                self.log("%s ✅ 检测到任务完成画面，已停止该设备刷机" % serial)
                self.set_status("%s 任务完成，已停止该设备" % serial)
                w = self.device_widgets.get(serial)
                if w:
                    self.root.after(0, lambda: w["state"].config(text="任务完成", foreground="green"))
                # 语音提醒（var_voice_control 由 _speak_event 内部判断；缺 pywin32 自动跳过；经 root.after 回主线程安全调用）
                self.root.after(0, lambda s=serial: self._speak_event(s, "任务完成"))
                return False
            if state == "normal":
                self.ui_correct_fail[serial] = 0  # 恢复正常：清空连续失败计数
                # 画面停滞检测：前台在目标应用内但画面连续不变（崩溃白屏/加载页/卡死）
                small = cv2.resize(img, (64, 64))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                sig = hashlib.md5(gray.tobytes()).hexdigest()
                last_sig, same = self.ui_freeze.get(serial, (None, 0))
                if sig == last_sig:
                    same += 1
                    if same >= UI_FREEZE_MAX_FAIL:
                        self.log_warning("%s 画面连续 %d 次检查无变化（疑似卡死/崩溃残留），强制重启 %s…"
                                         % (serial, UI_FREEZE_MAX_FAIL, target))
                        self._force_restart_target(serial)
                        self.ui_freeze[serial] = (None, 0)
                        self.ui_correct_fail[serial] = 0
                else:
                    same = 0
                self.ui_freeze[serial] = (sig, same)
                return True
            self.log_warning("%s 界面异常[%s] %s" % (serial, state, detail))
        # 执行分层纠正
        ok = self._correct_ui_state(serial, state, close_pos)
        if ok:
            self.ui_correct_fail[serial] = 0
            self.ui_force_restart[serial] = 0  # 恢复成功：清空强杀重启计数
            self.log("%s 界面已恢复正常，继续刷" % serial)
            return True
        self.ui_correct_fail[serial] = self.ui_correct_fail.get(serial, 0) + 1
        self.log_warning("%s 界面纠正第 %d 次未恢复（%s），下轮重试…"
                         % (serial, self.ui_correct_fail[serial], state))
        if self.ui_correct_fail[serial] >= UI_CORRECT_MAX_FAIL:
            # 连续纠正失败：先强杀重启目标应用给一次重生机会，超限才停线程
            restart = self.ui_force_restart.get(serial, 0)
            if restart < UI_FORCE_RESTART_LIMIT:
                self.ui_force_restart[serial] = restart + 1
                self.ui_correct_fail[serial] = 0
                self.log_warning("%s 连续纠正失败，第 %d/%d 次强杀重启 %s 后重试…"
                                 % (serial, restart + 1, UI_FORCE_RESTART_LIMIT, target))
                self._force_restart_target(serial)
                return True
            self._ui_correct_give_up(serial)
            return False
        return True

    def run_sign_in(self):
        devices = self._checked_devices()
        if not devices:
            messagebox.showwarning("提示", "请先勾选设备")
            return
        if not self.coords["sign"]:
            messagebox.showwarning("提示",
                                   "还没有签到坐标。\n\n步骤：手机打开快手/抖音的签到页面"
                                   "（让签到按钮显示在屏幕上）→ 点「采集坐标」→ 点图中签到按钮位置")
            return
        for serial in devices:
            threading.Thread(target=self._sign_in_worker, args=(serial,), daemon=True).start()

    def _sign_in_worker(self, serial):
        self.log("%s 开始自动签到（共 %d 个应用）…" % (serial, len(self.coords["sign"])))
        run_adb("-s", serial, "shell", "input keyevent 224")  # 唤醒
        time.sleep(1)
        run_adb("-s", serial, "shell", "input keyevent 82")   # 上滑解锁（无密码时有效）
        time.sleep(1)
        for pkg, (px, py) in list(self.coords["sign"].items()):
            if self.stop_event.is_set():
                return
            name = APP_NAMES.get(pkg, pkg)
            code, _, err = run_adb("-s", serial, "shell",
                                   "monkey -p %s -c android.intent.category.LAUNCHER 1" % pkg,
                                   timeout=20)
            if code != 0:
                self.log("%s ❌ 无法启动 %s: %s" % (serial, name, err))
                continue
            time.sleep(6)
            w, h = self.screen_sizes.get(serial) or self._read_screen_size(serial)
            x, y = int(w * px), int(h * py)
            code, _, err = run_adb("-s", serial, "shell",
                                   "input tap %d %d" % (x, y), timeout=15)
            if code == 0:
                self.log("%s ✅ %s 已点击签到按钮 (%d,%d)" % (serial, name, x, y))
            else:
                self.log("%s ❌ %s 点击失败: %s" % (serial, name, err))
            time.sleep(3)
        self.log("%s 自动签到流程结束（请在手机上确认签到成功）" % serial)

    def start(self):
        # 整体开始：首次全量启动；若已有单台在运行，仅补启尚未运行的勾选设备（不重复启动）
        targets = self._checked_devices()
        if not targets:
            self.detect_devices()
            targets = self._checked_devices()
            if not targets:
                messagebox.showwarning("提示", "没有可用的手机，请先连接设备")
                return

        alive = {s for s, t in self.workers.items() if t.is_alive()}
        already = [s for s in targets if s in alive]
        to_start = [s for s in targets if s not in alive]
        if not to_start:
            self.log("开始刷：勾选的设备均已在运行中，无需重复启动")
            return

        tmin, tmax = self.var_min.get(), self.var_max.get()
        swipe_on = self.var_swipe.get()
        if swipe_on:
            if tmin > tmax:
                messagebox.showwarning("提示", "最小间隔不能大于最大间隔")
                return
            if tmin < 1:
                messagebox.showwarning("提示", "间隔太短可能损伤手机，最低 1 秒")
                return

        # 点开始刷时保存当前勾选项/参数，下次启动直接恢复
        self._save_ui_state()

        if not self.running:
            self.running = True
            self.stop_event.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        try:
            self.log("=" * 40)
            if already:
                self.log("以下设备已在运行，跳过: %s" % ", ".join(already))
            if swipe_on:
                self.log("开始刷视频：本次启动 %d 台%s，间隔 %.1f~%.1f 秒，下滑概率 %d%%"
                         % (len(to_start),
                            ("（另有 %d 台已在运行）" % len(already)) if already else "",
                            tmin, tmax, self.var_prob.get()))
            else:
                self.log("开始赚金币任务模式：本次启动 %d 台%s（不滑动，只执行宝箱/图像任务/防息屏）"
                         % (len(to_start),
                            ("（另有 %d 台已在运行）" % len(already)) if already else ""))

            for serial in to_start:
                self._spawn_device_worker(serial, tmin, tmax)

            self.set_status("运行中… 共 %d 台手机" % len(to_start))
        except Exception as e:
            # 补启中途异常：不影响已在运行的设备，仅回滚本次新增状态并记录 ERROR
            self.log_error("启动刷视频失败: %s" % e, e)
            if not self.running or not any(t.is_alive() for t in self.workers.values()):
                self.running = False
                self.stop_event.set()
                self._update_start_btn_state()
                self.btn_stop.config(state="disabled")
                self.set_status("启动失败")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.stop_event.set()
        # 清掉单台暂停标志残留（线程即将全部退出；下次开始刷会重建）
        for pe in self.pause_events.values():
            pe.clear()
        self.pause_events.clear()
        self.task_done_flags.clear()
        self._update_start_btn_state()
        self.btn_stop.config(state="disabled")
        total = sum(self.counters.values())
        self.set_status("已停止")
        self.log("全部停止，总计刷 %d 个视频" % total)
        for serial, w in self.device_widgets.items():
            w["state"].config(text="已停止", foreground="gray")
            self._update_pause_btn(serial)
            self._disable_stayon(serial)

    def _spawn_device_worker(self, serial, tmin, tmax):
        """为单台设备创建并启动刷视频线程（整体开始 / 单台单独开始共用）"""
        self.counters[serial] = 0
        self.pause_events[serial] = threading.Event()  # 每台独立暂停标志（默认不暂停）
        self._update_device_count(serial)
        w = self.device_widgets.get(serial)
        if w:
            w["state"].config(text="运行中", foreground="green")
        self._update_pause_btn(serial)
        t = threading.Thread(target=self._swipe_loop,
                             args=(serial, tmin, tmax), daemon=True)
        self.workers[serial] = t
        t.start()

    def start_device(self, serial):
        """单台设备单独开始刷（对应设备线程独立 start，多设备互不影响；已在运行则提示并忽略）"""
        t = self.workers.get(serial)
        if t is not None and t.is_alive():
            self.log("%s ▶ 已在运行中，忽略本次单独开始" % serial)
            return
        tmin, tmax = self.var_min.get(), self.var_max.get()
        swipe_on = self.var_swipe.get()
        if swipe_on:
            if tmin > tmax:
                messagebox.showwarning("提示", "最小间隔不能大于最大间隔")
                return
            if tmin < 1:
                messagebox.showwarning("提示", "间隔太短可能损伤手机，最低 1 秒")
                return
        if not self.running:
            self.running = True
            self.stop_event.clear()
            self.btn_stop.config(state="normal")
        self.log("=" * 40)
        if swipe_on:
            self.log("单独开始刷：%s（间隔 %.1f~%.1f 秒，下滑概率 %d%%）"
                     % (serial, tmin, tmax, self.var_prob.get()))
        else:
            self.log("单独开始赚金币任务模式：%s（不滑动，只执行宝箱/图像任务/防息屏）" % serial)
        try:
            self._spawn_device_worker(serial, tmin, tmax)
            self.set_status("运行中… %s 已单独开始" % serial)
        except Exception as e:
            self.log_error("单独开始失败: %s" % e, e)
            self.running = False
            self.stop_event.set()
            self._update_start_btn_state()
            self.btn_stop.config(state="disabled")

    def _swipe(self, serial, direction="up"):
        """上滑=看下一个；下滑=回看上一个"""
        w, h = self.screen_sizes.get(serial, (1080, 2400))
        cx = w // 2 + random.randint(-int(w * 0.05), int(w * 0.05))
        if direction == "up":
            y1, y2 = int(h * 0.75), int(h * 0.25)
        else:
            y1, y2 = int(h * 0.25), int(h * 0.75)
        dur = random.randint(180, 420)
        code, _, err = run_adb("-s", serial, "shell",
                               "input swipe %d %d %d %d %d" % (cx, y1, cx, y2, dur),
                               timeout=20)
        if code != 0:
            self.log("%s 滑动失败: %s" % (serial, err or ("返回码 %d" % code)))
            return False
        return True

    def _maybe_click_box(self, serial):
        """到点自动点宝箱"""
        if not self.var_box.get():
            return
        interval = max(5, self.var_box_min.get()) * 60
        now = time.time()
        if now - self.last_box_click.get(serial, 0) < interval:
            return
        self.last_box_click[serial] = now
        target = self.target_apps.get(serial)
        coord = self.coords["box"].get(target)
        if not coord:
            if now - self.last_box_hint > 600:
                self.last_box_hint = now
                self.log("%s ℹ️ 未采集宝箱坐标，跳过（手机打开宝箱所在页面后点「采集坐标」）" % serial)
            return
        w, h = self.screen_sizes.get(serial, (1080, 2400))
        x, y = int(w * coord[0]), int(h * coord[1])
        code, _, _ = run_adb("-s", serial, "shell", "input tap %d %d" % (x, y), timeout=15)
        if code == 0:
            self.log("%s 🎁 已点击宝箱 (%d,%d)" % (serial, x, y))
            # 宝箱弹窗可能挡住视频，2 秒后按返回关掉再继续
            time.sleep(2)
            run_adb("-s", serial, "shell", "input keyevent 4", timeout=15)

    def _swipe_loop(self, serial, tmin, tmax):
        self._read_screen_size(serial)
        w, h = self.screen_sizes[serial]
        self.log("%s 屏幕 %dx%d，3 秒后开始（请切到抖音/快手）" % (serial, w, h))
        time.sleep(3)

        # 锁定目标应用：以开始刷时的前台应用为准
        fg = self._get_foreground(serial)
        if fg:
            self.target_apps[serial] = fg
            self.log("%s 目标应用: %s (%s)" % (serial, APP_NAMES.get(fg, fg), fg))
        else:
            self.target_apps.pop(serial, None)
            self.log("%s ⚠️ 无法识别前台应用，将不做误触检测" % serial)

        self._enable_stayon(serial)
        self.last_box_click[serial] = time.time()

        try:
            while not self.stop_event.is_set():
                # 任务完成：该设备已识别到 task_done 模板，停止本设备线程（其它设备不受影响）
                if self.task_done_flags.get(serial):
                    break
                # 单台暂停：线程不退出，阻塞等待「继续」或全局「全部停止」
                pe = self.pause_events.get(serial)
                if pe is not None and pe.is_set():
                    w = self.device_widgets.get(serial)
                    if w:
                        self.root.after(0, lambda: w["state"].config(text="已暂停", foreground="orange"))
                    self.set_status("运行中… %s 已暂停（单台，可点该行「▶ 继续」）" % serial)
                    while pe.is_set() and not self.stop_event.is_set():
                        time.sleep(0.2)
                    if self.stop_event.is_set():
                        break
                    if w:
                        self.root.after(0, lambda: w["state"].config(text="运行中", foreground="green"))
                    self.log("%s 已恢复刷" % serial)
                    continue
                # 误触检测：不在目标应用上时先恢复，不刷、不计数
                if not self._wait_on_target(serial):
                    break
                # 画面级界面异常自动纠正（弹窗/广告/错误页/退桌面等分层处理；连续失败停本设备）
                if not self._check_and_correct_ui(serial):
                    break

                self._keep_awake(serial)
                self._maybe_click_box(serial)
                self._image_tasks(serial)

                if self.var_swipe.get():
                    direction = "down" if random.randint(1, 100) <= self.var_prob.get() else "up"
                    if self._swipe(serial, direction):
                        self._swipe_fail_count[serial] = 0
                        self.counters[serial] = self.counters.get(serial, 0) + 1
                        self._update_device_count(serial)
                        self._update_total()
                        tag = "回看(下滑)" if direction == "down" else "下一个(上滑)"
                        self.log("%s 第 %d 次: %s" % (serial, self.counters[serial], tag))
                    else:
                        # 连续失败达到阈值：经 root.after 安全触发语音提醒「刷视频异常」（播报后重置计数）
                        self._swipe_fail_count[serial] = self._swipe_fail_count.get(serial, 0) + 1
                        if self._swipe_fail_count[serial] >= SWIPE_FAIL_SPEAK_THRESHOLD:
                            self._swipe_fail_count[serial] = 0
                            self.root.after(0, lambda s=serial: self._speak_event(s, "刷视频异常"))
                    wait = random.uniform(tmin, tmax)  # 每台独立随机节奏
                else:
                    wait = 10  # 纯任务模式：每 10 秒巡检一次宝箱/图像任务

                self.set_status("运行中… %s 下次检查 %.1f 秒后" % (serial, wait))
                end_t = time.time() + wait
                while time.time() < end_t and not self.stop_event.is_set():
                    time.sleep(0.2)
        except Exception as e:
            self.log_error("%s 刷视频线程异常，已退出: %s" % (serial, e), e)
        finally:
            try:
                self._disable_stayon(serial)
            except Exception:
                pass
            # 线程退出后清理自身登记（开始/暂停按钮存活判断依赖 workers）
            if self.workers.get(serial) is threading.current_thread():
                self.workers.pop(serial, None)
            self.task_done_flags.pop(serial, None)
            # 全局停止时 stop() 已统一置状态；单台自行退出（任务完成/异常）则把该设备标为已停止
            if not self.stop_event.is_set():
                self.root.after(0, lambda s=serial: self._mark_device_stopped(s))
            self.log(serial + " 线程退出")

    def _mark_device_stopped(self, serial):
        """单台线程自行退出后刷新设备行状态（开始按钮恢复可用）"""
        w = self.device_widgets.get(serial)
        if w:
            w["state"].config(text="已停止", foreground="gray")
        self._update_pause_btn(serial)
