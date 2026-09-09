# 历史 v2 运行说明

> **已停用的操作指南：v2 / 初始 `9bdecf7` 阶段。**下文原样保留旧依赖安装、C1 接入和首批文本验收口径，供解释历史结果；不再作为新机器安装步骤。接手图文批量实验请使用[操作者交接说明](OPERATOR_HANDOFF.md)和[快速上手](QUICKSTART.md)。[RUNNING.md](RUNNING.md)也是旧 v3 文本兼容入口，不是当前图文生产入口。下文的图片关闭、预算上限、源码未修改及未准备其余题目的描述均属于旧版本。

## 归档原文：安装与运行

本包提供第一期接入工具：把同一个 `shared_task.txt` 交给三个原生系统，保存实际请求、原始产物和首批正文。`systems/` 是冻结的原始源码，适配行为全部位于 `benchmark/`；不要把运行环境、密钥或旧产物写入 `systems/`。

初始版本 `9bdecf7` 的接入验证使用本地固定响应供应商。后续真实模型验证与失败记录单独保存在 [真实输出验证](LIVE_VALIDATION.md)。示例配置仍保留 `live=false`，模型、供应商和预算为 `null`。安装依赖、编译题目和运行离线测试不需要模型密钥；真实生成要在实验条件确定后另外配置。

## 1. 建立独立依赖环境

以下命令从本仓库根目录执行。假设 `python3.12`、`python3.10`、Node.js 22 或更高版本以及 npm 已在 PATH 中；已有合适的独立环境也可以直接使用，并在配置中填写解释器的绝对路径。

```bash
cd /绝对路径/story-benchmark-adapters
BENCH_ROOT="$PWD"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$BENCH_ROOT/work/envs" "$BENCH_ROOT/work/dependencies"

python3.12 -m venv "$BENCH_ROOT/work/envs/runner"
python3.12 -m venv "$BENCH_ROOT/work/envs/if_line"
python3.10 -m venv "$BENCH_ROOT/work/envs/ai4vn"

"$BENCH_ROOT/work/envs/if_line/bin/python" -m pip install -r "$BENCH_ROOT/benchmark/native_shims/if_line/requirements-text.txt"
"$BENCH_ROOT/work/envs/ai4vn/bin/python" -m pip install -r "$BENCH_ROOT/systems/AI4VisualNovel/requirements.txt"

"$BENCH_ROOT/work/envs/if_line/bin/python" -m pip check
"$BENCH_ROOT/work/envs/ai4vn/bin/python" -m pip check
"$BENCH_ROOT/work/envs/if_line/bin/python" -m pip freeze > "$BENCH_ROOT/work/dependencies/if_line.freeze.txt"
"$BENCH_ROOT/work/envs/ai4vn/bin/python" -m pip freeze > "$BENCH_ROOT/work/dependencies/ai4vn.freeze.txt"

RUNNER_PYTHON="$BENCH_ROOT/work/envs/runner/bin/python"
export PYTHONPATH="$BENCH_ROOT/benchmark"
```

公共 runner 使用 Python 标准库。IF Line 和 AI4VisualNovel 分别使用独立依赖环境；不要合并安装。IF Line 使用外置文字依赖清单，省去本期不调用的图像运行库；原始 requirements 文件保留不变。上游 requirements 中存在版本范围，所以每次正式环境都应保留实际安装后的 freeze 文件，不能仅凭相同 requirements 就声称依赖完全相同。本次 AI4VisualNovel 工程验证使用 Python 3.10.20；IF Line 使用 Python 3.12。

InfiPlot 使用原生 `pnpm-lock.yaml`。先在独立目录安装 pnpm 并填充依赖缓存，原始源码目录只用于读取清单：

```bash
npm install --prefix "$BENCH_ROOT/work/node-tools" pnpm@9.12.0
export PATH="$BENCH_ROOT/work/node-tools/node_modules/.bin:$PATH"

mkdir -p "$BENCH_ROOT/work/dependency-cache/infiplot"
cp "$BENCH_ROOT/systems/infiplot/package.json" "$BENCH_ROOT/work/dependency-cache/infiplot/"
cp "$BENCH_ROOT/systems/infiplot/pnpm-lock.yaml" "$BENCH_ROOT/work/dependency-cache/infiplot/"

cd "$BENCH_ROOT/work/dependency-cache/infiplot"
pnpm --version
pnpm install --frozen-lockfile
cd "$BENCH_ROOT"
```

pnpm 版本应为 9.12.0。实际 InfiPlot 运行时，适配器会另建源码副本并执行 `pnpm install --frozen-lockfile --offline`，随后从该副本启动 Next.js。若离线安装报缓存缺失，先完成上面的缓存准备；不要到 `systems/infiplot` 内补装或修改文件。

## 2. 只编译一份公共题目

```bash
cd "$BENCH_ROOT/benchmark"
"$RUNNER_PYTHON" -m story_benchmark compile \
  --case cases/CAMPUS-01.json \
  --out "$BENCH_ROOT/work/compiled/CAMPUS-01" \
  --allow-pilot

"$RUNNER_PYTHON" -m story_benchmark verify \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01"
```

编译输出目录必须是新目录。已有 bundle 应执行 `verify`，不应分别给三个项目再编译或改写题目。三个适配器后续都使用这一目录中的同一个公共文件。

`CAMPUS-01` 当前是 `TEXT_CONTINUATION_DEV` 的 pilot 案例，其固定开头和时间解释用于接入调试，尚不是已审批的正式题库。`--allow-pilot` 只允许这一开发范围，不会将案例升级为正式批准。正式实验前需要审核并冻结案例材料，再使用相应批准记录；不要仅通过手改状态绕过审核。

## 3. 一份实验配置控制三个系统

首次使用时，把模板复制到同一目录，保留相对路径的含义：

```bash
cp -n "$BENCH_ROOT/benchmark/configs/experiment.example.json" \
  "$BENCH_ROOT/benchmark/configs/experiment.local.json"
```

所有相对路径都以配置文件所在目录为基准解析。若把配置放到其他位置，要相应调整路径；也可以填写绝对路径。模板中的 `repo_path` 指向 `systems/`，`source_lock` 指向根目录 `baseline-lock.json`。原生目录没有嵌套 `.git` 是预期情况，源码版本由完整文件哈希锁核验。

`common` 中只有一份共同条件，三个系统的专有配置不能覆盖这些字段：

| 共同字段 | 运行前填写内容 |
| --- | --- |
| `live` | 默认 false；确认开始真实接入后才设为 true。 |
| `model` | 三者共同请求的明确文字模型名称。 |
| `model_base_url` | 三者最终使用的共同供应商 endpoint，不含凭证或查询参数。 |
| `timeout_seconds` | 正数，根运行的时间上限；同时用于原生阶段或请求超时。 |
| `max_calls` | 正整数，每个系统的一次根运行允许的实际模型 HTTP 发送次数。 |
| `max_output_tokens` | 正整数，每次模型请求的输出上限；原生已有更低上限时保留较低值。 |
| `max_input_chars` | 正整数，应用输出上限后的完整紧凑 HTTP JSON 请求的 Unicode 码点上限。 |
| `allow_pilot` | 仅开发 profile 的 pilot 接入允许为 true；不能替代正式案例审核。 |
| `model_parameters` | 可选的共同模型控制，默认 `{}` 保留原生参数。当前允许 `thinking` 和 `reasoning_effort`，不接受 prompt、schema、模型或预算覆盖。 |

这些限制不是等金额预算。规划、正文、审核、原生重试和原生媒体翻译所用的文字调用都计入所属系统的根运行，不能重启或读档后重新获得预算。不同方法的原生结构、采样设置和较低输出上限仍可能不同，实际参数会留在请求记录中。

三个专有块还需要填写：

| 系统 | 主要专有字段 |
| --- | --- |
| `if_line` | `python_executable` 为 IF Line 环境解释器绝对路径；保持 `managed_runtime="native_services"`、`isolated_deployment=true`；填写 `redis_executable` 或使用明确的 `redis_url_env`；`database_url_env` 和 `model_api_key_env` 只保存环境变量名称。 |
| `ai4visualnovel` | `python_executable` 为 AI4VisualNovel 环境解释器绝对路径；当前仪表化路径为 `text_provider="openai"`，`api_key_env` 默认 `OPENAI_API_KEY`。 |
| `infiplot` | `base_url` 是适配器自己的本地 Next.js 地址，默认 `http://127.0.0.1:3217`，**不是供应商地址**；`cookie_env` 默认 `INFIPLOT_COOKIE`；保持 `shared_opening=true`。 |

IF Line 的 `chapter_count` 表示整部作品的规划章数，模板采用冻结版本原生界面的默认值 `13`；旧 `first_chapter` 模式只生成其中第一章。设置为 `1` 会触发原生单章完结规则，不能把它当作“只生成首章”的开关。AI4VisualNovel 仍先生成原生故事图和多节点剧本；InfiPlot 只请求第一幕。详见 [公平性边界](FAIRNESS.md)。

### 使用澄清后的 C1 开发案例

新版案例和旧版分开，不能把旧运行目录的输入或适配器代码直接替换后继续算同一次运行。在 `benchmark/` 目录中使用：

```bash
"$RUNNER_PYTHON" -m story_benchmark compile \
  --case cases/CAMPUS-01-C1.json \
  --out "$BENCH_ROOT/work/compiled/CAMPUS-01-C1" --allow-pilot
"$RUNNER_PYTHON" -m story_benchmark verify \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01-C1"
cp -n configs/experiment.c1.example.json configs/experiment.c1.local.json
```

按前文配置共同模型、预算和本地依赖。新模板只在 IF Line 专有配置增加 `entry_mode: provided_prefix_candidates`，仍不自带模型凭证或启用真实请求。之后对三者都使用同一个 `--experiment configs/experiment.c1.local.json` 和上述同一个 `--bundle`，每者使用新的独立 `--run-dir`。禁止只给某个项目换任务或增加公共预算。

新 IF Line 模式以原生 API 保存并激活公共开头，在显式外置结构检查点上生成两个未选择候选；已有原生人工修订在响应丢失时可精确查回。生成候选的正文保存在 `export/unselected_previews.jsonl`，不是 `export/generated.jsonl`。原生未提供独立选项标题时，`generation_status=unsupported_output_boundary`，`native_integration=verified_candidate_previews_only` 仅表示真实候选接口已验证，不表示共同玩家可见输出合格。部署与字段细节见 [IF Line 外置说明](../benchmark/native_shims/if_line/README.md)。

`export/boundary_audit.json` 记录技术停止与来源校验；`observed_requires_content_review` 仍需核对选项含义和固定事实。InfiPlot 的 `native/choice-provenance.json` 只比较 Writer 到导出的选项变化；AI4VisualNovel 的原生节点数量失败保留在 `native/ai4vn/native_failure.json`。这些状态都不能被当成自动剧情评分。

例如使用支持相应字段的 DeepSeek 模型时，可在唯一的 `common` 块设置 `"model_parameters": {"thinking": {"type": "disabled"}}`。三个适配器会在实际 HTTP JSON 上应用同一参数，再检查完整请求预算；审计核对每次 HTTP 请求是否匹配。`{}` 不重写原生参数；三个系统使用同一模式。具体支持情况以供应商文档为准。[DeepSeek 模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)

## 4. 配置凭证与 IF Line 基础设施

通过当前终端的私密环境配置提供以下值，不要写入公共题目或 JSON 配置。三个系统可以使用同一供应商凭证，但各自的环境变量名称不同：

| 环境变量 | 用途 |
| --- | --- |
| `OPENAI_API_KEY` | 默认供 AI4VisualNovel 和 IF Line 的文字服务使用。 |
| `TEXT_API_KEY` | InfiPlot 的文字服务凭证；最终供应商仍由共同 `model_base_url` 决定。 |
| `NEXT_PUBLIC_SUPABASE_URL` | InfiPlot 原生鉴权使用的 Supabase 地址。 |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | InfiPlot 原生 Supabase 公共配置。 |
| `INFIPLOT_COOKIE` | 有效的原生 Supabase 登录会话 Cookie 全串；适配器不替代登录。 |
| `IFLINE_BENCH_DATABASE_URL` | IF Line 的独立 PostgreSQL 数据库连接串。 |

InfiPlot 的 API 鉴权保持原样。它先访问需要鉴权的 `/api/tts-provider` 检查就绪，再请求 `/api/start`；没有有效 Cookie 时会停止。不能把本地测试中的虚构 Cookie 当作真实凭证。

IF Line 的 `native_services` 模式需要 PostgreSQL 和 Redis。管理器会启动自己的 API、Celery worker 和 beat；若配置 `redis_executable`，还会启动仅监听 loopback、无持久化的独立 Redis。不要把日常项目的 Redis 或数据库地址填进实验配置。

数据库必须是 PostgreSQL，且名称以 `if_line_bench_` 开头。下面给出 macOS 上独立本机 PostgreSQL 16 实例的准备方式；端口 55432 和数据目录必须尚未被使用。此处的 trust 鉴权仅用于这个本机隔离实例：

```bash
brew install postgresql@16 redis
BENCH_PG_BIN="$(brew --prefix postgresql@16)/bin"
mkdir -p "$BENCH_ROOT/work/services"

"$BENCH_PG_BIN/initdb" \
  -D "$BENCH_ROOT/work/services/pgdata" \
  -U bench_admin --auth=trust

"$BENCH_PG_BIN/pg_ctl" \
  -D "$BENCH_ROOT/work/services/pgdata" \
  -l "$BENCH_ROOT/work/services/postgresql.log" \
  -o "-h 127.0.0.1 -p 55432" start

"$BENCH_PG_BIN/createdb" -h 127.0.0.1 -p 55432 -U bench_admin if_line_bench_campus01_001
export IFLINE_BENCH_DATABASE_URL="postgresql+psycopg://bench_admin@127.0.0.1:55432/if_line_bench_campus01_001"
command -v redis-server
```

把最后一条输出的绝对路径填入 `systems.if_line.redis_executable`。也可以使用另行准备的 PostgreSQL 实例及其正常鉴权，仍须创建专用数据库并满足命名前缀。空库由管理器执行原生 Alembic `upgrade head`；非空库会先进行原生 schema 检查。独立库由使用者创建，管理器不会自动创建或删除 PostgreSQL 数据库。

每次新实验准备新的独立库和新的 run 目录。管理器正常退出会清理自己启动的 API/worker/beat/Redis；上面手动启动的 PostgreSQL 实例仍由使用者管理。结束使用后可停止该实例，数据目录会保留：

```bash
"$BENCH_PG_BIN/pg_ctl" -D "$BENCH_ROOT/work/services/pgdata" stop
```

## 5. 检查共同配置，再串行生成

模板的 `live=false` 和空预算会让运行预检明确拒绝，这是预期行为。准备真实接入时，先填写共同模型、endpoint、预算、专有路径和环境变量，再将同一配置中的 `live` 改为 true。以下 `preflight` 命令本身不调用生成模型：

```bash
cd "$BENCH_ROOT/benchmark"
"$RUNNER_PYTHON" -m story_benchmark preflight-set \
  --experiment configs/experiment.local.json \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01"

"$RUNNER_PYTHON" -m story_benchmark preflight \
  --system infiplot \
  --experiment configs/experiment.local.json \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01"
```

只有预检通过后才运行生成；下面三条命令会使用配置中的真实供应商，可能产生费用。真实验证记录见上述报告。三个命令按顺序单独运行，任何一次失败先保留并检查证据：

```bash
"$RUNNER_PYTHON" -m story_benchmark run-first \
  --system ai4visualnovel --experiment configs/experiment.local.json \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01" \
  --run-dir "$BENCH_ROOT/work/runs/campus01-ai4vn-001"

"$RUNNER_PYTHON" -m story_benchmark run-first \
  --system infiplot --experiment configs/experiment.local.json \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01" \
  --run-dir "$BENCH_ROOT/work/runs/campus01-infiplot-001"

"$RUNNER_PYTHON" -m story_benchmark run-first \
  --system if_line --experiment configs/experiment.local.json \
  --bundle "$BENCH_ROOT/work/compiled/CAMPUS-01" \
  --run-dir "$BENCH_ROOT/work/runs/campus01-ifline-001"
```

`--experiment` 会根据 `--system` 从单份配置取出对应专有块，并合并唯一的 `common`。旧的 `--config` 仍可用于独立工程调试，但两者互斥；三系统比较应使用同一份 `--experiment`，避免各自漂移。

不需要提前手工启动三套服务。AI4VisualNovel 由外置 launcher 调用原生 `main.py --mode design`，完成后调用 `--mode script`；IF Line 由管理器启动原生 API/worker/beat，使用原生 guest 接口取得 sid，再走 Bible、大纲、章节和 Script IR 的任务链；InfiPlot 由适配器启动临时原生 Next.js 副本和外部 relay。

运行目录必须全新。若请求已发送但响应丢失，状态会保留为 `delivery_unknown`，不要无痕重发。只恢复已保存产物的导出可使用：

```bash
"$RUNNER_PYTHON" -m story_benchmark resume-export \
  --run-dir "$BENCH_ROOT/work/runs/campus01-infiplot-001"
```

该命令先核对已有证据、配置与适配器版本哈希，不会补发生成请求。产物被改写或版本改变时会拒绝；无法证明已有产物的中断不会被自动恢复成第二次生成。

## 6. 阅读结果与验证范围

每个 run 保存 manifest、公共文本、实际请求/响应、原生产物、调用 trace、错误和 export。重点检查 `audit.json` 中的公共输入完整性、发送次数、请求模型、usage 覆盖率与来源映射。公共开头在 `export/provided_prefix.txt`，新增正文在 `export/generated.jsonl`，不要把开头字数计为系统新增字数。

InfiPlot 的收件证据边界是 `native_sdk_task_block`：公共文本从实际 SDK 消息中提取，包含 relay 适配前后的原始请求与哈希。它不是直接抓取原生路由内部的 `worldSetting` 字段，因此保留 `direct_native_route_receiver_observed=false`。未看到原生兜底日志不等于没有兜底，相关状态保持未知。

以下表格保留初始版本的本地固定响应验证范围；后续真实模型结果见 [真实输出验证](LIVE_VALIDATION.md)。

| 系统 | 初始版本已验证的工程范围 | 当时未验证或不在一期范围 |
| --- | --- | --- |
| AI4VisualNovel | 原生 design/script CLI、本地固定响应供应商、Designer/Producer/Actor/Writer 链、实际需求读取和 HTTP 证据、原始及运行副本哈希、首个选择或控制流边界导出。 | 付费模型结果；render/play；Google provider 和流式覆盖；完整条件/跳转/多分支回放。 |
| InfiPlot | 发布的无 `.git` 原始源码快照、真实 Next.js `/api/start`、未改鉴权代码下的本地 auth/provider fixture、JSON/SSE、接收块和精确替换、预算、首选择导出、源码不变及服务清理。 | 真实供应商故事质量和真实账号的完整部署验收；续场/读档/分支回放；缺少全面内部钩子时无法证明无兜底。 |
| IF Line | 发布源码快照、全新 PostgreSQL 16 独立库及原生 Alembic 迁移、独立 Redis、原生 API/Celery worker/beat 投递链、sid 鉴权、本地固定响应供应商的 5 次 HTTP 调用、首章与 Script IR、已有产物恢复不新增调用、源文件不变和进程清理。 | 付费模型结果与真实供应商故障表现；长期并发/生产部署；播放器和完整分支回放。 |

`native_services` 的真实 PostgreSQL/Redis/Celery 链已经用本地固定响应供应商完成一次接入验证；另有 `engineering_fixed_response` 模式使用 SQLite 和直接线程 worker，二者证据应明确区分。固定响应中的故事、用量和账号数据都是测试数据，不能计入论文样本或真实模型质量分数。

各适配器的具体配置和边界参见 [IF Line 外置适配说明](../benchmark/native_shims/if_line/README.md)、[AI4VisualNovel 外置适配说明](../benchmark/native_shims/ai4vn/README.md)、[InfiPlot 外置适配说明](../benchmark/native_shims/infiplot/README.md)；冻结源码与适配器版本见 [源码溯源](PROVENANCE.md)。
