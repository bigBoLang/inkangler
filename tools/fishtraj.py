"""从 bot 日志里提取「鱼」的运动轨迹，找规律。

日志行格式（每帧一行）::

    0.08s zone=  0~ 71 zc= 36 prog=24.4% fish= 22 e= -13 reel IN

用法:
    python tools/fishtraj.py shots/bot_run_final.txt               # 总览
    python tools/fishtraj.py shots/bot_run_final.txt --dump 4      # 画第 4 次的时间序列
    python tools/fishtraj.py shots/bot_run_final.txt --turn        # 统计折返点
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

LINE = re.compile(
    r"^\s*(?P<t>[\d.]+)s\s+"
    r"zone=\s*(?P<z0>-?\d+)~\s*(?P<z1>-?\d+)\s+"
    r"zc=\s*(?P<zc>-?\d+)\s+"
    r"prog=\s*(?P<prog>[\d.]+)%\s+"
    r"fish=\s*(?P<fish>--|\d+)\s+"
    r"e=\s*(?P<e>--|[+-]?\d+)"
    r"(?:\s+v=\s*(?P<v>--|[+-]?\d+))?\s*"
    r"(?P<act>HOLD|reel)?"
)
ATTEMPT = re.compile(r"---\s*第\s*(\d+)\s*次")


class Track:
    __slots__ = ("n", "t", "fish", "z0", "z1", "zc", "prog", "act", "result")

    def __init__(self, n: int):
        self.n = n
        self.t, self.fish, self.z0, self.z1, self.zc, self.prog, self.act = [], [], [], [], [], [], []
        self.result = ""

    def add(self, t, fish, z0, z1, zc, prog, act):
        self.t.append(t); self.fish.append(fish); self.z0.append(z0)
        self.z1.append(z1); self.zc.append(zc); self.prog.append(prog)
        self.act.append(act)

    def arrays(self):
        f = np.array([np.nan if v is None else v for v in self.fish], float)
        return (np.array(self.t), f, np.array(self.zc, float), np.array(self.prog, float))


def parse(paths: list[Path]) -> list[Track]:
    tracks: list[Track] = []
    cur: Track | None = None
    res = re.compile(r"结果:\s*(\S+)")
    for p in paths:
        for raw in p.read_bytes().decode("utf-8", "replace").splitlines():
            m = ATTEMPT.search(raw)
            if m:
                cur = Track(int(m.group(1)))
                tracks.append(cur)
                continue
            m2 = res.search(raw)
            if m2 and cur is not None:
                cur.result = m2.group(1)
                continue
            m = LINE.match(raw)
            if not m or cur is None:
                continue
            fa = m.group("fish")
            cur.add(float(m.group("t")), None if fa == "--" else float(fa),
                    float(m.group("z0")), float(m.group("z1")), float(m.group("zc")),
                    float(m.group("prog")) / 100.0, m.group("act") or "")
    return tracks


def vel(t, f):
    """相邻帧速度数组（与 f 等长，首帧为 nan）。"""
    v = np.full(len(f), np.nan)
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        if 0 < dt <= 0.5 and not np.isnan(f[i]) and not np.isnan(f[i - 1]):
            v[i] = (f[i] - f[i - 1]) / dt
    return v


def medfilt(f, k=5):
    """中值滤波，滤掉 ±1° 的量化噪声。k 必须是奇数，k=5 约 0.25s。"""
    out = f.copy()
    n = len(f)
    h = k // 2
    for i in range(n):
        a, b = max(0, i - h), min(n, i + h + 1)
        seg = f[a:b]
        seg = seg[~np.isnan(seg)]
        out[i] = np.median(seg) if len(seg) else np.nan
    return out


def turning_points(t, f, min_move=6.0, v_thr=8.0, k=5):
    """找折返点：先中值滤波，再按速度符号切段，要求每段至少走了 min_move 度。"""
    fm = medfilt(f, k)
    v = vel(t, fm)
    tps = []
    dirn = 0
    run_lo = run_hi = None
    run_lo_t = run_hi_t = None

    def flush(dirn, lo, hi, lo_t, hi_t):
        if dirn < 0:
            return (lo_t, lo, -1)
        return (hi_t, hi, +1)

    for i in range(len(fm)):
        if np.isnan(fm[i]) or np.isnan(v[i]):
            continue
        d = 1 if v[i] > v_thr else (-1 if v[i] < -v_thr else 0)
        if d == 0:
            continue
        if dirn == 0:
            dirn = d
            run_lo = run_hi = fm[i]
            run_lo_t = run_hi_t = t[i]
            continue
        if fm[i] < run_lo:
            run_lo, run_lo_t = fm[i], t[i]
        if fm[i] > run_hi:
            run_hi, run_hi_t = fm[i], t[i]
        if d != dirn:
            if abs(run_hi - run_lo) >= min_move:
                tps.append(flush(dirn, run_lo, run_hi, run_lo_t, run_hi_t))
            dirn = d
            run_lo = run_hi = fm[i]
            run_lo_t = run_hi_t = t[i]
    return tps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--dump", type=int, default=None)
    ap.add_argument("--turn", action="store_true")
    ap.add_argument("--min-frames", type=int, default=20)
    a = ap.parse_args()

    paths: list[Path] = []
    for g in a.logs:
        paths.extend(Path(x) for x in glob.glob(g))
    tracks = parse(paths)
    print(f"共 {len(tracks)} 次尝试")

    if a.dump is not None:
        for tr in tracks:
            if tr.n != a.dump:
                continue
            t, f, zc, p = tr.arrays()
            print(f"--- 第 {tr.n} 次  result={tr.result}  {len(t)} 帧")
            step = max(1, len(t) // 120)
            for i in range(0, len(t), step):
                fs = " --" if np.isnan(f[i]) else "%3.0f" % f[i]
                print(f"{t[i]:7.2f}s fish={fs} zc={zc[i]:4.0f} prog={p[i]*100:5.1f} {tr.act[i]}")
            v = vel(t, f)
            vv = v[~np.isnan(v)]
            if len(vv):
                print(f"  速度 mean={vv.mean():+.1f} med={np.median(vv):+.1f} "
                      f"p5={np.percentile(vv,5):+.0f} p95={np.percentile(vv,95):+.0f} "
                      f"右{(vv>6).mean()*100:.0f}% 左{(vv<-6).mean()*100:.0f}% 停{(np.abs(vv)<=6).mean()*100:.0f}%")
            print("  折返点:", [(round(tt, 2), round(fp, 1)) for tt, fp, _ in turning_points(t, f)])
        return 0

    # 总览
    speeds, ups, downs, tps_all = [], [], [], []
    for tr in tracks:
        t, f, zc, p = tr.arrays()
        if np.sum(~np.isnan(f)) < a.min_frames:
            continue
        v = vel(t, f)
        vv = v[~np.isnan(v)]
        speeds.extend(vv.tolist())
        tps = turning_points(t, f)
        tps_all.extend(tps)
        span = np.nanmax(f) - np.nanmin(f)
        print(f"第 {tr.n:3d} 次 {tr.result:8s} 帧{len(t):5d} "
              f"fish[{np.nanmin(f):5.1f},{np.nanmax(f):5.1f}] 跨{span:5.1f}° "
              f"prog峰{np.nanmax(p)*100:5.1f}% 折返{len(tps)}次")
    v = np.array(speeds)
    print()
    print("速度(°/s): mean=%+.1f med=%+.1f p5=%+.0f p95=%+.0f  右%.0f%% 左%.0f%% 停%.0f%%" % (
        v.mean(), np.median(v), np.percentile(v, 5), np.percentile(v, 95),
        100 * (v > 6).mean(), 100 * (v < -6).mean(), 100 * (np.abs(v) <= 6).mean()))
    if tps_all:
        ext = np.array([fp for _, fp, _ in tps_all])
        print("\n折返点 φ 统计 (%d 个):" % len(ext))
        print("  min=%.0f p10=%.0f p25=%.0f med=%.0f p75=%.0f p90=%.0f max=%.0f" % (
            ext.min(), np.percentile(ext, 10), np.percentile(ext, 25), np.median(ext),
            np.percentile(ext, 75), np.percentile(ext, 90), ext.max()))
        print("  直方图:", np.histogram(ext, bins=np.arange(0, 181, 20))[0].tolist())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
