"""墨迹大侠 · 自动挑战 —— 图形界面。

    python tools/battleui.py

挑战模式里角色自己打，玩家只在升级时选一张技能卡。这个界面就是让它自动
选卡、关结算、再来一局。默认刷 1 局（约 8~9 分钟），也可以设 0 一直刷。

前置：游戏窗口打开，停在主界面或挑战里都行；微信是管理员权限时，本程序
也要管理员（或先跑 `python tools/remote.py --start`）。
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import battle, driver, win  # noqa: E402
from mjd.version import APP_NAME, VERSION  # noqa: E402


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_dir()
DATA = (ROOT / "墨迹大侠_数据") if getattr(sys, "frozen", False) else (ROOT / "shots")
SETTINGS = DATA / "battle_settings.json"
DEFAULT = {"rounds": 1, "pick": 1}


def _num(sb, default: int) -> int:
    try:
        txt = str(sb.get()).strip()
        return int(float(txt)) if txt else default
    except (ValueError, TypeError, tk.TclError):
        return default


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.stop_evt = threading.Event()
        self.worker: threading.Thread | None = None
        self.bot: battle.BattleBot | None = None

        cfg = dict(DEFAULT)
        if SETTINGS.exists():
            try:
                cfg.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
            except Exception:
                pass

        self.rounds = tk.IntVar(value=int(cfg["rounds"]))
        self.pick = tk.IntVar(value=int(cfg["pick"]))
        self.status = tk.StringVar(value="待机")
        self.counters = tk.StringVar(value="局数 0 · 通关 0 · 选技能 0")

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._poll)

    # ---------------------------------------------------------------- 界面
    def _build(self) -> None:
        r = self.root
        r.title(f"{APP_NAME} · 自动挑战 v{VERSION}")
        r.geometry("620x620")
        r.minsize(560, 500)
        f = ("Microsoft YaHei UI", 10)

        top = ttk.Frame(r, padding=(14, 12, 14, 4))
        top.pack(fill="x")
        ttk.Label(top, text="自动挑战", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        ttk.Label(top, text="  幸存者模式 · 自动选技能 / 关结算 / 再来一局",
                  foreground="#666", font=("Microsoft YaHei UI", 9)).pack(side="left", pady=(6, 0))

        box = ttk.LabelFrame(r, text=" 设置 ", padding=(12, 8))
        box.pack(fill="x", padx=14, pady=(8, 4))

        ttk.Label(box, text="刷几局", font=f).grid(row=0, column=0, sticky="w")
        self.sp_rounds = ttk.Spinbox(box, from_=0, to=999, width=6, textvariable=self.rounds)
        self.sp_rounds.grid(row=0, column=1, sticky="w", padx=(6, 2))
        ttk.Label(box, text="（0 = 一直刷）", foreground="#666",
                  font=("Microsoft YaHei UI", 9)).grid(row=0, column=2, sticky="w")

        ttk.Label(box, text="技能卡", font=f).grid(row=1, column=0, sticky="w", pady=(8, 0))
        pf = ttk.Frame(box)
        pf.grid(row=1, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(8, 0))
        for i, txt in enumerate(("左", "中", "右")):
            ttk.Radiobutton(pf, text=txt, value=i, variable=self.pick).pack(side="left", padx=(0, 12))

        ttk.Label(box, text="提示", foreground="#666",
                  font=("Microsoft YaHei UI", 9)).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(box, text="选「中」最稳；技能卡位置不影响能不能打，只影响 Build 花样。",
                  foreground="#666", font=("Microsoft YaHei UI", 9)).grid(
            row=2, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(8, 0))

        bar = ttk.Frame(r, padding=(14, 6))
        bar.pack(fill="x")
        self.btn_start = ttk.Button(bar, text="开始", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="管理员重启", command=self._relaunch_admin).pack(side="right")

        ttk.Label(r, textvariable=self.status, foreground="#1a6fd4",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16)
        ttk.Label(r, textvariable=self.counters, foreground="#333",
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(2, 6))

        lf = ttk.LabelFrame(r, text=" 日志 ", padding=(6, 4))
        lf.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.txt = tk.Text(lf, wrap="word", state="disabled", font=("Consolas", 9),
                           background="#1e1e1e", foreground="#d6d6d6",
                           insertbackground="#d6d6d6", relief="flat")
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

    def _log(self, msg: str) -> None:
        self.q.put(("log", msg))

    def _append(self, line: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", line + "\n")
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > 620:
            self.txt.delete("1.0", "%d.0" % (n - 600))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _refresh(self) -> None:
        st = self.bot.stats if self.bot else None
        if st:
            self.counters.set(f"局数 {st.rounds} · 通关 {st.cleared} · 选技能 {st.skills}")

    # ---------------------------------------------------------------- 运行
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            win.find_window("墨迹大侠")
        except Exception as e:
            messagebox.showerror("找不到游戏窗口",
                                 "没找到「墨迹大侠」窗口。\n先把微信小程序打开。\n\n%s" % e)
            return
        self.stop_evt.clear()
        self.status.set("启动中…")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._save()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_evt.set()
            self.status.set("正在停…（当前这一帧处理完就停）")
            self.btn_stop.configure(state="disabled")

    def _relaunch_admin(self) -> None:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showinfo("已经是管理员权限", "当前已经是管理员权限，不用再提权。")
            return
        if getattr(sys, "frozen", False):
            exe, params = str(sys.executable), None
        else:
            exe, params = sys.executable, '"%s"' % Path(__file__).resolve()
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, str(ROOT), 1)
        if rc <= 32:
            self._log(f"提权失败或被取消（ShellExecute 返回 {rc}）。")
            return
        self._log("已用管理员身份重新启动，本窗口关闭。")
        self.root.after(500, self.root.destroy)

    def _run(self) -> None:
        try:
            drv = driver.make_driver()
            self.q.put(("status", f"运行中 · {drv.name}"))
            self._log(f"驱动：{drv.name}")
            self.bot = battle.BattleBot(
                drv.grab, drv.click, rounds=_num(self.sp_rounds, 1),
                pick=int(self.pick.get()), log=self._log,
                stop=self.stop_evt.is_set,
            )
            st = self.bot.run()
            self._log(f"结束：{st.result or '停止'}  局数 {st.rounds}  通关 {st.cleared}  "
                      f"用时 {st.elapsed / 60:.1f} 分钟")
        except Exception as e:
            self._log("出错：%r" % e)
            for line in traceback.format_exc().rstrip().splitlines()[-8:]:
                self._log("   " + line.strip())
            self.q.put(("status", "出错，看日志"))
        finally:
            self.q.put(("idle", None))

    def _poll(self) -> None:
        try:
            for _ in range(200):
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._append(str(data))
                    self._refresh()
                elif kind == "status":
                    self.status.set(str(data))
                elif kind == "idle":
                    self.status.set("待机")
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self._refresh()
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _save(self) -> None:
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(
                {"rounds": _num(self.sp_rounds, 1), "pick": int(self.pick.get())},
                ensure_ascii=False, indent=2), encoding="utf-8")
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
