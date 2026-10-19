"""给 Pages 项目写 KV 绑定 + 环境变量，然后触发一次重新部署。

    set CF_API_TOKEN=...
    python tools/cf_pages.py

为什么用脚本：LICENSE_KEY 是私钥，走本地调用就不会出现在对话/日志里。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PEM = ROOT / "tools" / "_license" / "private.pem"
ADMIN_KEY_FILE = ROOT / "server" / "admin.key"
KV_ID_FILE = ROOT / "server" / "kv.id"

API = "https://api.cloudflare.com/client/v4"
ACCOUNT = "5a05d8c7301ba63933b0e5ba263226c6"
PROJECT = "sundayongblog"


def call(method: str, path: str, token: str, body=None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise SystemExit("%s %s 失败 %s：%s" % (method, path, e.code, e.read().decode("utf-8", "replace")[:500]))
    j = json.loads(text or "{}")
    if not j.get("success", False):
        raise SystemExit("%s %s 失败：%s" % (method, path, j.get("errors")))
    return j.get("result")


def main() -> int:
    token = os.environ.get("CF_API_TOKEN", "")
    if not token:
        print("缺 CF_API_TOKEN")
        return 2

    kv_id = KV_ID_FILE.read_text(encoding="utf-8").strip()
    admin = ADMIN_KEY_FILE.read_text(encoding="utf-8").strip()

    from cryptography.hazmat.primitives import serialization
    sk = serialization.load_pem_private_key(PRIVATE_PEM.read_bytes(), password=None)
    key_b64 = base64.b64encode(sk.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())).decode("ascii")

    base = "/accounts/%s/pages/projects/%s" % (ACCOUNT, PROJECT)
    proj = call("GET", base, token)
    cfg = proj.get("deployment_configs") or {}
    prod = dict(cfg.get("production") or {})

    kv = dict(prod.get("kv_namespaces") or {})
    kv["LIC"] = {"namespace_id": kv_id}
    env = dict(prod.get("env_vars") or {})
    env["LICENSE_KEY"] = {"type": "secret_text", "value": key_b64}
    env["ADMIN_KEY"] = {"type": "secret_text", "value": admin}
    prod["kv_namespaces"] = kv
    prod["env_vars"] = env
    cfg["production"] = prod

    merged = dict(proj)
    merged.pop("id", None)
    merged.pop("latest_deployment", None)
    merged.pop("canonical_deployment", None)
    merged.pop("created_on", None)
    merged.pop("subdomain", None)
    merged.pop("domains", None)
    merged.pop("source", None)
    merged.pop("uses_functions", None)
    merged.pop("deployment_configs", None)

    payload = {"deployment_configs": cfg}
    res = call("PATCH", base, token, payload)
    prod2 = (res.get("deployment_configs") or {}).get("production") or {}
    print("KV 绑定：%s" % list((prod2.get("kv_namespaces") or {}).keys()))
    print("环境变量：%s" % list((prod2.get("env_vars") or {}).keys()))

    dep = call("POST", base + "/deployments", token, {"branch": proj.get("production_branch") or "main"})
    print("已触发部署：%s  %s" % (dep.get("id"), (dep.get("url") or "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
