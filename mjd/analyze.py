"""钓鱼判定条（转盘外圈弧形）的视觉分析 —— 向量化实现。

按住转盘时会 PE 出现一圈弧形进度条：已蓄力部分为金色，剩余部分为蓝色。
本模块用预计算的极坐标采样表 + numpy 花式索引，把弧形量化成「金色占比」和
「金色区间的角度范围」，单帧耗时 ~5ms，可用于实时闭环。

坐标系：图像坐标，x 向右，y 向下。角度约定：0°=右，+90°=上，±180°=左。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# ---- 判定条几何（标定值，基于 552x991 窗口截图）
ARC_CENTER = (306.0, 655.0)
ARC_R_IN = 86.0
ARC_R_OUT = 108.0
N_ANG = 360
N_RAD = 16

# 弧形条大致区域，用于排除顶部金币图标等干扰
ARC_ROI = (150, 540, 460, 810)  # x0, y0, x1, y1

ImagesDir = Path(__file__).resolve().parents[1] / "shots"


@dataclass
class ArcState:
    present: bool          # 是否检测到弧形条
    frac: float            # 金色占「金+蓝」的比例 0~1
    ang_start: float       # 金色连续区间起点（度）
    ang_end: float         # 金色连续区间终点（度）
    covered: tuple[float, float]  # 有颜色覆盖的角度范围

    def __str__(self) -> str:
        if not self.present:
            return "ArcState(无判定条)"
        return (f"ArcState(frac={self.frac * 100:5.1f}% "
                f"gold={self.ang_start:6.1f}°~{self.ang_end:6.1f}° "
                f"cover={self.covered[0]:6.1f}°~{self.covered[1]:6.1f}°)")


ROI_X0, ROI_Y0, ROI_X1, ROI_Y1 = ARC_ROI
_ROI_W, _ROI_H = ROI_X1 - ROI_X0, ROI_Y1 - ROI_Y0


def _build_sampler(cx=ARC_CENTER[0], cy=ARC_CENTER[1],
                   r_in=ARC_R_IN, r_out=ARC_R_OUT):
    ang = np.linspace(-180.0, 180.0, N_ANG, endpoint=False)
    rad = np.linspace(r_in, r_out, N_RAD)
    A, R = np.meshgrid(ang, rad, indexing="ij")
    # 采样点转成 ROI 内的相对坐标，避免在整图上做 HSV
    X = np.rint(cx + R * np.cos(np.deg2rad(A))).astype(np.int32) - ROI_X0
    Y = np.rint(cy - R * np.sin(np.deg2rad(A))).astype(np.int32) - ROI_Y0
    np.clip(X, 0, _ROI_W - 1, out=X)
    np.clip(Y, 0, _ROI_H - 1, out=Y)
    return ang, X, Y


_ANG, _X, _Y = _build_sampler()


def to_ndarray(img) -> np.ndarray:
    """接受 PIL.Image 或 ndarray，统一转成 RGB ndarray。"""
    if isinstance(img, np.ndarray):
        return img
    return np.asarray(img.convert("RGB"))


def arc_masks(img_rgb: np.ndarray):
    """只在判定条 ROI 内做颜色分割，返回 (gold, blue) 布尔掩码。"""
    rgb = to_ndarray(img_rgb)[ROI_Y0:ROI_Y1, ROI_X0:ROI_X1]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gold = cv2.inRange(hsv, (12, 110, 140), (40, 255, 255)) > 0
    blue = cv2.inRange(hsv, (90, 70, 90), (135, 255, 255)) > 0
    return gold, blue


def _longest_run(mask: np.ndarray) -> tuple[int, int]:
    """环形布尔数组上最长连续 True 段的 (起, 止) 下标。"""
    n = len(mask)
    if not mask.any():
        return (-1, -1)
    if mask.all():
        return (0, n - 1)
    # 从某个 False 之后开始，把环展开成线性
    start = int(np.argmin(mask))
    m = np.roll(mask, -start)
    idx = np.flatnonzero(~m)
    if len(idx) < 2:
        return (0, n - 1)
    lens = np.diff(idx) - 1
    k = int(np.argmax(lens))
    s = (idx[k] + 1 + start) % n
    e = (idx[k + 1] - 1 + start) % n
    return (s, e)


def analyze(img) -> ArcState:
    rgb = to_ndarray(img)
    gold, blue = arc_masks(rgb)

    gs = gold[_Y, _X].any(axis=1)
    bs = blue[_Y, _X].any(axis=1)
    covered = gs | bs
    total = int(covered.sum())
    # 需同时有蓝色（未填充部分）才认为是真的进度条，
    # 否则转盘本身的金色光环会被误判。
    if total < 40 or int(bs.sum()) < 20:
        return ArcState(False, 0.0, 0.0, 0.0, (0.0, 0.0))

    frac = float(gs.sum()) / total

    # 覆盖区的角度范围
    ci = np.flatnonzero(covered)
    cover = (float(_ANG[ci.min()]), float(_ANG[ci.max()]))

    # 金色最长连续段
    gm = gs & covered
    s, e = _longest_run(gm)
    if s < 0:
        gold_span = (0.0, 0.0)
    else:
        gold_span = (float(_ANG[s]), float(_ANG[e]))

    return ArcState(True, frac, gold_span[0], gold_span[1], cover)


def analyze_file(path) -> ArcState:
    p = Path(path)
    if not p.is_absolute():
        cand = ImagesDir / p
        p = cand if cand.exists() else ImagesDir / Path(path).name
    raw = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(p)
    return analyze(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        print(f"{Path(arg).name}: {analyze_file(arg)}")
