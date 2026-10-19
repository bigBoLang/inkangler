"""实时监视遛鱼识别，用来排查现场问题。

用法:
    python tools/probe_live.py --secs 12 --rate 3 [--cast]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, fish, ui, win  # noqa: E402

DIAL = (int(ui.ELEMENTS["bait_count"].x), int(ui.ELEMENTS["bait_count"].y) + 10)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=12.0)
    ap.add_argument("--rate", type=float, default=3.0)
    ap.add_argument("--cast", action="store_true")
    a = ap.parse_args()

    w = win.find_window("墨迹大侠")
    pi = win.PostInput(w)
    print("窗口:", hex(w.hwnd), w.rect)

    if a.cast:
        print("抛竿 @", DIAL)
        pi.down(*DIAL)
        time.sleep(1.5)
        pi.up(*DIAL)

    t0 = time.time()
    while time.time() - t0 < a.secs:
        ts = time.time() - t0
        rgb = win.grab_window(w)
        st = arc.analyze(rgb)
        geom = st.geom or arc.default_geometry()
        m = arc.masks(rgb)
        band = arc.band_profile(rgb, geom)
        dark = int((band < arc.ZONE_V).sum())
        track = arc._span_from_hist(arc._hist(
            m["navy"] | m["zone"] | m["prog"], geom))
        fh = fish.find_fish(rgb, geom) if st.present else []
        print("%5.2fs dark=%3d track=%3.0f~%3.0f %s %s" % (
            ts, dark, track[0], track[1], st,
            ("fish=%.0f/eye%d" % (fh[0].phi, fh[0].eye)) if fh else ""))
        time.sleep(max(0.0, 1.0 / a.rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
