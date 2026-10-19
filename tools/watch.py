"""连续截图观察：定时抓取窗口画面，用来判断状态是否在变化。

用法:
    python tools/watch.py 6 3            # 每 3 秒抓一张，共 6 张
    python tools/watch.py 4 2 自动测试     # 备注
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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    note = sys.argv[3] if len(sys.argv) > 3 else "watch"

    w = win.find_window(TITLE)
    SHOTS.mkdir(exist_ok=True)
    stamp = time.strftime("%H%M%S")
    for i in range(n):
        img = win.grab_window(w)
        path = SHOTS / f"{stamp}_watch_{note}_{i:02d}.png"
        img.save(path)
        print(f"[{i}] {time.strftime('%H:%M:%S')} -> {path.name}")
        if i != n - 1:
            time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
