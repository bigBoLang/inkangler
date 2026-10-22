"""截图 / 点击后端：本地直连，或走提权助手。

微信（WeChatAppEx.exe）以管理员身份运行时，普通权限脚本的 PostMessage 会被
UIPI 过滤。这里先试本地；被挡就找 `tools/remote.py` 的提权助手（127.0.0.1）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from . import win

ROOT = Path(__file__).resolve().parents[1]
TITLE = "墨迹大侠"


class Driver(Protocol):
    name: str

    def grab(self) -> np.ndarray: ...
    def click(self, x: int, y: int) -> None: ...


class LocalDriver:
    name = "本地"

    def __init__(self) -> None:
        self.w = win.find_window(TITLE)
        self.pi = win.PostInput(self.w)

    def grab(self) -> np.ndarray:
        return np.asarray(win.grab_window(self.w).convert("RGB"))

    def click(self, x: int, y: int) -> None:
        self.pi.click(int(x), int(y), settle=0.06)


class RemoteDriver:
    name = "提权助手"

    def __init__(self) -> None:
        from tools.remote import client
        self.get, self.post = client()
        self._n = 0

    def grab(self) -> np.ndarray:
        import cv2
        self._n += 1
        rel = f"shots/_bot{self._n % 2}.png"
        self.get("/snap?path=" + rel)
        a = cv2.imdecode(np.fromfile(str(ROOT / rel), dtype=np.uint8), cv2.IMREAD_COLOR)
        if a is None:
            raise RuntimeError("远程截图失败：" + rel)
        return cv2.cvtColor(a, cv2.COLOR_BGR2RGB)

    def click(self, x: int, y: int) -> None:
        self.post("/click", {"x": int(x), "y": int(y), "hold": 0.06})


def make_driver(verbose: bool = True) -> Driver:
    w = win.find_window(TITLE)
    blocked = win.input_blocked(w)
    if not blocked:
        return LocalDriver()

    try:
        from tools.remote import alive
        if alive():
            if verbose:
                print("本进程权限不够，改用提权助手 tools/remote.py")
            return RemoteDriver()
    except Exception:
        pass

    raise RuntimeError(
        "微信是管理员权限，脚本点不动。二选一：\n"
        "  ① 用管理员身份运行本程序（exe 自带管理员清单）；\n"
        "  ② 先执行 python tools/remote.py --start（弹一次 UAC），再跑本程序。\n"
        f"（{blocked.splitlines()[0]}）"
    )
