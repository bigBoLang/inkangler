"""点击 UI 元素并截图，用于观察状态变化 / 标定验证。

用法:
    python tools/click.py auto                # 点「自动」并截图
    python tools/click.py fishbox 鱼箱         # 点「鱼箱」，截图名带备注
    python tools/click.py --at 270,810        # 直接点任意坐标
    python tools/click.py list                # 打印所有元素坐标
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import ui, win  # noqa: E402

TITLE = "墨迹大侠"
SHOTS = Path(__file__).resolve().parents[1] / "shots"


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "list":
        for e in sorted(ui.ELEMENTS.values(), key=lambda e: (e.y, e.x)):
            print(f"{e.key:14s} ({e.x:3d},{e.y:3d})  {e.desc}")
        return 0

    w = win.find_window(TITLE)
    ui.assert_ref_size(w.frame_size)

    if args[0] == "--at":
        x, y = (int(v) for v in args[1].split(","))
        note = args[2] if len(args) > 2 else f"at_{x}_{y}"
    else:
        e = ui.get(args[0])
        x, y = e.x, e.y
        note = args[1] if len(args) > 1 else e.key

    sx, sy = win.click_image(w, x, y)
    print(f"clicked {note!r} image=({x},{y}) screen=({sx},{sy})")

    time.sleep(1.2)
    img = win.grab_window(w)
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / f"{time.strftime('%H%M%S')}_click_{note}.png"
    img.save(path)
    print(f"saved={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
