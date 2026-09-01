# -*- coding: utf-8 -*-
"""
纯 ctypes Win32 托盘图标实现（替代 pystray）。
已实测：Shell_NotifyIcon NIM_ADD 在本机正常返回。
用法：
    app = TrayApp(ico_path, tooltip, menu_items, on_click=...)
    app.run()          # 阻塞主线程
    app.stop()         # 退出消息循环
menu_items: [(id:int, text:str, callback), ...]；id=0 表示分隔线。
"""

import ctypes
import ctypes.wintypes as wt
from ctypes import wintypes

# 用独立 DLL 实例（而非共享的 ctypes.windll.user32），
# 避免与 popup_win.py 同时设置 RegisterClassExW.argtypes 时互相覆盖导致
# "expected LP_WNDCLASSEXW instance instead of LP_WNDCLASSEXW"（同名不同类型）。
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---- 常量 ----
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TRAY = 0x8001
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_INFO = 0x01
IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x40
LR_LOADFROMFILE = 0x10
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTALIGN = 0x0002
TPM_BOTTOMALIGN = 0x0020
WS_POPUP = 0x80000000

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)

# ---- 结构体 ----
class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE), ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HANDLE),
    ]

class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", ctypes.c_uint),
        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
        ("time", ctypes.c_ulong), ("pt", wintypes.POINT),
    ]

class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("hWnd", wintypes.HWND),
        ("uID", ctypes.c_uint), ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint), ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128), ("dwState", ctypes.c_ulong),
        ("dwStateMask", ctypes.c_ulong), ("szInfo", ctypes.c_wchar * 256),
        ("uTimeout", ctypes.c_uint), ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_ulong),
    ]

# ---- API 签名 ----
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_long
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = ctypes.c_ushort
user32.CreateWindowExW.argtypes = [
    ctypes.c_ulong, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_ulong,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HANDLE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_uint, wintypes.LPCWSTR]
shell32.Shell_NotifyIconW.argtypes = [ctypes.c_ulong, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
user32.LoadImageW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_uint,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.LoadImageW.restype = wintypes.HANDLE


class TrayApp:
    def __init__(self, ico_path: str, tooltip: str, menu_items, on_click=None):
        """menu_items: [(id, text, callback), ...]，id=0 表示分隔线。"""
        self.ico_path = ico_path
        self.tooltip = tooltip[:127]
        self.menu_items = menu_items
        self.on_click = on_click
        self._handlers = {mid: cb for mid, _, cb in menu_items if mid}
        self.hwnd = None
        self._wndproc = WNDPROC(self._wnd_proc)  # 必须持有引用防止 GC
        self._class_name = "ClassroomBroadcastTrayWnd"
        self._nid = None

    # ---- Win32 窗口与托盘 ----

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                if self.on_click:
                    self.on_click()
                return 0
            if lparam == WM_RBUTTONUP:
                self._show_menu()
                return 0
        elif msg == WM_COMMAND:
            cb = self._handlers.get(wparam & 0xFFFF)
            if cb:
                cb()
            return 0
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.pointer(self._nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create(self):
        wc = WNDCLASSEXW(
            cbSize=ctypes.sizeof(WNDCLASSEXW), style=0,
            lpfnWndProc=self._wndproc, cbClsExtra=0, cbWndExtra=0,
            hInstance=kernel32.GetModuleHandleW(None), hIcon=None,
            hCursor=None, hbrBackground=None, lpszMenuName=None,
            lpszClassName=self._class_name, hIconSm=None)
        if not user32.RegisterClassExW(ctypes.pointer(wc)):
            raise OSError(f"RegisterClassEx 失败: {kernel32.GetLastError()}")
        self.hwnd = user32.CreateWindowExW(
            0, self._class_name, self.tooltip, WS_POPUP,
            0, 0, 0, 0, None, None, kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError(f"CreateWindowEx 失败: {kernel32.GetLastError()}")
        hicon = user32.LoadImageW(
            None, self.ico_path, IMAGE_ICON, 0, 0, LR_DEFAULTSIZE | LR_LOADFROMFILE)
        self._nid = NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
            uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY,
            hIcon=hicon, szTip=self.tooltip)
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.pointer(self._nid)):
            raise OSError(f"Shell_NotifyIcon NIM_ADD 失败: {kernel32.GetLastError()}")

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        for mid, text, _ in self.menu_items:
            if mid == 0:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                user32.AppendMenuW(menu, MF_STRING, mid, text)
        user32.SetForegroundWindow(self.hwnd)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.pointer(pt))
        user32.TrackPopupMenu(menu, TPM_RIGHTALIGN | TPM_BOTTOMALIGN,
                              pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)

    def run(self):
        """阻塞：创建托盘并运行消息循环。"""
        self._create()
        msg = MSG()
        while user32.GetMessageW(ctypes.pointer(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
