"""自动卖鱼。

流程（实机验证）
----------------
1. 主界面点左下「鱼箱」→ 弹出鱼箱面板（上面有「鱼箱容量 N/50」和「前往售卖」）。
2. 点「前往售卖」→ 进入售卖面板：4 列网格，每格一条鱼，点一下打勾选中。
3. 点底部「出售」→ 弹「恭喜获得」金币结算，点面板**外面**的暗区关掉
   （点面板本身不关，点 (276,600) 会命中「原价出售」按钮）。
4. 列表会刷新出还没卖的鱼，重复 2~3 直到网格里没鱼了。
5. 点右上角红叉关掉售卖面板，回到钓鱼界面。

网格坐标（552x991 窗口截图）
    列 x = 123 / 222 / 321 / 420
    行 y = 400 / 512 / 605（第三行被「出售可获得」横幅压住一半，仍可点）
"""
from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

from . import win

FISHBOX = (95, 890)      # 主界面左下角鱼箱
GO_SELL = (273, 792)     # 鱼箱面板里的「前往售卖」
SELL_CLOSE = (490, 232)  # 售卖面板右上角红叉
BOX_CLOSE = (489, 213)   # 鱼箱面板右上角红叉
SELL_BTN = (278, 806)    # 底部「出售」
BLANK = (276, 935)       # 关结算弹窗用（点面板外的暗区）

COLS = (123, 222, 321, 420)
ROWS = (400, 512, 605)

TILE_HALF = 30          # 每格取中心 60x60 判空
TILE_FRAC = 0.65        # 前两行：这个比例以上的像素不是米色底 → 有鱼
TILE_FRAC_LAST = 0.35   # 第三行被「出售可获得」横幅压住一半，阈值放低
EMPTY_ROWS = 2          # 从第几行开始算「被压住的行」


def tile_scores(img) -> list[float]:
    """12 个格位各自的「非米色底」占比。

    售卖面板的底色是米黄（H≈22, S≈85）；有鱼时格子会被刷成灰/蓝/紫/绿的底色，
    占比接近 1。空面板显示「暂无可售卖的鱼~」插图，占比接近 0。
    （早先只认「灰色方块」，于是彩色的鱼全被漏掉了。）
    """
    a = np.asarray(img.convert("RGB"))
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    H = hsv[..., 0].astype(np.int16)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)
    beige = (H > 12) & (H < 32) & (S > 55) & (S < 118) & (V > 150)
    out = []
    for y in ROWS:
        for x in COLS:
            p = beige[y - TILE_HALF:y + TILE_HALF, x - TILE_HALF:x + TILE_HALF]
            out.append(float(1.0 - p.mean()))
    return out


def count_tiles(img) -> int:
    """售卖网格里还剩几格鱼（按 12 个固定格位逐格判空）。"""
    scores = tile_scores(img)
    n = 0
    for i, v in enumerate(scores):
        n += 1 if v > (TILE_FRAC_LAST if i >= EMPTY_ROWS * len(COLS) else TILE_FRAC) else 0
    return n


PANEL_STRIP = (300, 340, 110, 460)   # 售卖面板「长按图标可查看属性详情」那一带


def sell_panel_open(img) -> bool:
    """售卖面板还开着吗。

    面板底是米黄（H≈22），钓鱼界面这一带是天空/海面的蓝色，两者分得很开。
    用来防止「面板不知什么时候关了，还傻乎乎按网格点」——那种情况下
    最后一击会点在转盘上，白白抛一竿。
    """
    a = np.asarray(img.convert("RGB"))
    y0, y1, x0, x1 = PANEL_STRIP
    hsv = cv2.cvtColor(a[y0:y1, x0:x1], cv2.COLOR_RGB2HSV)
    H = hsv[..., 0].astype(np.int16)
    S = hsv[..., 1].astype(np.int16)
    V = hsv[..., 2].astype(np.int16)
    beige = (H > 12) & (H < 32) & (S > 50) & (S < 120) & (V > 150)
    return float(beige.mean()) > 0.5


def go_sell_ready(img) -> bool:
    """鱼箱面板开好了没 —— 看「前往售卖」那个青绿色按钮在不在。

    主界面这个位置是转盘上沿（木色 H≈10），只有鱼箱面板才是青绿（H≈81）。
    没开好的话绝不能去点它，否则会点到转盘上、白白抛一竿。
    """
    a = np.asarray(img.convert("RGB"))
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    x, y = GO_SELL
    p = hsv[y - 10:y + 10, x - 10:x + 10].reshape(-1, 3)
    return 60.0 <= float(np.median(p[:, 0])) <= 110.0 and float(np.median(p[:, 1])) > 90.0


class Seller:
    def __init__(self, w, act: bool = True, log: Callable[[str], None] = print,
                 tap: float = 0.18, shot_dir=None):
        self.w = w
        self.pi = win.PostInput(w)
        self.act = act
        self.log = log
        self.tap = tap
        self.shot_dir = shot_dir
        self.in_box = False      # 鱼箱面板开着吗（close 只关自己开过的）
        self.in_sell = False     # 售卖面板开着吗

    def grab(self):
        return win.grab_window(self.w)

    def click(self, x: int, y: int, wait: float | None = None):
        if self.act:
            self.pi.click(x, y)
        time.sleep(self.tap if wait is None else wait)

    def open_sell(self) -> bool:
        """开鱼箱 → 前往售卖。返回售卖网格里有没有鱼。"""
        self.in_box = self.in_sell = False
        for _ in range(3):
            self.click(*FISHBOX, wait=1.1)
            img = self.grab()
            if self.shot_dir is not None:
                img.save(self.shot_dir / "box.png")
            if go_sell_ready(img):
                self.in_box = True
                break
        if not self.in_box:
            self.log("  鱼箱面板没打开，这轮不卖了")
            return False
        self.click(*GO_SELL, wait=1.3)
        img = self.grab()
        if self.shot_dir is not None:
            img.save(self.shot_dir / "list.png")
        self.in_sell = True
        return count_tiles(img) > 0

    def close(self) -> None:
        """关掉售卖面板（会退回鱼箱面板），再关掉鱼箱面板。"""
        if self.in_sell:
            self.click(*SELL_CLOSE, wait=0.9)
        if self.in_box:
            self.click(*BOX_CLOSE, wait=0.9)
        self.in_box = self.in_sell = False

    def sell_once(self) -> int:
        """选中当前页所有鱼并卖掉，返回点了几格。"""
        if not sell_panel_open(self.grab()):
            return 0
        n = 0
        for y in ROWS:
            for x in COLS:
                self.click(x, y)
                n += 1
        time.sleep(0.3)
        self.click(*SELL_BTN, wait=1.4)
        img = self.grab()
        # 结算弹窗：点空白处关掉，点两下保险
        for _ in range(3):
            if float(np.asarray(img.convert("L"), dtype=np.float32).mean()) > 110:
                break
            self.click(*BLANK, wait=0.7)
            img = self.grab()
        return n

    def run(self, max_batches: int = 8) -> int:
        """整轮卖鱼，返回卖了几批。"""
        if not self.open_sell():
            self.log("  售卖面板里没有鱼，直接关掉")
            self.close()
            return 0
        batches = 0
        for b in range(max_batches):
            img = self.grab()
            if not sell_panel_open(img):
                self.log("  售卖面板自己关掉了，停止")
                break
            left = count_tiles(img)
            if left == 0:
                break
            self.log(f"  第 {b + 1} 批：网格里还有 {left} 格")
            if self.sell_once() == 0:
                self.log("  售卖面板不在了，停止")
                break
            batches += 1
            if count_tiles(self.grab()) == 0:
                break
        self.close()
        img = self.grab()
        if self.shot_dir is not None:
            img.save(self.shot_dir / "after.png")
        return batches
