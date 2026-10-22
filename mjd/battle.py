"""「挑战」模式的自动循环：选技能 → 看戏 → 结算 → 再来一局。

挑战是个幸存者式的小游戏：角色自己打，玩家唯一必须做的事是**每次升级时
从三张技能卡里挑一张**。实测一局约 8~9 分钟，中途每 30 秒左右弹一次选卡，
打完 90 波 BOSS 出「恭喜通关」结算，点一下关掉就回主界面。

这个模块只做三件事：
1. `detect(rgb)` —— 认屏。只认几种关键画面，认不出就什么都不做：
   * `skill`  选技能（深色底 + 大标题「获得技能」）
   * `result` 结算（「恭喜通关」或底部「点击任意区域关闭」）
   * `home`   主界面（底部「挑战」按钮）
   * `combat` 战斗/掉落选择（左上角暂停键还在）
   * `ad`     广告落地页（底部绿色「立即玩」）
2. `BattleBot.step()` —— 按当前画面做一个动作，返回日志字符串。
3. `BattleBot.run()` —— 主循环；卡住 45 秒没有任何进展就停下，不乱点。

为什么不用 OCR：这几个画面都有稳定的图块（标题、按钮），模板匹配一次
只要 3~5 ms，比 OCR 快两个数量级，也不怕字体/字号变化。

避开广告：结算页右下角有个「双倍奖励」看广告的按钮（约 295,933）。
本模块只点画面中部的 (276,600)，既满足「点击任意区域关闭」，又永远点不到它。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _gray(name: str) -> np.ndarray:
    img = cv2.imread(str(TEMPLATES / name), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"缺少模板 mjd/templates/{name}；先跑 tools/snap.py 抓图")
    return img


# 主界面「挑战」按钮（蓝色横幅 + 大字）
T_CHALLENGE = _gray("challenge_title.png")
# 选技能标题「获得技能」
T_SKILL = _gray("skill_title.png")
# 结算大标题「恭喜通关」
T_CLEAR = _gray("clear_title.png")
# 结算页底部小字「点击任意区域关闭」
T_CLICK_ANY = _gray("click_any.png")
# 战斗 HUD 左上角的暂停键，用来区分「在打」和「认不出」
T_PAUSE = _gray("battle_pause.png")

# 三个技能卡的中心（按 552x991 标定）
CARDS = ((150, 400), (283, 400), (450, 400))
CHALLENGE_BTN = (283, 780)     # 主界面「挑战」
RESULT_CLICK = (276, 600)      # 结算页中部，远离「双倍奖励」
AD_CLOSE = (501, 96)           # 广告落地页右上角「关闭」

TH = {"challenge": 0.78, "skill": 0.80, "clear": 0.80, "click_any": 0.86, "pause": 0.86}


def _match(gray: np.ndarray, tpl: np.ndarray) -> float:
    if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
        return 0.0
    return float(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED).max())


@dataclass
class Screen:
    name: str                       # skill / result / home / combat / ad / unknown
    scores: dict[str, float] = field(default_factory=dict)
    cleared: bool = False           # 只有 result 有意义：是不是「恭喜通关」

    @property
    def actionable(self) -> bool:
        return self.name in ("skill", "result", "home", "ad")


def detect(rgb: np.ndarray) -> Screen:
    """认屏。传入 RGB uint8，返回 Screen。"""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    s = {
        "challenge": _match(gray, T_CHALLENGE),
        "skill": _match(gray, T_SKILL),
        "clear": _match(gray, T_CLEAR),
        "click_any": _match(gray, T_CLICK_ANY),
        "pause": _match(gray, T_PAUSE),
    }
    # 广告落地页：底部整条绿色「立即玩」按钮（和游戏内任何界面都不像）
    roi = rgb[835:900, 25:530].reshape(-1, 3).astype(np.float32).mean(axis=0)
    ad = bool(roi[1] > 140 and roi[1] - roi[0] > 60 and roi[1] - roi[2] > 40)
    if ad:
        return Screen("ad", s)

    if s["clear"] >= TH["clear"] or s["click_any"] >= TH["click_any"]:
        return Screen("result", s, cleared=s["clear"] >= TH["clear"])
    if s["skill"] >= TH["skill"]:
        return Screen("skill", s)
    if s["challenge"] >= TH["challenge"]:
        return Screen("home", s)
    if s["pause"] >= TH["pause"]:
        return Screen("combat", s)
    return Screen("unknown", s)


@dataclass
class RunStats:
    rounds: int = 0          # 打完的局数
    cleared: int = 0         # 其中通关的
    skills: int = 0          # 选了多少次技能
    started: float = field(default_factory=time.time)
    elapsed: float = 0.0
    result: str = ""


class BattleBot:
    """把「截图 + 点击」两个函数塞进来就能跑（本地/远程都行）。"""

    def __init__(self, grab: Callable[[], np.ndarray],
                 click: Callable[[int, int], None],
                 rounds: int = 0, pick: int = 1,
                 log: Callable[[str], None] = print,
                 stop: Callable[[], bool] | None = None,
                 poll: float = 0.55,
                 result_gap: float = 1.6,
                 start_gap: float = 3.0,
                 idle_limit: float = 45.0):
        self.grab = grab
        self.click = click
        self.rounds = rounds          # 0 = 一直刷
        self.pick = max(0, min(2, pick))
        self.log = log
        self.stop = stop or (lambda: False)
        self.poll = poll
        self.result_gap = result_gap
        self.start_gap = start_gap
        self.idle_limit = idle_limit
        self.stats = RunStats()
        self._t_result = 0.0
        self._t_start = 0.0
        self._skill_i = self.pick
        self._in_result = False
        self._run_open = False
        self._last_name = ""
        self._last_seen = time.time()

    def _say(self, msg: str) -> None:
        self.log(msg)

    def step(self) -> str:
        img = self.grab()
        sc = detect(img)
        now = time.time()
        name = sc.name
        if name in ("skill", "result", "home", "combat", "ad"):
            self._last_seen = now

        if name == "skill":
            self._run_open = True
            self._in_result = False
            x, y = CARDS[self._skill_i % 3]
            self._skill_i += 1
            self.click(x, y)
            self.stats.skills += 1
            time.sleep(0.9)
            return f"选技能 #{self.stats.skills} → ({x},{y})"

        if name == "result":
            if not self._in_result:
                self._in_result = True
                self._run_open = False
                self.stats.rounds += 1
                if sc.cleared:
                    self.stats.cleared += 1
                tail = "通关" if sc.cleared else "结束"
                self._say(f"第 {self.stats.rounds} 局{tail}，关结算")
            if now - self._t_result < self.result_gap:
                time.sleep(self.poll)
                return ""
            self._t_result = now
            self.click(*RESULT_CLICK)
            return ""

        if name == "home":
            self._in_result = False
            if self._run_open:
                self._run_open = False
            if self.rounds and self.stats.rounds >= self.rounds:
                self.stats.result = "done"
                return "ROUNDS_DONE"
            if now - self._t_start < self.start_gap:
                time.sleep(self.poll)
                return ""
            self._t_start = now
            self.click(*CHALLENGE_BTN)
            self._run_open = True
            return "开始挑战"

        if name == "ad":
            self.click(*AD_CLOSE)
            self._t_result = 0.0
            return "关广告"

        if name == "combat":
            time.sleep(self.poll)
            return ""

        time.sleep(self.poll)
        return ""

    def run(self) -> RunStats:
        self._say("开始：主界面 / 挑战里都行，脚本会自己找节奏")
        while not self.stop():
            r = self.step()
            if r:
                self._say(r)
            if r == "ROUNDS_DONE":
                self._say(f"完成 {self.stats.rounds} 局（通关 {self.stats.cleared}），收工")
                break
            if self._last_name == "unknown" and time.time() - self._last_seen > self.idle_limit:
                self.stats.result = "stuck"
                self._say(f"{self.idle_limit:.0f} 秒认不出画面，停下（可能弹了公告/广告/断网）")
                break
        self.stats.elapsed = time.time() - self.stats.started
        return self.stats
