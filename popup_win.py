# -*- coding: utf-8 -*-
"""
纯 ctypes Win32 全屏弹窗（替代 tkinter）。
红底大字 + 置顶 + ESC/点击关闭 + 定时自动关闭。
DPI 感知：字号/边距按物理像素动态计算，适配 100%~200% 缩放。
字号可由设置界面的「字号」档位缩放（show_popup 的 font_scale 参数）；该系数只抬高
理想字号，不抬高长文本的收缩下限，故不会重新引入 1080P@150% 的溢出裁切问题。
不依赖 tkinter，兼容无 GUI 模块的 Python 构建。
"""

import ctypes
import ctypes.wintypes as wt
import time

# 用独立 DLL 实例（而非共享的 ctypes.windll.user32），
# 避免与 tray_win.py 同时设置 RegisterClassExW.argtypes 时互相覆盖导致
# "expected LP_WNDCLASSEXW instance instead of LP_WNDCLASSEXW"（同名不同类型）。
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
shcore = ctypes.windll.shcore

# ---------- DPI 感知 ----------
# 优先使用 Per-Monitor V2（Win10 1703+），回退到 System DPI Aware
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
try:
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
except (AttributeError, OSError):
    try:
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except AttributeError:
            pass

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
WM_MOUSEMOVE = 0x0200
WM_SETCURSOR = 0x0020
WM_TIMER = 0x0113
VK_ESCAPE = 0x1B
SW_SHOWMAXIMIZED = 3
SW_SHOWNORMAL = 1
IDC_ARROW = 32512
IDC_HAND = 32649
HTCLIENT = 1
COLORREF = ctypes.c_ulong
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
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
RGB_CLOSE_BG = 0x004040C0     # BGR for #C04040 (close button background)
RGB_CLOSE_HOVER = 0x002020E8  # BGR for #E82020 (close button hover)
POPUP_CLASS = "ClassroomBroadcastPopup"
DT_CALCRECT = 0x00000400

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
user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int
# 标准光标用 MAKEINTRESOURCE（整数），故第二参声明为 c_void_p 而非 LPCWSTR
# restype 用 c_void_p（返回纯 int），便于直接赋给 WNDCLASSEXW.hCursor
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = ctypes.c_void_p
user32.SetCursor.argtypes = [wt.HANDLE]
user32.SetCursor.restype = wt.HANDLE
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.POINTER(RECT), wt.BOOL]
user32.InvalidateRect.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL

# ---------- 全局状态 ----------
_popup_state = {}
_popup_registered = False
_screen_w = 0
_screen_h = 0
_close_btn_rect = None   # 关闭按钮命中区域
_popup_hwnd = None       # 当前弹窗句柄，供外部线程关闭
_popup_opened_at = 0.0   # 弹窗打开时间戳
_btn_hover = False       # 鼠标是否悬停在关闭按钮上

def _calc_layout(rect, body_len, font_scale=1.0):
    """按屏幕物理像素计算字号、边距、关闭按钮位置。

    字号使用「em 高度」（CreateFont 传负值），比字符格高度更大更可控。
    正文很长时压缩标题高度，把空间让给正文。

    font_scale 是设置界面「字号」档位换算出的缩放系数，乘在按屏幕比例算出的基准
    字号上 —— 既让老师能调大小，又完整保留 DPI 自适应。注意它只抬高「理想字号」，
    **不抬高 _fit_font 的收缩下限**（下限由调用方按未缩放基准换算 min_ratio），
    否则长文本在大字号档会因下限过高而溢出裁切，P0 修过的 bug 会复发。
    """
    global _close_btn_rect
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    margin_x = max(48, int(w * 0.045))
    margin_top = max(28, int(h * 0.035))
    margin_bottom = max(36, int(h * 0.04))

    # 关闭按钮：右上角，边长为屏幕高度的 7%（触控友好）
    btn_size = max(56, int(h * 0.07))
    btn_margin = max(24, int(h * 0.025))
    _close_btn_rect = RECT(
        rect.right - btn_margin - btn_size,
        rect.top + btn_margin,
        rect.right - btn_margin,
        rect.top + btn_margin + btn_size,
    )

    # 标题：短正文给大标题，长正文压缩标题
    long_body = body_len > 120
    title_em = max(52, int(h * (0.075 if long_body else 0.115) * font_scale))
    title_h = int(title_em * 1.5)
    r_title = RECT(
        rect.left + margin_x,
        rect.top + margin_top,
        rect.right - margin_x - btn_size - btn_margin * 2,  # 避开关闭按钮
        rect.top + margin_top + title_h,
    )

    gap = int(h * (0.015 if long_body else 0.03))
    r_body = RECT(
        rect.left + margin_x,
        r_title.bottom + gap,
        rect.right - margin_x,
        rect.bottom - margin_bottom,
    )

    # 正文基准字号：屏幕高度的 8.5%（1080p ≈ 92px em），再乘字号档位系数
    body_em = max(34, int(h * 0.085 * font_scale))

    return r_title, r_body, title_em, body_em, btn_size


def _make_font(em_h):
    """创建微软雅黑粗体；em_h 为字符 em 高度（像素），内部转成负值传给 GDI。"""
    return gdi32.CreateFontW(
        -int(em_h), 0, 0, 0, FW_BOLD, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH, "Microsoft YaHei")


def _measure_height(hdc, text, r, em_h):
    """用 DT_CALCRECT 量出 text 在宽度 r 内、字号 em_h 下需要的高度。"""
    hfont = _make_font(em_h)
    old = gdi32.SelectObject(hdc, hfont)
    m = RECT(r.left, r.top, r.right, r.top)
    user32.DrawTextW(hdc, text, -1, ctypes.byref(m), DT_CALCRECT | DT_WORDBREAK)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(hfont)
    return m.bottom - m.top


def _fit_font(hdc, text, r, base_em, min_ratio=0.34, max_ratio=1.6):
    """自适应字号，返回 (字号, 实测高度)。

    - 短文本：在 base_em ~ base_em*max_ratio 间尽量放大，让教室后排也看得清
    - 长文本：按比例缩小，不低于 base_em*min_ratio
    """
    avail_h = r.bottom - r.top
    min_em = max(24, int(base_em * min_ratio))
    max_em = int(base_em * max_ratio)

    # 1) 尝试放大
    em = base_em
    while em < max_em:
        nxt = min(max_em, int(em * 1.15) + 1)
        if _measure_height(hdc, text, r, nxt) > avail_h:
            break
        em = nxt

    # 2) 按需缩小
    for _ in range(8):
        text_h = _measure_height(hdc, text, r, em)
        if text_h <= avail_h or em <= min_em:
            return em, text_h
        # 按面积比例收缩（宽高两个维度都要缩，故开平方更稳）
        ratio = (avail_h / float(text_h)) ** 0.5
        new_em = max(min_em, int(em * ratio))
        if new_em >= em:
            new_em = em - 1
        if new_em < min_em:
            return min_em, _measure_height(hdc, text, r, min_em)
        em = new_em
    return em, _measure_height(hdc, text, r, em)


def _draw_centered(hdc, text, r, em_h, color, text_h=None):
    """在 r 内水平+垂直居中绘制多行文本（DT_VCENTER 不支持多行，故手工偏移）。"""
    if text_h is None:
        text_h = _measure_height(hdc, text, r, em_h)
    avail_h = r.bottom - r.top
    top = r.top + max(0, (avail_h - text_h) // 2)
    draw_rect = RECT(r.left, top, r.right, top + max(text_h, 1))
    hfont = _make_font(em_h)
    old = gdi32.SelectObject(hdc, hfont)
    gdi32.SetTextColor(hdc, color)
    gdi32.SetBkMode(hdc, TRANSPARENT)
    user32.DrawTextW(hdc, text, -1, ctypes.byref(draw_rect),
                     DT_CENTER | DT_WORDBREAK)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(hfont)


def _popup_wndproc(hwnd, msg, wparam, lparam):
    global _btn_hover, _popup_hwnd
    if msg == WM_PAINT:
        ps = PAINTSTRUCT_FULL()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        rect = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))

        # 红底
        hbr = gdi32.CreateSolidBrush(RGB_RED)
        user32.FillRect(hdc, ctypes.byref(rect), hbr)
        gdi32.DeleteObject(hbr)

        # 计算布局
        title = _popup_state.get("title", "")
        body = _popup_state.get("body", "")
        scale = _popup_state.get("font_scale", 1.0)
        r_title, r_body, title_em, body_em, btn_size = _calc_layout(
            rect, len(body), scale)

        # 关闭按钮（右上角红底白 ×）—— 尺寸只跟屏幕走，不随字号档位变，
        # 保证它始终是可稳定命中的触控目标
        if _close_btn_rect:
            btn_color = RGB_CLOSE_HOVER if _btn_hover else RGB_CLOSE_BG
            hbr_btn = gdi32.CreateSolidBrush(btn_color)
            user32.FillRect(hdc, ctypes.byref(_close_btn_rect), hbr_btn)
            gdi32.DeleteObject(hbr_btn)
            _draw_centered(hdc, "×", _close_btn_rect,
                           max(30, int(btn_size * 0.62)), RGB_WHITE)

        # 字号档位只放大「理想字号」，收缩下限仍按未缩放基准算：把 min_ratio 除以
        # scale 后，下限 = int(base_em × scale × ratio ÷ scale) = 未缩放时的下限，
        # 因此长文本（节假日专题约 350 字）在 150% 档也不会被裁切。
        # 标题（黄色大字，同样自适应）
        if title:
            t_em, t_h = _fit_font(hdc, title, r_title, title_em,
                                  min_ratio=0.5 / scale, max_ratio=1.0)
            _draw_centered(hdc, title, r_title, t_em, RGB_YELLOW, t_h)

        # 正文（白色大字：长文本自动缩字号 + 真正垂直居中）
        if body:
            fitted_em, text_h = _fit_font(hdc, body, r_body, body_em,
                                          min_ratio=0.34 / scale)
            _draw_centered(hdc, body, r_body, fitted_em, RGB_WHITE, text_h)

        user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0

    elif msg == WM_SETCURSOR:
        # 必须显式设光标：否则系统沿用上一个窗口的光标，
        # 主线程忙于 TTS 时就会一直显示「转圈」忙碌光标。
        pt = wt.POINT()
        over_btn = False
        if _close_btn_rect and user32.GetCursorPos(ctypes.byref(pt)):
            over_btn = (_close_btn_rect.left <= pt.x <= _close_btn_rect.right and
                        _close_btn_rect.top <= pt.y <= _close_btn_rect.bottom)
        user32.SetCursor(user32.LoadCursorW(
            None, IDC_HAND if over_btn else IDC_ARROW))
        return 1

    elif msg == WM_MOUSEMOVE:
        if _close_btn_rect:
            x = lparam & 0xFFFF
            y = (lparam >> 16) & 0xFFFF
            hover = (_close_btn_rect.left <= x <= _close_btn_rect.right and
                     _close_btn_rect.top <= y <= _close_btn_rect.bottom)
            if hover != _btn_hover:
                _btn_hover = hover
                user32.InvalidateRect(hwnd, ctypes.byref(_close_btn_rect), True)
        return 0

    elif msg == WM_TIMER:
        # 1 秒轮询：到最短停留时间且 TTS 已念完才自动关闭
        if wparam == 1:
            elapsed = time.monotonic() - _popup_opened_at
            hold = _popup_state.get("hold", 12)
            max_hold = _popup_state.get("max_hold", 900)
            gate = _popup_state.get("should_close")
            ready = True if gate is None else bool(gate())
            if (elapsed >= hold and ready) or elapsed >= max_hold:
                user32.KillTimer(hwnd, 1)
                user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_KEYDOWN:
        if wparam == VK_ESCAPE:
            user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_LBUTTONDOWN:
        # 仅点击红底白 × 才关闭；点击其它区域不退出（避免上课误触）
        if _close_btn_rect:
            x = lparam & 0xFFFF
            y = (lparam >> 16) & 0xFFFF
            if (_close_btn_rect.left <= x <= _close_btn_rect.right and
                    _close_btn_rect.top <= y <= _close_btn_rect.bottom):
                user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_DESTROY:
        _popup_hwnd = None
        _btn_hover = False
        user32.KillTimer(hwnd, 1)
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
    # 关键：类光标为空时系统不会重置光标，鼠标移入会一直显示忙碌（转圈）光标
    wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
    wc.lpszClassName = POPUP_CLASS
    atom = user32.RegisterClassExW(ctypes.byref(wc))
    if not atom:
        raise OSError(f"RegisterClassEx popup failed: {kernel32.GetLastError()}")
    _popup_registered = True

def show_popup(title: str, text: str, hold_seconds: int = 12,
               should_close=None, max_hold_seconds: int = 900,
               font_scale: float = 1.0):
    """显示全屏红底弹窗，阻塞直到关闭。DPI 感知，字号按物理像素动态计算。

    参数：
        hold_seconds     最短停留秒数
        should_close     可选回调，返回 True 才允许到点自动关闭（用于等 TTS 念完）
        max_hold_seconds 兜底最长停留秒数，防止永不关闭
        font_scale       字号缩放系数（设置界面「字号」档位 ÷ 100），1.0 为基准。
                         非法值一律夹到 0.5~3.0，绝不因配置写坏而不弹窗。

    关闭方式：右上角红底白 ×、ESC 键、到点自动关闭、或外部调用 close_popup()。
    点击窗口其它区域不会关闭（避免上课误触）。
    """
    global _screen_w, _screen_h, _close_btn_rect
    global _popup_hwnd, _btn_hover, _popup_opened_at

    try:
        scale = float(font_scale)
    except (TypeError, ValueError):
        scale = 1.0
    if not scale == scale or scale <= 0:      # NaN 与非正数
        scale = 1.0
    scale = min(3.0, max(0.5, scale))

    _popup_state["title"] = title
    _popup_state["body"] = text
    _popup_state["hold"] = hold_seconds
    _popup_state["max_hold"] = max_hold_seconds
    _popup_state["should_close"] = should_close
    _popup_state["font_scale"] = scale
    _close_btn_rect = None
    _btn_hover = False
    _ensure_class()

    # DPI 感知后 GetSystemMetrics 返回物理像素
    _screen_w = user32.GetSystemMetrics(0)
    _screen_h = user32.GetSystemMetrics(1)

    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        POPUP_CLASS, "安全提醒",
        WS_POPUP | WS_VISIBLE,
        0, 0, _screen_w, _screen_h,
        None, None, kernel32.GetModuleHandleW(None), None)
    if not hwnd:
        raise OSError(f"CreateWindowEx popup failed: {kernel32.GetLastError()}")

    _popup_hwnd = hwnd
    _popup_opened_at = time.monotonic()

    user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)                 # 确保能收到 ESC 按键
    user32.SetTimer(hwnd, 1, 1000, None)  # 1 秒轮询关闭条件

    m = MSG()
    while user32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(m))
        user32.DispatchMessageW(ctypes.byref(m))

    _popup_hwnd = None


def close_popup():
    """从任意线程请求关闭当前弹窗；无弹窗时无操作。"""
    hwnd = _popup_hwnd
    if hwnd and user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def is_popup_open() -> bool:
    return _popup_hwnd is not None and bool(user32.IsWindow(_popup_hwnd))
