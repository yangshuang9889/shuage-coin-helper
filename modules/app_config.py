# -*- coding: utf-8 -*-
"""配置/坐标持久化、UI 状态保存恢复、模板目录迁移、模板路径命名。"""

import json
import os
import re
import shutil
import tkinter as tk

from constants import APP_NAMES, TPL_COMMON_DIR

def app_dir_name(pkg):
    """模板子目录名：按应用中文名（去掉括号等非法字符）；未知包名用包名；空=通用"""
    if not pkg:
        return TPL_COMMON_DIR
    name = APP_NAMES.get(pkg, pkg)
    name = re.sub(r'[\\/:*?"<>|()（）]', "", name).strip()
    return name or TPL_COMMON_DIR

def readable_tpl_name(name, filename):
    """旧式 tpl_<时间戳>.png → <模板名>_<时间戳>.png，便于人工辨认（已是新名则原样返回）"""
    stem, ext = os.path.splitext(filename)
    m = re.fullmatch(r"tpl_(\d+)", stem)
    if not m:
        return filename
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    if not safe:
        return filename
    return "%s_%s%s" % (safe, m.group(1), ext or ".png")

class ConfigMixin:
    UI_STATE_KEYS = (
        "var_min", "var_max", "var_prob",            # 刷视频参数
        "var_recover", "var_swipe",    # 勾选：误触恢复/上滑开关
        "var_stayon", "var_box", "var_box_min",      # 勾选：防息屏/宝箱/宝箱间隔
        "var_img", "var_img_interval",               # 勾选：图像任务/检查间隔
        "var_device_pause_btn", "var_voice_control", # 勾选：单台暂停按钮/语音控制（含语音提醒播报，默认关）
        "var_ui_correct",                       # 勾选：界面异常自动纠正（默认开，老配置缺键默认 True）
        "var_clean_other",                      # 勾选：清理后台任务（默认关，老配置缺键默认 False）
        "var_bind_app",                         # 文本：绑定操作 App 包名（可空，清理后台任务白名单）
        "var_wake_word",                       # 文本：语音控制唤醒词（默认「小金币」，说两次才唤醒）
        "var_mic_device",                      # 文本：语音控制麦克风设备（空=自动优先物理麦克风；__default__=跟随系统默认；数字=设备索引）
    )

    def _migrate_unified_config(self):
        """统一文件为配置.json：若程序目录存在旧 坐标.json（爽哥刷金币助手坐标.json 或历代旧名，
        可能同时存在旧 配置.json），则读取旧数据合并写入 爽哥刷金币助手配置.json
        （旧配置优先、坐标数据 sign/box/templates 完整并入），写入并校验成功后才删除旧文件；
        校验失败必须保留旧文件并报错，严禁丢数据。配置.json 已存在（含坐标键=已统一）则直接使用。"""
        cfg_path = os.path.join(self.data_dir, "爽哥刷金币助手配置.json")
        coords_candidates = ["爽哥刷金币助手坐标.json",
                             "刷视频工具坐标.json",
                             "爽哥快手抖音任务自动助手坐标.json"]
        cfg_candidates = ["刷视频工具配置.json",
                          "爽哥快手抖音任务自动助手配置.json"]

        # 目标已存在：若已是统一文件（含坐标键）则直接使用，不再迁移
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    exist = json.load(f)
                if isinstance(exist, dict) and any(k in exist for k in ("sign", "box", "templates")):
                    return False
            except Exception:
                pass

        # 定位旧坐标文件（取第一个存在的）
        old_coords = None
        for n in coords_candidates:
            p = os.path.join(self.data_dir, n)
            if os.path.isfile(p):
                old_coords = p
                break
        # 定位旧配置源（历代旧名；现存 cfg_path 若未统一也算旧配置源，旧配置优先：后读覆盖先读）
        old_cfg_sources = []
        for n in cfg_candidates:
            p = os.path.join(self.data_dir, n)
            if os.path.isfile(p):
                old_cfg_sources.append(p)
        if os.path.isfile(cfg_path):
            old_cfg_sources.append(cfg_path)
        if not old_coords and not old_cfg_sources:
            return False   # 无旧数据可迁移

        # 合并数据：旧配置优先（后读覆盖先读），坐标数据完整并入
        data = {}
        for src in old_cfg_sources:
            try:
                with open(src, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for k in ("adb", "ui", "notice_seen"):
                        if k in d:
                            data[k] = d[k]
                    if "qtscrcpy" in d and "scrcpy" not in data:
                        qt_old = d["qtscrcpy"] or ""
                        cand = os.path.join(os.path.dirname(qt_old), "scrcpy.exe") if qt_old else ""
                        data["scrcpy"] = cand if (cand and os.path.isfile(cand)) else ""
            except Exception:
                pass
        if old_coords:
            try:
                with open(old_coords, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for k in ("sign", "box", "templates"):
                        if k in d:
                            data[k] = d[k]
                    # 旧版坐标文件 settings 兜底配置（仅当未从旧配置取到任何配置时）
                    if not data.get("scrcpy") and not data.get("ui") and isinstance(d.get("settings"), dict):
                        for k, v in d["settings"].items():
                            if k in ("adb", "ui", "notice_seen"):
                                data.setdefault(k, v)
                        if "qtscrcpy" in d["settings"] and "scrcpy" not in data:
                            qt_old = d["settings"]["qtscrcpy"] or ""
                            cand = os.path.join(os.path.dirname(qt_old), "scrcpy.exe") if qt_old else ""
                            data["scrcpy"] = cand if (cand and os.path.isfile(cand)) else ""
            except Exception:
                pass
        # 确保程序依赖的顶层键齐全
        for k, default in (("scrcpy", ""), ("adb", ""), ("ui", {}), ("notice_seen", ""),
                           ("sign", {}), ("box", {}), ("templates", {})):
            data.setdefault(k, default)

        # 写入临时文件 → 读回校验 → 原子替换（校验失败保留旧文件并报错）
        tmp_path = cfg_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            with open(tmp_path, "r", encoding="utf-8") as f:
                verify = json.load(f)
            if verify != data:
                raise ValueError("写入校验不一致")
            os.replace(tmp_path, cfg_path)
        except Exception as e:
            self.log("配置统一迁移失败，旧文件已保留（未删除），请勿手动删除：%s" % e)
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return False

        # 写入并校验成功后才删除旧文件（旧坐标文件 + 旧配置旧名文件；cfg_path 本身升级为统一文件不删）
        deleted = []
        for p in (old_coords,) + tuple(old_cfg_sources):
            if p and os.path.isfile(p) and os.path.normcase(os.path.abspath(p)) != os.path.normcase(os.path.abspath(cfg_path)):
                try:
                    os.remove(p)
                    deleted.append(os.path.basename(p))
                except Exception as e:
                    self.log("配置统一迁移：旧文件删除失败（数据已并入配置.json，可手动删除）：%s：%s" % (p, e))
        self._unified_migrated_from = "、".join(deleted) if deleted else "旧文件"
        return True

    def _load_coords(self):
        try:
            if os.path.isfile(self.coords_file):
                with open(self.coords_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.coords["sign"].update(data.get("sign", {}))
                self.coords["box"].update(data.get("box", {}))
                self.coords["templates"].update(data.get("templates", {}))
        except Exception:
            pass

    def _save_coords(self):
        # 坐标与配置统一保存到单一 爽哥刷金币助手配置.json，避免互相覆盖
        self._save_data()

    def _tpl_dir_for(self, pkg):
        """该应用对应的模板子目录，不存在则创建；pkg 为空 → templates/通用/"""
        d = os.path.join(self.tpl_dir, app_dir_name(pkg))
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            return self.tpl_dir

    def _tpl_path(self, meta):
        """解析模板图片的真实路径（兼容旧的绝对路径、散落在 templates 根目录的文件）"""
        rel = (meta or {}).get("file", "") or ""
        base = os.path.basename(rel)
        cands = []
        if rel:
            cands.append(rel if os.path.isabs(rel) else os.path.join(self.data_dir, rel))
        sub = (meta or {}).get("dir", "")
        if base:
            if sub:
                cands.append(os.path.join(self.tpl_dir, sub, base))
            cands.append(os.path.join(self.tpl_dir, base))
        for c in cands:
            if c and os.path.isfile(c):
                return c
        # 兜底：模板被手工挪过位置 → 在 templates 下递归按文件名找
        if base:
            try:
                for root_, _dirs, files in os.walk(self.tpl_dir):
                    if base in files:
                        return os.path.join(root_, base)
            except Exception:
                pass
        return cands[0] if cands else ""

    def _migrate_templates(self):
        """旧版散落在 templates 根目录的模板：按应用归入子目录、文件名带上模板名。
        已有 dir 字段的模板尊重用户自定义分类，不强制按应用覆写（返回移动数量）"""
        moved = 0
        changed = False
        for name, meta in list(self.coords["templates"].items()):
            if not isinstance(meta, dict):
                continue
            old_dir = (meta.get("dir") or "").strip()
            sub = old_dir or app_dir_name(meta.get("pkg", ""))
            if meta.get("dir") != sub:
                meta["dir"] = sub
                changed = True
            src = self._tpl_path(meta)          # 先按旧信息定位文件
            new_base = readable_tpl_name(name, os.path.basename(meta.get("file") or ""))
            target = os.path.join(self.tpl_dir, sub, new_base)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if src and os.path.isfile(src) and not os.path.isfile(target) and \
                        os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(target)):
                    shutil.move(src, target)
                    moved += 1
            except Exception:
                pass
            rel = os.path.relpath(target, self.data_dir).replace("\\", "/")
            if meta.get("file") != rel:
                meta["file"] = rel
                changed = True
        if changed:
            self._save_data()
        return moved

    def _load_config(self):
        """从统一 爽哥刷金币助手配置.json 读取配置键（scrcpy/adb/ui/notice_seen）"""
        try:
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.config.update(data)
        except Exception:
            pass

    def _save_data(self):
        """统一保存：配置键（dict(self.config)）+ 坐标键（sign/box/templates）写入单一 爽哥刷金币助手配置.json"""
        data = dict(self.config)
        data["sign"] = self.coords["sign"]
        data["box"] = self.coords["box"]
        data["templates"] = self.coords["templates"]
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log("保存配置失败: " + str(e))

    def _save_config(self):
        # 远程地址已写死为代码常量，不再作为配置项读写；配置只保存本地路径与界面勾选
        self._save_data()

    def _coords_summary(self):
        sign_n = len(self.coords["sign"])
        box_n = len(self.coords["box"])
        return "已存坐标 → 签到: %d 个应用, 宝箱: %d 个应用" % (sign_n, box_n)

    def _save_ui_state(self):
        """把所有勾选项和参数保存到配置文件"""
        try:
            ui = {}
            for key in self.UI_STATE_KEYS:
                var = getattr(self, key, None)
                if var is not None:
                    ui[key] = var.get()
            self.config["ui"] = ui
            self._save_config()
        except Exception:
            pass

    def _restore_ui_state(self):
        """启动时恢复上次保存的勾选项和参数"""
        ui = self.config.get("ui", {})
        if not isinstance(ui, dict):
            return
        for key in self.UI_STATE_KEYS:
            if key in ui:
                try:
                    var = getattr(self, key)
                    if isinstance(var, tk.BooleanVar):
                        var.set(bool(ui[key]))
                    elif isinstance(var, tk.IntVar):
                        var.set(int(ui[key]))
                    elif isinstance(var, tk.StringVar):
                        var.set(str(ui[key]))
                    else:
                        var.set(float(ui[key]))
                except Exception:
                    pass
        # 麦克风设备列表恢复后重新定位选中项（配置值可能不在枚举中，回退到「自动」）
        try:
            if hasattr(self, "lst_mic"):
                self._fill_mic_devices()
        except Exception:
            pass
        # 老配置兼容：旧版「语音提醒」var_voice_hint 已合并入 var_voice_control，
        # 若旧配置勾选了提醒且语音控制未开启/缺失，则自动开启语音控制并保存，避免提醒功能丢失
        try:
            if ui.get("var_voice_hint") and not self.var_voice_control.get():
                self.var_voice_control.set(True)
                self._save_ui_state()
        except Exception:
            pass

    def _on_close(self):
        """关闭窗口前保存 UI 状态"""
        self.stop()
        self._stop_voice_listener()
        self._save_ui_state()
        self.root.destroy()
