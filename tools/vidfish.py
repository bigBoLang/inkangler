"""从录像里逐帧找「鱼」和「捕获区」，换算成 φ，验证鱼的运动规律。

录像里小程序窗口的大小和截图窗口不同，所以几何不能用 arc 那套常量，
这里先用 HoughCircles 把转盘内圈定出来当圆心（很稳），再按给定的半径带采样。

用法:
    python tools/vidfish.py <视频> --start 20 --end 25
    python tools/vidfish.py <视频> --start 20 --end 25 --montage
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


def find_dial_center(rgb: np.ndarray):
    """在画面下半部用 HoughCircles 找转盘，返回 (cx, cy, r)。"""
    h, w = rgb.shape[:2]
    y0 = int(h * 0.5)
    sub = rgb[y0:, :]
    g = cv2.cvtColor(sub, cv2.COLOR_RGB2GRAY)
    g = cv2.medianBlur(g, 7)
    g2 = cv2.resize(g, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    c = cv2.HoughCircles(g2, cv2.HOUGH_GRADIENT, dp=1.5, minDist=150,
                         param1=140, param2=45,
                         minRadius=int(50 * 1.5), maxRadius=int(150 * 1.5))
    if c is None:
        return None
    cx, cy, r = c[0][0]
    return float(cx / 1.5), float(cy / 1.5) + y0, float(r / 1.5)


def imwrite_u(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if ok:
        buf.tofile(str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=20.0)
    ap.add_argument("--end", type=float, default=25.0)
    ap.add_argument("--r-in", type=float, default=118.0)
    ap.add_argument("--r-out", type=float, default=200.0)
    ap.add_argument("--montage", action="store_true")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print("打不开视频")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 22.86
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(a.start * fps))
    ok, bgr = cap.read()
    if not ok:
        print("读帧失败")
        return 1
    rgb0 = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    cen = find_dial_center(rgb0)
    if cen is None:
        print("找不到转盘")
        return 1
    cx, cy, r_dial = cen
    print(f"转盘中心=({cx:.0f},{cy:.0f}) 内圈 r={r_dial:.0f}")

    # find_fish 内部用 arc.ROI 裁剪，这里把 ROI 设成全图
    h, w = rgb0.shape[:2]
    arc.ROI = (0, 0, w, h)
    geom = arc.Geometry(cx, cy, a.r_in, a.r_out)

    tiles = []
    print("  t(s)  fishφ   zoneφ      [捕获区亮度带]")
    n = int((a.end - a.start) * fps)
    for i in range(n):
        ok, bgr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        hits = fish_find(rgb, geom)
        fphi = hits[0] if hits else None
        # 捕获区：在半径带里找亮的
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        prof = band_bright(hsv, geom)
        zp = zone_span(prof)
        t = a.start + i / fps
        print("%6.2f  %s   %s" % (
            t, ("%5.1f" % fphi) if fphi is not None else "  -- ",
            ("%5.1f~%5.1f" % zp) if zp else "   --"))
        if a.montage and i % 3 == 0:
            tiles.append(rgb.copy())
    cap.release()

    if a.montage and tiles:
        mont = np.concatenate([cv2.resize(t, None, fx=0.5, fy=0.5) for t in tiles[:12]], axis=1)
        imwrite_u(ROOT / "shots" / "dbg" / "vidfish.png", mont)
        print("montage ->", mont.shape)
    return 0


def fish_find(rgb, geom, prev=None):
    """简易找鱼：环带内「暗 + 低饱和」的块，且附近有亮眼。返回 φ 列表。"""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    H, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    yy, xx = np.mgrid[0:rgb.shape[0], 0:rgb.shape[1]]
    rr = np.hypot(xx - geom.cx, yy - geom.cy)
    inband = (rr >= geom.r_in) & (rr <= geom.r_out)
    body = ((S <= 95) & (V <= 178) & inband).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(body, 8)
    out = []
    for k in range(1, n):
        area = stats[k, cv2.CC_STAT_AREA]
        if area < 40 or area > 1200:
            continue
        bw, bh = stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
        if min(bw, bh) / max(bw, bh) < 0.25:
            continue
        bx, by = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP]
        eye = int(((V >= 205) & (S <= 60))[by:by + bh, bx:bx + bw].sum())
        if eye < 4:
            continue
        px, py = cent[k]
        phi = float(arc.phi_of(np.array([px]), np.array([py]), geom)[0])
        out.append((float(eye), phi))
    out.sort(reverse=True)
    return [p for _, p in out]


def band_bright(hsv, geom, n_bins=180):
    return arc._band_median_v(hsv, geom, (max(geom.r_in + 8, 130.0), geom.r_out - 6))


def zone_span(prof):
    light = prof >= 195.0
    if light.sum() < 8:
        return None
    s, e = arc._span_from_flags(light)
    if e - s < 8:
        return None
    return (s, e)


if __name__ == "__main__":
    raise SystemExit(main())
