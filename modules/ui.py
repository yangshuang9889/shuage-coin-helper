# -*- coding: utf-8 -*-
"""UI 构建（设置页/设备页/关于/更新/公告/赞助）。"""

import base64
import hashlib
import re
import threading
import urllib.request
import webbrowser
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk

try:
    import cv2
    import numpy as np
    CV_OK = True
except ImportError:
    CV_OK = False

from constants import APP_VERSION, APP_SPONSOR_QR_B64, OFF_WORDS, SPONSOR_AMT_RE, SPONSOR_DATE_RE, SPONSOR_HEAD_RE
from swipe import CV_OK
from voice_config import DEFAULT_WAKE_WORD, check_voice_config

def _norm_amount(s):
    """金额格式化：20 -> ¥20，¥ 20 -> ¥20，30元 保持原样，空 -> —"""
    s = (s or "").strip()
    if not s:
        return "—"
    if re.search(r"[¥￥]", s):
        return "¥" + re.sub(r"[¥￥\s]", "", s)
    if "元" in s or "块" in s:
        return s
    return "¥" + s

def remote_url(value, default):
    """解析四个远程地址配置项，返回实际要用的地址：
      · 留空     → 用内置默认地址
      · off/none/关闭 等停用词 → 返回空字符串（该功能临时停用）
      · 其他     → 原样返回（是否合法由调用处再判断）
    """
    v = (value or "").strip()
    if not v:
        return (default or "").strip()
    if v.lower() in OFF_WORDS:
        return ""
    return v

def parse_sponsor_rows(text):
    """解析赞助名单文本（在线 md/txt），宽松兼容多种写法：
      · markdown 表格   | 网名 | 金额 | 时间 |
      · 分隔符         网名 | 金额 | 时间  /  网名,金额,时间  /  制表符
      · 自由行         网名 ¥20 2026-09-23
    标题(#)、引用(>)、代码块(```)、html 注释、表头行、表格分隔线都会自动跳过。
    返回 [{"name": 网名, "amount": 金额, "time": 时间}, ...]
    """
    rows = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if re.fullmatch(r"[|:\-\s=+]+", s):            # 表格分隔线 / 纯符号行
            continue
        if re.match(r"#{1,6}\s", s):                   # 标题
            continue
        if s.startswith("<!--") or s.endswith("-->"):  # html 注释
            continue
        if re.match(r"</?[A-Za-z!]", s):               # html 标签
            continue
        if s[0] in ">!":                               # 引用 / 图片
            continue
        if s.startswith("```"):                        # 代码块标记
            continue
        s = re.sub(r"^[-*+]\s+", "", s).strip()

        # 分隔符探测：竖线 > 中文逗号 > 英文逗号 > 制表符
        if "|" in s:
            sep = "|"
        elif "，" in s:
            sep = "，"
        elif "," in s:
            sep = ","
        elif "\t" in s:
            sep = "\t"
        else:
            sep = None

        name = amount = tm = ""
        if sep:
            cells = [c.strip() for c in s.split(sep)]
            while cells and not cells[0]:              # 去首部空列
                cells.pop(0)
            while cells and not cells[-1]:             # 去尾部空列（保留中间空位）
                cells.pop()
            if len(cells) >= 3:
                name, amount, tm = cells[0], cells[1], cells[2]
            elif len(cells) == 2:
                name, amount = cells
            else:
                name = cells[0] if cells else ""
        else:
            rest = s
            dm = SPONSOR_DATE_RE.search(rest)
            if dm:
                rest = rest.replace(dm.group(0), " ")
            am = SPONSOR_AMT_RE.search(rest)
            if am:
                amount = am.group(0)
                rest = rest.replace(am.group(0), " ")
            name = re.sub(r"^[\s|]+|[\s,，:：、\-—|]+$", "", rest).strip()
            if dm:
                tm = dm.group(1)

        if SPONSOR_HEAD_RE.match(name):                # 表头行跳过
            continue
        if not name and not amount and not tm:
            continue
        rows.append({
            "name": name or "热心网友",
            "amount": _norm_amount(amount),
            "time": (tm.replace(" ", "").replace("年", "-").replace("月", "-")
                       .replace("日", "").replace("/", "-").replace(".", "-")),
        })
    return rows

class _MicListTooltip:
    """常驻列表框的悬停说明气泡：鼠标悬停到某个选项时，弹出该项的说明气泡。

    列表框（tk.Listbox）直接常驻设置页，不再点击弹出下拉；descs 为 dict
    （按显示文本查说明）或 callable（传入显示文本返回说明），返回空则不弹气泡。
    """

    def __init__(self, listbox, descs):
        self.listbox = listbox
        self.descs = descs or {}
        self._tip = None          # 说明气泡 Toplevel
        self._tip_after = None    # 气泡延迟句柄
        self._last_index = -1
        listbox.bind("<Motion>", self._on_motion, add="+")
        listbox.bind("<Leave>", self._hide_tip, add="+")
        listbox.bind("<MouseWheel>", self._on_wheel, add="+")
        listbox.bind("<Destroy>", self._cleanup, add="+")

    def _desc_for(self, value):
        try:
            if callable(self.descs):
                desc = self.descs(value) or ""
            else:
                desc = self.descs.get(value, "") or ""
        except Exception:
            desc = ""
        # 防呆：说明文字不应等于选项文本本身（避免误显示设备名）
        if desc == (value or "").strip():
            return ""
        return desc

    def _on_motion(self, event):
        lb = self.listbox
        try:
            idx = lb.nearest(event.y)
            if idx < 0 or idx >= lb.size():
                return
        except Exception:
            return
        if idx != self._last_index:
            self._last_index = idx
            try:
                lb.selection_clear(0, "end")
                lb.selection_set(idx)
                lb.activate(idx)
            except Exception:
                pass
            self._hide_tip()
            self._schedule_tip(idx)

    def _on_wheel(self, event):
        lb = self.listbox
        if lb.size() == 0:
            return "break"
        try:
            delta = -1 if event.delta > 0 else 1
            lb.yview_scroll(delta, "units")
            first = int(lb.index("@0,0"))
            lb.selection_clear(0, "end")
            lb.selection_set(first)
            lb.activate(first)
        except Exception:
            return "break"
        self._last_index = first
        self._hide_tip()
        self._schedule_tip(first)
        return "break"

    def _schedule_tip(self, idx):
        lb = self.listbox
        try:
            val = lb.get(idx)
        except Exception:
            return
        desc = self._desc_for(val)
        if not desc:
            return

        def show():
            if self.listbox is None:
                return
            try:
                bbox = self.listbox.bbox(idx)
                if not bbox:
                    return
                tip = tk.Toplevel(self.listbox)
                tip.wm_overrideredirect(True)
                try:
                    tip.lift()
                    tip.attributes("-topmost", True)
                except Exception:
                    pass
                lbl = tk.Label(tip, text=desc, justify="left",
                               bg="#ffffe0", fg="#333333",
                               font=("Microsoft YaHei", 9),
                               padx=8, pady=5, relief="solid", borderwidth=1)
                lbl.pack()
                tip.update_idletasks()
                tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
                tx = self.listbox.winfo_rootx() + bbox[0] + bbox[2] + 4
                ty = self.listbox.winfo_rooty() + bbox[1]
                sw = self.listbox.winfo_screenwidth()
                sh = self.listbox.winfo_screenheight()
                if tx + tw > sw - 8:
                    tx = max(8, self.listbox.winfo_rootx() + bbox[0] - tw)
                if ty + th > sh - 8:
                    ty = max(8, self.listbox.winfo_rooty() + bbox[1] - th - 2)
                tip.wm_geometry("+%d+%d" % (tx, ty))
                self._tip = tip
            except Exception:
                self._tip = None

        try:
            self._tip_after = lb.after(300, show)
        except Exception:
            self._tip_after = None

    def _hide_tip(self, _e=None):
        if self._tip_after is not None:
            try:
                self.listbox.after_cancel(self._tip_after)
            except Exception:
                pass
            self._tip_after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _cleanup(self, _e=None):
        self._hide_tip()


def _mic_item_desc(value):
    """麦克风下拉选项的气泡文字：悬停显示设备名（去掉编号与程序附加的虚拟声卡标注）；自动/跟随无设备名则不弹。"""
    v = (value or "").strip()
    if v.startswith("自动") or v.startswith("跟随"):
        return ""
    v = re.sub(r"^\d+\s*-\s*", "", v)
    v = v.replace("（虚拟声卡）", "")
    return v.strip()


class UiMixin:
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # 设备区
        frm_dev = ttk.LabelFrame(self.root, text="设备（勾选要刷的手机）")
        frm_dev.pack(fill="x", **pad)
        row1 = ttk.Frame(frm_dev)
        row1.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Button(row1, text="检测设备", command=self.detect_devices).pack(side="left", padx=4)
        ttk.Button(row1, text="打开投屏", command=self.launch_scrcpy).pack(side="left", padx=4)

        row2 = ttk.Frame(frm_dev)
        row2.pack(fill="x", padx=8, pady=(2, 2))
        ttk.Label(row2, text="无线连接 IP:端口").pack(side="left")
        self.var_addr = tk.StringVar()
        ttk.Entry(row2, textvariable=self.var_addr, width=22).pack(side="left", padx=6)
        ttk.Button(row2, text="连接", command=self.connect_device).pack(side="left")

        row3 = ttk.Frame(frm_dev)
        row3.pack(fill="x", padx=8, pady=(2, 8))
        self.pair_text = tk.Text(row3, height=3, width=50, font=("Microsoft YaHei", 9),
                                 bd=1, relief="solid", highlightthickness=0)
        self.pair_text.pack(side="left", fill="x", expand=True)
        self.pair_text.insert("1.0", "# 每行一个：IP:端口 配对码\n# 例：192.168.1.100:37013 123456")
        self.pair_text.bind("<Return>", self._on_pair_text_enter)
        self.pair_text.bind("<Control-Return>", self._on_pair_text_newline)
        ttk.Button(row3, text="批量配对", command=self.batch_pair_device).pack(side="left", padx=6)
        ttk.Label(row3, text="（每行一个设备：IP:端口+空格+配对码；配对成功后再用上方 IP:端口连接）",
                  foreground="#888").pack(side="left", padx=8)

        # 全选/取消全选（勾上全部打钩，取消全部取消）
        row_select = ttk.Frame(frm_dev)
        row_select.pack(fill="x", padx=8, pady=(0, 2))
        self.var_select_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(row_select, text="全选设备", variable=self.var_select_all,
                        command=self._toggle_select_all).pack(side="left", padx=2)
        self.lbl_select_count = ttk.Label(row_select, text="", foreground="#888")
        self.lbl_select_count.pack(side="left", padx=6)

        # 设备列表（可滚动，设备多也不会把窗口撑破）
        list_wrap = ttk.Frame(frm_dev)
        list_wrap.pack(fill="x", padx=8, pady=(0, 8))
        self.can_list = tk.Canvas(list_wrap, height=132, highlightthickness=0,
                                  bg="#f7f7f7")
        vsb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.can_list.yview)
        self.can_list.configure(yscrollcommand=vsb.set)
        self.can_list.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.frm_list = ttk.Frame(self.can_list)
        self._list_win = self.can_list.create_window((0, 0), window=self.frm_list, anchor="nw")
        self.frm_list.bind("<Configure>", lambda e: self.can_list.configure(
            scrollregion=self.can_list.bbox("all")))
        self.can_list.bind("<Configure>", lambda e: self.can_list.itemconfigure(
            self._list_win, width=e.width))
        # 鼠标滚轮：仅当指针在设备列表上时滚动列表（不影响日志）
        self.can_list.bind_all("<MouseWheel>", self._on_list_wheel, add="+")
        ttk.Label(self.frm_list, text="尚未检测，请先点「检测设备」",
                  foreground="gray").pack(anchor="w", padx=4, pady=4)

        # 参数/任务分组标签页
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="x", **pad)
        tab_swipe = ttk.Frame(self.nb)
        tab_coin = ttk.Frame(self.nb)
        tab_img = ttk.Frame(self.nb)
        tab_set = ttk.Frame(self.nb)
        self.nb.add(tab_swipe, text="  刷视频  ")
        self.nb.add(tab_coin, text="  赚金币任务  ")
        self.nb.add(tab_img, text="  图像任务  ")
        self.nb.add(tab_set, text="  设置  ")

        # 参数区（刷视频标签页）
        frm_cfg = ttk.LabelFrame(tab_swipe, text="刷视频参数（对每台手机独立生效）")
        frm_cfg.pack(fill="x", padx=6, pady=6)
        ttk.Label(frm_cfg, text="最小间隔(秒)").grid(row=0, column=0, padx=8, pady=6, sticky="e")
        self.var_min = tk.DoubleVar(value=3.0)
        ttk.Spinbox(frm_cfg, from_=1, to=60, increment=0.5, width=6,
                    textvariable=self.var_min).grid(row=0, column=1, padx=4)

        ttk.Label(frm_cfg, text="最大间隔(秒)").grid(row=0, column=2, padx=8, pady=6, sticky="e")
        self.var_max = tk.DoubleVar(value=10.0)
        ttk.Spinbox(frm_cfg, from_=1, to=120, increment=0.5, width=6,
                    textvariable=self.var_max).grid(row=0, column=3, padx=4)

        ttk.Label(frm_cfg, text="下滑回看概率(%)").grid(row=1, column=0, padx=8, pady=6, sticky="e")
        self.var_prob = tk.IntVar(value=10)
        ttk.Spinbox(frm_cfg, from_=0, to=100, increment=5, width=6,
                    textvariable=self.var_prob).grid(row=1, column=1, padx=4)

        self.var_recover = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_cfg, text="误触弹窗自动恢复（按返回+重启目标应用）",
                        variable=self.var_recover).grid(row=2, column=0, columnspan=4,
                                                       padx=8, pady=6, sticky="w")

        self.var_swipe = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_cfg, text="自动上滑刷视频（不勾选 = 只执行宝箱/图像等赚金币任务，不滑动）",
                        variable=self.var_swipe).grid(row=3, column=0, columnspan=4,
                                                      padx=8, pady=6, sticky="w")

        # 赚金币任务区（赚金币标签页）
        frm_coin = ttk.LabelFrame(tab_coin, text="赚金币任务")
        frm_coin.pack(fill="x", padx=6, pady=6)
        self.var_stayon = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_coin, text="防息屏（刷时屏幕常亮）",
                        variable=self.var_stayon).grid(row=0, column=0, padx=8, pady=6,
                                                       sticky="w")
        self.var_box = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_coin, text="定时点宝箱，间隔",
                        variable=self.var_box).grid(row=0, column=1, padx=8, pady=6, sticky="w")
        self.var_box_min = tk.IntVar(value=30)
        ttk.Spinbox(frm_coin, from_=5, to=240, increment=5, width=5,
                    textvariable=self.var_box_min).grid(row=0, column=2)
        ttk.Label(frm_coin, text="分钟").grid(row=0, column=3, padx=4, pady=6, sticky="w")

        row_btn = ttk.Frame(frm_coin)
        row_btn.grid(row=1, column=0, columnspan=4, padx=8, pady=(2, 8), sticky="w")
        ttk.Button(row_btn, text="✍ 采集坐标（先在手机打开对应页面）",
                   command=self.capture_coords).pack(side="left", padx=4)
        ttk.Button(row_btn, text="🔔 立即签到", command=self.run_sign_in).pack(side="left", padx=4)
        self.lbl_coords = ttk.Label(row_btn, text=self._coords_summary(), foreground="#888")
        self.lbl_coords.pack(side="left", padx=10)

        # 图像任务区（图像任务标签页）
        frm_img = ttk.LabelFrame(tab_img, text="图像任务（按钮出现才点，支持到时间才开的任务）")
        frm_img.pack(fill="x", padx=6, pady=6)
        row_img = ttk.Frame(frm_img)
        row_img.pack(fill="x", padx=8, pady=6)
        if not CV_OK:
            ttk.Label(frm_img, text="⚠️ 缺少 OpenCV，图像识别不可用", foreground="red").pack(
                anchor="w", padx=8, pady=4)
        self.var_img = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_img, text="启用图像任务", variable=self.var_img).pack(side="left")
        ttk.Label(row_img, text="检查间隔").pack(side="left", padx=(10, 2))
        self.var_img_interval = tk.IntVar(value=15)
        ttk.Spinbox(row_img, from_=5, to=120, increment=5, width=4,
                    textvariable=self.var_img_interval).pack(side="left")
        ttk.Label(row_img, text="秒").pack(side="left")
        ttk.Button(row_img, text="🖼 采集图像模板（拖拽框选按钮）",
                   command=self.capture_template).pack(side="left", padx=10)

        row_tpl = ttk.Frame(frm_img)
        row_tpl.pack(fill="x", padx=8, pady=(0, 6))
        self.cmb_tpl = ttk.Combobox(row_tpl, width=26, state="readonly")
        self.cmb_tpl.pack(side="left")
        ttk.Button(row_tpl, text="✏ 编辑模板",
                   command=self.edit_template).pack(side="left", padx=(6, 0))
        ttk.Button(row_tpl, text="＋ 新增模板(选图片)",
                   command=self.add_template_local).pack(side="left", padx=6)
        ttk.Button(row_tpl, text="✏ 改分类",
                   command=self.rename_template_dir).pack(side="left")
        ttk.Button(row_tpl, text="删除选中模板",
                   command=self.delete_template).pack(side="left", padx=8)
        ttk.Button(row_tpl, text="📂 打开模板目录",
                   command=self.open_tpl_dir).pack(side="left")
        self.lbl_tpl = ttk.Label(row_tpl, text="", foreground="#888")
        self.lbl_tpl.pack(side="left", padx=10)
        self._refresh_tpl_list()

        # 设置区（设置标签页）
        frm_set = ttk.LabelFrame(tab_set, text="设置（语音控制与界面选项）")
        frm_set.pack(fill="x", padx=6, pady=6)
        # 新功能开关（默认关闭，勾选即时生效并保存）
        self.var_device_pause_btn = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_set, text="设备行显示单台暂停/继续按钮（默认关闭）",
                        variable=self.var_device_pause_btn,
                        command=self._on_device_pause_btn_toggle).grid(
            row=1, column=0, columnspan=4, padx=8, pady=(4, 0), sticky="w")
        self.var_voice_control = tk.BooleanVar(value=False)
        voice_ok, voice_reason = check_voice_config()
        self._voice_config_ok = voice_ok
        self._voice_config_reason = voice_reason
        voice_cb = ttk.Checkbutton(frm_set, text="语音控制（开刷/停止、离线识别、语音提醒（设备上下线/异常播报），默认关闭）",
                                   variable=self.var_voice_control,
                                   command=self._on_voice_toggle)
        voice_cb.grid(row=2, column=0, columnspan=3, padx=8, pady=(2, 4), sticky="w")
        if not voice_ok:
            # 配置不达标：开关置灰禁用（保留原值不影响持久化），旁边显示原因
            voice_cb.state(["disabled"])
            ttk.Label(frm_set, text="（%s）" % voice_reason,
                      foreground="#e06060").grid(
                row=2, column=3, padx=(0, 8), pady=(2, 4), sticky="w")
        # 界面异常自动纠正（画面级加固开关，默认开启，勾选即时生效并保存）
        self.var_ui_correct = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_set, text="界面异常自动纠正（非刷视频界面自动分层纠正，默认开启）",
                        variable=self.var_ui_correct,
                        command=self._on_ui_correct_toggle).grid(
            row=3, column=0, columnspan=4, padx=8, pady=(2, 4), sticky="w")
        # 唤醒词文本框（语音控制专用，改动即持久化；清空表示禁用唤醒、直接说指令）
        ttk.Label(frm_set, text="唤醒词（语音控制）：").grid(
            row=4, column=0, padx=8, pady=(2, 4), sticky="w")
        self.var_wake_word = tk.StringVar(value=DEFAULT_WAKE_WORD)
        # 输入框与保存按钮放入同一子 Frame 紧贴排列，避免按钮被列权重推远
        frm_wake = ttk.Frame(frm_set)
        frm_wake.grid(row=4, column=1, columnspan=2, padx=(0, 8),
                      pady=(2, 4), sticky="w")
        wake_entry = ttk.Entry(frm_wake, textvariable=self.var_wake_word, width=8)
        wake_entry.pack(side="left")
        self.var_wake_word.trace_add("write", self._on_wake_word_changed)
        wake_entry.bind("<FocusOut>", self._on_wake_word_committed)
        # 显式保存按钮：点击即保存唤醒词并弹出修改成功提示（失焦仅静默保存不再弹窗）
        ttk.Button(frm_wake, text="保存", width=6,
                   command=self._on_wake_word_save).pack(side="left", padx=(6, 0))
        # 麦克风设备（语音控制专用）：空=自动优先物理麦克风；__default__=跟随系统默认；数字=指定设备索引。
        # 加固：虚拟声卡（Voicemeeter 等）抢走系统默认输入时，自动降级到物理麦克风，不影响语音控制使用
        ttk.Label(frm_set, text="麦克风设备（语音控制）：").grid(
            row=5, column=0, padx=8, pady=(2, 4), sticky="w")
        self.var_mic_device = tk.StringVar(value="")
        # 常驻列表框：直接嵌入设置页（原弹出式自绘下拉改为 Listbox，取消点击展开）
        # 列表与滚动条放入同一子 Frame 紧贴排列，避免列表被拉伸后滚动条远离列表（视觉"分开"）
        frm_mic = ttk.Frame(frm_set)
        frm_mic.grid(row=5, column=1, columnspan=2, padx=(0, 8),
                     pady=(2, 4), sticky="w")
        self.lst_mic = tk.Listbox(frm_mic, height=5, activestyle="none",
                                  exportselection=False, width=24,
                                  font=("Microsoft YaHei", 9),
                                  bd=1, relief="solid", highlightthickness=0)
        self.lst_mic.pack(side="left", fill="y")
        # 仅当设备数超过可视高度（5）时才显示滚动条，避免项少时右侧留空白
        self._mic_vsb = tk.Scrollbar(frm_mic, orient="vertical",
                                     command=self.lst_mic.yview)
        self.lst_mic.configure(yscrollcommand=self._mic_vsb.set)
        self._mic_vsb.pack(side="left", fill="y")
        self._mic_vsb.pack_forget()
        self.lst_mic.bind("<<ListboxSelect>>", self._on_mic_device_changed)
        self._fill_mic_devices()
        _MicListTooltip(self.lst_mic, _mic_item_desc)
        # 清理后台任务（默认关闭）：界面异常纠正检测到前台为非绑定操作 App 时自动清理（返回键/force-stop）
        self.var_clean_other = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_set, text="清理后台任务（前台为非绑定操作 App 时自动清理，默认关闭）",
                        variable=self.var_clean_other,
                        command=self._on_clean_other_toggle).grid(
            row=6, column=0, columnspan=4, padx=8, pady=(2, 4), sticky="w")
        ttk.Label(frm_set, text="绑定操作 App（包名）：").grid(
            row=7, column=0, padx=8, pady=(2, 4), sticky="w")
        self.var_bind_app = tk.StringVar(value="")
        # 输入框与提示文字放入同一子 Frame 紧贴排列，避免提示被列权重推远
        frm_bind = ttk.Frame(frm_set)
        frm_bind.grid(row=7, column=1, columnspan=2, padx=(0, 8),
                      pady=(2, 4), sticky="w")
        bind_entry = ttk.Entry(frm_bind, textvariable=self.var_bind_app, width=22)
        bind_entry.pack(side="left")
        self.var_bind_app.trace_add("write", self._on_bind_app_changed)
        bind_entry.bind("<FocusOut>", self._on_bind_app_committed)
        ttk.Label(frm_bind, text="可空；配合「清理后台任务」使用，\n前台非此包名且非刷视频目标时自动清理",
                  foreground="#888", justify="left").pack(side="left", padx=(8, 0))
        frm_set.columnconfigure(1, weight=1)

        # 控制区（常驻，日志上方）
        frm_ctl = ttk.Frame(self.root)
        frm_ctl.pack(fill="x", padx=10, pady=(0, 4))
        self.btn_start = ttk.Button(frm_ctl, text="▶ 开始刷", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 6), ipadx=16, ipady=5)
        # 焦点不在配对码文本框时，按回车 = 点「开始刷」
        self.root.bind("<Return>", self._on_global_enter)
        self.btn_stop = ttk.Button(frm_ctl, text="■ 全部停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6, ipadx=16, ipady=5)
        self.lbl_total = ttk.Label(frm_ctl, text="总计: 0 个视频", font=("Microsoft YaHei", 10))
        self.lbl_total.pack(side="right", padx=10)
        ttk.Button(frm_ctl, text="关于", width=6,
                   command=self.show_about).pack(side="right", padx=4)
        ttk.Button(frm_ctl, text="导出日志", width=9,
                   command=self._export_log).pack(side="right", padx=4)
        ttk.Button(frm_ctl, text="检查更新", width=9,
                   command=lambda: self.check_update(manual=True)).pack(side="right", padx=4)
        ttk.Button(frm_ctl, text="📢 公告", width=8,
                   command=lambda: self.load_notice(manual=True)).pack(side="right", padx=4)
        ttk.Button(frm_ctl, text="❤ 赞助", width=7,
                   command=self.show_sponsor).pack(side="right", padx=4)

        # 状态栏
        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.var_status, relief="groove",
                  anchor="w", padding=5).pack(fill="x", padx=10)

        # 日志区（占满剩余空间，窗口拉大日志跟着变大）
        frm_log = ttk.LabelFrame(self.root, text="日志")
        frm_log.pack(fill="both", expand=True, padx=10, pady=(4, 8))
        self.txt_log = tk.Text(frm_log, height=10, font=("Consolas", 9),
                               bg="#1e1e1e", fg="#d4d4d4", state="disabled", wrap="word")
        log_vsb = ttk.Scrollbar(frm_log, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

        # Notebook 高度跟随当前标签页内容，避免内容少的页出现大块空白
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.root.after(60, self._on_tab_changed)

    def _on_global_enter(self, _e=None):
        """焦点不在配对码文本框时，按回车 = 点「开始刷」"""
        w = self.root.focus_get()
        if w is self.pair_text:
            return
        self.start()
        return "break"

    def _on_pair_text_newline(self, _e=None):
        """Ctrl+Enter 在配对码文本框换行"""
        self.pair_text.insert("insert", "\n")
        return "break"

    def _on_pair_text_enter(self, _e=None):
        """光标在配对码文本框按回车 = 点批量配对按钮"""
        self.batch_pair_device()
        return "break"

    def _on_tab_changed(self, _e=None):
        """切换标签页时按当前页内容自然高度调整 Notebook 高度"""
        try:
            cur = self.nb.nametowidget(self.nb.select())
            cur.update_idletasks()
            max_bottom = 0
            for child in cur.winfo_children():
                max_bottom = max(max_bottom, child.winfo_y() + child.winfo_reqheight())
            self.nb.configure(height=max_bottom + 14)
        except Exception:
            pass

    def _on_list_wheel(self, event):
        """指针在设备列表上时才滚动列表，避免影响日志区"""
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        node = w
        while node is not None:
            if node is self.can_list or node is self.frm_list:
                self.can_list.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"
            node = getattr(node, "master", None)

    def show_about(self):
        info = (
            "爽哥刷金币助手 " + APP_VERSION + "\n\n"
            "编程语言：Python 3.14 (tkinter + OpenCV)\n"
            "出品：杨爽工作室 × 爽仔（AI 助手）\n\n"
            "功能：多设备刷视频 / 误触自动恢复 /\n"
            "签到·宝箱·图像识别·广告自动关闭\n\n"
            "详细用法见《爽哥刷金币助手使用说明.md》"
        )
        messagebox.showinfo("关于", info)

    @staticmethod
    def _ver_tuple(v):
        """'v2.8' / '2.8.1' -> (2, 8) / (2, 8, 1)，便于比较"""
        nums = re.findall(r"\d+", v)
        return tuple(int(n) for n in nums) if nums else (0,)

    def check_update(self, manual=False):
        """后台请求更新地址（内置常量 APP_UPDATE_URL），内容格式: 版本号 或 版本号|下载链接"""
        url = (self.update_url or "").strip()
        # 空值或不是 http(s) 链接（比如随手输入的测试文字）都直接跳过，不去请求避免报错
        if not url or not url.lower().startswith(("http://", "https://")):
            if manual:
                if url:
                    self.log("ℹ️ 更新地址「%s」不是 http(s) 链接，已跳过检查" % url)
                else:
                    self.log("ℹ️ 更新检查已停用，跳过")
            return

        def work():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ShuangGeHelper"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    text = resp.read().decode("utf-8", errors="replace").strip()
                first = text.splitlines()[0].strip() if text else ""
                # 文件名/后缀/内容写法不限：第一行里自动识别版本号(V1.2.3.4)和下载链接(https://…)
                no_link = re.sub(r"https?://\S+", "", first)   # 先摘掉链接，避免把网址里的数字当成版本号
                mver = re.search(r"[Vv]?\d+(?:\.\d+){1,3}", no_link)
                remote_ver = mver.group(0) if mver else ""
                mlink = re.search(r"https?://[^\s\"'<>，。；、（）【】《》""？！]+", first)
                link = mlink.group(0).rstrip("，。；、）)】]\"'？?！!.,;") if mlink else ""
                if not remote_ver:
                    self.log("⚠️ 更新文件第一行没识别到版本号"
                             "（写法示例: V1.0.0.9 或 最新版 V1.0.0.9 下载 https://…）")
                    return
                remote_t = self._ver_tuple(remote_ver)
                local_t = self._ver_tuple(APP_VERSION)
                if remote_t > local_t:
                    msg = ("发现新版本 %s（当前 %s）" % (remote_ver, APP_VERSION) +
                           ("\n下载链接: " + link if link else "") +
                           "\n\n现在打开下载链接吗？")
                    self.log("🆕 发现新版本 %s，当前 %s" % (remote_ver, APP_VERSION))
                    self.root.after(0, lambda: messagebox.askyesno("发现新版本", msg)
                                    and link and webbrowser.open(link))
                elif manual:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "检查更新", "已是最新版本 %s ✅" % APP_VERSION))
                    self.log("检查更新: 已是最新版本 %s" % APP_VERSION)
            except Exception as e:
                if manual:
                    self.root.after(0, lambda: messagebox.showerror(
                        "检查更新", "检查失败，请确认更新地址可访问。\n\n" + str(e)))
                else:
                    self.log("启动检查更新未完成: " + str(e))

        threading.Thread(target=work, daemon=True).start()

    def load_notice(self, manual=False):
        """后台读取公告地址（内置常量 APP_NOTICE_URL）的纯文本，与检查更新同一套做法。
        启动时自动执行：内容与上次不同才弹窗（同一份公告不重复打扰，日志每次都会记录）；
        手动点「📢 公告」按钮时无论是否读过都展示。地址为空或不是 http(s) 链接则静默跳过。"""
        url = (self.notice_url or "").strip()
        # 空值或不是 http(s) 链接（比如随手输入的测试文字）都直接跳过，不去请求避免报错
        if not url or not url.lower().startswith(("http://", "https://")):
            if manual:
                if url:
                    self.log("ℹ️ 公告地址「%s」不是 http(s) 链接，已跳过" % url)
                else:
                    self.log("ℹ️ 公告已停用，跳过")
            return

        def work():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ShuangGeHelper"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                # 统一换行（Windows 上编辑的公告多为 CRLF，直接塞进 Text 会显示成双倍空行）
                text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
                if not text:
                    self.log("📢 公告内容为空，跳过")
                    return
                digest = hashlib.md5(text.encode("utf-8")).hexdigest()
                if not manual and digest == self.config.get("notice_seen"):
                    self.log("📢 公告无变化（已读），点「📢 公告」可再看")
                    return
                # 记下已读公告，避免同一份公告每次启动都弹窗
                self.config["notice_seen"] = digest
                self._save_config()
                head = text.splitlines()[0].strip()
                self.log("📢 公告: " + (head[:40] + "…" if len(head) > 40 else head))
                self.root.after(0, lambda: self._show_notice(text))
            except Exception as e:
                if manual:
                    self.root.after(0, lambda: messagebox.showerror(
                        "公告", "读取公告失败，请确认地址可访问。\n\n" + str(e)))
                else:
                    self.log("启动加载公告未完成: " + str(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_notice(self, text):
        """公告窗口：纯文本、可滚动，长公告也能完整读完"""
        try:
            win = tk.Toplevel(self.root)
            win.title("📢 公告")
            win.transient(self.root)
            frm = ttk.Frame(win, padding=8)
            frm.pack(fill="both", expand=True)
            body = tk.Text(frm, width=56, height=15, wrap="word",
                           font=("Microsoft YaHei", 10), padx=8, pady=6)
            vsb = ttk.Scrollbar(frm, orient="vertical", command=body.yview)
            body.configure(yscrollcommand=vsb.set)
            body.insert("1.0", text)
            body.configure(state="disabled")
            body.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            frm.rowconfigure(0, weight=1)
            frm.columnconfigure(0, weight=1)
            ttk.Button(win, text="我知道了", width=12,
                       command=win.destroy).pack(pady=(0, 10))
            win.update_idletasks()
            # 居中显示在主窗口三分之一高度处
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_height()) // 3)
            win.geometry("+%d+%d" % (x, y))
            win.lift()
            win.focus_force()
        except Exception:
            # 兜底：窗口创建失败时用系统弹窗展示
            messagebox.showinfo("公告", text)

    def show_sponsor(self):
        """赞助窗口：左侧二维码图片（地址已写死在代码 APP_SPONSOR_QR），右侧在线 md 名单（网名/赞助金额/赞助时间）。
        名单地址同样写死在内置常量 APP_SPONSOR_MD，不再从配置文件读取。"""
        try:
            win = tk.Toplevel(self.root)
            win.title("❤ 赞助支持 —— " + APP_VERSION)
            win.transient(self.root)
            outer = ttk.Frame(win, padding=10)
            outer.pack(fill="both", expand=True)
            outer.columnconfigure(1, weight=1)
            outer.rowconfigure(0, weight=1)

            # ---- 左：二维码 ----
            left = ttk.LabelFrame(outer, text="扫码赞助")
            left.grid(row=0, column=0, sticky="n", padx=(0, 10))
            self._qr_photo = None      # 保持引用，否则图片会被回收显示空白
            lbl_qr = ttk.Label(left, text="二维码加载中…", anchor="center",
                               justify="center", foreground="#888", wraplength=230)
            lbl_qr.pack(padx=16, pady=(12, 8))
            ttk.Label(left, text="扫码后请备注你的网名\n以便记入右侧名单",
                      justify="center", foreground="#777").pack(padx=12, pady=(0, 12))
            # 赞助声明（二维码下方）
            ttk.Separator(left).pack(fill="x", padx=12, pady=(0, 8))
            ttk.Label(left, text="赞助支持", foreground="#444",
                      font=("Microsoft YaHei UI", 10, "bold")).pack(padx=12)
            ttk.Label(left, text="一元不嫌少，十元不嫌多。\n"
                                 "无论金额多少，都是对本软件项目的认可。\n"
                                 "赞助属于自愿行为，不购买任何功能、不享有售后服务，"
                                 "仅表达您的一份善意。",
                      justify="center", foreground="#888",
                      wraplength=236).pack(padx=12, pady=(4, 12))

            # ---- 右：在线名单 ----
            right = ttk.LabelFrame(outer, text="赞助名单（在线读取）")
            right.grid(row=0, column=1, sticky="nsew")
            self.var_sp_stat = tk.StringVar(value="正在读取名单…")
            ttk.Label(right, textvariable=self.var_sp_stat,
                      foreground="#555").pack(anchor="w", padx=8, pady=(6, 4))
            body = ttk.Frame(right)
            body.pack(fill="both", expand=True, padx=(8, 6), pady=(0, 8))
            cols = ("no", "name", "amount", "time")
            tree = ttk.Treeview(body, columns=cols, show="headings", height=12)
            for c, title, w, anchor in (("no", "序号", 46, "center"),
                                        ("name", "网名", 170, "w"),
                                        ("amount", "赞助金额", 92, "center"),
                                        ("time", "赞助时间", 112, "center")):
                tree.heading(c, text=title)
                tree.column(c, width=w, anchor=anchor)
            vsb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")
            self._sp_tree = tree

            ttk.Button(win, text="关闭", width=12, command=win.destroy).pack(pady=(0, 10))
            win.update_idletasks()
            # 居中显示在主窗口三分之一高度处（与公告窗口一致）
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_height()) // 3)
            win.geometry("+%d+%d" % (x, y))
            win.lift()

            self._load_sponsor_qr(lbl_qr)
            self._load_sponsor_list()
        except Exception as e:
            messagebox.showerror("赞助", "打开赞助窗口失败：" + str(e))

    def _png_b64_resized(data, size=220):
        """下载到的图片字节 → 居中的白底正方形 PNG 的 base64 字符串。
        有 OpenCV 时缩放并合成白底，没有则原样交给 Tk 解码（Tk 8.6 自带 PNG 支持）。"""
        raw_b64 = base64.b64encode(data).decode("ascii")
        if not CV_OK:
            return raw_b64
        try:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None:
                return raw_b64
            h, w = img.shape[:2]
            scale = min(size / float(w), size / float(h))
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            elif img.shape[2] == 4:
                # 透明 PNG 在 Tk 里会显示成黑块，先合成到白底上
                alpha = img[:, :, 3:4].astype(np.float32) / 255.0
                img = (img[:, :, :3].astype(np.float32) * alpha
                       + 255.0 * (1.0 - alpha)).astype(np.uint8)
            canvas = np.full((size, size, 3), 255, np.uint8)
            h, w = img.shape[:2]
            oy, ox = (size - h) // 2, (size - w) // 2
            canvas[oy:oy + h, ox:ox + w] = img
            ok, buf = cv2.imencode(".png", canvas)
            return base64.b64encode(buf.tobytes()).decode("ascii") if ok else raw_b64
        except Exception:
            return raw_b64

    def _load_sponsor_qr(self, label):
        """优先使用内嵌 base64 二维码（APP_SPONSOR_QR_B64，写死内嵌、不依赖远程）；
        内嵌为空或解码失败时才回退到远程 URL 下载。"""
        b64 = (APP_SPONSOR_QR_B64 or "").strip()
        if b64:
            try:
                raw = base64.b64decode(b64, validate=True)
                if not raw:
                    raise ValueError("内嵌二维码数据为空")
                self._show_qr(label, b64)
                self.log("❤ 赞助二维码已加载（内嵌）")
                return
            except Exception as e:
                self.log("⚠️ 内嵌二维码解码失败，回退远程: " + str(e))

        url = (self.sponsor_qr or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            label.configure(text="二维码地址无效", foreground="#999")
            return

        def work():
            b64, err = None, ""
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ShuangGeHelper"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = resp.read()
                if not data:
                    raise ValueError("图片内容为空")
                b64 = self._png_b64_resized(data)
            except Exception as e:
                err = str(e)
            if b64:
                self.root.after(0, lambda: self._show_qr(label, b64))
                self.log("❤ 赞助二维码已加载（%s）" % url)
            else:
                self.log("⚠️ 赞助二维码加载失败: " + err)
                self.root.after(0, lambda: label.configure(
                    text="二维码加载失败\n%s\n\n%s" % (url, err), foreground="#c66"))

        threading.Thread(target=work, daemon=True).start()

    def _show_qr(self, label, b64):
        try:
            photo = tk.PhotoImage(data=b64)   # Tk 8.6 可直接解码 base64 PNG
            self._qr_photo = photo            # 必须留引用，否则图片被回收后显示空白
            label.configure(image=photo, text="")
            label.image = photo
        except Exception as e:
            label.configure(text="二维码显示失败\n" + str(e), foreground="#c66")

    def _load_sponsor_list(self):
        """后台读取在线名单（md/txt，内置常量 APP_SPONSOR_MD），回到主线程填表"""
        url = (self.sponsor_md or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            self.var_sp_stat.set("名单已停用\n（内置地址缺失或不可用）")
            return

        def work():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ShuangGeHelper"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                rows = parse_sponsor_rows(text)
                self.log("❤ 赞助名单已读取：%d 条记录（%s）" % (len(rows), url))
                self.root.after(0, lambda: self._fill_sponsor_tree(rows))
            except Exception as e:
                self.log("⚠️ 赞助名单读取失败: " + str(e))
                self.root.after(0, lambda: self.var_sp_stat.set(
                    "名单读取失败：%s" % str(e)[:70]))

        threading.Thread(target=work, daemon=True).start()

    def _fill_sponsor_tree(self, rows):
        """把名单填进表格：前三名带奖牌，底部统计人数/累计金额/最近时间"""
        tree = getattr(self, "_sp_tree", None)
        try:
            if tree is None or not tree.winfo_exists():
                return
        except Exception:
            return
        tree.delete(*tree.get_children())
        if not rows:
            self.var_sp_stat.set("名单还是空的 —— 期待你的名字出现在这里 ❤")
            return
        medals = ("🥇 ", "🥈 ", "🥉 ")
        total, has_amt = 0.0, False
        for i, r in enumerate(rows, 1):
            tree.insert("", "end", values=(
                i,
                (medals[i - 1] if i <= 3 else "") + r["name"],
                r["amount"],
                r["time"] or "—"))
            m = re.search(r"\d+(?:\.\d+)?", r["amount"])
            if m:
                total += float(m.group(0))
                has_amt = True
        money = ("¥" + (("%.2f" % total).rstrip("0").rstrip(".") or "0")) if has_amt else "—"
        self.var_sp_stat.set("共 %d 位赞助者　·　累计 %s　·　最近 %s"
                             % (len(rows), money, rows[0]["time"] or "—"))
