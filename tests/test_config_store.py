# -*- coding: utf-8 -*-
"""config_store 自动化测试（纯逻辑，无 GUI、无 TTS、不触碰真实 config.json）。

覆盖点：
1. merge_defaults 深合并：补齐缺失键、不覆盖已有值、不修改入参、保留未知键
2. 向后兼容：现网旧 config.json（无 popup_font_scale / tts_voice / tts_output_device）能安全升级
3. validate 接受合法输入：含各字段边界值、多行文本形式的假期列表
4. validate 拒绝非法输入：错时间格式、越界分钟/秒数、坏日期、空标题，且错误信息指明字段
5. validate 归一化：字符串数字转 int、档位吸附、tts_voice / tts_output_device 结构归一
6. save_atomic：往返一致、UTF-8 中文、.bak 备份、不留 .tmp、失败时原文件完好
7. load：损坏 json 与文件缺失均容错回退默认值，并通过回调上报原因
8. apply：原地变更 CONFIG，保持 dict 身份不变（热生效的基石）
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store


# 现网 config.json 的原始内容 —— 刻意不含本次新增的键，用于验证向后兼容
OLD_CONFIG = {
    "dismissal_time": "16:30",
    "tts_rate": 175,
    "tts_volume": 1.0,
    "popup_min_hold_seconds": 12,
    "rules": {
        "daily": {"enabled": True, "lead_minutes": 1, "title": "放学安全提醒"},
        "friday": {"enabled": True, "lead_minutes": 5, "title": "周末安全专题"},
        "holiday": {"enabled": True, "lead_minutes": 30, "title": "节假日安全专题"},
    },
    "upcoming_holidays": ["2026-10-01", "2026-10-02", "2027-01-01"],
}

DEFAULT_VOICE = {"id": "", "name": "系统默认"}
DEFAULT_OUTPUT = {"id": "", "name": "系统默认"}


def valid_raw(**over):
    """构造一份合法的表单原始输入（模拟设置窗口 collect_form 的产物）。"""
    raw = copy.deepcopy(OLD_CONFIG)
    raw["popup_font_scale"] = 100
    raw["tts_voice"] = copy.deepcopy(DEFAULT_VOICE)
    raw["tts_output_device"] = copy.deepcopy(DEFAULT_OUTPUT)
    raw.update(over)
    return raw


class ConfigStoreTestCase(unittest.TestCase):
    """基类：每个用例前后把模块级 CONFIG 恢复到初始快照，避免用例间串味。"""

    def setUp(self):
        self._snapshot = copy.deepcopy(config_store.CONFIG)

    def tearDown(self):
        config_store.CONFIG.clear()
        config_store.CONFIG.update(self._snapshot)


# ---------------------------------------------------------------- 默认值合并

class TestMergeDefaults(ConfigStoreTestCase):

    def test_empty_raw_yields_full_default_schema(self):
        self.assertEqual(config_store.merge_defaults({}), config_store.DEFAULT_CONFIG)

    def test_does_not_mutate_input(self):
        raw = {"dismissal_time": "15:00"}
        before = copy.deepcopy(raw)
        config_store.merge_defaults(raw)
        self.assertEqual(raw, before)

    def test_fills_missing_nested_rule_keys(self):
        merged = config_store.merge_defaults({"rules": {"daily": {"enabled": False}}})
        # 已给的键生效
        self.assertFalse(merged["rules"]["daily"]["enabled"])
        # 同组未给的键补默认
        self.assertEqual(merged["rules"]["daily"]["lead_minutes"],
                         config_store.DEFAULT_CONFIG["rules"]["daily"]["lead_minutes"])
        self.assertEqual(merged["rules"]["daily"]["title"],
                         config_store.DEFAULT_CONFIG["rules"]["daily"]["title"])
        # 整组未给的补全
        self.assertEqual(merged["rules"]["friday"],
                         config_store.DEFAULT_CONFIG["rules"]["friday"])
        self.assertEqual(merged["rules"]["holiday"],
                         config_store.DEFAULT_CONFIG["rules"]["holiday"])

    def test_existing_values_win_over_defaults(self):
        merged = config_store.merge_defaults({"dismissal_time": "15:30", "tts_rate": 200})
        self.assertEqual(merged["dismissal_time"], "15:30")
        self.assertEqual(merged["tts_rate"], 200)

    def test_unknown_keys_are_preserved(self):
        """手工加的未来键（如 V1.0 服务端地址）不应被合并逻辑吃掉。"""
        merged = config_store.merge_defaults({"server_url": "http://10.0.0.5:8000"})
        self.assertEqual(merged["server_url"], "http://10.0.0.5:8000")

    def test_result_is_deep_copy_of_defaults(self):
        """返回的嵌套结构不得与 DEFAULT_CONFIG 共享对象，否则改一个污染另一个。"""
        merged = config_store.merge_defaults({})
        merged["rules"]["daily"]["title"] = "被改坏了"
        merged["upcoming_holidays"].append("2030-01-01")
        self.assertEqual(config_store.DEFAULT_CONFIG["rules"]["daily"]["title"], "放学安全提醒")
        self.assertNotIn("2030-01-01", config_store.DEFAULT_CONFIG["upcoming_holidays"])

    def test_wrong_typed_nested_value_falls_back_to_default(self):
        """手工把 rules 写成 null / 字符串时不得让下游 CONFIG["rules"]["daily"] 崩掉。"""
        for bad in (None, "不是字典", 42, ["列表"]):
            merged = config_store.merge_defaults({"rules": bad})
            self.assertEqual(merged["rules"], config_store.DEFAULT_CONFIG["rules"],
                             f"rules={bad!r} 应回退默认结构")

    def test_wrong_typed_rule_entry_falls_back_to_default(self):
        merged = config_store.merge_defaults({"rules": {"daily": None}})
        self.assertEqual(merged["rules"]["daily"], config_store.DEFAULT_CONFIG["rules"]["daily"])
        self.assertEqual(merged["rules"]["friday"], config_store.DEFAULT_CONFIG["rules"]["friday"])


# ---------------------------------------------------------------- 向后兼容

class TestBackwardCompat(ConfigStoreTestCase):

    def test_old_config_gains_the_new_keys(self):
        merged = config_store.merge_defaults(copy.deepcopy(OLD_CONFIG))
        self.assertIn("popup_font_scale", merged)
        self.assertIn("tts_voice", merged)
        self.assertIn("tts_output_device", merged)
        self.assertEqual(merged["popup_font_scale"], 100)
        self.assertEqual(merged["tts_voice"], DEFAULT_VOICE)
        self.assertEqual(merged["tts_output_device"], DEFAULT_OUTPUT)

    def test_old_config_existing_values_untouched(self):
        merged = config_store.merge_defaults(copy.deepcopy(OLD_CONFIG))
        for key in OLD_CONFIG:
            self.assertEqual(merged[key], OLD_CONFIG[key], f"{key} 被意外改动")

    def test_old_config_validates_clean(self):
        """旧配置直接过校验应当零错误 —— 升级不能让老用户的配置变成非法。"""
        clean, errors = config_store.validate(copy.deepcopy(OLD_CONFIG))
        self.assertEqual(errors, [])
        self.assertIsNotNone(clean)


# ---------------------------------------------------------------- 合法输入

class TestValidateAccepts(ConfigStoreTestCase):

    def test_full_valid_config_passes(self):
        clean, errors = config_store.validate(valid_raw())
        self.assertEqual(errors, [])
        self.assertIsNotNone(clean)
        self.assertEqual(clean["dismissal_time"], "16:30")

    def test_does_not_mutate_input(self):
        raw = valid_raw()
        before = copy.deepcopy(raw)
        config_store.validate(raw)
        self.assertEqual(raw, before)

    def test_boundary_lead_minutes(self):
        for minutes in (0, 120):
            raw = valid_raw()
            raw["rules"]["daily"]["lead_minutes"] = minutes
            clean, errors = config_store.validate(raw)
            self.assertEqual(errors, [], f"lead_minutes={minutes} 应合法")
            self.assertEqual(clean["rules"]["daily"]["lead_minutes"], minutes)

    def test_boundary_hold_seconds(self):
        for seconds in (5, 300):
            clean, errors = config_store.validate(valid_raw(popup_min_hold_seconds=seconds))
            self.assertEqual(errors, [], f"hold_seconds={seconds} 应合法")
            self.assertEqual(clean["popup_min_hold_seconds"], seconds)

    def test_boundary_dismissal_time(self):
        for t in ("00:00", "23:59"):
            clean, errors = config_store.validate(valid_raw(dismissal_time=t))
            self.assertEqual(errors, [], f"dismissal_time={t} 应合法")
            self.assertEqual(clean["dismissal_time"], t)

    def test_empty_holiday_list_is_ok(self):
        clean, errors = config_store.validate(valid_raw(upcoming_holidays=[]))
        self.assertEqual(errors, [])
        self.assertEqual(clean["upcoming_holidays"], [])

    def test_holidays_from_multiline_text_with_blank_lines(self):
        """多行 EDIT 控件给出的是带 CRLF 的整块文本，空行须忽略。"""
        text = "2026-10-01\r\n\r\n2026-10-02\r\n   \n2027-01-01\r\n"
        clean, errors = config_store.validate(valid_raw(upcoming_holidays=text))
        self.assertEqual(errors, [])
        self.assertEqual(clean["upcoming_holidays"],
                         ["2026-10-01", "2026-10-02", "2027-01-01"])

    def test_holidays_accept_list_input_too(self):
        clean, errors = config_store.validate(
            valid_raw(upcoming_holidays=["2026-10-01", "2026-10-02"]))
        self.assertEqual(errors, [])
        self.assertEqual(clean["upcoming_holidays"], ["2026-10-01", "2026-10-02"])

    def test_real_voice_object_accepted(self):
        voice = {"id": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_ZH-CN_HUIHUI_11.0",
                 "name": "Microsoft Huihui Desktop"}
        clean, errors = config_store.validate(valid_raw(tts_voice=voice))
        self.assertEqual(errors, [])
        self.assertEqual(clean["tts_voice"], voice)

    def test_real_output_device_accepted(self):
        device = {"id": r"{0.0.0.00000000}.{some-endpoint-guid}",
                  "name": "Steam Streaming Speakers"}
        clean, errors = config_store.validate(valid_raw(tts_output_device=device))
        self.assertEqual(errors, [])
        self.assertEqual(clean["tts_output_device"], device)

    def test_title_at_max_length_accepted(self):
        clean, errors = config_store.validate(valid_raw())
        self.assertEqual(errors, [])
        raw = valid_raw()
        raw["rules"]["daily"]["title"] = "字" * 30
        clean, errors = config_store.validate(raw)
        self.assertEqual(errors, [], "30 字标题应合法")


# ---------------------------------------------------------------- 非法输入

class TestValidateRejects(ConfigStoreTestCase):

    def assertRejected(self, raw, field_hint):
        clean, errors = config_store.validate(raw)
        self.assertIsNone(clean, f"应拒绝，却返回了 {clean}")
        self.assertTrue(errors, "应给出至少一条错误")
        self.assertTrue(any(field_hint in e for e in errors),
                        f"错误信息应指明「{field_hint}」，实际：{errors}")

    def test_bad_time_format(self):
        for bad in ("16-30", "1630", "下午4点半", "16:30:00"):
            self.assertRejected(valid_raw(dismissal_time=bad), "放学时间")

    def test_time_out_of_range(self):
        for bad in ("24:00", "25:00", "16:60", "99:99"):
            self.assertRejected(valid_raw(dismissal_time=bad), "放学时间")

    def test_empty_time(self):
        self.assertRejected(valid_raw(dismissal_time=""), "放学时间")
        self.assertRejected(valid_raw(dismissal_time="   "), "放学时间")

    def test_lead_minutes_out_of_range(self):
        for bad in (-1, 121, 999):
            raw = valid_raw()
            raw["rules"]["friday"]["lead_minutes"] = bad
            self.assertRejected(raw, "提前分钟")

    def test_lead_minutes_not_a_number(self):
        for bad in ("abc", "", "五分钟"):
            raw = valid_raw()
            raw["rules"]["daily"]["lead_minutes"] = bad
            self.assertRejected(raw, "提前分钟")

    def test_empty_title(self):
        for bad in ("", "   "):
            raw = valid_raw()
            raw["rules"]["holiday"]["title"] = bad
            self.assertRejected(raw, "标题")

    def test_title_too_long(self):
        raw = valid_raw()
        raw["rules"]["daily"]["title"] = "字" * 31
        self.assertRejected(raw, "标题")

    def test_bad_holiday_date(self):
        for bad in ("2026-13-01", "2026-02-30", "not-a-date", "2026/10/01", "20261001"):
            self.assertRejected(valid_raw(upcoming_holidays=[bad]), "假期")

    def test_hold_seconds_out_of_range(self):
        for bad in (4, 301, -5):
            self.assertRejected(valid_raw(popup_min_hold_seconds=bad), "停留")

    def test_hold_seconds_not_a_number(self):
        self.assertRejected(valid_raw(popup_min_hold_seconds="abc"), "停留")

    def test_non_numeric_tier_field(self):
        self.assertRejected(valid_raw(popup_font_scale="大"), "字号")
        self.assertRejected(valid_raw(tts_rate="快"), "语速")
        self.assertRejected(valid_raw(tts_volume="响"), "音量")

    def test_all_errors_collected_not_just_first(self):
        """一次保存要把所有问题都告诉老师，不能改一个报一个。"""
        raw = valid_raw(dismissal_time="25:00", popup_min_hold_seconds=9999)
        raw["rules"]["daily"]["title"] = ""
        raw["upcoming_holidays"] = ["2026-13-01"]
        clean, errors = config_store.validate(raw)
        self.assertIsNone(clean)
        self.assertGreaterEqual(len(errors), 4, f"应收集全部错误，实际：{errors}")

    def test_errors_are_readable_chinese_strings(self):
        _, errors = config_store.validate(valid_raw(dismissal_time="25:00"))
        self.assertTrue(all(isinstance(e, str) and e for e in errors))


# ---------------------------------------------------------------- 归一化

class TestValidateNormalization(ConfigStoreTestCase):

    def test_string_numbers_coerced_to_int(self):
        """EDIT 控件取回的都是字符串，落盘必须是 int。"""
        raw = valid_raw(dismissal_time=" 15:45 ")
        raw["rules"]["daily"]["lead_minutes"] = "3"
        raw["popup_min_hold_seconds"] = "20"
        clean, errors = config_store.validate(raw)
        self.assertEqual(errors, [])
        self.assertEqual(clean["dismissal_time"], "15:45")
        self.assertIsInstance(clean["rules"]["daily"]["lead_minutes"], int)
        self.assertEqual(clean["rules"]["daily"]["lead_minutes"], 3)
        self.assertIsInstance(clean["popup_min_hold_seconds"], int)
        self.assertEqual(clean["popup_min_hold_seconds"], 20)

    def test_font_scale_snapped_to_tier(self):
        clean, _ = config_store.validate(valid_raw(popup_font_scale=120))
        self.assertEqual(clean["popup_font_scale"], 115)

    def test_font_scale_clamped_to_tier_range(self):
        clean, _ = config_store.validate(valid_raw(popup_font_scale=500))
        self.assertEqual(clean["popup_font_scale"], max(config_store.FONT_SCALE_TIERS))
        clean, _ = config_store.validate(valid_raw(popup_font_scale=1))
        self.assertEqual(clean["popup_font_scale"], min(config_store.FONT_SCALE_TIERS))

    def test_rate_snapped_to_tier(self):
        clean, _ = config_store.validate(valid_raw(tts_rate=180))
        self.assertEqual(clean["tts_rate"], 175)
        clean, _ = config_store.validate(valid_raw(tts_rate=190))
        self.assertEqual(clean["tts_rate"], 200)

    def test_volume_snapped_to_tier(self):
        clean, _ = config_store.validate(valid_raw(tts_volume=0.9))
        self.assertEqual(clean["tts_volume"], 0.85)
        clean, _ = config_store.validate(valid_raw(tts_volume="0.6"))
        self.assertIn(clean["tts_volume"], config_store.TTS_VOLUME_TIERS)

    def test_exact_tier_values_unchanged(self):
        for tier in config_store.FONT_SCALE_TIERS:
            clean, errors = config_store.validate(valid_raw(popup_font_scale=tier))
            self.assertEqual(errors, [])
            self.assertEqual(clean["popup_font_scale"], tier)

    def test_enabled_coerced_to_bool(self):
        raw = valid_raw()
        raw["rules"]["daily"]["enabled"] = 1
        raw["rules"]["friday"]["enabled"] = 0
        clean, errors = config_store.validate(raw)
        self.assertEqual(errors, [])
        self.assertIs(clean["rules"]["daily"]["enabled"], True)
        self.assertIs(clean["rules"]["friday"]["enabled"], False)

    def test_wrong_typed_voice_falls_back_to_system_default(self):
        """tts_voice 是新增键，无历史裸字符串格式；手工写成字符串/数字时按类型防御回退默认。"""
        for bad in ("SOME_VOICE_ID", 42, ["列表"]):
            clean, errors = config_store.validate(valid_raw(tts_voice=bad))
            self.assertEqual(errors, [], f"tts_voice={bad!r} 应静默回退而非报错")
            self.assertEqual(clean["tts_voice"], DEFAULT_VOICE)

    def test_missing_voice_name_filled(self):
        clean, errors = config_store.validate(valid_raw(tts_voice={"id": "X"}))
        self.assertEqual(errors, [])
        self.assertEqual(clean["tts_voice"]["id"], "X")
        self.assertTrue(clean["tts_voice"]["name"])

    def test_empty_voice_normalized_to_system_default(self):
        for empty in (None, "", {}):
            clean, errors = config_store.validate(valid_raw(tts_voice=empty))
            self.assertEqual(errors, [], f"tts_voice={empty!r} 应回退默认而非报错")
            self.assertEqual(clean["tts_voice"], DEFAULT_VOICE)

    def test_wrong_typed_output_device_falls_back_to_system_default(self):
        for bad in ("SOME_DEVICE_ID", 42, ["列表"]):
            clean, errors = config_store.validate(valid_raw(tts_output_device=bad))
            self.assertEqual(errors, [], f"tts_output_device={bad!r} 应静默回退而非报错")
            self.assertEqual(clean["tts_output_device"], DEFAULT_OUTPUT)

    def test_missing_output_device_name_filled(self):
        clean, errors = config_store.validate(valid_raw(tts_output_device={"id": "X"}))
        self.assertEqual(errors, [])
        self.assertEqual(clean["tts_output_device"]["id"], "X")
        self.assertTrue(clean["tts_output_device"]["name"])

    def test_empty_output_device_normalized_to_system_default(self):
        for empty in (None, "", {}):
            clean, errors = config_store.validate(valid_raw(tts_output_device=empty))
            self.assertEqual(errors, [],
                             f"tts_output_device={empty!r} 应回退默认而非报错")
            self.assertEqual(clean["tts_output_device"], DEFAULT_OUTPUT)

    def test_clean_output_is_json_serializable(self):
        clean, errors = config_store.validate(valid_raw())
        self.assertEqual(errors, [])
        json.dumps(clean, ensure_ascii=False)   # 不应抛异常

    def test_clean_contains_all_schema_keys(self):
        clean, _ = config_store.validate(valid_raw())
        for key in config_store.DEFAULT_CONFIG:
            self.assertIn(key, clean)


# ---------------------------------------------------------------- 原子保存

class TestSaveAtomic(ConfigStoreTestCase):

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_roundtrip_preserves_data(self):
        data, _ = config_store.validate(valid_raw(dismissal_time="15:30"))
        config_store.save_atomic(self.path, data)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)

    def test_utf8_chinese_preserved(self):
        data, _ = config_store.validate(valid_raw())
        config_store.save_atomic(self.path, data)
        with open(self.path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("放学安全提醒", text)
        self.assertIn("系统默认", text)

    def test_creates_bak_when_target_exists(self):
        config_store.save_atomic(self.path, {"dismissal_time": "16:30"})
        config_store.save_atomic(self.path, {"dismissal_time": "15:30"})
        bak = self.path + ".bak"
        self.assertTrue(os.path.exists(bak), "第二次保存前应备份原文件")
        with open(bak, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["dismissal_time"], "16:30")

    def test_no_bak_when_target_absent(self):
        config_store.save_atomic(self.path, {"dismissal_time": "16:30"})
        self.assertFalse(os.path.exists(self.path + ".bak"))

    def test_no_tmp_file_left_behind(self):
        config_store.save_atomic(self.path, {"dismissal_time": "16:30"})
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"残留临时文件：{leftovers}")

    def test_overwrites_existing_content(self):
        config_store.save_atomic(self.path, {"a": 1})
        config_store.save_atomic(self.path, {"b": 2})
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"b": 2})

    def test_failure_leaves_original_intact(self):
        config_store.save_atomic(self.path, {"dismissal_time": "16:30"})
        bad_path = os.path.join(self.dir, "no_such_dir", "config.json")
        with self.assertRaises(Exception):
            config_store.save_atomic(bad_path, {"dismissal_time": "15:30"})
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["dismissal_time"], "16:30")


# ---------------------------------------------------------------- 加载容错

class TestLoad(ConfigStoreTestCase):

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "config.json")

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_load_returns_saved_config(self):
        data, _ = config_store.validate(valid_raw(dismissal_time="15:30"))
        config_store.save_atomic(self.path, data)
        self.assertEqual(config_store.load(self.path), data)

    def test_load_merges_defaults_for_old_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(OLD_CONFIG, f, ensure_ascii=False)
        loaded = config_store.load(self.path)
        self.assertIn("popup_font_scale", loaded)
        self.assertIn("tts_voice", loaded)
        self.assertEqual(loaded["dismissal_time"], "16:30")

    def test_missing_file_returns_defaults_without_raising(self):
        self.assertEqual(config_store.load(self.path), config_store.DEFAULT_CONFIG)

    def test_corrupt_json_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ 这不是合法的 JSON ")
        self.assertEqual(config_store.load(self.path), config_store.DEFAULT_CONFIG)

    def test_corrupt_json_reports_error_via_callback(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ broken ")
        seen = []
        config_store.load(self.path, on_error=seen.append)
        self.assertTrue(seen, "损坏配置必须上报，不能静默吞掉")
        self.assertIn(self.path, seen[0])

    def test_missing_file_reports_error_via_callback(self):
        seen = []
        config_store.load(self.path, on_error=seen.append)
        self.assertTrue(seen)

    def test_wrong_toplevel_type_falls_back(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(["不是", "字典"], f, ensure_ascii=False)
        self.assertEqual(config_store.load(self.path), config_store.DEFAULT_CONFIG)

    def test_on_error_callback_exception_does_not_break_load(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ broken ")

        def boom(_msg):
            raise RuntimeError("回调自己炸了")

        self.assertEqual(config_store.load(self.path, on_error=boom),
                         config_store.DEFAULT_CONFIG)


# ------------------------------------------------------- 热生效：原地变更

class TestApply(ConfigStoreTestCase):

    def test_apply_updates_values(self):
        config_store.apply({"dismissal_time": "15:30"})
        self.assertEqual(config_store.CONFIG["dismissal_time"], "15:30")

    def test_apply_preserves_dict_identity(self):
        """热生效的关键：必须原地 clear+update，绝不能重新绑定名字。"""
        before = config_store.CONFIG
        config_store.apply({"dismissal_time": "15:30"})
        self.assertIs(config_store.CONFIG, before)

    def test_already_imported_reference_sees_change(self):
        """模拟 broadcast.py 的 `from config_store import CONFIG`。"""
        from config_store import CONFIG as imported
        config_store.apply({"dismissal_time": "14:00", "tts_rate": 230})
        self.assertEqual(imported["dismissal_time"], "14:00")
        self.assertEqual(imported["tts_rate"], 230)

    def test_apply_replaces_whole_dict_no_stale_keys(self):
        config_store.apply({"dismissal_time": "16:30", "stale_key": 1})
        config_store.apply({"dismissal_time": "15:30"})
        self.assertNotIn("stale_key", config_store.CONFIG)

    def test_apply_does_not_mutate_input(self):
        new = {"dismissal_time": "15:30"}
        config_store.apply(new)
        config_store.CONFIG["dismissal_time"] = "被外部改了"
        self.assertEqual(new["dismissal_time"], "15:30")

    def test_apply_keeps_nested_reference_isolated(self):
        new, _ = config_store.validate(valid_raw())
        config_store.apply(new)
        config_store.CONFIG["rules"]["daily"]["title"] = "改一下"
        self.assertEqual(new["rules"]["daily"]["title"], "放学安全提醒")


if __name__ == "__main__":
    unittest.main(verbosity=2)
