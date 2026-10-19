"""生成启动闪屏 assets/splash.png（PyInstaller --splash 用）。

闪屏是 exe 解压前就能显示的唯一东西：单文件模式要把 78MB 解到临时目录，
这几秒里用户什么都看不到，很容易以为没反应又双击一次。

    python tools/make_splash.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "splash.png"

sys.path.insert(0, str(ROOT))
from mjd.version import VERSION  # noqa: E402

W, H = 460, 240
BG = (16, 19, 24)
CARD = (23, 26, 33)
ACCENT = (61, 123, 253)
TEXT = (230, 232, 238)
MUTED = (139, 147, 167)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhbd.ttc"]
    for n in names:
        p = Path("C:/Windows/Fonts") / n
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def main() -> int:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=18, fill=CARD + (255,))

    # 左侧竖条
    d.rounded_rectangle([0, 0, 7, H - 1], radius=4, fill=ACCENT + (255,))

    d.text((34, 32), "inkangler", font=font(15), fill=ACCENT + (255,))
    ver = "v" + VERSION
    vw = d.textlength(ver, font=font(13))
    d.text((W - 34 - vw, 34), ver, font=font(13), fill=MUTED + (255,))
    d.text((34, 60), "墨迹大侠 · 自动钓鱼", font=font(30, bold=True), fill=TEXT + (255,))
    d.text((34, 108), "正在启动，请稍候…", font=font(15), fill=MUTED + (255,))

    # 底部进度条（静态装饰，表示"在动"）
    d.rounded_rectangle([34, H - 44, W - 34, H - 38], radius=3, fill=BG + (255,))
    d.rounded_rectangle([34, H - 44, 34 + int((W - 68) * 0.35), H - 38], radius=3,
                        fill=ACCENT + (255,))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print("已生成 %s（%d 字节）" % (OUT, OUT.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
