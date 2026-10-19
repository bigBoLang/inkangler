"""从录像里逐帧提取遛鱼小游戏的「鱼 / 蓝条」角度轨迹，找鱼运动的规律。

直接读 mp4（路径是 ASCII，VideoCapture 能用），不落盘 PNG。

用法:
    python tools/vidtrak.py <视频> --scan                # 只扫哪些时间段在遛鱼
    python tools/vidtrak.py <视频> --start 20 --end 26   # 分析这一段
    python tools/vidtrak.py <视频> --dump shots/vid/trak # 顺便把带标注的帧存下来
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

# ---- 原生分辨率 592x1280 下，遛鱼转盘的粗略工作区（只为裁剪提速用）
WORK = (0, 620, 592, 1180)   # x0,y0,x1,y1

# 深藏青轨道：蓝、暗、够饱和
NAVY_S = 90
NAVY_V = 200
# 亮青捕获区：蓝、亮
ZONE_V = 200


def imwrite_u(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if ok:
        buf.tofile(str(path))


def blue_band(rgb: np.ndarray) -> np.ndarray:
    """整幅里「蓝色且偏暗/亮」的像素 -> 可能是轨道。"""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    H, S, V = hsv[..., 0].astype(np.int16), hsv[..., 1].astype(np.int16), hsv[..., 2].astype(np.int16)
    blue = (H >= 92) & (H <= 132) & (S >= NAVY_S)
    dark = blue & (V <= NAVY_V)
    return dark


def fit_geom(rgb: np.ndarray):
    """在整幅原生帧上拟合轨道外沿圆 -> (cx, cy, r_out)。只取下半部分。"""
    x0, y0, x1, y1 = WORK
    sub = rgb[y0:y1, x0:x1]
    m = blue_band(sub).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    pts = []
    for x in range(20, sub.shape[1] - 20, 3):
        col = m[:, x]
        idx = np.flatnonzero(col)
        if len(idx):
            pts.append((x + x0, int(idx[0]) + y0))
    if len(pts) < 20:
        return None
    P = np.array(pts, float)
    for _ in range(4):
        A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]
        b = (P ** 2).sum(axis=1)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        cx, cy = sol[0], sol[1]
        r = float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 1.0)))
        resid = np.abs(np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r)
        keep = resid < 4.0
        if keep.all():
            break
        P = P[keep]
    return float(cx), float(cy), r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=1e9)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--dump", default="")
    ap.add_argument("--csv", default="")
    ap.add_argument("--fit-at", type=float, default=None)
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print("打不开视频")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 22.86
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"fps={fps:.2f} frames={n}")

    # 先拿一帧做几何标定
    fit_t = a.fit_at if a.fit_at is not None else (a.start + 0.5)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(fit_t * fps))
    ok, bgr = cap.read()
    if not ok:
        print("读帧失败")
        return 1
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    g = fit_geom(rgb)
    print("拟合几何:", None if g is None else tuple(round(v, 1) for v in g))
    cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
