"""离线回放：把日志里真实的「鱼」轨迹喂给控制器，比较旧/新控制律的跟踪误差。

鱼的位置是游戏自己驱动的，与我们怎么控蓝条无关，所以拿记录下来的鱼轨迹
重放是合法的。唯一的近似是要人为设一个「感知延迟」L：控制器在 t 时刻
看到的是 t−L 的鱼位置。旧控制律补不掉这个延迟，新控制律用前馈+提前量补。

用法:
    python tools/simctl.py shots/bot_log12.txt
    python tools/simctl.py shots/bot_log*.txt --latency 0.13
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import hook  # noqa: E402
from tools.fishtraj import parse  # noqa: E402

TRACK = 176.0


def simulate(fish_t, fish_p, zlen, dt, latency, kp, lead, feedforward,
             start=60.0, mode: str | None = None, ff_window: float | None = None,
             end_damp: float = 0.0, end_margin: float = 15.0):
    """返回 (平均|误差|, 平均滞后, 平均深度, prog 估计)。

    mode:
      'p'    纯比例（旧）
      'lead' 只用提前量瞄准（前馈速度不直接加）
      'ff'   提前量 + 鱼速前馈（默认，feedforward=True 时）

    end_damp: 0 表示关；否则鱼贴着轨道端头、还在往外游时，
              把前馈速度乘以这个系数（实测 2/3 的折返都发生在端头附近，
              先卸掉前馈，折返那一下就不会把蓝条甩过头）。
    """
    if mode is None:
        mode = "ff" if feedforward else "p"
    n = len(fish_p)
    z = start                      # 蓝条中心（内部真实值）
    acc = 0.5
    trk = hook.FishTracker() if ff_window is None else hook.FishTracker(window=ff_window)
    abs_e = lag = depth = 0.0
    cnt = 0
    prog = 0.35
    half = zlen / 2.0
    lo, hi = half, TRACK - half
    for i in range(n):
        t = fish_t[i]
        f_now = fish_p[i]
        # 控制器只能看到 latency 之前的位置
        j = int(np.searchsorted(fish_t, t - latency, side="right")) - 1
        f_see = None if j < 0 else float(fish_p[j])
        v_ff = trk.update(t, f_see)
        if f_see is None:
            continue
        zc = min(max(z, lo), hi)   # 游戏把蓝条夹在轨道里，读到的是夹住后的中心
        if end_damp and v_ff:
            if (v_ff > 0 and f_see > TRACK - end_margin) or \
               (v_ff < 0 and f_see < end_margin):
                v_ff *= end_damp
        if mode == "p":
            aim, base = f_see, 0.0
        elif mode == "lead":
            aim, base = f_see + v_ff * lead, 0.0
        else:
            aim, base = f_see + v_ff * lead, v_ff
        v = max(hook.V_LO, min(hook.V_HI, base + kp * (aim - zc)))
        acc += (v - hook.REL_V) / (hook.HOLD_V - hook.REL_V)
        if acc >= 1.0:
            acc -= 1.0
            held = True
        else:
            held = False
        if i + 1 < n:
            d = fish_t[i + 1] - fish_t[i]
            z += (hook.HOLD_V if held else hook.REL_V) * d
            z = min(max(z, lo), hi)
        # 覆盖深度按夹住后的中心算
        cov = abs(f_now - zc) <= half
        abs_e += abs(f_now - zc)
        lag += f_now - zc
        depth += max(0.0, 1.0 - abs(f_now - zc) / half) if cov else 0.0
        cnt += 1
        # 粗略的进度模型：居中 +16%/s，贴边少一点，没罩住 -4.6%/s
        if cov:
            prog += (2.0 + 14.0 * max(0.0, 1.0 - abs(f_now - zc) / half)) * d if i + 1 < n else 0
        else:
            prog -= 4.6 * d if i + 1 < n else 0
        prog = min(max(prog, 0.0), 94.3)
    return abs_e / cnt, lag / cnt, depth / cnt, prog


def sweep_grid(prepped, latency, windows, kps, leads, ff_window):
    """扫 kp × lead 的格子（都用前馈），顺便对比不同 FF 窗口。"""
    for w in windows:
        print(f"  FF 窗口 {w:.2f}s")
        print("    lead\\kp " + "".join("%8.1f" % k for k in kps))
        for ld in leads:
            cells = []
            for k in kps:
                es, ds = [], []
                for n, r, zl, g, fg in prepped:
                    dt = float(np.median(np.diff(g)))
                    e, l, d, p = simulate(g, fg, zl, dt, latency, k, ld, False,
                                          mode="ff", ff_window=w)
                    es.append(e); ds.append(d)
                cells.append("%6.2f/%.2f" % (np.mean(es), np.mean(ds)))
            print("    %5.2f  " % ld + "".join("%8s" % c for c in cells))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--latency", type=float, default=0.13)
    ap.add_argument("--min-frames", type=int, default=40)
    ap.add_argument("--kp", type=float, default=hook.KP)
    ap.add_argument("--lead", type=float, default=hook.T_LEAD)
    ap.add_argument("--sweep", action="store_true",
                    help="扫 FF 窗口 / 端头前馈抑制 的格子，找更好的参数")
    ap.add_argument("--grid", action="store_true",
                    help="扫 kp × lead 的格子（前馈版），格式 |e|/深度")
    a = ap.parse_args()

    paths: list[Path] = []
    for g in a.logs:
        paths.extend(Path(x) for x in glob.glob(g))
    tracks = parse(paths)

    old_e, new_e = [], []
    old_d, new_d = [], []
    lead_e, lead_d = [], []
    rows = []
    prepped = []
    for tr in tracks:
        t, f, zc, p = tr.arrays()
        m = ~np.isnan(f)
        if m.sum() < a.min_frames:
            continue
        # 鱼轨迹重采样到均匀网格
        tt = t[m]
        ff = f[m]
        dts = np.diff(tt)
        dts = dts[(dts > 0) & (dts < 0.4)]
        if len(dts) < 5:
            continue
        dt = float(np.median(dts))
        grid = np.arange(tt[0], tt[-1], dt)
        fg = np.interp(grid, tt, ff)
        zlen = float(np.median(np.array(tr.z1, float) - np.array(tr.z0, float)))
        if zlen <= 5:
            zlen = 50.0
        prepped.append((tr.n, tr.result, zlen, grid, fg))

        if a.sweep or a.grid:
            continue
        oe, ol, od, op = simulate(grid, fg, zlen, dt, a.latency, a.kp, a.lead, False, mode="p")
        ne, nl, nd, npg = simulate(grid, fg, zlen, dt, a.latency, a.kp, a.lead, True, mode="ff")
        le, ll, ld, lp = simulate(grid, fg, zlen, dt, a.latency, a.kp, a.lead, False, mode="lead")
        old_e.append(oe); new_e.append(ne)
        old_d.append(od); new_d.append(nd)
        lead_e.append(le); lead_d.append(ld)
        rows.append((tr.n, tr.result, zlen, oe, ne, le, od, nd, ld))

    if a.grid:
        print(f"离线扫参 kp×lead（前馈，延迟 {a.latency}s，{len(prepped)} 段轨迹）")
        return sweep_grid(prepped, a.latency, (0.35, 0.55), (3.0, 4.0, 5.0, 6.0),
                          (0.0, 0.10, 0.15, 0.25, 0.35), 0.35)

    if a.sweep:
        print(f"离线扫参（延迟 {a.latency}s, kp={a.kp}, lead={a.lead}, "
              f"{len(prepped)} 段轨迹）")
        print("  窗口s  端头抑制   平均|e|    深度    prog")
        for w in (0.25, 0.35, 0.45, 0.55, 0.75):
            for ed in (0.0, 0.3, 0.5):
                es, ds, ps = [], [], []
                for n, r, zl, grid, fg in prepped:
                    dt = float(np.median(np.diff(grid)))
                    e, l, d, pg = simulate(grid, fg, zl, dt, a.latency, a.kp, a.lead,
                                           False, mode="ff", ff_window=w, end_damp=ed)
                    es.append(e); ds.append(d); ps.append(pg)
                print("  %5.2f   %5.2f    %6.2f   %.3f   %5.1f"
                      % (w, ed, np.mean(es), np.mean(ds), np.mean(ps)))
        print()
        es, ds, ps = [], [], []
        for n, r, zl, grid, fg in prepped:
            dt = float(np.median(np.diff(grid)))
            e, l, d, pg = simulate(grid, fg, zl, dt, a.latency, a.kp, a.lead,
                                   False, mode="p")
            es.append(e); ds.append(d); ps.append(pg)
        print("  参考 纯比例       |e|=%.2f  深度=%.3f  prog=%.1f"
              % (np.mean(es), np.mean(ds), np.mean(ps)))
        return 0

    print("第 N 次  结果      区宽  旧|e|  前馈|e| 提前|e|   旧深度 前馈深 提前深")
    for n, r, zl, oe, ne, le, od, nd, ld in rows:
        print(f"{n:5d}  {r:8s} {zl:5.0f}  {oe:6.1f} {ne:6.1f} {le:6.1f}   "
              f"{od:.2f}  {nd:.2f}  {ld:.2f}")
    print()
    print(f"平均(延迟 {a.latency}s, kp={a.kp}, lead={a.lead}):")
    print(f"  纯比例      |e|={np.mean(old_e):5.2f}°  深度={np.mean(old_d):.3f}")
    print(f"  提前+前馈   |e|={np.mean(new_e):5.2f}°  深度={np.mean(new_d):.3f}")
    print(f"  只提前量    |e|={np.mean(lead_e):5.2f}°  深度={np.mean(lead_d):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
