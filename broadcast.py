# -*- coding: utf-8 -*-
"""
课堂安全播报助手 MVP
- 1530 定时播报：每日放学前 1 分钟 / 周五放学前 5 分钟 / 节假日放假前 30 分钟
- 红底大字全屏置顶弹窗 + TTS 朗读（SAPI5 同步 Speak，离线）
- 托盘常驻（pystray），支持立即试播
- 日志留痕：broadcast_log.csv
"""

import contextlib
import csv
import ctypes
import datetime
import math
import os
import queue
import re
import sys
import threading
import time
import traceback

import config_store

# PyInstaller 冻结态下 __file__ 指向临时解压目录（_MEIPASS），可写数据
# （config.json、log/）必须锚定 exe 所在目录，否则每次启动写到不同的临时目录。
BASE_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "log", "broadcast_log.csv")
DEBUG_LOG = os.path.join(BASE_DIR, "log", "debug.log")

# 绿色解压的 dist 目录里没有 log/，启动时先建好（目录不可写时各写日志点
# 自行容错，不挡启动）
try:
    os.makedirs(os.path.join(BASE_DIR, "log"), exist_ok=True)
except Exception:
    pass

# 托盘/设置窗图标：exe 旁的 app.ico 优先（老师可自行替换定制），没有则用
# onefile 打包内嵌的副本（--add-data 解到 sys._MEIPASS，每次运行路径都不同）
if getattr(sys, "frozen", False):
    _ico_beside = os.path.join(BASE_DIR, "app.ico")
    ICO_PATH = (_ico_beside if os.path.exists(_ico_beside)
                else os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "app.ico"))
else:
    ICO_PATH = os.path.join(BASE_DIR, "app.ico")

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
    # 朗读与音色枚举都直接走 SAPI5 COM，不再使用 pyttsx3 的 say/runAndWait：
    # 其异步事件循环实测同一进程内只有第一次出声（详见 speak() 内注释）。
    import comtypes.client
except Exception as e:
    fatal(f"依赖缺失，请先安装：pip install comtypes\n\n{e}")
    raise SystemExit(1)

try:
    from tray_win import TrayApp
except Exception as e:
    fatal(f"托盘模块加载失败：{e}\n{traceback.format_exc()}")
    raise SystemExit(1)

try:
    import content as content_lib
except Exception as e:
    fatal(f"加载内容模板失败：{e}\n{traceback.format_exc()}")
    raise SystemExit(1)

# ---------- 配置 ----------
# CONFIG 是 config_store.CONFIG 的**同一个 dict 对象引用**。设置界面保存后
# config_store.apply() 原地 clear()+update()，因此本文件所有「调用时取值」的
# 读取点（dismissal_datetime / today_broadcast_time / pick_content / _new_engine /
# _run_broadcast / scheduler_loop）一行都不用改即可热生效。
# 加载失败（文件缺失、JSON 损坏）由 config_store.load 容错回退默认值并经 _dbg 上报，
# 不再像旧 load_config 那样直接抛异常导致启动 fatal。
CONFIG = config_store.CONFIG
config_store.apply(config_store.load(CONFIG_PATH, on_error=_dbg))

# 配置变更信号：置位后调度器在下一个轮询周期强制重算下次播报时间。
# 必须有它 —— scheduler_loop 把 next_broadcast 缓存在局部变量，其重算条件
# （跨日 / 提前 20 小时内）在「只改了放学时间」时三个分支全不成立，
# 旧时刻会一直挂到跨日才更新。
_config_dirty = threading.Event()


def apply_config(clean: dict):
    """落盘 + 原地热应用 + 通知调度器重算。由设置窗口「保存并生效」调用。

    入参 clean 已由 config_store.validate() 校验并归一，保证合法且类型正确。
    先落盘再改内存：落盘失败时异常抛回调用方提示，内存配置保持不变，
    不会出现「界面说已生效、重启后又变回去」的不一致。
    """
    config_store.save_atomic(CONFIG_PATH, clean)
    config_store.apply(clean)
    _config_dirty.set()
    voice = clean.get("tts_voice") or {}
    _dbg(f"[config] 已保存并热生效：放学 {clean.get('dismissal_time')}，"
         f"字号 {clean.get('popup_font_scale')}%，语速 {clean.get('tts_rate')}，"
         f"音量 {clean.get('tts_volume')}，音色 {voice.get('name') or '系统默认'}")

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
# 试听专用停止信号，与 _tts_stop 分开：设置界面点「停止试听」不得影响正在进行的
# 正式播报，反之正式播报的关窗静音也不该误杀试听。
_preview_stop = threading.Event()
# 分句粒度决定「关窗后多久静音」。只按句号切分的话，
# 防溺水那种 90 字长句要念 30 秒才停 —— 故逗号/冒号也切。
# 不切「、」：它常用于「一、」「二、」这类序号，切开会被念成孤字。
_SENT_SPLIT = re.compile(r"(?<=[。！？；，：!?;,:])")


def _split_sentences(text: str):
    """按标点细切成 3~20 字的小段。

    SAPI 同步 Speak 无法从外部打断，只能逐段播放、
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


@contextlib.contextmanager
def _com_apartment():
    """在本线程初始化 COM 并在退出时配对 CoUninitialize。

    comtypes 直连 SAPI 的前提是每个使用线程都调过 CoInitialize ——
    这步以前由 pyttsx3 的 sapi5 驱动在内部代做，绕开它之后必须自己调用，
    否则播报工作线程里 CreateObject/Speak 全部抛
    CO_E_NOTINITIALIZED（0x800401F0，「尚未调用 CoInitialize」），
    整段播报静音（2026-09-08 用户实测）。
    CoInitialize 按线程引用计数，已初始化时返回 S_FALSE 也算一次成功调用，
    因此进入/退出成对调用即可，重复嵌套安全。
    """
    comtypes.CoInitialize()
    try:
        yield
    finally:
        comtypes.CoUninitialize()


# 音色枚举结果缓存：SAPI 枚举要新建一个 SpVoice（数十毫秒级），
# 而 _new_voice 每次播报都会调它，不缓存会平白多创建一个 COM 对象。
_voices_cache: list | None = None
_voices_lock = threading.Lock()


def list_voices() -> list:
    """枚举本机已安装的系统 TTS 语音，返回 [(id, name), ...]，结果按进程缓存。

    id 在 Windows SAPI5 下是注册表路径（如
    HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Speech\\Voices\\Tokens\\TTS_MS_ZH-CN_HUIHUI_11.0），
    换机器后可能失效 —— 故同时返回 name 供界面显示与日志提示。
    枚举失败返回空列表，绝不抛异常（设置界面据此只显示「系统默认」一项）。
    """
    global _voices_cache
    with _voices_lock:
        if _voices_cache is not None:
            return list(_voices_cache)

    voices = []
    try:
        with _com_apartment():
            tts = comtypes.client.CreateObject("SAPI.SpVoice")
            for token in tts.GetVoices():
                vid = str(token.Id or "").strip()
                if not vid:
                    continue
                try:
                    name = str(token.GetDescription() or "").strip()
                except Exception:
                    name = ""
                voices.append((vid, name or vid))
    except Exception as e:
        _dbg(f"[tts] 枚举音色失败，仅提供系统默认：{e}")

    with _voices_lock:
        _voices_cache = voices
    _dbg(f"[tts] 本机可用音色 {len(voices)} 个")
    return list(voices)


# wpm → SAPI Rate(-10..10) 换算基准，与 pyttsx3 sapi5 驱动的注册表默认值一致
# （E_REG ...\\MSMARY：a=156.63，b=1.11），保证既有 tts_rate 配置语义不变：
# Rate = log(wpm / a) / log(b)，如 175wpm ≈ Rate 1。
_RATE_A, _RATE_B = 156.63, 1.11


def _wpm_to_sapi_rate(wpm) -> int:
    try:
        r = int(math.log(max(1.0, float(wpm)) / _RATE_A) / math.log(_RATE_B))
    except (TypeError, ValueError):
        r = 0
    return max(-10, min(10, r))


def _new_voice(rate: int | None = None, volume: float | None = None,
               voice_id: str | None = None):
    """新建 SAPI SpVoice 并套用参数；参数为 None 时取 CONFIG 当前值。

    每次播报都新建（踩坑 #6），CONFIG 热更新后的语速/音量/音色下次播报自然生效。

    音色容错：配置的 id 不在本机枚举结果中时**跳过**音色选择（回落系统默认）
    并记一行日志，绝不抛异常 —— 教室换机器后语音包不同是常态，
    而任何异常都可能导致整次安全播报静音。
    """
    if voice_id is None:
        configured = CONFIG.get("tts_voice") or {}
        wanted = str(configured.get("id") or "").strip()
        label = str(configured.get("name") or "").strip() or wanted
    else:
        wanted = str(voice_id or "").strip()
        label = wanted

    if wanted and wanted not in {vid for vid, _ in list_voices()}:
        _dbg(f"[tts] 音色本机不存在（{label}），改用系统默认")
        wanted = ""

    tts = comtypes.client.CreateObject("SAPI.SpVoice")
    tts.Rate = _wpm_to_sapi_rate(CONFIG["tts_rate"] if rate is None else rate)
    try:
        vol = float(CONFIG["tts_volume"] if volume is None else volume)
    except (TypeError, ValueError):
        vol = 1.0
    tts.Volume = max(0, min(100, int(round(vol * 100))))
    if wanted:
        try:
            for token in tts.GetVoices():
                if str(token.Id or "") == wanted:
                    tts.Voice = token
                    break
            else:
                _dbg(f"[tts] 未找到音色 token（{label}），改用系统默认")
        except Exception as e:
            _dbg(f"[tts] 设置音色失败（{label}），改用系统默认：{e}")
    return tts


def speak(text: str, stop_event: threading.Event | None = None,
          rate: int | None = None, volume: float | None = None,
          voice_id: str | None = None):
    """逐句朗读（SAPI 同步 Speak）；stop_event 置位后返回（关窗即静音）。

    **为什么不用 pyttsx3 的 say/runAndWait**（2026-09-08 用户实测 bug：
    只能听到「同学们」，后续全部无声且无任何异常）：
    - 复用同一 engine：runAndWait 结束时 proxy.setBusy(False)，下一句 say()
      因不忙而立即异步开念；随后 runAndWait 入队的 endLoop 在 startLoop 首轮
      又被立即泵出 → driver.stop() → Speak("", PURGEBEFORESPEAK) 把刚开念的
      语音瞬间清掉（探针实测：第 1 句 1.4s，第 2~4 句全部 0.1s 即返回）。
    - 每句新建 engine 也无效：同一进程内只有第一次异步 Speak 出声（探针实测）。
    - 原生 SAPI **同步** Speak 连念 4 句全部出声（每句 1.5~2.5s），故直连 comtypes。

    同步 Speak 会阻塞到本句念完，句间检查停止标志，最坏延迟一段（约 5 秒），
    与原设计一致。rate / volume / voice_id 为 None 时取 CONFIG 当前值；
    试听会显式传入设置界面表单里**尚未保存**的值，故不能走 CONFIG。
    """
    stop = stop_event if stop_event is not None else threading.Event()
    tts = None
    with _com_apartment():
        for i, sent in enumerate(_split_sentences(text)):
            if stop.is_set():
                break
            try:
                if tts is None:
                    tts = _new_voice(rate, volume, voice_id)
                tts.Speak(sent)
            except Exception as e:
                _dbg(f"[tts] 第 {i + 1} 句失败，重建 SpVoice 重试：{e}")
                tts = None
                if stop.is_set():
                    break
                try:
                    tts = _new_voice(rate, volume, voice_id)
                    tts.Speak(sent)
                except Exception as e2:
                    _dbg(f"[tts] sentence dropped: {e2}")
                    tts = None


def stop_speaking():
    """请求停止当前语音播报。"""
    _tts_stop.set()

# ---------- 弹窗 ----------

from popup_win import show_popup as _show_popup, close_popup as _close_popup

# ---------- 播报执行 ----------

_broadcast_lock = threading.Lock()
_preview_lock = threading.Lock()   # 试听单例：连点「试听」不得叠音


def is_broadcast_running() -> bool:
    """正式播报是否正在进行。设置界面据此置灰「试听」按钮。

    用非阻塞抢锁探测：抢到就立刻释放，说明当前没有播报在跑。
    """
    if _broadcast_lock.acquire(blocking=False):
        _broadcast_lock.release()
        return False
    return True


def preview_speak(text: str, rate: int, volume: float, voice_id: str,
                  stop_event: threading.Event) -> bool:
    """试听：用设置界面表单里**尚未保存**的参数念一段真实播报内容。

    刻意**不抢** _broadcast_lock —— 若抢了，老师试听期间到点的正式播报会被
    do_broadcast 的非阻塞 acquire 静默丢弃，那是安全事故。改为：
      · 播报正在进行时直接拒绝试听（界面已置灰按钮，这里是纵深防御）
      · 用独立的 _preview_lock 保证试听自身不叠音
    返回 True 表示确实念了，False 表示被跳过。
    """
    if is_broadcast_running():
        _dbg("[preview] 播报进行中，跳过试听")
        return False
    if not _preview_lock.acquire(blocking=False):
        _dbg("[preview] 已有试听在进行，忽略本次")
        return False
    try:
        speak(text, stop_event, rate=rate, volume=volume, voice_id=voice_id)
        return True
    except Exception:
        _dbg("[preview] error:\n" + traceback.format_exc())
        return False
    finally:
        _preview_lock.release()


def stop_preview():
    """请求停止试听（不影响正式播报）。"""
    _preview_stop.set()


def do_broadcast(kind: str):
    """执行一次播报。同一时刻只允许一个（菜单连点、调度到点与手动试播可能撞车）。

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
    # 字号档位存的是百分比，换算成 popup_win 的缩放系数。配置被手工写坏时回落 1.0，
    # show_popup 内部还会再夹一次范围 —— 双保险确保「配置写坏也绝不影响弹窗」。
    try:
        font_scale = float(CONFIG.get("popup_font_scale", 100)) / 100.0
    except (TypeError, ValueError):
        font_scale = 1.0
    manual_close = True
    try:
        # 弹窗在当前工作线程跑消息循环；TTS 念完且到最短停留时间才自动关闭
        _show_popup(title, text, hold_seconds=hold,
                    should_close=tts_done.is_set, font_scale=font_scale)
    finally:
        manual_close = not tts_done.is_set()
        _tts_stop.set()                 # 关窗（× / ESC）后立即停音
        _close_popup()                  # 兜底：确保弹窗已销毁
        # 最长一段约 20 字 ≈ 7 秒语音，留足余量等它自然收尾
        tts_thread.join(timeout=12)

    log_broadcast(kind, title, text, "手动关闭" if manual_close else "已播报")

# ---------- 调度器 ----------

_scheduler_stop = threading.Event()


def _scheduler_wait(seconds: float):
    """等待 seconds；期间收到停止信号或配置变更则提前返回。

    不能直接用 _scheduler_stop.wait(seconds)：「今日无播报」分支一次要等最长 1 小时，
    老师这期间改了放学时间的话，热生效会被拖到一小时后才收敛。这里改成 0.5 秒一片
    地等 _config_dirty —— apply_config 置位它时本函数立刻返回。
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while not _scheduler_stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if _config_dirty.wait(min(remaining, 0.5)):
            return


def scheduler_loop():
    next_broadcast = None
    while not _scheduler_stop.is_set():
        now = datetime.datetime.now()
        # 配置热更新后强制重算。next_broadcast 是局部缓存，其重算条件
        # （跨日 / 进入提前 20 小时窗口）在「只改了放学时间」时三个分支全不成立，
        # 不显式置 None 的话旧时刻会一直挂到跨日才更新。
        # apply_config 是先改 CONFIG 再 set 脏标记，故此处重算必定读到新配置。
        if _config_dirty.is_set():
            _config_dirty.clear()
            next_broadcast = None
            _dbg("[scheduler] 配置已变更，重算下次播报时间（放学 "
                 f"{CONFIG['dismissal_time']}）")
        if next_broadcast is None or now.date() != next_broadcast.date() or now < next_broadcast - datetime.timedelta(hours=20):
            t = today_broadcast_time(now)
            if t is None:
                # 计算明天再查，避免忙等
                tomorrow = now + datetime.timedelta(days=1)
                tomorrow = tomorrow.replace(hour=0, minute=5, second=0, microsecond=0)
                next_broadcast = None
                wait = (tomorrow - now).total_seconds()
                print(f"[scheduler] 今日无播报，{tomorrow} 再检查")
                _scheduler_wait(min(wait, 3600))
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
        _scheduler_wait(15)

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
    _preview_stop.set()      # 立刻停止设置界面的试听，避免退出后线程残留
    try:
        _close_popup()       # 关闭可能还开着的全屏弹窗
    except Exception:
        pass
    if _TRAY:
        try:
            _TRAY.stop()
        except Exception:
            _dbg("[quit] tray.stop 异常:\n" + traceback.format_exc())


def open_settings():
    """打开设置窗口。

    **延迟导入** settings_win：它要读配置、调 TTS，若与本模块在模块级互相 import
    即成环。改为由本函数注入 on_apply / preview_speak / list_voices / is_busy 四个
    回调，settings_win 只依赖 config_store，可独立测试，且不存在任何导入环。

    本函数由托盘 _fire() 在后台 daemon 线程调用 —— 正好满足「Win32 消息循环必须在
    创建窗口的线程跑」（踩坑 #9）：各线程 GetMessageW(hwnd=None) 只取本线程窗口消息，
    与托盘主线程、弹窗工作线程互不干扰。
    """
    try:
        import settings_win
    except Exception:
        _dbg("[settings] 模块导入失败:\n" + traceback.format_exc())
        return
    try:
        settings_win.show_settings(
            on_apply=apply_config,
            preview_speak=preview_speak,
            list_voices=list_voices,
            is_busy=is_broadcast_running,
        )
    except Exception:
        _dbg("[settings] error:\n" + traceback.format_exc())


def build_tray() -> TrayApp:
    menu_items = [
        (1, "立即试播：每日 1 分钟提醒", lambda: do_broadcast("daily")),
        (2, "立即试播：周五 5 分钟专题", lambda: do_broadcast("friday")),
        (3, "立即试播：节假日 30 分钟专题", lambda: do_broadcast("holiday")),
        # 按用户决定，设置入口不加任何防误触保护（不做连点 5 次 / 快捷键 / 密码）
        (4, "设置…", open_settings),
        (0, "", None),
        (9, "退出", quit_app),
    ]
    # 左键/双击不绑定任何动作（on_click 缺省 None）：误触托盘就开播是打扰
    # （2026-09-08 用户反馈），触发方式只保留右键菜单的显式条目。
    return TrayApp(ICO_PATH, "课堂安全播报助手", menu_items)

# ---------- 入口 ----------

# 单实例互斥句柄：必须活到进程结束，故放模块级全局（GC 提前释放会让第二个
# 实例误判可以启动）。进程退出时由系统回收，无需主动 CloseHandle。
_single_instance_mutex = None


def _acquire_single_instance() -> bool:
    """单实例互斥：已有一个实例在跑则返回 False。

    CreateMutexW 创建的是内核对象，跨进程可见（pythonw 双击、注册表自启
    都走不同进程）。用 Local\\ 会话级命名空间：教室机器单账号场景够用，
    Global\\ 需要额外权限且会跨远程桌面会话误拦。
    创建失败（极端情况）选择放行 —— 宁可双开也不挡住安全播报。
    """
    global _single_instance_mutex
    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, r"Local\SafetyCastSingleInstance")
    if not handle:
        _dbg("[main] CreateMutexW 失败，放行启动")
        return True
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _single_instance_mutex = handle
    return True


def main():
    global _TRAY
    _dbg("main() 开始")
    if not _acquire_single_instance():
        _dbg("[main] 已有实例在运行，本进程退出")
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "课堂安全播报助手已在运行中（见任务栏右下角托盘图标）。",
                "课堂安全播报助手", 0x40)  # MB_ICONINFORMATION
        except Exception:
            pass
        os._exit(0)
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
        _preview_stop.set()
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
