"""抛竿后按脚本按住/松开来标定蓝条运动，并把每帧存图。

脚本写法：H3R2 表示「按住 3 秒，松开 2 秒」，可任意串联。

用法:
    python tools/capture_run.py --script H4R3H3 --out shots/mg2
    python tools/capture_run.py --hold --frames 26
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, fish, ui, win  # noqa: E402

DIAL = (int(ui.ELEMENTS["bait_count"].x), int(ui.ELEMENTS["bait_count"].y) + 10)
ROOT = Path(__file__).resolve().parents[1]


def brightness(img) -> float:
    a = np.asarray(img.convert("L"), dtype=np.float32)
    return float(a[300:940, 20:530].mean())


def dismiss(w, pi, tries: int = 5) -> bool:
    clicked = False
    for _ in range(tries):
        if brightness(win.grab_window(w)) >= 128:
            break
        pi.click(100, 300)
        clicked = True
        time.sleep(0.45)
    return clicked


def parse_script(s: str) -> list[tuple[str, float]]:
    out = []
    for m in re.finditer(r"([HRhr])(\d+(?:\.\d+)?)", s):
        out.append((m.group(1).upper(), float(m.group(2))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="H4R3H3R3", help="如 H4R3H3R3")
    ap.add_argument("--hold", action="store_true", help="全程按住（忽略 --script）")
    ap.add_argument("--out", default="shots/mg2")
    ap.add_argument("--frames", type=int, default=26, help="--hold 时的帧数")
    ap.add_argument("--cast", type=float, default=1.5)
    a = ap.parse_args()

    w = win.find_window("墨迹大侠")
    pi = win.PostInput(w)
    print("窗口:", hex(w.hwnd), w.rect)
    if dismiss(w, pi):
        print("关掉了弹窗")
    time.sleep(0.3)

    print("抛竿 …")
    pi.down(*DIAL)
    time.sleep(a.cast)
    pi.up(*DIAL)

    t0 = time.time()
    st = None
    while time.time() - t0 < 15.0:
        time.sleep(0.1)
        st = arc.analyze(win.grab_window(w))
        if st.present:
            break
    if st is None or not st.present:
        print("!! 没等到小游戏")
        return 1
    print("小游戏出现，用了 %.2fs" % (time.time() - t0))

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    if a.hold:
        plan = [("H", 999.0)]
    else:
        plan = parse_script(a.script)
    print("脚本:", plan)

    i = 0
    holding = False
    prev_fish = None
    for act, secs in plan:
        if act == "H" and not holding:
            pi.down(*DIAL)
            holding = True
        elif act == "R" and holding:
            pi.up(*DIAL)
            holding = False
        print(f"--- {act} {secs}s ---")
        t1 = time.time()
        while time.time() - t1 < secs and i < 400:
            ts = time.time() - t0
            rgb = win.grab_window(w)
            s = arc.analyze(rgb)
            fh = fish.find_fish(rgb, s.geom) if s.present else []
            fp = fh[0].phi if fh else None
            rgb.save(out / f"D_{i:03d}.png")
            vs = ""
            if prev_fish is not None and fp is not None:
                pass
            zc = (s.zone_span[0] + s.zone_span[1]) / 2
            print("%5.2fs %-62s fish=%-5s zc=%5.1f" % (
                ts, str(s), ("%.0f" % fp) if fp is not None else "--",
                zc if s.present else -1))
            prev_fish = fp
            i += 1
            if not s.present and i > 4:
                print("小游戏结束")
                break
        if not s.present and i > 4:
            break
    if holding:
        pi.up(*DIAL)
    time.sleep(0.3)
    print("松开后:", arc.analyze(win.grab_window(w)))
    if dismiss(w, pi):
        print("关掉了结束弹窗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
