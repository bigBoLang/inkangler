"""墨迹大侠 · 功能面板（先选，再跑）

    python tools/gameui.py

左边是游戏主界面上的全部入口，按「已可用 / 待标定 / 禁用」分类：
  * 勾选你想让脚本做的，点「开始选中」。
  * 已可用：自动钓鱼（打开钓鱼面板）、自动挑战、红点巡检、江湖助手一键日常。
  * 待标定：先记下你的选择，日志里会列出来；标定好一个就少一个。
  * 禁用：邮件（按你的要求）、充值/月卡/战令、兑换、广告 —— 勾不上。

三条硬限制写在代码里，不是 UI 摆设：
  1. 不花叶子：不点任何「购买 / 充值 / 叶子 / 元宝」按钮；
  2. 不碰兑换：兑换页整页跳过；
  3. 不碰邮件：邮件入口不点。
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import battle, driver, reddot, win  # noqa: E402
from mjd.version import VERSION  # noqa: E402


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_dir()
DATA = (ROOT / "墨迹大侠_数据") if getattr(sys, "frozen", False) else (ROOT / "shots")
SETTINGS = DATA / "game_settings.json"


@dataclass
class Feat:
    key: str
    cat: str
    name: str
    desc: str
    status: str          # ok / todo / off
    default: bool = False


FEATURES = [
    # ---------------- 已经能跑
    Feat("fishing", "已可用", "自动钓鱼", "抛竿 → 遛鱼 → 结算 → 卖鱼（打开钓鱼面板设置）", "ok", True),
    Feat("challenge", "已可用", "自动挑战", "幸存者模式：自动选技能 / 关结算 / 再来一局", "ok", False),
    Feat("reddot", "已可用", "红点巡检", "扫描主界面红点，只报告、不点击（绝对安全）", "ok", True),
    Feat("helper", "已可用", "江湖助手 · 一键日常", "一键勾选 → 一键完成（12000 铜钱 / 0 叶子）", "ok", True),

    # ---------------- 免费领取，待逐个标定
    Feat("welfare", "免费领取", "福利中心 · 签到", "累计签到里的免费奖励，不碰月卡/战令", "todo"),
    Feat("achievement", "免费领取", "成就 · 领取", "成就达成后的领取按钮", "todo"),
    Feat("gallery", "免费领取", "图鉴", "红点只是新内容，目前没看到领取按钮", "todo"),
    Feat("travel", "免费领取", "游历 · 挂机收益", "游历/挂机收益领取", "todo"),
    Feat("daily", "免费领取", "每日任务", "日常任务一键领取", "todo"),
    Feat("gameplay", "免费领取", "玩法 · 每日免费", "玩法页里每日免费次数", "todo"),
    Feat("dragon", "免费领取", "玉龙初征 · 免费档", "战令免费档（含付费档，默认不勾）", "todo"),

    # ---------------- 养成，只点免费
    Feat("market", "养成", "坊市 · 只看不买", "打开坊市只领免费/看行情，不买", "todo"),
    Feat("hero", "养成", "大侠升级", "用材料升级，不花叶子", "todo"),
    Feat("martial", "养成", "武学升级", "用材料升级，不花叶子", "todo"),
    Feat("sect", "养成", "门派日常", "门派里的免费日常", "todo"),
    Feat("elite", "养成", "精英挑战", "普通/精英切换后的挑战", "todo"),

    # ---------------- 禁区
    Feat("mail", "禁用", "邮件", "按你的要求：不碰", "off"),
    Feat("activity_paid", "禁用", "活动（消费送礼 / 每日充值）", "打开就是付费活动，按规则不点", "off"),
    Feat("paid", "禁用", "首充 / 月卡 / 战令 / 消费活动", "会引导充值，永远不点", "off"),
    Feat("exchange", "禁用", "兑换 / 商店", "按你的要求：不碰兑换", "off"),
    Feat("ads", "禁用", "广告 / 双倍奖励", "看广告的按钮，永远不点", "off"),
]

STATUS_TEXT = {"ok": "已可用", "todo": "待标定", "off": "禁用"}
STATUS_FG = {"ok": "#1e8e3e", "todo": "#a87900", "off": "#b03030"}


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.stop_evt = threading.Event()
        self.worker: threading.Thread | None = None
        self.bot: battle.BattleBot | None = None
        self.fish_top: tk.Toplevel | None = None
        self.vars: dict[str, tk.BooleanVar] = {}

        cfg = {}
        if SETTINGS.exists():
            try:
                cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        picked = set(cfg.get("picked") or [])
        self.rounds = tk.IntVar(value=int(cfg.get("rounds", 1)))
        self.status = tk.StringVar(value="待机 · 先勾选左边要跑的功能")
        self.redline = tk.StringVar(value="红点：还没巡检")
        self.counters = tk.StringVar(value="局数 0 · 通关 0 · 选技能 0")

        for f in FEATURES:
            self.vars[f.key] = tk.BooleanVar(
                value=(f.key in picked) if picked else f.default)

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._poll)

    # ---------------------------------------------------------------- 界面
    def _build(self) -> None:
        r = self.root
        r.title(f"墨迹大侠 · 功能面板 v{VERSION}")
        r.geometry("1060x780")
        r.minsize(900, 640)
        f = ("Microsoft YaHei UI", 10)
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=f)

        head = ttk.Frame(r, padding=(14, 10, 14, 2))
        head.pack(fill="x")
        ttk.Label(head, text="墨迹大侠 · 功能面板",
                  font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        ttk.Label(head, text="  勾选 → 开始选中；没标定的会先记下来",
                  foreground="#666", font=("Microsoft YaHei UI", 9)).pack(side="left", pady=(6, 0))

        body = ttk.Frame(r, padding=(10, 4, 10, 4))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=560)
        body.columnconfigure(1, weight=1, minsize=450)
        body.rowconfigure(0, weight=1)

        # 左：可滚动的功能清单
        left = ttk.LabelFrame(body, text=" 功能清单 ", width=560)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)
        canvas = tk.Canvas(left, borderwidth=0, highlightthickness=0, width=535)
        sb = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        cat = None
        for feat in FEATURES:
            if feat.cat != cat:
                cat = feat.cat
                ttk.Label(inner, text="  " + cat,
                          foreground="#3d7bfd",
                          font=("Microsoft YaHei UI", 11, "bold")).pack(
                    anchor="w", padx=6, pady=(10, 2))
            row = ttk.Frame(inner)
            row.pack(fill="x", padx=6, pady=1)
            row.columnconfigure(2, weight=1)
            cb = ttk.Checkbutton(row, variable=self.vars[feat.key],
                                 command=self._refresh_sel)
            cb.grid(row=0, column=0, sticky="n", padx=(0, 4))
            if feat.status == "off":
                cb.state(["disabled"])
            ttk.Label(row, text=feat.name,
                      font=("Microsoft YaHei UI", 10, "bold")).grid(
                row=0, column=1, sticky="nw")
            ttk.Label(row, text=feat.desc, foreground="#666",
                      wraplength=250, justify="left",
                      font=("Microsoft YaHei UI", 9)).grid(
                row=0, column=2, sticky="nw", padx=(6, 6))
            ttk.Label(row, text=STATUS_TEXT[feat.status], foreground=STATUS_FG[feat.status],
                      font=("Microsoft YaHei UI", 9, "bold")).grid(
                row=0, column=3, sticky="ne")

        # 右：安全锁 + 红点 + 日志
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        lock = ttk.LabelFrame(right, text=" 硬限制（不可关闭） ")
        lock.pack(fill="x")
        for txt in ("不花叶子 / 不充值", "不碰兑换 / 商店", "不碰邮件", "不点广告"):
            ttk.Label(lock, text="✔ " + txt, foreground="#1e8e3e",
                      font=("Microsoft YaHei UI", 10, "bold")).pack(
                anchor="w", padx=8, pady=1)
        ttk.Label(lock, text="这些限制写死在代码里，不是可选项；\n只点「领取 / 一键领取 / 签到」这类免费按钮。",
                  foreground="#1e8e3e", font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=8, pady=(3, 6))

        rd = ttk.LabelFrame(right, text=" 红点 ")
        rd.pack(fill="x", pady=(8, 0))
        ttk.Label(rd, textvariable=self.redline, wraplength=360,
                  justify="left", foreground="#333",
                  font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=8, pady=(6, 2))
        ttk.Button(rd, text="巡检红点（只报不点）", command=self._scan_red).pack(
            anchor="w", padx=8, pady=(0, 8))

        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(8, 0))
        self.btn_start = ttk.Button(bar, text="▶ 开始选中", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="钓鱼面板", command=self._open_fishing).pack(side="right")

        bar2 = ttk.Frame(right)
        bar2.pack(fill="x", pady=(6, 0))
        ttk.Label(bar2, text="挑战局数").pack(side="left")
        self.sp_rounds = ttk.Spinbox(bar2, from_=0, to=999, width=5, textvariable=self.rounds)
        self.sp_rounds.pack(side="left", padx=(4, 0))
        ttk.Label(bar2, text="（0 = 一直刷）").pack(side="left", padx=(6, 0))

        ttk.Label(right, textvariable=self.status, foreground="#1a6fd4",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(6, 0))
        ttk.Label(right, textvariable=self.counters, foreground="#555",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        lf = ttk.LabelFrame(right, text=" 日志 ")
        lf.pack(fill="both", expand=True, pady=(4, 0))
        self.txt = tk.Text(lf, wrap="word", state="disabled", font=("Consolas", 9),
                           background="#1e1e1e", foreground="#d6d6d6",
                           insertbackground="#d6d6d6", relief="flat")
        tsb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

        self._refresh_sel()
        self._log(f"墨迹大侠 · 功能面板 v{VERSION}")
        self._log("先在游戏里停在主界面；勾选后点「开始选中」。")
        self._log("禁用项（邮件/充值/兑换/广告）勾不上，也不会出现在运行列表里。")
        r.after(80, lambda: canvas.yview_moveto(0))

    # ---------------------------------------------------------------- 小工具
    def _picked(self) -> list[Feat]:
        return [f for f in FEATURES if self.vars[f.key].get()]

    def _refresh_sel(self) -> None:
        picked = self._picked()
        ok = sum(1 for f in picked if f.status == "ok")
        todo = sum(1 for f in picked if f.status == "todo")
        self.status.set(f"已选 {len(picked)} 项：可用 {ok} · 待标定 {todo}")

    def _log(self, msg: str) -> None:
        self.q.put(("log", msg))

    def _append(self, line: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", time.strftime("[%H:%M:%S] ") + line + "\n")
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > 620:
            self.txt.delete("1.0", "%d.0" % (n - 600))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    # ---------------------------------------------------------------- 动作
    def _scan_red(self) -> None:
        try:
            drv = driver.make_driver(verbose=False)
            sc = battle.detect(drv.grab())
            if sc.name != "home":
                self.redline.set("当前不在主界面（" + sc.name + "），先回到主界面再巡检")
                self._log("红点巡检：不在主界面，跳过")
                return
            dots = reddot.scan(drv.grab())
            text = reddot.summary(dots)
            self.redline.set(text)
            self._log("红点巡检：" + text)
        except Exception as e:
            self.redline.set("巡检失败：" + str(e))
            self._log("红点巡检失败：%r" % e)

    def _open_fishing(self) -> None:
        if self.fish_top is not None and self.fish_top.winfo_exists():
            self.fish_top.lift()
            return
        if getattr(sys, "frozen", False):
            import subprocess
            base = Path(sys.executable).resolve().parent
            for name in ("墨迹大侠自动钓鱼.exe", "墨迹大侠自动钓鱼_测试.exe"):
                p = base / name
                if p.exists():
                    subprocess.Popen([str(p)], cwd=str(base))
                    self._log("已打开钓鱼程序：" + p.name)
                    return
            messagebox.showinfo(
                "没找到钓鱼程序",
                "把「墨迹大侠自动钓鱼.exe」放在功能面板旁边，\n"
                "或者用源码版 python tools/gameui.py 里的内置钓鱼面板。")
            return
        try:
            import importlib
            FishApp = importlib.import_module("tools.fishui").App
            top = tk.Toplevel(self.root)
            top.geometry("620x820")
            top.title("墨迹大侠 · 自动钓鱼")
            FishApp(top, auth="")
            self.fish_top = top
            self._log("已打开钓鱼面板（在那边设置并点开始）")
        except Exception as e:
            self._log("打开钓鱼面板失败：%r" % e)
            messagebox.showerror("打开失败", str(e))

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        picked = self._picked()
        if not picked:
            messagebox.showinfo("没选功能", "先在左边勾选至少一项。")
            return
        try:
            win.find_window("墨迹大侠")
        except Exception as e:
            messagebox.showerror("找不到游戏窗口", str(e))
            return
        self._save()
        self.stop_evt.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.worker = threading.Thread(target=self._run, args=(picked,), daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.status.set("正在停…")
            self.btn_stop.configure(state="disabled")

    def _run(self, picked: list[Feat]) -> None:
        try:
            todo = [f.name for f in picked if f.status == "todo"]
            if todo:
                self._log("你勾了还没标定的：" + "、".join(todo))
                self._log("这些先跳过；告诉我先做哪个，我去把领取流程标定出来。")

            need_game = any(f.key in ("reddot", "challenge", "helper") for f in picked)
            drv = None
            if need_game:
                drv = driver.make_driver(verbose=True)
                self._log("驱动：" + drv.name)

            for f in picked:
                if self.stop_evt.is_set():
                    break
                if f.key == "reddot":
                    sc = battle.detect(drv.grab())
                    if sc.name != "home":
                        self._log("红点巡检：不在主界面（%s），跳过" % sc.name)
                        continue
                    dots = reddot.scan(drv.grab())
                    self._log("红点巡检：" + reddot.summary(dots))
                elif f.key == "challenge":
                    rounds = self._rounds()
                    self._log(f"自动挑战：{('一直刷' if rounds == 0 else str(rounds) + ' 局')}"
                              f"，技能卡选中间")
                    self.bot = battle.BattleBot(
                        drv.grab, drv.click, rounds=rounds, pick=1,
                        log=self._log, stop=self.stop_evt.is_set)
                    st = self.bot.run()
                    self._log(f"挑战结束：局数 {st.rounds} · 通关 {st.cleared} · "
                              f"选技能 {st.skills} 次 · {st.elapsed / 60:.1f} 分钟")
                elif f.key == "helper":
                    from mjd.dailies import HelperSweep
                    hs = HelperSweep(drv.grab, drv.click, log=self._log,
                                     stop=self.stop_evt.is_set)
                    r = hs.run()
                    self._log("江湖助手：" + str(r.get("reason")))
                elif f.key == "fishing":
                    self._log("自动钓鱼：请在右边的「钓鱼面板」里设置并点开始。")
                elif f.status == "todo":
                    self._log(f"{f.name}：还没标定（已记录）")
        except Exception as e:
            self._log("出错：%r" % e)
            for line in traceback.format_exc().rstrip().splitlines()[-8:]:
                self._log("   " + line.strip())
        finally:
            self.q.put(("idle", None))

    def _rounds(self) -> int:
        try:
            return max(0, int(float(str(self.sp_rounds.get()).strip() or "1")))
        except (ValueError, tk.TclError):
            return 1

    def _poll(self) -> None:
        try:
            for _ in range(200):
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._append(str(data))
                    if self.bot:
                        st = self.bot.stats
                        self.counters.set(f"局数 {st.rounds} · 通关 {st.cleared} · 选技能 {st.skills}")
                elif kind == "idle":
                    self.status.set("待机 · 先勾选左边要跑的功能")
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _save(self) -> None:
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({
                "picked": [f.key for f in self._picked()],
                "rounds": self._rounds(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self) -> None:
        self._save()
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.worker.join(timeout=3.0)
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
