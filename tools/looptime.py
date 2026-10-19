"""抓图卡顿 / 画面陈旧 的对照实验。

现象：遛鱼时日志里偶尔出现「两帧只差 0.1s，鱼的位置却跳了 40°」，
以及「整段停顿一两秒」。这到底是

  A. 我们自己的循环卡住了（PrintWindow 在等游戏窗口响应），还是
  B. 画面本身是陈旧的（游戏被别的程序挤到掉帧，PrintWindow 返回上一帧缓存）

这个脚本同一时刻用两种方式各抓一张，逐帧对比：

    pw      : PrintWindow(PW_RENDERFULLCONTENT)，抗遮挡
    screen  : 屏幕 BitBlt，不问游戏窗口，快但会被遮挡

对比指标：
  * 各阶段耗时（抓图 / 分析 / 找鱼）的中位、p90、最大
  * 「这一帧的画面和上一帧一模一样」的比例（画面陈旧率）
  * 两种方式测出来的鱼位置序列 —— 若 pw 的位置序列在跳而 screen 是平滑的，
    就说明是 PrintWindow 拿了陈旧缓存；若两者一起跳，那是游戏自己掉帧。

用法:
    python tools/looptime.py --secs 45
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import arc, fish, hook, win  # noqa: E402


def pct(a, p):
    return float(np.percentile(a, p)) if len(a) else 0.0


def fp(img) -> bytes:
    """画面指纹：降采样灰度，用来判断两帧是否完全一样。"""
    a = np.asarray(img.convert("L").resize((138, 248), 0), dtype=np.uint8)
    return a.tobytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=45.0)
    ap.add_argument("--casts", type=int, default=6)
    ap.add_argument("--cast-secs", type=float, default=1.5)
    ap.add_argument("--title", default="墨迹大侠")
    a = ap.parse_args()

    w = win.find_window(a.title)
    pi = win.PostInput(w)
    st_pw: dict[str, list] = {"grab": [], "ana": [], "find": []}
    st_sc: dict[str, list] = {"grab": [], "ana": [], "find": []}
    gaps: list[float] = []
    same = {"pw": 0, "screen": 0}
    tot = {"pw": 0, "screen": 0}
    ts: list[tuple[float, str, float | None, tuple]] = []   # (t, 谁, 鱼φ, zone)
    lastfp = {"pw": None, "screen": None}
    lastt = 0.0
    casts = 0
    t0 = time.time()
    while time.time() - t0 < a.secs:
        out = {}
        for name, mode in (("pw", "pw"), ("screen", "screen")):
            t1 = time.perf_counter()
            img = win.grab_window(w, mode=mode)
            t2 = time.perf_counter()
            s = arc.analyze(img)
            t3 = time.perf_counter()
            fh = fish.find_fish(img, s.geom, topk=1) if (s.present and s.geom) else []
            t4 = time.perf_counter()
            (st_pw if name == "pw" else st_sc)["grab"].append((t2 - t1) * 1000)
            (st_pw if name == "pw" else st_sc)["ana"].append((t3 - t2) * 1000)
            (st_pw if name == "pw" else st_sc)["find"].append((t4 - t3) * 1000)
            out[name] = (s, fh, img)

        st = out["pw"][0]
        if not st.present:
            if casts < a.casts:
                casts += 1
                pi.down(*hook.DIAL)
                time.sleep(a.cast_secs)
                pi.up(*hook.DIAL)
                time.sleep(0.8)
            lastt = 0.0
            lastfp = {"pw": None, "screen": None}
            continue

        tnow = time.perf_counter()
        if lastt:
            gaps.append((tnow - lastt) * 1000)
        lastt = tnow
        el = tnow - t0
        for name in ("pw", "screen"):
            f = fp(out[name][2])
            tot[name] += 1
            if f == lastfp[name]:
                same[name] += 1
            lastfp[name] = f
            s, fh = out[name][0], out[name][1]
            ts.append((el, name, fh[0].phi if fh else None, s.zone_span))

    path = Path(__file__).resolve().parents[1] / "shots" / "looptime.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"抛竿 {casts} 次  时长 {time.time()-t0:.1f}s\n")
        for name, stx in (("pw(PrintWindow)", st_pw), ("screen(BitBlt)", st_sc)):
            f.write(f"--- {name}\n")
            for k in ("grab", "ana", "find"):
                v = stx[k]
                f.write("  %-4s 中位 %6.1f ms  p90 %7.1f  p99 %7.1f  最大 %8.1f\n"
                        % (k, pct(v, 50), pct(v, 90), pct(v, 99), max(v) if v else 0))
            f.write("  画面与上一帧相同: %d/%d = %.1f%%\n"
                    % (same[name.split("(")[0]], tot[name.split("(")[0]],
                       100.0 * same[name.split("(")[0]] / max(tot[name.split("(")[0]], 1)))
        f.write("  循环帧间隔 中位 %.1f ms  p90 %.1f  最大 %.1f\n"
                % (pct(gaps, 50), pct(gaps, 90), max(gaps) if gaps else 0))
        f.write("\n每帧 (t, fish_pw, fish_screen, zone_pw, zone_screen)\n")
        i = 0
        while i < len(ts):
            if ts[i][1] == "pw":
                b = ts[i + 1] if i + 1 < len(ts) and ts[i + 1][1] == "screen" else None
                f.write("  %7.3f  pw=%s  sc=%s   z_pw=%3.0f~%3.0f  z_sc=%3.0f~%3.0f\n" % (
                    ts[i][0],
                    ("%3.0f" % ts[i][2]) if ts[i][2] is not None else " --",
                    ("%3.0f" % b[2]) if (b and b[2] is not None) else " --",
                    ts[i][3][0], ts[i][3][1],
                    b[3][0] if b else -1, b[3][1] if b else -1))
            i += 2
    print("写入", path)
    txt = path.read_text(encoding="utf-8")
    print("\n".join(txt.splitlines()[:16]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
