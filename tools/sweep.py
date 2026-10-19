"""按住时长扫描：找出能钓上鱼的按住时长。

对每个时长执行「按下 -> 保持 D 秒 -> 松开」，等待结果弹窗出现，
记录画面亮度（<阈值 表示有弹窗）并保存截图，便于人工核对弹窗内容。

用法:
    python tools/sweep.py --holds 0.4 0.8 1.2 1.6 2.0 2.4 2.8 --wait 2.2 --tag P
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import ui, win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"
DIM = 128.0


def bright(img) -> float:
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    return float(a[300:940, 20:530].mean())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xy", default="270,820")
    ap.add_argument("--holds", type=float, nargs="+", default=[0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8])
    ap.add_argument("--wait", type=float, default=2.2, help="松开后等待多久看结果")
    ap.add_argument("--dismiss-box", default="100,300")
    ap.add_argument("--tag", default="sweep")
    a = ap.parse_args()

    x, y = (int(v) for v in a.xy.split(","))
    dx, dy = (int(v) for v in a.dismiss_box.split(","))
    w = win.find_window(TITLE)
    ui.assert_ref_size(w.frame_size)
    pi = win.PostInput(w)
    SHOTS.mkdir(exist_ok=True)

    def clear_dialogs(max_tries: int = 4) -> None:
        for _ in range(max_tries):
            if bright(win.grab_window(w)) > DIM:
                return
            pi.click(dx, dy)
            time.sleep(0.8)

    clear_dialogs()
    print(f"起始 bright={bright(win.grab_window(w)):.1f}")

    for d in a.holds:
        pi.down(x, y)
        time.sleep(d)
        pi.up(x, y)
        time.sleep(a.wait)
        img = win.grab_window(w)
        b = bright(img)
        img.save(SHOTS / f"{a.tag}_hold{d:.1f}.png")
        print(f"按住 {d:4.1f}s -> bright={b:6.1f} {'【弹窗】' if b < DIM else ''}")
        clear_dialogs()
        time.sleep(0.4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
