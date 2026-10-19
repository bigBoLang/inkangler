"""列出所有可见顶层窗口（标题 / 进程 / 位置 / 大小）。"""
import ctypes
import ctypes.wintypes as wt
import json
import sys

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetProcessDPIAware()

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def get_exe_name(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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


def get_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def get_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


results = []


def cb(hwnd, _):
    if not user32.IsWindowVisible(hwnd):
        return True
    title = get_text(hwnd)
    if not title:
        return True
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return True
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 1 or h <= 1:
        return True
    pid = get_pid(hwnd)
    results.append({
        "hwnd": int(hwnd),
        "hwnd_hex": hex(hwnd),
        "title": title,
        "class": get_class(hwnd),
        "exe": get_exe_name(pid),
        "pid": pid,
        "rect": [rect.left, rect.top, rect.right, rect.bottom],
        "size": [w, h],
    })
    return True


user32.EnumWindows(EnumWindowsProc(cb), 0)

if len(sys.argv) > 1:
    needle = sys.argv[1].lower()
    results = [w for w in results
               if needle in w["title"].lower() or needle in w["exe"].lower()
               or needle in w["class"].lower()]

out = json.dumps(results, ensure_ascii=False, indent=1)
sys.stdout.buffer.write(out.encode("utf-8"))
sys.stdout.buffer.write(b"\n")
