# 项目组成与维护边界

**接手生成故事和跑实验，先读 [操作者交接手册](OPERATOR_HANDOFF.md)。** 本页解释代码、输入、证据与维护边界；安装及日常操作统一使用仓库根目录的 `bash experiment.sh`。

本项目把同一份故事任务和固定开头送入 IF Line、AI4VisualNovel、InfiPlot 的原生创作流程，保存实际路径、图文、调用与错误，并生成可直接打开的离线评审包。当前内置输入为 `v4-30-open-actions-pilot.2`：30 题均有设定和固定开头，尚待真实操作者审核。它不预设后续关键行动，不要求三者产生相同剧情。

## 仓库组成

```text
systems/                  冻结原生源码及原许可证
native-patches/            获准的 IF Line HTML 转换修复
benchmark/source/          原始题库与 recording_spec.v0.1.md
benchmark/suites/eval30_open/ 当前 30 题、前缀、开头、版本及哈希
benchmark/cases/           单题开发案例与历史协议材料
benchmark/examples/        已编译示例；不是统一入口默认 30 题
benchmark/story_benchmark/ 编译、驱动、调度、采集、校验与离线回放
benchmark/native_shims/    外置启动、观察与环境适配
tools/                   安装、配置、检查、实验入口与导出工具
docs/                    现行说明、技术参考及有日期的历史验证
work/                    本机环境、缓存、配置、密钥和结果；Git 忽略
```

`baseline-lock.json` 保存三个上游提交、逐文件哈希和省略项。IF Line 应用 `native-patches/if_line/manifest.json` 声明的补丁；另外两个项目保持冻结源码。运行前后检查源码身份，不在 `systems/` 中安装依赖、保存密钥或生成结果，也不对上游快照单独 `git pull`。出处见 [来源说明](PROVENANCE.md)。本仓库包含所需快照，不镜像三个上游的全部 Git 历史。

## 实验与评审边界

1. 统一入口编译、复制并冻结共同输入；首个真实创作请求另有接收审计，不只比较文件哈希。
2. 文字、图片、原生视觉审核的供应商和模型在公共配置中统一，图片模型使用 `gpt-image-2`。系统提示词、规划、审核、记忆与原生格式规则保留；完整内部消息不要求相同。
3. 按相同题目顺序和次数执行。`--count` 是每系统总尝试数，`--repeat` 是每题每系统次数；三个系统按所选顺序执行，`--concurrency` 控制当前系统的根任务数。
4. 每次保留一条实际访问路径。当前题库观察约 4000 个新增可见字符，按句界结束；公共开头不计入。没有全篇结局本身不扣分，原生提前结束、失败、正文不足和缺图都保留。
5. 保存八项评测证据、关联与文件哈希。M1/M2/M3/M5/M6 待外部内容评审；M4/M7/M8 从实测时间、真实 usage 与图片记录汇总，未知值不填零。
6. 生成后导出离线图文回放及总目录。它只展示已记录路径，不生成未选择路线，不改变原始指标。组织者保存整个实验目录，评审者接收 `REVIEW_DELIVERY.txt` 指定的 ZIP。

无适配器总 token、调用数、时间或费用上限；4000 字符不是费用上限。供应商额度、速率、上下文和原生内部循环仍可能限制运行。等待时间受调度、主机负载、冷启动与供应商影响，应随结果披露。

## 配置、冻结与迁移

`work/config/batch.local.json` 保存本机路径和公共供应商配置；`work/secrets.env` 只在本地保存三个 `BENCH_*_API_KEY` 值。`experiment.sh` 经安全读取器加载密钥，不把该文件作为 shell 脚本执行。终端已导出的同名变量优先，换 key 时须检查旧变量。

新主机按交接手册重新安装并生成本机配置，不复制虚拟环境或旧绝对路径。`configure_batch.py` 默认拒绝覆盖；`--force` 会重建配置并恢复模板供应商字段，因此先备份需保留的设置。日常更换供应商/密钥使用 `bash experiment.sh configure`。

`prepare` 只冻结输入和实验计划；`preflight` 在新目录创建计划并检查所选环境；`resume` 从已有计划启动并再次预检，只派发从未开始的队列项。两者不能互相覆盖同一输出目录。输入、公共配置、代码和原目录位置在计划中冻结；升级或改变条件后创建新实验。文档更新是否影响旧计划，以实际代码清单校验为准。完整语义见 [实验协议与命令参考](OPEN_ACTION_EXPERIMENTS.md)。

## 维护检查

```bash
source work/activate.sh
python3 tools/verify_sources.py
python3 tools/doctor.py --system all
python3 tools/smoke_test.py --system all --out work/smoke-release
bash experiment.sh test
```

`doctor` 和 `smoke` 不调用付费生成模型；后者使用本地固定响应检验原生链路，不证明真实模型可用或故事质量。已有验证报告分别注明提交、平台、夹具和跳过项，不能当作此后每个版本、每台机器都已验收。维护生成代码时应另跑对应记录/原生回归，确认输入、来源与八项证据未失联；不要修改封存历史运行迁就新 schema。

安装器用 `tools/linux/*py312.constraints.txt`、`renderer-package-lock.json` 与原生 pnpm 锁固定各环境依赖。实际版本保存在 `work/installation/`，随实验归档；硬件、内核和系统包更新仍需记录。发布提交代码、文档、来源锁、示例与测试，排除密钥、数据库、依赖缓存和私人调用档案。

仓库 [jyzxxz/story-benchmark-adapters](https://github.com/jyzxxz/story-benchmark-adapters) 为公开仓库，接收者可直接克隆。交接时提供仓库链接、选定提交号和 [操作者交接手册](OPERATOR_HANDOFF.md)，让对方配置自己的供应商与密钥。历史验证和废弃协议由 [仓库导航](../README.md) 区分，不作为当前默认输入。
