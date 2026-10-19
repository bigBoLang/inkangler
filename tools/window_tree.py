"""打印窗口的父子层级，用于确定 PostMessage 的目标窗口。

用法:
    python tools/window_tree.py 墨迹大侠
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import win  # noqa: E402

user32 = ctypes.windll.user32


def text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def cls(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def walk(hwnd, depth=0, limit=6):
    if depth > limit:
        return
    r = rect(hwnd)
    vis = "V" if user32.IsWindowVisible(hwnd) else "-"
    print("  " * depth + f"{vis} {hex(hwnd)} [{cls(hwnd)}] "
          f"rect={r} size={r[2] - r[0]}x{r[3] - r[1]} text={text(hwnd)!r}")
    children = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        children.append(h)
        return True

    user32.EnumChildWindows(hwnd, EnumProc(cb), 0)
    # EnumChildWindows 会递归列出所有后代，只取直接子窗口
    for c in children:
        if user32.GetParent(c) == hwnd:
            walk(c, depth + 1, limit)


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "墨迹大侠"
    w = win.find_window(title)
    print(f"顶层窗口 {w.title!r} {hex(w.hwnd)} frame={w.frame}")
    walk(w.hwnd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
