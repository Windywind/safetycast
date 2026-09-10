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
| 技术栈 | Python 3.13 + comtypes 直连 SAPI5（TTS）+ 纯 ctypes Win32 API（托盘/弹窗），无第三方 GUI 库 |
| 配套 PRD | 同目录 `课堂安全播报助手-PRD.md`（V1.0，规划至 V2 学校版） |

核心定位：**教室大屏上的"定时安全广播 + 远程喊话器"，班主任零操作完成 1530 播报与留痕。**

## 2. 已落盘文件清单

| 文件 | 作用 | 备注 |
|------|------|------|
| `broadcast.py` | 主程序：调度器 + 弹窗 + TTS + 托盘 + CSV 台账 | 242 行 |
| `content.py` | 1530 安全提醒内容模板库（每日/周五/节假日专题） | 可随意增改 |
| `config.json` | 配置：放学时间 16:30、规则开关、假期列表、字号档位、音色 | 可在设置界面修改，保存即生效（无需重启） |
| `popup_win.py` | 纯 ctypes Win32 全屏红底弹窗（替代 tkinter） | 置顶 + ESC/点击关闭 + 定时自动关闭 + 字号缩放系数 |
| `tray_win.py` | 纯 ctypes Win32 托盘模块（替代 pystray） | 右键菜单（试播/设置/退出）；左键不触发动作 |
| `config_store.py` | 配置层：默认值/深合并/校验/原子保存/热应用 | 不依赖 GUI，可 unittest 覆盖；CONFIG 为共享 dict 引用 |
| `settings_win.py` | 纯 ctypes Win32 单页分组设置窗口 | 三层滚动容器 + 音色枚举 + 试听 + 单例；只依赖 config_store |
| `start.bat` | 一键后台启动（pythonw 无窗口） | 含进程自检 |
| `start_debug.bat` | 前台调试启动（显示报错） | |
| `test_popup.py` / `test_popup.bat` | 弹窗+TTS 独立测试（交互式） | |
| `test_config_store.py` | 配置层自动化单元测试（67 项，全绿） | `python -m unittest tests.test_config_store` |
| `test_settings.py` / `test_settings.bat` | 设置窗口交互式测试（GUI+试听） | 保存重定向到临时文件，不污染真实 config.json |
| `app.ico` | 红黄托盘图标 | Pillow 生成 |
| `broadcast_log.csv` | 播报台账（自动留痕） | 已含 9/1 三次试播记录 |
| `debug.log` | 运行日志落盘 | |
| `README.md` | 使用与排障说明 | |
| `.venv/` | 虚拟环境（comtypes 等依赖） | |
| `__pycache__/` | Python 字节码缓存 | |

## 3. 核心逻辑速览

- **调度规则**（`broadcast.py`）：
  - 每日：教学日放学前 1 分钟（16:29）
  - 周五：放学前 5 分钟（16:25）
  - 节假日：**假期前最后一个教学日**放学前 30 分钟（16:00）
  - 假期当天 / 周末 / 已过放学时间 → 不播
- **弹窗**：全屏红底（#B01E1E）+ 黄色标题 + 白色正文，Always-On-Top，不抢焦点，朗读完自动关闭
- **TTS**：`comtypes` 直连 SAPI5 **同步 Speak**，每次播报新建 SpVoice；
  不用 pyttsx3 的 say/runAndWait（其异步事件循环同一进程内只有第一次出声，
  复用 engine 会被 endLoop→stop() 的 `Speak("", PURGE)` 清掉、每句新建 engine
  也无效——踩坑 #6 的最终版结论）。语速配置仍是 wpm，经 `_wpm_to_sapi_rate`
  换算为 SAPI Rate(-10..10)，语义与 pyttsx3 时代一致
- **托盘**：右键菜单"立即试播"三种内容；左键/双击不触发任何动作（防误触）
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
6. **pyttsx3 持久 engine 状态残留**：首次 `runAndWait` 后再次调用无声音。曾改为"每次播报新建 engine"，**2026-09-08 实测仍只出声第一句**（用户 bug 反馈：只听到"同学们"）：探针证实同一进程内只有第一次**异步** Speak 出声——复用 engine 时第二句被 endLoop→stop() 的 `Speak("", PURGE)` 瞬间清掉（无异常、0.1s 即返回），每句新建 engine 也一样哑。而原生 SAPI **同步** `Speak` 连念 4 句全部正常（每句 1.5~2.5s）。最终方案：**弃用 pyttsx3 朗读路径，comtypes 直连 SAPI5 同步 Speak**（pyttsx3 依赖已从 requirements 移除）。
7. **ctypes 结构体/argtypes 声明顺序**：`WNDCLASSEXW` 定义在文件后部但 argtypes 在前面引用会失败；`RegisterClassExW` argtypes 用 `c_void_p` 传 `byref(wc)` 类型不匹配；`CreateSolidBrush` 缺 argtypes。正确顺序：**常量 → 结构体 → API 签名 → 逻辑**。
8. **bat 里 `python -c` 传中文参数**：cmd 引号 + 中文混用极易出错。应改用独立 `.py` 测试文件。
9. **Win32 消息循环必须在创建窗口的线程跑**：弹窗放子线程（daemon），TTS 放主线程，主线程先 `sleep(0.3)` 等弹窗线程起来。
10. **节假日规则初始 bug**：原逻辑在"当天是假期"才触发，但放假当天已停课；改为"**假期前最后一个教学日**"触发。
11. **Win11 隐藏图标区**：新托盘图标可能被收进任务栏右下角 ∧ 隐藏区，需点开查看（README 已注明）。
12. **窗口类每进程只注册一次 → WNDPROC 必须模块级持有**：`RegisterClassExW` 同名类第二次注册会失败，故 `settings_win` 的类只注册一次，其 `lpfnWndProc` 必须指向**模块级** `_GLOBAL_WNDPROC`（永不回收），再由它转发到模块级 `_CURRENT` 当前实例。若直接把某个 `SettingsWindow` 实例方法绑成回调，第一个窗口关闭、实例被 GC 后，第二次开窗复用同一个类 → 回调指向已回收对象 → 崩溃。已用「连开两次窗口」探针验证第二次不崩。
13. **COMBOBOX 的 nHeight 是「收起+下拉」总高，但系统会把窗口矩形裁到收起高度**：`CBS_DROPDOWNLIST` 创建时传的 nHeight 需含下拉展开高度（否则列表只显示一两行），但实测窗口占位/命中测试只按收起高度算，**不会遮挡下方控件**，无需 z-order 特殊处理。下拉展开时临时覆盖属正常行为。
14. **纯 ctypes 下只用 user32 标准控件，不初始化 COMCTL32**：`STATIC/EDIT/BUTTON(含 BS_GROUPBOX、BS_AUTOCHECKBOX)/COMBOBOX` 用 `CreateWindowExW` 直接可建；而 `SysTabControl32`/Trackbar/ListView 属公共控件，须 `InitCommonControlsEx` 注册 `ICC_*` 类，坑多且与现有 DPI 感知易冲突。故设置窗口选「单页分组+滚动」而非标签页，语速/音量/字号用**预置档位下拉框**而非滑块，从根本上消除非法输入。
15. **滚动用「容器整体平移」而非逐个 MoveWindow**：主窗口内建 `hwnd_view`(带 WS_VSCROLL|WS_CLIPCHILDREN) → `hwnd_content`(全高) 两层，所有表单控件挂在 content 上；滚动只 `MoveWindow` content 一次。底部按钮挂在主窗口、位于 view 之外，故固定不滚动。`WS_CLIPCHILDREN` 保证移出可视区的内容不被绘制/命中。
16. **WNDPROC/DefWindowProcW 的 restype 用 `c_ssize_t`（LONG_PTR）而非 `c_long`**：`WM_CTLCOLORSTATIC` 要返回画刷句柄（HBRUSH），64 位下 `c_long` 会截断句柄高位导致崩溃或花屏。注意这与 `popup_win.py` 用 `c_long` 不同——弹窗不处理返回句柄的消息，设置窗口处理。
17. **`WindowsApps\python.exe` 占位符再次踩中**：本机 `python` 仍解析到微软商店占位符（`python --version` 无输出、脚本静默不执行）。运行测试/探针必须用项目内 `.venv\Scripts\python.exe`（或 `~\.workbuddy\binaries\python\versions\3.13.12\python.exe`）。另：控制台代码页为 GBK，Python 输出中文会乱码，取证时重定向到文件再读。
18. **本机 user32!DrawTextW 被端点钩子废掉**：`DrawTextW(DT_CALCRECT|DT_WORDBREAK)` 的 `uFormat` 被忽略、RECT 永不回写（限宽 50 仍报单行；DrawTextExW、内存 DC、独立/共享 DLL 实例均同），而 gdi32 的 `GetTextExtentPoint32W` 完全正常。故 `settings_win.py` 实测换行高改用**逐 token 贪心断行模拟**（空白可断、CJK 单字可断、拉丁串整体不可断）× 单行高，**不要再尝试 DrawText 系 API 做文本度量**。
19. **`max_pos==0` 的滚动条是"拖不动的满轨惰性条"**：`SetScrollInfo` 的 `nPage >= nMax+1` 时系统把拇指拉满整轨、无可滚范围，用户会以为控件坏了。`_update_scrollbar` 必须按现实 `ShowScrollBar(hwnd, SB_VERT, max_pos>0)` 显隐。另：写无头探针时须 `settings_win._CURRENT = inst`，否则 WM_SIZE 等消息经模块级转发落入 DefWindowProc，会误判"压矮不重排"。

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

- 无远程推送、无服务端地址配置（V1.0 内网服务端尚未开发，设置界面暂不放该字段以免困惑）。
- 开机自启（P2）已交付：`autostart.py` 写 HKCU Run 键（免提权，注册表为唯一事实来源，不进 config.json）；设置界面「启动」分组勾选即写/删。单实例互斥在 `broadcast.main()` 入口用 CreateMutexW（`Local\SafetyCastSingleInstance`），重复启动弹提示后退出。按用户决策：不做任务计划兜底、不做崩溃自恢复。
- 设置界面（P1）已交付：托盘右键「设置…」→ 单页分组窗口，保存即热生效、无需重启。热生效链路三处：① `config_store.apply()` 对 CONFIG 原地 `clear()+update()`；② `broadcast._config_dirty` 脏标记，`scheduler_loop` 在 15s 轮询内检测到则置 `next_broadcast=None` 强制重算；③ `popup_win.show_popup(font_scale=...)` 下次播报采用新字号。
- PRD 规划的 V1.0（内网服务端版）尚未开始：内网 REST 服务端（FastAPI+SQLite）、教师端 Web 页面、台账同步等。小程序本阶段不考虑。
- MVP 待办优先级：P0 DPI自适应+弹窗改进（已完成）→ P1 设置界面+配置扩展（已完成）→ P2 开机自启（已完成）+Excel导出 → P3 exe打包（已完成：`script/build_exe.bat`，PyInstaller **onefile**+windowed 单文件约 7MB；可写数据统一在 `%APPDATA%\SafetyCast\`（`config_store.DATA_DIR`，冻结态解析+旧 exe-旁数据一次性迁移、只拷不删不覆盖；开发态仍用项目目录随仓库走）；app.ico 经 --add-data 内嵌、exe 旁同名文件优先；曾在 onedir 模式下用户单独搬动 exe 报「找不到 python313.dll」，onedir 必须整文件夹搬运，故改 onefile）。
- 修改弹窗/托盘代码时，保持"结构体在前、argtypes 在后"的书写顺序。
