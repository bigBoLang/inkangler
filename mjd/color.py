"""读「恭喜获得」弹窗里鱼的**颜色**。

颜色显示在哪
------------
弹窗中间那行鱼名（`金目鲈`、`温泉鱼`…）是彩色描边的粗体字，背后的光晕
也是同一个色系——两者就是这条鱼的「颜色/品质色」。实测 36 张历史弹窗
（`shots/dialogs/`）只有 6 类：

    红   黄(金)   紫   蓝   绿   灰(白)

光晕是渐变的、还会被鱼身挡住，读数不稳；鱼名是平涂的点阵字，取它的
填充色最稳。所以这里只认名字那一行：

    y 268~300，x 210~345（名字永远水平居中，3~4 个字都落在这个框里）

判据
----
1. 先数「有颜色的亮像素」（S≥60, V≥150）：
   * 少于 40 个 → 名字是白/灰色 → `gray`
   * 一个都没有 → 这一帧还没画完，返回 None，调用方过一帧再读
2. 有的话取色相（圆周均值，避免红/紫跨 0° 出错），按下面分段：

   红  H<10 或 H≥170      黄  10≤H<38
   绿  38≤H<85            蓝  85≤H<125
   紫  125≤H<170

注意 OpenCV 的 H 是 0~180（不是 0~360）。
"""
from __future__ import annotations

import cv2
import numpy as np

# 鱼名那一行（归一化到 552x991 的截图坐标）
NAME_BAND = (268, 300, 210, 345)

# 内部代号 -> 界面上的中文（按界面顺序：红、黄、紫、蓝、绿、灰）
TIERS = ("red", "yellow", "purple", "blue", "green", "gray")
NAME = {"red": "红", "yellow": "黄", "green": "绿",
        "blue": "蓝", "purple": "紫", "gray": "灰"}
ORDER = {t: i for i, t in enumerate(TIERS)}

MIN_COLOR_PX = 40      # 彩色像素少于这个数就认为名字是白/灰
MIN_WHITE_PX = 40      # 白字至少这么多亮像素，否则当成没画完

# 色相分段（OpenCV H: 0~180）
BINS = [
    (125, 170, "purple"),
    (85, 125, "blue"),
    (38, 85, "green"),
    (10, 38, "yellow"),
    (0, 10, "red"),
    (170, 180, "red"),
]


def rgb_of(img) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img
    return np.asarray(img.convert("RGB"))


def hue_to_tier(h: float) -> str:
    for a, b, name in BINS:
        if a <= h < b:
            return name
    return "gray"


def read_color(img) -> tuple[str | None, dict]:
    """返回 (颜色代号, 调试信息)。

    读不出来（弹窗还没画完 / 构图不对）时返回 (None, 信息)，调用方应当重试，
    不要当成灰色——灰色是一个合法的判断结果。
    """
    a = rgb_of(img)
    y0, y1, x0, x1 = NAME_BAND
    if a.shape[0] < y1 or a.shape[1] < x1:
        return None, {"why": "图太小"}

    band = a[y0:y1, x0:x1]
    hsv = cv2.cvtColor(band, cv2.COLOR_RGB2HSV)
    H = hsv[..., 0].astype(np.int16)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)

    colored = (S >= 60) & (V >= 150)
    white = (S < 45) & (V >= 200)
    n_col = int(colored.sum())
    n_white = int(white.sum())

    info = {"colored": n_col, "white": n_white}

    if n_col < MIN_COLOR_PX:
        if n_white >= MIN_WHITE_PX:
            info.update(tier="gray", hue=None, sat=None)
            return "gray", info
        info.update(why="名字还没画出来")
        return None, info

    hs = np.radians(H[colored].astype(np.float32) * 2.0)     # 0~180 -> 0~360°
    mean = float(np.degrees(np.arctan2(np.sin(hs).mean(), np.cos(hs).mean())) / 2.0) % 180.0
    sat = float(np.median(S[colored]))
    tier = hue_to_tier(mean)
    info.update(tier=tier, hue=mean, sat=sat)
    return tier, info


def describe(tier: str, info: dict | None = None) -> str:
    """日志里用的一句话。"""
    t = NAME.get(tier, tier or "?")
    if not info or info.get("hue") is None:
        return t
    return "%s(H%.0f S%.0f)" % (t, info["hue"], info.get("sat") or 0)
