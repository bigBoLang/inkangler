"""按住某个坐标并连续截图，然后松开 —— 用来观察「钓鱼」过程中的状态变化。

用法:
    python tools/hold.py 270,820 --hold 5 --interval 0.5
    python tools/hold.py 270,820 --cycles 3 --hold 2 --gap 1

参数:
    --hold      每轮按住多少秒
    --interval  按住期间每隔多少秒截一张
    --cycles    按住/松开的轮数
    --gap       松开后到下一轮之间的间隔
    --tag       文件名备注
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import ui, win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xy", help="窗口内坐标，形如 270,820")
    ap.add_argument("--hold", type=float, default=5.0)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--tag", default="hold")
    a = ap.parse_args()

    x, y = (int(v) for v in a.xy.split(","))
    w = win.find_window(TITLE)
    ui.assert_ref_size(w.frame_size)
    if not win.focus(w):
        print("[警告] 未能把游戏窗口切到前台")

    SHOTS.mkdir(exist_ok=True)
    stamp = time.strftime("%H%M%S")
    seq = 0

    def shot(label: str):
        nonlocal seq
        img = win.grab_window(w)
        p = SHOTS / f"{stamp}_{a.tag}_{seq:02d}_{label}.png"
        img.save(p)
        print(f"  [{seq}] {datetime.now().strftime('%H:%M:%S.%f')[:-3]} {label}")
        seq += 1

    for c in range(a.cycles):
        print(f"--- cycle {c + 1}/{a.cycles}: press at ({x},{y}) ---")
        win.press(*win.image_to_screen(w, x, y))
        shot("press")
        t0 = time.time()
        while time.time() - t0 < a.hold:
            time.sleep(a.interval)
            shot(f"hold{time.time() - t0:.1f}s")
        win.release(*win.image_to_screen(w, x, y))
        shot("release")
        if c != a.cycles - 1:
            time.sleep(a.gap)
    shot("end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
