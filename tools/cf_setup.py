"""把授权服务需要的 Cloudflare 资源准备好：KV 命名空间 + 两个 secret。

    set CF_API_TOKEN=...
    set CF_ACCOUNT_ID=...
    python tools/cf_setup.py

做的事：
  1. 建（或复用）KV 命名空间 moji-license，把 id 写到 server/kv.id
  2. 把 Ed25519 私钥（PKCS#8 DER base64）设成 Worker 的 secret LICENSE_KEY
  3. 生成本地管理口令 server/admin.key，并设成 Worker 的 secret ADMIN_KEY
私钥只在内存里过一遍，不会打印出来。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PEM = ROOT / "tools" / "_license" / "private.pem"
ADMIN_KEY_FILE = ROOT / "server" / "admin.key"
KV_ID_FILE = ROOT / "server" / "kv.id"

API = "https://api.cloudflare.com/client/v4"
KV_TITLE = "moji-license"
WORKER = "sundayongblog"


def call(method: str, path: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit("%s %s 失败：%s" % (method, path, detail))
    if not j.get("success", False):
        raise SystemExit("%s %s 失败：%s" % (method, path, j.get("errors")))
    return j.get("result")


def main() -> int:
    token = os.environ.get("CF_API_TOKEN", "")
    account = os.environ.get("CF_ACCOUNT_ID", "")
    if not token or not account:
        print("缺 CF_API_TOKEN / CF_ACCOUNT_ID")
        return 2

    ns_list = call("GET", "/accounts/%s/storage/kv/namespaces?per_page=100" % account, token) or []
    ns = next((x for x in ns_list if x.get("title") == KV_TITLE), None)
    if ns:
        print("复用 KV：%s  id=%s" % (KV_TITLE, ns["id"]))
    else:
        ns = call("POST", "/accounts/%s/storage/kv/namespaces" % account, token, {"title": KV_TITLE})
        print("已建 KV：%s  id=%s" % (KV_TITLE, ns["id"]))
    KV_ID_FILE.write_text(ns["id"] + "\n", encoding="utf-8")

    from cryptography.hazmat.primitives import serialization
    sk = serialization.load_pem_private_key(PRIVATE_PEM.read_bytes(), password=None)
    key_b64 = base64.b64encode(sk.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())).decode("ascii")

    admin = ADMIN_KEY_FILE.read_text(encoding="utf-8").strip() if ADMIN_KEY_FILE.exists() else ""
    if not admin:
        admin = secrets.token_urlsafe(24)
        ADMIN_KEY_FILE.write_text(admin + "\n", encoding="utf-8")

    for name, text in (("LICENSE_KEY", key_b64), ("ADMIN_KEY", admin)):
        call("PUT", "/accounts/%s/workers/scripts/%s/secrets" % (account, WORKER), token,
             {"name": name, "text": text, "type": "secret_text"})
        print("已设置 secret：%s" % name)

    print("管理口令存在：%s" % ADMIN_KEY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
