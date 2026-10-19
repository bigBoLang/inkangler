"""从视频里抽帧，便于逐帧分析游戏画面。

OpenCV 自带 FFmpeg 解码，不需要单独安装 ffmpeg。

用法:
    python tools/video.py <视频文件>                  # 按原帧率全部抽出
    python tools/video.py <视频文件> --fps 10         # 每秒抽 10 帧
    python tools/video.py <视频文件> --every 5        # 每 5 帧抽 1 帧
    python tools/video.py <视频文件> --range 2 8      # 只抽第 2~8 秒
    python tools/video.py <视频文件> --montage --crop 140,520,480,830
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "shots" / "vid"


def imwrite_unicode(path: Path, img: np.ndarray) -> None:
    """cv2.imwrite 在 Windows 下不支持非 ASCII 路径。"""
    ext = path.suffix or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise RuntimeError(f"编码失败: {path}")
    buf.tofile(str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=None, help="目标抽帧帧率")
    ap.add_argument("--every", type=int, default=None, help="每隔 N 帧抽一张")
    ap.add_argument("--range", type=float, nargs=2, default=None,
                    metavar=("START", "END"), help="只抽这段时间（秒）")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--limit", type=int, default=0, help="最多抽多少张，0=不限")
    a = ap.parse_args()

    src = Path(a.video)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print("找不到视频:", src)
        return 1

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        print("无法打开视频（OpenCV 解码失败）:", src)
        return 1

    vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"视频: {src.name}  {w}x{h}  {vfps:.2f} fps  共 {total} 帧 "
          f"({total / vfps:.1f} 秒)")

    if a.fps:
        step = max(1, round(vfps / a.fps))
    elif a.every:
        step = max(1, a.every)
    else:
        step = 1

    t0, t1 = a.range if a.range else (0.0, float("inf"))
    tag = a.tag or src.stem
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    saved = 0
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        t = idx / vfps
        if t < t0:
            idx += 1
            continue
        if t > t1:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                if a.scale != 1.0:
                    frame = cv2.resize(frame, None, fx=a.scale, fy=a.scale,
                                       interpolation=cv2.INTER_AREA)
                imwrite_unicode(OUT_DIR / f"{tag}_{idx:05d}_t{t:06.2f}.png", frame)
                saved += 1
                if a.limit and saved >= a.limit:
                    break
        idx += 1

    cap.release()
    print(f"抽出 {saved} 帧 -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
