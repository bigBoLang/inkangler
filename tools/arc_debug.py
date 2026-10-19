"""遛鱼识别的调试可视化：把颜色分割结果、拟合的圆和 φ 角度画出来对照。

用法:
    python tools/arc_debug.py shots/X_minigame.png
    python tools/arc_debug.py "shots/vid/F_*.png" --limit 4
"""
from __future__ import annotations

import argparse
import glob
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


def render(path: Path, out_dir: Path) -> str:
    rgb = load_rgb(path)
    st = arc.analyze(rgb)
    m = arc.masks(rgb)
    x0, y0, x1, y1 = arc.ROI
    roi = cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2BGR)

    vis = np.zeros_like(roi)
    vis[m["navy"]] = (90, 90, 90)
    vis[m["zone"]] = (255, 220, 0)
    vis[m["prog"]] = (0, 140, 255)

    ov = roi.copy()
    g = st.geom
    if g:
        for r, col, w in ((g.r_in, (0, 255, 0), 1), (g.r_out, (0, 255, 0), 1)):
            cv2.circle(ov, (int(g.cx), int(g.cy)), int(r), col, w)
        # φ 参考射线：0=左端 90=顶 175=右端
        for phi in range(0, 180, 15):
            a = np.deg2rad(180 - phi)
            for rr in (g.r_in - 8, g.r_out + 8):
                px = int(g.cx + rr * np.cos(a))
                py = int(g.cy - rr * np.sin(a))
                cv2.circle(ov, (px, py), 1, (255, 0, 255), -1)
            if phi % 30 == 0:
                px = int(g.cx + (g.r_out + 22) * np.cos(a))
                py = int(g.cy - (g.r_out + 22) * np.sin(a))
                cv2.putText(ov, str(phi), (px - 8, py), cv2.FONT_HERSHEY_SIMPLEX,
                            0.32, (255, 0, 255), 1)
    if st.present:
        lines = [f"zone {st.zone_span[0]:.0f}~{st.zone_span[1]:.0f}",
                 f"prog {st.prog_span[0]:.0f}~{st.prog_span[1]:.0f} {st.prog_frac*100:.0f}%",
                 f"navy {st.navy_span[0]:.0f}~{st.navy_span[1]:.0f}"]
        for i, s in enumerate(lines):
            cv2.putText(ov, s, (4, 16 + 14 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (0, 255, 255), 1)

    panel = np.concatenate([ov, vis], axis=1)
    out = out_dir / f"dbg_{path.stem}.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", panel)[1].tofile(str(out))
    return f"{path.name}: {st}  -> {out.name}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()

    files: list[Path] = []
    for pat in a.images:
        p = Path(pat)
        if not p.is_absolute() and not glob.has_magic(str(p)):
            p = ROOT / p
        if not p.exists() and not glob.has_magic(str(p)):
            p = ROOT / "shots" / Path(pat).name
        if glob.has_magic(str(p)):
            files += [Path(x) for x in sorted(glob.glob(str(p)))]
        elif p.exists():
            files.append(p)

    out_dir = ROOT / "shots" / "dbg"
    for f in files[: a.limit]:
        print(render(f, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
