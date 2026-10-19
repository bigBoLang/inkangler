"""裁剪/放大截图，用于看清 UI 文字。

用法:
    python tools/crop.py <图片> <x> <y> <w> <h> [scale] [输出名]
    python tools/crop.py <图片> --preset all     # 输出一组常用区域
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CROPS = ROOT / "shots" / "crops"

# 相对 552x991 窗口截图的关注区域
PRESETS = {
    "top": (0, 40, 552, 230),
    "left": (0, 230, 120, 480),
    "right": (420, 230, 132, 700),
    "bottom": (0, 700, 552, 291),
    "center": (150, 640, 300, 260),
}


def do_crop(src: Path, box, scale: float, name: str) -> Path:
    x, y, w, h = box
    img = Image.open(src).crop((x, y, x + w, y + h))
    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    CROPS.mkdir(parents=True, exist_ok=True)
    out = CROPS / f"{src.stem}__{name}.png"
    img.save(out)
    return out


def main() -> int:
    src = Path(sys.argv[1])
    if not src.is_absolute():
        src = ROOT / src
    if sys.argv[2] == "--preset":
        which = sys.argv[3] if len(sys.argv) > 3 else "all"
        items = PRESETS.items() if which == "all" else [(which, PRESETS[which])]
        for name, box in items:
            print(do_crop(src, box, 3, name))
        return 0
    x, y, w, h = (int(v) for v in sys.argv[2:6])
    scale = float(sys.argv[6]) if len(sys.argv) > 6 else 3
    name = sys.argv[7] if len(sys.argv) > 7 else f"{x}_{y}_{w}_{h}"
    print(do_crop(src, (x, y, w, h), scale, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
