"""高频监控：观察判定条 / 弹窗 / 画面变化的实时状态。

用法:
    python tools/probe.py --seconds 12 --interval 0.1 --tag 空转
    python tools/probe.py --seconds 12 --tap 270,820 --tap-rate 6 --tag 连点

输出：
    t      时间
    arc    判定条金色占比（无判定条则 --）
    ang    判定条金色区间的中心角度
    br     画面平均亮度（弹窗遮罩会明显变暗）
    evt    检测到的事件（弹窗出现/消失、判定条出现/消失）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import analyze, win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"

# 弹窗判定：亮度阈值（正常 ~143，遮罩下 ~100 左右）
DIM_THRESHOLD = 128.0


def measure(img):
    st = analyze.analyze(img)
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    bright = float(a[300:940, 20:530].mean())
    return st, bright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--tap", default=None, help="连续点按的坐标 x,y")
    ap.add_argument("--tap-rate", type=float, default=6.0, help="每秒点按次数")
    ap.add_argument("--tag", default="probe")
    ap.add_argument("--save-every", type=int, default=0, help="每 N 帧存一张图")
    a = ap.parse_args()

    w = win.find_window(TITLE)
    pi = win.PostInput(w)

    tap_xy = None
    if a.tap:
        tap_xy = tuple(int(v) for v in a.tap.split(","))
        print(f"连续点按 {tap_xy} @ {a.tap_rate}/s")

    t0 = time.time()
    next_tap = t0
    prev_dim = None
    prev_present = None
    i = 0
    while True:
        t = time.time() - t0
        if t > a.seconds:
            break
        img = win.grab_window(w)
        st, bright = measure(img)
        dim = bright < DIM_THRESHOLD
        evt = []
        if prev_dim is not None and dim != prev_dim:
            evt.append("弹窗出现" if dim else "弹窗消失")
        if prev_present is not None and st.present != prev_present:
            evt.append("判定条出现" if st.present else "判定条消失")
        prev_dim, prev_present = dim, st.present

        arc = f"{st.frac * 100:5.1f}%" if st.present else "   --"
        ang = f"{(st.ang_start + st.ang_end) / 2:7.1f}°" if st.present else "      --"
        print(f"t={t:5.2f}s arc={arc} ang={ang} br={bright:6.1f} "
              f"{'弹窗' if dim else '    '} {' '.join(evt)}")

        if a.save_every and i % a.save_every == 0:
            img.save(SHOTS / f"{a.tag}_{i:03d}.png")
        i += 1

        if tap_xy and time.time() >= next_tap:
            pi.click(*tap_xy, settle=0.015)
            next_tap = time.time() + 1.0 / a.tap_rate
        else:
            time.sleep(max(0.0, min(a.interval, next_tap - time.time())
                           if tap_xy else a.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
