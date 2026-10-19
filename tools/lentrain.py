"""用历史结算截图训练「长度」数字模板（`mjd/digit_bank.npz`）。

截图来自 `shots/dialogs/`（遛鱼脚本每次弹结算窗都会自动存一张）。
`d000~d035` 这 36 张的长度已经人工读过，写在下面的 TRUTH 里；以后再攒到
新截图，只要把它加进 `shots/dialogs/labels.json`（{文件名: 长度}）就能一起训练。

用法:
    python tools/lentrain.py            # 训练 + 自检（把每张都重新认一遍）
    python tools/lentrain.py --show     # 顺便打印每张的切字结果
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import length  # noqa: E402
from tools.fishdbg import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIA = ROOT / "shots" / "dialogs"

# 人工读出来的 d000~d035（顺序即文件序号）
TRUTH = """62 5.3 76.6 61.4 31.2 3.4 12.4 14.1 61.9 32.8 62.5 18.9 32.6 7
5.4 45.2 5.1 41 63 60.6 45.8 2.8 31.4 50.3 34.7 31.4 59.5 32.9 35.8 61.4 30
35.6 102.5 59 5.4 3.7""".split()


def labels() -> dict[str, str]:
    """文件名 → 长度。内置 d000~d035，再叠加 labels.json。"""
    out = {f"d{i:03d}.png": t for i, t in enumerate(TRUTH)}
    extra = DIA / "labels.json"
    if extra.exists():
        out.update(json.loads(extra.read_text(encoding="utf-8")))
    return out


def chars(img) -> list[np.ndarray]:
    """切出这一行的每个字（含前后缀，交给调用方对齐）。"""
    a = length.rgb_of(img)
    band = a[length.BAND_Y0:length.BAND_Y1, length.BAND_X0:length.BAND_X1]
    m = length.gold_mask(band)
    out = []
    for x0, x1 in length.col_runs(m):
        g = length.glyph(m, x0, x1)
        if g is not None:
            out.append(g)
    return out


def build(show: bool = False) -> dict[str, np.ndarray]:
    lab = labels()
    bank: dict[str, list[np.ndarray]] = defaultdict(list)
    skipped = []
    for fn, t in sorted(lab.items()):
        p = DIA / fn
        if not p.exists():
            continue
        gs = chars(load(p))
        want = ["长", "度", ":"] + list(t) + ["C", "M"]
        if len(gs) != len(want):
            skipped.append((fn, t, len(gs), len(want)))
            continue
        for ch, g in zip(want, gs):
            bank[ch].append(g)
    if show:
        for fn, t, n, w in skipped:
            print(f"  跳过 {fn} {t}CM：切出 {n} 段，期望 {w}")
    print("  模板数量:", {k: len(v) for k, v in sorted(bank.items())})
    arrs = {k: np.stack(v) for k, v in bank.items()}
    np.savez(length.BANK, **arrs)
    print(f"  已写入 {length.BANK.relative_to(ROOT)}（{sum(len(v) for v in arrs.values())} 个字形）")
    return arrs


def check(show: bool = False) -> int:
    lab = labels()
    bad = 0
    for fn, t in sorted(lab.items()):
        p = DIA / fn
        if not p.exists():
            continue
        v, text, dist = length.read_length_detail(load(p))
        ok = v is not None and abs(v - float(t)) < 0.051
        if not ok:
            bad += 1
            print(f"  !! {fn} 期望 {t} 实得 {v}  字串={text!r} 最差距离={dist:.1f}")
        elif show:
            print(f"  {fn} {text} -> {v}  (最差 {dist:.1f})")
    n = sum(1 for fn in lab if (DIA / fn).exists())
    print(f"  自检: {n - bad}/{n} 正确")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    build(show=a.show)
    # 训练完把缓存清掉，用新模板重新认
    length._bank_cache = None
    return 1 if check(show=a.show) else 0


if __name__ == "__main__":
    raise SystemExit(main())
