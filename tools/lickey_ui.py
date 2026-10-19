"""授权管理菜单 —— 双击项目根目录的「授权管理.bat」就是跑这个。

为什么用 Python 而不是批处理：中文在 cmd 的批处理里很容易乱码/解析错，
这里统一把控制台切成 UTF-8，显示稳定。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

try:                                      # 让中文在这个窗口里正常显示
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    ctypes.windll.kernel32.SetConsoleCP(65001)
except Exception:                          # noqa: BLE001
    pass
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:                      # noqa: BLE001
        pass

import lickey                              # noqa: E402


def ask(prompt: str, default: str = "") -> str:
    s = input(prompt).strip()
    return s or default


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run("clip", input=text.encode("utf-16le"), check=True,
                       shell=True, creationflags=0x08000000)
        return True
    except Exception:                      # noqa: BLE001
        return False


def do_codes() -> None:
    owner = ask("使用人名字（可留空）：")
    count = ask("生成几个（回车 = 1）：", "1")
    days = ask("有效期天数（回车 = 永久）：", "0")
    try:
        count_i, days_i = int(count), int(days)
    except ValueError:
        print("数量/天数得是数字")
        return
    if count_i < 1 or count_i > 200:
        print("一次最多 200 个")
        return
    try:
        d = lickey._admin("POST", "/api/admin/codes",
                          {"count": count_i, "days": days_i, "owner": owner})
    except SystemExit as e:
        print("失败：%s" % e)
        return

    codes = d.get("codes", [])
    print()
    print("=" * 46)
    print("  使用人：%s    有效期：%s" % (owner or "（未署名）",
                                       ("%d 天" % days_i) if days_i else "永久"))
    print("  一码一机，一个码只能给一个人用")
    print("=" * 46)
    for c in codes:
        print("   " + c)
    print("=" * 46)
    if codes and copy_to_clipboard("\n".join(codes)):
        print("  已复制到剪贴板，直接粘给对方就行")


def do_machines() -> None:
    try:
        d = lickey._admin("GET", "/api/admin/machines")
    except SystemExit as e:
        print("失败：%s" % e)
        return
    rows = d.get("machines", [])
    print()
    if not rows:
        print("还没有机器激活过")
        return
    print("已激活 %d 台：" % d.get("count", len(rows)))
    print("  %-21s %-12s %s" % ("机器码", "使用人", "有效期"))
    print("  " + "-" * 44)
    for r in rows:
        exp = r.get("expires") or 0
        when = "永久"
        if exp:
            import time
            when = time.strftime("%Y-%m-%d", time.localtime(exp))
        print("  %-21s %-12s %s" % (r.get("machine"), r.get("owner") or "-", when))


def do_revoke() -> None:
    m = ask("要吊销的机器码：")
    if not m:
        return
    try:
        d = lickey._admin("POST", "/api/admin/revoke", {"machine": lickey._norm(m)})
    except SystemExit as e:
        print("失败：%s" % e)
        return
    print("已吊销：%s（对方下次启动就要重新激活）" % d.get("machine"))


def do_verify() -> None:
    src = ask("授权文件路径（可以把文件直接拖进来）：").strip('"')
    if not src:
        return
    lickey.cmd_verify(SimpleNamespace(source=src))


def do_local() -> None:
    lickey.cmd_machine(SimpleNamespace())
    lickey.cmd_status(SimpleNamespace())


MENU = """
==========================================
          墨迹大侠 · 授权管理
==========================================
  1. 生成激活码（发给别人）
  2. 查看已激活的机器
  3. 吊销一台机器
  4. 校验一份授权文件
  5. 本机机器码 / 授权状态
  0. 退出
==========================================
"""


def main() -> int:
    actions = {"1": do_codes, "2": do_machines, "3": do_revoke,
               "4": do_verify, "5": do_local}
    while True:
        print(MENU)
        try:
            c = input("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if c == "0":
            return 0
        fn = actions.get(c)
        if not fn:
            continue
        try:
            fn()
        except KeyboardInterrupt:
            print()
            return 0
        except Exception as e:             # noqa: BLE001
            print("出错了：%s" % e)
        print()


if __name__ == "__main__":
    raise SystemExit(main())
