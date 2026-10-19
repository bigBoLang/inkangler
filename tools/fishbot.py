"""遛鱼机器人：自动抛竿 → 遛鱼 → 结算（按模式直接卖 / 留鱼）→ 攒够自动卖鱼。

两种模式
--------
`--mode feed`  喂鱼：结算窗里读到长度 **大于** `--min-len`（默认 65cm）才点
               「原价出售」，比它短的关掉弹窗、留进鱼箱。默认不自动卖鱼箱
               （否则留下的小鱼又被卖掉了）。
`--mode coins` 鱼币：不看长度，结算窗里一律直接卖掉；鱼箱攒够 `--sell-every`
               条再整批卖（默认 12）。

用法:
    python tools/fishbot.py --rounds 5                     # 鱼币模式钓 5 竿
    python tools/fishbot.py --mode feed --min-len 65 --rounds 20
    python tools/fishbot.py --sell                         # 立刻卖一轮鱼
    python tools/fishbot.py --dry                          # 只看不动
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mjd import hook, sell, win  # noqa: E402
from mjd import license as lic  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _check_license() -> bool:
    ok, msg, _owner = lic.check(ROOT)
    if ok:
        print("授权：" + msg)
        return True
    print("没有授权：" + msg)
    print("本机机器码：%s" % lic.machine_code())
    print("把机器码发给作者换一份授权，放到 %s 即可。" % lic.license_path(ROOT))
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1, help="钓几竿；0 = 一直钓")
    ap.add_argument("--mode", choices=[hook.MODE_FEED, hook.MODE_COINS],
                    default=hook.MODE_COINS, help="feed=喂鱼（小的留下）/ coins=鱼币（全卖）")
    ap.add_argument("--min-len", type=float, default=hook.MIN_LEN,
                    help="喂鱼模式的长度阈值（cm）：超过就直接卖")
    ap.add_argument("--cast-level", type=int, default=0,
                    help="抛竿档位 1~10（满力=10）；0 = 按模式自动（喂鱼 3 / 鱼币 10）")
    ap.add_argument("--cast-max", type=float, default=hook.CAST_MAX,
                    help="满力档对应的按住秒数（蓄力条实测 1.7s 涨满）")
    ap.add_argument("--cast", type=float, default=None,
                    help="直接指定按住秒数（覆盖档位设置，调试用）")
    ap.add_argument("--kp", type=float, default=hook.KP)
    ap.add_argument("--max", type=float, default=90.0, help="单次遛鱼最长秒数")
    ap.add_argument("--sell-every", type=int, default=-1,
                    help="每钓到这么多条就整批卖一次鱼箱；0=不自动卖；-1=按模式（鱼币 12 / 喂鱼 0）")
    ap.add_argument("--sell", action="store_true", help="只卖一轮鱼然后退出")
    ap.add_argument("--dry", action="store_true", help="不发出任何输入，只观察")
    ap.add_argument("--frames", action="store_true", help="保存每帧截图")
    ap.add_argument("--watch", type=float, default=0.0, help="dry 模式下观察秒数")
    ap.add_argument("--lead", type=float, default=hook.T_LEAD,
                    help="前馈提前量（秒），补取图/下发延迟")
    ap.add_argument("--no-ff", action="store_true", help="关掉前馈，退回纯比例控制（对比用）")
    ap.add_argument("--jump-guard", action="store_true",
                    help="把「鱼位置跳变」当误检丢掉（默认关：实测跳变多是游戏掉帧）")
    ap.add_argument("--log", default="", help="完整日志写进文件（用管道会拖慢主循环）")
    a = ap.parse_args()

    if a.log:
        fh = open(a.log, "w", encoding="utf-8", buffering=1)
        sys.stdout = fh

    level = a.cast_level or (3 if a.mode == hook.MODE_FEED else 10)
    sell_every = (12 if a.mode == hook.MODE_COINS else 0) if a.sell_every < 0 else a.sell_every

    if not _check_license():
        return 3

    w = win.find_window("墨迹大侠")
    try:
        bot = hook.HookBot(act=not a.dry, kp=a.kp, cast_secs=a.cast,
                           keep_frames=a.frames, max_secs=a.max,
                           dialog_dir=ROOT / "shots" / "dialogs" / "live",
                           lead=a.lead, feedforward=not a.no_ff,
                           jump_guard=a.jump_guard,
                           cast_level=level, cast_max=a.cast_max,
                           mode=a.mode, min_len=a.min_len)
    except RuntimeError as e:
        sys.stdout = sys.__stdout__
        print("\n" + str(e) + "\n")
        return 2
    seller = sell.Seller(w, act=not a.dry,
                         shot_dir=(ROOT / "shots" / "sell") if a.frames else None)

    if a.sell:
        print("卖鱼:", seller.run(), "批")
        return 0

    if a.dry:
        t0 = time.time()
        prev = None
        while time.time() - t0 < (a.watch or 20.0):
            o, img = bot.observe(prev)
            if o.fish is not None:
                prev = o.fish
            print("%6.2fs bri=%5.1f zone=%3.0f~%3.0f zc=%3.0f prog=%4.1f%% fish=%s %s" % (
                time.time() - t0, o.bright, o.zone[0], o.zone[1], o.zc, o.prog * 100,
                ("%3.0f" % o.fish) if o.fish is not None else " --",
                "IN" if o.covered else ""))
            if a.frames:
                d = ROOT / "shots" / "run"
                d.mkdir(parents=True, exist_ok=True)
                img.save(d / f"R_{len(bot.frames):03d}.png")
                bot.frames.append(img)
            time.sleep(0.05)
        return 0

    print("模式: %s  阈值: %scm  抛竿: %d/10 (%.2fs)  鱼箱自动卖: %s"
          % ("喂鱼" if a.mode == hook.MODE_FEED else "鱼币", a.min_len, level,
             bot.cast_secs, ("每 %d 条" % sell_every) if sell_every else "关"))

    counts: dict[str, int] = {}
    since_sell = 0
    stuck = 0
    n_ok = n_sold = n_kept = 0
    agg_n = 0
    agg_abs_e = 0.0
    agg_depth = 0.0
    agg_lag = 0.0
    i = 0
    while a.rounds == 0 or i < a.rounds:
        i += 1
        bot.reset_stats()
        at = bot.run_once(i)
        if at.result == "stopped":
            print("  收到停止信号，收工")
            break
        counts[at.result] = counts.get(at.result, 0) + 1
        if bot.stat_n:
            print("  " + bot.track_stats())
            agg_n += bot.stat_n
            agg_abs_e += bot.stat_abs_e
            agg_depth += bot.stat_depth
            agg_lag += bot.stat_lag
        if at.result == "caught":
            n_ok += 1
            if at.action == "sold":
                n_sold += 1
            elif at.action == "kept":
                n_kept += 1
            since_sell += 1
            stuck = 0
        elif at.result == "stuck":
            print("  现在不在钓鱼界面（可能点到别的页面了），先停下，别乱点")
            break
        elif at.result == "no-minigame":
            stuck += 1
            if a.mode == hook.MODE_COINS and stuck in (3, 6):
                print("  连续进不了小游戏，先卖一轮鱼再试")
                seller.run()
            elif stuck >= 9:
                print("  抛竿一直没反应：鱼箱满了或没鱼饵了，收工")
                break
        else:
            stuck = 0
        if a.frames and at.samples:
            d = ROOT / "shots" / "run"
            d.mkdir(parents=True, exist_ok=True)
            for j, im in enumerate(bot.frames):
                im.save(d / f"R{i:02d}_{j:03d}.png")
            bot.frames.clear()

        if sell_every and since_sell >= sell_every:
            print(f"  == 钓到 {since_sell} 条，去卖鱼箱 ==")
            nb = seller.run()
            print(f"  卖了 {nb} 批")
            if nb == 0:
                print(f"  !! 鱼箱里没找到鱼，可能这几竿没真钓上来（计数 {since_sell}）")
            since_sell = 0
        time.sleep(0.4)

    print("汇总:", counts)
    print("钓上 %d 条：直接卖掉 %d，留进鱼箱 %d" % (n_ok, n_sold, n_kept))
    if agg_n:
        print("本轮跟踪总览: 有效帧 %d  平均|误差| %.1f°  平均滞后 %+.1f°  平均深度 %.2f"
              % (agg_n, agg_abs_e / agg_n, agg_lag / agg_n, agg_depth / agg_n))
    print(bot.timing_stats())
    if since_sell:
        print(f"鱼箱里还有 {since_sell} 条（本轮没卖）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
