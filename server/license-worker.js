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

  // 管理接口只看 header 里的口令，不走 URL（免得进日志）
  if (path.startsWith("/api/admin/")) {
    const key = req.headers.get("X-Admin-Key") || "";
    if (!env.ADMIN_KEY || key !== env.ADMIN_KEY) return bad("管理口令不对", 401);
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
