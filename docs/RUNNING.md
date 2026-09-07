# v3 运行说明

三个系统使用同一份任务和共同配置，成功和原生失败均返回同一 `result.json` 结构。合同见 [V3_CONTRACT.md](V3_CONTRACT.md)。

## 环境

所有环境、凭证、数据库和产物放在 `systems/` 外。公共 runner 使用 Python 3.10+ 标准库；Schema 测试另需 `jsonschema`。各原生环境分别准备：IF Line 使用 Python 3.12、外置 requirements-text.txt、PostgreSQL 16 与 Redis；AI4VisualNovel 使用 Python 3.10 与其原始 requirements；InfiPlot 使用 Node.js 22、pnpm 9.12.0 及原始 lockfile 的离线缓存。

具体环境、缓存、数据库准备命令保存在 [环境安装说明](RUNNING_V2_HISTORY.md)。该文件中的旧 C1 入口与验收标准已被 v3 取代。IF Line 需独立空数据库，名称以 `if_line_bench_` 开头；管理器自行启动 API、worker、beat 与独立 Redis。InfiPlot 需要有效 Supabase 配置和登录 Cookie，适配器自行启动隔离 Next 副本。此次验收的本地身份 fixture 不代表生产账号验收。

## 编译与共同配置

从仓库根目录执行：

```bash
export PYTHONDONTWRITEBYTECODE=1
cd benchmark
python3 -m story_benchmark compile --case cases/CAMPUS-01-V3.json --out ../work/CAMPUS-01-V3 --allow-pilot
python3 -m story_benchmark verify --bundle ../work/CAMPUS-01-V3
cp -n configs/experiment.v3.example.json configs/experiment.v3.local.json
```

也可直接验证并使用已编译的 `examples/CAMPUS-01-V3`。只编译一次，三个适配器使用同一个 bundle；编译目录必须全新。`pilot` 表示开发接入材料，不能冒充正式题库审批或其余 29 道题覆盖。

编辑唯一 `common` 块的模型、endpoint 和 model_parameters；准备真实生成时设置 live=true。模板默认采用无适配器预算上限，配置必须写明：

```json
{
  "budget_mode": "unlimited",
  "timeout_seconds": null,
  "max_calls": null,
  "max_output_tokens": null,
  "max_input_chars": null
}
```

四个限额字段必须显式为 null，不能遗漏或混入数字。三个专有块不能覆盖模式或限额。保留 `bounded` 模式供复现历史实验；省略 budget_mode 仍按旧 bounded 规则检查，四个正数必填。模板不含默认模型或凭证，路径相对配置文件解析。

无限模式取消适配器的根生成计时器、原生生成任务等待期限、HTTP 调用上限、输入长度拒绝和输出 token 注入或压低。调用次数与可得用量继续记录。原生循环停止规则、原生已指定的 token 参数、SDK/服务商请求限制继续保留；基础设施启动、依赖安装和关闭服务的独立超时不作为总生成期限。

IF Line 必须设置 `entry_mode="shared_first_choice"`，原生规划章数仍为 13；v3 会拒绝旧入口。解释器分别填各自独立环境。角色参数使用公共名单的**总人数，包含玩家**。

凭证仅通过环境提供：`OPENAI_API_KEY`、`TEXT_API_KEY`、`IFLINE_BENCH_DATABASE_URL`、`NEXT_PUBLIC_SUPABASE_URL`、`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`、`INFIPLOT_COOKIE`。不要放进 JSON、故事题目或仓库。

```bash
python3 -m story_benchmark preflight-set --experiment configs/experiment.v3.local.json --bundle ../work/CAMPUS-01-V3
```

预检不发送生成请求。实际请求保留原生规划、审核、重试、schema、采样参数和原生输出上限；共同预算政策不表示实际消耗或金额相等。

## 运行

基础设施准备好后，三系统串行执行并比较实际收件：

```bash
python3 -m story_benchmark run-set --experiment configs/experiment.v3.local.json --bundle ../work/CAMPUS-01-V3 --out ../work/runs/v3-001
```

也可分别调用，始终使用同一配置和 bundle，每次给全新目录：

```bash
python3 -m story_benchmark run-first --system if_line --experiment configs/experiment.v3.local.json --bundle ../work/CAMPUS-01-V3 --run-dir ../work/runs/v3-if-001
python3 -m story_benchmark run-first --system ai4visualnovel --experiment configs/experiment.v3.local.json --bundle ../work/CAMPUS-01-V3 --run-dir ../work/runs/v3-ai4-001
python3 -m story_benchmark run-first --system infiplot --experiment configs/experiment.v3.local.json --bundle ../work/CAMPUS-01-V3 --run-dir ../work/runs/v3-infi-001
```

Python 入口是 `story_benchmark.runner.execute_run(system, bundle, config, run_dir)`。输入预检拒绝会直接返回同形状封套，不创建伪运行证据；开始后的 `result.json` 在清理服务后封存。已有目录绝不覆盖。

`content.body` 是新增当前正文，`content.choices` 是原生标题/选项，`content.previews` 仅为原生玩家可见的未执行预览。内部未来脚本、摘要和 nextSceneSeed 留在原生产物中。正文允许为空。每段文字都有来源，故事不被适配器改写。

`outcome=completed` 只表示技术范围与来源检查完成，不保证剧情正确。`adapter_status=passed` 可与 `outcome=native_error` 同时出现：接入证据通过但原生失败。`delivery_unknown` 不算未发送或零费用。原生失败时命令退出 1，但仍输出统一结果。三系统汇总的 `adaptation_passed` 与 `ok` 同样分开。

## 核验与恢复

```bash
python3 -m story_benchmark audit-run --run-dir ../work/runs/v3-if-001
python3 -m story_benchmark resume-export --run-dir ../work/runs/v3-if-001
cd ..
python3 tools/verify_sources.py
```

v3 resume-export 校验文件、配置和可执行代码清单后返回原有 result，不补发请求或修改失败产物。版本变化需要新运行。服务未停止或来源校验失败会使适配验收失败。

Schema 位于 `benchmark/schemas/result-v3.schema.json`。运行 test_v3.py / test_v3_schema.py 验证公共封套；AI4 测试必须设置 AI4VN_TEST_PYTHON 指向完整原生依赖环境。真实原生链 fixture 是独立 opt-in 测试，普通单测不替代真实模型验证。
