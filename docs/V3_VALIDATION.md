# 三系统适配器 v3 最终验证（2026-09-07）

已完成本轮约定范围的公共提示词、外置接入、统一返回和验收修复。三个系统实际收到同一份任务和固定开头；成功与失败使用同一份 Schema，正文、选项和原生可见未来预览分开。原生源码和故事内容未修补。

本报告验收的是“固定开头之后，到第一次尚未选择的原生选项”。允许开头后直接显示选项，故事文字可以不同。结构验收不保证原生剧情符合所有事实或选择语义，不等于完整多分支故事质量实验。

## 最终冻结条件

真实生成执行版本：`b9d8468e655ed87068e5928c035116b6380ee928`。随后仅修正了审计对已记录 SDK 预算错误码的读取，三份结果在独立派生目录重审，零新增模型请求；原始封存结果同时保留。重审执行代码为 `87a74e9`；重审代码清单、原运行清单与父 manifest 哈希均在各 reaudit-origin.json 中。后续文档与证据提交不改变可执行清单。新案例为 CAMPUS-01-V3，仍是开发 pilot；不是全部 30 题正式验收。

- 公共文件：2,565 Unicode 字符，SHA-256 `c8741573a37879a743113da4013765d432d0e3dc9cc22f60697d1883030b9335`。
- 固定开头原文未改：SHA-256 `196d04a58ba5bce543e93788e496ae42fdd547d1583d185cda42c3b969159c65`。
- 唯一共同配置：DeepSeek 官方 endpoint、deepseek-v4-flash、thinking.disabled；每系统 1,800 秒、160 次实际 HTTP；每请求 16,384 输出 token 上限、200,000 紧凑 JSON 字符上限。保留原生更低上限、schema 和采样。
- 实际接收端文本逐字比较通过，首个真实创作请求均完整包含共同区块一次。观察边界分别是 IF 原生任务输入、AI4 requirements 读取、Infi SDK 任务区块。
- 所有 result 通过同一 Schema、输入一致、已导出文字来源、封存和恢复检查。IF/Infi 的完整接入证据通过；AI4 有 3 次历史 HTTP 尝试投递未知，完整链路状态仍为 unverified，不能报告三者所有接入检查全过。恢复与派生重审未重发请求。
- 三套源码共 2,162 文件（1,200 / 35 / 927）与冻结基线一致。

## 最终轮结果

字数按实际导出字符串长度统计，包含空白；固定开头不计入新增正文。预览不是已执行路径，独立计数。

| 系统 | 接入验收 | 运行结果 | 新增当前正文字符 | 原生选项 | 未执行预览 | HTTP 尝试 | 供应商 total tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| if_line | passed | completed | 0 | 2 | 2 | 6 | 20890 |
| ai4visualnovel | unverified | delivery_unknown | 0 | 0 | 0 | 160 | 不可得 |
| infiplot | passed | completed | 1009 | 2 | 0 | 3 | 13213 |

实际停止原因：

- if_line：无技术错误。故事语义未自动评分。
- ai4visualnovel：budget_exhausted — Native trace or export reported budget exhaustion.; delivery_unknown — ['2a7e700f4f844a42ace3de44f78e0352', '12d33a757c0a4185851e56f5eda39ec3', '8bbcfe451c7b45e68499fde9f1ba757e']
- infiplot：无技术错误。故事语义未自动评分。

`adapter_status=passed` 仅说明接入和来源证据通过。`native_error` 保留项目原生失败；`budget_exhausted` 表示达到共同预算，不能据此直接断定项目有 bug。失败字段可为空，但不补写虚构故事。金额不可得为 null，实际消耗不宣称相等。

## 修复内容

1. 公共文字统一角色总名单含玩家，消除“核心三人”与原生“总共三人”的歧义；明确可直接出现选项，区分内部规划与未执行预览。
2. IF Line 新入口不再把 C1 剧情片段重复传入候选 instructions。原生 option_key 用作真实卡片标题；原生 preview_text 分列保存。Bible、outline、公共开头和空状态依赖全部保留并记录，语义一致性标 not_evaluated。
3. AI4VisualNovel 保留原生设计/脚本流程和节点校验；精确区分节点数、空响应、坏 JSON、原生退出、预算和未知投递。支持零前置正文的原生菜单，不伪造选项或补节点。
4. InfiPlot 保留原生选择与 effect；nextSceneSeed 不作可见预览。HTTP 原生失败、部分响应、选项观察与超时清理补齐，封存前停止后台写入。
5. 统一成功/失败返回、源码/文字来源校验、预算优先级、真实 HTTP 计数和用量。未发送的预算拦截不再被算为一次调用；已知原生错误不再误归适配错误。
6. 同一 experiment 配置可通过 run-set 串行运行并比较实际收件；已有目录不覆盖，resume-export 只返回已封存结果。

## 回归证据

- 全套 unittest：182 项运行，172 通过、10 跳过。跳过包含 7 项当前解释器缺少可选 jsonschema 和 3 项 opt-in 集成；不是 182 全过。
- 使用已装 jsonschema 的独立环境：全部 v3 38 项通过，其中 7 项 Schema 测试实际执行；结构化 SDK 预算回归 2 项另行执行通过。
- IF trace：15 项通过。真 PG16/Redis/Celery 的 v3、旧候选、旧首章三个本地固定响应链分别通过。
- AI4 原生 CLI 完整 suite：33 项通过，含 v3 成功、空响应、坏 JSON、封存恢复；原生源码 35 文件不变。
- Infi 原始 Next 鉴权 route：无 Cookie 拒绝且零模型、有本地身份 Cookie 进入真实原生链；source/runtime 927 文件不变，固定响应与真实模型证据分开。

第一次全套回归曾使用 IF Python 执行 AI4 子进程，因缺 rembg/jsonschema/yaml 失败。随后显式指定 AI4VN_TEST_PYTHON 的完整环境，最终上述回归通过；未为此修改原生依赖或业务代码。

最终 AI4 真实轮已进入原生脚本流程，160 个 HTTP 尝试后预算拦截生效；原生随后导出的可见范围为空。3 个早期 HTTP 尝试没有对应响应记录，后续重试成功不能补齐前次用量。派生结果同时保留 budget_exhausted 与 delivery_unknown，不估算未知总用量。

先行 9ad8eba 真实轮也完整保留。最后发现的预算计数/分类边界修复后，以 b9d8468 新建三个运行目录重测；共同输入、开头、模型和预算不变，不覆盖旧结果或挑选故事。

## 文件和适用边界

统一结果、三个 audit、实际收件与源码清单在同目录 JSON 文件中；result 内的原生来源相对其对应完整 run 根目录解析。完整封存档包含本轮六个真实运行目录、三个明确标注的派生重审目录和启动脚本，不能把单独复制的 result 文件当成全部原始证据。

压缩包：`six-live-runs-and-reaudits.zip`，71507804 字节；SHA-256 `54b3531b87600a7bb4efe31793974f1a0ad8055f9203714c719611e3ba91cf9b`。已逐个核对六份 manifest 中的全部文件哈希，解包读取也验证通过。未包含实际模型凭证字节。

Infi 身份验证使用本地隔离身份 fixture，文字模型是真实服务；生产账号部署没有在本轮验收。原生故事矛盾、选项语义错误、完整分支回放、正式题库审批和质量统计留给后续项目或评测工作。当前共同输入与统一返回的代码修复和工程验收已完成；真实端到端结果存在上述预算及网络投递未知，不能宣称三个项目均已正常产出故事。

[统一结果与审计证据](evidence/v3-final-20260907/validation-summary.json) · [三个统一返回](evidence/v3-final-20260907/results.json) · [公共任务](../benchmark/examples/CAMPUS-01-V3/shared_task.txt)
