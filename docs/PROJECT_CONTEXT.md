# 课堂安全播报助手 — 项目导入文档

> 由 CodeBuddy 于 2026-09-01 从 WorkBuddy 项目导入。本文件沉淀项目背景、文件清单、踩坑记录与后续开发须知。

---

## 1. 项目概览

| 项目 | 内容 |
|------|------|
| 项目名 | 课堂安全播报助手（classroom-broadcast-mvp） |
| 原始位置 | `C:\Users\Windy\WorkBuddy\2026-08-31-21-55-23\classroom-broadcast-mvp` |
| 形态 | Windows 桌面驻留的 1530 安全教育自动播报工具（单机离线 MVP） |
| 来源 | 抖音视频（@老施收藏夹，演示「班级通知小管家」1530 安全教育场景），用户想复刻一个 Windows 版 |
| 竞品参考 | 班级通知小管家（PC+手机）、1530小助手（小程序/Web） |
| 技术栈 | Python 3.13 + pyttsx3（TTS）+ 纯 ctypes Win32 API（托盘/弹窗），无第三方 GUI 库 |
| 配套 PRD | 同目录 `课堂安全播报助手-PRD.md`（V1.0，规划至 V2 学校版） |

核心定位：**教室大屏上的"定时安全广播 + 远程喊话器"，班主任零操作完成 1530 播报与留痕。**

## 2. 已落盘文件清单

| 文件 | 作用 | 备注 |
|------|------|------|
| `broadcast.py` | 主程序：调度器 + 弹窗 + TTS + 托盘 + CSV 台账 | 242 行 |
| `content.py` | 1530 安全提醒内容模板库（每日/周五/节假日专题） | 可随意增改 |
| `config.json` | 配置：放学时间 16:30、规则开关、假期列表 | 改后需重启生效 |
| `popup_win.py` | 纯 ctypes Win32 全屏红底弹窗（替代 tkinter） | 置顶 + ESC/点击关闭 + 定时自动关闭 |
| `tray_win.py` | 纯 ctypes Win32 托盘模块（替代 pystray） | 右键菜单 + 双击试播 |
| `start.bat` | 一键后台启动（pythonw 无窗口） | 含进程自检 |
| `start_debug.bat` | 前台调试启动（显示报错） | |
| `test_popup.py` / `test_popup.bat` | 弹窗+TTS 独立测试 | |
| `app.ico` | 红黄托盘图标 | Pillow 生成 |
| `broadcast_log.csv` | 播报台账（自动留痕） | 已含 9/1 三次试播记录 |
| `debug.log` | 运行日志落盘 | |
| `README.md` | 使用与排障说明 | |
| `.venv/` | 虚拟环境（pyttsx3 等依赖） | |
| `__pycache__/` | Python 字节码缓存 | |

## 3. 核心逻辑速览

- **调度规则**（`broadcast.py`）：
  - 每日：教学日放学前 1 分钟（16:29）
  - 周五：放学前 5 分钟（16:25）
  - 节假日：**假期前最后一个教学日**放学前 30 分钟（16:00）
  - 假期当天 / 周末 / 已过放学时间 → 不播
- **弹窗**：全屏红底（#B01E1E）+ 黄色标题 + 白色正文，Always-On-Top，不抢焦点，朗读完自动关闭
- **TTS**：`pyttsx3`，**每次播报新建 engine**（避免 runAndWait 状态残留导致静音）
- **托盘**：右键菜单"立即试播"三种内容；双击=立即播每日提醒
- **日志**：每次播报写 `broadcast_log.csv`（时间/类型/标题/内容/状态），可导给学校迎检

## 4. WorkBuddy 记忆获取方式（已验证可用）

该项目在 WorkBuddy 下的持久化信息分布在三处：

| 位置 | 内容 | 说明 |
|------|------|------|
| `项目/.workbuddy/memory/2026-08-31.md`、`2026-09-01.md` | 每日开发记忆（需求来源、进度、踩坑结论） | **最直接**，按日期归档 |
| `~/.workbuddy/projects/c-Users-Windy-WorkBuddy-2026-08-31-21-55-23/cc4eb308-*.jsonl` | 完整会话流水（516 行，含每次对话/推理/工具调用） | 用于回溯详细过程 |
| `~/.workbuddy/MEMORY.md` | 跨项目用户偏好（操作授权规则、沟通偏好） | **必须遵守** |
| `~/.workbuddy/skills/windows-pystray-fallback/SKILL.md` | 已沉淀的托盘踩坑技能 | 可复用 |

会话标题时间线：`搜索抖音视频并整理 PRD` →（打断 docx）→ PRD Markdown → MVP 搭建 → 多轮排障修复。

## 5. 踩过的坑（重要，后续开发避免重蹈）

1. **`WindowsApps\python.exe` 是微软商店占位符**：创建 venv 失败（exit 49）。须用 `~\.workbuddy\binaries\python\versions\3.13.12\python.exe`（实测 3.13.14）。
2. **pystray 0.1.4 与 Python 3.13 + Win11 不兼容**：进程活着但托盘窗口未创建、图标不显示（`visible` 恒 False）。已改用纯 ctypes 自写 `tray_win.py`（`RegisterClassExW→CreateWindowExW→LoadImageW→Shell_NotifyIconW(NIM_ADD)`），验证通过。
3. **AI 沙箱会话隔离**：用 `start "" pythonw.exe` 起的进程只活在 AI 的隔离沙箱里，用户真实桌面看不到——"验证通过"不等于用户可见。**必须由用户在自己登录会话双击启动才算数**。
4. **bat 文件编码**：UTF-8 写的 bat 被 cmd 按 GBK 解析，中文路径全乱码导致整条命令失效。**bat 一律用纯英文 + 绝对路径 `cd /d %~dp0`**。
5. **managed Python 3.13.12 未编译 tkinter**：`ModuleNotFoundError: No module named 'tkinter'`，弹窗全崩（TTS 有声音只因 pyttsx3 不依赖 tkinter）。已用纯 Win32 API 重写 `popup_win.py`。
6. **pyttsx3 持久 engine 状态残留**：首次 `runAndWait` 后再次调用无声音。改为**每次播报新建 engine**。
7. **ctypes 结构体/argtypes 声明顺序**：`WNDCLASSEXW` 定义在文件后部但 argtypes 在前面引用会失败；`RegisterClassExW` argtypes 用 `c_void_p` 传 `byref(wc)` 类型不匹配；`CreateSolidBrush` 缺 argtypes。正确顺序：**常量 → 结构体 → API 签名 → 逻辑**。
8. **bat 里 `python -c` 传中文参数**：cmd 引号 + 中文混用极易出错。应改用独立 `.py` 测试文件。
9. **Win32 消息循环必须在创建窗口的线程跑**：弹窗放子线程（daemon），TTS 放主线程，主线程先 `sleep(0.3)` 等弹窗线程起来。
10. **节假日规则初始 bug**：原逻辑在"当天是假期"才触发，但放假当天已停课；改为"**假期前最后一个教学日**"触发。
11. **Win11 隐藏图标区**：新托盘图标可能被收进任务栏右下角 ∧ 隐藏区，需点开查看（README 已注明）。

## 6. 用户偏好（源自 WorkBuddy 全局记忆，必须遵守）

- **操作授权规则（重要）**：未经针对具体操作的明确授权，只做只读操作。改配置/装软件/删移文件/启停服务等必须先问再做。已因此被纠正两次，不得有第三次。
- **沟通偏好**：不要客套话（"好的！""很高兴帮你"），直接说事；对风险/破坏性操作先警告+列清单+等确认。
- **文档交付偏好**：默认给 Markdown，**不要主动上 docx 流水线**（曾因擅自走 tdoc-orchestrator 被叫停）。
- 用户背景：游戏串流/显示设备调试（Dell S2716DG + HDMI 诱骗器 + Sunshine/Moonlight），非本项目的上下文。

## 7. 当前状态与验证结果

- 2026-09-01 01:40~01:43 期间已在用户桌面成功试播：daily / friday / holiday 三种播报，CSV 台账有"开始→已播报"完整记录，弹窗+TTS 正常。
- 最终一次会话在修复 `popup_win.py` 结构体声明顺序时被打断（并遇到模型 429 配额报错），**落盘版本为已验证可用的最终版**。
- debug.log 确认：托盘窗口类 `ClassroomBroadcastTrayWnd` 创建成功，`scheduler_loop` 正常启动。

## 8. 后续开发注意事项 / 已知限制（MVP）

- 无远程推送（手机端）、无开机自启注册、无设置界面（改 config.json 后重启生效）。
- PRD 规划的 V1.0（联网版）尚未开始：教师端小程序、云端消息中转、台账导出等。
- 若需打包 exe：注意 pystray/tkinter 的坑已在代码层绕开，可直接用 PyInstaller 尝试。
- 修改弹窗/托盘代码时，保持"结构体在前、argtypes 在后"的书写顺序。
