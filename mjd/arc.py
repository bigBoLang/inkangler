"""遛鱼小游戏的实时视觉识别。

环绕转盘的弧形缎带，从外到内分三层：
    zone  亮青色「捕获区」—— 蓝条，要让它罩住鱼
    prog  橙色「进度条」—— 黄条，从左端起长满即钓上鱼
    navy  深藏青色轨道底色
另外轨道上还有移动的图标（鱼 / 宝箱）标记。

角度约定
--------
用 φ 表示「从轨道左端顺时针」的角度：
    φ=0   左端（3 点方向的反面，即 9 点方向）
    φ=90  正上方
    φ=175 右端
于是整条轨道就是 φ ∈ [0, ~175]，不需要处理跨 ±180° 的回绕。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ImagesDir = Path(__file__).resolve().parents[1] / "shots"

ROI = (125, 520, 490, 850)  # x0, y0, x1, y1（窗口内坐标）
PHI_LO, PHI_HI = -6.0, 184.0  # 轨道只在这个角度范围内，之外的一律当噪声丢掉

# 参考窗口尺寸与实测标定的轨道几何（窗口尺寸不变，所以是常量）。
# 拟合会被画面里的鱼竿/倒影污染，实测反复确认过：不同对局里外沿完全重合，
# 所以直接用常量，稳得多。
REF_SIZE = (552, 991)
REF_GEOM = (277.0, 764.7, 110.0, 156.0)

# ---- 颜色范围（HSV，OpenCV: H 0-179, S/V 0-255），由实拍采样得到
NAVY_LO, NAVY_HI = (104, 135, 95), (122, 190, 170)   # 深藏青轨道
ZONE_LO, ZONE_HI = (96, 145, 200), (110, 235, 255)   # 亮青色捕获区（只作粗参考）
PROG_LO, PROG_HI = (12, 125, 170), (30, 200, 255)    # 橙色进度条

# 蓝色轨道带（藏青底 + 亮青捕获区）所在的半径范围。
# 蓝色轨道带在外圈(~128~156)，金色进度环在内圈(~108~122)。
BAND_R = (131.0, 152.0)
ZONE_V = 180.0     # 亮度阈值：藏青 V≈126，捕获区 V≈245，分得很开
ZONE_GAP = 13      # 允许被鱼/宝箱图标遮住的最大角度（度）
NAVY_MIN = 40      # 至少要这么多度的藏青才认为在遛鱼
TRACK_DEG = 176.0  # 轨道总长（φ 从 0 到右端），用作进度百分比的分母

# 进度环（金色圆环，实为进度条）的采样参数。
PROG_R = (108.0, 122.0)
PROG_HUE = (8, 35)      # 橙色
PROG_S_MIN = 80.0
PROG_V_MIN = 165.0
PROG_HIT = 0.40         # 一个 φ 格里至少这么大比例的采样点发橙，才算「点亮」
PROG_FULL = 0.92        # 到这个比例就快上钩了（只用来打日志，不作判据）


@dataclass
class Geometry:
    cx: float
    cy: float
    r_in: float
    r_out: float

    @property
    def r_mid(self) -> float:
        return (self.r_in + self.r_out) / 2


@dataclass
class FishingState:
    present: bool = False
    geom: Geometry | None = None
    zone_span: tuple[float, float] = (0.0, 0.0)
    prog_span: tuple[float, float] = (0.0, 0.0)
    prog_frac: float = 0.0          # 进度金环点亮长度 / 轨道总长
    navy_span: tuple[float, float] = (0.0, 0.0)
    fish_angle: float | None = None  # 鱼标记的 φ

    def __str__(self) -> str:
        if not self.present:
            return "FishingState(未在遛鱼)"
        f = f"{self.fish_angle:.0f}°" if self.fish_angle is not None else "--"
        return (f"FishingState(zone={self.zone_span[0]:.0f}~{self.zone_span[1]:.0f}° "
                f"prog={self.prog_span[0]:.0f}~{self.prog_span[1]:.0f}° "
                f"({self.prog_frac * 100:4.1f}% of track) "
                f"fish={f} navy={self.navy_span[0]:.0f}~{self.navy_span[1]:.0f}°)")


def to_rgb(img) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img
    return np.asarray(img.convert("RGB"))


def masks(img_rgb) -> dict[str, np.ndarray]:
    """ROI 内的颜色掩码（ROI 局部坐标）。"""
    rgb = to_rgb(img_rgb)
    x0, y0, x1, y1 = ROI
    hsv = cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2HSV)
    return {
        "navy": cv2.inRange(hsv, NAVY_LO, NAVY_HI) > 0,
        "zone": cv2.inRange(hsv, ZONE_LO, ZONE_HI) > 0,
        "prog": cv2.inRange(hsv, PROG_LO, PROG_HI) > 0,
    }


# ---------------------------------------------------------------- 几何标定


def _circle_fit(pts: np.ndarray, iters: int = 4, tol: float = 3.0):
    """Kasa 代数拟合 + 残差剔除，返回 (cx, cy, r, n_inliers)。"""
    P = np.asarray(pts, float)
    for _ in range(iters):
        if len(P) < 8:
            return None
        A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]
        b = (P ** 2).sum(axis=1)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        cx, cy = sol[0], sol[1]
        r = float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 1.0)))
        resid = np.abs(np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r)
        keep = resid < tol
        if keep.all():
            break
        P = P[keep]
    return float(cx), float(cy), r, len(P)


def fit_geometry(m: dict[str, np.ndarray]) -> Geometry | None:
    """拟合缎带的外沿圆，得到圆心与半径。

    缎带只覆盖约半个圆周，直接对整块掩码做最小二乘会退化成小圆；
    这里改成先扫描出「外沿」这条干净的圆弧（从上往下扫、从两侧往中间扫），
    再对这条弧做代数拟合，条件数好得多。
    """
    x0, y0, x1, y1 = ROI
    band = (m["navy"] | m["zone"] | m["prog"]).astype(np.uint8)
    band = cv2.morphologyEx(band, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)).astype(bool)
    if band.sum() < 800:
        return None

    pts: list[tuple[int, int]] = []
    # 自上而下扫：得到缎带上沿（最干净的一段圆弧）
    for x in range(x0 + 40, x1 - 30, 4):
        col = band[:, x - x0]
        idx = np.flatnonzero(col)
        if len(idx):
            pts.append((x, y0 + int(idx[0])))
    if len(pts) < 20:
        return None
    first = _circle_fit(np.array(pts, float))
    if first is None:
        return None
    fcx, fcy, fr, _ = first

    # 再用首轮圆去筛选左右两侧的边缘点，把圆弧补完整
    def _near(x: int, y: int) -> bool:
        return abs(np.hypot(x - fcx, y - fcy) - fr) < 4.0

    for y in range(y0 + 120, y1 - 60, 4):
        row = band[y - y0, :]
        idx = np.flatnonzero(row)
        if len(idx):
            xl, xr = x0 + int(idx[0]), x0 + int(idx[-1])
            if _near(xl, y):
                pts.append((xl, y))
            if _near(xr, y):
                pts.append((xr, y))

    if len(pts) < 30:
        return None
    fit = _circle_fit(np.array(pts, float))
    if fit is None:
        return None
    cx, cy, r_out, n_in = fit
    if n_in < 25:
        return None

    # 用拟合出的圆心统计缎带的真实内外半径
    ys, xs = np.nonzero(band)
    d = np.hypot(xs + x0 - cx, ys + y0 - cy)
    sel = (d > r_out - 70) & (d < r_out + 12)
    if sel.sum() < 500:
        return None
    r_out = float(np.percentile(d[sel], 98))
    navy = m["navy"]
    ys, xs = np.nonzero(navy)
    dn = np.hypot(xs + x0 - cx, ys + y0 - cy)
    dn = dn[(dn > r_out - 70) & (dn < r_out + 8)]
    if len(dn) < 300:
        return None
    r_in = float(np.percentile(dn, 6))
    if r_out - r_in < 15:
        return None
    return Geometry(cx, cy, r_in, r_out)


def phi_of(xs: np.ndarray, ys: np.ndarray, geom: Geometry) -> np.ndarray:
    """把 ROI 局部像素坐标换算成 φ（度，0~360）。"""
    dx = xs - geom.cx
    dy = ys - geom.cy
    # 数学角：0=右，+90=上
    a = np.degrees(np.arctan2(-dy, dx))
    return (180.0 - a) % 360.0


def _runs_1d(hit: np.ndarray, ang: np.ndarray) -> list[tuple[float, float]]:
    """在 0~360 的环形数组上找连续 True 段，返回角度区间列表。"""
    n = len(hit)
    if not hit.any():
        return []
    if hit.all():
        return [(0.0, 360.0)]
    start = int(np.argmin(hit))
    m = np.roll(hit, -start)
    idx = np.flatnonzero(~m)
    out = []
    for k in range(len(idx) - 1):
        if idx[k + 1] - idx[k] > 1:
            s = (idx[k] + 1 + start) % n
            e = (idx[k + 1] - 1 + start) % n
            out.append((float(ang[s]), float(ang[e])))
    if idx[0] > 0:
        out.append((float(ang[(idx[-1] + 1 + start) % n]), float(ang[(idx[0] - 1 + start) % n])))
    out.sort(key=lambda se: (se[1] - se[0]) % 360.0, reverse=True)
    return out


def _hist(mask: np.ndarray, geom: Geometry, n_bins: int = 360,
          r_pad: float = 10.0) -> np.ndarray:
    """掩码在轨道环带内的 φ 直方图（1° 一格，只统计 φ∈[PHI_LO, PHI_HI]）。"""
    x0, y0, _, _ = ROI
    ys, xs = np.nonzero(mask)
    hist = np.zeros(n_bins, int)
    if len(xs) == 0:
        return hist
    xs = xs + x0
    ys = ys + y0
    r = np.hypot(xs - geom.cx, ys - geom.cy)
    keep = (r >= geom.r_in - r_pad) & (r <= geom.r_out + r_pad)
    xs, ys = xs[keep], ys[keep]
    if len(xs) == 0:
        return hist
    phi = phi_of(xs, ys, geom)
    phi = np.where(phi > 270.0, phi - 360.0, phi)
    phi = phi[(phi >= PHI_LO) & (phi <= PHI_HI)]
    if len(phi) == 0:
        return hist
    phi = np.where(phi < 0, phi + 360.0, phi)
    np.add.at(hist, (phi // (360.0 / n_bins)).astype(int) % n_bins, 1)
    return hist


def _norm(deg: float) -> float:
    """把 >180 的角度折到负数一侧，方便比较。"""
    return deg - 360.0 if deg > 180.0 else deg


def _span_from_hist(hist: np.ndarray, n_bins: int = 360,
                    min_px: int = 3) -> tuple[float, float]:
    ang = np.arange(n_bins) * (360.0 / n_bins)
    runs = _runs_1d(hist >= min_px, ang)
    if not runs:
        return (0.0, 0.0)
    s, e = runs[0]
    return (_norm(s), _norm(e))


def _len_from_zero(hist: np.ndarray, n_bins: int = 360,
                   min_px: int = 3, max_gap: int = 2) -> float:
    """从 φ=0 起、沿角度增长方向连续覆盖到多少度（黄条从左端开始长）。

    允许 max_gap 个空格的空隙，避免被图标抠出的洞截断。
    """
    return _len_from_flags(hist >= min_px, max_gap)


def to_hsv(img) -> np.ndarray:
    """PIL / RGB 数组 → HSV 数组（OpenCV 约定）。已经是 HSV 的数组直接传进来也行。"""
    return cv2.cvtColor(to_rgb(img), cv2.COLOR_RGB2HSV)


def _ring_samples(hsv: np.ndarray, geom: Geometry, r_band: tuple[float, float],
                  n_bins: int = 180):
    """按 φ 在半径带 [r0, r1] 上等距采样，返回 (H, S, V, ok)，形状都是 (n_bins, n_r)。"""
    phis = np.arange(n_bins, dtype=float)
    rs = np.arange(r_band[0], r_band[1] + 0.5, 2.0)
    P, R = np.meshgrid(phis, rs, indexing="ij")
    A = np.deg2rad(180.0 - P)
    X = np.rint(geom.cx + R * np.cos(A)).astype(int)
    Y = np.rint(geom.cy - R * np.sin(A)).astype(int)
    h, w = hsv.shape[:2]
    ok = (X >= 0) & (X < w) & (Y >= 0) & (Y < h)
    X = np.clip(X, 0, w - 1)
    Y = np.clip(Y, 0, h - 1)
    return (hsv[..., 0][Y, X].astype(np.float32),
            hsv[..., 1][Y, X].astype(np.float32),
            hsv[..., 2][Y, X].astype(np.float32), ok)


def _band_median_v(hsv: np.ndarray, geom: Geometry,
                   r_band: tuple[float, float] = BAND_R,
                   n_bins: int = 180) -> np.ndarray:
    _, _, V, ok = _ring_samples(hsv, geom, r_band, n_bins)
    V[~ok] = np.nan
    with np.errstate(all="ignore"):
        med = np.nanmedian(V, axis=1)
    return np.nan_to_num(med, nan=0.0)


def _prog_frac_profile(hsv: np.ndarray, geom: Geometry,
                       r_band: tuple[float, float] = PROG_R,
                       n_bins: int = 180) -> np.ndarray:
    H, S, V, ok = _ring_samples(hsv, geom, r_band, n_bins)
    hit = ((H >= PROG_HUE[0]) & (H <= PROG_HUE[1]) &
           (S >= PROG_S_MIN) & (V >= PROG_V_MIN) & ok)
    return hit.mean(axis=1)


def band_profile(img, geom: Geometry, n_bins: int = 180,
                 r_band: tuple[float, float] = BAND_R) -> np.ndarray:
    """轨道外带上每个 φ 的亮度中位数（V）。

    捕获区是「亮」的（V≈245），藏青底是「暗」的（V≈126），两者差得很开，
    用亮度判定比用色相稳（捕获区自身有从饱和青到淡蓝的渐变，
    色相/饱和度阈值只截到一部分，会导致蓝条位置在两端之间乱跳）。
    """
    return _band_median_v(to_hsv(img), geom, r_band, n_bins)


def prog_profile(img, geom: Geometry, n_bins: int = 180,
                 r_band: tuple[float, float] = PROG_R) -> np.ndarray:
    """进度环（转盘外那圈金色环）每个 φ 上「橙色采样点」的占比。

    这圈金色圆环就是进度条：从左端（φ=0）开始被点亮成橙色，点满一圈就钓上来。
    注意它和「捕获区」「藏青轨道」半径不同（在更内圈，r≈114），
    所以必须单独按这个半径采样，否则会串到别的东西上去。
    """
    return _prog_frac_profile(to_hsv(img), geom, r_band, n_bins)


def _len_from_flags(hit: np.ndarray, max_gap: int = 2) -> float:
    """从第 0 格起、「沿格号增大」连续覆盖到多少格（容忍 max_gap 个空格）。"""
    end = -1
    gap = 0
    for i in range(len(hit)):
        if hit[i]:
            end = i
            gap = 0
        else:
            gap += 1
            if gap > max_gap and end >= 0:
                break
    return 0.0 if end < 0 else float(end + 1)


def _span_from_flags(hit: np.ndarray, gap: float = ZONE_GAP,
                     lo: float = 0.0, step: float = 1.0) -> tuple[float, float]:
    """在「每个 φ 一个布尔」的序列上找最长连续段，允许 gap 度的空洞。

    空洞用来跨越压在轨道上的图标（鱼、宝箱），它们会把亮带掏出一块暗的。
    """
    idx = np.flatnonzero(hit)
    if len(idx) == 0:
        return (0.0, 0.0)
    groups: list[tuple[int, int]] = []
    s = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev <= gap:
            prev = i
        else:
            groups.append((s, prev))
            s = prev = i
    groups.append((s, prev))
    s, e = max(groups, key=lambda t: t[1] - t[0])
    return (lo + s * step, lo + e * step)


def default_geometry(shape: tuple[int, ...] | None = None) -> Geometry:
    """参考窗口下就是标定好的常量；窗口尺寸不同时按比例缩放。"""
    cx, cy, r_in, r_out = REF_GEOM
    if shape is not None and len(shape) >= 2:
        h, w = shape[0], shape[1]
        if w and h and (w, h) != REF_SIZE:
            s = min(w / REF_SIZE[0], h / REF_SIZE[1])
            cx, cy, r_in, r_out = cx * s, cy * s, r_in * s, r_out * s
    return Geometry(cx, cy, r_in, r_out)


def analyze(img, geom: Geometry | None = None, fit: bool = False) -> FishingState:
    rgb = to_rgb(img)
    m = masks(rgb)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    if geom is None:
        geom = fit_geometry(m) if fit else default_geometry(rgb.shape)
    if geom is None:
        return FishingState(present=False)

    navy_span = _span_from_hist(_hist(m["navy"], geom))

    track_mask = m["navy"] | m["zone"] | m["prog"]
    track_span = _span_from_hist(_hist(track_mask, geom))
    if track_span[1] - track_span[0] < 60:
        return FishingState(present=False, geom=geom, navy_span=navy_span)

    band = _band_median_v(hsv, geom)
    light = band >= ZONE_V
    if int((~light).sum()) < NAVY_MIN:
        return FishingState(present=False, geom=geom, navy_span=navy_span)
    zone_span = _span_from_flags(light)

    prog_len = _len_from_flags(_prog_frac_profile(hsv, geom) >= PROG_HIT)

    return FishingState(present=True, geom=geom, zone_span=zone_span,
                        prog_span=(0.0, prog_len), prog_frac=prog_len / TRACK_DEG,
                        navy_span=navy_span)


def analyze_file(path) -> FishingState:
    p = Path(path)
    if not p.is_absolute():
        p = ImagesDir / p
    if not p.exists():
        p = ImagesDir / Path(path).name
    raw = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(p)
    return analyze(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


if __name__ == "__main__":
    import sys

    for a in sys.argv[1:]:
        print(f"{Path(a).name}: {analyze_file(a)}")
