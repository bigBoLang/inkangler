"""把授权服务器（server/license-worker.js）部署到 Cloudflare Workers。

不需要 Node / wrangler：直接用 Cloudflare 的 REST API + 标准库。

准备
----
1. 建一个 API Token（Cloudflare 面板 → 右上头像 → My Profile → API Tokens →
   Create Token → Custom token），权限给：
       Account → Workers Scripts → Edit
       Account → Workers KV Storage → Edit
       Zone    → Workers Routes  → Edit      （要绑自定义域名时才需要）
       Account → Account Settings → Read
   账户范围选你自己的账户，Zone 可选。有效期建议 1 天，用完就删。

2. 运行：
       set CF_API_TOKEN=你的token
       python tools/deploy_server.py

   想绑到自己的域名（推荐）：
       python tools/deploy_server.py --host auth.dabolang.dpdns.org --zone your-zone-name

会做的事
--------
* 建（或复用）KV 命名空间 moji-license
* 上传 Worker：moji-license
* 把 Ed25519 私钥（tools/_license/private.pem）作为 secret 传上去
* 生成管理口令 ADMIN_KEY，存到 server/admin.key，并打印出来
* 最后打印 https://.../api/health 供核对
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import ssl
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_JS = ROOT / "server" / "license-worker.js"
PRIVATE_PEM = ROOT / "tools" / "_license" / "private.pem"
ADMIN_KEY_FILE = ROOT / "server" / "admin.key"
SCRIPT_NAME = "moji-license"
KV_TITLE = "moji-license"

API = "https://api.cloudflare.com/client/v4"


def call(method: str, path: str, token: str, body=None, ctype="application/json", raw: bool = False):
    data = None
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = ctype
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(detail)
            msgs = "; ".join("%s %s" % (x.get("code"), x.get("message")) for x in j.get("errors", []))
        except Exception:
            msgs = detail[:400]
        raise SystemExit("API 出错 %s %s：%s" % (method, path, msgs or detail[:400]))
    if raw:
        return payload
    j = json.loads(payload or b"{}")
    if not j.get("success", False) and "result" not in j:
        raise SystemExit("API 返回失败 %s %s：%s" % (method, path, j.get("errors")))
    return j.get("result")


def find_kv(token: str, account: str) -> str:
    namespaces = call("GET", "/accounts/%s/storage/kv/namespaces?per_page=100" % account, token) or []
    for ns in namespaces:
        if ns.get("title") == KV_TITLE:
            print("复用 KV 命名空间：%s (%s)" % (KV_TITLE, ns["id"]))
            return ns["id"]
    ns = call("POST", "/accounts/%s/storage/kv/namespaces" % account, token, {"title": KV_TITLE})
    print("已建 KV 命名空间：%s (%s)" % (KV_TITLE, ns["id"]))
    return ns["id"]


def multipart(metadata: dict, filename: str, code: bytes) -> tuple[bytes, str]:
    boundary = "----moji" + uuid.uuid4().hex
    parts = []
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"metadata\"\r\n"
                  "Content-Type: application/json\r\n\r\n" % boundary).encode())
    parts.append(json.dumps(metadata).encode())
    parts.append(("\r\n--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                  "Content-Type: application/javascript+module\r\n\r\n"
                  % (boundary, filename, filename)).encode())
    parts.append(code)
    parts.append(("\r\n--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def private_key_b64() -> str:
    from cryptography.hazmat.primitives import serialization
    if not PRIVATE_PEM.exists():
        raise SystemExit("找不到私钥 %s，先跑 python tools/lickey.py init" % PRIVATE_PEM)
    sk = serialization.load_pem_private_key(PRIVATE_PEM.read_bytes(), password=None)
    der = sk.private_bytes(serialization.Encoding.DER,
                           serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())
    return base64.b64encode(der).decode("ascii")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("CF_API_TOKEN", ""))
    ap.add_argument("--account", default=os.environ.get("CF_ACCOUNT_ID", ""))
    ap.add_argument("--host", default="", help="绑定自定义域名，例如 auth.dabolang.dpdns.org")
    ap.add_argument("--zone", default="", help="上面的域名所属 zone 名，例如 dabolang.dpdns.org")
    ap.add_argument("--new-admin-key", action="store_true", help="重新生成管理口令")
    a = ap.parse_args()

    if not a.token:
        print("缺 API Token。先 set CF_API_TOKEN=...，或用 --token")
        return 2
    if not WORKER_JS.exists():
        print("找不到 %s" % WORKER_JS)
        return 2

    who = call("GET", "/user/tokens/verify", a.token)
    print("Token 有效：%s" % who.get("status"))

    accounts = call("GET", "/accounts?per_page=50", a.token) or []
    if a.account:
        account = a.account
    elif len(accounts) == 1:
        account = accounts[0]["id"]
    else:
        print("有多个账户，用 --account 指定：")
        for acc in accounts:
            print("  %s  %s" % (acc["id"], acc.get("name")))
        return 2
    print("账户：%s" % account)

    kv_id = find_kv(a.token, account)

    code = WORKER_JS.read_bytes()
    metadata = {
        "main_module": "license-worker.js",
        "compatibility_date": "2025-09-01",
        "bindings": [{"type": "kv_namespace", "name": "LIC", "namespace_id": kv_id}],
        "observability": {"enabled": True},
    }
    body, ctype = multipart(metadata, "license-worker.js", code)
    call("PUT", "/accounts/%s/workers/scripts/%s" % (account, SCRIPT_NAME), a.token,
         body, ctype)
    print("已上传 Worker：%s" % SCRIPT_NAME)

    admin_key = ADMIN_KEY_FILE.read_text(encoding="utf-8").strip() if ADMIN_KEY_FILE.exists() else ""
    if not admin_key or a.new_admin_key:
        admin_key = secrets.token_urlsafe(24)
        ADMIN_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADMIN_KEY_FILE.write_text(admin_key + "\n", encoding="utf-8")

    for name, text in (("LICENSE_KEY", private_key_b64()), ("ADMIN_KEY", admin_key)):
        call("PUT", "/accounts/%s/workers/scripts/%s/secrets" % (account, SCRIPT_NAME), a.token,
             {"name": name, "text": text, "type": "secret_text"})
    print("已设置 secret：LICENSE_KEY、ADMIN_KEY")

    base = ""
    if a.host:
        if not a.zone:
            raise SystemExit("绑自定义域名要同时给 --zone")
        zones = call("GET", "/zones?name=%s" % a.zone, a.token) or []
        if not zones:
            raise SystemExit("找不到 zone：%s（Token 需要有 Zone:Read 权限）" % a.zone)
        zone_id = zones[0]["id"]
        call("PUT", "/accounts/%s/workers/domains" % account, a.token, {
            "zone_id": zone_id, "hostname": a.host,
            "service": SCRIPT_NAME, "environment": "production",
        })
        base = "https://" + a.host
        print("已绑定域名：%s" % base)
    else:
        sub = call("GET", "/accounts/%s/workers/subdomain" % account, a.token) or {}
        if sub.get("subdomain"):
            base = "https://%s.%s.workers.dev" % (SCRIPT_NAME, sub["subdomain"])
            print("地址：%s" % base)
        else:
            print("还没设 workers.dev 子域，去面板里给账户设一个，或用 --host 绑自己的域名")

    if base:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=30) as r:
                print("健康检查：%s" % r.read().decode("utf-8", "replace")[:200])
        except Exception as e:                       # noqa: BLE001
            print("健康检查没通（%s）。可能还在生效，等 10 秒再试：%s/api/health" % (e, base))

    print("\n管理口令（已存 %s，别外传）：\n  %s" % (ADMIN_KEY_FILE, admin_key))
    print("生成激活码示例：")
    print('  curl -s -X POST %s/api/admin/codes -H "X-Admin-Key: %s" '
          '-H "Content-Type: application/json" -d "{\\"count\\":1,\\"owner\\":\\"张三\\"}"' % (base or "https://<你的域名>", admin_key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
