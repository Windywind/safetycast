# -*- coding: utf-8 -*-
"""独立测试脚本：弹窗 + TTS，不依赖 bat 内联代码。"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from popup_win import show_popup
from broadcast import speak

TITLE = "放学安全提醒"
TEXT = "同学们，放学了。路上请走人行道、看红绿灯，不在马路上追逐打闹。高高兴兴上学，平平安安回家。"

if __name__ == "__main__":
    print("3秒后弹窗+语音...")
    time.sleep(3)

    t = threading.Thread(
        target=show_popup,
        args=(TITLE, TEXT),
        kwargs={"hold_seconds": 15},
        daemon=True,
    )
    t.start()

    time.sleep(0.3)
    speak(TEXT)

    t.join(timeout=20)
    print("测试完成")
