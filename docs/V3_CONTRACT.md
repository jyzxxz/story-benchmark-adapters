# 历史合同：v3 公共输入与统一返回

> **适用范围：v3 / CAMPUS-01-V3 / 首个未执行选择。**本页用于解读 `result-v3.schema.json` 和旧文本运行，不定义当前图文批次的停止与返回范围。当前 v4 会执行实际选择并记录共同阅读窗口、图文与八项证据，参见[操作者交接说明](OPERATOR_HANDOFF.md)、[批量运行](BATCH_RUNNING.md)和[记录说明](BATCH_RECORDING.md)。本页“原生源码保持冻结”指该轮，现行 IF Line 使用已披露的 [HTML 修复变体](IFLINE_HTML_PATCH.md)。

本次修复的是适配器的输入递送、停止范围和返回结构。三个项目接收同一份完整公共任务，继续使用各自原生的规划、记忆、审核与生成流程。故事可以不同；输入题目、输出范围、预算政策和结果字段相同。原生源码保持冻结，外置接入规则单独记录。

## 同一份文件如何进入三个项目

公共编译器只生成一次 `shared_task.txt`，内容包含故事 brief、固定开头和共同要求。三个适配器从同一个编译包读取它，不分别摘要、补写题目或创建另外一份剧情指令。

本轮包为 `benchmark/examples/CAMPUS-01-V3`：

- 公共任务为 2,565 个 Unicode 字符。
- SHA-256 为 `c8741573a37879a743113da4013765d432d0e3dc9cc22f60697d1883030b9335`。
- 原始 brief 和 opening 保持原文；共同说明澄清了角色范围、已发生开头、原生内部预生成与玩家可见输出的区别。
- `input_contract.adapter_story_reinjection` 为 `none`：适配器不能在后续创作步骤选择性地再次追加 C1、人物或开头提醒。

检查对象包括实际接收端解码后的任务，以及首个实际创作请求中公共任务完整出现一次。主文件哈希相同只是其中一项证据。

三个项目的完整模型请求不会相同。原生系统提示词、JSON schema、大纲处理、上下文摘要、候选生成与审核步骤仍然不同；这些是被比较的方法。外置适配器不代替它们补剧情、修复大纲或构造记忆。

## 统一输出范围：停在第一次尚未执行的选择

v3 的 `output_contract` 明确约定：

```json
{
  "version": "3.0",
  "scope": "first_unselected_choice",
  "decision_id": "C1",
  "choice_count": 2,
  "selection_executed": false,
  "allow_empty_body": true,
  "native_choice_previews": "separate"
}
```

固定开头已经写到选择前，因此项目可以直接返回原生选项。新增的选择前正文允许为空；不会为了满足篇幅或表面格式额外要求模型写一段衔接文字。提供的开头保存在 `input.provided_prefix`，不会被计作新生成正文。

选项显示文本使用原生产物。IF Line 原生界面的候选卡片使用 `option_key` 作为标题，因此 v3 可以直接把该字段作为统一返回的 `label`。适配器不能从公共 C1 抄出标题来代替原生生成结果，也不把简短原生标题自动改写成一段行动说明。

只有原生界面附带在选项卡片上的未来预览进入 `content.previews`，通过 `choice_id` 关联选项。例如 IF Line 的 preview_text。AI4VisualNovel 内部未来节点脚本、InfiPlot 的 nextSceneSeed 留在原始产物中，不进入此栏。预览不进入当前路径的 `content.body`，也不表示玩家已经行动。执行状态必须有记录支持；缺少证据时保持未知或失败，不能默认填成“未选择”。

## 三个系统共用的 result.json

统一返回使用 `schema_version: "3.0"`。成功、原生失败、接入失败和预算耗尽使用相同的顶层结构；尚无内容的数组为空，无法确认的数据为 `null`。字段不同不再由调用方按项目猜测。

| 字段 | 含义 |
| --- | --- |
| `system`、`run_id`、`case_id` | 项目、运行与公共题目的身份 |
| `outcome` | 本次整体结果类别 |
| `adapter_status` | 接收、来源映射及请求记录的工程验证状态 |
| `native_status` | 原生运行完成、失败、未知或尚未开始 |
| `input` | 公共任务和开头原文、哈希、输入契约、实际接收相等与首请求出现次数 |
| `scope` | 输出契约、是否到达边界、选项数、选择执行状态及问题码 |
| `content` | 当前正文 `body`、选项 `choices`、独立未来预览 `previews`、质量评估状态 |
| `errors` | 带 `category`、`code`、`message` 的原始问题分类 |
| `usage` | 实际 HTTP 次数、用量记录完整性、真实 token 用量和可确认的费用 |
| `artifacts` | manifest、audit、正文、选项、预览、metadata 与开头文件的相对路径 |
| `provenance` | 证据类型、适配代码清单哈希、源码完整性、模型参数核验和语义未评估标记 |

正文项包含 `id/kind/speaker/text/source`；选项项包含 `id/label/source/preview_ids`；预览项包含 `id/choice_id/kind/speaker/text/source`。每段文字或标题的 `source` 指向真实原生产物中的文件与精确位置，JSON 使用 RFC 6901 指针，文本使用行号。

例如，IF Line 的两个标题可以分别来自 `native/candidate_previews.json` 的 `/0/option_key` 和 `/1/option_key`；对应预览分别来自 `/0/preview_text` 和 `/1/preview_text`。这只是来源位置示例，不是手工指定的故事输出。

| `outcome` | 解释 |
| --- | --- |
| `completed` | 请求证据和来源映射有效，技术上到达共同输出边界 |
| `native_error` | 原生流程、原生响应或原生结构校验失败 |
| `native_output_incomplete` | 原生有产物，但没有完整满足本次输出范围 |
| `input_rejected` | 公共输入、配置或前置检查不满足要求 |
| `adapter_error` | 接入、导出、来源证据或清理出现问题 |
| `budget_exhausted` | 本次统一预算耗尽 |
| `delivery_unknown` | 请求投递或完成状态无法确认，不能假定没有执行或没有费用 |

`completed` 不是文学质量分数，也不证明每个选项在语义上正确实现 C1。`content.quality_status` 和上下文语义状态仍为 `not_evaluated`。原生输出错误保持原样供后续评审，不会由适配器改写成通过的结果。供应商未提供的 token 或费用不估算为零；明确没有发生的调用数量可以为零。

## IF Line 的 v3 接入与上下文边界

v3 必须配置 `entry_mode: "shared_first_choice"`。旧 `first_chapter` 和 `provided_prefix_candidates` 仅保留用于旧契约复现；v3 case 使用旧入口时 preflight 拒绝。

IF Line 的实际顺序是：

1. 原生生成并激活 Bible。
2. 原生生成并激活大纲；默认规划总长保持冻结 UI 的 13 章。
3. 调用原生 manual revision 服务存入精确开头，并激活该 revision。该开头的 `generation_task_id` 为 `null`。
4. 外置启动器进行公开记录的结构 checkpoint 初始化，绑定已有正文 revision、结构 ID 和 hash；原生快照服务提供空 `{}` state。
5. 原生生成并读取 2 个候选，停止，不 promote、不选择、不应用 state delta。

第 4 步是外置初始化能力，不能说成冻结仓库已经提供的 checkpoint 创建 API。它不抽取人物知识、库存或剧情事实，也不重复存一份开头到 checkpoint payload。分支请求通过原生 `chapter_tail` 接收开头一次。

候选接口不再收到适配器从 C1 提取的 `instructions`。HTTP API 省略该字段后，原生保存的 `request_identity.instructions`、`provider_input.instructions` 以及实际供应商请求中该值均为空字符串。适配器只把共同契约的 `choice_count` 映射到原生 `candidate_count`。

原生候选服务要求 Bible、大纲、章节 revision 及其 hash 对应；manual revision 创建也依赖该上下文。现有原生大纲生成来源不读取已经导入的章节正文，单纯把大纲生成放到导入开头之后不能保证规划与开头一致。因此 v3 保留完整原生规划，不跳过约束，不用额外 AI 重写大纲。

`native/native_context.json` 保留各阶段生成顺序、task/revision ID、完整大纲、实际候选请求、开头和空 state 的来源。它明确标记 `semantic_consistency: "not_evaluated"` 与 `adapter_semantic_rewriting: false`。哈希能核对使用了哪份材料，不能证明这些材料在故事内容上相互一致。

## 当前预算模式

同一共同配置默认使用 `budget_mode: "unlimited"`，`timeout_seconds/max_calls/max_output_tokens/max_input_chars` 必须全部显式为 null。适配器不设置生成总时长、HTTP 次数、输入长度和输出 token 上限；原生停止规则、原生 token 参数和模型服务限制仍保留。用量继续记录，不要求三者消耗相等。基础设施启动、安装与清理采用独立超时，不限制生成过程。

`bounded` 模式继续用于复现旧结果；未指定模式时仍按旧规则要求正数限额，不能将漏填配置当作无限。旧的 160 次调用实测封存保留，不因更换模式重新标记为通过。

## 本地验证范围与证据

以下是实现阶段完成的 IF Line 本地检查；统一 result、封存回归和后续真实模型重测单独记在 [v3 验证报告](V3_VALIDATION.md)，与固定响应证据分开。

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| IF adapter unittest | 17 通过 | 新旧契约、输入匹配、来源、恢复与新标题/预览映射 |
| IF trace pytest | 15 通过 | 真实请求观察、预算、模型参数、流和传输记录 |
| v3 原生服务本地链 | 1 通过，10.492 秒，3 次 localhost HTTP | 实际 PG16、Redis、Celery solo worker、beat、原生 sid/API 与固定响应供应商 |
| 旧候选入口回归 | 1 通过，10.857 秒，3 次 localhost HTTP | 保留旧入口行为，独立数据库与证据目录 |
| 旧首章入口回归 | 1 通过，10.530 秒，5 次 localhost HTTP | 保留原生首章/Script IR 链及 13 章规划 |

三个原生服务检查均为 **0 次付费调用**。其源码指纹运行前后相同：`3a3197fcb37218e06260756e43a1a3a33fadc16422e7dc8d545db99b9d0f7025`。固定响应只证明接入可运行，不能用于故事质量评分。

本次工作树中的独立证据目录为：

- `work/if-line-evidence/native-services-v3-v2`
- `work/if-line-evidence/native-services-legacy-candidates-v1`
- `work/if-line-evidence/native-services-legacy-chapter-v1`

v3 实际请求检查还确认：首次公共文本一次、候选上下文开头一次、无后续公共片段强化、空 state、未执行选择、原生标题和预览精确来源、已完成操作重入不增加模型请求。手动开头写入后模拟丢失 HTTP 响应，适配器通过唯一精确 revision 恢复；错误开头 hash、不存在的 checkpoint 和过期 ETag 均被拒绝。

首次 `native-services-v3-v1` 现场保留：原生生成成功，新增测试误读了 Task API 未暴露的 `parameters` 字段。测试改为读取实际 `source_refs/candidate_set_source/request_identity` 后，以全新目录复测通过。证据目录从不合并或覆盖。

具体启动和测试要求见 [IF Line 外置接入说明](../benchmark/native_shims/if_line/README.md)。所有真实模型运行使用统一模型、端点、参数和预算，冻结新证据。结构验收不代替故事内容评审。
