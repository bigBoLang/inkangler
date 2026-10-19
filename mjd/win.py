"""Windows 窗口定位 / 截图 / 鼠标输入。

面向微信小程序（WeChatAppEx.exe, Chromium 内核）这类窗口：
- 用标题定位窗口
- 用 PrintWindow 抓窗口自身画面（被别的窗口挡住也不影响）
- **推荐用 PostInput（PostMessage）发鼠标消息**：不移动真实光标、不抢焦点，
  用户可以照常用电脑。文件后半段的 SendInput 版本（click/drag/focus）
  会真的动光标，只留给早期探索脚本用，机器人不要碰。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass

from PIL import Image

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

# 进程设为「每显示器 DPI 感知」，否则高 DPI 下坐标会与实际像素对不上
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

SRCCOPY = 0x00CC0020
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# 标定时的画面尺寸：所有 ROI / 圆弧几何 / 按钮坐标都按这个尺寸写死。
# 实际窗口大小不同时，抓图会缩放成这个尺寸（点击时再换算回去），
# 所以小程序窗口被拖动/缩放后依然能用。
CAPTURE_SIZE = (552, 991)

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

INPUT_MOUSE = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    rect: tuple[int, int, int, int]  # 含不可见边框，left, top, right, bottom
    frame: tuple[int, int, int, int]  # 可见区域（DWM 扩展边框），屏幕绝对坐标

    @property
    def frame_size(self) -> tuple[int, int]:
        return (self.frame[2] - self.frame[0], self.frame[3] - self.frame[1])


# ---------------------------------------------------------------- 窗口定位


def list_windows(needle: str | None = None) -> list[WindowInfo]:
    """枚举可见顶层窗口；needle 会同时匹配标题 / 类名 / 进程名。"""
    out: list[WindowInfo] = []
    needle_l = needle.lower() if needle else None

    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value

        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left <= 1 or rect.bottom - rect.top <= 1:
            return True

        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)

        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _exe_name(pid.value)

        if needle_l and needle_l not in title.lower() and needle_l not in cls.value.lower() \
                and needle_l not in exe.lower():
            return True

        out.append(WindowInfo(
            hwnd=int(hwnd),
            title=title,
            class_name=cls.value,
            pid=pid.value,
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            frame=extended_frame(hwnd),
        ))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def _exe_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def extended_frame(hwnd: int) -> tuple[int, int, int, int]:
    """DWM 可见边框；高 DPI / Win10+ 下 GetWindowRect 会多出一圈透明边框。"""
    r = wt.RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd), wt.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(r), ctypes.sizeof(r))
    if hr != 0:
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)
    return (r.left, r.top, r.right, r.bottom)


def find_window(title: str, exact: bool = False) -> WindowInfo:
    """按标题找窗口。

    非精确模式下先找**标题完全相同**的那个，再退回「包含」匹配。
    这一步很关键：配套的图形界面窗口标题是「墨迹大侠 · 自动钓鱼」，
    只做包含匹配会把自己的界面当成游戏，于是截图/点击全打到自己窗口上。
    """
    wins = list_windows()
    if not exact:
        for w in wins:
            if w.title == title:
                return w
    for w in wins:
        if (w.title == title) if exact else (title in w.title):
            return w
    raise LookupError(f"没找到窗口: {title!r}；当前可见窗口: "
                      f"{[w.title for w in wins]}")


# ---------------------------------------------------------------- 截图


def grab_rect(left: int, top: int, right: int, bottom: int) -> Image.Image:
    """用 BitBlt 抓屏幕任意矩形，返回 RGB 图。"""
    w, h = right - left, bottom - top
    hwnd_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, left, top, SRCCOPY)
        return _dib_to_image(mem_dc, bmp, w, h)
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, hwnd_dc)


def grab_window(win: WindowInfo, mode: str = "pw") -> Image.Image:
    """抓窗口画面，并统一缩放成 CAPTURE_SIZE。

    mode="pw"  : PrintWindow 渲染窗口自身内容，不受遮挡影响（默认，推荐）
    mode="screen": 屏幕区域位图，所见即所得，但会被其他窗口遮挡

    PrintWindow 不是每个窗口/每个版本都支持（实测微信小程序某些版本会返回
    全黑）。失败时退回屏幕截图，**绝不能再调回 grab_window(pw)**——那会
    无限递归直接 RecursionError（以前的坑）。
    """
    if mode == "screen":
        img = grab_rect(*win.frame)
    else:
        img = grab_window_pw(win)
        if img is None:
            _note_capture("screen")
            img = grab_rect(*win.frame)
        else:
            _note_capture("pw")
    return _to_capture_size(img)


def _to_capture_size(img: Image.Image) -> Image.Image:
    """把画面缩放到标定尺寸。

    所有 ROI / 圆弧几何 / 按钮坐标都是按 552x991 标定的。换了别的窗口大小时
    （小程序会按窗口等比拉伸画面）只要在入口处缩放一次，后面的代码全都不用改。
    点击时 PostInput 会做反向换算。
    """
    if img.size == CAPTURE_SIZE:
        return img
    return img.resize(CAPTURE_SIZE, Image.BILINEAR)


_capture_mode: str | None = None


def _note_capture(mode: str) -> None:
    global _capture_mode
    _capture_mode = mode


def capture_note() -> str | None:
    """返回一句「截图方式」的提示（只在切换时给一次），给界面日志用。"""
    global _capture_mode_note
    if _capture_mode == "screen" and not _capture_mode_note:
        _capture_mode_note = True
        return ("注意：这个窗口不支持 PrintWindow 抓图，已改用屏幕截图——"
                "运行时要让游戏窗口保持可见、别被别的窗口挡住。")
    return None


_capture_mode_note = False


def grab_window_pw(win: WindowInfo, client_only: bool = False) -> Image.Image:
    """用 PrintWindow 渲染窗口自身内容，不受其他窗口遮挡影响。

    Chromium 系窗口（微信小程序）支持 PW_RENDERFULLCONTENT。
    返回图像与 grab_window 对齐（裁剪掉不可见边框）。
    """
    left, top, right, bottom = win.rect
    w, h = right - left, bottom - top
    hwnd_dc = user32.GetWindowDC(win.hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        flags = 0x00000001 if client_only else 0x00000002  # CLIENTONLY : RENDERFULLCONTENT
        ok = user32.PrintWindow(win.hwnd, mem_dc, flags)
        if not ok:
            return None
        img = _dib_to_image(mem_dc, bmp, w, h)
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(win.hwnd, hwnd_dc)

    # 窗口矩形 -> 可见边框，做同样的裁剪，保证与 grab_window 坐标一致
    dx, dy = win.frame[0] - win.rect[0], win.frame[1] - win.rect[1]
    fw, fh = win.frame_size
    return img.crop((dx, dy, dx + fw, dy + fh))


def _dib_to_image(dc, bmp, w: int, h: int) -> Image.Image:
    buf = ctypes.create_string_buffer(w * h * 4)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    gdi32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


# ---------------------------------------------------------------- 鼠标


def _send_mouse(flags: int, x: int | None = None, y: int | None = None):
    mi = MOUSEINPUT()
    if x is not None and y is not None:
        vx = user32.GetSystemMetrics(0)   # SM_CXSCREEN
        vy = user32.GetSystemMetrics(1)   # SM_CYSCREEN
        # ABSOLUTE 用 0..65535 归一化坐标
        mi.dx = int(x * 65535 / max(vx - 1, 1))
        mi.dy = int(y * 65535 / max(vy - 1, 1))
        flags |= MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE
    mi.dwFlags = flags
    inp = INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=mi))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def click(screen_x: int, screen_y: int, hold: float = 0.05):
    """真实鼠标点击屏幕绝对坐标。"""
    _send_mouse(MOUSEEVENTF_MOVE, screen_x, screen_y)
    time.sleep(0.02)
    _send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(hold)
    _send_mouse(MOUSEEVENTF_LEFTUP)


def press(screen_x: int, screen_y: int):
    _send_mouse(MOUSEEVENTF_MOVE, screen_x, screen_y)
    time.sleep(0.02)
    _send_mouse(MOUSEEVENTF_LEFTDOWN)


def release(screen_x: int, screen_y: int):
    _send_mouse(MOUSEEVENTF_MOVE, screen_x, screen_y)
    time.sleep(0.02)
    _send_mouse(MOUSEEVENTF_LEFTUP)


def move(screen_x: int, screen_y: int):
    _send_mouse(MOUSEEVENTF_MOVE, screen_x, screen_y)


MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


def scroll(screen_x: int, screen_y: int, notches: int, pause: float = 0.06):
    """把鼠标移到 (x,y) 后滚动滚轮。正数向上，负数向下。"""
    move(screen_x, screen_y)
    time.sleep(0.05)
    step = WHEEL_DELTA if notches > 0 else -WHEEL_DELTA
    for _ in range(abs(notches)):
        mi = MOUSEINPUT()
        mi.mouseData = ctypes.c_ulong(step).value if step > 0 else ctypes.c_ulong(step & 0xFFFFFFFF).value
        mi.dwFlags = MOUSEEVENTF_WHEEL
        inp = INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=mi))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(pause)


def scroll_image(win: WindowInfo, x: int, y: int, notches: int, ensure_focus: bool = True):
    if ensure_focus:
        focus(win)
    sx, sy = image_to_screen(win, x, y)
    scroll(sx, sy, notches)


def drag(screen_x1: int, screen_y1: int, screen_x2: int, screen_y2: int,
         steps: int = 20, pause: float = 0.012, hold: float = 0.12):
    """按住并拖动（模拟触摸滑动），用于滚动列表。"""
    _send_mouse(MOUSEEVENTF_MOVE, screen_x1, screen_y1)
    time.sleep(0.05)
    _send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(hold)
    for i in range(1, steps + 1):
        t = i / steps
        px = int(screen_x1 + (screen_x2 - screen_x1) * t)
        py = int(screen_y1 + (screen_y2 - screen_y1) * t)
        _send_mouse(MOUSEEVENTF_MOVE, px, py)
        time.sleep(pause)
    time.sleep(hold)
    _send_mouse(MOUSEEVENTF_LEFTUP)


def drag_image(win: WindowInfo, x1: int, y1: int, x2: int, y2: int,
               ensure_focus: bool = True, **kw):
    if ensure_focus:
        focus(win)
    drag(*image_to_screen(win, x1, y1), *image_to_screen(win, x2, y2), **kw)


# ---------------------------------------------------------------- 消息投递（不抢鼠标）
#
# SendInput 会真的移动系统光标、抢焦点；PostMessage 只把鼠标消息塞给目标窗口，
# 用户的光标和焦点都不受影响。微信小程序内容渲染在子窗口
# Chrome_RenderWidgetHostHWND 里，消息要投给它。

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
MK_LBUTTON = 0x0001

_RENDER_WIDGET_CLASSES = (
    "Chrome_RenderWidgetHostHWND",
    "Intermediate D3D Window",
)


def find_descendant(hwnd: int, class_names) -> int | None:
    """广度优先找一个指定类名的后代窗口。"""
    found = []

    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        if buf.value in class_names:
            found.append(h)
        return True

    user32.EnumChildWindows(hwnd, EnumProc(cb), 0)
    return found[0] if found else None


def input_target(win: WindowInfo) -> int:
    """返回接收鼠标消息的窗口句柄（优先 Chromium 渲染窗口）。"""
    return find_descendant(win.hwnd, _RENDER_WIDGET_CLASSES) or win.hwnd


def _make_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _client_pt(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    pt = wt.POINT(int(screen_x), int(screen_y))
    user32.ScreenToClient(wt.HWND(hwnd), ctypes.byref(pt))
    return pt.x, pt.y


def post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
    return bool(user32.PostMessageW(wt.HWND(hwnd), wt.UINT(msg),
                                     wt.WPARAM(wparam), wt.LPARAM(lparam)))


def input_blocked(win: WindowInfo) -> str | None:
    """检查能不能给这个窗口发模拟点击；不能就返回一句人话。

    微信如果是以「管理员身份」运行的，它的窗口完整性级别比本程序高，
    Windows 的 UIPI 会直接把 PostMessage 的鼠标消息挡掉（返回 0），
    PrintWindow 也会失败。症状：
      * 截图只能是屏幕截图（PrintWindow 返回全黑）
      * 脚本点哪儿都没反应（界面上一切正常，真人点却有反应）
    常见的鼠标消息被挡，WM_NULL 这类系统消息却是通的，所以这里拿
    WM_MOUSEMOVE 当探针。
    """
    hwnd = input_target(win)
    if post_message(hwnd, WM_MOUSEMOVE, 0, _make_lparam(0, 0)):
        return None
    return ("游戏窗口的权限比本程序高（微信大概是以「管理员身份」运行的）。\n"
            "Windows 不允许低权限程序给它发模拟点击，所以脚本点哪儿都没反应。\n"
            "二选一：\n"
            "  ① 点界面上的「管理员重启」，用管理员身份运行本程序（会弹 UAC）；\n"
            "  ② 关掉微信，用普通方式重新打开小程序，再运行本程序。")


class PostInput:
    """通过 PostMessage 操作窗口，不移动真实光标、不抢焦点。"""

    def __init__(self, win: WindowInfo):
        self.win = win
        self.hwnd = input_target(win)

    # ---- 基本动作
    def move_to(self, x: int, y: int, buttons: int = 0):
        sx, sy = image_to_screen(self.win, x, y)
        cx, cy = _client_pt(self.hwnd, sx, sy)
        post_message(self.hwnd, WM_MOUSEMOVE, buttons, _make_lparam(cx, cy))
        return cx, cy

    def down(self, x: int, y: int):
        cx, cy = self.move_to(x, y)
        post_message(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, _make_lparam(cx, cy))

    def up(self, x: int, y: int):
        cx, cy = self.move_to(x, y)
        post_message(self.hwnd, WM_LBUTTONUP, 0, _make_lparam(cx, cy))

    def click(self, x: int, y: int, settle: float = 0.04):
        cx, cy = self.move_to(x, y)
        lp = _make_lparam(cx, cy)
        post_message(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(settle)
        post_message(self.hwnd, WM_LBUTTONUP, 0, lp)

    # ---- 组合动作
    def long_press(self, x: int, y: int, seconds: float = 3.0,
                   stop_event=None, on_tick=None, tick: float = 0.25):
        """按住不放 seconds 秒；期间可回调，用于闭环判断后提前松开。

        on_tick() 返回 True 表示立即松开。
        """
        self.down(x, y)
        t0 = time.time()
        try:
            while time.time() - t0 < seconds:
                time.sleep(tick)
                if on_tick and on_tick():
                    break
                if stop_event is not None and stop_event():
                    break
        finally:
            self.up(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, steps: int = 24,
             pause: float = 0.012):
        self.down(x1, y1)
        time.sleep(0.08)
        for i in range(1, steps + 1):
            t = i / steps
            self.move_to(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t),
                         MK_LBUTTON)
            time.sleep(pause)
        time.sleep(0.08)
        self.up(x2, y2)


# ---------------------------------------------------------------- 前台 / 坐标换算

SW_RESTORE = 9


def focus(win: WindowInfo) -> bool:
    """把窗口切到前台并确保不最小化。返回是否成功拿到前台。"""
    hwnd = win.hwnd
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    # 抢前台：临时把自己的输入线程挂到当前前台线程上
    cur_tid = kernel32.GetCurrentThreadId()
    tgt_tid = user32.GetWindowThreadProcessId(fg, None)
    attached = False
    if tgt_tid and tgt_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, tgt_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, tgt_tid, False)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def image_to_screen(win: WindowInfo, x: int, y: int) -> tuple[int, int]:
    """把「标定尺寸截图」里的像素坐标换算成屏幕绝对坐标。

    截图统一缩放到 CAPTURE_SIZE，而实际窗口可能大一点/小一点，
    所以这里要按窗口真实尺寸做一次反向缩放。
    """
    fw, fh = win.frame_size
    return (int(win.frame[0] + x * fw / CAPTURE_SIZE[0]),
            int(win.frame[1] + y * fh / CAPTURE_SIZE[1]))


def click_image(win: WindowInfo, x: int, y: int, hold: float = 0.06,
                ensure_focus: bool = True):
    """点击窗口截图内的 (x, y)。"""
    if ensure_focus:
        focus(win)
    sx, sy = image_to_screen(win, x, y)
    click(sx, sy, hold=hold)
    return (sx, sy)
