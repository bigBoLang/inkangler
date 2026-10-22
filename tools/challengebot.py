"""墨迹大侠 · 自动挑战（幸存者模式）

游戏里角色自己打，玩家只要在升级时选一张技能卡。脚本负责：
    选技能 → 等结算 → 关结算 → 回主界面继续挑战

用法：
    python tools/challengebot.py --rounds 1     # 刷一局（约 8~9 分钟）
    python tools/challengebot.py --rounds 5     # 刷五局
    python tools/challengebot.py --rounds 0     # 一直刷，Ctrl-C 收工
    python tools/challengebot.py --dry          # 只认屏、不点击（先看看认得准不准）
    python tools/challengebot.py --pick 0       # 永远选左边那张（0/1/2 = 左/中/右）

权限：微信若以管理员身份运行，本脚本也要管理员；否则会自动改走
`tools/remote.py` 的提权小助手（先 `python tools/remote.py --start`）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import battle, driver  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1, help="刷几局；0 = 一直刷")
    ap.add_argument("--pick", type=int, default=1, choices=[0, 1, 2],
                    help="技能卡位置：0 左 / 1 中 / 2 右")
    ap.add_argument("--dry", action="store_true", help="只认屏，不点击")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="dry 模式看多少秒（默认 20）")
    ap.add_argument("--log", default="", help="日志另存到文件")
    a = ap.parse_args()

    fh = None
    if a.log:
        p = Path(a.log)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = p.open("w", encoding="utf-8", buffering=1)
        sys.stdout = fh

    drv = driver.make_driver()
    print(f"驱动：{drv.name}")

    def log(msg: str) -> None:
        print(time.strftime("[%H:%M:%S] ") + msg, flush=True)

    if a.dry:
        t0 = time.time()
        secs = a.seconds or 20.0
        last = None
        while time.time() - t0 < secs:
            sc = battle.detect(drv.grab())
            if sc.name != last:
                print(f"{time.time() - t0:6.1f}s {sc.name:8s} "
                      f"challenge={sc.scores['challenge']:.2f} skill={sc.scores['skill']:.2f} "
                      f"clear={sc.scores['clear']:.2f} any={sc.scores['click_any']:.2f} "
                      f"pause={sc.scores['pause']:.2f}", flush=True)
                last = sc.name
            time.sleep(0.4)
        return 0

    bot = battle.BattleBot(
        drv.grab, drv.click, rounds=a.rounds, pick=a.pick, log=log,
        stop=lambda: False,
    )
    try:
        st = bot.run()
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，收工")
        return 0
    if fh:
        print(f"日志：{fh.name}")
    print(f"结果：{st.result or '停止'}  局数 {st.rounds}  通关 {st.cleared}  "
          f"选技能 {st.skills} 次  用时 {st.elapsed / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
