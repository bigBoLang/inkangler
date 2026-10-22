"""每日免费日常：江湖助手「一键勾选 → 一键完成」。

流程（坐标按 552×991 截图空间标定）：
    主界面 → 江湖助手 → 一键勾选 → 一键完成
    → 确认弹窗（12000 铜钱，0 叶子）→ 确定
    → 执行完毕结算 → 点画面中部关闭
    → 关闭助手页 → 回主界面

安全边界：
  * 只走这一条固定路径，不点「购买设置」「高级助手 / 前往激活」；
  * 弹窗写死为「0 叶子」的这一种，确认键坐标 (420,640)；
  * 如果主界面认不出、或助手页没打开，就直接退出，不乱点。
"""
from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

from . import battle

# 552×991 截图坐标
HELPER_ENTRY = (500, 420)
CHECK_ALL = (130, 795)
RUN_ALL = (320, 795)
CONFIRM = (420, 640)          # 弹窗「确定」；实测命中区比视觉中心高约 13px
DIALOG_CLOSE = (498, 299)     # 弹窗右上角 X（确认没点上时的退路）
SUMMARY_CLICK = (276, 600)    # 结算页中部，避开任何广告/双倍按钮
PAGE_CLOSE = (498, 116)       # 助手页右上角 X

# 「执行完毕」标题模板：只在结算页命中
_T_DONE = cv2.imread(
    str(battle.TEMPLATES / "helper_done.png"), cv2.IMREAD_GRAYSCALE)


def dialog_open(rgb: np.ndarray) -> bool:
    """确认弹窗：左下角绿色「取消」按钮。"""
    r = rgb[610:680, 60:250]
    g = ((r[:, :, 1] > 120) & (r[:, :, 1] > r[:, :, 0] + 30)
         & (r[:, :, 1] > r[:, :, 2] + 20))
    return int(g.sum()) > 2000


def summary_open(rgb: np.ndarray) -> bool:
    """「执行完毕」结算页。"""
    if _T_DONE is None:
        return False
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if gray.shape[0] < _T_DONE.shape[0] or gray.shape[1] < _T_DONE.shape[1]:
        return False
    score = float(cv2.matchTemplate(gray, _T_DONE, cv2.TM_CCOEFF_NORMED).max())
    return score >= 0.72


class HelperSweep:
    def __init__(self, grab: Callable[[], np.ndarray], click: Callable[[int, int], None],
                 log: Callable[[str], None] = print,
                 stop: Callable[[], bool] = lambda: False,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.grab = grab
        self.click = click
        self.log = log
        self.stop = stop
        self.sleep = sleep

    def _home(self) -> bool:
        return battle.detect(self.grab()).name == "home"

    def run(self) -> dict:
        """返回 {'ok': bool, 'summary': bool, 'reason': str}。"""
        if self.stop():
            return {"ok": False, "summary": False, "reason": "已停止"}
        if not self._home():
            return {"ok": False, "summary": False, "reason": "不在主界面"}

        self.click(*HELPER_ENTRY)
        self.sleep(1.2)
        if self._home():
            # 入口偶尔会被飘字挡住，再点一次
            self.click(*HELPER_ENTRY)
            self.sleep(1.2)
        if self._home():
            return {"ok": False, "summary": False, "reason": "江湖助手没打开"}

        self.log("江湖助手：一键勾选")
        self.click(*CHECK_ALL)
        self.sleep(0.6)
        if self.stop():
            return {"ok": False, "summary": False, "reason": "已停止"}

        self.log("江湖助手：一键完成")
        self.click(*RUN_ALL)
        self.sleep(1.3)

        seen_dialog = dialog_open(self.grab())
        if seen_dialog:
            self.log("江湖助手：确认 12000 铜钱 / 0 叶子")
            self.click(*CONFIRM)
            self.sleep(2.8)
        else:
            self.log("江湖助手：没有确认弹窗（今天可能已经做完）")

        seen_summary = False
        for _ in range(6):
            if self.stop():
                break
            if summary_open(self.grab()):
                seen_summary = True
                break
            self.sleep(0.6)
        if seen_summary:
            self.log("江湖助手：执行完毕，收奖励")
            self.click(*SUMMARY_CLICK)
            self.sleep(1.0)
        elif seen_dialog:
            # 点过确定但没等到结算页：多等一下，仍没有就关页面
            self.sleep(2.0)
            seen_summary = summary_open(self.grab())
            if seen_summary:
                self.click(*SUMMARY_CLICK)
                self.sleep(1.0)

        # 确认键没点上时别把弹窗留在屏幕上
        if dialog_open(self.grab()):
            self.log("江湖助手：确认没点上，先关弹窗")
            self.click(*DIALOG_CLOSE)
            self.sleep(0.8)

        self.click(*PAGE_CLOSE)
        self.sleep(1.2)
        if not self._home():
            # 结算页可能还在，再点一下中部；然后重试关闭
            self.click(*SUMMARY_CLICK)
            self.sleep(0.8)
            self.click(*PAGE_CLOSE)
            self.sleep(1.2)

        if self._home():
            return {"ok": True, "summary": seen_summary, "reason": "完成"}
        return {"ok": False, "summary": seen_summary, "reason": "关不掉助手页，已停手"}
