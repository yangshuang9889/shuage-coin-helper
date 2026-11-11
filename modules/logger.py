# -*- coding: utf-8 -*-
"""日志（内存队列、日志窗口、导出 logs\）与状态栏。"""

import os
import sys
import time
import traceback
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk

from constants import APP_VERSION, LOG_MAX_LINES

def _trace_summary(exc, max_frames=3):
    """把异常对象转成紧凑的 traceback 摘要（最近 max_frames 帧 + 异常类型与消息），方便日志定位。

    不抛异常：任何情况下都返回可读字符串（最少为 str(exc)）。
    """
    try:
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        keep = [l.rstrip("\n") for l in tb_lines
                if not l.startswith("Traceback (most recent call last):")]
        # 每帧占 2 行（位置行 + 代码行），只保留最后 max_frames 帧 + 异常信息行
        tail = keep[-(1 + max_frames * 2):]
        return " | ".join(tail)
    except Exception:
        return str(exc)

class LoggerMixin:
    def _log(self, msg, level="INFO", exc=None):
        """统一日志入口：带完整时间戳与级别（INFO/WARNING/ERROR）；exc 非空时自动附加
        traceback 摘要（最近 3 帧 + 异常类型/消息），方便定位错误。UI 日志区最多保留
        LOG_MAX_LINES 行，超出自动裁剪最旧行（避免内存/UI 卡顿）。"""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        line = "[%s] [%s] %s" % (now, level, msg)
        if exc is not None:
            line += "\n    └ %s" % _trace_summary(exc)

        def _append():
            try:
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end", line + "\n")
                # 行数裁剪：只保留最近 LOG_MAX_LINES 行
                n = int(self.txt_log.index("end-1c").split(".")[0])
                if n > LOG_MAX_LINES:
                    self.txt_log.delete("1.0", "%d.0" % (n - LOG_MAX_LINES))
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
                self._sync_log_window()
            except Exception:
                pass
        self.root.after(0, _append)

    def log(self, msg):
        """常规日志（INFO 级），保持旧调用点不变"""
        self._log(msg, "INFO")

    def log_warning(self, msg, exc=None):
        """警告日志（WARNING 级），可附带异常摘要"""
        self._log(msg, "WARNING", exc)

    def log_error(self, msg, exc=None):
        """错误日志（ERROR 级），可附带异常摘要"""
        self._log(msg, "ERROR", exc)

    def set_status(self, text):
        self.root.after(0, lambda: self.var_status.set(text))

    def _open_log_window(self):
        """打开独立只读日志窗口：展示当前全部日志，可滚动；每 1 秒自动跟随主日志区新增内容"""
        if getattr(self, "_log_win", None) is not None and self._log_win.winfo_exists():
            try:
                self._log_win.lift()
                self._log_win.focus_set()
                return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        win.title("日志 - 爽哥刷金币助手 " + APP_VERSION)
        win.geometry("760x520")
        win.minsize(480, 320)
        self._log_win = win
        self._log_win_auto_scroll = True

        top = ttk.Frame(win)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="实时日志（只读，最多保留最近 %d 行，自动跟随刷新）" % LOG_MAX_LINES,
                  foreground="#666").pack(side="left")
        ttk.Button(top, text="刷新", width=6,
                   command=self._sync_log_window).pack(side="right", padx=4)
        ttk.Button(top, text="导出日志", width=9,
                   command=self._export_log).pack(side="right", padx=4)

        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._txt_log_win = tk.Text(frm, font=("Consolas", 9), wrap="word",
                                    bg="#1e1e1e", fg="#d4d4d4", state="disabled")
        log_vsb = ttk.Scrollbar(frm, orient="vertical", command=self._txt_log_win.yview)
        self._txt_log_win.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side="right", fill="y")
        self._txt_log_win.pack(fill="both", expand=True, padx=4, pady=4)

        bottom = ttk.Frame(win)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Checkbutton(bottom, text="自动跟随滚动到底部",
                        variable=tk.BooleanVar(value=True),
                        command=self._on_log_win_autoscroll_toggle).pack(side="left")
        ttk.Button(bottom, text="关闭", width=8, command=win.destroy).pack(side="right")

        def _close(*_):
            try:
                win.destroy()
            except Exception:
                pass
        win.protocol("WM_DELETE_WINDOW", _close)
        self._sync_log_window()
        self._log_win_after = win.after(1000, self._poll_log_window)

    def _on_log_win_autoscroll_toggle(self):
        self._log_win_auto_scroll = not self._log_win_auto_scroll

    def _poll_log_window(self):
        """日志窗口后台轮询：窗口存在则同步主日志内容并继续定时；不存在则清理句柄"""
        win = getattr(self, "_log_win", None)
        if win is None or not win.winfo_exists():
            self._log_win = None
            return
        self._sync_log_window()
        try:
            self._log_win_after = win.after(1000, self._poll_log_window)
        except Exception:
            self._log_win = None

    def _sync_log_window(self):
        """把主日志区当前内容同步到日志窗口（只读 Text 全量替换，保持自动滚动）"""
        win = getattr(self, "_log_win", None)
        if win is None or not win.winfo_exists():
            return
        txt = getattr(self, "_txt_log_win", None)
        if txt is None:
            return
        try:
            content = self.txt_log.get("1.0", "end-1c")
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", content)
            if self._log_win_auto_scroll:
                txt.see("end")
            txt.configure(state="disabled")
        except Exception:
            pass

    def _export_log(self):
        """导出当前日志到程序目录 logs\\，文件名带时间戳；UTF-8 BOM 编码兼容记事本中文。
        打包运行时取 exe 所在目录（单文件模式 __file__ 指向临时解压目录，不能作为程序目录）。"""
        try:
            if getattr(sys, "frozen", False):
                prog_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                prog_dir = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(prog_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            dt = time.localtime()
            name = "爽哥刷金币助手_%04d%02d%02d_%02d%02d%02d.log" % (
                dt.tm_year, dt.tm_mon, dt.tm_mday, dt.tm_hour, dt.tm_min, dt.tm_sec)
            path = os.path.join(log_dir, name)
            content = self.txt_log.get("1.0", "end-1c")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(content)
            self.log("✅ 日志已导出: %s" % path)
            messagebox.showinfo("导出成功", "日志已导出到：\n%s" % path)
        except Exception as e:
            messagebox.showerror("导出失败", "导出日志失败：\n%s" % e)
