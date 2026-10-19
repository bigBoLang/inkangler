"""量「抛完竿之后多久才真正开始控制」——喂鱼档位 vs 鱼币档位对比。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import hook  # noqa: E402


def run(level: int, rounds: int = 2, mode: str = "feed") -> None:
    bot = hook.HookBot(act=True, mode=mode, cast_level=level, log=lambda s: None)
    obs = []
    press_t = []

    real_obs = bot.observe
    real_press = bot.press

    def obs_spy(prev=None):
        o, img = real_obs(prev)
        obs.append((time.perf_counter(), o.present, o.prog, o.fish, o.bright, o.zlen))
        return o, img

    def press_spy():
        press_t.append(time.perf_counter())
        return real_press()

    bot.observe = obs_spy          # type: ignore[method-assign]
    bot.press = press_spy          # type: ignore[method-assign]

    for r in range(1, rounds + 1):
        bot.settle_dialogs()
        bot.trk.reset()
        bot.sync_up()
        obs.clear()
        press_t.clear()
        bot.cast()
        t_up = time.perf_counter()
        o = bot.wait_minigame()
        t_track = time.perf_counter() if o is not None else None
        a = bot.fight(max_secs=45)
        if t_track is None:
            print(f"  档{level} 第{r}竿: 没等到轨道 ({a.result})")
            bot.dismiss()
            continue
        first_fish = next((t for t, p, pr, f, b, z in obs if f is not None), None)
        first_press = press_t[0] if press_t else None
        print("  档%d 第%d竿: 轨道 +%.2fs  找到鱼 +%.2fs  第一次按住 +%.2fs  结果 %s prog=%.0f%%"
              % (level, r, t_track - t_up,
                 (first_fish - t_up) if first_fish else float("nan"),
                 (first_press - t_up) if first_press else float("nan"),
                 a.result, (obs[-1][2] if obs else 0) * 100))
        if a.result == "dialog":
            bot.handle_catch(verbose=False)
        else:
            bot.dismiss()


if __name__ == "__main__":
    lv = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    run(lv, n)
