# -*- coding: utf-8 -*-
"""交互式测试：设置窗口（GUI + TTS），需在本机登录会话双击 test_settings.bat 运行。

为什么是交互式而非自动化
------------------------
踩坑 #3：AI 沙箱起的 GUI 进程只活在沙箱里，用户真实桌面看不到，故滚动/分组/音色/
试听/弹窗这些**视觉与音频**效果无法自动断言。本脚本把真实回调接好，由老师在自己
桌面上逐项肉眼/亲耳确认。纯逻辑（校验/合并/原子保存）已由 test_config_store.py 覆盖。

安全说明
--------
本脚本会把 broadcast.CONFIG_PATH 临时指向一个临时文件，因此点「保存并生效」只会写
临时文件 + 更新本进程内存配置，**不会改动你真实的 config.json**。进程退出即丢弃。

请对照下方清单逐项验证（窗口打开后按提示操作）。
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store
import broadcast
import settings_win

CHECKLIST = """
================ 设置窗口交互验证清单 ================
请逐项操作并肉眼/亲耳确认：

[1] 窗口外观：标题「课堂安全播报助手 — 设置」，任务栏/标题栏有 app.ico 图标。
[2] 分组齐全：从上到下应有 5 个带边框分组 ——
        放学时间 / 播报规则 / 假期列表 / 弹窗样式 / 语音。
[3] 缩放与滚动：拖窗口四边可改尺寸、最大化按钮可用；
        把窗口拖矮（或在小屏上）内容溢出时右侧出现滚动条，
        拖动滚动条、点上下箭头、在表单上滚滚轮，内容应平滑上下移动；
        底部「保存并生效 / 取消 / 恢复默认」三个按钮**固定不动**、不随内容滚动。
[4] 播报规则：每日/周五/节假日三行，各有「启用」勾选、提前分钟数、弹窗标题。
[5] 假期列表：多行文本框，可粘贴多行 YYYY-MM-DD；回车应换行（不触发保存）。
[6] 音色下拉：点开应列出「系统默认（跟随 Windows）」+ 本机已安装语音
        （如 Huihui / Kangkang，具体取决于这台机器装了哪些语音包）。
[7] 试听：选一个音色、调语速/音量，点「试听」——应听到用**当前未保存**参数
        朗读一句真实播报内容；点「停止试听」应立即中断。
[8] 校验：把放学时间改成 25:99（或把提前分钟改成 999），点「保存并生效」——
        应弹出中文错误提示，明确指出哪一项、允许范围，且窗口**不关闭**、
        焦点跳到出错控件，内容未写盘。改回合法值再保存。
[9] 恢复默认：点「恢复默认」，表单应回到出厂值（放学 16:30 等），但**未落盘**，
        仍需点保存才生效。
[10] 保存热生效：填合法值点「保存并生效」——窗口应自动关闭；
        控制台会打印保存后的配置摘要（写入的是临时文件，不动你的真实 config.json）。
[11] 单例：窗口开着时再次运行本脚本（或重复触发），不应开出第二个窗口。
[12] 取消：点「取消」或右上角 ×，窗口关闭且不保存任何改动。
[13] 两行提示：放学时间 / 弹窗样式 / 试听右侧的长提示应**完整显示两行**，
        没有任何一行被切掉；分组边框完整包住加高后的行
        （历史上试听行提示第二行被裁，看起来像"下面还有东西"）。
[14] 滚动条两态：窗口高度足以放下全部内容时（含拖高 / 最大化后），
        右侧**不应出现滚动条**（历史上是一条拖不动的满轨惰性条）；
        仅当把窗口拖矮 / 小屏 / 高 DPI 导致内容溢出时才出现滚动条，
        且此时拇指必须可拖。
=====================================================
"""


def main():
    # 把保存路径重定向到临时文件：验证「保存热生效」但不污染真实 config.json
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    tmp_path = tmp.name
    tmp.close()
    # 用当前真实配置作为临时文件初值，保证保存往返内容一致可比对
    config_store.save_atomic(tmp_path, config_store.CONFIG)
    broadcast.CONFIG_PATH = tmp_path

    print(CHECKLIST)
    print(f"[test] 保存将写入临时文件：{tmp_path}")
    print("[test] 3 秒后打开设置窗口，请按上方清单逐项验证……")
    time.sleep(3)

    result = {}

    def on_apply(clean):
        # 走 broadcast 真实热应用链路（落盘到临时文件 + 原地更新 CONFIG + 置脏标记）
        broadcast.apply_config(clean)
        result["applied"] = dict(clean)
        print("\n[test] 已保存并热生效，摘要：")
        print(f"       放学时间 = {clean.get('dismissal_time')}")
        print(f"       字号档位 = {clean.get('popup_font_scale')}%")
        print(f"       语速/音量 = {clean.get('tts_rate')} / {clean.get('tts_volume')}")
        voice = clean.get("tts_voice") or {}
        print(f"       音色 = {voice.get('name') or '系统默认'}")
        print(f"       最短停留 = {clean.get('popup_min_hold_seconds')} 秒")

    # show_settings 内部跑消息循环，必须在后台线程调用（踩坑 #9：线程亲和）
    t = threading.Thread(
        target=settings_win.show_settings,
        args=(on_apply, broadcast.preview_speak, broadcast.list_voices,
              broadcast.is_broadcast_running),
        daemon=True)
    t.start()
    t.join()

    print("\n[test] 设置窗口已关闭。")
    if "applied" in result:
        print("[test] 本次会话内你保存过一次配置（写入临时文件，真实 config.json 未改动）。")
    else:
        print("[test] 本次未保存（取消 / 直接关闭）。")

    # 清理临时文件
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    print("[test] 交互验证结束。请对照清单确认每一项是否符合预期。")


if __name__ == "__main__":
    main()
