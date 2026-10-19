"""卖掉鱼箱里的鱼。

用法:
    python tools/selltest.py            # 完整跑一轮
    python tools/selltest.py --count    # 只看现在售卖面板里有多少格
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import sell, win  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--batches", type=int, default=8)
    ap.add_argument("--shots", action="store_true")
    a = ap.parse_args()

    w = win.find_window("墨迹大侠")
    sd = ROOT / "shots" / "sell" if a.shots else None
    if sd:
        sd.mkdir(parents=True, exist_ok=True)
    s = sell.Seller(w, shot_dir=sd)

    if a.count:
        # 打开鱼箱 → 前往售卖，看一眼网格里有多少鱼，再关掉
        if not s.open_sell():
            print("第一页格数: 0（或者鱼箱面板没打开）")
            s.close()
            return 0
        print("第一页格数:", sell.count_tiles(s.grab()), "（一页最多 12 格）")
        s.close()
        return 0

    t0 = time.time()
    n = s.run(max_batches=a.batches)
    print("卖了 %d 批，用时 %.1fs" % (n, time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
