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
import re
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

_tts_stop = threading.Event()   # 置位后立刻停止朗读
# 分句粒度决定「关窗后多久静音」。只按句号切分的话，
# 防溺水那种 90 字长句要念 30 秒才停 —— 故逗号/冒号也切。
# 不切「、」：它常用于「一、」「二、」这类序号，切开会被念成孤字。
_SENT_SPLIT = re.compile(r"(?<=[。！？；，：!?;,:])")


def _split_sentences(text: str):
    """按标点细切成 3~20 字的小段。

    pyttsx3 的 runAndWait 无法从外部可靠打断，只能逐段播放、
    段间检查停止标志，最坏延迟一段（约 5 秒）。
    """
    out = []
    for s in _SENT_SPLIT.split(text):
        s = s.strip()
        if not s:
            continue
        # 换行也切开，避免一段里夹着多个自然段
        for part in s.split("\n"):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _new_engine():
    engine = pyttsx3.init()
    engine.setProperty("rate", CONFIG["tts_rate"])
    engine.setProperty("volume", CONFIG["tts_volume"])
    return engine


def _safe_stop(engine):
    if engine is not None:
        try:
            engine.stop()
        except Exception:
            pass
    return None


def speak(text: str, stop_event: threading.Event | None = None):
    """逐句朗读；stop_event 置位后立刻停止（关窗即静音）。"""
    stop = stop_event if stop_event is not None else threading.Event()
    engine = None
    try:
        for sent in _split_sentences(text):
            if stop.is_set():
                break
            try:
                if engine is None:
                    engine = _new_engine()
                engine.say(sent)
                engine.runAndWait()
            except Exception as e:
                # runAndWait 状态残留时重建 engine 重试本句
                _dbg(f"[tts] retry with new engine: {e}")
                engine = _safe_stop(engine)
                if stop.is_set():
                    break
                try:
                    engine = _new_engine()
                    engine.say(sent)
                    engine.runAndWait()
                except Exception as e2:
                    _dbg(f"[tts] sentence dropped: {e2}")
                    engine = _safe_stop(engine)
    finally:
        _safe_stop(engine)


def stop_speaking():
    """请求停止当前语音播报。"""
    _tts_stop.set()

# ---------- 弹窗 ----------

from popup_win import show_popup as _show_popup, close_popup as _close_popup

# ---------- 播报执行 ----------

_broadcast_lock = threading.Lock()


def do_broadcast(kind: str):
    """执行一次播报。同一时刻只允许一个（托盘单击会连发多条鼠标消息）。

    必须在工作线程中调用：内部会阻塞跑弹窗消息循环 + 等 TTS 念完。
    """
    if not _broadcast_lock.acquire(blocking=False):
        _dbg(f"[broadcast] 已有播报在进行，忽略本次 {kind}")
        return
    try:
        _run_broadcast(kind)
    except Exception:
        _dbg("[broadcast] error:\n" + traceback.format_exc())
    finally:
        _broadcast_lock.release()


def _run_broadcast(kind: str):
    now = datetime.datetime.now()
    title, text = pick_content(kind, now)
    log_broadcast(kind, title, text, "开始")

    _tts_stop.clear()
    tts_done = threading.Event()

    def _tts_worker():
        try:
            speak(text, _tts_stop)
        finally:
            tts_done.set()

    tts_thread = threading.Thread(target=_tts_worker, daemon=True)
    tts_thread.start()

    hold = CONFIG.get("popup_min_hold_seconds", 12)
    manual_close = True
    try:
        # 弹窗在当前工作线程跑消息循环；TTS 念完且到最短停留时间才自动关闭
        _show_popup(title, text, hold_seconds=hold,
                    should_close=tts_done.is_set)
    finally:
        manual_close = not tts_done.is_set()
        _tts_stop.set()                 # 关窗（× / ESC）后立即停音
        _close_popup()                  # 兜底：确保弹窗已销毁
        # 最长一段约 20 字 ≈ 7 秒语音，留足余量等它自然收尾
        tts_thread.join(timeout=12)

    log_broadcast(kind, title, text, "手动关闭" if manual_close else "已播报")

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

def _hard_exit_watchdog(seconds: float = 4.0):
    """兜底：若正常退出流程卡住，强制结束进程，杜绝残留 pythonw/pyw。"""
    def _watch():
        time.sleep(seconds)
        _dbg(f"[quit] {seconds}s 内未正常退出，强制 os._exit(0)")
        os._exit(0)
    threading.Thread(target=_watch, daemon=True).start()


def quit_app():
    """彻底退出：停调度 → 停语音 → 关弹窗 → 停托盘 → 兜底强杀。"""
    _dbg("[quit] 开始退出")
    _hard_exit_watchdog(4.0)
    _scheduler_stop.set()
    _tts_stop.set()          # 立刻停止后台语音
    try:
        _close_popup()       # 关闭可能还开着的全屏弹窗
    except Exception:
        pass
    if _TRAY:
        try:
            _TRAY.stop()
        except Exception:
            _dbg("[quit] tray.stop 异常:\n" + traceback.format_exc())


def build_tray() -> TrayApp:
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
    finally:
        # 确保语音停止、弹窗关闭，并且进程一定结束（不残留 pythonw/pyw）
        _tts_stop.set()
        try:
            _close_popup()
        except Exception:
            pass
        # 给正在收尾的播报线程一点时间把日志写完
        _broadcast_lock.acquire(timeout=2.0)
        _dbg("进程结束，os._exit(0)")
        os._exit(0)

if __name__ == "__main__":
    _dbg("进程启动，python=" + sys.executable)
    main()
