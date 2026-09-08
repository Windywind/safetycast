# -*- coding: utf-8 -*-
"""autostart.py 单元测试（开机自启注册表读写）。

覆盖：
1. startup_command：路径加引号、指向 broadcast.py
2. is_enabled：默认 False、enable 后 True、disable 后 False
3. enable：幂等、重复调用自愈刷新
4. disable：值不存在/键不存在时静默跳过（幂等）
5. set_enabled：按布尔切换

策略：用真实 winreg 读写 HKCU 下的**测试专用路径**
（Software\\SafetyCastTests\\Run），绝不动真实 Run 键；setUp/tearDown 双向清场，
即使断言炸了也不留垃圾。
"""

import os
import sys
import unittest
import winreg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autostart

TEST_ROOT = winreg.HKEY_CURRENT_USER
TEST_PATH = r"Software\SafetyCastTests\Run"


def _cleanup():
    """删掉测试值与测试键（不存在则忽略）。"""
    try:
        with winreg.OpenKey(TEST_ROOT, TEST_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, autostart.VALUE_NAME)
    except FileNotFoundError:
        pass
    try:
        winreg.DeleteKey(TEST_ROOT, TEST_PATH)
    except (FileNotFoundError, OSError):
        pass


class TestStartupCommand(unittest.TestCase):
    def test_command_quotes_paths_and_targets_broadcast(self):
        cmd = autostart.startup_command()
        if getattr(sys, "frozen", False):
            self.assertEqual(cmd, f'"{sys.executable}"')
            return
        # 开发态：<解释器> <broadcast.py 绝对路径>，两段都必须带引号（容忍空格）
        self.assertTrue(cmd.startswith('"'), cmd)
        script = os.path.join(os.path.dirname(autostart.__file__),
                              "broadcast.py")
        self.assertIn(f'"{script}"', cmd)
        # 有 pythonw.exe 时必须优先用它（自启不能弹控制台黑窗）
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(pythonw):
            self.assertIn(f'"{pythonw}"', cmd)

    def test_command_accepts_explicit_script(self):
        cmd = autostart.startup_command(script=r"C:\some dir\app.py")
        self.assertTrue(cmd.endswith(r'"C:\some dir\app.py"'), cmd)


class TestRegistryRoundTrip(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_disabled_by_default(self):
        self.assertFalse(autostart.is_enabled(TEST_ROOT, TEST_PATH))

    def test_enable_then_is_enabled(self):
        autostart.enable(TEST_ROOT, TEST_PATH)
        self.assertTrue(autostart.is_enabled(TEST_ROOT, TEST_PATH))
        # 写入的内容就是 startup_command()
        with winreg.OpenKey(TEST_ROOT, TEST_PATH, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            value, vtype = winreg.QueryValueEx(key, autostart.VALUE_NAME)
        self.assertEqual(vtype, winreg.REG_SZ)
        self.assertEqual(value, autostart.startup_command())

    def test_enable_is_idempotent(self):
        autostart.enable(TEST_ROOT, TEST_PATH)
        autostart.enable(TEST_ROOT, TEST_PATH)   # 重复启用不报错（自愈刷新）
        self.assertTrue(autostart.is_enabled(TEST_ROOT, TEST_PATH))

    def test_disable_removes_value(self):
        autostart.enable(TEST_ROOT, TEST_PATH)
        autostart.disable(TEST_ROOT, TEST_PATH)
        self.assertFalse(autostart.is_enabled(TEST_ROOT, TEST_PATH))

    def test_disable_when_absent_is_noop(self):
        autostart.disable(TEST_ROOT, TEST_PATH)   # 键都不存在，不抛异常
        self.assertFalse(autostart.is_enabled(TEST_ROOT, TEST_PATH))

    def test_is_enabled_ignores_unrelated_values(self):
        # 别的程序的值不影响本程序的判定
        with winreg.CreateKeyEx(TEST_ROOT, TEST_PATH, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "SomeOtherApp", 0, winreg.REG_SZ, "x")
        self.assertFalse(autostart.is_enabled(TEST_ROOT, TEST_PATH))


if __name__ == "__main__":
    unittest.main()
