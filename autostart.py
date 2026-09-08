# -*- coding: utf-8 -*-
"""开机自启：HKCU Run 注册表项的读写。

设计要点：
- 只写 **HKCU**（当前用户），不需要管理员提权；教室机器通常单账号登录，
  HKLM 反而引入 UAC 弹窗。
- 状态以注册表为唯一事实来源，不进 config.json —— 两处各存一份迟早不一致
  （例如用户用 Autoruns 等工具删掉启动项后配置里还写着开）。
- 每次「启用」都重写一遍值：用户把程序目录搬家后，再点一次保存即自愈。
- 按 PRD 决策：仅注册表 Run key，不做任务计划兜底、不做崩溃自恢复。
"""

import os
import sys
import winreg

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 英文值名：部分国产安全软件/旧工具读中文值名会乱码，且 grep 日志友好
VALUE_NAME = "SafetyCast"


def startup_command(script: str | None = None) -> str:
    """登记到 Run 键的启动命令（路径一律加引号，容忍空格）。

    - 打包态（PyInstaller，sys.frozen）：直接指向 exe 自身
    - 开发态（.py 运行）：优先 pythonw.exe（无控制台黑窗）+ broadcast.py 绝对路径；
      同目录没有 pythonw.exe 时退回当前解释器
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    if script is None:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "broadcast.py")
    return f'"{exe}" "{script}"'


def is_enabled(root=winreg.HKEY_CURRENT_USER, path: str = RUN_KEY_PATH) -> bool:
    """Run 键下存在本程序的值即视为已启用（键不存在时返回 False，不抛异常）。"""
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def enable(root=winreg.HKEY_CURRENT_USER, path: str = RUN_KEY_PATH) -> str:
    """写入/刷新启动项，返回实际登记的命令串（便于记日志）。"""
    cmd = startup_command()
    with winreg.CreateKeyEx(root, path, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def disable(root=winreg.HKEY_CURRENT_USER, path: str = RUN_KEY_PATH) -> None:
    """删除启动项；值或键本来就不存在时静默跳过（幂等）。"""
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def set_enabled(on: bool) -> None:
    """按勾选状态落注册表。"""
    if on:
        enable()
    else:
        disable()
