"""看某一帧里「鱼检测」到底挑出了哪些候选，用来查误检。

和 mjd/fish.py 的判据一致，但会把**所有**候选都列出来（不截断 topk），
并画一张标注图：绿框 = 选中，红框 = 落选，同时标出各自 φ 与眼白像素数。

用法:
    python tools/fishdbg.py shots/run/R01_*.png            # 逐帧打印
    python tools/fishdbg.py shots/run/R01_030.png --save   # 存一张标注图
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, fish  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> np.ndarray:
    raw = np.fromfile(str(path), dtype=np.uint8)
    return cv2.cvtColor(cv2.imdecode(raw, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def candidates(rgb: np.ndarray, geom: arc.Geometry, **kw):
    """返回 [(phi, area, eye, box, r)]，不排序、不截断。"""
    x0, y0, x1, y1 = arc.ROI
    sub = rgb[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_RGB2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    s_max = kw.get("s_max", fish.S_MAX)
    v_max = kw.get("v_max", fish.V_MAX)
    area_min = kw.get("area_min", fish.AREA_MIN)
    aspect_min = kw.get("aspect_min", fish.ASPECT_MIN)
    eye_min = kw.get("eye_min", fish.EYE_MIN)

    body = ((S <= s_max) & (V <= v_max)).astype(np.uint8)
    ys, xs = np.nonzero(body)
    X, Y = xs + x0, ys + y0
    r = np.hypot(X - geom.cx, Y - geom.cy)
    phi = arc.phi_of(X.astype(float), Y.astype(float), geom)
    keep = (r >= geom.r_in - 2) & (r <= geom.r_out + 4) & (phi >= 0.5) & (phi <= 177.0)
    m = np.zeros_like(body)
    m[ys[keep], xs[keep]] = 1

    yy = np.arange(y0, y1, dtype=np.float32)
    xx = np.arange(x0, x1, dtype=np.float32)
    RR = np.hypot(xx[None, :] - geom.cx, yy[:, None] - geom.cy)
    bright = ((V >= fish.EYE_V) & (S <= fish.EYE_S) &
              (RR >= geom.r_in) & (RR <= geom.r_out - 4)).astype(np.uint8)

    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < area_min:
            continue
        bx, by = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        eye = int(bright[by:by + bh, bx:bx + bw].sum())
        if eye < eye_min:
            continue
        ok = min(bw, bh) / max(bw, bh) >= aspect_min
        cx, cy = float(cent[i][0]) + x0, float(cent[i][1]) + y0
        out.append((float(arc.phi_of(np.array([cx]), np.array([cy]), geom)[0]),
                    area, eye, (bx + x0, by + y0, bw, bh), float(np.hypot(cx - geom.cx, cy - geom.cy)), ok))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", nargs="+")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--top", type=int, default=4)
    a = ap.parse_args()

    paths = []
    for g in a.frames:
        paths.extend(Path(p) for p in sorted(glob.glob(g)))
    if not paths:
        print("没有匹配的图")
        return 1

    for p in paths[:400]:
        rgb = load(p)
        st = arc.analyze(rgb)
        if not st.present or st.geom is None:
            print(f"{p.name}: 不在遛鱼")
            continue
        cs = candidates(rgb, st.geom)
        cs.sort(key=lambda c: -c[2])
        desc = "  ".join(f"φ={c[0]:5.1f} area={c[1]:4d} eye={c[2]:3d} r={c[4]:5.1f}"
                         for c in cs[:a.top])
        print(f"{p.name}: geom=({st.geom.cx:.0f},{st.geom.cy:.0f},{st.geom.r_in:.0f},{st.geom.r_out:.0f}) "
              f"prog={st.prog_frac*100:5.1f}% zone={st.zone_span[0]:.0f}~{st.zone_span[1]:.0f} | {desc or '无候选'}")

        if a.save:
            vis = rgb.copy()
            for j, (phi, area, eye, (bx, by, bw, bh), rr, ok) in enumerate(cs):
                col = (0, 255, 0) if j == 0 else (255, 0, 0)
                cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), col, 2)
                cv2.putText(vis, f"{phi:.0f}/{eye}", (bx, by - 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
            cv2.circle(vis, (int(st.geom.cx), int(st.geom.cy)), 3, (255, 255, 0), -1)
            out = ROOT / "shots" / "dbg" / ("fishdbg_" + p.name)
            ok, buf = cv2.imencode(".png", cv2.cvtColor(vis, cv2.COLOR_RGB2BGR))
            buf.tofile(str(out))
            print("   ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
