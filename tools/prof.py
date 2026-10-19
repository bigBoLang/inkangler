"""给遛鱼主循环的每一段计时，看帧率被什么拖住了。

用法:
    python tools/prof.py --n 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, fish, win  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    a = ap.parse_args()

    w = win.find_window("墨迹大侠")
    tw = win.PostInput(w)
    tg, ta, tf = [], [], []
    img = None
    for i in range(a.n):
        t0 = time.perf_counter()
        img = win.grab_window(w)
        t1 = time.perf_counter()
        st = arc.analyze(img)
        t2 = time.perf_counter()
        if st.present and st.geom:
            fish.find_fish(img, st.geom, topk=1)
        t3 = time.perf_counter()
        tg.append(t1 - t0); ta.append(t2 - t1); tf.append(t3 - t2)
        time.sleep(0.02)
    tot = np.array(tg) + np.array(ta) + np.array(tf)
    print("n=%d" % a.n)
    print("  grab_window 中位 %5.1f ms  p90 %5.1f" % (np.median(tg) * 1e3, np.percentile(tg, 90) * 1e3))
    print("  arc.analyze 中位 %5.1f ms  p90 %5.1f" % (np.median(ta) * 1e3, np.percentile(ta, 90) * 1e3))
    print("  find_fish   中位 %5.1f ms  p90 %5.1f" % (np.median(tf) * 1e3, np.percentile(tf, 90) * 1e3))
    print("  合计        中位 %5.1f ms -> %.1f fps" % (np.median(tot) * 1e3, 1.0 / np.median(tot)))

    # PostMessage 一次往返要多久（点转盘下面那块木板，点错也无害）
    tt = []
    for _ in range(20):
        t0 = time.perf_counter()
        tw.click(276, 935)
        tt.append(time.perf_counter() - t0)
    print("  PostMessage click 中位 %5.1f ms" % (np.median(tt) * 1e3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
