# -*- coding: utf-8 -*-
"""独立测试脚本：弹窗 + 可中断 TTS，不依赖 bat 内联代码。

验证点：
1. 全屏红底弹窗，字号按物理像素自适应（1080p 正文约 145px em）
2. 鼠标移入不显示忙碌（转圈）光标；移到右上角 × 变手型
3. 只有点 × 或按 ESC 才关闭，点其它地方不关闭
4. 关闭弹窗后语音在 ~5 秒内停止（分句朗读 + 停止事件）
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from popup_win import show_popup
from broadcast import speak, _split_sentences

TITLE = "放学安全提醒"
TEXT = "同学们，放学了。路上请走人行道、看红绿灯，不在马路上追逐打闹。高高兴兴上学，平平安安回家。"

if __name__ == "__main__":
    parts = _split_sentences(TEXT)
    print(f"分句 {len(parts)} 段，最长 {max(len(p) for p in parts)} 字")

    stop = threading.Event()
    done = threading.Event()

    def tts():
        try:
            speak(TEXT, stop)
        finally:
            done.set()

    print("3 秒后弹窗 + 语音；请点右上角 × 或按 ESC 关闭，观察语音是否立刻停止")
    time.sleep(3)

    t = threading.Thread(target=tts, daemon=True)
    t.start()

    t0 = time.monotonic()
    # should_close=done.is_set：语音念完且满 15 秒才自动关闭
    show_popup(TITLE, TEXT, hold_seconds=15, should_close=done.is_set)
    closed_at = time.monotonic() - t0

    manual = not done.is_set()
    stop.set()
    t.join(timeout=12)
    silent_at = time.monotonic() - t0

    print(f"弹窗显示 {closed_at:.1f}s，{'手动关闭' if manual else '自动关闭'}")
    print(f"语音在 {silent_at:.1f}s 停止（关窗后 {silent_at - closed_at:.1f}s 内静音）")
    if manual:
        assert silent_at - closed_at < 8, "关窗后语音未及时停止"
    print("测试完成")
