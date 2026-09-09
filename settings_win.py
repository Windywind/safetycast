# -*- coding: utf-8 -*-
"""纯 ctypes Win32 单页分组设置窗口（不引入 tkinter / PyQt / wxPython）。

设计要点
--------
1. **只用 user32 标准控件**：STATIC / EDIT / BUTTON（BS_GROUPBOX、BS_AUTOCHECKBOX、
   BS_PUSHBUTTON）/ COMBOBOX。不用 SysTabControl32、Trackbar、ListView —— 那些属
   公共控件，必须 InitCommonControlsEx 并处理 ICC_* 类注册，纯 ctypes 下坑多且与
   现有 DPI 感知设置易冲突。语速/音量/字号因此用**预置档位下拉框**而非滑块，
   既规避 Trackbar 依赖，也从根上消除非法输入。

2. **三层窗口实现滚动**（而非逐个 MoveWindow 平移 N 个子控件）：
       hwnd_main    框架 + 底部固定按钮
         └ hwnd_view    WS_CHILD | WS_VSCROLL | WS_CLIPCHILDREN，可视区
             └ hwnd_content  WS_CHILD，高度 = 内容总高，滚动时整体 MoveWindow 一次
                 └ 全部表单控件
   子窗口天然被父窗口客户区裁剪，故 content 超出 view 的部分自动不可见、不可点，
   无需 WS_CLIPSIBLINGS 之类的 z-order 处理。底部按钮挂在 hwnd_main 上、位于
   hwnd_view 之外，因此**固定不滚动**，符合对话框惯例。

3. **COMBOBOX 高度语义**（已用无头探针实测确认）：CBS_DROPDOWNLIST 的 nHeight 是
   「闭合高度 + 下拉列表高度」，但系统会把**窗口矩形裁剪为闭合高度**（实测请求 200
   得到 24），下拉区不占矩形。故可放心给足下拉高度，不会遮挡下方控件的点击。

4. **依赖注入断环**：本模块不 import broadcast（否则与 broadcast 的延迟导入构成
   概念上的环，且会把 comtypes/SAPI / popup_win / tray_win 拖进测试依赖）。保存、
   试听、音色枚举全部由调用方注入回调，本模块只依赖 config_store / autostart
   （两者均为纯 stdlib 逻辑模块，可进单元测试）。

5. **绝不在 wndproc 里同步做耗时操作**：落盘、试听朗读均派发后台线程，理由同
   tray_win._fire 的注释（否则窗口假死、鼠标转圈、WM_CLOSE 处理不了导致进程残留）。

6. **消息循环线程亲和**：本窗口由托盘 _fire() 派生的后台 daemon 线程创建并跑自己的
   GetMessageW 循环。各线程 GetMessageW(hwnd=None) 只取本线程窗口消息，与托盘主线程、
   弹窗工作线程互不干扰；WM_DESTROY 里的 PostQuitMessage 只结束本线程循环。
"""

import copy
import ctypes
import ctypes.wintypes as wt
import datetime
import math
import os
import re
import sys
import threading
import traceback

import autostart
import config_store

# 用独立 DLL 实例（而非共享的 ctypes.windll.user32），避免与 popup_win.py /
# tray_win.py 同时设置 RegisterClassExW.argtypes 时互相覆盖导致
# "expected LP_WNDCLASSEXW instance instead of LP_WNDCLASSEXW"（同名不同类型）。
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

# 冻结态下锚定 exe 所在目录（理由同 broadcast.py 的 BASE_DIR 注释）
BASE_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
DEBUG_LOG = os.path.join(BASE_DIR, "log", "debug.log")
# 图标解析与 broadcast.py 同策略：exe 旁优先，否则 onefile 包内 _MEIPASS 副本
if getattr(sys, "frozen", False):
    _ico_beside = os.path.join(BASE_DIR, "app.ico")
    ICO_PATH = (_ico_beside if os.path.exists(_ico_beside)
                else os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "app.ico"))
else:
    ICO_PATH = os.path.join(BASE_DIR, "app.ico")

# ---------- 常量 ----------

SETTINGS_CLASS = "ClassroomBroadcastSettingsWnd"
ERROR_CLASS_ALREADY_EXISTS = 1410

# 窗口样式
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_THICKFRAME = 0x00040000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
WS_CLIPCHILDREN = 0x02000000
WS_TABSTOP = 0x00010000
WS_EX_CONTROLPARENT = 0x00010000

# 控件样式
ES_LEFT = 0x0000
ES_MULTILINE = 0x0004
ES_AUTOHSCROLL = 0x0080
ES_AUTOVSCROLL = 0x0040
ES_WANTRETURN = 0x1000
CBS_DROPDOWNLIST = 0x0003
CBS_AUTOVSCROLL = 0x0040
BS_PUSHBUTTON = 0x00000000
BS_DEFPUSHBUTTON = 0x00000001
BS_AUTOCHECKBOX = 0x00000003
BS_GROUPBOX = 0x00000007
SS_LEFT = 0x00000000

# 消息
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_SIZE = 0x0005
WM_MOUSEWHEEL = 0x020A
WM_VSCROLL = 0x0115
WM_SETFONT = 0x0030
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CTLCOLORSTATIC = 0x0138
WM_APP_PREVIEW_DONE = 0x8001      # 试听线程收尾后回 UI 线程刷新按钮状态

# 通知码 / 标准按钮 ID
IDOK = 1
IDCANCEL = 2

# 控件 ID
ID_RESET = 3
ID_PREVIEW = 11
ID_PREVIEW_STOP = 12
ID_DISMISSAL = 20
ID_HOLD = 21
ID_HOLIDAYS = 22
ID_FONT = 23
ID_RATE = 24
ID_VOLUME = 25
ID_VOICE = 26
ID_AUTOSTART = 27
ID_RULE_BASE = 100      # 每条规则占 3 个：enabled / lead / title

# 滚动条
SB_VERT = 1
SB_LINEUP, SB_LINEDOWN = 0, 1
SB_PAGEUP, SB_PAGEDOWN = 2, 3
SB_THUMBTRACK = 5
SB_ENDSCROLL = 8
SIF_RANGE = 0x0001
SIF_PAGE = 0x0002
SIF_POS = 0x0004
SIF_DISABLENOSCRROLL = 0x0008
SIF_ALL = SIF_RANGE | SIF_PAGE | SIF_POS | SIF_DISABLENOSCRROLL
SCROLL_STEP = 24

# 换行模拟的 token 切分：拉丁/数字串整体不可断、空白可断、其余（CJK/标点）单字可断
_TOKEN_RE = re.compile(r"[A-Za-z0-9:%.\-+()/]+|\s|.")

# 复选框 / 下拉框
BM_SETCHECK = 0x00F1
BM_GETCHECK = 0x00F0
BST_UNCHECKED = 0
BST_CHECKED = 1
CB_ADDSTRING = 0x0143
CB_GETCURSEL = 0x0147
CB_SETCURSEL = 0x014E

# 系统度量 / 颜色 / 字体
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_CXVSCROLL = 2
SPI_GETWORKAREA = 0x0030
COLOR_BTNFACE = 15
LOGPIXELSY = 90
TRANSPARENT = 1
FW_NORMAL = 400
FW_BOLD = 700
DEFAULT_CHARSET = 1
OUT_DEFAULT_PRECIS = 0
CLIP_DEFAULT_PRECIS = 0
CLEARTYPE_QUALITY = 5
DEFAULT_PITCH = 0
IDC_ARROW = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
LR_DEFAULTSIZE = 0x40
MB_OK = 0x00000000
MB_ICONERROR = 0x00000010
MB_ICONINFORMATION = 0x00000040
SW_SHOW = 5
SW_RESTORE = 9

# ---------- 布局（逻辑像素 @96DPI，创建时统一乘 scale） ----------

WIN_CLIENT_W = 520      # 主窗口客户区宽（不含滚动条）
PAD = 14                # 窗口内边距
GROUP_GAP = 12          # 组间距
GROUP_TITLE_H = 22      # 组标题占位高
GROUP_PAD_X = 10        # 组内左右留白
GROUP_PAD_BOTTOM = 10   # 组内底部留白
ROW_H = 28              # 行高
CTRL_H = 24             # 控件高
BAR_H = 54              # 底部按钮栏高
BTN_W = 104
BTN_H = 28
COMBO_DROP = 220        # 下拉展开高度（不占窗口矩形，见模块 docstring 第 3 点）
HOLIDAY_EDIT_H = 92

INNER_X = PAD + GROUP_PAD_X                            # 组内控件起始 x = 24
INNER_W = WIN_CLIENT_W - 2 * PAD - 2 * GROUP_PAD_X     # 组内可用宽 = 472

# 播报规则行的列布局（组内相对坐标）
COL_CHECK_W = 96
COL_LEAD_LBL_X = 104
COL_LEAD_LBL_W = 34
COL_LEAD_X = 140
COL_LEAD_W = 44
COL_UNIT_X = 188
COL_UNIT_W = 30
COL_TITLE_LBL_X = 224
COL_TITLE_LBL_W = 32
COL_TITLE_X = 258
COL_TITLE_W = INNER_W - COL_TITLE_X                    # 214

# 档位标签（顺序必须与 config_store 里的 tiers 元组严格一致）
FONT_SCALE_LABELS = ("90%（较小）", "100%（标准）", "115%（较大）",
                     "130%（大）", "150%（特大）")
TTS_RATE_LABELS = ("较慢", "慢", "标准", "快", "较快")
TTS_VOLUME_LABELS = ("50%", "70%", "85%", "100%（最大）")
DEFAULT_VOICE_LABEL = "系统默认（跟随 Windows）"

# 校验失败时按关键词把焦点送到出错控件（值为 self.ctl 的逻辑名）
_FOCUS_HINTS = (
    ("放学时间", "dismissal"),
    ("提前分钟", "lead"),
    ("标题", "title"),
    ("假期", "holidays"),
    ("停留", "hold"),
    ("字号", "font"),
    ("语速", "rate"),
    ("音量", "volume"),
    ("音色", "voice"),
)

# 长提示文案与逻辑宽的单一数据源：实测换行高度与创建标签共用此表，
# 保证「测量宽 == 创建宽」，两边换行行数必然一致。宽须与 _build_form 的 x 偏移配套。
_HINT_SPECS = (
    ("dismissal_hint", "24 小时制 HH:MM。播报按下方规则在此时刻前提前触发。",
     INNER_W - 156),
    ("holiday_hint", "每行一个日期，格式 YYYY-MM-DD（如 2026-10-01）；空行忽略，"
                     "可整学期批量粘贴。", INNER_W),
    ("font_hint", "在屏幕自适应字号基础上的缩放；长文本仍会自动收缩，不会裁切。",
     INNER_W - 236),
    ("hold_hint", "秒。语音念完后弹窗至少停留这么久，便于学生看清。",
     INNER_W - 146),
    ("preview_hint", "试听会用当前的语音设置朗读今日「每日提醒」内容",
     INNER_W - 196),
)
_HINT_BY_KEY = {k: (t, w) for k, t, w in _HINT_SPECS}


def _is_cjk(ch: str) -> bool:
    """CJK 及全角标点单字可断行（与 DrawText 默认 WordBreak 行为一致）。"""
    return ord(ch) >= 0x2E80

# ---------- 回调类型 ----------

# 返回类型用 c_ssize_t（LONG_PTR）而非 popup_win 的 c_long：本模块的 wndproc 要在
# WM_CTLCOLORSTATIC 里返回画刷句柄，而 LRESULT 在 64 位下是 64 位量，c_long 会截断。
# 各模块持有独立的 WinDLL 实例与独立的 WNDPROC 类型，此处与 popup_win 不一致不会冲突。
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)

# ---------- 结构体（必须在引用它们的 API 签名之前定义） ----------


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND), ("message", ctypes.c_uint),
        ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
        ("time", ctypes.c_ulong), ("pt", POINT),
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


class SIZE(ctypes.Structure):
    _fields_ = [
        ("cx", ctypes.c_long),
        ("cy", ctypes.c_long),
    ]


class SCROLLINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("fMask", wt.UINT),
        ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
        ("nPage", wt.UINT), ("nPos", ctypes.c_int), ("nTrackPos", ctypes.c_int),
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
user32.DefWindowProcW.restype = ctypes.c_ssize_t      # LRESULT = LONG_PTR
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
# IsDialogMessageW 让非对话框窗口也获得 Tab 焦点遍历与 Enter/Esc 语义
user32.IsDialogMessageW.argtypes = [wt.HWND, ctypes.POINTER(MSG)]
user32.IsDialogMessageW.restype = wt.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.EnableWindow.argtypes = [wt.HWND, wt.BOOL]
user32.EnableWindow.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.MoveWindow.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wt.BOOL]
user32.MoveWindow.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.GetSysColorBrush.argtypes = [ctypes.c_int]
user32.GetSysColorBrush.restype = wt.HBRUSH
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = ctypes.c_void_p
user32.LoadImageW.argtypes = [wt.HANDLE, wt.LPCWSTR, ctypes.c_uint,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.LoadImageW.restype = wt.HANDLE
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_uint]
user32.MessageBoxW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.SetScrollInfo.argtypes = [wt.HWND, ctypes.c_int,
                                 ctypes.POINTER(SCROLLINFO), wt.BOOL]
user32.SetScrollInfo.restype = ctypes.c_int
user32.GetScrollInfo.argtypes = [wt.HWND, ctypes.c_int, ctypes.POINTER(SCROLLINFO)]
user32.GetScrollInfo.restype = wt.BOOL
# SendMessageW 的 lParam 声明为 c_void_p：既能传整数（BST_CHECKED 等），也能传
# 字符串（CB_ADDSTRING / WM_GETTEXT 缓冲区，ctypes 自动转 wchar_t*）。
user32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, ctypes.c_void_p]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.SetWindowTextW.restype = wt.BOOL
user32.ShowScrollBar.argtypes = [wt.HWND, ctypes.c_int, wt.BOOL]
user32.ShowScrollBar.restype = wt.BOOL
user32.SystemParametersInfoW.argtypes = [
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
user32.SystemParametersInfoW.restype = wt.BOOL

gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_long, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
    wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.GetTextExtentPoint32W.argtypes = [
    wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(SIZE)]
gdi32.GetTextExtentPoint32W.restype = wt.BOOL

kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetLastError.restype = wt.DWORD


# ---------- 模块级辅助 ----------

def _dbg(msg: str):
    """写 log/debug.log。与 broadcast._dbg 同格式，但刻意不 import broadcast（断环）。"""
    try:
        os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _dpi_scale() -> float:
    """按 LOGPIXELSY/96 取缩放系数，夹在 [1.0, 3.0]。

    进程级 DPI 感知已由 popup_win.py 在导入时设置（Per-Monitor V2 → System DPI
    Aware → SetProcessDPIAware 三级回退），此处无需重复设置。
    """
    hdc = user32.GetDC(None)
    dpi = 0
    try:
        if hdc:
            dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSY)
    finally:
        if hdc:
            user32.ReleaseDC(None, hdc)
    if not dpi:
        return 1.0
    return min(3.0, max(1.0, dpi / 96.0))


def _make_font(em_h: int, bold: bool = False):
    """创建微软雅黑字体；em_h 为字符 em 高度（像素），内部转负值传给 GDI。

    与 popup_win._make_font 同款写法，字族一致以保持全应用视觉统一。
    """
    return gdi32.CreateFontW(
        -int(em_h), 0, 0, 0, FW_BOLD if bold else FW_NORMAL, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH, "Microsoft YaHei")


class SettingsWindow:
    """单页分组设置窗口。一个实例对应一个窗口，跑完消息循环即废弃。"""

    def __init__(self, on_apply, preview_speak, list_voices, is_busy=None):
        self._on_apply = on_apply
        self._preview_speak = preview_speak
        self._list_voices = list_voices
        self._is_busy = is_busy

        self.scale = _dpi_scale()
        self.hinstance = kernel32.GetModuleHandleW(None)
        # WNDPROC 必须持有引用防 GC：回调被回收后窗口消息触发即崩溃
        self._wndproc = WNDPROC(self._wnd_proc)

        # 字体在创建任何控件之前就绪（_create 会立即 WM_SETFONT）
        em = max(12, int(round(13 * self.scale)))
        self._font = _make_font(em)
        self._font_bold = _make_font(em, bold=True)
        self._fonts = [self._font, self._font_bold]

        self.hwnd_main = None
        self.hwnd_view = None
        self.hwnd_content = None
        self.ctl: dict = {}            # 逻辑名 -> 控件 HWND
        self._voice_items: list = []   # [(id, name), ...]，索引与音色下拉框严格对应
        self._content_h = 0
        self._scroll_pos = 0
        self._hint_h: dict = {}      # key -> 实测换行高（逻辑像素），开窗期间缓存
        self._preview_stop = threading.Event()
        self._preview_thread = None
        self._closing = False

    # ---- 尺寸换算 ----

    def _s(self, v) -> int:
        """逻辑像素 -> 物理像素。"""
        return max(1, int(round(v * self.scale)))

    # ---- 控件创建 ----

    def _create(self, cls_name, text, style, x, y, w, h, cid=0,
                parent=None, bold=False, tabstop=True):
        # WS_TABSTOP 只给可交互控件：STATIC / GROUPBOX 带上它会让 Tab 焦点停在纯文字
        # 标签上，老师得按很多次 Tab 才能走完表单。
        base_style = WS_CHILD | WS_VISIBLE | style
        if tabstop:
            base_style |= WS_TABSTOP
        hwnd = user32.CreateWindowExW(
            0, cls_name, text, base_style,
            self._s(x), self._s(y), self._s(w), self._s(h),
            parent if parent is not None else self.hwnd_content,
            cid, self.hinstance, None)
        if not hwnd:
            raise OSError(
                f"CreateWindowEx 失败({cls_name}): {kernel32.GetLastError()}")
        # 统一字体；lParam=1 表示立即重绘
        user32.SendMessageW(hwnd, WM_SETFONT,
                            self._font_bold if bold else self._font, 1)
        return hwnd

    def _label(self, text, x, y, w, h=CTRL_H, bold=False):
        return self._create("Static", text, SS_LEFT, x, y, w, h,
                            bold=bold, tabstop=False)

    def _measure_text_h(self, text: str, w_logical: int) -> int:
        """用当前字体实测 text 在 w_logical 宽内换行后的高度（逻辑像素）。

        STATIC 的 SS_LEFT 会按矩形宽自动换行，但矩形太矮时多出的行被裁掉 ——
        这正是「提示第二行消失、看似下面还有内容」的根因，故标签高度必须按
        实测换行高给足。

        度量通路用 GetTextExtentPoint32W 逐 token 贪心模拟 DT_WORDBREAK 的断行
        （空白处可断、CJK 单字可断、拉丁串整体不可断）。不用
        DrawTextW(DT_CALCRECT)：本机实测该调用被端点钩子废掉 —— uFormat 被忽略、
        RECT 永不回写（宽 50 仍报单行），而 gdi32 的度量调用完全正常。
        结果夹在 [CTRL_H, 3*CTRL_H]，防异常度量值把布局撑坏。
        """
        limit = max(1, self._s(w_logical))
        hdc = user32.GetDC(None)
        if not hdc:
            return CTRL_H
        old = None
        try:
            old = gdi32.SelectObject(hdc, self._font)
            sz = SIZE()
            if not gdi32.GetTextExtentPoint32W(hdc, "测", 1, ctypes.byref(sz)):
                return CTRL_H
            line_h = max(1, int(sz.cy))
            n_lines = self._count_wrap_lines(hdc, text, limit)
        finally:
            # 还原 DC 上的旧对象再释放，防 GDI 泄漏
            if old:
                gdi32.SelectObject(hdc, old)
            user32.ReleaseDC(None, hdc)
        h = int(math.ceil(n_lines * line_h / self.scale))
        return max(CTRL_H, min(CTRL_H * 3, h))

    def _count_wrap_lines(self, hdc, text: str, limit: int) -> int:
        """贪心模拟 DT_WORDBREAK 断行，返回行数（物理像素口径）。"""
        widths: dict = {}
        lines = 1
        cur = 0
        prev_breakable = False   # 上一个 token 之后是否允许断行
        for tok in _TOKEN_RE.findall(text):
            w = widths.get(tok)
            if w is None:
                sz = SIZE()
                gdi32.GetTextExtentPoint32W(hdc, tok, len(tok),
                                            ctypes.byref(sz))
                w = widths[tok] = int(sz.cx)
            if tok.isspace():
                cur += w
                prev_breakable = True
                continue
            break_before = prev_breakable or _is_cjk(tok[0])
            if cur > 0 and break_before and cur + w > limit:
                lines += 1
                cur = w
            else:
                cur += w
            prev_breakable = _is_cjk(tok[-1])
        return lines

    def _hint_heights(self) -> dict:
        """各长提示的实测换行高缓存；_planned_content_h 与 _build_form 共用同一份。"""
        if not self._hint_h:
            self._hint_h = {k: self._measure_text_h(t, w)
                            for k, t, w in _HINT_SPECS}
            _dbg(f"[settings] 提示实测高(逻辑px): {self._hint_h}")
        return self._hint_h

    def _hint(self, key: str, x: int, y: int) -> int:
        """按实测换行高创建提示标签（文案/宽/高全部取自单一数据源），返回占用高。"""
        text, w = _HINT_BY_KEY[key]
        h = self._hint_heights()[key]
        self._label(text, x, y, w, h=h)
        return h

    def _planned_content_h(self) -> int:
        """按实测行高推算内容总高（逻辑像素），用于开窗前确定窗口尺寸。

        与 _build_form 共用同一份实测高度缓存，故预估 == 实测，
        _create_windows 里的一致性自检恒过；屏幕允许时窗口一次开够高，
        装不下时再由滚动条兜底。
        """
        hh = self._hint_heights()
        groups = (
            GROUP_TITLE_H + max(ROW_H, hh["dismissal_hint"]) + GROUP_PAD_BOTTOM,
            GROUP_TITLE_H + ROW_H * len(config_store.RULE_KINDS) + GROUP_PAD_BOTTOM,
            GROUP_TITLE_H + hh["holiday_hint"] + 4 + HOLIDAY_EDIT_H + GROUP_PAD_BOTTOM,
            GROUP_TITLE_H + max(ROW_H, hh["font_hint"])
            + max(ROW_H, hh["hold_hint"]) + GROUP_PAD_BOTTOM,
            GROUP_TITLE_H + ROW_H * 2 + max(BTN_H, hh["preview_hint"])
            + GROUP_PAD_BOTTOM,
            GROUP_TITLE_H + ROW_H + GROUP_PAD_BOTTOM,   # 组 6：启动
        )
        gaps = GROUP_GAP * (len(groups) - 1)
        return PAD + sum(groups) + gaps + PAD

    def _edit(self, x, y, w, cid, h=CTRL_H, multiline=False):
        style = ES_LEFT | ES_AUTOHSCROLL
        if multiline:
            # ES_WANTRETURN 让多行框自己吃掉回车（换行），不触发默认按钮「保存」
            style = ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN
        return self._create("Edit", "", style, x, y, w, h, cid)

    def _combo(self, x, y, w, cid):
        # 高度给足下拉展开空间；系统会把窗口矩形裁剪为闭合高度，不遮挡下方控件
        return self._create("ComboBox", "", CBS_DROPDOWNLIST | CBS_AUTOVSCROLL,
                            x, y, w, COMBO_DROP, cid)

    def _check(self, text, x, y, w, cid):
        return self._create("Button", text, BS_AUTOCHECKBOX, x, y, w, CTRL_H, cid)

    def _group(self, y, title, inner_h):
        """画 GROUPBOX，返回 (组内第一行 y, 下一组 y)。"""
        total_h = GROUP_TITLE_H + inner_h + GROUP_PAD_BOTTOM
        self._create("Button", title, BS_GROUPBOX,
                     PAD, y, WIN_CLIENT_W - 2 * PAD, total_h,
                     bold=True, tabstop=False)
        return y + GROUP_TITLE_H, y + total_h + GROUP_GAP

    # ---- 控件读写 ----

    def _get_text(self, hwnd) -> str:
        n = int(user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0))
        buf = ctypes.create_unicode_buffer(max(2, n + 2))
        user32.SendMessageW(hwnd, WM_GETTEXT, len(buf), buf)
        return buf.value

    def _set_text(self, hwnd, text):
        user32.SetWindowTextW(hwnd, str(text))

    def _get_check(self, hwnd) -> bool:
        return user32.SendMessageW(hwnd, BM_GETCHECK, 0, 0) == BST_CHECKED

    def _set_check(self, hwnd, value: bool):
        user32.SendMessageW(hwnd, BM_SETCHECK,
                            BST_CHECKED if value else BST_UNCHECKED, 0)

    def _cb_fill(self, hwnd, labels):
        for text in labels:
            user32.SendMessageW(hwnd, CB_ADDSTRING, 0, text)

    def _cb_index(self, hwnd) -> int:
        return int(user32.SendMessageW(hwnd, CB_GETCURSEL, 0, 0))

    def _cb_select(self, hwnd, idx: int):
        user32.SendMessageW(hwnd, CB_SETCURSEL, max(0, int(idx)), 0)

    def _cb_select_tier(self, hwnd, tiers, value, default_value):
        """按当前值选中档位；值不在档位表中（手工改过 config.json）则选默认档位。"""
        ordered = list(tiers)
        try:
            idx = ordered.index(value)
        except ValueError:
            try:
                idx = ordered.index(default_value)
            except ValueError:
                idx = 0
        self._cb_select(hwnd, idx)

    def _tier_value(self, key, tiers):
        idx = self._cb_index(self.ctl[key])
        if 0 <= idx < len(tiers):
            return tiers[idx]
        return tiers[0]

    # ---- 音色下拉 ----

    def _fill_voices(self, current: dict):
        """枚举本机音色填充下拉框，首项固定为「系统默认」。

        枚举失败（教室机器没装任何语音包 / SAPI 异常）时只剩首项，界面照常可用 ——
        这与 broadcast._new_engine 的优雅回退是同一套容错思路。
        """
        hwnd = self.ctl["voice"]
        self._voice_items = [("", config_store.DEFAULT_VOICE_NAME)]
        try:
            for vid, name in (self._list_voices() or []):
                self._voice_items.append((str(vid), str(name)))
        except Exception:
            _dbg("[settings] 音色枚举回调异常:\n" + traceback.format_exc())

        self._cb_fill(hwnd, [DEFAULT_VOICE_LABEL if i == 0 else name
                            for i, (_, name) in enumerate(self._voice_items)])

        # 选中当前配置的音色；id 已不在本机（换过机器）时回落首项并提示
        wanted = str((current or {}).get("id") or "").strip()
        idx = 0
        if wanted:
            for i, (vid, _) in enumerate(self._voice_items):
                if vid == wanted:
                    idx = i
                    break
            else:
                label = str((current or {}).get("name") or wanted)
                _dbg(f"[settings] 配置音色本机不存在（{label}），界面回落系统默认")
        self._cb_select(hwnd, idx)
        _dbg(f"[settings] 音色下拉共 {len(self._voice_items)} 项，选中第 {idx} 项")

    def _current_voice(self) -> dict:
        idx = self._cb_index(self.ctl["voice"])
        if 0 <= idx < len(self._voice_items):
            vid, name = self._voice_items[idx]
        else:
            vid, name = "", config_store.DEFAULT_VOICE_NAME
        return {"id": vid, "name": name}

    # ---- 表单构建 ----

    def _build_form(self):
        cfg = config_store.CONFIG
        rules = cfg.get("rules") or {}
        defaults = config_store.DEFAULT_CONFIG

        # 各长提示的实测换行高：行高 / 组高 / 内容总高全部由此驱动，
        # 保证开窗预估与建表单实测同源，提示第二行不再被裁
        hh = self._hint_heights()

        # 组 1：放学时间
        r1 = max(ROW_H, hh["dismissal_hint"])
        y, y_next = self._group(PAD, "放学时间", r1)
        self._label("放学时间", INNER_X, y, 70)
        self.ctl["dismissal"] = self._edit(INNER_X + 76, y, 70, ID_DISMISSAL)
        self._set_text(self.ctl["dismissal"], cfg.get("dismissal_time", "16:30"))
        self._hint("dismissal_hint", INNER_X + 156, y)
        y = y_next

        # 组 2：播报规则
        kinds = config_store.RULE_KINDS
        y, y_next = self._group(y, "播报规则", ROW_H * len(kinds))
        for i, kind in enumerate(kinds):
            row_y = y + i * ROW_H
            entry = rules.get(kind) or {}
            dflt = defaults["rules"][kind]
            base = ID_RULE_BASE + i * 3

            self.ctl[f"{kind}_enabled"] = self._check(
                config_store.RULE_LABELS[kind], INNER_X, row_y, COL_CHECK_W, base)
            self._set_check(self.ctl[f"{kind}_enabled"],
                            bool(entry.get("enabled", dflt["enabled"])))

            self._label("提前", INNER_X + COL_LEAD_LBL_X, row_y, COL_LEAD_LBL_W)
            self.ctl[f"{kind}_lead"] = self._edit(
                INNER_X + COL_LEAD_X, row_y, COL_LEAD_W, base + 1)
            self._set_text(self.ctl[f"{kind}_lead"],
                           entry.get("lead_minutes", dflt["lead_minutes"]))
            self._label("分钟", INNER_X + COL_UNIT_X, row_y, COL_UNIT_W)

            self._label("标题", INNER_X + COL_TITLE_LBL_X, row_y, COL_TITLE_LBL_W)
            self.ctl[f"{kind}_title"] = self._edit(
                INNER_X + COL_TITLE_X, row_y, COL_TITLE_W, base + 2)
            self._set_text(self.ctl[f"{kind}_title"],
                           entry.get("title", dflt["title"]))

            # 校验失败时的焦点落点：每类控件取第一个
            self.ctl.setdefault("lead", self.ctl[f"{kind}_lead"])
            self.ctl.setdefault("title", self.ctl[f"{kind}_title"])
        y = y_next

        # 组 3：假期列表
        hint_h = hh["holiday_hint"]
        y, y_next = self._group(y, "假期列表", hint_h + 4 + HOLIDAY_EDIT_H)
        self._hint("holiday_hint", INNER_X, y)
        self.ctl["holidays"] = self._edit(
            INNER_X, y + hint_h + 4, INNER_W, ID_HOLIDAYS,
            h=HOLIDAY_EDIT_H, multiline=True)
        self._set_text(self.ctl["holidays"],
                       "\r\n".join(cfg.get("upcoming_holidays") or []))
        y = y_next

        # 组 4：弹窗样式（右侧两条提示都可能换行成两行，行高取实测）
        r1 = max(ROW_H, hh["font_hint"])
        r2 = max(ROW_H, hh["hold_hint"])
        y, y_next = self._group(y, "弹窗样式", r1 + r2)
        self._label("字号", INNER_X, y, 70)
        self.ctl["font"] = self._combo(INNER_X + 76, y, 150, ID_FONT)
        self._cb_fill(self.ctl["font"], FONT_SCALE_LABELS)
        self._cb_select_tier(self.ctl["font"], config_store.FONT_SCALE_TIERS,
                             cfg.get("popup_font_scale"),
                             defaults["popup_font_scale"])
        self._hint("font_hint", INNER_X + 236, y)

        row2 = y + r1
        self._label("最短停留", INNER_X, row2, 70)
        self.ctl["hold"] = self._edit(INNER_X + 76, row2, 60, ID_HOLD)
        self._set_text(self.ctl["hold"], cfg.get("popup_min_hold_seconds", 12))
        self._hint("hold_hint", INNER_X + 146, row2)
        y = y_next

        # 组 5：语音（试听行提示可能换行成两行，行高取实测）
        r3 = max(BTN_H, hh["preview_hint"])
        y, y_next = self._group(y, "语音", ROW_H * 2 + r3)
        self._label("音色", INNER_X, y, 70)
        self.ctl["voice"] = self._combo(INNER_X + 76, y, INNER_W - 76, ID_VOICE)
        self._fill_voices(cfg.get("tts_voice") or {})

        row2 = y + ROW_H
        self._label("语速", INNER_X, row2, 70)
        self.ctl["rate"] = self._combo(INNER_X + 76, row2, 150, ID_RATE)
        self._cb_fill(self.ctl["rate"], TTS_RATE_LABELS)
        self._cb_select_tier(self.ctl["rate"], config_store.TTS_RATE_TIERS,
                             cfg.get("tts_rate"), defaults["tts_rate"])
        self._label("音量", INNER_X + 240, row2, 40)
        self.ctl["volume"] = self._combo(INNER_X + 286, row2, INNER_W - 286, ID_VOLUME)
        self._cb_fill(self.ctl["volume"], TTS_VOLUME_LABELS)
        self._cb_select_tier(self.ctl["volume"], config_store.TTS_VOLUME_TIERS,
                             cfg.get("tts_volume"), defaults["tts_volume"])

        row3 = y + ROW_H * 2
        self.ctl["preview"] = self._create(
            "Button", "试听", BS_PUSHBUTTON, INNER_X, row3, 90, BTN_H, ID_PREVIEW)
        self.ctl["preview_stop"] = self._create(
            "Button", "停止试听", BS_PUSHBUTTON, INNER_X + 96, row3, 90, BTN_H,
            ID_PREVIEW_STOP)
        self._hint("preview_hint", INNER_X + 196, row3)
        y = y_next

        # 组 6：启动（状态以注册表为唯一事实来源，开窗时现读；不进 config.json）
        y, y_next = self._group(y, "启动", ROW_H)
        self.ctl["autostart"] = self._check(
            "开机自动启动（登录 Windows 后自动运行本程序）",
            INNER_X, y, INNER_W, ID_AUTOSTART)
        try:
            self._set_check(self.ctl["autostart"], autostart.is_enabled())
        except Exception:
            _dbg("[settings] 读取开机自启状态失败:\n" + traceback.format_exc())
        y = y_next

        # 内容总高 = 最后一组的 next_y 已含 GROUP_GAP，去掉尾部多余间距再补 PAD
        self._content_h = y - GROUP_GAP + PAD
        return self._content_h

    def _reset_form(self):
        """把表单恢复为 DEFAULT_CONFIG 的值（只改界面，不落盘；仍需点保存）。"""
        d = config_store.DEFAULT_CONFIG
        self._set_text(self.ctl["dismissal"], d["dismissal_time"])
        for i, kind in enumerate(config_store.RULE_KINDS):
            entry = d["rules"][kind]
            self._set_check(self.ctl[f"{kind}_enabled"], entry["enabled"])
            self._set_text(self.ctl[f"{kind}_lead"], entry["lead_minutes"])
            self._set_text(self.ctl[f"{kind}_title"], entry["title"])
        self._set_text(self.ctl["holidays"], "\r\n".join(d["upcoming_holidays"]))
        self._cb_select_tier(self.ctl["font"], config_store.FONT_SCALE_TIERS,
                             d["popup_font_scale"], d["popup_font_scale"])
        self._set_text(self.ctl["hold"], d["popup_min_hold_seconds"])
        self._cb_select_tier(self.ctl["rate"], config_store.TTS_RATE_TIERS,
                             d["tts_rate"], d["tts_rate"])
        self._cb_select_tier(self.ctl["volume"], config_store.TTS_VOLUME_TIERS,
                             d["tts_volume"], d["tts_volume"])
        self._cb_select(self.ctl["voice"], 0)
        _dbg("[settings] 已恢复默认值到表单（未落盘）")

    def _collect(self) -> dict:
        """读取表单 -> 待校验的 raw dict。

        以当前 CONFIG 深拷贝为底再覆盖表单值，**保留未知顶层键**（如将来手工加的
        server_url），避免保存时把配置里本模块不认识的字段吃掉。
        """
        raw = copy.deepcopy(config_store.CONFIG)
        rules = {}
        for kind in config_store.RULE_KINDS:
            rules[kind] = {
                "enabled": self._get_check(self.ctl[f"{kind}_enabled"]),
                "lead_minutes": self._get_text(self.ctl[f"{kind}_lead"]),
                "title": self._get_text(self.ctl[f"{kind}_title"]),
            }
        raw.update({
            "dismissal_time": self._get_text(self.ctl["dismissal"]),
            "rules": rules,
            "upcoming_holidays": self._get_text(self.ctl["holidays"]),
            "popup_font_scale": self._tier_value(
                "font", config_store.FONT_SCALE_TIERS),
            "popup_min_hold_seconds": self._get_text(self.ctl["hold"]),
            "tts_rate": self._tier_value("rate", config_store.TTS_RATE_TIERS),
            "tts_volume": self._tier_value("volume", config_store.TTS_VOLUME_TIERS),
            "tts_voice": self._current_voice(),
        })
        return raw

    # ---- 窗口创建与布局 ----

    def _register_class(self):
        """注册窗口类。类在进程内只注册一次，故回调必须用模块级 _GLOBAL_WNDPROC。

        若把 self._wndproc 直接塞进类里：第二次打开设置窗口时 RegisterClassExW 会因
        类已存在而失败（沿用旧类），而旧类的回调绑定的是**第一个实例**——那个实例
        早已 GC，消息一到即崩溃。模块级回调 + _CURRENT 转发彻底规避此问题。
        """
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = _GLOBAL_WNDPROC
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self.hinstance
        wc.hIcon = _load_icon()
        wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
        # 系统色刷约定：句柄值 = 颜色索引 + 1，由系统持有，绝不可 DeleteObject
        wc.hbrBackground = COLOR_BTNFACE + 1
        wc.lpszMenuName = None
        wc.lpszClassName = SETTINGS_CLASS
        wc.hIconSm = wc.hIcon
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = kernel32.GetLastError()
            # 同一进程内第二次打开设置窗口时类已存在，属正常情况
            if err != ERROR_CLASS_ALREADY_EXISTS:
                raise OSError(f"RegisterClassEx 失败: {err}")
            _dbg(f"[settings] 窗口类已存在，复用（err={err}）")

    def _create_windows(self):
        planned_h = self._planned_content_h()
        sb_w = user32.GetSystemMetrics(SM_CXVSCROLL)
        client_w = self._s(WIN_CLIENT_W) + sb_w

        # 窗口高度：内容 + 底部按钮栏，上限取**工作区**（排除任务栏）再留一点余量。
        # 旧版用「全屏高 *0.88 - 80」，125% DPI 下会白白砍掉几十像素，导致
        # 「工作区明明装得下却仍出滚动条」；装不下时才由滚动条兜底
        # （1366x768 一类小屏就会触发）。
        work = self._workarea()
        screen_h = work.bottom - work.top
        desired_client_h = self._s(planned_h + BAR_H)
        max_client_h = max(self._s(200), screen_h - self._s(16))
        client_h = min(desired_client_h, max_client_h)

        # THICKFRAME 允许拖四边改尺寸（小屏上老师可自行拉高到滚动条消失），
        # MAXIMIZEBOX 允许一键最大化；两条路径都会发 WM_SIZE → _layout 重排。
        style = (WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX
                 | WS_MAXIMIZEBOX | WS_THICKFRAME)
        # 先建一个小窗口，再用「外框 − 客户区」差值精确换算目标尺寸，
        # 免去 AdjustWindowRect 对各种边框组合的猜测
        self.hwnd_main = user32.CreateWindowExW(
            0, SETTINGS_CLASS, "课堂安全播报助手 — 设置", style,
            0, 0, 200, 200, None, None, self.hinstance, None)
        if not self.hwnd_main:
            raise OSError(f"创建设置主窗口失败: {kernel32.GetLastError()}")

        wr, cr = RECT(), RECT()
        user32.GetWindowRect(self.hwnd_main, ctypes.byref(wr))
        user32.GetClientRect(self.hwnd_main, ctypes.byref(cr))
        dx = (wr.right - wr.left) - (cr.right - cr.left)
        dy = (wr.bottom - wr.top) - (cr.bottom - cr.top)

        screen_w = user32.GetSystemMetrics(SM_CXSCREEN) or 1920
        win_w, win_h = client_w + dx, client_h + dy
        x = max(0, (screen_w - win_w) // 2)
        # 在工作区内略偏上，符合对话框惯例；不会压到任务栏
        y = max(0, work.top + int((screen_h - win_h) * 0.4))
        user32.MoveWindow(self.hwnd_main, x, y, win_w, win_h, True)

        # 可视区：持有滚动条；WS_CLIPCHILDREN 避免父窗口重绘时盖住子控件
        self.hwnd_view = user32.CreateWindowExW(
            WS_EX_CONTROLPARENT, SETTINGS_CLASS, "",
            WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_CLIPCHILDREN,
            0, 0, self._s(WIN_CLIENT_W), self._s(200),
            self.hwnd_main, None, self.hinstance, None)
        if not self.hwnd_view:
            raise OSError(f"创建滚动可视区失败: {kernel32.GetLastError()}")

        # 内容层：高度 = 内容总高，滚动时整体平移一次
        self.hwnd_content = user32.CreateWindowExW(
            WS_EX_CONTROLPARENT, SETTINGS_CLASS, "",
            WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN,
            0, 0, self._s(WIN_CLIENT_W), self._s(planned_h),
            self.hwnd_view, None, self.hinstance, None)
        if not self.hwnd_content:
            raise OSError(f"创建内容容器失败: {kernel32.GetLastError()}")

        # 表单控件（全部挂在 content 上）
        actual_h = self._build_form()
        if actual_h != planned_h:
            _dbg(f"[settings] 内容高度实测 {actual_h} 与预估 {planned_h} 不符，已按实测布局")
        user32.MoveWindow(self.hwnd_content, 0, 0,
                          self._s(WIN_CLIENT_W), self._s(actual_h), True)

        # 底部按钮挂在主窗口上、位于可视区之外，因此固定不滚动
        bar_y = client_h - self._s(BAR_H)
        bw, bh = self._s(BTN_W), self._s(BTN_H)
        gap = self._s(8)
        by = bar_y + (self._s(BAR_H) - bh) // 2
        right = client_w - sb_w - gap
        self.ctl["save"] = self._create(
            "Button", "保存并生效", BS_DEFPUSHBUTTON, 0, 0, BTN_W, BTN_H, IDOK,
            parent=self.hwnd_main)
        self.ctl["cancel"] = self._create(
            "Button", "取消", BS_PUSHBUTTON, 0, 0, BTN_W, BTN_H, IDCANCEL,
            parent=self.hwnd_main)
        self.ctl["reset"] = self._create(
            "Button", "恢复默认", BS_PUSHBUTTON, 0, 0, BTN_W, BTN_H, ID_RESET,
            parent=self.hwnd_main)
        user32.MoveWindow(self.ctl["save"], right - bw, by, bw, bh, True)
        user32.MoveWindow(self.ctl["cancel"], right - gap - bw * 2, by, bw, bh, True)
        user32.MoveWindow(self.ctl["reset"], self._s(PAD), by, bw, bh, True)

        self._set_preview_buttons(running=False)
        self._layout()

    def _workarea(self) -> RECT:
        """屏幕工作区（排除任务栏）；取失败时回退整屏，保证开窗尺寸仍有上限。"""
        rc = RECT()
        ok = user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)
        if not ok or rc.right - rc.left <= 0 or rc.bottom - rc.top <= 0:
            rc.left = rc.top = 0
            rc.right = user32.GetSystemMetrics(SM_CXSCREEN) or 1920
            rc.bottom = user32.GetSystemMetrics(SM_CYSCREEN) or 1080
        return rc

    def _layout(self):
        """按主窗口客户区尺寸摆放可视区与底部按钮栏，并刷新滚动条范围。"""
        if not self.hwnd_main or not self.hwnd_view:
            return
        rc = RECT()
        user32.GetClientRect(self.hwnd_main, ctypes.byref(rc))
        cw, ch = rc.right - rc.left, rc.bottom - rc.top
        if cw <= 0 or ch <= 0:
            return

        sb_w = user32.GetSystemMetrics(SM_CXVSCROLL)
        bar_h = self._s(BAR_H)
        view_h = max(self._s(ROW_H), ch - bar_h)
        # view 窗口宽恒等于客户区宽，滚动条占 view **内侧**；内容宽 = view 客户宽
        # （显示滚动条时扣一个 sb_w）。旧写法把 view 窗口宽先扣 sb_w，其客户区再被
        # 自身滚动条扣一次，组框右边框会被裁掉几像素。
        # 显示与否只取决于内容高与 view 高，与宽无关，无循环依赖。
        shown = self._s(self._content_h) > view_h
        content_w = max(self._s(80), cw - (sb_w if shown else 0))
        user32.MoveWindow(self.hwnd_view, 0, 0, cw, view_h, True)

        bw, bh = self._s(BTN_W), self._s(BTN_H)
        gap = self._s(8)
        by = ch - bar_h + (bar_h - bh) // 2
        # 底部按钮右缘与内容右缘对齐：显示滚动条时让出滚动条宽度
        user32.MoveWindow(self.ctl["save"], content_w - gap - bw, by, bw, bh, True)
        user32.MoveWindow(self.ctl["cancel"], content_w - gap * 2 - bw * 2,
                          by, bw, bh, True)
        user32.MoveWindow(self.ctl["reset"], self._s(PAD), by, bw, bh, True)

        self._update_scrollbar(content_w, view_h)

    def _update_scrollbar(self, content_w: int, view_h: int):
        """按「内容总高 − 可视高」设置滚动范围，并整体平移内容层一次。

        content_w 为 view 的客户区宽（已扣除滚动条自身宽度），内容层按它取宽。
        """
        content_h = max(1, self._s(self._content_h))
        max_pos = max(0, content_h - view_h)
        self._scroll_pos = max(0, min(self._scroll_pos, max_pos))

        # 滚动条按现实显隐：内容恰好放得下（max_pos==0）时 nPage>=nMax 会让拇指
        # 被系统拉成满轨、拖不动，形同坏件，索性隐藏；溢出时才显示，此时
        # nPage<nMax 拇指必然可拖。隐藏后 view 客户区变宽只是右侧多一条背景，
        # 控件左锚定、宽 <= INNER_W，不受影响。
        user32.ShowScrollBar(self.hwnd_view, SB_VERT, max_pos > 0)

        si = SCROLLINFO()
        si.cbSize = ctypes.sizeof(SCROLLINFO)
        si.fMask = SIF_ALL
        si.nMin = 0
        si.nMax = content_h - 1
        si.nPage = view_h
        si.nPos = self._scroll_pos
        user32.SetScrollInfo(self.hwnd_view, SB_VERT, ctypes.byref(si), True)
        # 滚动 = 平移内容层一次，而非逐个 MoveWindow N 个子控件
        user32.MoveWindow(self.hwnd_content, 0, -self._scroll_pos,
                          content_w, content_h, True)

    def _scroll_by(self, delta: int):
        if not delta or not self.hwnd_view:
            return
        rc = RECT()
        user32.GetClientRect(self.hwnd_view, ctypes.byref(rc))
        view_w, view_h = rc.right - rc.left, rc.bottom - rc.top
        if view_w <= 0 or view_h <= 0:
            return
        old = self._scroll_pos
        self._scroll_pos += delta
        self._update_scrollbar(view_w, view_h)
        return self._scroll_pos != old

    def _ensure_visible(self, hwnd):
        """把控件滚进可视区（校验失败聚焦时用）。两者都取屏幕坐标，差值即可用。"""
        rc, vr = RECT(), RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return
        if not user32.GetWindowRect(self.hwnd_view, ctypes.byref(vr)):
            return
        if rc.top < vr.top:
            self._scroll_by(rc.top - vr.top)
        elif rc.bottom > vr.bottom:
            self._scroll_by(rc.bottom - vr.bottom)

    # ---- 消息处理 ----

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_COMMAND:
                self._on_command(wparam & 0xFFFF)
                return 0
            if msg == WM_APP_PREVIEW_DONE and hwnd == self.hwnd_main:
                # 试听线程（念完或被停止）收尾后回 UI 线程恢复按钮状态。
                # 历史 bug：曾漏掉此分支，消息落入 DefWindowProcW 被丢弃，
                # 按钮永久停在 running 态（试听禁用），无法再次试听。
                self._set_preview_buttons(running=False)
                return 0
            if msg == WM_VSCROLL and hwnd == self.hwnd_view:
                self._on_vscroll(wparam & 0xFFFF, wparam)
                return 0
            if msg == WM_MOUSEWHEEL:
                # 滚轮消息发给焦点窗口。落在多行假期框里时交还默认处理（滚它自己的
                # 内容），否则滚动整个表单 —— 老师用滚轮浏览长表单是基本预期。
                if hwnd == self.ctl.get("holidays"):
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
                delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
                notches = delta // 120 if delta >= 0 else -((-delta) // 120)
                self._scroll_by(-notches * 3 * self._s(SCROLL_STEP))
                return 0
            if msg == WM_SIZE and hwnd == self.hwnd_main:
                self._layout()
                return 0
            if msg == WM_CTLCOLORSTATIC:
                # STATIC / 复选框 / GROUPBOX 的文字背景须与对话框灰底一致，
                # 否则在 COLOR_BTNFACE 底上出现一块块白底。返回系统共享刷，不可删除。
                gdi32.SetBkMode(wparam, TRANSPARENT)
                return user32.GetSysColorBrush(COLOR_BTNFACE)
            if msg == WM_CLOSE:
                self._close()
                return 0
            if msg == WM_DESTROY and hwnd == self.hwnd_main:
                # 只结束本线程的消息循环，不影响托盘主线程与弹窗工作线程
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            # wndproc 里抛异常会直接搞崩消息循环，任何分支都必须兜住
            _dbg("[settings] wndproc 异常:\n" + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_vscroll(self, code: int, wparam: int):
        si = SCROLLINFO()
        si.cbSize = ctypes.sizeof(SCROLLINFO)
        si.fMask = SIF_ALL
        if not user32.GetScrollInfo(self.hwnd_view, SB_VERT, ctypes.byref(si)):
            return
        page = max(1, int(si.nPage))
        step = self._s(SCROLL_STEP)
        pos = self._scroll_pos
        if code == SB_LINEUP:
            pos -= step
        elif code == SB_LINEDOWN:
            pos += step
        elif code == SB_PAGEUP:
            pos -= page
        elif code == SB_PAGEDOWN:
            pos += page
        elif code == SB_THUMBTRACK:
            pos = int(si.nTrackPos)
        else:
            return                      # SB_ENDSCROLL 等无需处理
        self._scroll_pos = pos
        rc = RECT()
        user32.GetClientRect(self.hwnd_view, ctypes.byref(rc))
        self._update_scrollbar(rc.right - rc.left, rc.bottom - rc.top)

    def _on_command(self, cid: int):
        if cid == IDOK:
            self._save()
        elif cid == IDCANCEL:
            self._close()
        elif cid == ID_RESET:
            self._reset_form()
        elif cid == ID_PREVIEW:
            self._preview()
        elif cid == ID_PREVIEW_STOP:
            self._stop_preview()

    def _close(self):
        if self._closing or not self.hwnd_main:
            return
        self._closing = True
        self._preview_stop.set()          # 关窗即停试听，避免线程残留
        user32.DestroyWindow(self.hwnd_main)

    # ---- 保存 ----

    def _save(self):
        raw = self._collect()
        clean, errors = config_store.validate(raw)
        if errors:
            _dbg("[settings] 校验失败: " + " | ".join(errors))
            self._show_errors(errors)
            return                        # 不落盘、不关窗，保留用户已填内容供修正
        _dbg("[settings] 校验通过，派发后台线程落盘")
        # 开机自启不走 config_store（注册表是唯一事实来源），单独随保存动作落注册表
        autostart_on = self._get_check(self.ctl["autostart"])
        # 落盘是磁盘 I/O，绝不在 wndproc 里同步做（否则窗口假死、鼠标转圈）
        threading.Thread(target=self._apply_async, args=(clean, autostart_on),
                         daemon=True).start()

    def _apply_async(self, clean: dict, autostart_on: bool):
        try:
            self._on_apply(clean)
        except Exception:
            _dbg("[settings] 保存失败:\n" + traceback.format_exc())
            tail = traceback.format_exc().strip().splitlines()[-1]
            self._msg(f"保存失败，配置未生效，原设置保持不变。\n\n{tail}", error=True)
            return
        try:
            autostart.set_enabled(autostart_on)
            _dbg(f"[settings] 开机自启已{'启用' if autostart_on else '禁用'}")
        except Exception:
            # 配置已生效，仅自启项写失败：如实告知（话术不与上面的整体失败混淆）
            _dbg("[settings] 开机自启写注册表失败:\n" + traceback.format_exc())
            tail = traceback.format_exc().strip().splitlines()[-1]
            self._msg(f"配置已保存并生效，但开机自启设置失败：\n\n{tail}",
                      error=True)
        _dbg("[settings] 保存成功并已热生效，关闭窗口")
        # 回 UI 线程关窗（不能在后台线程直接 DestroyWindow）
        if self.hwnd_main:
            user32.PostMessageW(self.hwnd_main, WM_CLOSE, 0, 0)

    def _show_errors(self, errors: list):
        body = "\n".join(f"· {e}" for e in errors)
        self._msg(f"以下设置项需要修正，未保存到磁盘：\n\n{body}", error=True)
        self._focus_error(errors[0])

    def _focus_error(self, first_error: str):
        """按错误文案里的关键词把焦点送到出错控件，并先滚进可视区。"""
        for keyword, key in _FOCUS_HINTS:
            if keyword in first_error:
                hwnd = self.ctl.get(key)
                if hwnd:
                    self._ensure_visible(hwnd)
                    user32.SetFocus(hwnd)
                    return

    def _msg(self, text: str, error: bool = False):
        flags = MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION)
        user32.MessageBoxW(self.hwnd_main, text, "课堂安全播报助手 — 设置", flags)

    # ---- 试听 ----

    def _set_preview_buttons(self, running: bool):
        user32.EnableWindow(self.ctl["preview"], not running)
        user32.EnableWindow(self.ctl["preview_stop"], running)

    def _sample_text(self) -> str:
        """用今日「每日提醒」的真实内容 + 表单里当前的标题，让老师听到实际效果。"""
        title = (self._get_text(self.ctl["daily_title"]).strip()
                 or config_store.DEFAULT_CONFIG["rules"]["daily"]["title"])
        try:
            import content as content_lib
            body = content_lib.get_daily_content(datetime.datetime.now())
        except Exception:
            _dbg("[settings] 取试听文本失败:\n" + traceback.format_exc())
            body = "请同学们有序排队离校，注意交通安全。"
        return f"{title}。{body}"

    def _preview(self):
        if self._preview_thread is not None and self._preview_thread.is_alive():
            return                        # 已在试听，忽略连点
        if self._is_busy is not None:
            try:
                busy = bool(self._is_busy())
            except Exception:
                busy = False
            if busy:
                self._msg("正式播报正在进行，试听已跳过。\n请稍后再试。", error=True)
                return

        rate = self._tier_value("rate", config_store.TTS_RATE_TIERS)
        volume = self._tier_value("volume", config_store.TTS_VOLUME_TIERS)
        voice_id = self._current_voice()["id"]
        text = self._sample_text()
        self._preview_stop.clear()
        self._set_preview_buttons(running=True)
        _dbg(f"[settings] 开始试听 rate={rate} volume={volume} "
             f"voice={voice_id or '系统默认'}")

        def _run():
            try:
                self._preview_speak(text, rate, volume, voice_id, self._preview_stop)
            except Exception:
                _dbg("[settings] 试听失败:\n" + traceback.format_exc())
            finally:
                # 回 UI 线程刷新按钮状态（后台线程不直接碰控件）
                if self.hwnd_main:
                    user32.PostMessageW(self.hwnd_main, WM_APP_PREVIEW_DONE, 0, 0)

        self._preview_thread = threading.Thread(target=_run, daemon=True)
        self._preview_thread.start()

    def _stop_preview(self):
        self._preview_stop.set()
        _dbg("[settings] 请求停止试听")

    # ---- 运行 ----

    def run(self):
        self._register_class()
        self._create_windows()
        user32.ShowWindow(self.hwnd_main, SW_SHOW)
        user32.UpdateWindow(self.hwnd_main)
        user32.SetForegroundWindow(self.hwnd_main)
        user32.SetFocus(self.ctl["dismissal"])

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            # IsDialogMessageW 让这个非对话框窗口也获得 Tab 焦点遍历与 Enter/Esc 语义
            if user32.IsDialogMessageW(self.hwnd_main, ctypes.byref(msg)):
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._cleanup()

    def _cleanup(self):
        self._preview_stop.set()
        for font in self._fonts:
            try:
                gdi32.DeleteObject(font)
            except Exception:
                pass
        self._fonts = []


# ---------- 模块级：类回调转发与单例 ----------

_CURRENT = None                 # 当前存活的 SettingsWindow（单例，见 show_settings）
_ICON = None
_settings_lock = threading.Lock()


def _load_icon():
    """加载 app.ico 并缓存。进程级复用，避免每次开窗都泄漏一个 HICON。"""
    global _ICON
    if _ICON is None and os.path.exists(ICO_PATH):
        try:
            _ICON = user32.LoadImageW(None, ICO_PATH, IMAGE_ICON, 0, 0,
                                      LR_LOADFROMFILE | LR_DEFAULTSIZE)
        except Exception:
            _ICON = None
    return _ICON


def _dispatch(hwnd, msg, wparam, lparam):
    """窗口类的常驻回调，转发给当前实例。

    必须是模块级对象：窗口类在进程内只注册一次，若绑定某个实例的方法，第二次开窗
    时旧实例已被 GC，消息一到即崩溃。
    """
    inst = _CURRENT
    if inst is None:
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    return inst._wnd_proc(hwnd, msg, wparam, lparam)


# 模块级 WNDPROC，永不 GC
_GLOBAL_WNDPROC = WNDPROC(_dispatch)


def show_settings(on_apply, preview_speak, list_voices, is_busy=None) -> None:
    """打开设置窗口并**阻塞直到关闭**。必须由后台线程调用（本函数内跑消息循环）。

    参数（依赖注入，本模块因此不必 import broadcast，也就没有循环导入）：
      on_apply(clean: dict) -> None
          校验通过后调用，由 broadcast 落盘 + 热应用 + 置调度器脏标记。
          在后台线程执行；抛异常则弹错误提示且窗口保持打开。
      preview_speak(text, rate, volume, voice_id, stop_event) -> None
          试听。用表单当前值（尚未保存），故不能走 CONFIG。在后台线程执行。
      list_voices() -> list[tuple[str, str]]
          [(id, name), ...]。首项「系统默认」由本函数插入，回调不必包含。
      is_busy() -> bool
          可选。正式播报进行中时返回 True，试听按钮据此拒绝并提示。

    单例：已有窗口时激活它并立即返回，不新建第二个。
    """
    global _CURRENT

    if not _settings_lock.acquire(blocking=False):
        inst = _CURRENT
        _dbg("[settings] 已有设置窗口，激活之")
        if inst is not None and inst.hwnd_main:
            user32.ShowWindow(inst.hwnd_main, SW_RESTORE)
            user32.SetForegroundWindow(inst.hwnd_main)
        return

    inst = SettingsWindow(on_apply, preview_speak, list_voices, is_busy)
    _CURRENT = inst
    try:
        inst.run()
    except Exception:
        _dbg("[settings] 设置窗口异常退出:\n" + traceback.format_exc())
    finally:
        _CURRENT = None
        _settings_lock.release()
