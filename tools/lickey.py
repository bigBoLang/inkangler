"""授权签发工具 —— **只留在你自己机器上，别跟 exe 一起发出去。**

    python tools/lickey.py init                      # 第一次：生成密钥对（只做一次！）
    python tools/lickey.py machine                   # 看本机机器码
    python tools/lickey.py issue <机器码> --owner 张三  # 签一份授权，打印出来
    python tools/lickey.py issue <机器码> --owner 张三 --out license.key
    python tools/lickey.py verify <授权文件或文本>     # 校验一份授权
    python tools/lickey.py status                    # 看本机当前授权状态

签发流程（给别人的电脑用）：
    1. 对方打开程序 → 界面弹出「未授权」，上面有他的机器码，让他发给你；
    2. 你在这台机器上 `issue <他的机器码> --owner 他的名字`；
    3. 把打印出来的一行 `MOJI1.xxxx` 发给他，粘进程序里就能用。

注意
----
* `tools/_license/private.pem` 是**私钥**：能签出无限多授权。别发人、别传网盘，
  最好复制一份到 U 盘/密码管理器里备份。丢了就只能重新 init（老授权全作废）。
* 私钥和 `mjd/_pubkey.py` 是一对：重新 init 会换掉公钥，必须重新打包 exe。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mjd import license as lic          # noqa: E402

KEY_DIR = ROOT / "tools" / "_license"
PRIVATE_PEM = KEY_DIR / "private.pem"
PUBKEY_PY = ROOT / "mjd" / "_pubkey.py"


def _norm(code: str) -> str:
    s = "".join(ch for ch in code.upper() if ch.isalnum())
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))


def cmd_init(a) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if PRIVATE_PEM.exists() and not a.force:
        print("已经初始化过了：%s" % PRIVATE_PEM)
        print("要换密钥就加 --force（注意：所有已发出的授权都会失效，exe 要重新打包）")
        return 1
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    sk = Ed25519PrivateKey.generate()
    PRIVATE_PEM.write_bytes(sk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    pk = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    PUBKEY_PY.write_text(
        '"""自动生成，别手改。公钥（公开的，放程序里没问题）。"""\n'
        'PUBLIC_KEY = "%s"\n' % base64.b64encode(pk).decode("ascii"),
        encoding="utf-8")
    print("私钥：%s" % PRIVATE_PEM)
    print("公钥：%s" % PUBKEY_PY)
    print("本机机器码：%s" % lic.machine_code())
    print("\n** 私钥千万别发给别人 **；换密钥后 exe 必须重新打包。")
    return 0


def cmd_machine(a) -> int:
    print(lic.machine_code())
    return 0


def cmd_issue(a) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not PRIVATE_PEM.exists():
        print("还没有密钥，先跑：python tools/lickey.py init")
        return 1
    code = _norm(a.machine)
    if len(code.replace("-", "")) < 12:
        print("机器码看着不对：%r" % a.machine)
        return 1
    now = int(time.time())
    payload = {
        "app": lic.APP_ID,
        "machine": code,
        "owner": a.owner or "",
        "issued": now,
        "expires": 0 if not a.days else now + int(a.days) * 86400,
    }
    sk = serialization.load_pem_private_key(PRIVATE_PEM.read_bytes(), password=None)
    assert isinstance(sk, Ed25519PrivateKey)
    sig = sk.sign(lic.canon(payload))
    payload["sig"] = base64.b64encode(sig).decode("ascii")
    line = lic.PREFIX + base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")

    ok, msg, _ = lic.verify_text(line)      # 自检一遍
    if not ok and code == lic.machine_code():
        print("自检失败：%s" % msg)
        return 1

    print("\n机器码：%s" % code)
    print("使用人：%s" % (a.owner or "（未署名）"))
    print("有效期：%s" % ("永久" if not a.days else "%d 天" % a.days))
    print("\n授权文本（整行复制发给对方）：\n")
    print(line)
    if a.out:
        out = Path(a.out)
        if not out.is_absolute():
            out = ROOT / out
        out.write_text(line + "\n", encoding="utf-8")
        print("\n已写入：%s" % out)
    return 0


def cmd_verify(a) -> int:
    p = Path(a.source)
    text = p.read_text(encoding="utf-8") if p.exists() else a.source
    ok, msg, owner = lic.verify_text(text)
    print(("通过：%s" % msg) if ok else ("不通过：%s" % msg))
    return 0 if ok else 1


def cmd_status(a) -> int:
    print("本机机器码：%s" % lic.machine_code())
    ok, msg, _ = lic.check(ROOT)
    print(("通过：" if ok else "不通过：") + msg)
    return 0 if ok else 1


# ---------------------------------------------------------------- 在线激活码
def _admin(method: str, path: str, body=None):
    import urllib.error
    import urllib.request

    server = (os.environ.get("MOJI_ACTIVATE_URL") or lic.DEFAULT_SERVER).rstrip("/")
    key = os.environ.get("MOJI_ADMIN_KEY", "")
    if not key:
        kf = ROOT / "server" / "admin.key"
        key = kf.read_text(encoding="utf-8").strip() if kf.exists() else ""
    if not key:
        raise SystemExit("没有管理口令。先部署服务器（tools/deploy_server.py），"
                         "或设环境变量 MOJI_ADMIN_KEY")
    req = urllib.request.Request(
        server + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Admin-Key": key,
                 "User-Agent": "moji-license/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8", "replace"))
            raise SystemExit("服务器拒绝了：%s" % j.get("error"))
        except ValueError:
            raise SystemExit("服务器返回 %s" % e.code)
    except Exception as e:                                  # noqa: BLE001
        raise SystemExit("连不上 %s（%s）" % (server, e))


def cmd_codes(a) -> int:
    d = _admin("POST", "/api/admin/codes",
               {"count": a.count, "days": a.days, "owner": a.owner})
    print("使用人：%s   有效期：%s" % (a.owner or "（未署名）",
                                    ("%d 天" % a.days) if a.days else "永久"))
    print("激活码（一码一机，发一个给对方）：")
    for c in d.get("codes", []):
        print("  " + c)
    return 0


def cmd_machines(a) -> int:
    d = _admin("GET", "/api/admin/machines")
    rows = d.get("machines", [])
    print("已激活 %d 台" % d.get("count", len(rows)))
    for r in rows:
        exp = r.get("expires") or 0
        when = time.strftime("%Y-%m-%d", time.localtime(exp)) if exp else "永久"
        print("  %s  %-12s  %s" % (r.get("machine"), r.get("owner") or "-", when))
    return 0


def cmd_revoke(a) -> int:
    d = _admin("POST", "/api/admin/revoke", {"machine": _norm(a.machine)})
    print("已吊销：%s（对方下次要授权就得重新激活）" % d.get("machine"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="授权签发（别外传这个脚本和私钥）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="生成密钥对（只做一次）")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    sub.add_parser("machine", help="打印本机机器码").set_defaults(fn=cmd_machine)
    sub.add_parser("status", help="看本机授权状态").set_defaults(fn=cmd_status)

    p = sub.add_parser("issue", help="签一份授权")
    p.add_argument("machine", help="对方的机器码")
    p.add_argument("--owner", default="", help="使用人名字（会写进授权）")
    p.add_argument("--days", type=int, default=0, help="有效天数，默认永久")
    p.add_argument("--out", default="", help="顺便写到文件，例如 license.key")
    p.set_defaults(fn=cmd_issue)

    p = sub.add_parser("verify", help="校验授权")
    p.add_argument("source", help="授权文件路径，或直接给授权文本")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("codes", help="在线生成激活码（发给别人用）")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--days", type=int, default=0, help="有效期天数，0=永久")
    p.add_argument("--owner", default="", help="使用人名字")
    p.set_defaults(fn=cmd_codes)

    sub.add_parser("machines", help="看看哪些机器已经激活").set_defaults(fn=cmd_machines)

    p = sub.add_parser("revoke", help="吊销一台机器")
    p.add_argument("machine")
    p.set_defaults(fn=cmd_revoke)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
