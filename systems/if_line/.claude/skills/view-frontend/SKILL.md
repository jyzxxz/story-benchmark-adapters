---
name: view-frontend
description: 查看 if_line 的 web 前端界面。启动 vite dev server,只用固定测试账号 999@qq.com 登录,用 Playwright 截图任意页面。当需要看前端实际渲染效果、验证 UI 改动、或以登录态浏览某个项目时使用。
---

# 查看 if_line 前端界面

无浏览器 MCP 工具时,用「vite + 测试账号登录 + Playwright」三步查看真实登录态下的前端。

## 账号(铁律)

**只能用这个账号登录:**

- 邮箱:`999@qq.com`
- 密码:`98765432f`

**禁止动任何其他账号**:不查/不改别人的密码,不给别的用户签发会话,不用
`create_user_session` 之类的数据库手段造登录态。999@qq.com(user 87)本身就是
主要测试项目的属主(含项目82),用它就够了。

## 前置检查

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:60002/docs   # 真后端,期望 200
```

注意有两个 app.main 实例:8000(miniconda 起的旧实例,v1 API)和
**60002(venv --reload,当前代码,前端代理指向它)**。一律用 60002。
后端没跑时按 [[backend-outage-runbook]] 处理(uvicorn + celery worker + beat
三件套,查 logs/uvicorn.log)。

## 1. 启动前端

```bash
cd /home/workspace/fengbohan/if_line/frontend
(npm run dev > /tmp/vite.log 2>&1 &)
sleep 4 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173   # 期望 200
```

vite 已配好 `/api`、`/static` 代理到 60002,无需改配置。

## 2. 登录拿 cookie

```bash
curl -s -c /tmp/cookie.txt -X POST http://127.0.0.1:60002/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"999@qq.com","password":"98765432f"}' | head -c 200
grep -i sid /tmp/cookie.txt   # 应看到 HttpOnly 的 sid cookie
```

登录失败时先确认打的是 60002 而不是 8000(旧实例没有 /api/auth/login)。

## 3. Playwright 截图

frontend 的 node_modules 里自带 playwright,脚本必须放在 frontend 目录下运行
(ESM 从脚本所在目录解析依赖):

```bash
cd /home/workspace/fengbohan/if_line/frontend
cat > /tmp/shot.mjs <<'EOF'
import { chromium } from 'playwright';
import fs from 'fs';
const [name, val] = fs.readFileSync('/tmp/cookie.txt', 'utf8').trim().split('=');
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1680, height: 1000 } });
await ctx.addCookies([{ name, value: val, domain: '127.0.0.1', path: '/' }]);
const p = await ctx.newPage();
p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE-ERR:', m.text().slice(0, 200)); });
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle', timeout: 60000 });
await p.waitForTimeout(2000);
await p.screenshot({ path: '/tmp/ui.png', fullPage: false });
console.log('URL:', p.url());
await b.close();
EOF
cp /tmp/shot.mjs ./shot.mjs && node shot.mjs; rm -f shot.mjs
```

常用路由(见 `frontend/src/router/index.ts`):

| 路由 | 页面 |
|---|---|
| `/` | 项目列表(我的项目 / 公开作品) |
| `/project/:id` | 项目编辑(故事设定/章节大纲/路径章节/分支候选) |
| `/project/:id/stats` | 项目统计 |
| `/vn-graph-player` | VN 图播放器 |
| `/create` | 新建项目 |

截完用 Read 工具查看 PNG(多模态可读图)。要交互(点标签、滚动)就在脚本里加
`p.click(...)` 后再截图;SPA 路由跳转后 URL 会带 `?path=...&chapter=...` 参数。

## 清理

```bash
pkill -f vite   # 登录态留在 cookie 里自动过期即可,无需注销
```
