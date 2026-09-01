# -*- coding: utf-8 -*-
"""
课堂安全播报助手 MVP
- 1530 定时播报：每日放学前 1 分钟 / 周五放学前 5 分钟 / 节假日放假前 30 分钟
- 红底大字全屏置顶弹窗 + TTS 朗读（pyttsx3，离线）
- 托盘常驻（pystray），支持立即试播
- 日志留痕：broadcast_log.csv
"""

import csv
import datetime
import json
import os
import queue
import sys
import threading
import time
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "log", "broadcast_log.csv")
DEBUG_LOG = os.path.join(BASE_DIR, "log", "debug.log")

def _dbg(msg: str):
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass

def fatal(msg: str):
    _dbg("FATAL: " + msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "课堂安全播报助手 - 启动失败", 0)
    except Exception:
        pass

# 延迟导入并捕获依赖缺失
try:
    import pyttsx3
except Exception as e:
    fatal(f"依赖缺失，请先安装：pip install pyttsx3\n\n{e}")
    raise SystemExit(1)

try:
    from tray_win import TrayApp
except Exception as e:
    fatal(f"托盘模块加载失败：{e}\n{traceback.format_exc()}")
    raise SystemExit(1)

ICO_PATH = os.path.join(BASE_DIR, "app.ico")

try:
    import content as content_lib
except Exception as e:
    fatal(f"加载内容模板失败：{e}\n{traceback.format_exc()}")
    raise SystemExit(1)

# ---------- 配置 ----------

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

def dismissal_datetime(now: datetime.datetime) -> datetime.datetime:
    h, m = CONFIG["dismissal_time"].split(":")
    return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)

def is_holiday(date, holidays):
    return any(str(date) == str(h) for h in holidays)

def is_last_school_day_before_holiday(date, holidays):
    """今天是教学日且明天是假期 → 今天是假期前最后一个教学日。"""
    tomorrow = date + datetime.timedelta(days=1)
    return not is_holiday(date, holidays) and is_holiday(tomorrow, holidays)

def today_broadcast_time(now: datetime.datetime) -> datetime.datetime | None:
    """返回今天应触发的播报时间；若已错过返回 None。"""
    rules = CONFIG["rules"]
    holidays = CONFIG.get("upcoming_holidays", [])
    if is_holiday(now.date(), holidays):
        return None  # 假期当天停课不播
    base = dismissal_datetime(now)
    weekend = now.weekday() >= 5
    holiday = is_last_school_day_before_holiday(now.date(), holidays)

    # 优先级：假期前最后教学日 > 周五 > 每日；周末不播每日
    if holiday and rules["holiday"]["enabled"]:
        t = base - datetime.timedelta(minutes=rules["holiday"]["lead_minutes"])
        kind = "holiday"
    elif now.weekday() == 4 and rules["friday"]["enabled"]:
        t = base - datetime.timedelta(minutes=rules["friday"]["lead_minutes"])
        kind = "friday"
    elif not weekend and rules["daily"]["enabled"]:
        t = base - datetime.timedelta(minutes=rules["daily"]["lead_minutes"])
        kind = "daily"
    else:
        return None
    return t if t > now else None

def pick_content(kind: str, now: datetime.datetime) -> tuple[str, str]:
    rules = CONFIG["rules"]
    if kind == "holiday":
        return rules["holiday"]["title"], content_lib.get_holiday_content(now, CONFIG.get("upcoming_holidays", []))
    if kind == "friday":
        return rules["friday"]["title"], content_lib.get_friday_content()
    return rules["daily"]["title"], content_lib.get_daily_content(now)

# ---------- 日志 ----------

def log_broadcast(kind: str, title: str, text: str, status: str):
    new_file = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["时间", "类型", "标题", "内容", "状态"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kind, title, text.replace("\n", " "), status])
    print(f"[log] {kind} {status}: {title}")

# ---------- TTS ----------

def speak(text: str):
    """每次新建 engine 避免 runAndWait 状态残留导致后续静音。"""
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", CONFIG["tts_rate"])
        engine.setProperty("volume", CONFIG["tts_volume"])
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        _dbg(f"[tts] failed: {e}")
        print(f"[tts] failed: {e}")

# ---------- 弹窗 ----------

from popup_win import show_popup as _show_popup

def show_popup(title: str, text: str):
    """全屏红底弹窗，阻塞直到关闭（Win32 消息循环必须在创建它的线程跑）。"""
    hold = CONFIG.get("popup_min_hold_seconds", 12)
    _show_popup(title, text, hold_seconds=hold)

# ---------- 播报执行 ----------

def do_broadcast(kind: str):
    now = datetime.datetime.now()
    title, text = pick_content(kind, now)
    log_broadcast(kind, title, text, "开始")
    # 弹窗在子线程跑（阻塞，但只阻塞该子线程）
    popup_thread = threading.Thread(
        target=show_popup, args=(title, text), daemon=True)
    popup_thread.start()
    # TTS 在主线程同步播放（等弹窗线程先起来）
    time.sleep(0.3)
    speak(text)
    log_broadcast(kind, title, text, "已播报")

# ---------- 调度器 ----------

_scheduler_stop = threading.Event()

def scheduler_loop():
    next_broadcast = None
    while not _scheduler_stop.is_set():
        now = datetime.datetime.now()
        if next_broadcast is None or now.date() != next_broadcast.date() or now < next_broadcast - datetime.timedelta(hours=20):
            t = today_broadcast_time(now)
            if t is None:
                # 计算明天再查，避免忙等
                tomorrow = now + datetime.timedelta(days=1)
                tomorrow = tomorrow.replace(hour=0, minute=5, second=0, microsecond=0)
                next_broadcast = None
                wait = (tomorrow - now).total_seconds()
                print(f"[scheduler] 今日无播报，{tomorrow} 再检查")
                _scheduler_stop.wait(min(wait, 3600))
                continue
            next_broadcast = t
            print(f"[scheduler] 下次播报: {next_broadcast} ({CONFIG['dismissal_time']} 放学)")
        if now >= next_broadcast:
            # 判断类型并执行（假期当天不播）
            holidays = CONFIG.get("upcoming_holidays", [])
            if is_holiday(now.date(), holidays):
                print("[scheduler] 今天是假期，不播报")
                tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
                next_broadcast = tomorrow
                continue
            if is_last_school_day_before_holiday(now.date(), holidays):
                kind = "holiday"
            elif now.weekday() == 4:
                kind = "friday"
            else:
                kind = "daily"
            do_broadcast(kind)
            tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
            next_broadcast = tomorrow
            continue
        _scheduler_stop.wait(15)

# ---------- 托盘 ----------

_TRAY: TrayApp | None = None

def build_tray() -> TrayApp:
    def quit_app():
        _scheduler_stop.set()
        if _TRAY:
            _TRAY.stop()

    menu_items = [
        (1, "立即试播：每日 1 分钟提醒", lambda: do_broadcast("daily")),
        (2, "立即试播：周五 5 分钟专题", lambda: do_broadcast("friday")),
        (3, "立即试播：节假日 30 分钟专题", lambda: do_broadcast("holiday")),
        (0, "", None),
        (9, "退出", quit_app),
    ]
    return TrayApp(ICO_PATH, "课堂安全播报助手", menu_items,
                   on_click=lambda: do_broadcast("daily"))

# ---------- 入口 ----------

def main():
    global _TRAY
    _dbg("main() 开始")
    try:
        threading.Thread(target=scheduler_loop, daemon=True).start()
        _dbg("scheduler_loop 已启动，放学时间=" + CONFIG["dismissal_time"])
        _TRAY = build_tray()
        _dbg("托盘对象已创建，开始 run()")
        _TRAY.run()  # 阻塞，直到退出
        _dbg("托盘 run() 返回，程序退出")
    except Exception:
        fatal(traceback.format_exc())

if __name__ == "__main__":
    _dbg("进程启动，python=" + sys.executable)
    main()
