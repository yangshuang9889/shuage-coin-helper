# -*- coding: utf-8 -*-
"""语音控制（vosk 离线识别/麦克风/唤醒词）配置检查与监听线程。"""

import json
import os
import threading
import time
from tkinter import messagebox

try:
    import vosk
    import sounddevice as sd
    VOICE_IMPORTS_OK = True
except Exception:
    VOICE_IMPORTS_OK = False

VOICE_VIRTUAL_MARKERS = (
    "voicemeeter", "cable", "vb-audio", "virtual", "虚拟", "混音", "wave out",
)

VOICE_COMMANDS = (
    ("开始刷", "start"),
    ("开刷", "start"),
    ("开始", "start"),
    ("全部停止", "stop"),
    ("停止", "stop"),
    ("停", "stop"),
)

VOICE_MIN_RAM_MB = 4096      # 内存下限：4GB

VOICE_MIN_CPU_CORES = 4      # 逻辑核心数下限：4 核

DEFAULT_WAKE_WORD = "小金币"

VOICE_WAKE_TIMEOUT_SECONDS = 12   # 唤醒态超时：10-15 秒无命令自动退回待唤醒

VOICE_HALF_WAKE_TIMEOUT_SECONDS = 10   # 半唤醒态超时：8-12 秒内没说第二次唤醒词，自动退回待唤醒

def check_voice_config():
    """检查当前电脑配置是否满足语音识别（vosk）最低要求。

    用标准库获取：os.cpu_count() 取逻辑核心数；ctypes 调 Windows API
    GlobalMemoryStatusEx 取物理内存。任一信息获取失败时保守判定为不达标。

    返回 (ok: bool, reason: str)；ok=True 时 reason 为空串。
    """
    try:
        cores = os.cpu_count() or 0
    except Exception:
        cores = 0
    ram_mb = 0
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            ram_mb = int(stat.ullTotalPhys // (1024 * 1024))
    except Exception:
        ram_mb = 0
    ok = (ram_mb >= VOICE_MIN_RAM_MB and cores >= VOICE_MIN_CPU_CORES)
    if ok:
        return True, ""
    if ram_mb <= 0 or cores <= 0:
        return False, "无法获取系统配置（内存/CPU），语音识别功能已禁用"
    return False, ("当前内存 %.1f GB / CPU %d 核，不满足语音识别最低要求（%.1fGB / %d核）" %
                   (ram_mb / 1024.0, cores, VOICE_MIN_RAM_MB / 1024.0, VOICE_MIN_CPU_CORES))

class VoiceConfigMixin:
    def _voice_model_path(self):
        """查找外置中文语音模型目录：程序目录下 vosk-model-small-cn-0.22 或 models\子目录"""
        for cand in (os.path.join(self.data_dir, "vosk-model-small-cn-0.22"),
                     os.path.join(self.data_dir, "models", "vosk-model-small-cn-0.22")):
            if os.path.isdir(cand):
                return cand
        return None

    def _start_voice_listener(self, show_warning=True, welcome_text="欢迎使用爽哥刷金币助手语音控制功能"):
        """开启后台语音监听线程（daemon）。依赖缺失/模型缺失/配置不达标时提示并自动关闭开关，不崩溃
        welcome_text：本次开启后延迟播报的欢迎语文案；手动勾选默认长版，重启/自动恢复传短版"""
        if self.voice_thread is not None and self.voice_thread.is_alive():
            return
        # 双保险：即使开关被强行打开，启动前也校验电脑配置，不达标则不启动
        ok, reason = check_voice_config()
        if not ok:
            self.var_voice_control.set(False)
            self._save_ui_state()
            self.log("语音控制：电脑配置不满足要求，未启动监听（%s）" % reason)
            if show_warning:
                messagebox.showwarning("语音控制", reason)
            return
        if not VOICE_IMPORTS_OK:
            self.var_voice_control.set(False)
            self._save_ui_state()
            self.log("语音控制：缺少 vosk/sounddevice 组件，已自动关闭（可 pip install vosk sounddevice 后重试）")
            if show_warning:
                messagebox.showwarning("语音控制",
                                       "语音识别组件未安装（需要 vosk + sounddevice）。\n"
                                       "请运行: pip install vosk sounddevice\n"
                                       "或重新打包包含该依赖的版本。")
            return
        model = self._voice_model_path()
        if not model:
            self.var_voice_control.set(False)
            self._save_ui_state()
            self.log("语音控制：未找到中文语音模型 vosk-model-small-cn-0.22，已自动关闭")
            if show_warning:
                messagebox.showwarning(
                    "语音控制",
                    "未找到中文语音模型 vosk-model-small-cn-0.22。\n\n"
                    "请将模型文件夹放到程序目录：\n    %s\n"
                    "或：\n    %s\n\n放置后重新勾选开关即可。" %
                    (os.path.join(self.data_dir, "vosk-model-small-cn-0.22"),
                     os.path.join(self.data_dir, "models", "vosk-model-small-cn-0.22")))
            return
        self.voice_event = threading.Event()
        self.voice_last_action = 0.0
        self.voice_awake = False          # 两段式：当前是否处于唤醒态（已说唤醒词，等待指令）
        self.voice_awake_until = 0.0      # 唤醒态截止时间，超时自动退回待唤醒
        self.voice_half_awake = False     # 半唤醒态：已听到第一次唤醒词，等待第二次确认
        self.voice_half_awake_until = 0.0 # 半唤醒态截止时间，超时自动退回待唤醒
        self.voice_thread = threading.Thread(target=self._voice_loop,
                                             args=(model,), daemon=True)
        self.voice_thread.start()
        self.voice_no_wake_hinted = False   # 未设置唤醒词的一次性提示标志
        wake = (self.var_wake_word.get() or "").strip()
        if wake:
            self.set_status("语音待唤醒（说「%s」）" % wake)
            self.log("语音控制已开启：说「%s」唤醒后，再说「开始刷/开刷/开始」开始，「停止/停/全部停止」停止（离线识别）" % wake)
        else:
            self.set_status("未设置唤醒词，语音控制不响应命令")
            self.log("语音控制已开启：未设置唤醒词，语音控制不响应命令（请在设置中填写唤醒词）")
        # 开启后延迟 2 秒语音播报欢迎语（勾选语音控制即触发）
        self._voice_welcome_text = welcome_text
        self.root.after(2000, self._voice_welcome_announce)

    def _voice_welcome_announce(self):
        """语音控制开启 2 秒后播报欢迎语：仅当开关仍开启且监听线程存活时播报，避免刚开就关还误播"""
        try:
            if not self.var_voice_control.get():
                return
            if self.voice_thread is None or not self.voice_thread.is_alive():
                return
            text = getattr(self, "_voice_welcome_text", "欢迎使用爽哥刷金币助手语音控制功能")
            self._speak_many([text])
        except Exception:
            pass

    def _stop_voice_listener(self):
        """停止语音监听线程（可随时退出，不阻塞主线程）"""
        if self.voice_thread is None:
            return
        self.voice_event.set()
        self.voice_thread.join(timeout=3)
        self.voice_thread = None
        self.voice_awake = False
        self.voice_awake_until = 0.0
        self.voice_half_awake = False
        self.voice_half_awake_until = 0.0
        self.set_status("就绪")
        self.log("语音控制已关闭")

    def _on_voice_toggle(self):
        """语音控制开关变化：开启即启动监听线程，关闭即停止并保存"""
        try:
            self._save_ui_state()
        except Exception:
            pass
        if self.var_voice_control.get():
            self._start_voice_listener(show_warning=True)
        else:
            self._stop_voice_listener()

    def _on_wake_word_changed(self, *args):
        """唤醒词文本框内容变化即保存到配置 ui 键 var_wake_word；清空则语音控制不响应任何命令"""
        try:
            self._save_ui_state()
        except Exception:
            pass

    def _on_wake_word_committed(self, *args):
        """唤醒词输入框失焦时仅静默保存（修改成功提示交由显式保存按钮触发）"""
        try:
            self._save_ui_state()
        except Exception:
            pass

    def _on_wake_word_save(self):
        """点击「保存」按钮：保存唤醒词并弹出修改成功提示"""
        try:
            self._save_ui_state()
        except Exception:
            pass
        try:
            messagebox.showinfo("提示", "唤醒词修改成功")
        except Exception:
            pass

    def _fill_mic_devices(self):
        """填充麦克风设备列表：自动（物理优先）/ 跟随系统默认 / 具体输入设备（虚拟声卡标注）。
        常驻 Listbox（self.lst_mic）直接展示全部选项；opts 与 vals 一一对应。"""
        opts = ["自动（物理麦克风优先）", "跟随系统默认设备"]
        vals = ["", "__default__"]
        if VOICE_IMPORTS_OK:
            try:
                for i, info in enumerate(sd.query_devices()):
                    if info.get("max_input_channels", 0) <= 0:
                        continue
                    if not self._is_blocking_input(info):
                        continue
                    name = info.get("name") or ""
                    is_virtual = any(k.lower() in name.lower() for k in VOICE_VIRTUAL_MARKERS)
                    label = "%d - %s%s" % (i, name, "（虚拟声卡）" if is_virtual else "")
                    opts.append(label)
                    vals.append(str(i))
            except Exception:
                pass
        lb = getattr(self, "lst_mic", None)
        if lb is None:
            return
        try:
            lb.delete(0, "end")
            for o in opts:
                lb.insert("end", o)
            self._mic_opts = opts
            self._mic_vals = vals
            cur = self.var_mic_device.get()
            idx = vals.index(cur) if cur in vals else 0
            lb.selection_clear(0, "end")
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)
        except Exception:
            pass
        vsb = getattr(self, "_mic_vsb", None)
        if vsb is not None:
            try:
                if len(opts) > 5:
                    vsb.pack(side="left", fill="y")
                else:
                    vsb.pack_forget()
            except Exception:
                pass

    def _on_mic_device_changed(self, *args):
        """麦克风设备切换：同步选中项到配置变量；保存配置；若监听运行中则重启监听以应用新设备"""
        lb = getattr(self, "lst_mic", None)
        if lb is not None:
            try:
                sel = lb.curselection()
                if sel:
                    idx = sel[0]
                    vals = getattr(self, "_mic_vals", None)
                    if vals is not None and 0 <= idx < len(vals):
                        self.var_mic_device.set(vals[idx])
            except Exception:
                pass
        try:
            self._save_ui_state()
        except Exception:
            pass
        if self.voice_thread is not None and self.voice_thread.is_alive():
            self.log("麦克风设备已切换，重启语音监听以生效")
            self._stop_voice_listener()
            if self.var_voice_control.get():
                self._start_voice_listener(show_warning=False, welcome_text="欢迎使用爽哥刷金币助手")

    def _is_blocking_input(self, info):
        """判断输入设备是否支持阻塞式 read：Windows WDM-KS hostapi 不支持 blocking API，过滤掉；
        其余（MME/WASAPI 等）保留，避免降级候选里混入必失败的设备"""
        try:
            ha = sd.query_hostapis(info.get("hostapi", 0))
            ha_name = ha.get("name", "") if isinstance(ha, dict) else str(ha)
            if "WDM-KS" in ha_name:
                return False
        except Exception:
            pass
        return True

    def _scan_physical_mics(self):
        """扫描物理输入设备（排除 Voicemeeter/CABLE/VB-Audio 等虚拟声卡），按真实麦克风优先排序，返回 [(index, name), ...]"""
        mics = []
        if not VOICE_IMPORTS_OK:
            return mics
        try:
            for i, info in enumerate(sd.query_devices()):
                if info.get("max_input_channels", 0) <= 0:
                    continue
                name = info.get("name") or ""
                if any(k.lower() in name.lower() for k in VOICE_VIRTUAL_MARKERS):
                    continue
                if not self._is_blocking_input(info):
                    continue
                mics.append((i, name))
        except Exception:
            return mics

        def _phy_score(item):
            n = item[1].lower()
            score = 0
            if "麦克风" in n or "mic" in n:
                score -= 3
            if "realtek" in n or "he" in n or "g1500" in n:
                score -= 2
            if "usb" in n or "usb audio" in n:
                score -= 1
            return score
        mics.sort(key=_phy_score)
        return mics

    def _build_mic_candidates(self):
        """构建麦克风候选列表（按降级顺序）：用户指定设备 > 物理麦克风 > 系统默认兜底(None)。
        保证任何情况下最终都能落到可用设备；虚拟声卡只在用户明确指定时才被使用。
        自动模式物理麦克风优先；「跟随系统默认」模式系统默认优先、物理麦克风降级兜底。"""
        cands = []
        sel = (self.var_mic_device.get() or "").strip()
        if sel == "__default__":
            cands.append((None, "系统默认设备"))
            for idx, name in self._scan_physical_mics():
                cands.append((idx, name))
            return cands
        if sel:
            try:
                idx = int(sel)
                info = sd.query_devices(idx)
                if info.get("max_input_channels", 0) > 0:
                    cands.append((idx, info.get("name") or "指定设备"))
            except Exception:
                pass
        for idx, name in self._scan_physical_mics():
            if not any(c[0] == idx for c in cands):
                cands.append((idx, name))
        if not cands:
            cands.append((None, "系统默认设备"))
        return cands

    def _voice_loop(self, model_path):
        """语音监听线程主体（守护版）：模型加载 + 识别循环；运行期异常自动重启（连续 5 次后停止），模型加载失败直接停止并提示，不静默失效"""
        try:
            vosk.SetLogLevel(-1)
            model = vosk.Model(model_path)
        except Exception as e:
            self._voice_fatal("语音模型加载失败: %s" % e)
            return
        restart = 0
        while not self.voice_event.is_set():
            try:
                self._voice_recognize_loop(model)
                return  # 正常退出（开关关闭或线程被要求停止）
            except Exception as e:
                if self.voice_event.is_set():
                    return
                restart += 1
                if restart >= 5:
                    self._voice_fatal("语音监听连续异常，已停止: %s" % e)
                    return
                self.root.after(0, lambda r=restart, m=str(e): self.log(
                    "语音监听异常，3 秒后自动重启（第 %d 次）: %s" % (r, m)))
                for _ in range(30):
                    if self.voice_event.is_set():
                        return
                    time.sleep(0.1)

    def _voice_recognize_loop(self, model):
        """识别主循环：按候选设备（指定/物理麦克风/系统默认）逐级尝试打开麦克风流 + vosk 识别；
        当前设备打开失败或 stream 连续失败 30 次时自动降级到下一候选（虚拟声卡被排除在外），全部失败才抛异常交由守护重启。
        物理麦克风是自动降级的第一目标，不依赖系统默认设备被谁占用。"""
        rec = vosk.KaldiRecognizer(model, 16000)
        candidates = self._build_mic_candidates()
        first = candidates[0] if candidates else (None, "系统默认设备")
        self.root.after(0, lambda: self.log(
            "语音控制使用麦克风：%s" % ("系统默认设备" if first[0] is None else first[1])))
        last_err = None
        for pos, (dev_idx, dev_name) in enumerate(candidates):
            if self.voice_event.is_set():
                return
            if dev_idx is None:
                stream_kwargs = {}
                show_name = "系统默认设备"
            else:
                stream_kwargs = {"device": dev_idx}
                show_name = dev_name
            try:
                with sd.RawInputStream(samplerate=16000, blocksize=4000,
                                       dtype="int16", channels=1,
                                       **stream_kwargs) as stream:
                    stream_fail = 0
                    while not self.voice_event.is_set():
                        try:
                            data, _ = stream.read(4000)
                            data = bytes(data)  # cffi buffer -> bytes，vosk AcceptWaveform 需要 char* 兼容对象
                            stream_fail = 0
                        except Exception:
                            if self.voice_event.is_set():
                                return
                            stream_fail += 1
                            if stream_fail >= 30:
                                raise RuntimeError("麦克风连续读取失败（可能被占用或设备移除）")
                            continue  # 麦克风暂时无数据/出错，继续监听
                        if rec.AcceptWaveform(data):
                            try:
                                res = json.loads(rec.Result())
                            except Exception:
                                continue
                            text = (res.get("text") or "").strip()
                            if text:
                                self._handle_voice_text(text)
                return  # 当前设备正常退出（开关关闭或线程被要求停止）
            except Exception as e:
                if self.voice_event.is_set():
                    return
                last_err = e
                if pos < len(candidates) - 1:
                    nxt = candidates[pos + 1]
                    nxt_name = "系统默认设备" if nxt[0] is None else nxt[1]
                    self.root.after(0, lambda m=str(e), a=show_name, b=nxt_name: self.log(
                        "麦克风「%s」不可用（%s），自动切换到「%s」" % (a, m, b)))
                    continue  # 降级到下一候选
                # 全部候选失败，交守护重启
                raise RuntimeError("麦克风采集失败（已尝试 %d 个设备）: %s" % (len(candidates), last_err))

    def _voice_fatal(self, msg):
        """语音监听不可恢复失败：记录日志、关闭开关、保存状态（守护线程内调用，需经 root.after 回主线程）"""
        try:
            self.root.after(0, lambda: self.log_error("语音控制已停止: %s" % msg, None))
            self.root.after(0, lambda: self.var_voice_control.set(False))
            self._save_ui_state()
        except Exception:
            pass

    def _handle_voice_text(self, text):
        """两段式语音识别（说两次唤醒词才唤醒，没唤醒绝对不操作）：
        - 待唤醒态：识别文本包含唤醒词（模糊包含匹配）→ 进入半唤醒态，播报「再说一次」并状态栏提示；
        - 半唤醒态：第二次识别文本含唤醒词 → 正式进入唤醒态（播报「我在」）；超时（VOICE_HALF_WAKE_TIMEOUT_SECONDS 秒）自动退回待唤醒；期间识别到命令词一律不执行、不打断等待；
        - 唤醒态：识别到有效命令词即执行并退回待唤醒；再次识别到唤醒词保持唤醒并重置命令窗口；超时（VOICE_WAKE_TIMEOUT_SECONDS 秒无命令）自动回退；
        - 唤醒词为空时语音控制不响应任何命令（提示「未设置唤醒词，语音控制不响应命令」）
        加固：识别文本与唤醒词统一去掉空格再匹配——vosk 中文识别结果字间常带空格（如「小 金币」「开 始」），直接子串匹配会漏判"""
        now = time.time()
        text = "".join((text or "").split())
        wake = "".join(((self.var_wake_word.get() or "").strip()).split())
        if not wake:
            # 未设置唤醒词：语音控制不可用，不响应任何命令（仅提示一次，避免反复刷屏）
            if not self.voice_no_wake_hinted:
                self.voice_no_wake_hinted = True
                self.set_status("未设置唤醒词，语音控制不响应命令")
                self.log("未设置唤醒词，语音控制不响应命令（请在设置中填写唤醒词）")
            return
        if self.voice_half_awake:
            # 半唤醒态：超时则回退；第二次命中唤醒词才正式唤醒
            if now > self.voice_half_awake_until:
                self.voice_half_awake = False
                self.set_status("语音待唤醒（说「%s」）" % wake)
                self.log("🎤 半唤醒超时（%d 秒内未再说一次），回到待唤醒" % VOICE_HALF_WAKE_TIMEOUT_SECONDS)
                return
            if wake in text:
                self.voice_half_awake = False
                self.voice_awake = True
                self.voice_awake_until = now + VOICE_WAKE_TIMEOUT_SECONDS
                self.log("🎤 第二次唤醒词「%s」确认（识别：%s）→ 已唤醒，请说指令" % (wake, text))
                self.set_status("已唤醒，请说指令")
                self._speak_many(["我在，请说"])
            return   # 非唤醒词不打断半唤醒等待
        if self.voice_awake:
            # 唤醒态：先查超时
            if now > self.voice_awake_until:
                self.voice_awake = False
                self.set_status("语音待唤醒（说「%s」）" % wake)
                self.log("🎤 唤醒超时，回到待唤醒")
                return
            if wake in text:
                # 唤醒态下又说唤醒词：保持唤醒态并重置命令窗口，不执行命令（避免误退出/误执行）
                self.voice_awake_until = now + VOICE_WAKE_TIMEOUT_SECONDS
                self.log("🎤 唤醒态下再次听到唤醒词「%s」：保持唤醒，命令窗口已重置" % wake)
                self.set_status("已唤醒，请说指令")
                return
            if self._try_voice_command(text, now):
                self.voice_awake = False
                self.set_status("语音待唤醒（说「%s」）" % wake)
            return
        # 待唤醒态：第一次命中唤醒词 → 半唤醒
        if wake in text:
            self.voice_half_awake = True
            self.voice_half_awake_until = now + VOICE_HALF_WAKE_TIMEOUT_SECONDS
            self.log("🎤 第一次唤醒词「%s」命中（识别：%s）→ 已听到唤醒词，请再说一次" % (wake, text))
            self.set_status("已听到唤醒词，请再说一次")
            self._speak_many(["再说一次"])

    def _try_voice_command(self, text, now):
        """命令词匹配：命令执行入口强制校验——必须处于唤醒态且未超时，否则一律丢弃不执行
        （记日志「未唤醒，忽略命令」，不播报）。命中后经 root.after 回调 start/stop（3 秒去抖防重复触发）。返回是否命中"""
        if not self.voice_awake or now > self.voice_awake_until:
            # 未唤醒（待唤醒/半唤醒）或已超时：没唤醒绝对不操作
            self.log("未唤醒，忽略命令（识别：%s）" % text)
            return False
        if now - self.voice_last_action < 3:
            return False
        for word, action in VOICE_COMMANDS:
            if word in text:
                self.voice_last_action = now
                self.log("🎤 语音识别「%s」→ 执行: %s" % (text, action))
                self.root.after(0, lambda a=action: self._voice_action(a))
                return True
        return False

    def _voice_action(self, action):
        """在主线程执行语音命令对应的动作（start/stop 内部自带状态保护）"""
        if action == "start":
            self.start()
        elif action == "stop":
            self.stop()
