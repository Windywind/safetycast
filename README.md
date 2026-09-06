# 课堂安全播报助手 MVP

Windows 桌面驻留的 1530 安全教育自动播报工具（单机离线版）。

## 运行环境

- **操作系统**：Windows 10/11（64 位）
- **Python**：3.8+（推荐 3.10-3.13），需从 [python.org](https://python.org) 安装，安装时勾选 "Add Python to PATH"
- **无需联网**：全离线运行，仅在首次安装依赖时需要联网

## 快速开始

### 首次使用（安装依赖）

1. 确认系统已安装 Python 并加入 PATH。
2. 双击 `script/setup.bat`，等待依赖安装完成。

### 日常启动

1. 双击 `script/start.bat` 启动（后台运行，看托盘区红黄图标）。
2. **图标可能被 Win11 收进隐藏图标区**：点任务栏右下角的 **∧（向上箭头）**，在展开区里找红底黄心的图标。
3. 右键托盘图标可"立即试播"三种提醒（双击图标 = 立即播每日提醒）：
   - 每日 1 分钟提醒（红底大字 + 语音朗读）
   - 周五 5 分钟专题
   - 节假日 30 分钟专题

> 启动脚本会自动检测 Python：优先使用项目内 `.venv`（若存在），否则使用系统 Python。无需手动指定路径。

## 排障

- 看不到图标：检查 `log/debug.log` 最后一行是否为"托盘对象已创建，开始 run()"；没有则说明启动失败（会弹错误框）。
- 确认进程：任务管理器里应有 `pythonw.exe`；若启动失败，`script/start_debug.bat` 会显示错误弹窗。
- 托盘实现：`tray_win.py`（纯 Win32 API，不依赖 pystray，兼容 Python 3.13/Win11）。

## 自动播报规则（config.json）

- 放学时间：`dismissal_time`（默认 16:30）
- 每日：放学前 1 分钟（按星期轮换内容）
- 周五：放学前 5 分钟
- 节假日：假期前最后一个教学日，放学前 30 分钟（在 `upcoming_holidays` 里配置假期日期，格式 `YYYY-MM-DD`）

## 文件说明

| 文件 | 作用 |
|------|------|
| `broadcast.py` | 主程序：调度器 + 弹窗 + TTS + 托盘 |
| `content.py` | 安全提醒内容模板库（可随意增改） |
| `popup_win.py` | 纯 ctypes Win32 全屏红底弹窗 |
| `tray_win.py` | 纯 ctypes Win32 托盘模块 |
| `config.json` | 配置（放学时间、规则开关、假期列表） |
| `app.ico` | 托盘图标 |
| `requirements.txt` | Python 依赖清单 |
| `script/setup.bat` | 首次安装依赖（pip install -r requirements.txt） |
| `script/start.bat` | 一键启动（pythonw 无窗口后台运行） |
| `script/start_debug.bat` | 前台调试启动（显示报错） |
| `log/broadcast_log.csv` | 播报台账（自动记录，已 gitignore） |
| `log/debug.log` | 调试日志（已 gitignore） |
| `tests/test_popup.py` | 弹窗 + TTS 独立测试 |
| `tests/test_popup.bat` | 测试启动脚本 |
| `docs/PROJECT_CONTEXT.md` | 项目背景与踩坑记录 |
| `docs/课堂安全播报助手-PRD.md` | 产品需求文档 |
| `docs/broadcast_log_sample.csv` | 台账样例 |

## 说明与限制

- 弹窗全屏红底置顶、朗读完自动关闭，也可点屏幕或按 ESC 关闭。
- 全离线运行，不联网。
- MVP 限制：无远程推送（手机端）、无开机自启注册、无设置界面（改 config.json 后重启生效）。

## 交付部署

1. 将整个项目文件夹拷贝到目标机器。
2. 确认目标机器已安装 Python（3.8+），并加入了系统 PATH。
3. 双击 `script/setup.bat` 安装依赖。
4. 双击 `script/start.bat` 启动程序。

> 若目标机器有 `.venv`，启动脚本会优先使用它；没有则自动使用系统 Python，无需额外配置。
