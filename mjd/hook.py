"""遛鱼小游戏的闭环控制器。

机制（实测标定）
----------------
* 点住转盘中心约 1.5 秒再松开 = 抛竿；咬钩时间不定（1~15 秒），之后
  转盘外圈出现弧形轨道。
* 轨道上的元素（都在转盘外圈同一条半径带上）：
    - 蓝色「捕获区」：一段约 68° 的亮带。按住左键 → 以约 +100°/s 向右
      （φ 增大）移动；松开 → 以约 −140°/s 向左移动。两端会被轨道长度截断，
      所以它的中心只能落在 φ≈35~145 之间。
    - 金色「进度环」：转盘外面那圈金环（r≈114，比蓝条内一档），从 φ=0 起
      向右被点亮成橙色。鱼被蓝条罩住时以约 +23°/s 增长，没罩住时回落；
      点满一圈鱼就上钩，随后弹结算窗。
    - 「鱼」图标贴在轨道上，自己来回游：向右约 +23°/s，向左约 −35°/s。
* 目标：让蓝条一直罩住鱼，把黄条喂满。

控制
----
误差 e = φ_鱼 − φ_蓝条中心。按住平均 +100°/s、松开 −140°/s，所以要让蓝条
保持不动的占空比是 140/240 ≈ 58%。这里用「期望速度 → 占空比」的连续控制：
    v = clip(kp·e, −130, +90)
    p_hold = (v + 140) / 240
再用一个累加器把它变成逐帧的「按住 / 松开」，避免蓝条在两个极端之间来回撞。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import arc, color, fish, length, ui, win

DIAL = (int(ui.ELEMENTS["bait_count"].x), int(ui.ELEMENTS["bait_count"].y) + 10)
DIM = 128.0          # 亮度低于此值说明有弹窗
DIM_ROI = (300, 940, 20, 530)

# 结算弹窗判别：「恭喜获得」标题后面是暖金色光晕，「鱼溜走了」后面是蓝光。
# 取标题那一带，比较 R 与 B 的平均值，两者差 +50 与 -55，分得极开。
RESULT_ROI = (225, 265, 195, 360)
RESULT_RB = -10.0

# 关弹窗用的点击点。注意：弹窗面板本身（约 y 190~690）点它是不关的，
# 得点面板外面的暗色区域。这个点在弹窗打开时一定落在暗区，
# 而在正常钓鱼界面里只是转盘下面的木板，点了无害。
DIALOG_CLICK = (276, 935)

HOLD_V = 100.0       # 按住时蓝条速度 °/s
REL_V = -140.0       # 松开时蓝条速度 °/s
KP = 4.0             # 误差 → 期望速度 的比例
V_LO, V_HI = -130.0, 90.0

# ---- 抛竿力度
# 按住转盘中心时，转盘上方会出现一条大圆弧蓄力条（蓝→金），实测：
#   * 约 0.3s 开始出现金色，之后基本线性增长；
#   * 1.68s 左右涨满并保持到约 3.1s，再按久反而回落（泄力）。
# 所以「满力」= 1.7s，十档 = level/10 × 满力；太短的按住可能不算抛竿，
# 统一兜底到 0.22s。用户可调满力值（UI 里的「满力时长」）。
CAST_MAX = 1.70      # 蓄力条涨满所需的按住时长（秒）
CAST_MIN = 0.22      # 兜底的最短按住时长
CAST_LEVEL = 10      # 默认档位（鱼币模式用满力）
# 松手之后那条蓄力弧还会停留约 0.3s（蓝弧还亮着、金色部分≈刚才的蓄力比例）。
# 轨道识别会把蓝弧当成「轨道」、把金色部分当成「进度」，于是机器人以为小游戏
# 已经开始了，空打两三帧判「鱼跑了」——而且这时候真正的鱼还没咬钩。
# 所以抛竿后先静默这段时间，并且要求连续两帧都看到轨道才算真的开始。
CAST_GRACE = 0.50

# ---- 两种模式
# feed : 喂鱼 —— 只在「长度 > 阈值」时点「原价出售」，小的留进鱼箱
# coins: 鱼币 —— 不看长度，一律直接卖
MODE_FEED = "feed"
MODE_COINS = "coins"
MIN_LEN = 65.0       # 喂鱼模式的长度阈值（cm）
SELL_WAIT_MAX = 12.0  # 结算窗里最多花多久去点「原价出售」
# 公告横幅偶尔会整条盖住「原价出售」。这时按已知坐标盲点一次（单按钮在正中，
# 新纪录双按钮时在左半边），点完再确认画面确实回到钓鱼主界面。
BLIND_BTN = ((280, 591), (189, 591))


def decide(mode: str, val: float | None, tier: str | None,
           sell_colors: set[str], min_len: float) -> tuple[bool, str]:
    """结算时「卖」还是「留」。纯函数，方便单独测。

    优先级：鱼币模式（全卖） > 勾选的颜色（直接卖） > 长度阈值。
    颜色没读出来（tier=None）就退回长度规则，不会因为认不出颜色就乱卖。
    """
    if mode == MODE_COINS:
        return True, "鱼币模式：全卖"
    if tier and tier in sell_colors:
        return True, "颜色 %s（勾了「直接卖」）" % color.NAME.get(tier, tier)
    if val is None:
        return False, "长度没认出来，先留进鱼箱"
    if val > min_len:
        return True, "> %.0f" % min_len
    return False, "≤ %.0f" % min_len

# ---- 前馈（把鱼的「规律」用起来）
# 实测：鱼的角速度基本恒定（右 ~+25°/s、左 ~-35°/s，快的到 ±47），到轨道两端才折返。
# 纯比例控制稳态误差 = v鱼/kp ≈ 5~12°，再加上一帧取图/下发的延迟（v鱼×0.15s）
# 实际滞后 15~20°；而捕获区宽度有时只有 30~50°（半宽 15~25°），鱼就一直在区边
# 蹭进蹭出，进度涨不起来（深度越浅涨得越慢：贴边 +9%/s，居中 +17%/s）。
# 所以把估出来的鱼速直接前馈进去，再按延迟把瞄准点往前推一点：
#     u = v鱼 + kp·(鱼 + v鱼·T_LEAD − 蓝条中心)
# 稳态时蓝条中心就压着「鱼 + v鱼·T_LEAD」，正好补掉延迟。
T_LEAD = 0.15        # 前馈提前量（秒），约等于一帧的取图+处理+下发延迟
FF_WINDOW = 0.35     # 估鱼速用的时间窗（秒）；一折返，窗口内均值自然过零，不会暴冲
FF_MAX = 70.0        # 前馈速度上限 °/s（防止误检把蓝条甩飞）
FF_LOST = 0.45       # 连续这么久看不到鱼，就把前馈清零

# 跳变误检剔除：**默认关**。实测「相邻两帧鱼跳 40°」几乎都不是误检，而是
# 机器被别的程序挤满时游戏自己掉帧 / 我们这一帧卡了一两秒——跳的幅度正是鱼在
# 那段真实时间里游出来的。此时把观测丢掉反而跟丢。留个开关方便以后在
# 「画面干净、帧率稳定」的场合再验证。
JUMP_GUARD = False
JUMP_BASE = 32.0     # °，基础容差
JUMP_RATE = 60.0     # 每多 1 秒帧间隔，多给这么多度
JUMP_MAX = 130.0     # °，封顶
PRED_KEEP = 0.7      # 丢了鱼之后，最多靠外推顶这么久（秒）


class FishTracker:
    """在线估计鱼的角速度（最小二乘斜率），给控制器做前馈；顺带剔除跳变误检。

    鱼一段直游时速度很稳，所以窗口取 0.55s 足够滤掉 ±1° 的量化噪声；
    折返时窗口里前后两段正负抵消，斜率平滑过零，正好当作「先别动」。

    另外鱼「匀速直线 ~3s 才折返」意味着：偶尔一两帧看不到鱼时，
    用上次位置 + 速度外推出来的落点比「松开不管」准得多，所以提供了 predict()。
    """

    def __init__(self, window: float = FF_WINDOW, v_max: float = FF_MAX,
                 lost: float = FF_LOST, pred_keep: float = PRED_KEEP,
                 jump_guard: bool = JUMP_GUARD):
        self.window = window
        self.v_max = v_max
        self.lost = lost
        self.pred_keep = pred_keep
        self.jump_guard = jump_guard
        self.hist: deque[tuple[float, float]] = deque()
        self.v = 0.0
        self.t_seen = 0.0
        self.phi: float | None = None      # 最近一次被采信的鱼位置
        self.t_phi = 0.0

    def accept(self, t: float, phi: float | None) -> bool:
        """这个观测像真的吗？离上一次采信的位置太远就当成误检丢掉（默认关）。"""
        if phi is None:
            return False
        if not self.jump_guard:
            return True
        if self.phi is None or t - self.t_phi > self.pred_keep:
            return True
        tol = min(JUMP_MAX, JUMP_BASE + JUMP_RATE * max(0.0, t - self.t_phi))
        return abs(phi - self.phi) <= tol

    def update(self, t: float, phi: float | None) -> float:
        if phi is None:
            if self.t_seen and t - self.t_seen > self.lost:
                self.hist.clear()
                self.v = 0.0
            return self.v
        self.t_seen = t
        self.phi, self.t_phi = phi, t
        self.hist.append((t, phi))
        while self.hist and t - self.hist[0][0] > self.window:
            self.hist.popleft()
        if len(self.hist) >= 3:
            ts = np.array([h[0] for h in self.hist])
            ps = np.array([h[1] for h in self.hist])
            ts = ts - ts.mean()
            den = float((ts * ts).sum())
            if den > 1e-6:
                v = float((ts * (ps - ps.mean())).sum() / den)
                self.v = float(np.clip(v, -self.v_max, self.v_max))
        return self.v

    def predict(self, t: float) -> float | None:
        """看不到鱼时，用「上次位置 + 匀速外推」顶上；太久没见就放弃。"""
        if self.phi is None or t - self.t_phi > self.pred_keep:
            return None
        return float(np.clip(self.phi + self.v * (t - self.t_phi), 0.0, 176.0))

    def reset(self) -> None:
        self.hist.clear()
        self.v = 0.0
        self.t_seen = 0.0
        self.phi = None
        self.t_phi = 0.0


def brightness(img) -> float:
    a = np.asarray(img.convert("L"), dtype=np.float32)
    return float(a[DIM_ROI[0]:DIM_ROI[1], DIM_ROI[2]:DIM_ROI[3]].mean())


# 钓鱼主界面判别：转盘下方那块木头（没有文字，颜色很稳定，实测 (95,66,48)）。
# 用它区分「主界面 / 小游戏」和「跑到别的页面去了」——用比值而不是绝对亮度，
# 这样白天/黄昏场景整体亮度变化也不影响。
MAIN_ROI = (860, 880, 250, 290)


def main_screen(img) -> bool:
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    y0, y1, x0, x1 = MAIN_ROI
    p = a[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0)
    r, g, b = float(p[0]), float(p[1]), float(p[2])
    return (r > 60 and 1.25 < r / max(g, 1.0) < 1.65
            and 1.2 < g / max(b, 1.0) < 1.6 and r - b > 25)


def classify_dialog(img) -> str:
    """分辨结算弹窗是「恭喜获得」还是「鱼溜走了」。"""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    y0, y1, x0, x1 = RESULT_ROI
    r = a[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0)
    return "caught" if float(r[0] - r[2]) > RESULT_RB else "escaped"


@dataclass
class Observation:
    t: float
    present: bool
    bright: float
    zone: tuple[float, float] = (0.0, 0.0)
    prog: float = 0.0
    fish: float | None = None
    eye: int = 0
    geom: arc.Geometry | None = None

    @property
    def zc(self) -> float:
        return (self.zone[0] + self.zone[1]) / 2.0

    @property
    def zlen(self) -> float:
        return self.zone[1] - self.zone[0]

    @property
    def covered(self) -> bool:
        return self.fish is not None and self.zone[0] <= self.fish <= self.zone[1]


@dataclass
class Attempt:
    n: int = 0
    samples: list[Observation] = field(default_factory=list)
    result: str = ""          # 'caught' | 'escaped' | 'gone' | 'no-minigame' | 'timeout'
    note: str = ""
    length: float | None = None   # 结算窗里读到的鱼长（cm）
    action: str = ""              # 'sold' | 'kept' | 'unknown'


class HookBot:
    def __init__(self, act: bool = True, kp: float = KP, cast_secs: float | None = None,
                 log: Callable[[str], None] = print, keep_frames: bool = False,
                 max_secs: float = 60.0, dialog_dir=None, lead: float = T_LEAD,
                 feedforward: bool = True, jump_guard: bool = JUMP_GUARD,
                 cast_level: int = CAST_LEVEL, cast_max: float = CAST_MAX,
                 mode: str = MODE_COINS, min_len: float = MIN_LEN,
                 sell_colors: set[str] | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 on_event: Callable[[dict], None] | None = None):
        self.w = win.find_window("墨迹大侠")
        self.pi = win.PostInput(self.w)
        self.act = act
        if act:
            # 权限不够的话（微信以管理员身份运行），后面点哪儿都没反应，
            # 不如在最开始就说清楚。
            reason = win.input_blocked(self.w)
            if reason:
                raise RuntimeError(reason)
        self.kp = kp
        # 抛竿：给了 cast_secs 就按秒数（老用法），否则按档位算
        self.cast_max = float(cast_max)
        self.cast_level = int(max(1, min(10, cast_level)))
        self.cast_secs = (float(cast_secs) if cast_secs
                          else max(CAST_MIN, self.cast_max * self.cast_level / 10.0))
        self.mode = mode
        self.min_len = float(min_len)
        # 勾选的颜色：钓上来直接卖，不看长度；没勾的仍按长度判断。
        # 只在喂鱼模式生效，鱼币模式本来就全卖。
        self.sell_colors = set(sell_colors or ())
        self.should_stop = should_stop
        self.on_event = on_event
        self.max_secs = max_secs
        self.dialog_dir = dialog_dir
        self.dialog_n = 0
        if dialog_dir is not None and Path(dialog_dir).exists():
            nums = [int(p.stem[1:]) for p in Path(dialog_dir).glob("d*.png")
                    if p.stem[1:].isdigit()]
            self.dialog_n = (max(nums) + 1) if nums else 0
        self.log = log
        self.keep_frames = keep_frames
        self.down = False
        self.acc = 0.0
        self.frames: list = []
        self.lead = lead
        self.feedforward = feedforward
        self.trk = FishTracker(jump_guard=jump_guard)
        # 统计：跟得准不准（|误差| 均值）与覆盖深度均值，用来量化效果
        self.stat_n = 0
        self.stat_abs_e = 0.0
        self.stat_depth = 0.0
        self.stat_lag = 0.0
        self.stat_rej = 0     # 这一轮被当误检丢掉的帧
        self.stat_pred = 0    # 这一轮靠外推顶上的帧
        # 耗时统计：看循环到底卡在哪个阶段（机器被别的程序占满时最有用）
        self.t_grab: list[float] = []
        self.t_ana: list[float] = []
        self.t_find: list[float] = []
        self.t_gap: list[float] = []
        self.t_last = 0.0

    # ---------------------------------------------------------------- 基础
    def grab(self):
        img = win.grab_window(self.w)
        note = win.capture_note()
        if note:
            self.log("  " + note)
        return img

    def observe(self, prev_phi: float | None = None) -> tuple[Observation, object]:
        t0 = time.perf_counter()
        img = self.grab()
        t1 = time.perf_counter()
        t = time.time()
        st = arc.analyze(img)
        t2 = time.perf_counter()
        o = Observation(t=t, present=st.present, bright=brightness(img), geom=st.geom)
        if st.present:
            o.zone = st.zone_span
            o.prog = st.prog_frac
            fh = fish.find_fish(img, st.geom, prev_phi=prev_phi, topk=1)
            if fh:
                o.fish = fh[0].phi
                o.eye = fh[0].eye
        t3 = time.perf_counter()
        self.t_grab.append((t1 - t0) * 1000)
        self.t_ana.append((t2 - t1) * 1000)
        self.t_find.append((t3 - t2) * 1000)
        return o, img

    def press(self):
        if self.act and not self.down:
            self.pi.down(*DIAL)
            self.down = True

    def release(self):
        if self.act and self.down:
            self.pi.up(*DIAL)
            self.down = False

    def sync_up(self):
        """不管我们以为按没按住，都补发一次「松开」。

        按住状态只存在我们这个进程里：万一上一竿在按住时就结束了、或者中途被
        打断，脚本以为「按着」而游戏以为「松开了」（或反过来），蓝条就会不听话。
        每竿开始时无条件下发一次抬起，保证两边状态一致——单独一个抬起消息
        不会被当成点击（点击必须是按下+抬起）。
        """
        if self.act:
            self.pi.up(*DIAL)
        self.down = False

    def click(self, x: int, y: int):
        if self.act:
            self.pi.click(x, y)
        else:
            time.sleep(0.05)

    def _stopping(self) -> bool:
        """UI 上点了「停止」吗。每轮遛鱼的循环里都会看一眼，能马上松手退出。"""
        return bool(self.should_stop and self.should_stop())

    def fps(self, n: int = 60) -> float:
        """最近 n 帧的中位帧率（UI 上实时显示，低于 8fps 说明机器被占满了）。"""
        if not self.t_gap:
            return 0.0
        med = float(np.median(self.t_gap[-n:]))
        return 1000.0 / max(med, 0.01)

    # ---------------------------------------------------------------- 状态判定
    def settle_dialogs(self, tries: int = 5) -> int:
        """关掉当前弹窗，返回关掉几个。"""
        n = 0
        for _ in range(tries):
            img = self.grab()
            if brightness(img) >= DIM:
                break
            self.click(*DIALOG_CLICK)
            n += 1
            time.sleep(0.5)
        return n

    def cast(self) -> None:
        self.pi.down(*DIAL)
        self.down = True
        time.sleep(self.cast_secs)
        self.pi.up(*DIAL)
        self.down = False

    def wait_minigame(self, timeout: float = 16.0, verbose: bool = True,
                      grace: float = 0.0) -> Observation | None:
        """等轨道出现。咬钩时间 1~15s 都可能，所以等久一点。

        `grace`：抛竿后先忽略这么多秒——刚松手时那条蓄力弧还亮着，
        蓝弧会被当成「轨道」、金色部分被当成「进度」，于是机器人以为小游戏
        已经开始、空打几帧就判「鱼跑了」，而真正的鱼还没咬钩。
        另外要求连续两帧都识别到轨道才返回，避免一闪而过的残影。
        """
        t0 = time.time()
        last = 0.0
        gate = 0
        while time.time() - t0 < timeout:
            if self._stopping():
                return None
            o, img = self.observe()
            if o.bright < DIM:
                return None            # 出弹窗了（鱼箱满 / 没饵）
            if time.time() - t0 < grace:
                time.sleep(0.05)
                continue
            if o.present and o.zone[1] > o.zone[0] + 25:
                gate += 1
                if gate >= 2:
                    return o
            else:
                gate = 0
            el = time.time() - t0
            if verbose and el - last >= 2.0:
                last = el
                self.log("  ...等咬钩 %4.1fs bright=%5.1f present=%s" % (el, o.bright, o.present))
            time.sleep(0.05)
        return None

    # ---------------------------------------------------------------- 主循环
    def fight(self, max_secs: float = 60.0, verbose: bool = True) -> Attempt:
        at = Attempt()
        t0 = time.time()
        prev_phi: float | None = None
        gone = 0
        full = 0
        miss = 0
        best = 0.0
        best_t = 0.0
        self.acc = 0.5
        self.t_last = 0.0
        while time.time() - t0 < max_secs:
            if self._stopping():
                at.result = "stopped"
                break
            t_loop = time.perf_counter()
            o, img = self.observe(prev_phi)
            if self.t_last:
                self.t_gap.append((t_loop - self.t_last) * 1000)
            self.t_last = t_loop
            at.samples.append(o)
            if self.keep_frames:
                self.frames.append(img)

            # 先剔除跳变误检：明显不合理的观测当没看见，用外推顶着
            raw = o.fish if (o.present and o.fish is not None) else None
            if raw is not None and not self.trk.accept(o.t, raw):
                raw = None
                self.stat_rej += 1
            if raw is not None:
                prev_phi = raw
            v_ff = self.trk.update(o.t, raw)
            tgt = raw if raw is not None else self.trk.predict(o.t)
            pred = raw is None and tgt is not None
            if pred:
                self.stat_pred += 1

            zone_ok = o.present and o.zlen > 25 and tgt is not None
            if zone_ok:
                if raw is not None:
                    miss = 0
                # 瞄准点 = 鱼 + 提前量；期望速度 = 鱼速(前馈) + 比例修正
                aim = tgt + (v_ff * self.lead if self.feedforward else 0.0)
                e = aim - o.zc
                base = v_ff if self.feedforward else 0.0
                v = max(V_LO, min(V_HI, base + self.kp * e))
                self.acc += (v - REL_V) / (HOLD_V - REL_V)
                if self.acc >= 1.0:
                    self.acc -= 1.0
                    want = True
                else:
                    want = False
                if want:
                    self.press()
                else:
                    self.release()
                if raw is not None:
                    half = max(o.zlen / 2.0, 1.0)
                    self.stat_n += 1
                    self.stat_abs_e += abs(o.fish - o.zc)
                    self.stat_lag += (o.fish - o.zc)
                    self.stat_depth += max(0.0, 1.0 - abs(o.fish - o.zc) / half)
            elif o.present:
                # 真的找不到鱼（连外推都过期了）：保持当前动作，连着好几帧才松开，
                # 免得「按着不动」把蓝条顶死在某一端。
                miss += 1
                if miss >= 5:
                    self.release()

            if o.present:
                if o.prog > best + 0.02:
                    best, best_t = o.prog, time.time() - t0
                full = full + 1 if o.prog >= arc.PROG_FULL else 0

            if verbose:
                self.log("  %5.2fs zone=%3.0f~%3.0f zc=%3.0f prog=%4.1f%% fish=%s e=%s v=%s %s%s%s" % (
                    time.time() - t0, o.zone[0], o.zone[1], o.zc, o.prog * 100,
                    ("%3.0f" % o.fish) if (o.fish is not None and not pred and raw is not None) else " --",
                    ("%+4.0f" % (tgt - o.zc)) if zone_ok else "  --",
                    ("%+4.0f" % v_ff) if zone_ok else "  --",
                    "HOLD" if self.down else "reel",
                    " PRED" if pred else "",
                    (" FULL" if full >= 2 else "") + (" IN" if o.covered else "")))

            if o.bright < DIM:
                at.result = "dialog"
                break
            if not o.present:
                gone += 1
                if gone >= 3:
                    at.result = "gone"
                    break
                continue
            gone = 0
            # 进度长时间没有推进，说明卡住了（鱼一直罩不住），放弃重来
            if time.time() - t0 - best_t > 30.0:
                at.result = "stall"
                break
        self.release()
        if not at.result:
            at.result = "timeout"
        return at

    def finish(self, wait: float = 4.0) -> str:
        """等结算弹窗出现，并把弹窗截下来存盘。返回 'caught'/'escaped'/'clear'。"""
        t0 = time.time()
        dark = 0
        while time.time() - t0 < wait:
            img = self.grab()
            if brightness(img) < DIM:
                dark += 1
                if dark >= 2:
                    if self.dialog_dir is not None:
                        self.dialog_dir.mkdir(parents=True, exist_ok=True)
                        img.save(self.dialog_dir / f"d{self.dialog_n:03d}.png")
                        self.dialog_n += 1
                    return classify_dialog(img)
            time.sleep(0.1)
        return "clear"

    def dismiss(self, tries: int = 5) -> int:
        n = 0
        for _ in range(tries):
            img = self.grab()
            if brightness(img) >= DIM:
                return n
            self.click(*DIALOG_CLICK)
            n += 1
            time.sleep(0.55)
        return n

    # ---------------------------------------------------------------- 结算窗
    def handle_catch(self, verbose: bool = True) -> tuple[float | None, str]:
        """结算窗还开着：读长度和颜色，决定「直接卖掉」还是「留进鱼箱」。

        返回 (长度, 动作)，动作 = 'sold' / 'kept' / 'unknown'。
        卖：点青绿色的「原价出售」（新纪录时它左边那个，右边橙色的「前往鱼塘」别碰）。
        留：点面板外面的暗区关掉，鱼就进鱼箱。
        """
        val = None
        text = ""
        tier = None
        cinfo: dict = {}
        # 弹窗是淡入的，第一帧可能还没把字/颜色画全，给它 1.5s 重读几次
        for _ in range(4):
            img = self.grab()
            if brightness(img) >= DIM:
                break
            if val is None:
                val, text, _ = length.read_length_detail(img)
            if tier is None:
                tier, cinfo = color.read_color(img)
            if val is not None and tier is not None:
                break
            time.sleep(0.4)

        want_sell, why = decide(self.mode, val, tier, self.sell_colors, self.min_len)

        if verbose:
            self.log("  结算: 长度 %s · 颜色 %s  %s → %s" % (
                ("%.1fcm" % val) if val is not None else "?(%s)" % (text or "读不到"),
                color.describe(tier, cinfo) if tier else "?",
                why, "直接卖掉" if want_sell else "留进鱼箱"))

        if not want_sell:
            self.dismiss()
            return val, "kept"

        t0 = time.time()
        blind = 0
        while time.time() - t0 < SELL_WAIT_MAX:
            img = self.grab()
            if brightness(img) >= DIM:
                ok = main_screen(img)
                if ok:
                    return val, "sold"
                self.log("  !! 弹窗关掉了，但画面不像钓鱼主界面，先收手（请看一眼游戏）")
                return val, "unknown"
            btn = length.find_sell_btn(img)
            if btn is not None:
                self.click(*btn)
            elif time.time() - t0 > 1.5 and blind < len(BLIND_BTN):
                # 按钮被公告横幅盖住了：按坐标盲点，点完看弹窗关没关
                self.click(*BLIND_BTN[blind])
                blind += 1
            time.sleep(0.5)
        self.log("  !! 一直没找到「原价出售」，只好关掉（这条留在鱼箱里）")
        self.dismiss()
        return val, "unknown"

    # ---------------------------------------------------------------- 跟踪质量
    def track_stats(self) -> str:
        """这一轮遛鱼「跟得准不准」的汇总，用来对比控制器的效果。"""
        if not self.stat_n:
            return "跟踪统计: 无有效帧"
        return ("跟踪统计: n=%d 平均|误差|=%.1f° 平均滞后=%+.1f° 平均深度=%.2f"
                " 丢帧%d 外推%d"
                % (self.stat_n, self.stat_abs_e / self.stat_n,
                   self.stat_lag / self.stat_n, self.stat_depth / self.stat_n,
                   self.stat_rej, self.stat_pred))

    def timing_stats(self) -> str:
        """循环耗时统计：帧间隔 + 各阶段耗时，看清是不是机器被占满拖慢的。"""
        if not self.t_gap:
            return "耗时统计: 无"

        def pc(a: list[float], p: float) -> float:
            return float(np.percentile(a, p)) if a else 0.0

        med = pc(self.t_gap, 50)
        return ("耗时统计: 帧间隔 中位 %.0f / p90 %.0f / 最大 %.0f ms（%.1f fps）"
                "  | 抓图 %.0f 分析 %.0f 找鱼 %.0f ms（中位）"
                % (med, pc(self.t_gap, 90), max(self.t_gap),
                   1000.0 / max(med, 0.01),
                   pc(self.t_grab, 50), pc(self.t_ana, 50), pc(self.t_find, 50)))

    def reset_stats(self) -> None:
        self.stat_n = 0
        self.stat_abs_e = 0.0
        self.stat_depth = 0.0
        self.stat_lag = 0.0
        self.stat_rej = 0
        self.stat_pred = 0

    def run_once(self, n: int = 0, verbose: bool = True) -> Attempt:
        self.settle_dialogs()
        self.trk.reset()
        self.sync_up()
        if verbose:
            self.log(f"--- 第 {n} 次 ---")
        o0, img0 = self.observe()
        if not o0.present and not main_screen(img0):
            # 既没有轨道、也不是钓鱼主界面 —— 大概是点到公告/活动页去了。
            # 这时候抛竿只会把状态弄得更乱，停下来让人看一眼。
            if verbose:
                self.log("  !! 现在不在钓鱼界面（可能点到别的页面了），这一轮跳过")
            return Attempt(n=n, result="stuck")
        if o0.present:
            # 上一竿没打完就退出来了，小游戏还在，直接接着打
            if verbose:
                self.log("  小游戏还在，直接接管")
        else:
            self.cast()
            if verbose:
                self.log("  抛竿 %.2fs（力度 %d/10）" % (self.cast_secs, self.cast_level))
            o = self.wait_minigame(grace=CAST_GRACE)
            if self._stopping():
                at = Attempt(n=n, result="stopped")
                self.release()
                return at
            if o is None:
                at = Attempt(n=n, result="no-minigame")
                self.release()
                if verbose:
                    self.log("  没等到轨道（可能鱼箱满 / 没饵 / 咬钩太慢）")
                self.dismiss()
                return at
            if verbose:
                self.log(f"  轨道出现: zone={o.zone[0]:.0f}~{o.zone[1]:.0f} prog={o.prog*100:.0f}%")
        at = Attempt(n=n)
        t_round = time.time()
        for _ in range(4):
            if self._stopping():
                at.result = "stopped"
                break
            a = self.fight(max_secs=self.max_secs, verbose=verbose)
            at.samples.extend(a.samples)
            at.result = a.result
            if a.result not in ("gone", "stall", "timeout"):
                break
            if time.time() - t_round > 60.0:
                break                 # 一竿耗太久就不恋战了
            # 轨道可能只是「这条跑了 / 卡住了」的短暂空档，小游戏本身还在。
            # 等一下再看，还在就接着打，不要白白浪费一竿。
            back = False
            for _ in range(6):
                o = self.observe()[0]
                if o.bright < DIM:
                    break            # 出结算弹窗了，走正常流程
                if o.present:
                    back = True
                    break
                time.sleep(0.25)
            if not back:
                break
            if verbose:
                self.log(f"  轨道又出现了（{a.result}），接着打")
        res = self.finish()
        if res != "clear":
            at.result = res
        elif at.result in ("gone", "timeout", "dialog", "stall"):
            at.result = "gone"
        if res == "caught":
            at.length, at.action = self.handle_catch(verbose)
        else:
            self.dismiss()
        if verbose:
            extra = ""
            if at.length is not None:
                extra = "  长度 %.1fcm %s" % (at.length, at.action)
            self.log(f"  结果: {at.result}{extra}")
        if self.on_event:
            self.on_event({"n": n, "result": at.result, "length": at.length,
                           "action": at.action, "fps": self.fps(),
                           "stats": self.track_stats(),
                           "cast_secs": self.cast_secs,
                           "cast_level": self.cast_level})
        return at
