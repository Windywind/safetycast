# -*- coding: utf-8 -*-
"""broadcast.plan_today 单元测试：播报优先级 + 「跳过」留痕矩阵。

背景：调度逻辑从「互斥 elif 只选一个」改为「逐规则规划 + 被覆盖项留痕」后，
重叠场景（如假期前最后教学日恰逢周五）会同时记多条跳过。本测试用固定日期
（2026-09，其 09-12 为周六）锁定 weekday 与假期关系，验证 plan_today 返回值。
"""
import copy
import csv
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broadcast

MON = datetime.datetime(2026, 9, 14, 10, 0, 0)  # 周一
THU = datetime.datetime(2026, 9, 17, 10, 0, 0)  # 周四
FRI = datetime.datetime(2026, 9, 18, 10, 0, 0)  # 周五
SAT = datetime.datetime(2026, 9, 12, 10, 0, 0)  # 周六


def _rules(daily=True, friday=True, holiday=True):
    return {
        "daily": {"enabled": daily, "lead_minutes": 1, "title": "放学安全提醒"},
        "friday": {"enabled": friday, "lead_minutes": 5, "title": "周末安全专题"},
        "holiday": {"enabled": holiday, "lead_minutes": 30, "title": "节假日安全专题"},
    }


class PlanTodayTestCase(unittest.TestCase):
    def setUp(self):
        self._snapshot = copy.deepcopy(broadcast.CONFIG)

    def tearDown(self):
        broadcast.CONFIG.clear()
        broadcast.CONFIG.update(self._snapshot)

    def _plan(self, now, holidays=(), daily=True, friday=True, holiday=True):
        broadcast.CONFIG["rules"] = _rules(daily, friday, holiday)
        broadcast.CONFIG["upcoming_holidays"] = list(holidays)
        primary, skipped = broadcast.plan_today(now)
        return primary, [k for k, _ in skipped]

    # ------------------------------------------------------------ 正常日
    def test_weekday_primary_daily_no_skip(self):
        self.assertEqual(self._plan(MON), ("daily", []))

    def test_friday_overrides_daily(self):
        self.assertEqual(self._plan(FRI), ("friday", ["daily"]))

    def test_friday_disabled_falls_back_to_daily(self):
        self.assertEqual(self._plan(FRI, friday=False), ("daily", []))

    # ------------------------------------------------------------ 假期边界
    def test_holiday_eve_overrides_daily(self):
        # 09-18（周五）为假期 → 09-17（周四）是假期前最后教学日
        self.assertEqual(self._plan(THU, holidays=("2026-09-18",)),
                         ("holiday", ["daily"]))

    def test_holiday_eve_on_friday_overrides_both(self):
        # 09-19（周六）为假期 → 09-18（周五）既是周五又是假期前最后教学日
        self.assertEqual(self._plan(FRI, holidays=("2026-09-19",)),
                         ("holiday", ["friday", "daily"]))

    def test_holiday_day_no_broadcast(self):
        primary, kinds = self._plan(FRI, holidays=("2026-09-18",))
        self.assertIsNone(primary)
        self.assertEqual(kinds, ["holiday"])

    # ------------------------------------------------------------ 不播场景
    def test_weekend_no_broadcast(self):
        primary, kinds = self._plan(SAT)
        self.assertIsNone(primary)
        self.assertEqual(kinds, ["daily"])

    def test_all_disabled_no_broadcast(self):
        primary, kinds = self._plan(MON, daily=False, friday=False, holiday=False)
        self.assertIsNone(primary)
        self.assertEqual(kinds, ["daily"])

    # ------------------------------------------------------------ 标题回退
    def test_title_missing_falls_back_to_kind(self):
        broadcast.CONFIG["rules"] = _rules(friday=False, holiday=False)
        del broadcast.CONFIG["rules"]["daily"]["title"]
        primary, skipped = broadcast.plan_today(MON)
        self.assertEqual(primary, "daily")
        self.assertEqual(skipped, [])
        self.assertEqual(broadcast._rule_title("daily"), "daily")


class LogSkipTestCase(unittest.TestCase):
    """log_skip / _write_skips 的 CSV 落账测试。

    broadcast.LOG_PATH 是 import 时定死的模块级常量，测试把它临时指到临时目录，
    避免污染真实 log/broadcast_log.csv；tearDown 一并还原。
    """

    def setUp(self):
        self._snapshot = copy.deepcopy(broadcast.CONFIG)
        self._orig_log_path = broadcast.LOG_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        broadcast.LOG_PATH = os.path.join(self._tmpdir.name, "broadcast_log.csv")
        broadcast.CONFIG["rules"] = _rules()

    def tearDown(self):
        broadcast.LOG_PATH = self._orig_log_path
        broadcast.CONFIG.clear()
        broadcast.CONFIG.update(self._snapshot)
        self._tmpdir.cleanup()

    def _rows(self):
        with open(broadcast.LOG_PATH, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))

    def test_log_skip_writes_header_and_row(self):
        broadcast.log_skip("daily", "周末停课")
        rows = self._rows()
        self.assertEqual(rows[0], ["时间", "类型", "标题", "内容", "状态"])
        self.assertEqual(len(rows), 2)
        kind, title, text, status = rows[1][1:]
        self.assertEqual(kind, "daily")
        self.assertEqual(title, broadcast._rule_title("daily"))
        self.assertEqual(text, "")
        self.assertEqual(status, "跳过：周末停课")

    def test_write_skips_batch_keeps_order(self):
        broadcast._write_skips([
            ("friday", "被「节假日安全专题」覆盖"),
            ("daily", "被「节假日安全专题」覆盖"),
        ])
        rows = self._rows()
        self.assertEqual(len(rows), 3)  # 表头 + 2 条跳过
        self.assertEqual([r[1] for r in rows[1:]], ["friday", "daily"])
        self.assertEqual(rows[1][4], "跳过：被「节假日安全专题」覆盖")
        self.assertEqual(rows[2][4], "跳过：被「节假日安全专题」覆盖")

    def test_write_skips_empty_writes_nothing(self):
        broadcast._write_skips([])
        self.assertFalse(os.path.exists(broadcast.LOG_PATH))

    def test_log_skip_title_falls_back_to_kind(self):
        broadcast.CONFIG["rules"] = _rules(friday=False, holiday=False)
        del broadcast.CONFIG["rules"]["daily"]["title"]
        broadcast.log_skip("daily", "xx")
        self.assertEqual(self._rows()[1][2], "daily")

    def test_log_written_as_utf8_sig_bom(self):
        # Excel 双击打开中文不乱码，依赖 utf-8-sig 写入的 BOM 头
        broadcast.log_skip("daily", "周末停课")
        with open(broadcast.LOG_PATH, "rb") as f:
            self.assertEqual(f.read(3), b"\xef\xbb\xbf")


if __name__ == "__main__":
    unittest.main()