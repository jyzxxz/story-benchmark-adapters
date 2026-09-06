# Contributing to InfiPlot

Thanks for your interest in contributing to InfiPlot! 🎉 We welcome bug
reports, feature ideas, code, docs, and everything in between.

[English](#contributing-to-infiplot) · [中文](#贡献指南)

---

## Contributing

### Sign the CLA

InfiPlot is open-sourced under
[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html). To let us use external
contributions alongside the project's other (including closed-source)
licensing, **every external contributor must sign our Contributor License
Agreement (CLA)** before a pull request can be merged:

1. Read the [CLA](./CLA.md). A non-binding Chinese reference translation is in
   [CLA.zh.md](./CLA.zh.md).
2. Open your pull request.
3. Reply to the PR with exactly:

   ```
   I have read the CLA Document and I hereby sign the CLA
   ```

You only need to sign **once**. The CLA bot will record your signature and
update the PR status. Project maintainers and bots are exempt automatically.

> By signing, you grant the project maintainers a license to use your
> contribution under AGPL-3.0 **and** any other terms (including proprietary /
> closed-source). See [CLA.md §2](./CLA.md) for the full terms.

### Development setup

You'll need **Node.js ≥ 22** and **pnpm**.

```bash
git clone https://github.com/<your-fork>/infiplot.git
cd infiplot
pnpm install
cp .env.example .env.local   # fill in your provider keys
pnpm dev                      # http://localhost:3000
```

For provider configuration, see the Configuration guide in
[README.md](./README.md).

### Making changes

1. Fork the repo and create a branch from `staging` (or `main`).
2. Make your changes. Keep them focused — one concern per PR.
3. Validate before pushing:

   ```bash
   pnpm typecheck   # tsc --noEmit
   pnpm lint        # next lint
   ```

   (There's no test suite, so typecheck + lint are the primary gates.)

4. Write a clear PR description and reference any related issues.

### Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/), scoped
where it helps. Match the style you see in `git log`:

```
feat(web): add login button to header
fix(play): restore voice retention after prefetch
perf(engine): overlap writer phase B with painting
chore(engine): bump runware timeout default
docs(readme): clarify provider configuration
```

Common scopes: `web`, `play`, `engine`, `api`, `image`, `tts`, `docs`.

### Where to look

- [`AGENTS.md`](./AGENTS.md) — the primary architectural guide; read the
  section relevant to your change before editing.
- [`lib/types/index.ts`](./lib/types/index.ts) — shared domain contracts.
- [`lib/engine/`](./lib/engine/) — core story engine.
- [`app/api/`](./app/api/) — serverless API routes.

### Reporting bugs & ideas

Open an [issue](https://github.com/zonghaoyuan/infiplot/issues). Include
reproduction steps, what you expected, and what you saw.

### Contact

hi@infiplot.com

---

## 贡献指南

### 签署 CLA

InfiPlot 以
[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) 协议开源。为了让我们
可以将外部贡献同时用于项目的其他（含闭源）授权，**每位外部贡献者在 PR 合并
前都必须签署《贡献者许可协议》（CLA）**：

1. 阅读 [CLA](./CLA.md)（中文参考译文见 [CLA.zh.md](./CLA.zh.md)）。
2. 提交你的 pull request。
3. 在该 PR 中回复以下内容（一字不差）：

   ```
   I have read the CLA Document and I hereby sign the CLA
   ```

你只需签署**一次**。CLA bot 会记录你的签名并更新 PR 状态。项目维护者与
bot 账户会自动豁免。

> 签署即表示你授予项目维护者一项许可：可依 AGPL-3.0 **及任何其他条款**
> （含专有 / 闭源条款）使用你的贡献。完整条款见 [CLA.md §2](./CLA.md)。

### 开发环境

需要 **Node.js ≥ 22** 和 **pnpm**。

```bash
git clone https://github.com/<你的 fork>/infiplot.git
cd infiplot
pnpm install
cp .env.example .env.local   # 填入你的供应商密钥
pnpm dev                      # http://localhost:3000
```

供应商配置请参阅 [README.md](./README.md) 中的配置教程。

### 修改流程

1. fork 仓库，从 `staging`（或 `main`）创建分支。
2. 修改代码。保持聚焦——一个 PR 只解决一个问题。
3. 推送前自检：

   ```bash
   pnpm typecheck   # tsc --noEmit
   pnpm lint        # next lint
   ```

   （本项目没有测试套件，typecheck 与 lint 是主要校验手段。）

4. 写清楚 PR 描述，关联相关 issue。

### 提交信息

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范，
必要时带上 scope。参考 `git log` 中的现有风格：

```
feat(web): 给页头加登录按钮
fix(play): 修复预取后语音丢失的问题
perf(engine): 让 writer phase B 与绘画重叠
chore(engine): 提高 runware 超时默认值
docs(readme): 补充供应商配置说明
```

常用 scope：`web`、`play`、`engine`、`api`、`image`、`tts`、`docs`。

### 哪里看代码

- [`AGENTS.md`](./AGENTS.md)——主要的架构指南，改代码前请先读相关章节。
- [`lib/types/index.ts`](./lib/types/index.ts)——共享领域契约。
- [`lib/engine/`](./lib/engine/)——核心剧情引擎。
- [`app/api/`](./app/api/)——serverless API 路由。

### 反馈 Bug 与想法

欢迎开 [issue](https://github.com/zonghaoyuan/infiplot/issues)，请附复现
步骤、期望行为与实际现象。

### 联系方式

hi@infiplot.com
