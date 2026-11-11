# -*- coding: utf-8 -*-
"""adb 设备检测/监听/自动无线连接/设备行 UI/单台暂停/备注。"""

import os
import re
import threading
import time
from tkinter import messagebox
from tkinter import simpledialog
import tkinter as tk
from tkinter import ttk

from adb_utils import run_adb

class DevicesMixin:
    def detect_devices(self, quiet=False):
        """检测已连接设备并刷新列表（手动按钮与自动轮询共用入口）。
        quiet=True（自动轮询）时 adb 失败仅记日志不弹框。任何异常记录 ERROR 日志，不静默崩溃。"""
        with self._device_detect_lock:
            try:
                state_map, devices = self._list_devices()
                if state_map is None:
                    msg = "adb 执行失败，请确认 adb 已安装并加入 PATH，或在「设置」里选择 adb.exe 路径"
                    if quiet:
                        self.log("自动检测设备：%s" % msg)
                    else:
                        messagebox.showerror("错误", msg)
                    return
                # 同步自动轮询的设备集合签名，避免把刚刷新过的列表误判为变化
                self._last_detect_sig = set(devices)

                # 语音提醒：对比上次检测结果，计算上线/下线/异常设备
                # （_prev_devices 只含正常设备，同一 serial 只播一次：下线/异常播报后移出，重新上线才再次播报，天然去重）
                new_devices = [d for d in devices if d not in self._prev_devices]
                gone = [d for d in self._prev_devices if d not in devices]
                abnormal = [s for s, st in state_map.items() if st != "device" and s in self._prev_devices]
                self._prev_devices = set(devices)

                # 语音提醒（随语音控制开关）：上线「备注名上线/新设备上线」/ 下线「已断开」/ offline、unauthorized「连接异常」
                # 必须放在下方「未检测到设备」return 之前，否则设备全部消失/离线时播报会丢失
                if self.var_voice_control.get():
                    for d in new_devices:
                        self._speak_online(d)
                    for d in abnormal:
                        self._speak_event(d, "设备离线" if state_map[d] == "offline" else "连接异常")
                    for d in gone:
                        if d not in abnormal:
                            self._speak_event(d, "已断开")

                # USB 设备新接入时自动开启无线调试端口 5555（serial 不带冒号即为 USB 连接；
                # daemon 线程执行，避免 adb tcpip 阻塞 UI；已存在于 _prev_devices 不会重复触发）
                for d in new_devices:
                    if ":" not in d:
                        self._auto_tcpip(d)

                # 刷新列表前保留旧勾选状态（自动轮询刷新时不让用户勾选被重置）
                prev_checked = {s: w["var"].get() for s, w in self.device_widgets.items()}
                # 清空旧列表
                for child in self.frm_list.winfo_children():
                    child.destroy()
                self.device_widgets.clear()

                if not devices:
                    ttk.Label(self.frm_list, text="未检测到设备（无线请填 IP:5555 后点「连接」）",
                              foreground="red").pack(anchor="w")
                    self.log("未检测到设备")
                    self._sync_select_all_state()
                    return

                for d in devices:
                    self._add_device_row(d, checked=prev_checked.get(d, True))
                self._sync_select_all_state()
                self.log("检测到 %d 台设备: %s" % (len(devices), ", ".join(devices)))
            except Exception as e:
                self.log_error("检测设备失败: %s" % e, e)

    def _list_devices(self, timeout=15):
        """执行 adb devices 并解析 serial -> 状态。返回 (state_map, devices)；adb 失败返回 (None, [])。"""
        code, out, _ = run_adb("devices", timeout=timeout)
        if code != 0:
            return None, []
        state_map = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("device", "offline", "unauthorized"):
                state_map[parts[0]] = parts[1]
        devices = [s for s, st in state_map.items() if st == "device"]
        return state_map, devices

    def _auto_tcpip(self, serial):
        """USB 设备接入后自动开启无线调试端口 5555（daemon 线程执行，不阻塞 UI）。
        成功后自动获取设备 IP 显示到日志与设备行，拔掉 USB 后即可用 adb connect <IP>:5555 无线连接。"""
        def _run():
            try:
                self.log("USB 设备 %s 已接入，自动开启无线调试端口 5555 …" % serial)
                ret, out, err = run_adb("-s", serial, "tcpip", "5555")
                text = (out or err).strip()
                if ret == 0:
                    # adb tcpip 后设备端 adbd 会重启，立即查 IP 会报 device not found，先等设备恢复
                    time.sleep(5)
                    ip = self._fetch_device_ip(serial)
                    self._device_ips[serial] = ip
                    addr = ("%s:5555" % ip) if ip else "<手机IP>:5555"
                    self.log("设备 %s 无线调试已开启：拔线后可用 adb connect %s 无线连接" % (serial, addr))
                    if ip:
                        # 拿到真实 IP 后自动无线连接并验证连通，拔线后即处于无线连接状态
                        try:
                            c_ret, c_out, c_err = run_adb("connect", "%s:5555" % ip, timeout=10)
                            c_text = (c_out or c_err).strip()
                            if c_ret == 0 and ("connected" in c_text or "already connected" in c_text):
                                self.log("已自动无线连接 %s:5555，拔线后仍可无线使用" % ip)
                                if self.var_voice_control.get():
                                    self._speak_event(serial, "无线已连接")
                            else:
                                self.log("自动无线连接 %s:5555 未成功：%s（拔线后请手动连接）" % (ip, c_text or "未知错误"))
                        except Exception as ce:
                            self.log("自动无线连接 %s:5555 异常：%s（拔线后请手动连接）" % (ip, ce))
                    try:
                        self.root.after(0, lambda s=serial: self._update_serial_ip_label(s))
                    except Exception:
                        pass
                    if self.var_voice_control.get():
                        self._speak_event(serial, "无线已开启")
                else:
                    self.log_error("设备 %s 开启无线调试失败: %s" % (serial, text))
                    if self.var_voice_control.get():
                        self._speak_event(serial, "开启无线异常")
            except Exception as e:
                self.log_error("自动开启无线调试异常: %s" % e, e)
        threading.Thread(target=_run, daemon=True).start()

    def _fetch_device_ip(self, serial):
        """读取 USB 设备当前无线 IP：优先 getprop dhcp.wlan0.ipaddress，其次 wlan0 接口，
        再全量接口扫第一个非回环 IPv4；tcpip 后 adbd 重启导致的 device not found 会重试；失败返回空串。"""
        for attempt in range(3):
            try:
                # 部分 ROM 提供 dhcp 属性，直接拿 IP
                code, out, err = run_adb("-s", serial, "shell", "getprop", "dhcp.wlan0.ipaddress", timeout=10)
                v = (out or "").strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", v) and not v.startswith("127."):
                    return v
                # 优先 wlan0 接口
                code, out, err = run_adb("-s", serial, "shell", "ip", "addr", "show", "wlan0", timeout=10)
                m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out or "")
                if m and not m.group(1).startswith("127."):
                    return m.group(1)
                # 全量接口兜底
                code, out, err = run_adb("-s", serial, "shell", "ip", "addr", "show", timeout=10)
                for line in (out or "").splitlines():
                    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", line)
                    if m and not m.group(1).startswith("127."):
                        return m.group(1)
            except Exception:
                pass
            time.sleep(1.5)
        return ""

    def _update_serial_ip_label(self, serial):
        """设备行 serial/备注后追加无线连接地址（IP:5555），供拔线后直接照着连接"""
        w = self.device_widgets.get(serial)
        if w is None:
            return
        ip = self._device_ips.get(serial, "")
        text = self._note_text(serial)
        if ip:
            text = "%s　%s:5555" % (text, ip)
        w["serial_label"].config(text=text)

    def _start_auto_detect(self):
        """启动后台自动检测线程：每 3 秒探测一次设备，接入/拔出自动刷新列表并播报。"""
        if self._auto_detect_thread is not None and self._auto_detect_thread.is_alive():
            return
        self._auto_detect_thread = threading.Thread(target=self._auto_detect_loop, daemon=True)
        self._auto_detect_thread.start()
        self.log("自动检测设备已开启：每 3 秒检测一次，设备接入/拔出自动刷新列表")

    def _auto_detect_loop(self):
        """自动检测主循环（daemon 线程）：探测失败/异常时静默跳过，不阻塞、不弹框。"""
        while True:
            try:
                self._auto_detect_once()
            except Exception:
                pass
            time.sleep(3)

    def _auto_detect_once(self):
        """单次自动探测：仅当正常设备集合发生变化才回主线程刷新列表并播报。
        运行中不重建列表（避免破坏正在刷的线程对 device_widgets 的引用），停止后自动补刷。"""
        state_map, devices = self._list_devices(timeout=5)
        if state_map is None:
            return
        sig = set(devices)
        if sig == self._last_detect_sig:
            return
        # 运行中先不重建列表，停止后下一轮轮询会检测到差异并补刷
        if self.running:
            return
        self._last_detect_sig = sig
        try:
            self.root.after(0, lambda: self.detect_devices(quiet=True))
        except Exception:
            pass

    def _toggle_select_all(self):
        """全选/取消全选：勾选时全部设备打钩，取消时全部取消"""
        want = self.var_select_all.get()
        for w in self.device_widgets.values():
            w["var"].set(want)
        self._sync_select_all_state()

    def _sync_select_all_state(self):
        """个别勾选变化后同步全选框状态；设备列表重建后调用以刷新计数"""
        if not hasattr(self, "var_select_all"):
            return
        vals = [w["var"].get() for w in self.device_widgets.values()]
        self.var_select_all.set(bool(vals) and all(vals))
        if hasattr(self, "lbl_select_count"):
            checked = sum(vals)
            self.lbl_select_count.config(text="已选 %d/%d 台" % (checked, len(vals)))

    def _add_device_row(self, serial, checked=True):
        row = ttk.Frame(self.frm_list)
        row.pack(fill="x", pady=2)
        var = tk.BooleanVar(value=checked)
        ttk.Checkbutton(row, variable=var,
                        command=self._sync_select_all_state).pack(side="left")
        ip = self._device_ips.get(serial, "")
        label_text = self._note_text(serial)
        if ip:
            label_text = "%s　%s:5555" % (label_text, ip)
        serial_lbl = ttk.Label(row, text=label_text, font=("Consolas", 9))
        serial_lbl.pack(side="left", padx=4)
        # 设备备注：✎ 备注按钮，点击弹输入框（留空清除备注）
        ttk.Button(row, text="✎ 备注", width=6,
                   command=lambda s=serial: self._edit_note(s)).pack(side="left", padx=2)
        count_lbl = ttk.Label(row, text="已刷: %d" % self.counters.get(serial, 0), foreground="#06c")
        count_lbl.pack(side="left", padx=12)
        state_lbl = ttk.Label(row, text="", foreground="green")
        state_lbl.pack(side="left", padx=6)
        self.device_widgets[serial] = {"var": var, "count": count_lbl, "state": state_lbl,
                                       "serial_label": serial_lbl}
        # 单台「▶ 开始」按钮：始终显示，独立启动本台设备线程（已在运行则忽略）
        start_btn = ttk.Button(row, text="▶ 开始", width=7,
                               command=lambda s=serial: self.start_device(s))
        start_btn.pack(side="left", padx=4)
        self.device_widgets[serial]["start_btn"] = start_btn
        # 设置页「设备行显示单台暂停/继续按钮」开启时，为该设备行加一个小按钮（仅控制本台）
        if self.var_device_pause_btn.get():
            pause_btn = ttk.Button(row, text="⏸ 暂停", width=7,
                                   command=lambda s=serial: self.toggle_device_pause(s))
            pause_btn.pack(side="left", padx=4)
            self.device_widgets[serial]["pause_btn"] = pause_btn
        if serial not in self.counters:
            self.counters[serial] = 0

    def _refresh_device_rows(self):
        """按设置开关重建设备行（保留勾选状态与计数），用于切换「单台暂停按钮」开关后即时生效"""
        serials = list(self.device_widgets.keys())
        checked = {s: self.device_widgets[s]["var"].get() for s in serials}
        for child in self.frm_list.winfo_children():
            child.destroy()
        self.device_widgets.clear()
        for s in serials:
            self._add_device_row(s, checked=checked.get(s, True))
        self._sync_select_all_state()

    def _on_device_pause_btn_toggle(self):
        """单台暂停按钮开关变化：即时刷新设备行并保存"""
        try:
            self._save_ui_state()
        except Exception:
            pass
        self._refresh_device_rows()
        self.log("设备行单台暂停/继续按钮: %s" % ("开启" if self.var_device_pause_btn.get() else "关闭"))

    def toggle_device_pause(self, serial):
        """单台设备暂停/继续（仅影响该台，不影响其他设备；全局停止仍一键全停）"""
        pe = self.pause_events.get(serial)
        if pe is None:
            return
        if pe.is_set():
            pe.clear()
            self.log("%s ▶ 已继续" % serial)
        else:
            pe.set()
            self.log("%s ⏸ 已暂停（本台暂停，其他设备不受影响；点「■ 全部停止」可一键全停）" % serial)
        self._update_pause_btn(serial)

    def _update_pause_btn(self, serial):
        """刷新单台按钮（▶开始/⏸暂停）文案与状态标签（主线程安全）"""
        def _upd():
            w = self.device_widgets.get(serial)
            if not w:
                return
            pe = self.pause_events.get(serial)
            btn = w.get("pause_btn")
            sbtn = w.get("start_btn")
            alive = self.workers.get(serial) is not None and self.workers[serial].is_alive()
            if sbtn:
                sbtn.config(state="disabled" if alive else "normal")
            if pe is not None and pe.is_set():
                if btn:
                    btn.config(text="▶ 继续")
                w["state"].config(text="已暂停", foreground="orange")
            else:
                if btn:
                    btn.config(text="⏸ 暂停")
        self.root.after(0, _upd)

    def _note_text(self, serial, name=None):
        """设备行显示文本：有备注显示「备注名（serial）」，无备注只显示 serial"""
        if name is None:
            name = self.device_notes.get(serial) or ""
        name = (name or "").strip()
        return "%s（%s）" % (name, serial) if name else serial

    def _edit_note(self, serial):
        """✎ 备注：弹输入框编辑该设备备注名（留空或取消则清除备注）；保存后立即刷新并持久化"""
        cur = self.device_notes.get(serial, "")
        name = simpledialog.askstring("设备备注",
                                      "为该设备设置备注名（留空清除备注）：\n设备: %s" % serial,
                                      initialvalue=cur)
        if name is None:
            return  # 用户取消
        name = name.strip()
        if name:
            self.device_notes[serial] = name
        else:
            self.device_notes.pop(serial, None)
        self.config["device_notes"] = self.device_notes
        try:
            self._save_data()
        except Exception:
            pass
        self._update_note_label(serial)
        self.log("设备备注已保存: %s → %s" % (serial, name if name else "（无，已清除）"))

    def _update_note_label(self, serial):
        """刷新设备行备注显示（主线程安全）"""
        def _upd():
            w = self.device_widgets.get(serial)
            if w:
                lbl = w.get("serial_label")
                if lbl:
                    lbl.config(text=self._note_text(serial))
        self.root.after(0, _upd)

    def connect_device(self):
        addr = self.var_addr.get().strip()
        if not addr:
            messagebox.showwarning("提示", "请输入 IP:端口，例如 192.168.9.20:5555")
            return
        if ":" not in addr:
            addr += ":5555"
        self.log("正在连接 " + addr + " …")
        code, out, err = run_adb("connect", addr)
        text = out or err
        self.log("连接结果: " + text)
        if "connected" in text.lower() and "cannot" not in text.lower():
            self.var_addr.set("")
            self.detect_devices()
        else:
            messagebox.showwarning("提示", "连接失败: " + text +
                                   "\n请确认手机与电脑同一网络，且已开启无线调试")
            # 语音提醒：手动连接失败播报「{设备标识} 连接异常」（IP 形式自动截取 IP 部分）
            if self.var_voice_control.get():
                self._speak_event(addr, "连接异常")

    def batch_pair_device(self):
        """批量无线配对：每行格式 IP:端口 配对码"""
        text = self.pair_text.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("提示", "请在文本框中填写设备信息，格式：\nIP:端口 配对码")
            return
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not lines:
            messagebox.showwarning("提示", "没有有效的设备信息")
            return
        results = []
        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                results.append({"addr": line, "success": False, "msg": "格式错误"})
                continue
            addr = parts[0]
            code = parts[-1]
            ret, out, err = run_adb("pair", addr, code)
            result_text = (out or err).strip()
            success = ret == 0 and "successfully paired" in result_text.lower()
            results.append({"addr": addr, "code": code, "success": success, "msg": result_text})
        # 汇总结果
        ok = sum(1 for r in results if r["success"])
        fail = sum(1 for r in results if not r["success"])
        self.log(f"批量配对完成: {ok} 成功 / {fail} 失败 / 共 {len(results)} 台")
        msg = f"批量配对完成\n\n成功: {ok}\n失败: {fail}\n共: {len(results)} 台"
        for r in results:
            status = "✓" if r["success"] else "✗"
            msg += f"\n{status} {r['addr']} 配对码 {r['code']}"
        messagebox.showinfo("批量配对结果", msg)
        if ok > 0 and self.var_voice_control.get():
            self._speak_event("全部", f"{ok} 台配对成功，{fail} 台失败")

    def _update_total(self):
        total = sum(self.counters.values())
        self.root.after(0, lambda: self.lbl_total.config(text="总计: %d 个视频" % total))

    def _update_device_count(self, serial):
        def _upd():
            w = self.device_widgets.get(serial)
            if w:
                w["count"].config(text="已刷: %d" % self.counters.get(serial, 0))
        self.root.after(0, _upd)

    def _checked_devices(self):
        return [s for s, w in self.device_widgets.items() if w["var"].get()]
