"""从「恭喜获得」结算弹窗里读鱼的长度，并找到「原价出售」按钮。

为什么要自己认字
----------------
* 弹窗里那一行 `长度:62.8CM` 是固定点阵的金色小字，只有数字、`.`、`C`、`M`
  这些字形，而机器上又没装 OCR（tesseract / easyocr 都没有）。
* 所以按「切字 → 模板最近邻」来做：字高固定 ≈20px、字距固定、
  位置随文本居中而平移，但「C」「M」永远在行尾——用
  `(\\d+(?:\\.\\d+)?)CM` 把数字挑出来，前后多切出来的杂字自动忽略。
* 模板由 36 张历史弹窗截图（`shots/dialogs/d000~d035`）训练，
  留一验证 298/298 全对，见 `tools/lentrain.py`。

按钮定位
--------
「原价出售」是青绿色圆角按钮。颜色 (57,158,123) 很独特，
而橙色「前往鱼塘」、鱼身、金色文字都进不了这个阈值。
新纪录时会出现两个按钮，出售键在左边——取最左边的那个就对了。
"""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np

# 文字带：y 488~522 覆盖那一行字（行高 20，上下留余量）
BAND_Y0, BAND_Y1 = 488, 522
# x 放到 165：三位整数 + 一位小数（`102.5`）时整行最长，最左会到 210 附近
BAND_X0, BAND_X1 = 165, 390
MIN_COL_W = 2          # 列投影里至少这么宽的段才算一个字（滤掉噪点）
CW, CH = 16, 26        # 单字模板画布（宽, 高）

BANK = Path(__file__).with_name("digit_bank.npz")
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)CM")

# 「原价出售」按钮所在的横带（弹窗中部偏下）
SELL_BAND = (548, 636)
SELL_MIN_AREA = 2200
SELL_MIN_W, SELL_MIN_H = 70, 22


def rgb_of(img) -> np.ndarray:
    """PIL / ndarray 都接。"""
    if isinstance(img, np.ndarray):
        return img if img.ndim == 3 else np.stack([img] * 3, -1)
    return np.asarray(img.convert("RGB"))


def gold_mask(a: np.ndarray) -> np.ndarray:
    """金色小字（长度那一行）。"""
    r = a[..., 0].astype(np.int16)
    g = a[..., 1].astype(np.int16)
    b = a[..., 2].astype(np.int16)
    return ((r > 190) & (g > 130) & (b < 140) & (r - b > 55)).astype(np.uint8)


def col_runs(m: np.ndarray, min_w: int = MIN_COL_W) -> list[tuple[int, int]]:
    """按列投影切字，返回 [(x0, x1), ...]。"""
    cols = (m.sum(0) > 0).astype(np.int8)
    out: list[tuple[int, int]] = []
    cur = None
    for i, v in enumerate(cols):
        if v and cur is None:
            cur = i
        elif not v and cur is not None:
            if i - cur >= min_w:
                out.append((cur, i))
            cur = None
    if cur is not None and len(cols) - cur >= min_w:
        out.append((cur, len(cols)))
    return out


def glyph(m: np.ndarray, x0: int, x1: int) -> np.ndarray | None:
    """把一个字放进 CW×CH 画布，保留它在行内的上下位置（底对齐）。"""
    sub = m[:, x0:x1]
    ys = np.nonzero(sub.sum(1))[0]
    if not len(ys):
        return None
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    sub = sub[y0:y1]
    canvas = np.zeros((CH, CW), np.uint8)
    h = min(sub.shape[0], CH - min(y0, CH - 1))
    w = min(sub.shape[1], CW)
    if h <= 0 or w <= 0:
        return None
    canvas[y0:y0 + h, :w] = sub[:h, :w]
    return canvas


_bank_cache: dict | None = None


def load_bank(path: Path = BANK) -> dict[str, np.ndarray]:
    global _bank_cache
    if _bank_cache is None:
        d = np.load(path)
        _bank_cache = {k: d[k] for k in d.files}
    return _bank_cache


def classify(c: np.ndarray, bank: dict[str, np.ndarray]) -> tuple[str, float]:
    """最近邻（平均绝对差）。字形是固定点阵，同类之间差 <10，异类 >40。"""
    best_l, best_d = "", 1e9
    cc = c.astype(np.int16)
    for k, arr in bank.items():
        d = np.abs(arr.astype(np.int16) - cc[None]).mean(axis=(1, 2))
        i = int(d.argmin())
        if float(d[i]) < best_d:
            best_l, best_d = k, float(d[i])
    return best_l, best_d


def read_length_detail(img) -> tuple[float | None, str, float]:
    """返回 (长度, 认出来的整行字, 最差单字距离)。认不出返回 (None, 字串, 距离)。"""
    a = rgb_of(img)
    if a.shape[0] < BAND_Y1 or a.shape[1] < BAND_X1:
        return None, "", 99.0
    band = a[BAND_Y0:BAND_Y1, BAND_X0:BAND_X1]
    m = gold_mask(band)
    if m.sum() < 20:
        return None, "", 99.0
    bank = load_bank()
    text = ""
    worst = 0.0
    for x0, x1 in col_runs(m):
        g = glyph(m, x0, x1)
        if g is None:
            continue
        lab, dist = classify(g, bank)
        text += lab
        worst = max(worst, dist)
    mt = NUM_RE.search(text)
    if not mt:
        return None, text, worst
    try:
        return float(mt.group(1)), text, worst
    except ValueError:
        return None, text, worst


def read_length(img) -> float | None:
    return read_length_detail(img)[0]


def find_sell_btn(img) -> tuple[int, int] | None:
    """找「原价出售」按钮中心；找不到返回 None（可能还在播新纪录动画）。"""
    a = rgb_of(img)
    if a.shape[0] < SELL_BAND[1]:
        return None
    y0, y1 = SELL_BAND
    band = a[y0:y1].astype(np.int16)
    r, g, b = band[..., 0], band[..., 1], band[..., 2]
    m = ((g > 105) & (g - r > 40) & (g - b > 20)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for k in range(1, n):
        x, y, w, h, ar = (int(v) for v in st[k])
        if ar < SELL_MIN_AREA or w < SELL_MIN_W or h < SELL_MIN_H:
            continue
        if best is None or x < best[0]:
            best = (x, y, w, h)
    if best is None:
        return None
    x, y, w, h = best
    return (x + w // 2, y + y0 + h // 2)
