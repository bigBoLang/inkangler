"""在遛鱼轨道上找「鱼」图标。

鱼是贴在弧形轨道上的一张精灵图，会随所处角度旋转，用模板匹配又慢又容易
在转盘附近误命中。改用更稳的特征：**鱼身上有大块亮白**（眼白 + 白肚 + 白描边），
而轨道（藏青 / 亮青 / 橙）和宝箱（红金）都不是低饱和的高亮色。
于是在轨道环带内找「亮 + 低饱和」的连通块，最大的那块就是鱼。

返回的角度是这块白色（主要是鱼头部位）的 φ，鱼身中心比它略靠后几度，
控制时把目标角往前偏一点即可。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import arc

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# 暗色鱼身检测参数（实测标定）
S_MAX = 95       # 饱和度上限（藏青/亮青/橙都远高于此）
V_MAX = 175      # 亮度上限
AREA_MIN = 100   # 面积下限
ASPECT_MIN = 0.25
EYE_V = 205      # 眼白亮度
EYE_S = 60       # 眼白饱和度
EYE_MIN = 8      # 至少这么多眼白像素（轨道本身的暗边/橙条暗边都是 0）


@dataclass
class FishHit:
    phi: float
    r: float
    score: float
    x: int
    y: int
    area: int = 0
    eye: int = 0
    w: int = 0
    h: int = 0
    angle: float = 0.0
    flip: bool = False

    @property
    def center_phi(self) -> float:
        return self.phi


def to_rgb(img) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img
    return np.asarray(img.convert("RGB"))


def find_fish(img, geom: arc.Geometry | None = None,
              phi_lo: float = 0.5, phi_hi: float = 177.0,
              topk: int = 3, prev_phi: float | None = None, **kw) -> list[FishHit]:
    """在轨道上找鱼。

    判据：轨道环带内「低饱和 + 偏暗」的连通块（鱼身是灰橄榄色），
    且块内要有一小块很亮的低饱和像素（鱼的眼白）。轨道是藏青/亮青/橙，
    宝箱是红金，都不会同时满足这两条。
    """
    rgb = to_rgb(img)
    if geom is None:
        st = arc.analyze(rgb)
        if not st.geom:
            return []
        geom = st.geom

    x0, y0, x1, y1 = arc.ROI
    sub = rgb[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_RGB2HSV)
    s_max = kw.get("s_max", S_MAX)
    v_max = kw.get("v_max", V_MAX)
    area_min = kw.get("area_min", AREA_MIN)
    aspect_min = kw.get("aspect_min", ASPECT_MIN)
    eye_min = kw.get("eye_min", EYE_MIN)
    need_eye = kw.get("need_eye", True)

    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    body = ((S <= s_max) & (V <= v_max)).astype(np.uint8)
    ys, xs = np.nonzero(body)
    if len(xs) < area_min:
        return []
    X = xs + x0
    Y = ys + y0
    r = np.hypot(X - geom.cx, Y - geom.cy)
    phi = arc.phi_of(X.astype(float), Y.astype(float), geom)
    keep = (r >= geom.r_in - 2) & (r <= geom.r_out + 4) & \
           (phi >= phi_lo) & (phi <= phi_hi)
    if keep.sum() < area_min:
        return []
    m = np.zeros_like(body)
    m[ys[keep], xs[keep]] = 1

    # 眼白：亮 + 低饱和，且必须落在轨道环带之内（排除天空）
    yy = np.arange(y0, y1, dtype=np.float32)
    xx = np.arange(x0, x1, dtype=np.float32)
    RR = np.hypot(xx[None, :] - geom.cx, yy[:, None] - geom.cy)
    bright = ((V >= EYE_V) & (S <= EYE_S) & (RR >= geom.r_in) &
              (RR <= geom.r_out - 4)).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return []

    hits: list[FishHit] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < area_min:
            continue
        bx, by = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if min(bw, bh) / max(bw, bh) < aspect_min:
            continue          # 又细又长 → 是橙条的暗边，不是鱼
        eye = int(bright[by:by + bh, bx:bx + bw].sum())
        if need_eye and eye < eye_min:
            continue
        cx, cy = float(cent[i][0]) + x0, float(cent[i][1]) + y0
        hits.append(FishHit(
            phi=float(arc.phi_of(np.array([cx]), np.array([cy]), geom)[0]),
            r=float(np.hypot(cx - geom.cx, cy - geom.cy)),
            score=min(area / 300.0, 1.0), x=int(cx), y=int(cy),
            area=area, eye=eye, w=bw, h=bh))

    if prev_phi is not None and hits:
        def key(h: FishHit):
            d = abs((h.phi - prev_phi + 180.0) % 360.0 - 180.0)
            return (d > 50.0, -h.eye, -h.area)
        hits.sort(key=key)
    else:
        hits.sort(key=lambda h: (-h.eye, -h.area))
    return hits[:topk]


# ------------------------------------------------------------------ 模板匹配（备用）

def _rotate(tpl: np.ndarray, deg: float) -> np.ndarray:
    h, w = tpl.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2 - 0.5, h / 2 - 0.5), deg, 1.0)
    return cv2.warpAffine(tpl, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def save_template(src_path: Path, box: tuple[int, int, int, int], name: str) -> Path:
    """从截图里抠出鱼存成模板。box = (x, y, w, h)。"""
    raw = np.fromfile(str(src_path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(src_path)
    x, y, w, h = box
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMPLATES_DIR / f"{name}.png"
    cv2.imencode(".png", img[y:y + h, x:x + w])[1].tofile(str(out))
    return out


def find_fish_template(img, geom: arc.Geometry | None = None,
                       step: float = 15.0, min_score: float = 0.5,
                       r_pad: float = 10.0, topk: int = 1) -> list[FishHit]:
    """多角度模板匹配（慢，约 1.5s，仅作对照用）。"""
    rgb = to_rgb(img)
    if geom is None:
        st = arc.analyze(rgb)
        if not st.geom:
            return []
        geom = st.geom
    tpls = []
    for p in sorted(TEMPLATES_DIR.glob("fish*.png")):
        raw = np.fromfile(str(p), dtype=np.uint8)
        im = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if im is None:
            continue
        base = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
        for flip, v in ((False, base), (True, base[:, ::-1])):
            v = np.ascontiguousarray(v)
            for deg in np.arange(0.0, 360.0, step):
                tpls.append((_rotate(v, float(deg)), float(deg), flip))

    x0, y0, x1, y1 = arc.ROI
    sub = np.ascontiguousarray(rgb[y0:y1, x0:x1])
    sh, sw = sub.shape[:2]
    hits: list[FishHit] = []
    for tpl, ang, flip in tpls:
        th, tw = tpl.shape[:2]
        if th >= sh or tw >= sw:
            continue
        res = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
        cx = (np.arange(res.shape[1]) + tw / 2) + x0
        cy = (np.arange(res.shape[0]) + th / 2) + y0
        CX, CY = np.meshgrid(cx, cy)
        R = np.hypot(CX - geom.cx, CY - geom.cy)
        PHI = arc.phi_of(CX, CY, geom)
        bad = (R < geom.r_in - r_pad) | (R > geom.r_out + r_pad) | \
              (PHI < 1.0) | (PHI > 176.0)
        res = np.where(bad, -1.0, res)
        f = int(np.argmax(res))
        ry, rx = divmod(f, res.shape[1])
        sc = float(res[ry, rx])
        if sc < min_score:
            continue
        px, py = float(CX[ry, rx]), float(CY[ry, rx])
        hits.append(FishHit(phi=float(PHI[ry, rx]),
                            r=float(np.hypot(px - geom.cx, py - geom.cy)),
                            score=sc, x=int(px), y=int(py), angle=ang, flip=flip))
    hits.sort(key=lambda h: -h.score)
    return hits[:topk]
