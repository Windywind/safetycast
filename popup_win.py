# -*- coding: utf-8 -*-
"""
纯 ctypes Win32 全屏弹窗（替代 tkinter）。
红底大字 + 置顶 + ESC/点击关闭 + 定时自动关闭。
不依赖 tkinter，兼容无 GUI 模块的 Python 构建。
"""

import ctypes
import ctypes.wintypes as wt

# 用独立 DLL 实例（而非共享的 ctypes.windll.user32），
# 避免与 tray_win.py 同时设置 RegisterClassExW.argtypes 时互相覆盖导致
# "expected LP_WNDCLASSEXW instance instead of LP_WNDCLASSEXW"（同名不同类型）。
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ---------- 常量 ----------
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_LBUTTONDOWN = 0x0201
WM_TIMER = 0x0113
VK_ESCAPE = 0x1B
SW_SHOWMAXIMIZED = 3
SW_SHOWNORMAL = 1
COLORREF = ctypes.c_ulong
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_WORDBREAK = 0x00000010
FW_BOLD = 700
TRANSPARENT = 1
DEFAULT_CHARSET = 1
OUT_DEFAULT_PRECIS = 0
CLIP_DEFAULT_PRECIS = 0
CLEARTYPE_QUALITY = 5
DEFAULT_PITCH = 0
RGB_RED = 0x001E1EB0      # BGR for #B01E1E
RGB_WHITE = 0x00FFFFFF
RGB_YELLOW = 0x0000D7FF   # BGR for #FFD700
POPUP_CLASS = "ClassroomBroadcastPopup"

# ---------- 回调类型 ----------
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)

# ---------- 结构体（必须在 API 签名之前定义） ----------
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class PAINTSTRUCT_FULL(ctypes.Structure):
    _fields_ = [
        ("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", RECT),
        ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
        ("rgbReserved", ctypes.c_ubyte * 32),
    ]

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HANDLE),
        ("hIcon", wt.HANDLE), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HANDLE),
    ]

class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND), ("message", ctypes.c_uint),
        ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
        ("time", ctypes.c_ulong), ("pt", wt.POINT),
    ]

# ---------- API 签名 ----------
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = ctypes.c_ushort
user32.CreateWindowExW.argtypes = [
    ctypes.c_ulong, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_ulong,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HANDLE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_long
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT_FULL)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT_FULL)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(RECT), wt.HBRUSH]
user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int,
                              ctypes.POINTER(RECT), wt.UINT]
user32.DrawTextW.restype = ctypes.c_int
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_long, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
    wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SetTextColor.argtypes = [wt.HDC, COLORREF]
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.CreateSolidBrush.argtypes = [COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND,
                                ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_uint]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]

# ---------- 全局状态 ----------
_popup_state = {}
_popup_registered = False

def _popup_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_PAINT:
        ps = PAINTSTRUCT_FULL()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        rect = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))

        # 红底
        hbr = gdi32.CreateSolidBrush(RGB_RED)
        user32.FillRect(hdc, ctypes.byref(rect), hbr)
        gdi32.DeleteObject(hbr)

        # 标题（黄色大字）
        title = _popup_state.get("title", "")
        if title:
            r_title = RECT(rect.left + 80, rect.top + 60,
                           rect.right - 80, rect.top + 160)
            hfont = gdi32.CreateFontW(
                60, 0, 0, 0, FW_BOLD, 0, 0, 0,
                DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                DEFAULT_PITCH, "Microsoft YaHei")
            old = gdi32.SelectObject(hdc, hfont)
            gdi32.SetTextColor(hdc, RGB_YELLOW)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            user32.DrawTextW(hdc, title, -1, ctypes.byref(r_title),
                             DT_CENTER | DT_WORDBREAK)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(hfont)

        # 正文（白色大字）
        body = _popup_state.get("body", "")
        if body:
            r_body = RECT(rect.left + 120, rect.top + 180,
                           rect.right - 120, rect.bottom - 80)
            hfont2 = gdi32.CreateFontW(
                40, 0, 0, 0, FW_BOLD, 0, 0, 0,
                DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                DEFAULT_PITCH, "Microsoft YaHei")
            old2 = gdi32.SelectObject(hdc, hfont2)
            gdi32.SetTextColor(hdc, RGB_WHITE)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            user32.DrawTextW(hdc, body, -1, ctypes.byref(r_body),
                             DT_CENTER | DT_VCENTER | DT_WORDBREAK)
            gdi32.SelectObject(hdc, old2)
            gdi32.DeleteObject(hfont2)

        user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0

    elif msg == WM_TIMER:
        if wparam == 1:
            user32.KillTimer(hwnd, 1)
            user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_KEYDOWN:
        if wparam == VK_ESCAPE:
            user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_LBUTTONDOWN:
        user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0

    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

# 必须持有引用防止 GC
_popup_wndproc_ref = WNDPROC(_popup_wndproc)

def _ensure_class():
    global _popup_registered
    if _popup_registered:
        return
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = _popup_wndproc_ref
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = POPUP_CLASS
    atom = user32.RegisterClassExW(ctypes.byref(wc))
    if not atom:
        raise OSError(f"RegisterClassEx popup failed: {kernel32.GetLastError()}")
    _popup_registered = True

def show_popup(title: str, text: str, hold_seconds: int = 12):
    """显示全屏红底弹窗，阻塞直到关闭。"""
    _popup_state["title"] = title
    _popup_state["body"] = text
    _ensure_class()

    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        POPUP_CLASS, "安全提醒",
        WS_POPUP | WS_VISIBLE,
        0, 0, screen_w, screen_h,
        None, None, kernel32.GetModuleHandleW(None), None)
    if not hwnd:
        raise OSError(f"CreateWindowEx popup failed: {kernel32.GetLastError()}")

    user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
    user32.SetForegroundWindow(hwnd)
    user32.SetTimer(hwnd, 1, hold_seconds * 1000, None)

    m = MSG()
    while user32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(m))
        user32.DispatchMessageW(ctypes.byref(m))
