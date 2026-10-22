"""摸清「挑战」模式的界面：暗屏选技能就点中间那张，其余时间只观察。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from tools.remote import client  # noqa: E402

OUT = ROOT / "shots" / "probe"
TPL = cv2.imread(str(ROOT / "mjd" / "templates" / "skill_title.png"))


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    get, post = client()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    last = None
    last_save = 0.0
    n = 0
    while time.time() - t0 < secs:
        p = OUT / "_cur.png"
        get("/snap?path=" + p.relative_to(ROOT).as_posix())
        a = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
        score = float(cv2.matchTemplate(a, TPL, cv2.TM_CCOEFF_NORMED).max())
        state = "skill" if score > 0.75 else "other"
        now = time.time()
        if state != last or now - last_save > 10:
            n += 1
            cv2.imencode(".png", a)[1].tofile(str(OUT / f"{n:03d}_{state}_{int(now - t0):04d}.png"))
            print(f"{now - t0:6.1f}s {state:5s} title={score:.2f} all={a.mean():5.1f}", flush=True)
            last = state
            last_save = now
        if state == "skill":
            post("/click", {"x": 283, "y": 400, "hold": 0.05})
            time.sleep(1.0)
        else:
            time.sleep(0.7)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
