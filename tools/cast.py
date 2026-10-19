"""抛竿过程观测：短暂按一下触发抛竿，然后在不干预的情况下高频观察进度条变化。

用来判断「鱼咬钩后进度条是否会自己涨」「需要玩家做什么」。

用法:
    python tools/cast.py --press 270,820 --tap 0.08 --observe 5 --interval 0.08 --tag C1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import analyze, ui, win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"
DIM = 128.0


def bright(img) -> float:
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    return float(a[300:940, 20:530].mean())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--press", default="270,820")
    ap.add_argument("--tap", type=float, default=0.08, help="按住多久（模拟点击）")
    ap.add_argument("--observe", type=float, default=5.0)
    ap.add_argument("--interval", type=float, default=0.08)
    ap.add_argument("--tag", default="cast")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    x, y = (int(v) for v in a.press.split(","))
    w = win.find_window(TITLE)
    ui.assert_ref_size(w.frame_size)
    pi = win.PostInput(w)
    SHOTS.mkdir(exist_ok=True)

    # 清掉残留弹窗
    for _ in range(4):
        if bright(win.grab_window(w)) > DIM:
            break
        pi.click(100, 300)
        time.sleep(0.8)

    pi.down(x, y)
    time.sleep(a.tap)
    pi.up(x, y)
    t0 = time.time()
    i = 0
    prev = None
    while time.time() - t0 < a.observe:
        img = win.grab_window(w)
        st = analyze.analyze(img)
        b = bright(img)
        t = time.time() - t0
        arc = f"{st.frac * 100:5.1f}%" if st.present else "  无 "
        ang = f"{(st.ang_start + st.ang_end) / 2:7.1f}°" if st.present else "     --"
        print(f"t={t:5.2f}s arc={arc} ang={ang} br={b:6.1f} {'弹窗' if b < DIM else ''}")
        if a.save:
            img.save(SHOTS / f"{a.tag}_{i:03d}_t{t:04.2f}.png")
        i += 1
        prev = st
        time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
