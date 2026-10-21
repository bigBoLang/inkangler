"""墨迹大侠 · 自动钓鱼 —— 图形界面。

    python tools/fishui.py

两种模式
--------
* 喂鱼：结算窗里读到「长度 > 阈值」（默认 65cm）才点「原价出售」，
  比它短的关掉弹窗、留进鱼箱。默认不自动卖鱼箱（不然小鱼又被卖了）。
* 鱼币：不看长度，结算窗里一律直接卖；鱼箱攒够 N 条再整批卖。

抛竿力度
--------
按住转盘的时间决定蓄力（实测 1.7s 涨满，再按久会泄力）。
界面上的 1~10 档 = 满力的 10%~100%，喂鱼默认 3 档（30%）、鱼币默认 10 档（100%），
可以随时拖动。所有设置都会记在 shots/ui_settings.json 里，下次打开还在。
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import ctypes
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import color, hook, sell, win  # noqa: E402
from mjd import license as lic  # noqa: E402
from mjd.version import APP_NAME, AUTHOR, NOTICE, VERSION  # noqa: E402

def _app_dir() -> Path:
    """配置文件/日志/截图放哪儿。

    打包成 exe 后必须放在 exe 旁边：PyInstaller 单文件模式会把代码解到临时目录，
    退出就删，配置写那里等于每次都丢。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_dir()
# 配置/截图/新对话框存哪儿：脚本版放项目的 shots/，exe 版放同目录的「墨迹大侠_数据」
DATA = (ROOT / "墨迹大侠_数据") if getattr(sys, "frozen", False) else (ROOT / "shots")
SETTINGS = DATA / "ui_settings.json"

MODE_TEXT = {hook.MODE_FEED: "喂鱼", hook.MODE_COINS: "鱼币"}
MODE_DEFAULT_LEVEL = {hook.MODE_FEED: 3, hook.MODE_COINS: 10}
# 颜色复选框的文字颜色（深一点，浅色背景上也看得清）
TIER_FG = {"red": "#c0392b", "yellow": "#a87900", "green": "#1e8e3e",
           "blue": "#1a6fd4", "purple": "#7d3cc0", "gray": "#6b7280"}
DEFAULT = {
    "mode": hook.MODE_FEED,
    "min_len": 65.0,
    "level": 3,
    "rounds": 0,
    "auto_sell": False,
    "sell_every": 12,
    "cast_max": hook.CAST_MAX,
    "sell_colors": [],
}


def _num(sb, default, cast=int):
    """读 Spinbox 里的字。

    直接读控件（而不是 IntVar）：用户在输入框里改到一半、或者把内容删空时，
    IntVar 会抛 TclError，整轮就直接退出去了。这里统一兜底成默认值。
    """
    try:
        txt = str(sb.get()).strip()
        return cast(float(txt)) if txt else cast(default)
    except (ValueError, TypeError, tk.TclError):
        return cast(default)


class App:
    def __init__(self, root: tk.Tk, auth: str = ""):
        self.root = root
        self.auth = auth
        self.q: queue.Queue = queue.Queue()
        self.stop_evt = threading.Event()
        self.worker: threading.Thread | None = None
        self.bot = None
        self.n_ok = self.n_sold = self.n_kept = 0
        self.n_round = 0

        cfg = dict(DEFAULT)
        if SETTINGS.exists():
            try:
                cfg.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
            except Exception:
                pass

        # ---------------- 变量
        self.mode = tk.StringVar(value=cfg["mode"])
        self.min_len = tk.DoubleVar(value=float(cfg["min_len"]))
        self.level = tk.IntVar(value=int(cfg["level"]))
        self.rounds = tk.IntVar(value=int(cfg["rounds"]))
        self.auto_sell = tk.BooleanVar(value=bool(cfg["auto_sell"]))
        self.sell_every = tk.IntVar(value=int(cfg["sell_every"]))
        self.cast_max = tk.DoubleVar(value=float(cfg["cast_max"]))
        picked = set(cfg.get("sell_colors") or ())
        self.sell_colors = {t: tk.BooleanVar(value=t in picked) for t in color.TIERS}
        self.status = tk.StringVar(value="待机")
        self.counters = tk.StringVar(value="钓上 0 · 卖出 0 · 留箱 0")
        self.last = tk.StringVar(value="最近：—")
        self.cast_hint = tk.StringVar(value="")

        self._build()
        self._sync_mode(auto=False)
        self._update_cast_hint()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._poll)

    # ---------------------------------------------------------------- 界面
    def _build(self) -> None:
        r = self.root
        r.title("%s  v%s%s" % (
            APP_NAME, VERSION,
            "  [管理员]" if ctypes.windll.shell32.IsUserAnAdmin() else "  [普通权限]"))
        r.geometry("620x820")
        r.minsize(580, 700)
        f = ("Microsoft YaHei UI", 10)
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=f)

        pad = {"padx": 12, "pady": 4}
        top = ttk.Frame(r)
        top.pack(fill="x", **pad)

        # 模式
        box = ttk.LabelFrame(top, text=" 模式 ")
        box.pack(fill="x", pady=(0, 6))
        ttk.Radiobutton(box, text="喂鱼（超过阈值直接卖，小的留进鱼箱）",
                        value=hook.MODE_FEED, variable=self.mode,
                        command=self._sync_mode).pack(anchor="w", padx=10, pady=(6, 2))
        ttk.Radiobutton(box, text="鱼币（所有长度都直接卖）",
                        value=hook.MODE_COINS, variable=self.mode,
                        command=self._sync_mode).pack(anchor="w", padx=10, pady=(0, 8))

        # 钓鱼设置（阈值 / 力度 / 竿数 / 自动卖箱）—— 用 grid 对齐，标签一列、控件一列
        box2 = ttk.LabelFrame(top, text=" 钓鱼设置 ")
        box2.pack(fill="x", pady=(0, 6))
        g = ttk.Frame(box2)
        g.pack(fill="x", padx=10, pady=(8, 8))
        g.columnconfigure(2, weight=1)
        LAB = {"padx": (0, 10), "sticky": "w", "pady": 3}

        ttk.Label(g, text="长度阈值").grid(row=0, column=0, **LAB)
        f = ttk.Frame(g)
        f.grid(row=0, column=1, sticky="w", pady=3)
        self.sp_len = ttk.Spinbox(f, from_=1, to=200, increment=1, width=6,
                                  textvariable=self.min_len, command=self._save_later)
        self.sp_len.pack(side="left")
        ttk.Label(f, text="cm").pack(side="left", padx=(4, 0))
        self.lb_len = ttk.Label(g, text="喂鱼模式：超过它就卖")
        self.lb_len.grid(row=0, column=2, sticky="w", pady=3)

        ttk.Label(g, text="抛竿力度").grid(row=1, column=0, **LAB)
        self.sl = tk.Scale(g, from_=1, to=10, orient="horizontal", showvalue=False,
                           variable=self.level, length=210, sliderlength=22,
                           command=lambda *_: self._update_cast_hint())
        self.sl.grid(row=1, column=1, sticky="w", pady=1)
        ttk.Label(g, textvariable=self.cast_hint).grid(row=1, column=2, sticky="w", pady=3)

        ttk.Label(g, text="满力按住").grid(row=2, column=0, **LAB)
        f = ttk.Frame(g)
        f.grid(row=2, column=1, sticky="w", pady=3)
        self.sp_max = ttk.Spinbox(f, from_=1.0, to=4.0, increment=0.1, width=6,
                                  textvariable=self.cast_max, command=self._update_cast_hint)
        self.sp_max.pack(side="left")
        ttk.Label(f, text="秒").pack(side="left", padx=(4, 0))
        ttk.Label(g, text="蓄力条涨满的时间").grid(row=2, column=2, sticky="w", pady=3)

        ttk.Label(g, text="钓多少竿").grid(row=3, column=0, **LAB)
        self.sp_rounds = ttk.Spinbox(g, from_=0, to=9999, increment=1, width=7,
                                     textvariable=self.rounds)
        self.sp_rounds.grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(g, text="0 = 一直钓").grid(row=3, column=2, sticky="w", pady=3)

        ttk.Checkbutton(g, text="自动卖鱼箱", variable=self.auto_sell,
                        command=self._save_later).grid(row=4, column=0, sticky="w", pady=(6, 0))
        f = ttk.Frame(g)
        f.grid(row=4, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f, text="每").pack(side="left")
        self.sp_every = ttk.Spinbox(f, from_=1, to=50, increment=1, width=5,
                                    textvariable=self.sell_every)
        self.sp_every.pack(side="left", padx=(6, 6))
        ttk.Label(f, text="条卖一次").pack(side="left")

        # 颜色（可多选）
        bg = style.lookup("TFrame", "background") or "#f0f0f0"
        box_c = ttk.LabelFrame(top, text=" 颜色（勾了就直接卖，可多选） ")
        box_c.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(box_c)
        row.pack(fill="x", padx=10, pady=(8, 0))
        self.cb_colors: dict[str, tk.Checkbutton] = {}
        for t in color.TIERS:
            cb = tk.Checkbutton(row, text=color.NAME[t], variable=self.sell_colors[t],
                                command=self._save_later,
                                font=("Microsoft YaHei UI", 10, "bold"),
                                fg=TIER_FG[t], activeforeground=TIER_FG[t],
                                bg=bg, activebackground=bg, selectcolor="white",
                                disabledforeground="#aaa", bd=0, highlightthickness=0)
            cb.pack(side="left", padx=(0, 12))
            self.cb_colors[t] = cb
        ttk.Button(row, text="清空", width=5,
                   command=lambda: self._set_colors(())).pack(side="right")
        ttk.Button(row, text="全选", width=5,
                   command=lambda: self._set_colors(color.TIERS)).pack(side="right", padx=(0, 4))
        ttk.Label(box_c, foreground="#7a8296", justify="left", wraplength=560,
                  text="勾选的颜色直接卖、不看长度；没勾的按长度判断。鱼币模式全卖。").pack(fill="x", padx=10, pady=(2, 8))

        # 点进输入框就全选，免得在原来的「0」后面接着打字变成 012 / 120
        for sb in (self.sp_len, self.sp_max, self.sp_rounds, self.sp_every):
            sb.bind("<FocusIn>", lambda e: e.widget.selection_range(0, "end"))
            sb.bind("<FocusOut>", lambda e: self._save_later())

        # 按钮
        row = ttk.Frame(top)
        row.pack(fill="x", pady=(2, 8))
        right = ttk.Frame(row)
        right.pack(side="right")
        left = ttk.Frame(row)
        left.pack(side="left")
        self.btn_start = ttk.Button(left, text="▶  开始", width=7, command=self._start)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop = ttk.Button(left, text="■  停止", width=7, command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_sell = ttk.Button(left, text="卖空鱼箱", width=8, command=self._sell_now)
        self.btn_sell.pack(side="left")
        self.btn_admin = ttk.Button(right, text="管理员重启", width=10, command=self._relaunch_admin)
        self.btn_admin.pack(side="right")
        ttk.Button(right, text="关于", width=5, command=self._about).pack(side="right", padx=(0, 6))

        # 状态
        st = ttk.Frame(top)
        st.pack(fill="x")
        ttk.Label(st, textvariable=self.status, foreground="#0b6",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        ttk.Label(st, textvariable=self.counters).pack(side="right")

        info = ttk.Frame(top)
        info.pack(fill="x", pady=(2, 4))
        ttk.Label(info, textvariable=self.last, foreground="#555").pack(side="left")

        ttk.Label(top, foreground="#7a8296", justify="left", wraplength=520,
                  text="提示：先在游戏里停在钓鱼界面，再点「开始」；"
                       "运行中不抢鼠标和焦点，可以照常用电脑。").pack(fill="x", pady=(0, 2))
        ttk.Label(top, foreground="#9a5050", justify="left", wraplength=520,
                  text="作者：%s　·　%s" % (AUTHOR, NOTICE)).pack(fill="x", pady=(0, 4))

        # 日志
        box4 = ttk.LabelFrame(r, text=" 日志 ")
        box4.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.txt = tk.Text(box4, height=14, wrap="none", font=("Consolas", 9),
                           background="#111", foreground="#ddd", insertbackground="#ddd")
        sb = ttk.Scrollbar(box4, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.txt.configure(state="disabled")
        if self.auth:
            self._log(self.auth)
        self._log("%s v%s · %s" % (APP_NAME, VERSION,
                                   "已授权" if self.auth else "未授权"))
        self._log("作者 %s · %s" % (AUTHOR, NOTICE))
        self._log("准备就绪。先在游戏里停在钓鱼界面，再点「开始」。")

    # ---------------------------------------------------------------- 小工具
    def _update_cast_hint(self) -> None:
        lv = max(1, min(10, int(self.level.get())))
        cm = _num(self.sp_max, hook.CAST_MAX, float)
        hold = max(hook.CAST_MIN, cm * lv / 10.0)
        self.cast_hint.set("%d 档 · %d%% · 按住 %.2fs" % (lv, lv * 10, hold))

    def _sync_mode(self, auto: bool = True) -> None:
        feed = self.mode.get() == hook.MODE_FEED
        self.sp_len.configure(state="normal" if feed else "disabled")
        for cb in self.cb_colors.values():
            cb.configure(state="normal" if feed else "disabled")
        if auto:
            # 切换模式时把「默认值」带出来，之后用户还能自己拖
            self.level.set(MODE_DEFAULT_LEVEL[self.mode.get()])
            self.auto_sell.set(not feed)
            self._update_cast_hint()
            self._save_later()

    def _set_colors(self, tiers) -> None:
        """「全选 / 清空」用。"""
        want = set(tiers)
        for t, v in self.sell_colors.items():
            v.set(t in want)
        self._save_later()

    def _log(self, msg: str) -> None:
        self.q.put(("log", msg))

    def _save_later(self) -> None:
        self.root.after(400, self._save)

    def _save(self) -> None:
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({
                "mode": self.mode.get(),
                "min_len": _num(self.sp_len, DEFAULT["min_len"], float),
                "level": int(self.level.get()),
                "rounds": _num(self.sp_rounds, 0, int),
                "auto_sell": bool(self.auto_sell.get()),
                "sell_every": _num(self.sp_every, DEFAULT["sell_every"], int),
                "cast_max": _num(self.sp_max, hook.CAST_MAX, float),
                "sell_colors": [t for t, v in self.sell_colors.items() if v.get()],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------------------------------------------------------------- 运行
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            win.find_window("墨迹大侠")
        except Exception as e:
            messagebox.showerror("找不到游戏窗口", "没找到「墨迹大侠」窗口。\n先把微信小程序打开、停在钓鱼界面。\n\n%s" % e)
            return
        self.stop_evt.clear()
        self.n_ok = self.n_sold = self.n_kept = self.n_round = 0
        self._refresh_counters()
        self.last.set("最近：—")
        self.status.set("启动中…")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_sell.configure(state="disabled")
        self._save()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.status.set("正在停…（会先松手，再收工）")
            self.btn_stop.configure(state="disabled")

    def _relaunch_admin(self) -> None:
        """以管理员身份重新启动自己（微信是管理员权限时必须这样）。"""
        if ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showinfo("已经是管理员权限", "当前已经是管理员权限，不用再提权。")
            return
        if getattr(sys, "frozen", False):        # exe 自己重开，双击就带 UAC 清单
            exe, params, workdir = str(sys.executable), None, str(ROOT)
        else:
            exe, params, workdir = sys.executable, '"%s"' % Path(__file__).resolve(), str(ROOT)
        _release_instance()                # 先交还单实例锁，否则新进程会被挡住
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, workdir, 1)
        if rc <= 32:                       # 0 = 失败, 5 = 用户取消 UAC
            _single_instance()             # 没起来，把锁拿回来
            self._log("提权失败或被取消（ShellExecute 返回 %d）。" % rc)
            return
        self._log("已用管理员身份重新启动，本窗口关闭。")
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
        self.root.after(500, self.root.destroy)

    def _about(self) -> None:
        """版本 / 授权 / 机器码 / 使用提示，出问题时方便截图或复制。"""
        ok, msg, owner = lic.check(ROOT)
        machine = lic.machine_code()
        line = ("已授权：%s" % owner) if ok else ("未授权：%s" % msg)
        if not ok and self.auth:      # 已经由启动流程判定过的授权，直接沿用
            line = self.auth

        win = tk.Toplevel(self.root)
        win.title("关于 · " + APP_NAME)
        win.geometry("540x480")
        win.resizable(False, False)
        win.transient(self.root)
        f = ("Microsoft YaHei UI", 10)

        tk.Label(win, text=APP_NAME,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", padx=16, pady=(16, 0))
        tk.Label(win, text="v%s   ·   inkangler   ·   作者 %s" % (VERSION, AUTHOR),
                 foreground="#3d7bfd", font=f).pack(anchor="w", padx=16)
        tk.Label(win, text=line, font=f, justify="left", wraplength=500).pack(
            anchor="w", padx=16, pady=(10, 0))
        tk.Label(win, text="机器码：%s" % machine,
                 font=("Consolas", 10)).pack(anchor="w", padx=16, pady=(6, 0))
        tk.Label(win, text="服务地址：%s" % lic.server_url(), foreground="#666",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(2, 0))

        tips = ("使用提示\n"
                "1. 微信里打开游戏，停在钓鱼界面，再点「开始」。\n"
                "2. 运行中不抢鼠标、不抢焦点，可以照常用电脑。\n"
                "3. 点了没反应 / 截图全黑 → 用「管理员重启」。\n"
                "4. 「喂鱼」只卖超过阈值的鱼，短的留进鱼箱。\n"
                "5. 「鱼币」所有鱼都卖，抛竿默认满力。\n"
                "6. 勾选的颜色钓上来直接卖，不看长度。")
        tk.Label(win, text=tips, font=f, justify="left", wraplength=500,
                 foreground="#333").pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="作者 %s　·　%s" % (AUTHOR, NOTICE),
                 font=("Microsoft YaHei UI", 9), justify="left", wraplength=500,
                 foreground="#9a5050").pack(anchor="w", padx=16, pady=(12, 0))

        def copy_all() -> None:
            win.clipboard_clear()
            win.clipboard_append("\n".join([
                "%s v%s" % (APP_NAME, VERSION), line,
                "机器码：" + machine, "服务：" + lic.server_url()]))
            copy_btn.configure(text="已复制")

        btns = ttk.Frame(win)
        btns.pack(side="bottom", fill="x", padx=16, pady=12)
        copy_btn = ttk.Button(btns, text="复制版本信息", command=copy_all)
        copy_btn.pack(side="left")
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right")

    def _sell_now(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.status.set("卖鱼箱…")
        self.btn_start.configure(state="disabled")
        self.btn_sell.configure(state="disabled")

        def job():
            try:
                w = win.find_window("墨迹大侠")
                n = sell.Seller(w, act=True, log=self._log).run()
                self._log("卖完：%d 批" % n)
            except Exception as e:
                self._log("卖鱼出错：%r" % e)
            finally:
                self.q.put(("idle", None))

        threading.Thread(target=job, daemon=True).start()

    def _run(self) -> None:
        try:
            w = win.find_window("墨迹大侠")
            reason = win.input_blocked(w)
            if reason:
                self._log(reason)
                self.q.put(("status", "权限不够：需要管理员"))
                return
            mode = self.mode.get()
            min_len = _num(self.sp_len, DEFAULT["min_len"], float)
            cast_max = _num(self.sp_max, hook.CAST_MAX, float)
            every = _num(self.sp_every, DEFAULT["sell_every"], int) if self.auto_sell.get() else 0
            total = _num(self.sp_rounds, 0, int)
            bot = hook.HookBot(
                act=True, mode=mode, min_len=min_len,
                sell_colors={t for t, v in self.sell_colors.items() if v.get()},
                cast_level=int(self.level.get()), cast_max=cast_max,
                log=self._log, should_stop=self.stop_evt.is_set,
                on_event=lambda d: self.q.put(("event", d)),
                dialog_dir=DATA / "dialogs" / "live",
                max_secs=90.0,
            )
            self.bot = bot
            seller = sell.Seller(w, act=True, log=self._log)
            self._log("开始：%s模式，阈值 %scm，抛竿 %d 档（%.2fs），鱼箱%s，计划 %s"
                      % (MODE_TEXT[mode], min_len, bot.cast_level, bot.cast_secs,
                         ("每 %d 条自动卖" % every) if every else "不自动卖",
                         ("%d 竿" % total) if total else "一直钓"))
            since = 0
            i = 0
            while not self.stop_evt.is_set():
                i += 1
                if total and i > total:
                    self._log("已钓满 %d 竿，收工" % total)
                    break
                self.n_round = i
                self.q.put(("status", "遛鱼中 · 第 %d 竿%s"
                            % (i, ("/%d" % total) if total else "")))
                bot.reset_stats()
                at = bot.run_once(i)
                if at.result == "stopped":
                    self._log("已停止")
                    break
                if at.result == "caught":
                    self.n_ok += 1
                    if at.action == "sold":
                        self.n_sold += 1
                    elif at.action == "kept":
                        self.n_kept += 1
                    since += 1
                if at.result == "stuck":
                    self._log("现在不在钓鱼界面（可能点到别的页面了），已停下，请看一眼游戏")
                    self.q.put(("status", "停了：不在钓鱼界面"))
                    break
                self.q.put(("counters", None))
                if every and since >= every and not self.stop_evt.is_set():
                    self.q.put(("status", "卖鱼箱…"))
                    nb = seller.run()
                    self._log("卖鱼箱：%d 批" % nb)
                    since = 0
                time.sleep(0.3)
        except Exception as e:
            self._log("出错：%r" % e)
            for line in traceback.format_exc().rstrip().splitlines()[-8:]:
                self._log("   " + line.strip())
            self.q.put(("status", "出错了，看日志"))
        finally:
            if self.bot is not None:
                try:
                    self.bot.release()
                except Exception:
                    pass
            self.q.put(("idle", None))

    # ---------------------------------------------------------------- 刷新
    def _refresh_counters(self) -> None:
        total = _num(self.sp_rounds, 0, int)
        self.counters.set("第 %d%s 竿 · 钓上 %d · 卖出 %d · 留箱 %d"
                          % (self.n_round, ("/%d" % total) if total else "",
                             self.n_ok, self.n_sold, self.n_kept))

    def _poll(self) -> None:
        try:
            for _ in range(200):
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._append(str(data))
                elif kind == "status":
                    self.status.set(str(data))
                elif kind == "counters":
                    self._refresh_counters()
                elif kind == "idle":
                    self.status.set("待机")
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.btn_sell.configure(state="normal")
                elif kind == "event":
                    d = data
                    if d.get("length") is not None:
                        act = {"sold": "直接卖掉", "kept": "留进鱼箱",
                               "unknown": "没卖成"}.get(d.get("action", ""), d.get("action", ""))
                        self.last.set("最近：%.1fcm → %s" % (d["length"], act))
                    fps = d.get("fps") or 0
                    if fps:
                        self.status.set("遛鱼中 · 第 %d 竿 · %.1ffps%s"
                                        % (d.get("n", 0), fps,
                                           "  ⚠ 帧率偏低" if fps < 8 else ""))
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _append(self, line: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", line + "\n")
        # 只留最后 600 行，免得跑一晚上把内存撑爆
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > 620:
            self.txt.delete("1.0", "%d.0" % (n - 600))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _on_close(self) -> None:
        self._save()
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.worker.join(timeout=3.0)
        self.root.destroy()


def _license_gate(root: tk.Tk, reason: str) -> tuple[bool, str]:
    """没授权时弹窗：显示本机机器码，让对方粘授权文本。"""
    code = lic.machine_code()
    res = {"ok": False, "msg": ""}
    f = ("Microsoft YaHei UI", 10)

    dlg = tk.Toplevel(root)
    dlg.title("激活 · 墨迹大侠自动钓鱼")
    dlg.geometry("580x470")
    dlg.resizable(False, False)
    dlg.attributes("-topmost", True)      # 主窗口是 withdraw 状态，transient 会被一起隐藏

    tk.Label(dlg, text="这台电脑还没有授权：" + reason, font=f, fg="#b00000",
             wraplength=540, justify="left").pack(anchor="w", padx=14, pady=(14, 8))
    tk.Label(dlg, text="① 机器码（点「复制」发给作者）：", font=f).pack(anchor="w", padx=14)

    row = tk.Frame(dlg)
    row.pack(fill="x", padx=14, pady=(3, 10))
    ent = tk.Entry(row, font=("Consolas", 13), justify="center")
    ent.insert(0, code)
    ent.configure(state="readonly")
    ent.pack(side="left", fill="x", expand=True, ipady=4)

    tk.Label(dlg, text="② 有激活码就直接在这儿激活（12 位，形如 7K2M-9PQX-3RTY）：",
             font=f).pack(anchor="w", padx=14)
    row2 = tk.Frame(dlg)
    row2.pack(fill="x", padx=14, pady=(3, 10))
    ent_code = tk.Entry(row2, font=("Consolas", 13), justify="center")
    ent_code.pack(side="left", fill="x", expand=True, ipady=4)
    btn_online = ttk.Button(row2, text="在线激活", width=10)
    btn_online.pack(side="left", padx=(6, 0))

    tk.Label(dlg, text="③ 或者把收到的授权文本（MOJI1.… 一整行）粘到下面：",
             font=f).pack(anchor="w", padx=14)
    box = tk.Text(dlg, height=4, font=("Consolas", 9), wrap="char")
    box.pack(fill="both", expand=True, padx=14, pady=(3, 6))

    tip = tk.Label(dlg, text="", font=f, fg="#b00000", wraplength=540, justify="left")
    tip.pack(anchor="w", padx=14)

    tk.Label(dlg, text="作者 %s　·　%s" % (AUTHOR, NOTICE),
             font=("Microsoft YaHei UI", 9), fg="#9a5050",
             wraplength=540, justify="left").pack(anchor="w", padx=14, pady=(6, 0))

    def _finish(text: str, msg: str) -> None:
        try:
            lic.save(ROOT, text)
        except OSError as e:
            tip.configure(text="授权有效，但写不进 %s（%s）" % (lic.license_path(ROOT), e),
                          fg="#b00000")
            return
        res["ok"], res["msg"] = True, msg
        dlg.destroy()

    def _copy() -> None:
        dlg.clipboard_clear()
        dlg.clipboard_append(code)
        tip.configure(text="机器码已复制到剪贴板。", fg="#2a7")

    def _activate() -> None:
        text = box.get("1.0", "end").strip()
        if not text:
            tip.configure(text="先把授权文本粘进来。", fg="#b00000")
            return
        ok, msg, _owner = lic.verify_text(text)
        if not ok:
            tip.configure(text="✗ " + msg, fg="#b00000")
            return
        _finish(text, msg)

    def _log(line: str) -> None:
        """激活过程记一行，出问题时不用猜（窗口版 exe 看不到 stderr）。"""
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            with open(DATA / "activation.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except OSError:
            pass

    def _online() -> None:
        c = ent_code.get().strip()
        if not c:
            tip.configure(text="先填激活码。", fg="#b00000")
            ent_code.focus_set()
            return
        btn_online.configure(state="disabled")
        tip.configure(text="正在连激活服务器…（失败会自动换线路重试，最多约 30 秒）", fg="#555")
        _log("激活尝试：机器码 %s，码 %s" % (code, c))

        # 工作线程只往队列里放结果，绝不碰 Tk 控件：
        # 跨线程调 dlg.after() 在打包后的窗口版 exe 里可能被静默吞掉，
        # 界面就会永远停在「正在连激活服务器…」。
        q: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                ok, msg, text = lic.activate_online(code, c, log=_log)
            except Exception as e:                      # noqa: BLE001
                _log("激活抛异常：" + traceback.format_exc())
                ok, msg, text = False, "激活时出错：%s: %s" % (type(e).__name__, e), ""
            _log("激活结果：ok=%s msg=%s" % (ok, msg))
            q.put((ok, msg, text))

        def poll() -> None:
            try:
                ok, msg, text = q.get_nowait()
            except queue.Empty:
                dlg.after(120, poll)
                return
            btn_online.configure(state="normal")
            if not ok:
                tip.configure(text="✗ " + msg, fg="#b00000")
                return
            _finish(text, msg)

        threading.Thread(target=work, daemon=True).start()
        dlg.after(120, poll)

    btn_online.configure(command=_online)

    btns = tk.Frame(dlg)
    btns.pack(fill="x", padx=14, pady=10)
    ttk.Button(btns, text="粘贴激活", command=_activate).pack(side="right")
    ttk.Button(btns, text="退出", command=dlg.destroy).pack(side="right", padx=(0, 8))
    ttk.Button(btns, text="复制机器码", command=_copy).pack(side="left")

    dlg.bind("<Return>", lambda e: _online())
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    dlg.grab_set()
    ent_code.focus_set()
    root.wait_window(dlg)
    return res["ok"], res["msg"]


# ---------------------------------------------------------------- 单实例 / 闪屏
_MUTEX: int | None = None
TITLE_PREFIX = APP_NAME


def _focus_existing() -> None:
    """把已经在跑的那个窗口叫到前面（跨权限失败也不报错）。"""
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(TITLE_PREFIX):
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    if found:
        user32.ShowWindow(found[0], 9)          # SW_RESTORE
        user32.SetForegroundWindow(found[0])


def _single_instance() -> bool:
    """第一次运行返回 True；已经有实例在跑就叫它到前面并返回 False。"""
    global _MUTEX
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    h = k32.CreateMutexW(None, False, "Local\\inkangler-auto-fish")
    if not h:                                    # 拿不到锁就别拦着用户
        return True
    if ctypes.get_last_error() == 183:           # ERROR_ALREADY_EXISTS
        k32.CloseHandle(h)
        _focus_existing()
        return False
    _MUTEX = h
    return True


def _release_instance() -> None:
    """提权重启前必须先松手，否则新进程会被自己的锁挡在门外。"""
    global _MUTEX
    if _MUTEX:
        ctypes.windll.kernel32.CloseHandle(_MUTEX)
        _MUTEX = None


def _close_splash() -> None:
    """关掉 PyInstaller 的启动闪屏（源码直接跑时没有这个模块）。"""
    try:
        import pyi_splash                      # type: ignore[import-not-found]
        pyi_splash.close()
    except Exception:                          # noqa: BLE001
        pass


def main() -> int:
    # 单实例：双击第二次时直接提示，避免出现两个窗口、两个激活框
    if not _single_instance():
        ctypes.windll.user32.MessageBoxW(
            None,
            "程序已经在运行了，不用再点一次。\n\n"
            "窗口可能被别的程序挡住了，按 Alt+Tab 找一下。",
            TITLE_PREFIX, 0x40)                # MB_ICONINFORMATION
        return 0

    try:
        root = tk.Tk()
        root.withdraw()
        ok, msg, _owner = lic.check(ROOT)
        if not ok:
            _close_splash()                    # 先把闪屏撤掉，别盖住激活窗
            ok, msg = _license_gate(root, msg)
            if not ok:
                root.destroy()
                return 3
        root.deiconify()
        App(root, auth=msg)
        root.update()                          # 先画出主窗口，再撤闪屏，中间不闪白
        _close_splash()
        root.mainloop()
        return 0
    finally:
        _close_splash()                        # 任何异常路径都不留悬空闪屏


if __name__ == "__main__":
    raise SystemExit(main())
