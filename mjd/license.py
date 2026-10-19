"""授权验证：机器码 + Ed25519 签名，防止这个 exe 被随手拷走就到处跑。

为什么这么设计
--------------
* **exe 里只放公钥**。签发用的私钥在 `tools/_license/private.pem`，永远不进
  exe、也不随程序发出去。别人把 exe 拆开也只能看到公钥，签不出新授权。
* **绑定机器码**。机器码 = 注册表 `MachineGuid` + C 盘卷序列号，取 SHA-256
  前 80 位，换台电脑就对不上。
* **带使用人名字**。授权里写了 `owner`，泄漏出去能查到是谁那份。
* **一行文本**：`MOJI1.<base64>`，可以直接微信发给对方粘进去。

诚实说明：Python 打包的 exe 是可以解包、改字节码的，这套东西挡的是「拷过去
就能用」这种随手传播，挡不住铁了心破解的人。真要更硬的保护就得上服务器校验
或者加壳（pyarmor / VMProtect），那是另一回事了。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_ID = "mojidaxia-fishing-v1"
PREFIX = "MOJI1."
LICENSE_FILE = "license.key"

# 在线激活服务器（Cloudflare Pages Functions，挂在博客域名下）。
# 临时改地址：设环境变量 MOJI_ACTIVATE_URL
DEFAULT_SERVER = "https://blog.dabolang.dpdns.org"

try:                                    # 由 tools/lickey.py init 生成
    from ._pubkey import PUBLIC_KEY     # type: ignore
except Exception:                       # 还没生成公钥 → 授权功能关闭
    PUBLIC_KEY = ""


# ---------------------------------------------------------------- 机器码
def _machine_guid() -> str:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except OSError:
        return ""


def _volume_serial(root: str = "C:\\") -> str:
    import ctypes
    from ctypes import wintypes
    serial, maxlen, flags = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        root, None, 0, ctypes.byref(serial), ctypes.byref(maxlen),
        ctypes.byref(flags), None, 0)
    return "%08X" % serial.value if ok else ""


def machine_code() -> str:
    """本机机器码，形如 `Q7KD-2XMB-4ZTN-6WRA`。"""
    raw = "|".join([APP_ID, _machine_guid(), _volume_serial()])
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    s = base64.b32encode(digest[:10]).decode("ascii").rstrip("=")
    return "-".join(s[i:i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------- 签名
PAYLOAD_KEYS = ("app", "machine", "owner", "issued", "expires")


def canon(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def enabled() -> bool:
    return bool(PUBLIC_KEY)


def _decode(text: str) -> dict:
    """接受 `MOJI1.<b64>` 一行文本，也容忍直接贴 JSON / 带换行空格。"""
    s = "".join(text.split())
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
        s += "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(s.encode("ascii")).decode("utf-8")
    else:
        raw = s
    return json.loads(raw)


def verify_text(text: str) -> tuple[bool, str, str]:
    """校验一段授权文本。返回 (通过?, 说明, 使用人)。"""
    if not enabled():
        return True, "未启用授权校验（没有公钥）", ""
    try:
        data = _decode(text)
        sig = base64.b64decode(data.pop("sig"))
        payload = {k: data[k] for k in PAYLOAD_KEYS if k in data}
        missing = [k for k in PAYLOAD_KEYS if k not in payload]
        if missing:
            return False, "授权内容不完整（缺 %s）" % "、".join(missing), ""
    except Exception as e:                           # noqa: BLE001
        return False, "授权文本读不出来（%s）" % e, ""

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY))
        pk.verify(sig, canon(payload))
    except InvalidSignature:
        return False, "签名不对（授权被改过，或者不是这台机器的）", ""
    except Exception as e:                           # noqa: BLE001
        return False, "验签失败（%s）" % e, ""

    if payload.get("app") != APP_ID:
        return False, "授权不是给这个程序的", ""

    mine = machine_code()
    if str(payload.get("machine", "")).upper() != mine:
        return False, ("机器码不符：这份授权是给 %s 的，本机是 %s。"
                       % (payload.get("machine"), mine)), ""

    exp = int(payload.get("expires") or 0)
    if exp and time.time() > exp:
        return False, "授权已于 %s 过期" % time.strftime("%Y-%m-%d", time.localtime(exp)), ""

    owner = str(payload.get("owner") or "")
    tail = "（永久）" if not exp else "（到 %s）" % time.strftime("%Y-%m-%d", time.localtime(exp))
    return True, "已授权：%s%s" % (owner or "未署名", tail), owner


# ---------------------------------------------------------------- 文件
def license_path(base: Path) -> Path:
    return Path(base) / LICENSE_FILE


def server_url() -> str:
    return (os.environ.get("MOJI_ACTIVATE_URL") or DEFAULT_SERVER).rstrip("/")


def activate_online(machine: str, code: str, server: str | None = None,
                    timeout: float = 20.0) -> tuple[bool, str, str]:
    """拿激活码去服务器换授权。返回 (成功?, 说明, 授权文本)。

    一码一机：这个码第一次激活时就和 machine 绑死，换台电脑再来就被拒绝。
    同一个码在同一台机器上重复激活是允许的（重装、换硬盘时省事）。
    """
    url = (server or server_url()) + "/api/activate"
    body = json.dumps({"machine": machine, "code": code}).encode("utf-8")
    # 不带 UA 会被 Cloudflare 拦成 403（error code: 1010）
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "moji-license/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8", "replace"))
            return False, str(data.get("error") or ("服务器返回 %s" % e.code)), ""
        except Exception:                                  # noqa: BLE001
            return False, "激活服务器返回 %s" % e.code, ""
    except Exception as e:                                 # noqa: BLE001
        return False, "连不上激活服务器（%s）" % e, ""

    if not data.get("ok"):
        return False, str(data.get("error") or "激活失败"), ""
    text = str(data.get("license") or "")
    ok, msg, _ = verify_text(text)
    if not ok:                       # 服务器给的也照样验签，不盲信
        return False, "服务器给的授权校验不过：%s" % msg, ""
    return True, msg, text


def save(base: Path, text: str) -> Path:
    p = license_path(base)
    p.write_text(text.strip() + "\n", encoding="utf-8")
    return p


def check(base: Path, path: Path | None = None) -> tuple[bool, str, str]:
    p = Path(path) if path else license_path(base)
    if not enabled():
        return True, "未启用授权校验", ""
    if not p.exists():
        return False, "没有授权文件（%s）" % p.name, ""
    try:
        return verify_text(p.read_text(encoding="utf-8"))
    except OSError as e:
        return False, "读不了授权文件（%s）" % e, ""
