"""把弧形轨道展开成 (φ, 半径) 直角坐标图，便于看清各元素的位置。

左上: 展开后的真实颜色   右上: 颜色分类结果
左下: 原始画面里的采样网格

用法:
    python tools/unwrap.py shots/X_minigame.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_rgb(path: Path) -> np.ndarray:
    raw = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--phi0", type=float, default=-10.0)
    ap.add_argument("--phi1", type=float, default=195.0)
    ap.add_argument("--scale", type=int, default=3)
    a = ap.parse_args()

    p = Path(a.image)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        p = ROOT / "shots" / Path(a.image).name
    rgb = load_rgb(p)

    st = arc.analyze(rgb)
    print(st)
    if not st.geom:
        print("未拟合出几何")
        return 1
    g = st.geom
    print(f"center=({g.cx:.1f},{g.cy:.1f}) r_in={g.r_in:.1f} r_out={g.r_out:.1f}")

    x0, y0, x1, y1 = arc.ROI
    hsv = cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2HSV)

    phis = np.arange(a.phi0, a.phi1, 1.0)
    rs = np.arange(g.r_in - 25, g.r_out + 25, 1.5)
    P, R = np.meshgrid(phis, rs, indexing="ij")
    A = np.deg2rad(180.0 - P)
    X = np.rint(g.cx + R * np.cos(A)).astype(int)
    Y = np.rint(g.cy - R * np.sin(A)).astype(int)
    inb = (X >= x0) & (X < x1) & (Y >= y0) & (Y < y1)
    Xc = np.clip(X - x0, 0, x1 - x0 - 1)
    Yc = np.clip(Y - y0, 0, y1 - y0 - 1)

    color = hsv[Yc, Xc].astype(np.float32)
    H, S, V = color[..., 0], color[..., 1], color[..., 2]
    navy = (H >= arc.NAVY_LO[0]) & (H <= arc.NAVY_HI[0]) & (S >= arc.NAVY_LO[1]) & (S <= arc.NAVY_HI[1]) & (V >= arc.NAVY_LO[2]) & (V <= arc.NAVY_HI[2])
    zone = (H >= arc.ZONE_LO[0]) & (H <= arc.ZONE_HI[0]) & (S >= arc.ZONE_LO[1]) & (S <= arc.ZONE_HI[1]) & (V >= arc.ZONE_LO[2]) & (V <= arc.ZONE_HI[2])
    prog = (H >= arc.PROG_LO[0]) & (H <= arc.PROG_HI[0]) & (S >= arc.PROG_LO[1]) & (S <= arc.PROG_HI[1]) & (V >= arc.PROG_LO[2]) & (V <= arc.PROG_HI[2])

    true_img = cv2.cvtColor(rgb[y0:y1, x0:x1][Yc, Xc], cv2.COLOR_RGB2BGR)
    cls = np.zeros_like(true_img)
    cls[navy] = (90, 90, 90)
    cls[zone] = (255, 220, 0)
    cls[prog] = (0, 140, 255)

    def up(im):
        return cv2.resize(im, (im.shape[1] * a.scale, im.shape[0] * a.scale),
                          interpolation=cv2.INTER_NEAREST)

    panel = np.concatenate([up(true_img), up(cls)], axis=1)
    # 加上半径刻度线
    for rr in range(int(g.r_in) - 24, int(g.r_out) + 24, 10):
        row = int((rr - (g.r_in - 25)) / 1.5)
        if 0 <= row < panel.shape[0]:
            cv2.line(panel, (0, row), (panel.shape[1], row), (0, 255, 0), 1)
    out = ROOT / "shots" / "dbg" / f"unwrap_{p.stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", panel)[1].tofile(str(out))
    print("->", out, panel.shape)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
