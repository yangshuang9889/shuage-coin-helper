# -*- coding: utf-8 -*-
"""语音播报（Windows SAPI 合成）设备上下线/异常播报。"""

import threading

try:
    import win32com.client
    import pythoncom
    TTS_IMPORTS_OK = True
except Exception:
    TTS_IMPORTS_OK = False

class SpeechMixin:
    def _device_label(self, serial):
        """设备标识：备注名优先；无备注返回 serial；IP 形式（192.168.1.100:5555）截取 IP 部分（去端口），本地 USB serial 原样用"""
        name = (self.device_notes.get(serial) or "").strip()
        if name:
            return name
        s = str(serial)
        if ":" in s:
            head = s.split(":")[0]
            if head.replace(".", "").isdigit():  # 形如 192.168.1.100:5555 → 192.168.1.100
                return head
        return s

    def _speak_online(self, serial):
        """设备接入（上线）播报：有备注播「备注名称上线」，无备注播「新设备上线」（不播 serial/IP）；受语音控制开关控制（含语音提醒播报）"""
        if not self.var_voice_control.get():
            return
        name = (self.device_notes.get(serial) or "").strip()
        if name:
            self._speak_many(["%s上线" % name])
        else:
            self._speak_many(["新设备上线"])

    def _speak_event(self, serial, status_text):
        """集中播报单个设备事件：格式「{设备标识} {状态}」。上线/下线/刷视频异常/连接异常等事件统一走此方法"""
        if not self.var_voice_control.get():
            return
        self._speak_many(["%s %s" % (self._device_label(serial), status_text)])

    def _speak_many(self, texts):
        """后台线程按顺序播报多条文本（Windows SAPI，可选依赖；缺 pywin32 自动跳过不崩溃，不阻塞 UI）"""
        if not texts:
            return
        if not TTS_IMPORTS_OK:
            self.log("语音提示：未安装 pywin32（SAPI 语音合成），已跳过播报")
            return

        def _do():
            try:
                pythoncom.CoInitialize()
                sp = win32com.client.Dispatch("SAPI.SpVoice")
                for t in texts:
                    try:
                        sp.Speak(t)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

        threading.Thread(target=_do, daemon=True).start()
