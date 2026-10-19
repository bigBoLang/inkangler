"""在截图上叠加「绝对坐标」网格，可指定裁剪区域，用于精确标定 UI 位置。

用法:
    python tools/grid.py <图片>                       # 全图，step=50
    python tools/grid.py <图片> --box 0 850 260 141    # 裁剪区域，绝对坐标网格
    python tools/grid.py <图片> --box ... --step 10 --scale 4
    python tools/grid.py <图片> --marks 190,900,505,428   # 额外画标记点并编号
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def annotate(src: Path, box=None, step=50, scale=2, marks=(), out_suffix=""):
    full = Image.open(src).convert("RGB")
    if box:
        x, y, w, h = box
        img = full.crop((x, y, x + w, y + h))
    else:
        x, y = 0, 0
        img = full
    w, h = img.size
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    d = ImageDraw.Draw(img)

    def sx(v):
        return (v - x) * scale

    def sy(v):
        return (v - y) * scale

    for gx in range((x // step) * step, x + w + step, step):
        if gx < x:
            continue
        major = (gx % (step * 5) == 0)
        col = (255, 0, 0) if major else (255, 170, 170)
        d.line([(sx(gx), 0), (sx(gx), h * scale)], fill=col, width=2 if major else 1)
        if major or step >= 20:
            d.text((sx(gx) + 3, 3), str(gx), fill=(180, 0, 0))
    for gy in range((y // step) * step, y + h + step, step):
        if gy < y:
            continue
        major = (gy % (step * 5) == 0)
        col = (0, 90, 255) if major else (160, 200, 255)
        d.line([(0, sy(gy)), (w * scale, sy(gy))], fill=col, width=2 if major else 1)
        if major or step >= 20:
            d.text((3, sy(gy) + 3), str(gy), fill=(0, 0, 180))

    for i, (mx, my) in enumerate(marks):
        cx, cy = sx(mx), sy(my)
        d.line([(cx - 14, cy), (cx + 14, cy)], fill=(0, 200, 0), width=3)
        d.line([(cx, cy - 14), (cx, cy + 14)], fill=(0, 200, 0), width=3)
        d.text((cx + 8, cy - 24), f"#{i} ({mx},{my})", fill=(0, 140, 0))

    tag = f"__box{box[0]}_{box[1]}" if box else ""
    out = src.with_name(src.stem + f"__grid{step}{tag}{out_suffix}.png")
    img.save(out)
    return out


def main() -> int:
    args = sys.argv[1:]
    src = Path(args[0])
    if not src.is_absolute():
        src = ROOT / src
    box = step = scale = None
    marks = []
    i = 1
    while i < len(args):
        if args[i] == "--box":
            box = tuple(int(v) for v in args[i + 1:i + 5]); i += 5
        elif args[i] == "--step":
            step = int(args[i + 1]); i += 2
        elif args[i] == "--scale":
            scale = int(args[i + 1]); i += 2
        elif args[i] == "--marks":
            flat = [int(v) for v in args[i + 1].split(",")]
            marks = list(zip(flat[0::2], flat[1::2])); i += 2
        else:
            i += 1
    print(annotate(src, box, step or 50, scale or 2, marks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
