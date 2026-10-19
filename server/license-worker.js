/**
 * 墨迹大侠自动钓鱼 —— 激活 / 授权服务器（Cloudflare Worker，单文件、无依赖）
 *
 * 它做什么
 * --------
 * 发「一次性激活码」，用户拿机器码 + 激活码来换一份绑定该机器的授权，
 * 授权用 Ed25519 私钥签名（私钥只存在 Worker secret 里，exe 里只有公钥）。
 * 换台电脑：机器码变了，同一条激活码绑不上（已绑定别的机器就直接拒绝）。
 *
 * 接口
 * ----
 *   GET  /api/health                                健康检查
 *   GET  /api/admin                                 管理网页（浏览器打开，口令在页面里输）
 *   POST /api/activate      {machine, code}          用激活码换授权
 *   GET  /api/status?machine=XXXX-...               查这台机器授权状态
 *   POST /api/admin/codes   {count, days, owner}     生成激活码（要 X-Admin-Key）
 *   GET  /api/admin/machines                         列出已激活机器（要 X-Admin-Key）
 *   POST /api/admin/revoke  {machine}                吊销一台机器（要 X-Admin-Key）
 *
 * 绑定（wrangler.toml / 部署脚本里配）
 * ------------------------------------
 *   LIC            KV 命名空间，存激活码和机器记录
 *   LICENSE_KEY    Ed25519 私钥，PKCS#8 DER 的 base64（secret）
 *   ADMIN_KEY      管理接口口令（secret）
 */

const APP_ID = "mojidaxia-fishing-v1";
const PREFIX = "MOJI1.";
const CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";   // Crockford：去掉了 I L O U
const CODE_LEN = 12;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,X-Admin-Key",
  "Access-Control-Max-Age": "86400",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS },
  });
}

const ok = (extra = {}) => json({ ok: true, ...extra });
const bad = (msg, status = 400) => json({ ok: false, error: msg }, status);

// ---------------------------------------------------------------- 工具
function normMachine(s) {
  return String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, "").replace(/(.{4})(?=.)/g, "$1-");
}

function normCode(s) {
  return String(s || "").toUpperCase().replace(/[^0-9A-Z]/g, "").replace(/[ILOU]/g, "");
}

function fmtCode(raw) {
  return raw.replace(/(.{4})(?=.)/g, "$1-");
}

function newCode() {
  const buf = new Uint8Array(CODE_LEN);
  crypto.getRandomValues(buf);
  let s = "";
  for (let i = 0; i < CODE_LEN; i++) s += CODE_ALPHABET[buf[i] % CODE_ALPHABET.length];
  return s;
}

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

function fromB64(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * 规范化 JSON：键按字母序、无空格，**必须和 Python 端 lic.canon() 逐字节一致**。
 * Python: json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 */
function canon(p) {
  return JSON.stringify({
    app: p.app,
    expires: p.expires,
    issued: p.issued,
    machine: p.machine,
    owner: p.owner,
  });
}

async function signLicense(payload, env) {
  const der = fromB64(env.LICENSE_KEY);
  // Workers 里叫 NODE-ED25519；别的运行时（本地 Chrome 测试）叫 Ed25519。两个都试。
  let key = null, algName = "";
  for (const alg of [{ name: "NODE-ED25519", namedCurve: "NODE-ED25519" }, { name: "Ed25519" }]) {
    try {
      key = await crypto.subtle.importKey("pkcs8", der, alg, false, ["sign"]);
      algName = alg.name;
      break;
    } catch {
      // 试下一个
    }
  }
  if (!key) throw new Error("运行时不支持 Ed25519");
  const sig = new Uint8Array(await crypto.subtle.sign(
    { name: algName }, key, new TextEncoder().encode(canon(payload))));
  const body = { ...payload, sig: b64(sig) };
  return PREFIX + b64url(new TextEncoder().encode(JSON.stringify(body)));
}

async function rateLimit(env, ip, limit = 30) {
  const hour = Math.floor(Date.now() / 3600000);
  const key = `rl:${ip}:${hour}`;
  const n = parseInt(await env.LIC.get(key), 10) || 0;
  if (n >= limit) return false;
  await env.LIC.put(key, String(n + 1), { expirationTtl: 7200 });
  return true;
}

function clientIp(req) {
  return req.headers.get("CF-Connecting-IP") || "?";
}

// 定长比较，别让口令逐字节试探出长度差异
function safeEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  let d = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) d |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return d === 0;
}

// ---------------------------------------------------------------- 接口
async function activate(req, env) {
  let body;
  try {
    body = await req.json();
  } catch {
    return bad("请求格式不对");
  }
  const machine = normMachine(body.machine);
  const code = normCode(body.code);
  if (machine.length < 12) return bad("机器码不对");
  if (code.length !== CODE_LEN) return bad("激活码是 %d 位（形如 ABCD-EFGH-JKMN）".replace("%d", CODE_LEN));

  if (!(await rateLimit(env, clientIp(req)))) return bad("试得太频繁了，过一会儿再试", 429);

  const ck = `code:${code}`;
  const raw = await env.LIC.get(ck);
  if (!raw) return bad("激活码不存在，检查一下有没有打错", 404);
  const rec = JSON.parse(raw);

  if (rec.used_by && rec.used_by !== machine) {
    return bad("这个激活码已经绑定到另一台机器了（一码一机）", 409);
  }
  if (await env.LIC.get(`revoked:${machine}`)) {
    return bad("这台机器已被吊销，请联系作者", 403);
  }

  const now = Math.floor(Date.now() / 1000);
  const expires = rec.days ? now + rec.days * 86400 : 0;
  const payload = {
    app: APP_ID,
    machine,
    owner: rec.owner || "",
    issued: rec.issued || now,
    expires,
  };
  const license = await signLicense(payload, env);

  // 绑定：同一个码再激活同一台机器是幂等的（重装系统/换硬盘后还能再要）
  rec.used_by = machine;
  rec.used_at = now;
  await env.LIC.put(ck, JSON.stringify(rec));
  await env.LIC.put(`machine:${machine}`, JSON.stringify({
    owner: payload.owner, code, issued: payload.issued, expires, at: now,
  }));

  return ok({ license, owner: payload.owner, expires, machine });
}

async function status(req, env, url) {
  const machine = normMachine(url.searchParams.get("machine"));
  if (machine.length < 12) return bad("机器码不对");
  if (await env.LIC.get(`revoked:${machine}`)) return ok({ activated: true, revoked: true });
  const raw = await env.LIC.get(`machine:${machine}`);
  if (!raw) return ok({ activated: false });
  const rec = JSON.parse(raw);
  return ok({ activated: true, revoked: false, owner: rec.owner, expires: rec.expires });
}

async function adminCodes(req, env) {
  const body = await req.json().catch(() => ({}));
  const count = Math.min(Math.max(parseInt(body.count, 10) || 1, 1), 200);
  const days = parseInt(body.days, 10) || 0;
  const owner = String(body.owner || "").slice(0, 40);
  const now = Math.floor(Date.now() / 1000);
  const out = [];
  for (let i = 0; i < count; i++) {
    const code = newCode();
    await env.LIC.put(`code:${code}`, JSON.stringify({ owner, days, issued: now }));
    out.push(fmtCode(code));
  }
  return ok({ codes: out, days, owner });
}

async function adminMachines(env) {
  const list = await env.LIC.list({ prefix: "machine:", limit: 1000 });
  const rows = [];
  for (const k of list.keys) {
    const rec = JSON.parse((await env.LIC.get(k.name)) || "{}");
    rows.push({ machine: k.name.slice(8), ...rec });
  }
  return ok({ count: rows.length, machines: rows });
}

async function adminRevoke(req, env) {
  const body = await req.json().catch(() => ({}));
  const machine = normMachine(body.machine);
  if (machine.length < 12) return bad("机器码不对");
  await env.LIC.put(`revoked:${machine}`, "1");
  return ok({ machine });
}

// ---------------------------------------------------------------- 管理网页
// 走同一个文件发出，随 API 一起部署；页面本身不含任何密钥，
// 口令由用户在浏览器里输入，只存 sessionStorage，关掉标签页就没了。
function htmlPage(body) {
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
    },
  });
}

const ADMIN_PAGE = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>墨迹大侠 · 授权管理</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0f1115;color:#e6e8ee;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:18px 14px 60px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:14px}
h1{font-size:19px;margin:0;font-weight:600}
.muted{color:#8b93a7;font-size:13px}
.card{background:#171a21;border:1px solid #232833;border-radius:12px;padding:14px;margin-bottom:14px}
label{display:block;font-size:13px;color:#aab2c5;margin:0 0 10px}
input{width:100%;margin-top:5px;background:#0f1115;border:1px solid #2b3140;color:#e6e8ee;border-radius:9px;padding:10px 11px;font:inherit;outline:none}
input:focus{border-color:#3d7bfd}
button{background:#232833;color:#e6e8ee;border:1px solid #2f3646;border-radius:9px;padding:10px 14px;font:inherit;cursor:pointer}
button:hover{background:#2b3140}
button.primary{background:#3d7bfd;border-color:#3d7bfd;color:#fff;font-weight:600}
button.primary:hover{background:#3168d8}
button:disabled{opacity:.55;cursor:default}
.row{display:flex;gap:10px;align-items:center}
.row input{flex:1;margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tabs{display:flex;gap:8px;margin-bottom:12px}
.tabs button{flex:1;padding:9px}
.tabs button.on{background:#3d7bfd;border-color:#3d7bfd;color:#fff}
.out{margin-top:14px}
.out h3{font-size:14px;font-weight:500;color:#aab2c5;margin:0 0 10px}
.code{display:flex;align-items:center;justify-content:space-between;gap:10px;background:#0f1115;border:1px dashed #2f3646;border-radius:9px;padding:10px 12px;margin-bottom:8px;font:600 16px/1.2 ui-monospace,Consolas,monospace;letter-spacing:.5px}
.code button{padding:6px 10px;font-size:13px;font-weight:400}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #232833;vertical-align:top}
th{color:#8b93a7;font-weight:500;white-space:nowrap}
td.mono{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;white-space:nowrap}
.badge{display:inline-block;padding:1px 8px;border-radius:20px;font-size:12px;background:#2a3040;color:#aab2c5}
.badge.perm{background:#1d3a2a;color:#6ee7a0}
.badge.exp{background:#3a2a1d;color:#f5b26b}
.badge.dead{background:#3a1d24;color:#f87171}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#232833;border:1px solid #3d7bfd;padding:10px 16px;border-radius:10px;font-size:14px;box-shadow:0 8px 24px #0008;max-width:90vw;z-index:9}
.toast.err{border-color:#f87171}
.empty{color:#8b93a7;font-size:13.5px;padding:14px 4px}
@media (max-width:520px){.grid{grid-template-columns:1fr}.card{padding:12px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>墨迹大侠 · 授权管理</h1>
    <span id="who" class="muted">未登录</span>
  </header>

  <section id="login" class="card">
    <label>管理口令
      <input id="key" type="password" placeholder="X-Admin-Key" autocomplete="current-password">
    </label>
    <button id="btnLogin" class="primary">进入</button>
    <p class="muted" style="margin:10px 0 0">口令只保存在这个标签页里，关掉就没了。</p>
  </section>

  <main id="app" hidden>
    <nav class="tabs">
      <button id="tabGen" class="on">生成激活码</button>
      <button id="tabList">已激活机器</button>
    </nav>

    <section id="paneGen" class="card">
      <div class="grid">
        <label>使用人<input id="owner" placeholder="张三" maxlength="40"></label>
        <label>数量<input id="count" type="number" value="1" min="1" max="200"></label>
        <label>有效期（天）<input id="days" type="number" value="0" min="0" max="3650"></label>
      </div>
      <p class="muted" style="margin:0 0 12px">有效期填 0 = 永久。一码一机，谁先激活就绑死谁。</p>
      <button id="btnGen" class="primary">生成</button>
      <div id="out" class="out"></div>
    </section>

    <section id="paneList" class="card" hidden>
      <div class="row" style="margin-bottom:12px">
        <button id="btnRefresh">刷新</button>
        <span id="listInfo" class="muted"></span>
      </div>
      <div id="machines"></div>
    </section>
  </main>

  <div id="toast" class="toast" hidden></div>
</div>
<script>
(function () {
  var $ = function (s) { return document.querySelector(s); };
  var KEY = sessionStorage.getItem("moji_admin_key") || "";
  var timer = 0;

  function toast(msg, kind) {
    var t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (kind === "err" ? " err" : "");
    t.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(function () { t.hidden = true; }, 2800);
  }

  function copy(text) {
    var done = function () { toast("已复制 " + text); };
    var fallback = function () {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); }
      catch (e) { toast("复制失败，手动选一下", "err"); }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else {
      fallback();
    }
  }

  function api(path, opt) {
    opt = opt || {};
    var headers = { "Content-Type": "application/json" };
    if (KEY) headers["X-Admin-Key"] = KEY;
    return fetch(path, { method: opt.method || "GET", headers: headers, body: opt.body })
      .then(function (r) {
        return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; });
      })
      .then(function (j) {
        if (!j.ok) throw new Error(j.error || "请求失败");
        return j;
      });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function ts(sec) {
    if (!sec) return "—";
    var d = new Date(sec * 1000);
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function loggedIn(yes) {
    $("#login").hidden = yes;
    $("#app").hidden = !yes;
    $("#who").textContent = yes ? "已登录" : "未登录";
  }

  function enter(k) {
    KEY = String(k || "").trim();
    if (!KEY) { toast("先填口令", "err"); return; }
    api("/api/admin/machines").then(function (j) {
      sessionStorage.setItem("moji_admin_key", KEY);
      $("#key").value = "";
      loggedIn(true);
      renderMachines(j);
    }).catch(function (e) {
      KEY = "";
      sessionStorage.removeItem("moji_admin_key");
      loggedIn(false);
      toast(e.message, "err");
    });
  }

  function renderMachines(j) {
    var box = $("#machines");
    box.textContent = "";
    var rows = (j.machines || []).slice().sort(function (a, b) { return (b.at || 0) - (a.at || 0); });
    $("#listInfo").textContent = "共 " + rows.length + " 台";
    if (!rows.length) {
      var e = document.createElement("div");
      e.className = "empty";
      e.textContent = "还没有机器激活过。";
      box.appendChild(e);
      return;
    }
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var htr = document.createElement("tr");
    ["机器码", "使用人", "激活时间", "有效期", ""].forEach(function (t) {
      var th = document.createElement("th");
      th.textContent = t;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    rows.forEach(function (m) {
      var tr = document.createElement("tr");

      var tdM = document.createElement("td");
      tdM.className = "mono";
      tdM.textContent = m.machine || "";
      tr.appendChild(tdM);

      var tdO = document.createElement("td");
      tdO.textContent = m.owner || "—";
      tr.appendChild(tdO);

      var tdT = document.createElement("td");
      tdT.textContent = ts(m.at);
      tr.appendChild(tdT);

      var tdE = document.createElement("td");
      var b = document.createElement("span");
      b.className = "badge";
      if (!m.expires) {
        b.className += " perm";
        b.textContent = "永久";
      } else if (m.expires * 1000 < Date.now()) {
        b.className += " dead";
        b.textContent = "已过期 " + ts(m.expires);
      } else {
        b.className += " exp";
        b.textContent = ts(m.expires);
      }
      tdE.appendChild(b);
      tr.appendChild(tdE);

      var tdA = document.createElement("td");
      var btn = document.createElement("button");
      btn.textContent = "吊销";
      btn.onclick = function () {
        if (!confirm("吊销 " + m.machine + " ？\\n吊销后这台机器不能再激活（已发出去的 license.key 仍可离线使用）。")) return;
        btn.disabled = true;
        api("/api/admin/revoke", { method: "POST", body: JSON.stringify({ machine: m.machine }) })
          .then(function () { toast("已吊销"); return api("/api/admin/machines"); })
          .then(renderMachines)
          .catch(function (e) { toast(e.message, "err"); })
          .finally(function () { btn.disabled = false; });
      };
      tdA.appendChild(btn);
      tr.appendChild(tdA);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  function showCodes(codes, owner, days) {
    var out = $("#out");
    out.textContent = "";
    if (!codes || !codes.length) return;

    var h = document.createElement("h3");
    h.textContent = (owner ? "使用人：" + owner + " · " : "") +
      (days ? days + " 天" : "永久") + " · 共 " + codes.length + " 枚";
    out.appendChild(h);

    codes.forEach(function (c) {
      var row = document.createElement("div");
      row.className = "code";
      var span = document.createElement("span");
      span.textContent = c;
      var b = document.createElement("button");
      b.textContent = "复制";
      b.onclick = function () { copy(c); };
      row.appendChild(span);
      row.appendChild(b);
      out.appendChild(row);
    });

    var all = document.createElement("button");
    all.textContent = "复制全部";
    all.onclick = function () { copy(codes.join("\\n")); };
    out.appendChild(all);
  }

  $("#btnLogin").onclick = function () { enter($("#key").value); };
  $("#key").addEventListener("keydown", function (e) {
    if (e.key === "Enter") enter($("#key").value);
  });

  $("#tabGen").onclick = function () {
    $("#tabGen").className = "on";
    $("#tabList").className = "";
    $("#paneGen").hidden = false;
    $("#paneList").hidden = true;
  };
  $("#tabList").onclick = function () {
    $("#tabList").className = "on";
    $("#tabGen").className = "";
    $("#paneGen").hidden = true;
    $("#paneList").hidden = false;
    refresh();
  };

  function refresh() {
    $("#btnRefresh").disabled = true;
    api("/api/admin/machines")
      .then(renderMachines)
      .catch(function (e) {
        toast(e.message, "err");
        if (/口令/.test(e.message)) { KEY = ""; sessionStorage.removeItem("moji_admin_key"); loggedIn(false); }
      })
      .finally(function () { $("#btnRefresh").disabled = false; });
  }
  $("#btnRefresh").onclick = refresh;

  $("#btnGen").onclick = function () {
    var btn = this;
    var body = {
      owner: $("#owner").value.trim(),
      count: parseInt($("#count").value, 10) || 1,
      days: parseInt($("#days").value, 10) || 0
    };
    btn.disabled = true;
    btn.textContent = "生成中…";
    api("/api/admin/codes", { method: "POST", body: JSON.stringify(body) })
      .then(function (j) { showCodes(j.codes, j.owner, j.days); toast("已生成 " + j.codes.length + " 枚"); })
      .catch(function (e) { toast(e.message, "err"); })
      .finally(function () { btn.disabled = false; btn.textContent = "生成"; });
  };

  if (KEY) enter(KEY);
})();
</script>
</body>
</html>`;

// ---------------------------------------------------------------- 入口
// 同一个文件既能当 Cloudflare Worker 用（export default），
// 也能放进博客仓库的 functions/api/[[path]].js 当 Pages Function 用（onRequest）。
async function handle(req, env) {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  if (path === "/" || path === "/api/health") {
    return ok({ name: "墨迹大侠授权服务", time: new Date().toISOString(), kv: !!env.LIC });
  }

  // 配置没做全时给一句人话，别甩 500
  if (!env.LIC) return bad("服务器没绑 KV：Pages → Settings → Functions → KV 绑定，变量名要叫 LIC", 500);
  if (!env.LICENSE_KEY) return bad("服务器没配密钥：环境变量 LICENSE_KEY（secret）", 500);

  // 管理网页（页面本身不含密钥）
  if (path === "/api/admin" && req.method === "GET") return htmlPage(ADMIN_PAGE);

  // 管理接口只看 header 里的口令，不走 URL（免得进日志）
  if (path.startsWith("/api/admin/")) {
    const ip = clientIp(req);
    const failKey = `adminfail:${ip}`;
    if ((parseInt(await env.LIC.get(failKey), 10) || 0) >= 10) {
      return bad("口令试错太多次，10 分钟后再试", 429);
    }
    const key = req.headers.get("X-Admin-Key") || "";
    if (!env.ADMIN_KEY || !safeEq(key, env.ADMIN_KEY)) {
      const n = (parseInt(await env.LIC.get(failKey), 10) || 0) + 1;
      await env.LIC.put(failKey, String(n), { expirationTtl: 600 });
      return bad("管理口令不对", 401);
    }
    await env.LIC.delete(failKey).catch(() => {});
    if (path === "/api/admin/codes" && req.method === "POST") return adminCodes(req, env);
    if (path === "/api/admin/machines" && req.method === "GET") return adminMachines(env);
    if (path === "/api/admin/revoke" && req.method === "POST") return adminRevoke(req, env);
    return bad("没有这个接口", 404);
  }

  if (path === "/api/activate" && req.method === "POST") return activate(req, env);
  if (path === "/api/status" && req.method === "GET") return status(req, env, url);

  return bad("没有这个接口", 404);
}

export default {
  fetch(req, env) {
    return handle(req, env);
  },
};

// Pages Functions 用这个入口
export function onRequest(context) {
  return handle(context.request, context.env);
}
