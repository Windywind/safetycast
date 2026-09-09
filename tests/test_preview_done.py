# -*- coding: utf-8 -*-
"""settings_win._wnd_proc 的 WM_APP_PREVIEW_DONE 分支单元测试。

背景（2026-09-09 用户反馈 bug）：点「试听」→「停止试听」后按钮不恢复、
无法再次试听。根因：试听线程 finally 里 PostMessageW(hwnd_main,
WM_APP_PREVIEW_DONE)，但 _wnd_proc 没有该消息的分支，消息直接落入
DefWindowProcW 被丢弃，_set_preview_buttons(running=False) 永不执行，
按钮永久停在 running 态（试听禁用、停止试听启用）。

无需真实窗口：_wnd_proc 是纯 Python 分派 + 对 user32.EnableWindow 的调用，
用假 HWND + mock EnableWindow 即可断言「收到消息后按钮恢复」。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings_win

FAKE_PREVIEW = 0x1111
FAKE_PREVIEW_STOP = 0x2222
FAKE_MAIN = 0x9999


def _make_inst():
    inst = settings_win.SettingsWindow(
        on_apply=None, preview_speak=None, list_voices=lambda: [])
    inst.hwnd_main = FAKE_MAIN
    inst.ctl["preview"] = FAKE_PREVIEW
    inst.ctl["preview_stop"] = FAKE_PREVIEW_STOP
    return inst


class TestPreviewDoneRestoresButtons(unittest.TestCase):
    def test_preview_done_restores_buttons(self):
        inst = _make_inst()
        with mock.patch.object(settings_win.user32, "EnableWindow") as en:
            ret = inst._wnd_proc(FAKE_MAIN,
                                 settings_win.WM_APP_PREVIEW_DONE, 0, 0)
            calls = {args[0]: args[1] for args, _ in en.call_args_list}
        # 消息被本分支吃掉（返回 0），不再落 DefWindowProcW
        self.assertEqual(ret, 0)
        # 试听恢复可用、停止试听恢复禁用
        self.assertEqual(calls.get(FAKE_PREVIEW), True)
        self.assertEqual(calls.get(FAKE_PREVIEW_STOP), False)


if __name__ == "__main__":
    unittest.main()
