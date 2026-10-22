"""主界面红点巡检。

红点是右上角带白「!」的小圆标（H≈0~8，S/V 都很高）。这里只做**检测和命名**，
不点任何东西 —— 巡检本身绝对安全。

命名靠固定锚点：主界面的入口位置是固定的，红点离哪个锚点最近就叫什么。
认不出的红点原样列出坐标，不猜。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# (名字, x, y, 是否安全免费, 备注)
ANCHORS = (
    ("成就", 287, 187, True, "成就奖励"),
    ("邮件", 430, 187, False, "按你的要求：不碰"),
    ("好友", 480, 187, False, "社交，跳过"),
    ("图鉴", 530, 187, False, "只是新内容，没有领取"),
    ("活动", 61, 255, False, "消费送礼/每日充值，别点"),
    ("首充礼包", 61, 318, False, "充值"),
    ("再续侠旅", 61, 381, False, "充值/月卡"),
    ("食指大动", 61, 444, False, "活动，可能引导充值"),
    ("消费狂欢", 61, 514, False, "消费活动"),
    ("盖世绝伦团", 61, 570, False, "活动，可能引导充值"),
    ("六元礼包", 61, 631, False, "充值"),
    ("玉龙初征", 522, 270, False, "战令，可能含付费档"),
    ("福利中心", 522, 328, True, "签到等免费奖励"),
    ("江湖助手", 522, 391, True, "一键日常（0 叶子）"),
    ("伙伴", 503, 735, False, "限时伙伴，跳过"),
    ("游历", 87, 788, True, "挂机/游历收益"),
    ("江湖", 67, 912, False, "入口，不一定是奖励"),
    ("坊市", 145, 912, False, "只看不买"),
    ("大侠", 224, 912, False, "养成"),
    ("武学", 381, 912, False, "养成"),
    ("门派", 459, 912, False, "养成"),
    ("玩法", 538, 912, False, "入口，不一定是奖励"),
)

RADIUS = 32


@dataclass
class Dot:
    name: str
    x: int
    y: int
    area: int
    safe: bool
    note: str = ""
    known: bool = True


def _red_mask(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # 红点偏正红；橙色（H 10~20）是装饰，不要
    m = (((h <= 8) | (h >= 172)) & (s >= 150) & (v >= 130)).astype(np.uint8)
    return m


def scan(rgb: np.ndarray) -> list[Dot]:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = _red_mask(rgb)
    white = ((s <= 70) & (v >= 190)).astype(np.uint8)
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    dots: list[Dot] = []
    for i in range(1, n):
        x, y, w, hh, area = (int(v2) for v2 in stats[i])
        if area < 35 or area > 320:
            continue
        if w < 7 or hh < 7 or w > 30 or hh > 30:
            continue
        ar = w / max(hh, 1)
        if ar < 0.45 or ar > 2.4:
            continue
        # 红点中间有白色「!」或数字；红飘带/红色文字没有，借此过滤装饰
        if int(white[y:y + hh, x:x + w].sum()) < 5:
            continue
        cx, cy = int(cent[i][0]), int(cent[i][1])
        best = None
        bd = 1e9
        for name, ax, ay, safe, note in ANCHORS:
            d = (cx - ax) ** 2 + (cy - ay) ** 2
            if d < bd:
                bd, best = d, (name, safe, note)
        if best and bd <= RADIUS ** 2:
            dots.append(Dot(best[0], cx, cy, area, best[1], best[2]))
        else:
            dots.append(Dot("未识别", cx, cy, area, False, "位置不在已知入口", known=False))

    # 同一入口可能被拆成两三个碎块（白「!」把红圆切成两半），合并成最近的锚点
    merged: dict[str, Dot] = {}
    for d in dots:
        k = d.name
        if k not in merged or d.area > merged[k].area:
            merged[k] = d
    order = {a[0]: i for i, a in enumerate(ANCHORS)}
    return sorted(merged.values(), key=lambda d: order.get(d.name, 999))


def summary(dots: list[Dot]) -> str:
    if not dots:
        return "没有红点"
    safe = [d.name for d in dots if d.safe and d.name != "未识别"]
    rest = [d.name for d in dots if not d.safe and d.name != "未识别"]
    unk = [f"({d.x},{d.y})" for d in dots if d.name == "未识别"]
    parts = []
    if safe:
        parts.append("安全可领：" + "、".join(safe))
    if rest:
        parts.append("不自动点：" + "、".join(rest))
    if unk:
        parts.append("未识别：" + "、".join(unk))
    return "；".join(parts)
