# -*- coding: utf-8 -*-
"""config_store 数据目录解析与旧数据迁移的单元测试。

背景：可写数据（config.json / log/）曾锚定 exe 旁，2026-09-10 用户决策改为
统一写入 %APPDATA%\\SafetyCast\\（exe 放受保护目录也照常可写）。
开发态（.py 运行）仍用项目目录，config.json 随仓库走、测试不受影响。
旧版 exe-旁数据在首次运行新版本时一次性迁移（目标已存在则不覆盖）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store


class TestComputeDataDir(unittest.TestCase):
    def test_frozen_uses_appdata(self):
        self.assertEqual(
            config_store.compute_data_dir(True, r"D:\anywhere", r"C:\Users\u\AppData\Roaming"),
            r"C:\Users\u\AppData\Roaming\SafetyCast")

    def test_dev_uses_project_dir(self):
        proj = os.path.dirname(os.path.abspath(config_store.__file__))
        self.assertEqual(
            config_store.compute_data_dir(False, r"C:\exe dir", "whatever"),
            proj)


class TestMigrateLegacyData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = os.path.join(self.tmp, "legacy")   # 模拟 exe 旁旧目录
        self.data = os.path.join(self.tmp, "data")       # 模拟 APPDATA 新目录
        os.makedirs(self.legacy)
        os.makedirs(self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_legacy(self, with_config=True, with_log=True):
        if with_config:
            with open(os.path.join(self.legacy, "config.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"dismissal_time": "15:40"}, f)
        if with_log:
            os.makedirs(os.path.join(self.legacy, "log"))
            with open(os.path.join(self.legacy, "log", "broadcast_log.csv"), "w",
                      encoding="utf-8") as f:
                f.write("kind,status\n")

    def test_nothing_to_migrate(self):
        moved = config_store.migrate_legacy_data(self.data, self.legacy)
        self.assertEqual(moved, [])

    def test_migrates_config_and_log(self):
        self._make_legacy()
        moved = config_store.migrate_legacy_data(self.data, self.legacy)
        self.assertEqual(sorted(moved), ["config.json", "log"])
        with open(os.path.join(self.data, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["dismissal_time"], "15:40")
        self.assertTrue(os.path.exists(
            os.path.join(self.data, "log", "broadcast_log.csv")))
        # 旧文件保留（只拷贝不删除：迁移失败可回退，老师数据零风险）
        self.assertTrue(os.path.exists(
            os.path.join(self.legacy, "config.json")))

    def test_never_overwrites_existing_target(self):
        self._make_legacy()
        with open(os.path.join(self.data, "config.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"dismissal_time": "17:00"}, f)
        moved = config_store.migrate_legacy_data(self.data, self.legacy)
        with open(os.path.join(self.data, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["dismissal_time"], "17:00")
        self.assertIn("log", moved)
        self.assertNotIn("config.json", moved)

    def test_log_only(self):
        self._make_legacy(with_config=False)
        moved = config_store.migrate_legacy_data(self.data, self.legacy)
        self.assertEqual(moved, ["log"])


if __name__ == "__main__":
    unittest.main()
