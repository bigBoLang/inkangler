"""把一组截图拼成联系表（contact sheet），便于一次性查看整个时序。

用法:
    python tools/montage.py "shots/*T4*" --cols 5 --out shots/montage_T4.png
    python tools/montage.py --list "shots/*T4*"
"""
from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--scale", type=float, default=0.42)
    ap.add_argument("--out", default=None)
    ap.add_argument("--crop", default=None, help="裁剪区域 x0,y0,x1,y1（只看重点区域）")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    files = sorted(glob.glob(str(ROOT / a.pattern)) if not Path(a.pattern).is_absolute()
                   else glob.glob(a.pattern))
    if not files:
        print("没有匹配的文件:", a.pattern)
        return 1
    if a.list:
        for f in files:
            print(Path(f).name)
        return 0

    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    thumbs = []
    for f in files:
        img = Image.open(f).convert("RGB")
        if crop:
            img = img.crop(crop)
        img = img.resize((int(img.width * a.scale), int(img.height * a.scale)),
                         Image.LANCZOS)
        thumbs.append((Path(f).stem, img))

    tw, th = thumbs[0][1].size
    cols = a.cols
    rows = (len(thumbs) + cols - 1) // cols
    pad = 14
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad * 2) + pad),
                      (28, 28, 32))
    d = ImageDraw.Draw(sheet)
    for i, (name, img) in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + pad * 2)
        sheet.paste(img, (x, y))
        label = name.split("_", 1)[1] if "_" in name else name
        d.text((x + 2, y + th + 2), label, fill=(230, 230, 230))

    out = Path(a.out) if a.out else ROOT / "shots" / "montage.png"
    sheet.save(out)
    print(f"{len(thumbs)} 帧 -> {out}  ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
