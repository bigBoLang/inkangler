"""遛鱼小游戏的实机实验台。

用法:
    # 抛竿后按住 6 秒，同时高频采样识别结果
    python tools/minigame.py hold --secs 6 --rate 15 --save

    # 只拍一张并识别
    python tools/minigame.py look
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, ui, win  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIAL = (int(ui.ELEMENTS["bait_count"].x), int(ui.ELEMENTS["bait_count"].y) + 10)


def brightness(img: Image.Image) -> float:
    a = np.asarray(img.convert("L"), dtype=np.float32)
    return float(a[300:940, 20:530].mean())


def dismiss_dialog(w, pi, tries: int = 4) -> bool:
    """弹窗（变暗）时点空白处关掉。返回是否点过。"""
    clicked = False
    for _ in range(tries):
        img = win.grab_window(w)
        if brightness(img) >= 128:
            break
        pi.click(100, 300)
        clicked = True
        time.sleep(0.45)
    return clicked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["look", "hold", "tap"])
    ap.add_argument("--secs", type=float, default=6.0)
    ap.add_argument("--rate", type=float, default=15.0)
    ap.add_argument("--cast", type=float, default=1.5, help="抛竿按住时长")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    w = win.find_window("墨迹大侠")
    pi = win.PostInput(w)
    print("窗口:", hex(w.hwnd), w.rect)

    def shot():
        return win.grab_window(w)

    if a.mode == "look":
        img = shot()
        print("亮度 %.1f  %s" % (brightness(img), arc.analyze(img)))
        return 0

    if a.mode == "cast":
        return 0

    if dismiss_dialog(w, pi):
        print("关掉了弹窗")
    time.sleep(0.3)

    # 抛竿：点住 ~1.5s 再松开
    print(f"抛竿: 按住 {a.cast}s @ {DIAL}")
    pi.down(*DIAL)
    time.sleep(a.cast)
    pi.up(*DIAL)
    time.sleep(0.4)

    if a.mode == "tap":
        for i in range(int(a.secs * a.rate)):
            pi.down(*DIAL)
            time.sleep(0.06)
            pi.up(*DIAL)
            img = shot()
            st = arc.analyze(img)
            print("%5.2fs tap  %s" % (i / a.rate, st))
            if not st.present:
                break
        return 0

    # hold 模式：按住并采样
    pi.down(*DIAL)
    t0 = time.time()
    out_dir = ROOT / "shots" / "mg"
    if a.save:
        out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    while time.time() - t0 < a.secs:
        img = shot()
        st = arc.analyze(img)
        t = time.time() - t0
        print("%5.2fs  bri=%5.1f  %s" % (t, brightness(img), st))
        if a.save and n % 3 == 0:
            img.save(out_dir / f"H_{n:03d}_t{t:04.1f}.png")
        n += 1
        time.sleep(max(0.0, 1.0 / a.rate - 0.02))
    pi.up(*DIAL)
    time.sleep(0.3)
    img = shot()
    print("松开后: %s" % arc.analyze(img))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
