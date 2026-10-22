"""管理员权限的「手」：本机 HTTP 小服务，替普通权限的脚本截图 / 点击游戏窗口。

微信以管理员身份运行时，普通权限进程发过去的鼠标消息会被 UIPI 丢掉。
这个脚本用管理员权限启动一次，之后开发脚本通过 127.0.0.1 让它代劳，
不用每次弹 UAC。

用法（普通权限）：
    python tools/remote.py --start        # 弹一次 UAC，后台常驻
    python tools/remote.py --stop
    python tools/remote.py --status

接口（都只监听 127.0.0.1，需要 shots/_agent_token.txt 里的口令）：
    GET  /status
    GET  /win
    GET  /snap?path=shots/xx.png
    POST /click    {"x":1,"y":2,"hold":0.06}
    POST /press    {"x":1,"y":2}
    POST /release  {"x":1,"y":2}
    POST /drag     {"x1":1,"y1":2,"x2":3,"y2":4,"steps":20,"pause":0.012}
    POST /focus
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mjd import win  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
TOKEN_FILE = ROOT / "shots" / "_agent_token.txt"
LOG_FILE = ROOT / "shots" / "_agent.log"
TITLE = "墨迹大侠"

_lock = threading.Lock()


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def window() -> win.WindowInfo:
    return win.find_window(TITLE)


class Handler(BaseHTTPRequestHandler):
    server_version = "MojinAgent/1.0"

    def log_message(self, fmt, *args):  # 安静点
        pass

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self) -> bool:
        tok = self.headers.get("X-Token", "")
        try:
            want = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            want = ""
        if not want or tok != want:
            self._json({"ok": False, "error": "bad token"}, 403)
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        if not self._ok():
            return
        u = urlparse(self.path)
        if u.path == "/status":
            try:
                w = window()
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
            return self._json({"ok": True, "hwnd": w.hwnd, "frame": list(w.frame),
                               "size": list(w.frame_size),
                               "blocked": win.input_blocked(w)})
        if u.path == "/win":
            w = window()
            return self._json({"ok": True, "hwnd": w.hwnd, "title": w.title,
                               "frame": list(w.frame), "size": list(w.frame_size)})
        if u.path == "/snap":
            q = parse_qs(u.query)
            name = (q.get("path") or ["shots/_remote.png"])[0]
            out = (ROOT / name).resolve()
            if ROOT not in out.parents:
                return self._json({"ok": False, "error": "path outside repo"}, 400)
            with _lock:
                img = win.grab_window(window())
                out.parent.mkdir(parents=True, exist_ok=True)
                img.save(out)
            return self._json({"ok": True, "path": str(out), "size": list(img.size),
                               "note": win.capture_note()})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if not self._ok():
            return
        a = self._body()
        u = urlparse(self.path)
        w = window()
        pi = win.PostInput(w)
        try:
            with _lock:
                if u.path == "/click":
                    pi.click(int(a["x"]), int(a["y"]), settle=float(a.get("hold", 0.06)))
                elif u.path == "/press":
                    pi.down(int(a["x"]), int(a["y"]))
                elif u.path == "/release":
                    pi.up(int(a["x"]), int(a["y"]))
                elif u.path == "/drag":
                    win.drag_image(
                        w, int(a["x1"]), int(a["y1"]), int(a["x2"]), int(a["y2"]),
                        steps=int(a.get("steps", 20)),
                        pause=float(a.get("pause", 0.012)),
                        hold=float(a.get("hold", 0.12)),
                    )
                elif u.path == "/focus":
                    win.focus(w)
                else:
                    return self._json({"ok": False, "error": "not found"}, 404)
            return self._json({"ok": True})
        except Exception as e:
            log(f"{u.path} 失败: {e!r}")
            return self._json({"ok": False, "error": repr(e)}, 500)


def serve() -> int:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(os.urandom(16).hex(), encoding="utf-8")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"agent 启动 pid={os.getpid()} {HOST}:{PORT}")
    print(f"agent listening on http://{HOST}:{PORT} (pid={os.getpid()})", flush=True)
    srv.serve_forever()
    return 0


# ------------------------------------------------------------------ 客户端 / 启动器

def client():
    from urllib.request import Request, urlopen

    tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
    base = f"http://{HOST}:{PORT}"
    headers = {"X-Token": tok, "User-Agent": "MojinAgent/1.0"}

    def get(path: str, timeout: float = 5.0):
        r = Request(base + path, headers=headers)
        with urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(path: str, payload: dict, timeout: float = 5.0):
        data = json.dumps(payload).encode("utf-8")
        h = dict(headers, **{"Content-Type": "application/json"})
        r = Request(base + path, data=data, headers=h)
        with urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return get, post


def alive() -> bool:
    try:
        get, _ = client()
        return bool(get("/status", timeout=1.0).get("ok"))
    except Exception:
        return False


def start() -> int:
    if alive():
        print("agent 已在运行")
        return 0
    import subprocess
    if sys.platform != "win32":
        print("只支持 Windows")
        return 2
    exe = sys.executable
    py = str(Path(__file__).resolve())
    cmd = (f"Start-Process -FilePath '{exe}' "
           f"-ArgumentList '\"{py}\" --serve' -Verb RunAs -WindowStyle Hidden")
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=False)
    for _ in range(100):
        if alive():
            print("agent 已启动")
            return 0
        time.sleep(0.1)
    print("agent 没起来（UAC 被拒绝？）")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="（提权后）真正跑服务")
    ap.add_argument("--start", action="store_true", help="弹 UAC 后台启动")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("cmd", nargs="*", help="snap/click/press/release/drag/win")
    a = ap.parse_args()
    if a.serve:
        return serve()
    if a.start:
        return start()
    if a.stop:
        import urllib.request
        print("请用任务管理器结束 python.exe --serve，或重启电脑")
        return 0
    if a.status or not a.cmd:
        try:
            get, _ = client()
            print(json.dumps(get("/status"), ensure_ascii=False, indent=1))
            return 0
        except Exception as e:
            print("agent 未运行:", e)
            return 1
    get, post = client()
    c = a.cmd
    if c[0] == "snap":
        print(get("/snap?path=" + (c[1] if len(c) > 1 else "shots/_remote.png")))
    elif c[0] in ("click", "press", "release"):
        print(post("/" + c[0], {"x": int(c[1]), "y": int(c[2])}))
    elif c[0] == "drag":
        print(post("/drag", {"x1": int(c[1]), "y1": int(c[2]),
                             "x2": int(c[3]), "y2": int(c[4])}))
    else:
        print("用法: remote.py [--start|--status|snap <path>|click x y|drag x1 y1 x2 y2]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
