"""标定蓝条（捕获区）的运动速度。

流程：抛竿 -> 等小游戏出现 -> 依次执行「不按 / 按住 / 不按」三个阶段，
每帧打印蓝条区间、鱼的位置和黄条长度。用来量出按住/松开时蓝条各有多快。

用法:
    python tools/zoneprobe.py --secs 4 --rate 12
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, fish, ui, win  # noqa: E402

DIAL = (int(ui.ELEMENTS["bait_count"].x), int(ui.ELEMENTS["bait_count"].y) + 10)


def brightness(img) -> float:
    a = np.asarray(img.convert("L"), dtype=np.float32)
    return float(a[300:940, 20:530].mean())


def dismiss(w, pi, tries: int = 4) -> bool:
    clicked = False
    for _ in range(tries):
        if brightness(win.grab_window(w)) >= 128:
            break
        pi.click(100, 300)
        clicked = True
        time.sleep(0.45)
    return clicked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=4.0, help="每个阶段的时长")
    ap.add_argument("--rate", type=float, default=12.0)
    ap.add_argument("--cast", type=float, default=1.5)
    a = ap.parse_args()

    w = win.find_window("墨迹大侠")
    pi = win.PostInput(w)
    print("窗口:", hex(w.hwnd), w.rect)

    if dismiss(w, pi):
        print("关掉了弹窗")
    time.sleep(0.3)

    print(f"抛竿: 按住 {a.cast}s")
    pi.down(*DIAL)
    time.sleep(a.cast)
    pi.up(*DIAL)

    # 等小游戏出来（咬钩时间不定，最多等 15s）
    t0 = time.time()
    st = None
    while time.time() - t0 < 15.0:
        time.sleep(0.15)
        st = arc.analyze(win.grab_window(w))
        if st.present:
            break
    if st is None or not st.present:
        print("!! 没等到小游戏")
        return 1
    print("小游戏出现，用了 %.2fs" % (time.time() - t0))

    for phase, hold in (("release1", False), ("HOLD", True), ("release2", False)):
        print(f"--- {phase} ---")
        if hold:
            pi.down(*DIAL)
        t0 = time.time()
        prev = None
        while time.time() - t0 < a.secs:
            ts = time.time() - t0
            rgb = win.grab_window(w)
            s = arc.analyze(rgb)
            fh = fish.find_fish(rgb, s.geom) if s.present else []
            fp = fh[0].phi if fh else None
            zc = (s.zone_span[0] + s.zone_span[1]) / 2 if s.present else None
            d = ""
            if prev is not None and zc is not None and ts > prev[0] + 1e-3:
                d = " dz/dt=%+6.0f°/s" % ((zc - prev[1]) / (ts - prev[0]))
            if zc is not None:
                prev = (ts, zc)
            print("%5.2fs zone=%s zc=%s fish=%s prog=%4.1f%%%s" % (
                ts,
                ("%.0f~%.0f" % s.zone_span) if s.present else "--",
                ("%.0f" % zc) if zc is not None else "--",
                ("%.0f" % fp) if fp is not None else "--",
                s.prog_frac * 100 if s.present else 0.0, d))
            time.sleep(max(0.0, 1.0 / a.rate - 0.02))
        if hold:
            pi.up(*DIAL)
        time.sleep(0.2)

    if dismiss(w, pi):
        print("关掉了结束弹窗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
