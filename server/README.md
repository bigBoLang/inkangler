# 授权服务器（Cloudflare）

一个文件、零依赖，两种用法，接口完全一样：

| 用法 | 文件放哪 | 地址 | 适合 |
|---|---|---|---|
| **Pages Function**（推荐） | 博客仓库 `functions/api/[[path]].js` | `https://blog.dabolang.dpdns.org/api/...` | 你已经配好 GitHub → Cloudflare Pages 自动部署 |
| 独立 Worker | `server/license-worker.js` 用 API 上传 | `https://<名字>.<子域>.workers.dev` 或自定义域 | 不想动博客，或用 `tools/deploy_server.py` 一键部署 |

因为同一个文件里同时导出了 `onRequest`（Pages）和 `default.fetch`（Worker），所以内容一模一样，
只是放的位置不同。

---

## 用法一：放进博客仓库（自动部署）

1. 在仓库根目录建文件：`functions/api/[[path]].js`，内容 = `server/license-worker.js`
   （工作区里已经准备好一份：`cfsite/functions/api/[[path]].js`，整块拖进 GitHub 网页即可）
2. Pages 会自动重新部署。部署完后访问
   `https://blog.dabolang.dpdns.org/api/health` 应返回 `{"ok":true,...}`
3. 去 Cloudflare 面板 → **Workers & Pages → 你的 Pages 项目 → Settings → Functions**：
   - **KV namespace bindings**：建一个 KV 命名空间（名字随便，比如 `moji-license`），
     变量名必须叫 **`LIC`**
   - **Environment variables**（Production，类型选 *Secret*）：
     - `LICENSE_KEY` = Ed25519 私钥的 PKCS#8 DER base64（下面有生成命令）
     - `ADMIN_KEY` = 管理口令（自己随便定一串长的）

> ⚠️ 少了 `LIC` 绑定，`/api/activate` 会报 “KV ... undefined”；
> 少了 `LICENSE_KEY`，签名会失败。三个都得配。

生成 `LICENSE_KEY`（在你自己的电脑上跑，输出**只贴进 Cloudflare 面板**）：

```bash
python -c "import base64;from cryptography.hazmat.primitives import serialization as s;sk=s.load_pem_private_key(open('tools/_license/private.pem','rb').read(),password=None);print(base64.b64encode(sk.private_bytes(s.Encoding.DER,s.PrivateFormat.PKCS8,s.NoEncryption())).decode())"
```

---

## 用法二：独立 Worker（不用 GitHub）

```bash
set CF_API_TOKEN=...        # 权限见 tools/deploy_server.py 顶部注释
python tools/deploy_server.py --host auth.dabolang.dpdns.org --zone dabolang.dpdns.org
```

脚本会自动建 KV、上传 Worker、设好两个 secret、绑域名，最后打印管理口令。

---

## 接口

```
GET  /api/health                                健康检查
POST /api/activate      {"machine","code"}      用激活码换授权
GET  /api/status?machine=XXXX-...               查激活状态/是否被吊销
POST /api/admin/codes   {"count","days","owner"} 生成激活码（X-Admin-Key）
GET  /api/admin/machines                        列出已激活机器（X-Admin-Key）
POST /api/admin/revoke  {"machine"}             吊销（X-Admin-Key）
```

日常操作直接用 `tools/lickey.py`：

```bash
python tools/lickey.py codes --owner 张三 --count 1     # 生成激活码
python tools/lickey.py machines                         # 谁激活了
python tools/lickey.py revoke 3LVH-Y4NM-SYI7-2J6S       # 吊销
```

（它读 `server/admin.key` 或环境变量 `MOJI_ADMIN_KEY`；服务器地址读 `MOJI_ACTIVATE_URL`，
默认 `https://blog.dabolang.dpdns.org`。）

---

## 数据（KV）

| key | 内容 |
|---|---|
| `code:<12位>` | `{owner, days, issued, used_by, used_at}`，用完就绑定机器 |
| `machine:<机器码>` | `{owner, code, issued, expires, at}` |
| `revoked:<机器码>` | `"1"` |

一码一机：`used_by` 已经是别的机器就直接 409。
同一台机器重新激活同一个码是允许的（重装系统不用重新要码）。
