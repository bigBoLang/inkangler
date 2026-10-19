"""UI 元素坐标表（相对「墨迹大侠」窗口截图的像素坐标，原点 = 窗口左上角）。

坐标系说明
----------
`win.grab_window()` 抓的是窗口可见区域（含微信小程序的标题栏，约 48px 高）。
所以这里的坐标 = 截图里的像素坐标，点击时由 win.click_image() 换算成屏幕绝对坐标：
    screen = (frame.left + x, frame.top + y)

参考分辨率 REF_SIZE 是标定时窗口的尺寸。窗口被缩放的话所有坐标都会失效，
`assert_ref_size()` 会据此报警。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Element:
    key: str
    x: int
    y: int
    desc: str
    hit: int = 30  # 命中半径，用于「找色 / 校验」时判断


REF_SIZE = (552, 991)

# ---------------------------------------------------------------- 元素表
ELEMENTS: dict[str, Element] = {
    e.key: e for e in [
        Element("back",        62, 178, "返回箭头"),
        Element("title",      260, 168, "渔场标题(紫霞渔场)"),
        Element("level",      214, 213, "Lv.6"),
        Element("exp_bar",    295, 213, "经验/MAX 进度条"),
        Element("help",       526, 250, "帮助 ?"),
        Element("gold",       428, 168, "金币数量"),
        Element("bait_type",  428, 212, "鱼饵/渔力类型计数"),
        Element("power",      265, 283, "渔力 168898"),
        Element("power_info", 350, 283, "渔力 i 按钮"),

        # 左侧竖排
        Element("song_ling",   52, 320, "渔歌令"),
        Element("priv_card",   52, 437, "特权卡"),
        Element("contest",     52, 552, "钓鱼大赛"),
        Element("auto",        52, 730, "自动(齿轮) —— 自动钓鱼开关", hit=40),

        # 右侧竖排
        Element("shop",       502, 285, "渔具店"),
        Element("sell",       502, 375, "售卖 —— 卖鱼入口", hit=40),
        Element("pond",       512, 635, "鱼塘"),
        Element("bait",       472, 718, "鱼饵 —— 换饵/饵料", hit=40),
        Element("items",      470, 810, "道具"),
        Element("gear",       470, 898, "渔具 —— 鱼竿/渔具升级", hit=40),

        # 底部 / 中间
        Element("fishbox",     95, 890, "鱼箱 —— 查看鱼的数量", hit=40),
        Element("bait_count", 270, 810, "中间转盘上的鱼饵数量 x294"),

        # 顶部计数区（数值读取用 ROI 的中心）
        Element("gold_roi",   478, 168, "金币数字区域"),
        Element("bait_type_roi", 478, 212, "鱼饵类型数字区域"),
    ]
}


def get(key: str) -> Element:
    try:
        return ELEMENTS[key]
    except KeyError:
        raise KeyError(f"未知元素 {key!r}；可用: {sorted(ELEMENTS)}") from None


def assert_ref_size(size: tuple[int, int]) -> None:
    """窗口尺寸与标定时不一致就警告（截图会自动缩放，一般不用管）。"""
    if size != REF_SIZE:
        import sys
        if getattr(sys, "stdout", None):      # 打包成 windowed exe 时没有 stdout
            print(f"[警告] 窗口尺寸 {size} != 标定尺寸 {REF_SIZE}，"
                  f"截图会自动缩放回标定尺寸；只有做标定时才需要理会。")
