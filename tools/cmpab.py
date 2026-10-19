"""把两次运行的日志并排比一下：控制律 A/B 用。

只读日志，不动游戏。会解析每个「--- 第 N 次 ---」到「结果: …」之间的小结，
以及最后的「跟踪统计 / 耗时统计」。

用法:
    python tools/cmpab.py shots/ab2_off.txt shots/ab2_on.txt
    python tools/cmpab.py shots/ab2_off.txt shots/ab2_on.txt --names 基准 前馈
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROUND = re.compile(r"---\s*第\s*(\d+)\s*次\s*---")
RESULT = re.compile(r"结果:\s*(\w+)")
STAT = re.compile(
    r"跟踪统计:\s*n=(\d+)\s+平均\|误差\|=([\d.]+)°\s+平均滞后=([+-]?[\d.]+)°"
    r"\s+平均深度=([\d.]+)(?:\s+丢帧(\d+)\s+外推(\d+))?")
OVERALL = re.compile(
    r"本轮跟踪总览:\s*有效帧\s*(\d+)\s+平均\|误差\|\s*([\d.]+)°"
    r"\s+平均滞后\s*([+-][\d.]+)°\s+平均深度\s*([\d.]+)")
TIMING = re.compile(r"耗时统计:.*")
COUNT = re.compile(r"汇总:\s*(\{.*\})")


def parse(path: Path):
    txt = path.read_bytes().decode("utf-8", "replace")
    rounds = []          # (n, result, stats...)
    cur = None
    for line in txt.splitlines():
        m = ROUND.search(line)
        if m:
            cur = {"n": int(m.group(1)), "result": "", "n_frame": 0,
                   "abs_e": 0.0, "lag": 0.0, "depth": 0.0}
            continue
        m = RESULT.search(line)
        if m and cur is not None:
            cur["result"] = m.group(1)
            continue
        m = STAT.search(line)
        if m and cur is not None:
            cur["n_frame"] = int(m.group(1))
            cur["abs_e"] = float(m.group(2))
            cur["lag"] = float(m.group(3))
            cur["depth"] = float(m.group(4))
            cur["rej"] = int(m.group(5) or 0)
            cur["pred"] = int(m.group(6) or 0)
            rounds.append(cur)
            cur = None
    ov = OVERALL.search(txt)
    tm = TIMING.search(txt)
    cn = COUNT.search(txt)
    return {
        "rounds": rounds,
        "overall": tuple(float(x) if i else int(x) for i, x in enumerate(ov.groups()))
        if ov else None,
        "timing": tm.group(0) if tm else "",
        "counts": cn.group(1) if cn else "",
    }


def summarize(name: str, d) -> None:
    rs = d["rounds"]
    print(f"=== {name}")
    if not rs:
        print("  没有解析到轮次")
        return
    tot = sum(r["n_frame"] for r in rs)
    print("  轮次 %d  有效帧 %d" % (len(rs), tot))
    if tot:
        print("  帧数加权：|误差| %.1f°  滞后 %+.1f°  深度 %.3f"
              % (sum(r["abs_e"] * r["n_frame"] for r in rs) / tot,
                 sum(r["lag"] * r["n_frame"] for r in rs) / tot,
                 sum(r["depth"] * r["n_frame"] for r in rs) / tot))
    print("  轮均值  ：|误差| %.1f°  深度 %.3f"
          % (sum(r["abs_e"] for r in rs) / len(rs),
             sum(r["depth"] for r in rs) / len(rs)))
    for k in ("caught", "escaped", "gone", "stall", "timeout", "dialog"):
        c = sum(1 for r in rs if r["result"] == k)
        if c:
            print("    %-8s %d" % (k, c))
    if d["overall"]:
        n, e, lag, dep = d["overall"]
        print("  总览行：有效帧 %d  |误差| %.1f°  深度 %.2f" % (n, e, dep))
    if d["counts"]:
        print("  汇总：", d["counts"])
    if d["timing"]:
        print("  " + d["timing"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--names", nargs="*", default=[])
    ap.add_argument("--detail", action="store_true", help="逐轮列出")
    a = ap.parse_args()

    for i, p in enumerate(a.logs):
        name = a.names[i] if i < len(a.names) else Path(p).stem
        d = parse(Path(p))
        summarize(name, d)
        if a.detail:
            print("    轮  结果      帧    |误差|  深度  外推")
            for r in d["rounds"]:
                print("    %3d %-9s %4d  %6.1f  %.2f  %4d"
                      % (r["n"], r["result"] or "?", r["n_frame"],
                         r["abs_e"], r["depth"], r.get("pred", 0)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
