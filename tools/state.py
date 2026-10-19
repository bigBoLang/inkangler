"""把识别结果标注在画面上，做联系表，便于人工核对。

用法:
    python tools/state.py "shots/mg/H_*.png"
    python tools/state.py "shots/U_cast_*.png" --cols 4
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


def load_rgb(path: Path) -> np.ndarray:
    raw = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def arc_pt(g, phi, r):
    a = np.deg2rad(180 - phi)
    return int(g.cx + r * np.cos(a)), int(g.cy - r * np.sin(a))


def annotate(rgb: np.ndarray) -> tuple[np.ndarray, str]:
    st = arc.analyze(rgb)
    img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if not st.present or not st.geom:
        cv2.putText(img, "no minigame", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 255), 2)
        return img, "no minigame"
    g = st.geom
    cv2.circle(img, (int(g.cx), int(g.cy)), int(g.r_in), (0, 255, 0), 1)
    cv2.circle(img, (int(g.cx), int(g.cy)), int(g.r_out), (0, 255, 0), 1)
    # 轨道范围
    for phi in range(0, 180, 10):
        x, y = arc_pt(g, phi, g.r_out + 6)
        cv2.circle(img, (x, y), 1, (255, 0, 255), -1)
    # 捕获区
    for phi in np.arange(st.zone_span[0], st.zone_span[1] + 0.5, 1.0):
        cv2.circle(img, arc_pt(g, phi, g.r_mid), 2, (255, 255, 0), -1)
    # 进度环（转盘外那圈金色环，r≈114）
    r_prog = (arc.PROG_R[0] + arc.PROG_R[1]) / 2
    for phi in np.arange(st.prog_span[0], st.prog_span[1] + 0.5, 1.0):
        cv2.circle(img, arc_pt(g, phi, r_prog), 2, (0, 140, 255), -1)
    # 鱼
    fh = fish.find_fish(rgb, g, topk=3)
    txt = f"zone {st.zone_span[0]:.0f}-{st.zone_span[1]:.0f} prog {st.prog_span[0]:.0f}-{st.prog_span[1]:.0f}"
    if fh:
        h = fh[0]
        cv2.drawMarker(img, (h.x, h.y), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
        cv2.circle(img, arc_pt(g, h.phi, g.r_mid), 3, (0, 0, 255), -1)
        inside = st.zone_span[0] <= h.phi <= st.zone_span[1]
        txt += f"  fish {h.phi:.0f} eye{h.eye} {'IN' if inside else 'out'}"
    cv2.rectangle(img, (0, 0), (552, 18), (0, 0, 0), -1)
    cv2.putText(img, txt, (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)
    return img, txt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--scale", type=float, default=0.62)
    ap.add_argument("--crop", default="110,510,510,880")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()

    files = sorted(glob.glob(str(ROOT / a.pattern)))
    if not files:
        print("没匹配到:", a.pattern)
        return 1
    crop = tuple(int(v) for v in a.crop.split(","))
    thumbs = []
    for f in files[: a.limit]:
        rgb = load_rgb(Path(f))
        img, txt = annotate(rgb)
        c = img[crop[1]:crop[3], crop[0]:crop[2]]
        c = cv2.resize(c, None, fx=a.scale, fy=a.scale, interpolation=cv2.INTER_AREA)
        thumbs.append((Path(f).stem, c, txt))
        print(f"{Path(f).name:28s} {txt}")

    tw, th = thumbs[0][1].shape[1], thumbs[0][1].shape[0]
    rows = (len(thumbs) + a.cols - 1) // a.cols
    pad = 6
    sheet = np.full((rows * (th + 26) + pad, a.cols * (tw + pad) + pad, 3), 30, np.uint8)
    for i, (name, c, txt) in enumerate(thumbs):
        r, col = divmod(i, a.cols)
        y = pad + r * (th + 26)
        x = pad + col * (tw + pad)
        sheet[y:y + th, x:x + tw] = c
        cv2.putText(sheet, name[-9:], (x + 2, y + th + 11), cv2.FONT_HERSHEY_SIMPLEX,
                    0.34, (230, 230, 230), 1)
        cv2.putText(sheet, txt[:44], (x + 2, y + th + 23), cv2.FONT_HERSHEY_SIMPLEX,
                    0.32, (120, 220, 255), 1)
    out = Path(a.out) if a.out else ROOT / "shots" / "dbg" / "state.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", sheet)[1].tofile(str(out))
    print("->", out, sheet.shape)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
