---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: b39d940f30a0c1cf547a23c927f28498_b433634fcff411f19fe7525400de85a5
    ReservedCode1: yhaEMLIx07Dw01DjnLotoTzRHGENRa7Yf7KIadsiY7D/8PKf5wieEmb+z+9S/77HVvfgzdEZhvNIDPyfb6FGA/vyhmmoPa++XKpK0mD01GPymBZnGMxlwoBbe2OgQWjoWojbNe1aUfCjMFX09rpbGXd+7EJ6wl5yEGajnR8EKvgYRWuYnQE/3Nx6xxs=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: b39d940f30a0c1cf547a23c927f28498_b433634fcff411f19fe7525400de85a5
    ReservedCode2: yhaEMLIx07Dw01DjnLotoTzRHGENRa7Yf7KIadsiY7D/8PKf5wieEmb+z+9S/77HVvfgzdEZhvNIDPyfb6FGA/vyhmmoPa++XKpK0mD01GPymBZnGMxlwoBbe2OgQWjoWojbNe1aUfCjMFX09rpbGXd+7EJ6wl5yEGajnR8EKvgYRWuYnQE/3Nx6xxs=
---



# 爽哥刷金币助手 · 项目开发手册（DEVELOPMENT.md）

> **本手册服务于一条铁律：每次修改代码前，先通读一遍本手册，再动手；写完代码后，必须立即更新本手册（改码后必更新手册），保持手册与代码同步。**
> 手册覆盖项目总览、核心架构与数据流、版本管理规范、新增功能清单、删除/废弃清单、修改代码注意事项、打包与发布流程、官网维护、历史版本演进与踩坑记录九大章节。
> 当前基准版本：**V1.0.2.3**（源码/程序内版本）；官网展示版本保持 **V1.0.2.2**（未同步，见「版本管理规范」）。
> 最后更新：2026-10-24。

---

## 目录

> **强规则：写代码前先通读本手册；写完代码后必须立即更新本手册（改码后必更新手册），保持手册与代码同步。**

1. [项目总览](#一项目总览)
2. [核心架构与数据流](#二核心架构与数据流)
3. [版本管理规范（重点）](#三版本管理规范重点)
4. [新增功能清单（增加什么）](#四新增功能清单增加什么)
5. [删除/废弃清单（删除什么）](#五删除废弃清单删除什么)
6. [修改代码注意事项（每次必看）](#六修改代码注意事项每次必看)
7. [打包与发布流程](#七打包与发布流程)
8. [官网维护](#八官网维护)
9. [历史版本演进与踩坑记录](#九历史版本演进与踩坑记录)

---

## 一、项目总览

### 1.1 项目是什么

「爽哥刷金币助手」是一个 Windows 桌面工具：通过 **adb/scrcpy 无线调试** 连接多台 Android 手机，自动上滑刷短视频赚金币。核心能力包括：多设备并发刷视频、误触弹窗自动恢复、签到/宝箱/图像模板任务、广告自动关闭、滑块验证码拖动、任务完成自动停止、离线语音控制与语音播报、界面异常自动纠正等。另有 **Android 原生版**（包名 `com.yscl.autoapp`）供手机端独立挂机。

项目路径：`E:\yscl\shuage-coin-helper`（本手册即存放于项目根目录）。

### 1.2 技术栈

| 组件 | 版本/说明 | 用途 |
|---|---|---|
| Python | 3.12.10（运行时）；编译/打包用 `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` | 主程序语言 |
| GUI | tkinter + ttk（vista 主题） | 主界面、日志窗口、模板编辑等 |
| 图像识别 | OpenCV（cv2）+ numpy，`cv2.matchTemplate` | 屏幕截图匹配模板 |
| 编译 | Cython 3.3.0（`setup.py` 编译 `constants.py`、`ui.py`、`scrcpy.py`、`swipe.py`） | 生成 `cp312-win_amd64.pyd` |
| 打包 | PyInstaller（onedir，`contents_directory='_lib'`）+ Inno Setup 6 | 生成安装包 |
| 设备控制 | adb（`bin\adb.exe`）、scrcpy（`bin\scrcpy.exe`，仅手动投屏） | 无线调试、投屏 |
| 离线语音 | vosk（`models\vosk-model-small-cn-0.22`）+ sounddevice | 唤醒词/命令识别、录音 |
| 语音播报 | SAPI（pywin32 `win32com`），缺 pywin32 时静默跳过 | 事件语音提醒 |
| 远程服务 | 硬编码 URL（检查更新/公告/赞助，域名 `sgzs.杨爽.com`） | 更新检查、公告、赞助名单 |

**关键约束：Cython 编译出的 pyd 是 cp312 平台专用，主程序与 PyInstaller 打包必须使用 Python 3.12 环境，否则 pyd 无法导入。**

### 1.3 目录结构及各目录职责

```
E:\yscl\shuage-coin-helper\
├── 爽哥刷金币助手.py          # 主程序入口（.py 启动，非 frozen 时注入 modules 路径）
├── 爽哥刷金币助手.spec        # PyInstaller 打包配置（onedir/_lib/collect_all/版本/图标）
├── 爽哥刷金币助手使用说明.md    # 用户使用说明（含版本记录，版本号落点之一）
├── 版本信息.txt               # exe 版本元数据（filevers/prodvers/FileVersion/ProductVersion）
├── 版本一致性检查.py           # 辅助脚本：检查版本号落点一致性
├── update.txt                 # 根目录更新数据源（首行：版本号|下载链接）
├── 启动爽哥刷金币助手.bat       # 一键启动 dist\爽哥刷金币助手.exe
├── appicon.ico                # 程序图标（打包用）
├── License.txt / 赞助名单模板.md
├── _modules_src\              # 【源码目录】可读可改的 .py 源文件（改完需重编 pyd）
│   ├── constants.py           # 全局常量：APP_VERSION、远程 URL、阈值、包名表等（Cython 编译）
│   ├── ui.py                  # 界面构建、远程地址解析、更新检查、公告、赞助（Cython 编译）
│   ├── app_config.py          # 配置读写迁移、UI 状态键、关闭钩子
│   ├── devices.py             # 设备检测/自动轮询/USB 自动无线/单台暂停
│   ├── swipe.py               # 刷视频核心（滑动循环/模板/界面纠正/签到/启动停止）
│   ├── voice_config.py        # 语音配置检查、监听线程、唤醒/命令处理
│   ├── speech.py              # 语音播报（SAPI）
│   ├── scrcpy.py              # scrcpy 手动投屏（自动投屏已删）
│   ├── adb_utils.py           # adb 命令封装（无窗口执行、二进制截屏）
│   ├── logger.py              # 日志/状态栏/日志窗口
│   ├── utils.py               # 通用工具（_find_bundled 找 bin 下 exe）
│   ├── setup.py               # Cython 编译入口（["constants.py","ui.py","scrcpy.py","swipe.py"]）
│   └── *.c / *.cp312-win_amd64.pyd  # Cython 中间产物与编译结果
├── modules\                   # 【运行目录】编译后的同名 pyd 集中存放（程序实际加载）
├── installer\                 # 安装包输出：主程序 Setup.exe、语音模型 Setup.exe
├── website\                   # 静态官网（单文件 index.html + md 数据源）
│   ├── index.html             # 官网页面（JS fetch 渲染 md）
│   ├── downloads.md           # 下载菜单数据源
│   ├── changelog.md           # 更新日志数据源
│   ├── sponsors.md            # 赞助名单数据源
│   ├── update.txt             # 官网 update 数据源（首行 版本号|链接）
│   ├── notice.txt             # 启动公告内容（33 字节纯文本）
│   ├── android-update.json    # Android 版独立更新数据源
│   ├── counter.php            # 访问计数
│   ├── dl\                    # 历史安装包副本（主程序 exe 副本保留不删；Android APK 直链）
│   └── 预览官网(读md生效).bat   # 本地预览辅助脚本
├── bin\                       # adb/scrcpy 及其 dll（打包时整体拷入 dist）
│   ├── adb.exe / AdbWinApi.dll / AdbWinUsbApi.dll / libusb-1.0.dll
│   ├── scrcpy.exe / scrcpy-server / scrcpy-noconsole.vbs / scrcpy.png
│   ├── avcodec-63.dll / avformat-61.dll / avutil-61.dll / swresample-7.dll / SDL3.dll
│   ├── disconnected.png
│   └── open_a_terminal_here.bat
├── models\                    # vosk 离线语音模型（vosk-model-small-cn-0.22）
├── templates\                 # 图像模板按应用分包（如 com.tencent.qqlive，另有“通用”）
└── output\ / temp\            # 开发过程中的中间/产出目录（见「修改代码注意事项」）
```

> 根目录若存在 `*.cp312-win_amd64.pyd`（如 `swipe.cp312-win_amd64.pyd`），属历史重编时的额外覆盖产物；运行时以 `modules\` 下同名 pyd 为准，重编流程见第三节。

### 1.4 启动方式

1. **源码运行（开发调试）**：直接用 Python 3.12 运行 `爽哥刷金币助手.py`（`pythonw` 可无黑窗）。入口在非 frozen 环境会把 `modules` 插入 `sys.path`，保证优先加载编译后的 pyd。
2. **打包运行（发布验证）**：双击根目录 `启动爽哥刷金币助手.bat`（内部 `start "" "%~dp0dist\爽哥刷金币助手.exe"`），或直接运行 `dist\爽哥刷金币助手\爽哥刷金币助手.exe`。
3. **安装包运行**：运行 `installer\` 下 Inno Setup 生成的 Setup.exe 安装后启动。

### 1.5 硬件与运行环境

- **Windows 11**（本项目开发环境 Build 26200），需可运行 Python 3.12 与 tkinter。
- **Android 手机**：开启 USB 调试；USB 插入可自动开启 5555 无线端口并自动 `adb connect`（见设备管理）。
- **语音控制硬件要求**：`check_voice_config` 校验 **内存 ≥ 4GB 且 CPU 核数 ≥ 4**，不达标时设置页语音开关置灰并显示原因。
- **模型文件**：`models\vosk-model-small-cn-0.22` 必须随程序分发，缺失时语音控制不可用。
- **adb/scrcpy**：`bin\` 目录随包分发；`utils._find_bundled` 优先找 `bin\*.exe`，找不到再退回系统 PATH。

---

## 二、核心架构与数据流

### 2.1 主程序入口（爽哥刷金币助手.py）

- 文件仅约 160 行，定义主类 **`VideoSwiperApp`**，通过多 Mixin 继承组装全部能力：
  `LoggerMixin / ConfigMixin / DevicesMixin / SwipeMixin / VoiceConfigMixin / SpeechMixin / ScrcpyMixin / UiMixin`。
- `__init__` 依次完成：
  1. 初始化状态字典：`pause_events / workers / target_apps / task_done_flags / screen_sizes / device_widgets` 等；
  2. 统一配置迁移（`_migrate_unified_config`：旧 `坐标.json + 配置.json` → `爽哥刷金币助手配置.json`，校验成功后删除旧文件）；
  3. 模板按应用分目录迁移（V1.0.0.6 逻辑，旧模板自动归档改名）；
  4. 恢复 UI 状态（勾选/开关/唤醒词/麦克风等，`UI_STATE_KEYS`）；
  5. 自动恢复语音监听（若上次勾选了语音控制）；
  6. 启动检查更新（后台）、加载公告（有变化才弹窗）、自动检测设备。
- 窗口标题拼接 `APP_VERSION`；`main()` 中尝试启用 `ttk.Style().theme_use("vista")` 后进入 `root.mainloop()`。

### 2.2 modules 下各 pyd 模块职责（源码在 _modules_src，同名）

| 模块 | 职责要点 |
|---|---|
| `constants` | `APP_VERSION`、`APP_UPDATE_URL/APP_NOTICE_URL/APP_SPONSOR_QR/APP_SPONSOR_MD` 远程地址、`MATCH_THRESHOLD=0.80`、`APP_NAMES`（快手/抖音等包名）、`LAUNCHER_PKGS`、`TPL_COMMON_DIR="通用"`、`UI_CHECK_INTERVAL/UI_CORRECT_MAX_FAIL/UI_FORCE_RESTART_LIMIT/UI_FREEZE_MAX_FAIL/UI_RECOVER_MAX_ATTEMPTS/SWIPE_FAIL_SPEAK_THRESHOLD`、base64 图标 |
| `ui` | `_build_ui` 界面构建、`remote_url` 远程地址解析（off 停用）、`parse_sponsor_rows`、`check_update`（读 update.txt 首行）、`load_notice`（公告哈希去重）、`_show_notice` 可滚动窗口、`_MicListTooltip` 麦克风悬停气泡 |
| `app_config` | `UI_STATE_KEYS`（全部设置开关持久化键）、`_migrate_unified_config` 配置迁移、坐标/模板读写、`_on_close`（退出清理） |
| `devices` | `_start_auto_detect`（每 3 秒轮询）、USB 接入自动 `tcpip 5555` + `adb connect IP:5555`、设备行 UI（备注/计数/状态/单台开始/暂停）、`toggle_device_pause`、`connect_device`、`batch_pair_device`（批量配对） |
| `swipe` | 刷视频核心（最大，约 1966 行）：模板定义、图像识别、界面纠正、签到、启动/停止、单设备 worker |
| `voice_config` | 语音开关/唤醒词/麦克风配置、`_start_voice_listener`、监听守护线程、命令匹配 |
| `speech` | `_speak_event` / `_speak_online` / `_speak_many`（SAPI 播报，受 `var_voice_control` 控制） |
| `scrcpy` | `launch_scrcpy`（手动投屏，启动后自动将设备媒体音量静音）、`_mute_device`（adb media volume 静音，失败仅日志不影响投屏）、`_update_start_btn_state`（已解除 scrcpy 缺失禁用限制） |
| `adb_utils` | `run_adb` / `run_adb_binary`（`CREATE_NO_WINDOW` 无黑窗，二进制模式供截屏） |
| `logger` | `_log/log/log_warning/log_error`（traceback 摘要、行数裁剪）、`set_status`、`_open_log_window` 独立日志窗口 |
| `utils` | `_find_bundled`（优先 `bin\*.exe` 再 PATH） |

### 2.3 图像识别链路

```
adb exec-out screencap（run_adb_binary 二进制截屏）
        │
        ▼
cv2 解码 + cv2.matchTemplate（模板图 vs 全屏截图，阈值 MATCH_THRESHOLD=0.80，模板可独立覆盖阈值）
        │
        ▼
_swipe_loop 主循环按检查间隔（默认 15 秒，可调 5~120）调用 _image_tasks
        │
        ▼
_image_tasks：先处理“前置类”kind（task_done / captcha / live / wait_back），
             再处理常规“点击类”kind（task / wait_appear / wait_disappear）
        │
        ▼
_classify_ui_state：按当前界面状态归为 normal / ad / popup / captcha / live / slider / task_done
        │
        ▼
_correct_ui_state：分层纠正（slider 拖动 → captcha 暂停 → live/ad/popup 返回关闭 → home/other_app 拉起目标）
        │
        ▼
_verify_ui_recovered / _ui_correct_give_up / _check_and_correct_ui（卡死/异常重启目标）
```

要点：
- 截图通过 **adb 二进制截屏**（非 PrintWindow），天然规避滚动条/窗口遮挡问题（见踩坑记录）。
- 点击坐标 = 模板匹配中心 + 模板独立点击偏移；命中后有冷却，避免重复点击。
- `task_done` 识别到后：**停止该台设备**刷金币并语音播报「任务完成」，多设备互不影响（`task_done_flags` 按 serial 隔离）。
- `captcha` 识别到后：**暂停该设备**并提醒人工处理。
- `slider_gap + slider_btn`：滑块验证码，先找缺口再拖拖柄（拖动路径在 `_correct_ui_state`）。
- `live`：命中直播间即按返回键退出。
- `wait_back`：等待元素出现，出现即按返回键。
- 界面异常纠正默认开启（`var_ui_correct`），非刷视频界面自动分层纠正；`UI_CORRECT_MAX_FAIL/UI_FORCE_RESTART_LIMIT/UI_FREEZE_MAX_FAIL/UI_RECOVER_MAX_ATTEMPTS` 控制恢复/重启策略。

### 2.4 模板机制（kind 十类）

定义位置：`swipe.py` 顶部 `_TPL_KIND_NAMES / _TPL_KIND_CHOICES / _TPL_KIND_DISPLAY / _TPL_KIND_REVERSE`。

| kind（存储 key） | 中文名 | 含义与处理分支 |
|---|---|---|
| `task` | 任务按钮 | 出现即点击（常规任务按钮） |
| `close` | 广告关闭按钮× | 通用广告关闭按钮，命中即点 |
| `wait_appear` | 等待元素出现 | 等待元素出现后才继续 |
| `wait_disappear` | 等待元素消失 | 等待元素消失后才继续 |
| `live` | 直播间 | 命中直播间即按返回键退出 |
| `wait_back` | 等待元素出现（出现即按返回键） | 等待元素出现，出现即按返回键 |
| `captcha` | 验证码 | 命中即暂停设备提醒人工处理 |
| `slider_gap` | 滑块缺口 | 滑块验证码需滑到的位置（缺口） |
| `slider_btn` | 滑块按钮 | 滑块验证码底部拖柄 |
| `task_done` | 任务完成（识别到即停止刷） | 识别到即停止该设备刷金币并语音播报 |

兼容红线：新增/修改 kind 必须同步 `_TPL_KIND_NAMES`、`_TPL_KIND_CHOICES`（下拉显示）、`_TPL_KIND_REVERSE`，并在 `_image_tasks` 前置循环与 `_classify_ui_state` 补分支；运行时用 `meta.get("kind", "task")` 兜底，**旧模板无 kind 字段一律按 task 处理，保证旧配置兼容**。

模板存储：`templates\` 按应用分包（包名目录，另有 `通用` 目录 `TPL_COMMON_DIR`）；模板元数据（名称/包名/类型/识别阈值/分类目录/点击偏移/出现后动作/备注）保存在配置/坐标 JSON 中，支持「采集图像模板（拖拽框选）」「编辑模板」「新增模板(选图片)」「改分类」「删除选中模板」「打开模板目录」。

### 2.5 语音链路（vosk 离线）

```
设置页「语音控制」开关（var_voice_control，默认关闭）
        │（check_voice_config：内存≥4GB 且核数≥4，否则置灰）
        ▼
_start_voice_listener（voice_config.py L92）：校验依赖/模型/配置 → 启动守护线程
        ▼
_voice_loop（守护版）：vosk.Model 加载；运行期异常 3 秒后自动重启，连续 5 次才停止
        ▼
_voice_recognize_loop：按候选设备逐级尝试（用户指定 > 物理麦克风 > 系统默认兜底），
       sounddevice 阻塞 read 250ms/帧；麦克风连续 30 次失败视为不可用自动降级
        ▼
_handle_voice_text：两段式唤醒（说两次唤醒词才唤醒）+ 唤醒态命令匹配
        ▼
_try_voice_command：强制校验唤醒态；3 秒去抖；命中 VOICE_COMMANDS → root.after 回调 start/stop
```

要点：
- 默认唤醒词 `DEFAULT_WAKE_WORD = "小金币"`；唤醒词清空 = 语音控制不响应任何命令。
- **两段式唤醒**：待唤醒 → 半唤醒（播报「再说一次」）→ 正式唤醒（播报「我在，请说」）；半唤醒/唤醒均有超时回退；唤醒态再听到唤醒词仅重置命令窗口不执行。
- 识别文本与唤醒词**统一去空格匹配**（vosk 中文识别常输出「小 金币」）。
- 麦克风设备三态：空 = 自动优先物理麦克风；`__default__` = 跟随系统默认；数字 = 指定设备索引。虚拟声卡（Voicemeeter/CABLE/VB-Audio）默认排除，仅在用户明确指定时才用；`_mic_vsb` 滚动条仅当设备数 > 5 才显示。
- 语音播报（`speech.py`）：设备上下线/异常/任务完成等经 `_speak_event` 播报，受 `var_voice_control` 控制；`_speak_online/_speak_many` 缺 pywin32 时跳过不报错。

### 2.6 设备管理

- **自动轮询**：`devices._start_auto_detect` 每 3 秒 `adb devices` 检测，新设备自动加入设备列表；设备行显示备注/计数/状态。
- **USB 自动无线**：检测到 USB 接入 → 自动 `tcpip 5555` → 读取设备 IP → 自动 `adb connect IP:5555`，拔线后手机保持无线连接态；失败自动提示手动连接。
- **无线连接**：界面「无线连接 IP:端口」+「连接」；「批量配对」文本框每行 `IP:端口 配对码`，配合 `adb pair`。
- **全选/取消全选**：`var_select_all` + `_toggle_select_all`；设备列表 Canvas 可滚动，滚轮仅当指针在列表上时滚动。
- **单台控制**：`start_device(serial)` 每台设备独立线程（`_spawn_device_worker` → `_swipe_loop`），多设备并发互不影响；「设备行显示单台暂停/继续按钮」默认关闭（`var_device_pause_btn`），开启后 `toggle_device_pause` 单台暂停。
- **线程隔离**：每设备一个 worker 线程，`pause_events/workers/target_apps/task_done_flags` 均按 serial 组织；全局「开始刷」「全部停止」遍历全部设备。

---

## 三、版本管理规范（重点）

> 每次升版本号，**必须先与用户确认目标版本**。历史教训：用户曾明确要求日常不升版本号、仅明确指示才升；也曾要求主程序与语音模型包版本统一（V1.0.2.2）。**改动前先确认，不要自行决定版本号。**

### 3.1 版本号主定义

- **主定义**：`_modules_src\constants.py` 中的 `APP_VERSION = "V1.0.2.3"`（当前）。
- 主程序窗口标题、关于弹窗、检查更新对比均使用 `APP_VERSION`。

### 3.2 需同步的落点清单（全量）

| # | 文件 | 同步内容 |
|---|---|---|
| 1 | `_modules_src\constants.py` | `APP_VERSION`（唯一主定义，需重编 pyd 才生效） |
| 2 | `版本信息.txt` | `filevers=(1,0,2,3)`、`prodvers=(1,0,2,3)`、`FileVersion=V1.0.2.3`、`ProductVersion=V1.0.2.3`（PyInstaller 读它写 exe 属性） |
| 3 | 根目录 `update.txt` | 首行 `V1.0.2.3|下载链接`（程序检查更新的数据源） |
| 4 | `website\update.txt` | 同根 update.txt 格式（官网数据源） |
| 5 | `爽哥刷金币助手使用说明.md` | 版本记录段落 |
| 6 | 主程序 `.py` 窗口标题 | 已自动引用 `APP_VERSION`（无需手改，但需确认） |
| 7 | `website\index.html` | 多 `data-ver/APP_VER` 与日期（**不同步官网时保持旧值**） |
| 8 | `website\downloads.md` | 最新版本号、发布日期、MD5、下载链接（**不同步官网时保持旧值**） |
| 9 | `website\changelog.md` | 新增版本记录行（**不同步官网时保持旧值**） |

**不同步官网原则**：在「不打包、不同步官网」的开发中间态，`index.html / downloads.md / changelog.md` 一律保持旧版本号不动，避免与未重新发布的旧安装包 MD5/链接产生矛盾。只有正式发布时才同步官网（见第八节）。
**中间产物**：`constants.c` 是 Cython 生成的中间文件，无需手改。

### 3.3 pyd 重编流程（改 _modules_src 源码后必做）

1. **确认环境**：使用 Python 3.12 绝对路径
   `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`。
2. **停占用进程**：先结束正在运行的 exe/pythonw 进程，避免 `copy2` 覆盖 pyd 时 **WinError 32**（文件被占用）。
3. **编译**：在 `_modules_src` 下执行 `python setup.py build_ext --inplace`（`setup.py` 内 `cythonize(["constants.py", "ui.py", "scrcpy.py", "swipe.py"])`），生成对应 `cp312-win_amd64.pyd`。
   > 注意：当前 `setup.py` **只编译 constants、ui、scrcpy、swipe 四个模块**。其他模块（devices/voice_config 等）如被修改，需先在 `setup.py` 的 `cythonize` 列表中加入对应模块再编译。
4. **覆盖**：用 `copy2` 把新 pyd 覆盖到 `modules\` 下同名 pyd；根目录若存在同名 pyd（历史遗留）一并覆盖。
5. **import 验证**：用 Python 3.12 `import` 验证新 pyd 可加载且 `APP_VERSION` 为新值（例：`from modules.constants import APP_VERSION`）。
6. **回归验证**：源码运行 + 打包 exe 各验证一次关键路径。

### 3.4 历史教训（版本相关）

- **不主动升版本**：日常迭代不升版本号，用户明确指示才升；升级前必须与用户确认目标版本。
- **版本统一**：主程序与语音模型包版本曾被要求统一为 V1.0.2.2；发布时两个 Setup 包版本需一致决策（确认是否统一）。
- **落点遗漏**：版本号散落 10 余处（源码/元数据/官网），改完必须全局搜索旧版本号复核无残留（可用 `版本一致性检查.py` 或 `fs_search_content` 全项目搜索）。
- **同文件多处修改必须串行**：同一文件多个落点放同一批并行 edit 会被后写覆盖（见踩坑记录）。

---

## 四、新增功能清单（增加什么）

> 按版本时间线从旧到新整理。每项注明功能位置与关键逻辑，供「修改/回归测试时对照」。

### 4.1 起步阶段（09-21~22）

| 功能 | 位置/关键逻辑 |
|---|---|
| 单机自动刷 | `_swipe_loop` 上滑循环（间隔 `var_min/var_max`，默认 3~10 秒） |
| 多机同刷 | `_spawn_device_worker` 每设备独立线程 |
| 误触恢复 | `_recover`（先返回键 ×2，无效则 `_launch_app` 重启目标） |
| 签到宝箱 | `run_sign_in` / `_sign_in_worker`；`var_box/var_box_min` 定时点宝箱 |
| OpenCV 图像识别 | `cv2.matchTemplate` + 截图链路（见 2.3） |
| 广告自动关闭 | `close` kind 模板命中即点 |

### 4.2 V1.0.0.5 ~ V1.0.1.9

| 版本 | 功能 | 位置/关键逻辑 |
|---|---|---|
| V1.0.0.5 | 界面布局重构：功能区收进四个标签页（刷视频/赚金币任务/图像任务/设置）、设备列表可滚动、窗口自适应 | `ui._build_ui`；Notebook + Canvas 滚动列表；`_on_tab_changed` 自动调 Notebook 高度 |
| V1.0.0.6 | 模板按应用分目录：快手/抖音/视频号/通用各归各位，旧模板自动归档改名 | `templates\` 分包；启动时模板迁移 |
| V1.0.0.9 | 更新文件格式自由化：任意文件名/后缀，第一行自动识别版本号与下载链接 | `check_update` 读首行 `版本号|链接` |
| V1.0.1.0 | 模板分类目录可自定义：采集时指定分类、随时改分类，支持「宝箱专用」等自定义目录 | `rename_template_dir` / 采集窗口 |
| V1.0.1.2 | 全新程序图标：深藏青底+播放键+金币，16~256 多尺寸内嵌 exe | `appicon.ico` + spec icon |
| V1.0.1.4 | 文件全面更名 + exe 属性：程序/配置/坐标统一新命名，旧文件自动迁移不丢数据；内嵌版本/公司/版权 | `_migrate_unified_config`、版本信息.txt |
| V1.0.1.6 | 启动公告：远程纯文本，内容变化才弹窗；控制区新增「📢 公告」按钮；exe 公司属性改为杨爽工作室 | `ui.load_notice`（哈希去重弹窗）、`_show_notice` 可滚动窗口 |
| V1.0.1.7 | 新增赞助支持：主界面「❤ 赞助」按钮；左侧微信收款码、右侧在线赞助名单（自动统计人数/累计金额/前三名），名单格式宽松 | `ui.parse_sponsor_rows`、`show_sponsor` |
| V1.0.1.8 | 远程地址统一到新域名 `sgzs.杨爽.com`；四个远程地址全部内置默认值开箱即用；填写规则：留空=用内置地址，填 off=临时停用，填网址=用自定义 | `ui.remote_url` 解析 |
| V1.0.1.9 | 设置页新增 scrcpy 开源地址链接（github.com/Genymobile/scrcpy） | 设置区蓝色链接 |

### 4.3 V1.0.2.0

| 功能 | 位置/关键逻辑 |
|---|---|
| **可编辑模板功能** | 图像任务页「✏ 编辑模板」「＋ 新增模板(选图片)」按钮；可改模板名称/应用包名/类型/**识别阈值**/分类目录/**点击偏移**/出现后动作/备注；支持更换图片、直接选本地图片新增模板 |
| 每模板独立阈值与偏移 | 运行时按模板自身参数匹配点击（`meta.get("threshold")` / 偏移字段） |
| 改动保存进坐标 JSON，旧配置完全兼容 | 兼容 `meta.get("kind", "task")` 兜底 |
| 远程地址全部代码内置 | 检查更新/启动公告/赞助名单/**赞助二维码**改为代码内置固定地址；配置文件中对应项删除；微信收款码与官网域名同步更新 |

### 4.4 V1.0.2.1

| 功能 | 位置/关键逻辑 |
|---|---|
| **task_done 模板类型**：识别到即停止该设备刷金币并语音播报「任务完成」，多设备仅停对应设备 | `_TPL_KIND_*` 新增 `task_done`；`_image_tasks` 前置循环 + `_classify_ui_state`；`task_done_flags[serial]` |
| 模板类型汉化 | 下拉显示中文名（`_TPL_KIND_CHOICES` value=英文 key、显示=中文） |
| 新增模板窗口布局优化、保存按钮下增加实例说明 | 模板编辑/新增窗口 |
| **开始刷不再自动拉起投屏** | 删除自动投屏逻辑（见删除清单 5.1） |
| 语音欢迎播报：勾选语音控制后约 2 秒自动播报「欢迎使用爽哥刷金币助手语音控制功能」（仅语音开关开启且识别线程存活时） | `voice_config._start_voice_listener` 欢迎语延迟播报 |
| 导出日志改为写入**程序所在目录\logs\**（打包=exe 目录，源码=脚本目录） | `_export_log`；便于查找与备份 |
| **语音依赖修复重新打包**：发布包内置 vosk/sounddevice 语音组件（约 32MB），配合 models 开箱即用 | spec `collect_all('vosk')/collect_all('sounddevice')` |
| **麦克风设备下拉**：自动优先物理麦克风/跟随系统默认/指定设备；自动排除 Voicemeeter 等虚拟声卡；物理麦克风不可用时自动降级并日志提示 | `voice_config._fill_mic_devices/_scan_physical_mics/_build_mic_candidates/_is_blocking_input` |
| 修复 sounddevice 0.5.x 不兼容 `read(timeout=…)` 导致「麦克风连续读取失败」 | 改阻塞读取，250ms 一帧，退出响应不受影响 |
| 不影响系统默认输入设备设置 | 仅指定设备打开流，不改系统默认 |
| **USB 自动无线连接**：插 USB 自动开 5555 端口、读 IP、`adb connect IP:5555`；拔线后手机保持无线连接；失败自动提示手动连接 | `devices` USB 接入钩子 |
| 语音识别文本统一去空格匹配（解决「小 金币」带空格失配） | `_handle_voice_text` `"".join(text.split())` |
| 监听线程守护自动重启（异常 3 秒后拉起，连续 5 次才停止） | `_voice_loop` restart 计数 |
| 麦克风连续 30 次读取失败视为设备不可用自动重启 | `_voice_recognize_loop` stream_fail ≥ 30 |
| 唤醒确认播报改为「我在，请说」 | `_handle_voice_text` 唤醒态 |

### 4.5 V1.0.2.2

| 功能 | 位置/关键逻辑 |
|---|---|
| **设备单独开始**：每台设备行可单独点「▶ 开始刷」，互不影响 | `start_device(serial)` + 设备行按钮 |
| **绑定操作 App 自动绑定**：点「开始刷」时自动锁定目标包名；刷视频与清理后台任务自动跳过已绑定应用/桌面；绑定集合 = 自动目标 + 设置页「绑定操作App」+ 模板 bind_app | `_get_bind_apps`（swipe.py L1393） |
| **清理后台任务优化**：other_app 清理分支自动跳过绑定集合与桌面，避免误杀目标 | `_clean_other_app`（swipe.py L1422） |
| **图像模板 task_done**：识别到「任务完成」模板即停止该台设备刷金币并语音播报 | `_image_tasks` + `_classify_ui_state` |
| 版本号统一 V1.0.2.2（与语音模型包一致），主程序标题同步显示版本号 | 版本管理规范全量同步 |

### 4.6 V1.0.2.3（当前）

| 功能 | 位置/关键逻辑 |
|---|---|
| 设置页布局修正：语音开关 `voice_cb` 改 `columnspan=3` 防撑宽首列 | `ui._build_ui` 设置区 |
| 红色原因 Label 改 `column=3` 与保存按钮对齐 | `ui._build_ui` 设置区 |
| `var_scrcpy_path` 全项目无残留 | 删除控件后全项目检索清理（见删除清单） |
| 麦克风滚动条 `_mic_vsb` 仅设备数 > 5 才显示 | `voice_config._fill_mic_devices` |
| 麦克风设备列表布局修复：`lst_mic` 原 `grid(row=5, column=1, columnspan=2, sticky="ew")` 被拉伸变宽，滚动条 `_mic_vsb` 独立放 `column=3`，列表宽时滚动条与列表间出现大片空白；改为列表与滚动条放入同一子 Frame `frm_mic` 内 `pack(side="left", fill="y")` 紧贴排列；`_fill_mic_devices` 中滚动条显隐由 `grid()/grid_remove()` 改为 `pack(side="left", fill="y")/pack_forget()`；重编 ui/voice_config 两个 pyd 覆盖 modules | `ui._build_ui` 设置区 + `voice_config._fill_mic_devices` |
| 唤醒词输入框 `wake_entry` 宽度 `width=14` 改为 `width=8`（原过长，恰好容纳"小金"等短唤醒词；"绑定操作 App"输入框 `width=22` 保持不变）；重编 ui pyd 覆盖 modules | `ui._build_ui` 设置区 |
| 绑定操作 App 行提示文字与输入框分离：灰色提示 Label（"可空；配合「清理后台任务」使用…"）原独立 `grid(row=7, column=3)`，因列1权重被推远出现大量空白；改为与 `bind_entry` 放入同一子 Frame `frm_bind` 内 `pack(side="left")` 紧贴排列（Label `padx=(8,0)`），`frm_bind` grid `sticky="w"` 不拉伸；重编 ui pyd 覆盖 modules | `ui._build_ui` 设置区 |
| **图像任务同帧多命中按屏幕纵坐标排序依次完成**（未升版本号）：`_image_tasks` 常规循环不再「每轮只处理一个命中即 return」，改为收集本帧全部命中的 task / wait_appear / wait_disappear 模板，按屏幕纵坐标 y 从上到下排序后依次完成（wait_disappear 消失→点击原位置、wait_appear op=back→按返回键、其余→点击目标位置）；task_done / captcha / live / wait_back 前置优先逻辑与 60 秒命中冷却保持不变；`setup.py` cythonize 列表加入 `"swipe.py"`（现为 constants/ui/scrcpy/swipe），重编 swipe cp312 pyd 覆盖 modules 与根目录并 import 验证 | `swipe._image_tasks`（约 L1176-1267）+ `_modules_src/setup.py` |
| **图像任务分区域完成**（未升版本号，**已改为自由文本命名，汉字数字方案已废弃**）：模板 meta 改用可选 `region_name` 自由文本字段（如「50个红包」「20看广告」），**不再写入整数 `region`**；旧配置兼容：读取区域统一走 `_region_name_of(meta)` = `meta.get("region_name")` 或旧数字兼容「区域N」（`"区域%s" % meta.get("region", 1)`），旧模板自动显示为「区域1」等；编辑窗 `_open_template_editor`「任务区域」由 Spinbox 改为文本 Entry（width 适中），默认值取 `_region_name_of(meta)`（自定义名或旧数字转「区域N」），提示文案改「给区域起个名字，如：50个红包、20看广告；留空=默认区域」；框选采集窗 `_show_capture_window`「任务区域」同步改文本 Entry，默认留空；`_save_template_from_editor` 与 `on_release` 保存逻辑改为：名称 strip 后非空写 `meta["region_name"]`、留空不写，**不再写 `meta["region"]`**；**汉字数字方案已废弃**：删除 `_REGION_CN_NAMES`（一~五十） / `_REGION_CN_REVERSE` / `_region_num_to_cn` 等常量与函数，避免死代码；`_image_tasks` 常规循环：先遍历 `coords["templates"].items()` 按**配置文件中模板首次出现的区域顺序**构建区域名→序号映射 `region_order`（区域先出现的先完成），pending 元组 region 键改为字符串 `region_name`（wait_disappear 消失后入 pending、wait_appear op=back、custom 分支同样带该模板 region_name），排序改为 `pending.sort(key=lambda it: (region_order.get(it[0], 10**9), it[1]))`（区域按首次出现序，同区域内再按屏幕纵坐标 y 从上到下），循环解包同步更新；task_done / captcha / live / wait_back 前置优先逻辑与 60 秒命中冷却保持不变；采集窗与编辑窗类型清单保持同步；重编 swipe cp312 pyd 覆盖 modules 与根目录并 import 验证通过 | `swipe._region_name_of` + `_open_template_editor`（L342 起）+ `_save_template_from_editor` + `_show_capture_window`（L753 起）+ `on_release` + `_image_tasks` 常规循环 |
| **打开投屏自动静音**：手动点「打开投屏」（ui.py L281 `command=self.launch_scrcpy`）启动 scrcpy 后，自动执行 `adb shell media volume --stream 3 --set 0` 将设备媒体音量静音（仅媒体流，静音失败仅日志提示不影响投屏）；`setup.py` 编译清单加入 `scrcpy.py`，重编 scrcpy pyd 覆盖 modules | `scrcpy.launch_scrcpy` + `_mute_device` |

### 4.7 其他并行能力

| 功能 | 说明 |
|---|---|
| **Android 原生版 V1.0.0**（2026-10-13，包名 `com.yscl.autoapp`） | 手机端无障碍服务自动上滑刷抖音/快手；下滑回看概率 0~100%（默认 10%，SharedPreferences 持久化）；悬浮窗控制面板（开始/停止/上滑/下滑/收起展开）；语音控制（开始/停止/上滑/下滑/概率N，数字自动限制 0~100）；日志查看/清空/导出分享；独立更新数据源 `android-update.json`；主界面标题署名「杨爽用Marvis开发」。**注意：Android 版是独立项目，PC 版修改时不要动其数据源格式** |
| 设置页各开关 | `var_min/max/prob/recover/swipe/stayon/box/img/device_pause_btn/voice_control/ui_correct/clean_other/bind_app/wake_word/mic_device` 等全部持久化于 `UI_STATE_KEYS` |

---

## 五、删除/废弃清单（删除什么）

> 以下功能/代码已被删除或废弃。**修改代码时严禁重新引入**；若需求近似，按「新增」流程走，不要复活旧实现。

### 5.1 开始刷自动投屏（已删）

- **删了什么**：`_auto_launch_scrcpy` / `_auto_launch_on_start` 自动投屏逻辑。
- **为什么删**：V1.0.2.1 起「开始刷不再自动拉起投屏」——投屏不是刷金币必需，自动拉起打扰用户。
- **连带清理**：`scrcpy.py` 保留 `launch_scrcpy`（手动投屏，V1.0.2.3 起启动后自动静音媒体音量）、`_mute_device` 与 `_update_start_btn_state`；scrcpy 相关依赖仍随 bin 分发。

### 5.2 scrcpy 缺失禁用开始刷限制（已解除）

- **删了什么**：`_update_start_btn_state` 中「scrcpy 缺失时禁用开始刷」的限制逻辑。
- **为什么删**：开始刷已不依赖投屏，scrcpy 缺失不应阻止刷金币。
- **连带清理**：无；`_update_start_btn_state` 仍保留用于其他状态更新。

### 5.3 var_scrcpy_path 控件及引用（已清理）

- **删了什么**：设置页 scrcpy 路径输入框控件 `var_scrcpy_path` 及其全部下游引用。
- **为什么删**：V1.0.2.3 设置页布局修正时发现该控件已无实际用途（scrcpy 不再自动拉起），保留只会撑乱布局。
- **连带清理**：**全项目检索无残留**（app_config/devices/主入口均无 `var_scrcpy_path`）；这是「删 UI 控件必须全项目检索下游引用」的反面教材——当时正是靠全项目搜索才确认清干净，否则运行期会 `AttributeError`。

### 5.4 旧桌面 exe 清理

- **删了什么**：旧版本散落在桌面的 exe。
- **为什么删**：避免用户误用旧版；桌面分发统一走 installer/官网。

### 5.5 远程地址配置项（已删除）

- **删了什么**：检查更新 / 启动公告 / 赞助名单 / 赞助二维码的配置文件项。
- **为什么删**：V1.0.2.0 起全部远程地址**代码内置**（constants.py 硬编码 URL），配置文件不再需要对应项，配置更精简。
- **连带规则**：`ui.remote_url` 的填写规则——**留空=用内置地址，填 off=临时停用，填网址=用自定义**。

### 5.6 旧配置文件（迁移后删除）

- **删了什么**：旧 `坐标.json`、`配置.json`。
- **为什么删**：`_migrate_unified_config` 统一为 `爽哥刷金币助手配置.json`；**校验成功后删除旧文件**，防止再次迁移/残留。
- **注意**：删除走应用内逻辑，属受控迁移，不要手动删用户数据。

### 5.7 旧模板目录（归档改名）

- **删了什么**：V1.0.0.6 前未按应用分包的旧模板。
- **为什么删/改**：模板按应用分目录（快手/抖音/视频号/通用），旧模板**自动归档改名**不丢数据。

### 5.8 其他废弃注意

- 根目录历史遗留 `*.cp312-win_amd64.pyd`（如 `swipe.cp312-win_amd64.pyd`）：非运行加载路径（运行用 `modules\`），重编时一并覆盖或清理，但**不要误删 `modules\` 下同名文件**。
- `V1.0.2.2 还原`：历史上曾出现需要回退版本号的情况（主程序与语音包曾要求统一 V1.0.2.2）。回退/还原必须按第三节版本管理规范**先与用户确认目标版本**，再全量同步落点。

---

## 六、修改代码注意事项（每次必看）

> 以下 10 条在每次改代码前逐条过一遍，任何一条都可能造成线上/运行期事故。

### 6.1 改 _modules_src 源码后必须重编对应 pyd

- 程序运行/打包加载的是 **`modules\` 下同名 pyd**（同名 pyd 优先于源码），不是 `_modules_src\*.py`。
- 只改 `constants.py / ui.py / scrcpy.py / swipe.py`：按 3.3 流程重编即可（setup.py 已含这四个模块）。
- **改了其他模块（swipe/devices/voice_config/app_config/speech/scrcpy/adb_utils/logger/utils）**：必须先把模块名加入 `_modules_src\setup.py` 的 `cythonize([...])` 列表，再编译、覆盖 `modules\`，最后 `import` 验证。
- 源码调试也要用 Python 3.12（pyd 为 cp312，低版本解释器无法导入）。
- **严禁**：改完源码不重编就直接打包/交付，运行期的旧 pyd 会让你以为修改没生效。

### 6.2 删 UI 控件必须全项目检索下游引用

- 删除任何 `var_*` / 控件后，**必须全项目搜索**（app_config/devices/主入口/各 Mixin）残留引用，否则运行期 `AttributeError`（`var_scrcpy_path` 就是先例）。
- 控件删除还注意：布局网格（grid）其他控件对齐可能被影响——删一行后检查同行/同列 `columnspan`。

### 6.3 版本号改动必须按第三节清单全量同步

- 主定义 `constants.APP_VERSION` + 版本信息.txt + 根/website update.txt + 使用说明.md + 官网三件套（按「不同步官网」原则区分场景）。
- 改完**全局搜索旧版本号**确认无残留（推荐跑 `版本一致性检查.py`）。

### 6.4 新增模板 kind 走最短兼容路径

- 新增 kind 必须同步五处：`_TPL_KIND_NAMES`（名字映射）、`_TPL_KIND_CHOICES`（下拉选项）、`_TPL_KIND_REVERSE`（中文反查）、`_image_tasks` 前置/常规分支、`_classify_ui_state` 分类分支。
- **兜底红线**：`meta.get("kind", "task")` 不得移除——旧模板没有 kind 字段，一律按 `task` 处理，保证旧配置兼容。
- 白名单：任何地方对 kind 的校验列表（如有）同步更新，防止下拉选不出新类型。

### 6.5 不用中文路径/文件名（开发/编译/打包侧）

- 脚本、Cython 中间产物、临时文件路径避免中文与空格组合，防止编码/打包踩坑（项目本身中文文件名是既有事实，新增开发脚本尽量英文）。
- `strftime()` 格式串**严禁中文字符**（`%Y年%m月%d日` 会触发 `UnicodeEncodeError`），用 f-string 拼接：`f"{dt.year}年{dt.month:02d}月{dt.day:02d}日 {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"`。

### 6.6 中间产物与最终产物分开

- 开发过程脚本/清单/临时数据写会话 **temp** 目录；最终产出（报告/打包清单等）写 **output** 目录；用户明确指定路径（如本手册）按用户指定。
- 严禁写系统临时目录（`C:\Temp` 等），临时目录用 `$env:TEMP` / `tempfile.gettempdir()` 动态获取。

### 6.7 禁止 shell/python 物理删除

- 文件/目录删除一律用 **delete 工具（移回收站）**；严禁 `rm / rmdir / del / Remove-Item / shutil.rmtree / os.remove` 等物理删除命令（含整理撤销后清理空目录场景）。
- 递归删除会被拦截，改用 delete 工具逐个/批量移回收站。
- 批量/模糊删除（“旧文件”“没用的”）先列出结果并经用户确认再删。

### 6.8 tkinter 布局与截图踩坑

- **grid 对齐**：改设置区控件注意 row/column 对齐；`columnspan` 不足会把相邻列撑宽（`voice_cb` 教训：改 `columnspan=3` 防撑宽首列）。
- **wraplength**：长文案 Label 需设 wraplength，否则窗口被撑爆；`justify="left"` 配合换行。
- **Notebook 高度**：`_on_tab_changed` 按当前页内容自然高度调整，新增控件后检查高度自适应。
- **截图**：图像识别用 adb 二进制截屏，**不要用 PrintWindow 截桌面窗口**（丢滚动条/遮挡，见踩坑）。
- **同文件多处编辑必须串行**：同一文件多个修改点分批次顺序 edit，改完用搜索复核（并行 edit 会丢映射）。

### 6.9 删除/破坏性操作前先确认

- 删文件、覆盖、批量变更先确认影响范围；🔴 高风险（格式化/清空/批量破坏）必须获得明确授权，🟡 中风险（覆盖/配置变更/终止进程）二次确认。
- 单文件小改动、只读操作直接执行。

### 6.10 打包流程要点（每次发布前核对）

- PyInstaller 用 Python 3.12 + spec 文件；**前台重定向 `*>log` 会截断**，改用 `Start-Process` 后台重定向（见 7.1）。
- dist 组装必须带 `bin`（adb/scrcpy/dll）与 `models`（vosk 模型），否则无线调试/语音不可用。
- Inno Setup 打包后必须**静默安装/卸载验证**，再算 MD5 更新官网。

### 6.11 改码后必更新手册（强规则）

- 每次改码（含源码、pyd、打包配置、官网数据源、开发脚本）完成后，**必须立即**将本次改动要点、踩坑与决策追加进对应章节（新增功能→第四章、删除→第五章、踩坑→第九章、注意项→本章），保持手册与代码同步。
- 手册是下次动手前的第一读物：**遗漏更新 = 下次改码失去依据**。宁可多记，不可不记。
- 提交/交付前自查：本次改动是否已在手册中找到对应落点并完成记录；未记录视为任务未完成。

---

## 七、打包与发布流程

### 7.1 PyInstaller（onedir）

- 命令：`pyinstaller 爽哥刷金币助手.spec --noconfirm`（用 Python 3.12 环境执行）。
- spec 关键配置（`爽哥刷金币助手.spec`）：
  - `glob.glob('modules/*.cp312-win_amd64.pyd')` 显式收集所有 pyd 到 `binaries`（PyInstaller 无法从 pyd 二进制提取间接 import）。
  - `collect_all('vosk') / ('sounddevice') / ('win32com') / ('win32api') / ('pythoncom') / ('cv2') / ('numpy')` 收集动态依赖与数据。
  - `hiddenimports += ['tkinter.filedialog','tkinter.messagebox','tkinter.simpledialog']`。
  - `EXE(name='爽哥刷金币助手', console=False, contents_directory='_lib', version='版本信息.txt', icon=['appicon.ico'])`。
  - `COLLECT(name='ShuageCoinHelper')`（onedir 目录名）。
- **踩坑**：前台 `pyinstaller ... *> log.txt` 重定向会把构建过程截断成不完整日志，改用 `Start-Process pyinstaller -ArgumentList ... -RedirectStandardOutput log.txt -RedirectStandardError err.txt -Wait` 后台方式。

### 7.2 dist 组装

1. 取 `dist\ShuageCoinHelper\` 产物。
2. 把 `bin\` 目录整个拷入 exe 同层（adb.exe/scrcpy.exe/全部 dll/scrcpy-server）。
3. 把 `models\` 目录拷入 exe 同层（vosk 模型）。
4. 确认 `爽哥刷金币助手.exe`、`_lib\`、`modules pyd` 都在位；启动冒烟：`dist\爽哥刷金币助手.exe` 可开窗、可检测设备、语音开关可用。

### 7.3 Inno Setup 6（安装包）

- iss 脚本关键字段（脚本为发布时编写，仓库不常驻 iss 文件）：
  - `AppName=爽哥刷金币助手`、`AppVersion=<目标版本>`、`AppPublisher=杨爽工作室`。
  - `OutputBaseFilename=爽哥刷金币助手_V<版本>_Setup`（与语音模型包区分命名）。
  - `Source: dist 目录` 整体安装到 `{autopf}`，创建桌面/开始菜单快捷方式。
  - 语音模型包单独一个 Setup（`爽哥刷金币助手_语音模型_V<版本>_Setup.exe`，装主程序后按需安装），两者版本号决策统一。
- 主程序与语音模型包两个 Setup 均保留在 `installer\`。

### 7.4 静默安装/卸载验证

- 安装：`Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`，验证桌面快捷方式、目录文件齐全、启动正常。
- 卸载：`{app}\unins000.exe /VERYSILENT`，验证注册表与目录清理干净。

### 7.5 产物 MD5

- `certutil -hashfile 爽哥刷金币助手.exe MD5`（或 PowerShell `Get-FileHash -Algorithm MD5`）。
- 主程序包与语音模型包各算一个 MD5，写入官网 `downloads.md`（见第八节）。

### 7.6 官网同步清单（正式发布时）

1. `website\update.txt`：首行更新为 `新版本号|123网盘链接`。
2. `website\downloads.md`：最新版本号/发布日期/MD5（主程序+语音包）/下载链接。
3. `website\changelog.md`：追加本次版本记录行（宽松格式）。
4. `website\index.html`：`data-ver/APP_VER` 与日期同步（发布态才动）。
5. `website\dl\`：保留主程序 exe 副本（不删旧副本）。
6. 上传 123 网盘：主程序包与语音模型包均走 **123 网盘分发，不用官网直链**；Android APK 走 `./dl` 直链。
7. **未正式发布时**：以上全部保持旧值，仅更新程序内部落点（第三节「不同步官网原则」）。

---

## 八、官网维护

### 8.1 网站结构

- **单文件页面**：`website\index.html`（约 63KB），内联 JS `fetch` 渲染各 md 数据源；本地双击 `file://` 打开 fetch 被浏览器拦截，需用 `预览官网(读md生效).bat` 起本地服务或用 http 访问。
- 数据源文件（改 md 即改官网，不动 index.html）：
  - `downloads.md`：下载菜单（悬停弹出）内容 + 下载说明。
  - `changelog.md`：更新日志区块。
  - `sponsors.md`：赞助名单（与软件内置赞助窗口一致）。
- 其他：`update.txt`（版本数据源）、`notice.txt`（启动公告，33 字节）、`android-update.json`（Android 独立更新源：`version/downloadUrl/notes`）、`counter.php`（计数）、`dl\`（历史安装包副本）。

### 8.2 downloads.md 格式（宽松兼容）

- 一行一条：`- [标题](链接)` 最推荐；`标题 | 链接 | 副标题` 竖线/表格写法也行；`---` 画分隔线；`# 第一行标题` 做菜单小标题。
- 链接必须是 `http://`、`https://`、`/`、`./` 或 `#` 开头；其他行（空行/注释/说明）自动跳过。
- **注意**：说明文字行里不要同时出现「竖线」和「https 链接」，否则被误当成菜单项。
- 当前关键内容：最新版 `V1.0.2.2`（发布日期 2026-10-19）、主程序包 MD5 `3F560B042E476ECFF092552AD7857245`、语音模型包 MD5 `2230CA9F5DDD61A1A21FC20C16B7D695`、123 网盘链接、使用说明文档、历史版本目录、Android APK。

### 8.3 changelog.md 格式

- 每行一条记录，宽松格式任选：表格写法 `| 版本 | 日期 | 内容 |`；竖线写法 `V1.0.2.0 | 2026-09-29 | 内容……`；逗号写法；制表符写法。
- `#` 开头行与空行忽略；第一列版本号、第二列日期（可为「起步阶段」等文字）、第三列起为内容；内容可用 `**加粗**` 标记重点。

### 8.4 update.txt 格式

- 首行 `版本号|下载链接`（如 `V1.0.2.3|https://...123pan...`）。根目录与 `website\update.txt` 相同格式。
- 程序 `check_update` 读首行：版本号不同则提示更新并给链接。

### 8.5 分发与 MD5 同步位置

- **主程序与语音模型包均走 123 网盘**，不用官网直链；`website\dl` 保留主程序 exe 副本（不删）。
- MD5 同步位置：`downloads.md` 的 MD5 行（发布态更新）；用户校验方法说明也在该文件。
- Android 版更新走 `android-update.json`，与 PC 版 `update.txt` 完全隔离。

---

## 九、历史版本演进与踩坑记录

### 9.1 版本演进里程碑

| 时间 | 版本 | 里程碑 |
|---|---|---|
| 09-21~22 | 起步阶段 | 单机自动刷 → 多机同刷 → 误触恢复 → 签到宝箱 → OpenCV 图像识别 → 广告自动关闭 |
| 09-23 | V1.0.0.5 | 界面布局重构（四标签页、设备列表滚动、窗口自适应） |
| 09-23 | V1.0.0.6 | 模板按应用分目录 + 旧模板自动归档 |
| 09-23 | V1.0.0.9 | 更新文件格式自由化（首行识别版本号+链接） |
| 09-23 | V1.0.1.0 | 模板分类目录可自定义（宝箱专用等） |
| 09-23 | V1.0.1.2 | 全新程序图标 |
| 09-23 | V1.0.1.4 | 文件全面更名 + exe 内嵌版本/公司/版权 + 旧配置迁移 |
| 09-23 | V1.0.1.6 | 启动公告 + 公告按钮 + 公司属性杨爽工作室 |
| 09-23 | V1.0.1.7 | 赞助支持（收款码 + 在线名单统计） |
| 09-23 | V1.0.1.8 | 远程地址统一新域名 + 全部内置默认值 |
| 09-23 | V1.0.1.9 | 设置页 scrcpy 开源地址链接 |
| 09-29 | V1.0.2.0 | 可编辑模板（阈值/偏移/动作/备注）+ 远程地址全部代码内置 |
| 10-13 | Android 1.0.0 | Android 原生版首发（无障碍/悬浮窗/语音/日志/独立更新源） |
| 10-18 | V1.0.2.1 | task_done 模板、模板类型汉化、开始刷不再自动投屏、语音依赖打包修复、麦克风设备下拉与降级、USB 自动无线、语音线程守护 |
| 10-19 | V1.0.2.2 | 设备单独开始、绑定操作 App 自动绑定与清理后台优化、task_done 即停设备、版本号统一 V1.0.2.2 |
| 10-23 | V1.0.2.3 | 设置页布局修正（voice_cb columnspan、原因 Label 对齐、var_scrcpy_path 清理、麦克风滚动条按需显示） |

### 9.2 踩坑记录（务必引以为戒）

| # | 坑 | 现象 | 根因 | 解法/规则 |
|---|---|---|---|---|
| 1 | **Cython 编译 WinError 32** | `copy2` 覆盖 pyd 时报文件被占用 | exe/pythonw 进程占用 pyd | 先停占用进程再覆盖 |
| 2 | **同文件并行 edit 丢映射** | 同一 .md/.txt 多处替换后仍有旧值残留（如 V1.0.2.2 残留） | edit_file 对同文件并发写未串行互斥，后写者基于旧快照覆盖 | 同文件多处修改必须**串行**分批执行，改完全局搜索复核 |
| 3 | **PrintWindow 截图丢滚动条** | 截桌面窗口拿不到完整界面 | PrintWindow 只截可见区域，滚动条区域/遮挡丢失 | 图像识别改用 **adb exec-out screencap** 全屏截取 |
| 4 | **360 浏览器 CDP 限制** | 用 360 浏览器调试/预览官网受限 | 360 浏览器对 CDP（Chrome DevTools Protocol）有限制 | 官网预览用 `预览官网(读md生效).bat` 起本地服务/换标准浏览器，别依赖 360 调试 |
| 5 | **PyInstaller 前台重定向截断** | `*>log` 构建日志不完整 | 前台管道缓冲/截断 | 改用 `Start-Process` 后台重定向 + `-Wait` |
| 6 | **递归删除被拦截** | shell/python 递归删除失败 | 安全策略拦截物理删除 | 用 **delete 工具移回收站**，禁止 rm/rmdir/Remove-Item |
| 7 | **sounddevice 0.5.x `read(timeout)` 不兼容** | 「麦克风连续读取失败」 | 新版 sounddevice 移除 timeout 语义 | 改阻塞读取，250ms 一帧 |
| 8 | **vosk 中文识别带空格** | 唤醒词/命令失配（「小 金币」「开 始」） | vosk 中文结果字间带空格 | 识别文本与唤醒词**统一去空格**再匹配 |
| 9 | **虚拟声卡抢系统默认输入** | 语音识别用错设备 | Voicemeeter 等虚拟声卡占用默认输入 | 物理麦克风优先 + 自动降级 + 虚拟声卡仅显式指定才用 |
| 10 | **语音监听线程静默失效** | 语音功能悄悄挂掉 | 线程异常未恢复 | 守护自动重启（3 秒后拉起，连续 5 次才停止）+ 30 次读取失败判不可用 |
| 11 | **strftime 中文字符编码错误** | `%Y年%m月%d日` 抛 UnicodeEncodeError | 格式串含中文字符 | f-string 拼接日期 |
| 12 | **打包缺语音组件** | 发布包语音提示「组件未安装」 | 未 collect vosk/sounddevice | spec `collect_all('vosk'/'sounddevice')`，exe 内置依赖约 32MB |
| 13 | **tkinter 布局撑宽** | 设置页首列被长文案撑宽 | Checkbutton 缺 columnspan | `voice_cb` 改 `columnspan=3`；长文案设 wraplength |
| 14 | **版本号落点遗漏** | 部分文件残留旧版本号 | 版本号散落 10 余处 | 全局搜索旧版本号 + `版本一致性检查.py` |
| 15 | **bat 必须保持 CRLF 行尾** | `启动爽哥刷金币助手_源码版.bat` 被写成 LF-only 行尾，cmd 解析 `cd /d` 报 `'/d' is not recognized`，`start` 命令从未执行，桌面快捷方式点击无反应 | bat 文件行尾为 LF，cmd 对非 CRLF 批处理解析异常 | 写/改 .bat 一律使用 CRLF 行尾；生成后先实测双击或 `cmd /c` 验证，不能只看内容文本 |

### 9.3 协作与工具红线（本项目长期约定）

- **多 Agent 协作**：file-agent 负责代码/打包/验证；改代码前先通读本手册。
- **主程序保持 .py 启动**：入口 `爽哥刷金币助手.py` 不打包成单文件脚本，业务逻辑全部在 modules pyd。
- **官网不同步原则**：非正式发布不改官网三件套。
- **123 网盘分发**：主程序与语音包均走 123 网盘，不用官网直链；`website\dl` 主程序 exe 副本不删。

---

*本手册随项目持续更新。任何一次「新增/删除/版本升级/踩坑」都应在对应章节登记，保持手册始终是动手前的第一读物。*
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
