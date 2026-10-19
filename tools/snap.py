"""截图 CLI：抓「墨迹大侠」小程序窗口存到 shots/。

用法:
    python tools/snap.py               # 抓一张，文件名带时间戳
    python tools/snap.py 1 钓前         # 抓一张，名字带备注
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"


def main() -> int:
    note = "_".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    w = win.find_window(TITLE)
    img = win.grab_window(w)
    SHOTS.mkdir(exist_ok=True)
    name = time.strftime("%H%M%S") + (f"_{note}" if note else "") + ".png"
    path = SHOTS / name
    img.save(path)
    print(f"title={w.title!r} hwnd={hex(w.hwnd)} frame={w.frame} size={w.frame_size}")
    print(f"saved={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
