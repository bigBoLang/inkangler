"""精细追踪一次钓鱼尝试的完整过程。

按住所给坐标，在按住期间与松开之后都持续高频采样，输出：
    - gold   : 判定条金色占比
    - bright : 画面平均亮度（弹窗变暗会明显下降，可作为「有弹窗」的判据）
    - arc    : 是否检测到判定条

用法:
    python tools/trace.py --press 270,820 --hold 8 --interval 0.15 --tag 尝试1
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import analyze, ui, win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"


def metrics(img):
    st = analyze.analyze(img)
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    bright = float(a[300:940, 20:530].mean())
    return st, bright

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--press", required=True)
    ap.add_argument("--hold", type=float, default=8.0)
    ap.add_argument("--after", type=float, default=4.0, help="松开后继续观察的秒数")
    ap.add_argument("--interval", type=float, default=0.15)
    ap.add_argument("--tag", default="trace")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    x, y = (int(v) for v in a.press.split(","))
    w = win.find_window(TITLE)
    ui.assert_ref_size(w.frame_size)
    pi = win.PostInput(w)

    SHOTS.mkdir(exist_ok=True)
    stamp = time.strftime("%H%M%S")
    i = 0
    t0 = time.time()

    def sample(label: str):
        nonlocal i
        img = win.grab_window(w)
        st, bright = metrics(img)
        t = time.time() - t0
        g = f"{st.frac * 100:5.1f}%" if st.present else "   --"
        print(f"t={t:5.2f}s {label:8s} gold={g} bright={bright:6.1f}")
        if a.save:
            img.save(SHOTS / f"{stamp}_{a.tag}_{i:03d}_{label}.png")
        i += 1
        return st, bright

    sample("before")
    pi.down(x, y)
    print(f"--- press at ({x},{y}) ---")
    while time.time() - t0 < a.hold:
        sample("hold")
        time.sleep(a.interval)
    pi.up(x, y)
    print("--- release ---")
    tr = time.time() - t0
    while time.time() - t0 < tr + a.after:
        sample("after")
        time.sleep(a.interval)
    sample("end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
