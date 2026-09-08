# -*- coding: utf-8 -*-
"""配置层：schema 默认值、深合并、字段校验、原子保存、热应用。

设计要点
--------
1. **不依赖任何 GUI / TTS 模块**，可被 unittest 直接覆盖（见 tests/test_config_store.py）。
2. `CONFIG` 是模块级规范字典。`broadcast.py` 用 `from config_store import CONFIG` 拿到
   **同一个对象引用**；热应用靠 `apply()` 原地 `clear() + update()`，绝不重新绑定名字，
   因此 broadcast 里所有「调用时取值」的读取点无需改动即可立即生效。
3. `validate()` 返回的 clean 已完成类型转换与档位归一，可直接落盘；任一字段非法则
   返回 `(None, errors)`，errors 为中文提示，逐条指明「哪一项、错在哪、允许范围」。
4. `save_atomic()` 先备份 `.bak`，再写 `.tmp` → `flush` → `fsync` → `os.replace`。
   `os.replace` 在 Windows 上是原子操作，杜绝写坏配置导致下次启动直接 fatal。
"""

import copy
import datetime
import json
import os
import re
import shutil

# ------------------------------------------------------------------ schema

# 语速 / 音量 / 字号一律用预置档位而非自由输入：
# 既规避 Win32 Trackbar 公共控件（需 InitCommonControlsEx），也从根上消除非法输入。
TTS_RATE_TIERS = (140, 160, 175, 200, 230)
TTS_VOLUME_TIERS = (0.5, 0.7, 0.85, 1.0)
FONT_SCALE_TIERS = (90, 100, 115, 130, 150)

# 取值范围
LEAD_MINUTES_RANGE = (0, 120)
HOLD_SECONDS_RANGE = (5, 300)
TITLE_MAX_LEN = 30

DEFAULT_VOICE_NAME = "系统默认"

DEFAULT_CONFIG = {
    "dismissal_time": "16:30",
    "tts_rate": 175,
    "tts_volume": 1.0,
    "tts_voice": {"id": "", "name": DEFAULT_VOICE_NAME},
    "popup_min_hold_seconds": 12,
    # 字号是「缩放百分比」而非绝对像素：乘到 popup_win 按屏幕物理像素算出的
    # title_em / body_em 上，既给老师调节权，又保留 DPI 自适应与长文本自动收缩。
    "popup_font_scale": 100,
    "rules": {
        "daily": {"enabled": True, "lead_minutes": 1, "title": "放学安全提醒"},
        "friday": {"enabled": True, "lead_minutes": 5, "title": "周末安全专题"},
        "holiday": {"enabled": True, "lead_minutes": 30, "title": "节假日安全专题"},
    },
    # 仅当 config.json 完全缺失该键时才用到；每学期应在设置界面里更新。
    "upcoming_holidays": ["2026-10-01", "2026-10-02", "2027-01-01"],
}

RULE_KINDS = ("daily", "friday", "holiday")
RULE_LABELS = {"daily": "每日提醒", "friday": "周五专题", "holiday": "节假日专题"}

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# 模块级规范字典。初始为默认值，由调用方 load()+apply() 注入磁盘配置。
CONFIG = copy.deepcopy(DEFAULT_CONFIG)


# ------------------------------------------------------------------ 合并

def _deep_merge(base: dict, over: dict) -> dict:
    """以 base 为底、over 覆盖，返回新字典。字典递归合并，其余类型（含 list）整体替换。

    防御：某键在 base 中是 dict 而 over 给的是 null / 字符串等（手工改坏 config.json
    的常见情形），保留 base 的结构，避免下游 `CONFIG["rules"]["daily"]` 直接 TypeError。
    """
    out = copy.deepcopy(base)
    for key, value in over.items():
        default = out.get(key)
        if isinstance(default, dict):
            if isinstance(value, dict):
                out[key] = _deep_merge(default, value)
            # 类型不符 → 保留默认结构
        else:
            out[key] = copy.deepcopy(value)
    return out


def merge_defaults(raw) -> dict:
    """深合并：以 DEFAULT_CONFIG 为底，raw 覆盖；补齐旧配置缺失键，不修改入参。

    未知键（如将来手工加的 server_url）原样透传，不被吃掉。
    """
    if not isinstance(raw, dict):
        raw = {}
    return _deep_merge(DEFAULT_CONFIG, raw)


# ------------------------------------------------------------------ 校验

def _norm_time(value, errors: list) -> str:
    label = "放学时间"
    text = str(value).strip() if value is not None else ""
    match = _TIME_RE.match(text)
    if not match:
        errors.append(f"{label}格式应为 HH:MM（例如 16:30），当前为「{text}」")
        return DEFAULT_CONFIG["dismissal_time"]
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        errors.append(f"{label}超出范围（允许 00:00~23:59），当前为「{text}」")
        return DEFAULT_CONFIG["dismissal_time"]
    return f"{hour:02d}:{minute:02d}"


def _norm_int(value, errors: list, label: str, bounds, default: int) -> int:
    lo, hi = bounds
    text = str(value).strip() if value is not None else ""
    try:
        number = int(round(float(text)))
    except (TypeError, ValueError):
        errors.append(f"{label}应为整数（允许 {lo}~{hi}），当前为「{text}」")
        return default
    if number < lo or number > hi:
        errors.append(f"{label}超出范围（允许 {lo}~{hi}），当前为 {number}")
        return default
    return number


def _norm_title(value, errors: list, label: str, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        errors.append(f"{label}不能为空")
        return default
    if len(text) > TITLE_MAX_LEN:
        errors.append(f"{label}过长（最多 {TITLE_MAX_LEN} 字，当前 {len(text)} 字）")
        return default
    return text


def _norm_holidays(value, errors: list) -> list:
    """接受 list 或多行文本（多行 EDIT 控件给出的是带 CRLF 的整块字符串），空行忽略。"""
    if value is None:
        items = []
    elif isinstance(value, str):
        items = value.replace("\r", "\n").split("\n")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        errors.append(f"假期列表格式不正确（应为每行一个 YYYY-MM-DD 日期），当前为「{value}」")
        return []

    out = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        match = _DATE_RE.match(text)
        if not match:
            errors.append(f"假期日期格式应为 YYYY-MM-DD（例如 2026-10-01），当前为「{text}」")
            continue
        try:
            datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            errors.append(f"假期日期「{text}」不存在，请检查月份与日期")
            continue
        out.append(text)
    return out


def _snap_tier(value, tiers, errors: list, label: str, default, as_int: bool):
    """把手工填入的任意数值吸附到最近的合法档位；无法解析时报错并回退默认。"""
    text = str(value).strip() if value is not None else ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        errors.append(f"{label}不是有效数字（可选档位：{'、'.join(str(t) for t in tiers)}），"
                      f"当前为「{text}」")
        return default
    snapped = min(tiers, key=lambda t: abs(t - number))
    return int(snapped) if as_int else snapped


def _norm_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "是")
    return bool(value)


def _norm_voice(value) -> dict:
    """归一为 {"id": str, "name": str}；空值回退「系统默认」。

    同时存 id 与 name：id 用于匹配 SAPI 音色 token（SpVoice.Voice），name 用于
    换机器后 id 失效时的日志提示与模糊匹配兜底。

    入参经 merge_defaults 后必为 dict（类型不符者已被上游防御回退），此处只兜底空值。
    """
    if not isinstance(value, dict):
        return {"id": "", "name": DEFAULT_VOICE_NAME}

    voice_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    if not voice_id:
        return {"id": "", "name": DEFAULT_VOICE_NAME}
    return {"id": voice_id, "name": name or voice_id}


def validate(raw) -> tuple:
    """校验并规范化。

    返回 `(clean, [])` 表示全部合法，clean 已完成类型转换与档位归一、可直接落盘；
    返回 `(None, errors)` 表示存在非法项，errors 为中文提示列表（一次性收集全部问题，
    而非改一个报一个）。缺失的键按默认值补齐，故旧配置直接过校验也是零错误。
    """
    errors: list = []
    data = merge_defaults(raw)

    data["dismissal_time"] = _norm_time(data.get("dismissal_time"), errors)

    rules = data.get("rules")
    if not isinstance(rules, dict):
        rules = copy.deepcopy(DEFAULT_CONFIG["rules"])
    clean_rules = {}
    for kind in RULE_KINDS:
        entry = rules.get(kind)
        if not isinstance(entry, dict):
            entry = copy.deepcopy(DEFAULT_CONFIG["rules"][kind])
        label = RULE_LABELS[kind]
        default_entry = DEFAULT_CONFIG["rules"][kind]
        clean_rules[kind] = {
            "enabled": _norm_bool(entry.get("enabled", default_entry["enabled"])),
            "lead_minutes": _norm_int(
                entry.get("lead_minutes", default_entry["lead_minutes"]), errors,
                f"{label}提前分钟数", LEAD_MINUTES_RANGE, default_entry["lead_minutes"]),
            "title": _norm_title(
                entry.get("title", default_entry["title"]), errors,
                f"{label}标题", default_entry["title"]),
        }
    data["rules"] = clean_rules

    data["upcoming_holidays"] = _norm_holidays(data.get("upcoming_holidays"), errors)

    data["popup_min_hold_seconds"] = _norm_int(
        data.get("popup_min_hold_seconds"), errors, "弹窗最短停留秒数",
        HOLD_SECONDS_RANGE, DEFAULT_CONFIG["popup_min_hold_seconds"])

    data["popup_font_scale"] = _snap_tier(
        data.get("popup_font_scale"), FONT_SCALE_TIERS, errors, "弹窗字号",
        DEFAULT_CONFIG["popup_font_scale"], as_int=True)

    data["tts_rate"] = _snap_tier(
        data.get("tts_rate"), TTS_RATE_TIERS, errors, "语速",
        DEFAULT_CONFIG["tts_rate"], as_int=True)

    data["tts_volume"] = _snap_tier(
        data.get("tts_volume"), TTS_VOLUME_TIERS, errors, "音量",
        DEFAULT_CONFIG["tts_volume"], as_int=False)

    data["tts_voice"] = _norm_voice(data.get("tts_voice"))

    if errors:
        return None, errors
    return data, []


# ------------------------------------------------------------------ 持久化

def save_atomic(path: str, data: dict) -> None:
    """备份 .bak → 写 .tmp → flush → fsync → os.replace。失败抛异常，原文件保持完好。"""
    path = os.path.abspath(path)
    tmp = path + ".tmp"
    text = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def load(path: str, on_error=None) -> dict:
    """读取配置；文件缺失、JSON 损坏、顶层类型不对均容错回退默认值，绝不抛异常。

    on_error(message) 用于把回退原因上报给调用方（broadcast 传 _dbg 写 debug.log），
    回调自身抛异常也不影响加载。
    """
    def report(message: str):
        if on_error is None:
            return
        try:
            on_error(message)
        except Exception:
            pass

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        report(f"配置文件不存在，改用默认配置：{path}")
        return merge_defaults({})
    except (OSError, ValueError) as exc:
        report(f"配置文件读取失败（{exc}），改用默认配置：{path}")
        return merge_defaults({})

    if not isinstance(raw, dict):
        report(f"配置文件顶层不是 JSON 对象，改用默认配置：{path}")
        return merge_defaults({})

    return merge_defaults(raw)


# ------------------------------------------------------------------ 热应用

def apply(new: dict) -> None:
    """原地变更 CONFIG（clear + update），使所有「调用时取值」的读取点立即生效。

    必须原地变更而非 `CONFIG = new`：broadcast.py 已通过 `from config_store import CONFIG`
    持有旧对象引用，重新绑定名字会让它永远看不到新配置。
    """
    snapshot = copy.deepcopy(new) if isinstance(new, dict) else {}
    CONFIG.clear()
    CONFIG.update(snapshot)
