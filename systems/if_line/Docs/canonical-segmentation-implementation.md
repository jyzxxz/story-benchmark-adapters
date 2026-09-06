你现在开始正式修复 if_line 中“正文 → 旁白/对白 → speaker → VoiceLine → VNGraph → TTS”这一整条链路。

前一阶段已经完成排查，不要再重复做 Phase 1，也不要只给我方案。
现在直接进入：

Phase 2：补 regression tests
Phase 3：完成最小兼容设计
Phase 4：实际修改代码
Phase 5：完整回归测试

仓库：
zhending216-hub/if_line

当前重点分支：
integration/api-unification

==================================================
0. 开始前要求
==================================================

先执行并记录：

git status --short
git branch --show-current
git log -5 --oneline

如果工作区已有用户未提交修改：
- 不得覆盖；
- 不得 reset；
- 不得 checkout --；
- 不得删除；
- 修改时避开无关文件。

不要创建新的大型重构分支体系。
不要动与本问题无关的 COS、素材生成、登录、权限等功能。

本次核心目标只有一个：

建立唯一的 canonical textual segmentation，
让：

ChapterRevision
→ Script IR
→ VoiceLine
→ VNGraph
→ TTS

全部共享同一份“旁白/对白/说话人原子切分结果”。

禁止继续维持：
Script IR 切一次
VoiceLine 再切一次
VNGraph 再靠 text 猜一次

这种架构。


==================================================
1. 已确认的问题，不需要重新证明
==================================================

前一阶段已经确认以下问题：

P0-1
voice_line_service.deterministic_voice_specs()
试图：

from app.services.chapter_voice_service import chapter_voice_service

但模块没有导出这个名字。

ImportError 又被：

except Exception

静默吞掉。

所以生产 voice.slice 实际一直走：

_text_only_specs()

而不是预期的 LLM 切分。

不要只修这个 import。
因为旧的 ChapterVoice LLM 方案本身也不是最终正确架构。


P0-2
Script IR 当前 paragraph 是最小语义单元：

一个 paragraph
只能有：
- 一个 kind
- 一个 speaker_character_id
- 一个 speaker_name

因此：

林夜推开门，朝里面看了一眼：“有人吗？”苏婉从桌后站起来：“你终于来了。”

当前 schema 无法表达：

旁白
→ 林夜对白
→ 旁白
→ 苏婉对白

这是结构问题，不是 prompt 问题。


P0-3
VNGraph 当前音频关联存在：

voices_by_text[paragraph.text]

即：
Script IR 整 paragraph 文本
必须等于
VoiceLine.text

才能挂上音频。

但 VoiceLine 是句级/对白级，
paragraph 是段级。

所以混合段天然匹配不上。


P0-4
实际生产 _infer_speaker() 太弱：

只支持：
角色正式名
+
引号前短窗口
+
少量说/道/问等动词

因此以下大量失败：

“你来了。”林夜说道。

林夜推开门，看了她一眼：“有人吗？”

苏姑娘道：“多谢。”

他说：“不行。”

林夜：“好。”


P0-5
目前 character_id 不统一：

Script IR / VNGraph
VoiceLine / TTS
CanonicalCharacterResolver

存在不同 ID 生成体系。

必须收口。


P1：
还存在：
- duplicate text 全局 seen_texts 去重；
- unknown speaker dialogue 强制降级 narration；
- >8000 字头尾截断；
- LLM failure 全 narration；
- annotation gap 静默 narration；
- legacy fallback 全 narration；
- 240 字引号 regex 上限；
等问题。

这些要一并处理。


==================================================
2. 本次架构原则：ONE canonical segmentation
==================================================

这次禁止新增第五套 splitter。

允许：
- 重构现有 deterministic_quote_splitter.py；
- 或将其演化/迁移为 canonical segmentation service；
- 保留旧模块做 compatibility wrapper。

但最终必须只有一个“正文原子切分事实源”。

概念流程：

ChapterRevision.content
        ↓
Canonical Text Segmentation
        ↓
Atomic textual spans
        ↓
kind classification
        ↓
speaker resolution
        ↓
Script IR
        ↓
VoiceLine
        ↓
VNGraph
        ↓
TTS


不要：

ChapterRevision
 ├── Script LLM 自己切
 ├── Voice LLM 自己切
 └── regex 再切

三套并存。


==================================================
3. 原子文本结构
==================================================

优先采用 additive 方案，不破坏现有 paragraph。

推荐：

paragraphs[].utterances[]

不要删除现有 paragraph：
- scene_id
- keyframe
- stage_events
- paragraph_id
- source_span
- scene metadata
等字段。

paragraph 继续承担：

场景级 / 视觉级 metadata

utterance 承担：

实际显示 / speaker / TTS / 对话粒度。


建议 utterance 至少包含：

{
    "utterance_id": "...",
    "paragraph_id": "...",
    "order_index": 0,

    "source_start": 123,
    "source_end": 136,

    "source_text": "原文对应内容",
    "spoken_text": "实际用于显示/TTS的内容",

    "kind": "narration|dialogue|monologue|action",

    "speaker_character_id": null,
    "speaker_name": null,
    "speaker_display_name": null,

    "speaker_resolution": "resolved|unresolved|narrator",
    "confidence": 1.0,

    "attribution_source":
        "explicit_prefix|
         explicit_suffix|
         name_colon|
         alias|
         context|
         llm|
         unresolved"
}

具体字段名可以根据当前项目风格调整。

但是必须满足：

1.
每个 utterance 可追溯 ChapterRevision 原文。

2.
source_start/source_end 必须是真实原文 offset。

3.
顺序必须稳定。

4.
重复文本不能合并。

5.
不能把 text 当天然唯一 ID。

6.
source span 是身份的重要组成部分。


==================================================
4. canonical deterministic segmentation
==================================================

重构：

backend/app/services/deterministic_quote_splitter.py

或者把其核心迁移到：

canonical_text_segmentation.py

但是：
不要留下两套各自真正执行的算法。

旧 splitter 可以变 wrapper。


deterministic parser 主要负责：

A. 确定可靠文本边界
B. 明显旁白
C. 明显引号对白
D. 明显 speaker attribution
E. 保证 source coverage

它不要负责猜复杂语义。


至少支持：

前置 speaker：

林夜说道：“走。”

林夜道：“走。”

林夜问：“去哪？”

林夜轻声说：“走吧。”

林夜压低声音道：“别动。”


后置 speaker：

“走。”林夜说道。

“不要。”苏婉摇头说道。


姓名冒号：

林夜：“走。”

苏婉：不要。


动作 + 对白：

林夜推开门，看了苏婉一眼：“走。”

这里：
前半段 = narration/action
引号内 = 林夜 dialogue


同段多人：

林夜推开门：“走。”苏婉摇头：“不去。”

必须切成：

narration/action
dialogue 林夜
narration/action
dialogue 苏婉


插入 attribution：

“我……”苏婉低下头，“不知道。”

原则上两个 quotation fragment 都应该能够关联苏婉，
中间“苏婉低下头”保留为 narration/action。


连续对白：

“走吧。”
“去哪？”
“城外。”

如果缺显式 speaker：
仍然必须识别为 dialogue。

speaker 可以 unresolved。

绝对禁止因为 speaker 不确定就：

kind=narration


==================================================
5. 区分 dialogue 和 speaker attribution
==================================================

这次必须改正一个核心语义错误：

“这是对白”
和
“知道是谁说的”

是两个独立问题。

例如：

“快跑！”

即使不知道是谁说：

kind = dialogue
speaker_character_id = null
speaker_resolution = unresolved

绝对不能：

kind = narration


原则：

quote / syntax
决定：
是不是 dialogue

speaker resolver
决定：
是谁说

两个阶段不能混。


==================================================
6. CanonicalCharacterResolver 接管角色解析
==================================================

把角色解析统一收口到：

CanonicalCharacterResolver

仔细检查：

backend/app/services/canonical_character_resolver.py

正式角色名、alias、历史兼容 ID 都统一从这里解析。

不要：

Script IR 自己生成一种 ID
VoiceLine 再 md5 一种 ID
VNGraph 又使用第三种 ID。


必须实现：

同一个角色：

苏婉
婉儿
苏姑娘

最终：

speaker_character_id

必须是同一个 canonical character_id。


测试：

canonical name:
苏婉

aliases:
婉儿
苏姑娘

文本：

苏姑娘道：“谢谢。”
婉儿说：“我知道。”

两条都解析为苏婉同一 character_id。


如果 alias 数据目前 StoryBible schema 已经存在：
直接复用。

如果 resolver 支持 alias：
不要重新实现另一套 alias matcher。


==================================================
7. speaker attribution 优先级
==================================================

建议固定优先级：

1. 明确姓名+说话动词
2. 明确姓名+冒号
3. 后置 attribution
4. canonical alias
5. 当前段显式角色
6. 上下文连续 speaker
7. 对话 turn-taking（仅作为低置信度辅助）
8. LLM resolve
9. unresolved


不要让 LLM 覆盖高置信 deterministic 结果。


必须保存：

attribution_source
confidence

方便调试。


==================================================
8. LLM 的职责必须缩小
==================================================

不要再把整章正文交给 LLM 让它：

“重新切一遍并抄回 text”。

以后 LLM 只处理 deterministic parser 无法可靠解决的 span，例如：

- 引号到底是对白还是强调语；
- speaker attribution 不明确；
- monologue / dialogue 歧义；
- “他说/她说”需要上下文解析。


LLM 输入应该是：

目标 span
+
前后若干 canonical spans
+
当前 scene cast
+
canonical characters
+
aliases

输出必须引用：

utterance_id

或者：

source_start/source_end


禁止 LLM 返回新的正文。


返回之后必须验证：

1. utterance_id 存在
2. source_start/end 没改变
3. source_text 没改变
4. speaker_character_id 属于 canonical characters
5. 不得新增正文
6. 不得删除 span


LLM failure：

保留 deterministic 结果。

例如：

kind=dialogue
speaker_resolution=unresolved

不要全段 narration。


==================================================
9. 长章节不能再 trim 中段
==================================================

废弃这种逻辑：

head
+
“…中段省略...”
+
tail


任何情况下：

ChapterRevision.content

都必须 100% coverage。


如果 LLM unresolved spans 太多：

采用 chunk：

- 按 canonical spans 分组
- 每组携带前后 1~3 span context
- 只让 LLM返回 attribution annotation
- merge 时按 utterance_id 合并

不能删除中间正文。


==================================================
10. Script IR 修改
==================================================

修改：

backend/app/services/chapter_script_ir.py
backend/app/application/chapter_script_service.py
backend/app/integrations/llm/chapter_script_adapter.py

要求：

paragraphs 仍存在。

每个 paragraph 新增：

utterances


例如：

原文：

林夜推开门：“走。”苏婉摇头：“不。”

应得到概念结构：

paragraph:
{
  paragraph_id,
  scene_id,
  ...,

  utterances: [
    {
      kind: narration,
      source_text: "林夜推开门："
    },
    {
      kind: dialogue,
      spoken_text: "走。",
      speaker_character_id: 林夜
    },
    {
      kind: narration,
      source_text: "苏婉摇头："
    },
    {
      kind: dialogue,
      spoken_text: "不。",
      speaker_character_id: 苏婉
    }
  ]
}


旧 paragraph.kind /
paragraph.speaker_character_id

为了兼容可以继续保留。

但是新路径不能再把它们当 utterance 真相。

可以把 legacy paragraph 字段定义为：
- aggregate / primary annotation
- 或第一个主要语义

但必须写注释说明 legacy only。


==================================================
11. coverage validation
==================================================

Script IR 现有 source coverage 机制是好的。

将它扩展到 utterance。

必须检查：

所有 canonical spans 的 source ranges：

- 不越界
- 不逆序
- 不重叠异常
- 不漏正文


注意：
引号字符、冒号、空白、换行等是否属于 utterance source span，
可以有明确设计。

但最终必须存在完整 source coverage map。

不能出现：

原文中间 30 字没有任何 span。


建议输出：

segmentation_coverage = {
    total_characters,
    covered_characters,
    coverage_ratio,
    utterance_count,
    unresolved_speaker_count
}

coverage_ratio 必须 = 1.0。


==================================================
12. VoiceLine 不允许再自己切正文
==================================================

重点修改：

backend/app/application/voice_line_service.py
backend/app/workers/voice_tasks.py


新的正式路径：

ChapterRevision
→ canonical Script IR utterances
→ VoiceLineSpec
→ VoiceLine


不要：

VoiceLine
→ chapter_voice_service._slice_chapter()

不要：

VoiceLine
→ _text_only_specs()

作为独立生产 splitter。


如果当前 selected ChapterScriptRevision：

存在 utterances：

直接消费。


如果是 legacy ChapterScriptRevision：

没有 utterances：

必须调用“同一个 canonical segmentation service”
即时产生兼容 utterances。

不是再写一个 fallback splitter。


如果没有 ScriptRevision：

也只能使用同一个 canonical segmentation service。

绝不使用第三套算法。


==================================================
13. occurrence 语义
==================================================

重复文本绝对保留。

测试：

林夜：“好。”
苏婉：“好。”
林夜：“好。”

必须：

3 条 VoiceLine

三个不同 occurrence_id。


删除这种逻辑：

seen_texts = set()
if text in seen_texts:
    continue


如果为了防止 LLM 重复输出：
应该按：

utterance_id / source span

去重。

不是按 text 去重。


==================================================
14. VNGraph 改为 utterance 粒度
==================================================

重点检查：

vn_graph_compiler.py
vn_graph_service.py
vn_graph_derivation.py
以及所有生成 Line node 的位置。


对于新 Script IR：

一个 utterance
=
一个可播放 Line / narration unit


不要：

一个 paragraph
=
一个 Line


例如：

paragraph：

林夜推开门：“走。”苏婉摇头：“不。”

应编译：

Line 1
narration
林夜推开门：

Line 2
speaker 林夜
走。

Line 3
narration
苏婉摇头：

Line 4
speaker 苏婉
不。


paragraph 的：

scene
background
keyframe
stage event

继续在对应 paragraph / utterance 边界挂载。


==================================================
15. VNGraph 与 VoiceLine 禁止 text 全等 join
==================================================

删除生产路径中：

voices_by_text[paragraph.text]

这种设计。


音频匹配必须基于稳定 occurrence identity。


优先方案：

canonical global utterance order
+
speaker_character_id
+
spoken_text
+
chapter_revision_id

与：

make_occurrence_id()

统一。


如果现有 VoiceLine occurrence_id 已经是：

chapter_revision_id
+
order_index
+
speaker_key
+
text

那么：

VNGraph compiler 应使用同一个 helper / 同一个算法计算 occurrence_id，

然后：

voices_by_occurrence_id

关联。


禁止复制一份 hash 算法。
直接复用：

make_occurrence_id()


最终：

VNGraph Line
应该能保存：

voice_line_id
或 occurrence_id
或稳定 segment key

至少一种。

不要再靠 text。


==================================================
16. character_id 必须统一
==================================================

检查：

chapter_script_ir._character_id
voice_line_service._character_id
CanonicalCharacterResolver
StoryBibleRevision character_id


最终确定唯一 canonical source。


优先：

StoryBible canonical character_id
+
CanonicalCharacterResolver


legacy md5 只做历史兼容映射。

新数据不能再生成：

一边 character-<sha256>
另一边 md5(name|project_id)

这种分裂。


修改之后添加测试：

Script IR character_id
==
VoiceLine speaker_character_id
==
VNGraph CharacterId
==
TTS speaker_key 对应 canonical character


==================================================
17. narrator 处理
==================================================

旁白明确：

kind=narration

speaker_character_id=null

speaker_name 可以：
旁白
或 null

内部 speaker_key：

narrator


但是：

dialogue + unresolved speaker

必须：

kind=dialogue
speaker_character_id=null
speaker_resolution=unresolved

不能使用 narrator 的语义覆盖它。


TTS 对 unresolved dialogue：

暂时可以选择：
- narrator fallback voice
或
- generic dialogue voice

但 metadata 必须仍保留：

kind=dialogue
speaker unresolved

不能把业务数据改成 narration。


==================================================
18. deterministic parser 必须覆盖的 golden cases
==================================================

先写 regression tests，再修改实现。

至少加入：

CASE 1
林夜道：“走。”

期望：
dialogue
speaker=林夜


CASE 2
“走。”林夜道。

speaker=林夜


CASE 3
林夜推开门：“走。”

narration/action
+
dialogue 林夜


CASE 4
林夜推开门，看向苏婉：“走。”苏婉摇头：“不。”

至少：
narration
dialogue 林夜
narration
dialogue 苏婉


CASE 5
“我……”苏婉低下头，“不知道。”

两个 dialogue 都归苏婉，
中间 narration/action 保留。


CASE 6
“走吧。”
“去哪？”
“城外。”

三条 dialogue。

speaker 不确定可以 unresolved。


CASE 7
苏姑娘道：“多谢。”

canonical：
苏婉
alias：
苏姑娘

speaker_character_id = 苏婉 canonical ID


CASE 8
婉儿轻声道：“我知道。”

alias resolve。


CASE 9
他说：“不行。”

dialogue
speaker unresolved 或通过 context resolve

不能 narration。


CASE 10
神秘声音道：“回来。”

如果角色表不存在：
dialogue
speaker unresolved

不能 narration。


CASE 11
林夜：“好。”
苏婉：“好。”
林夜：“好。”

必须三个 occurrence。


CASE 12
所谓“天命”，不过如此。

这里不要机械判人物对白。

允许：
narration
或 unresolved classification 交 LLM

但不能直接确定成角色台词。


CASE 13
他读到纸条上写着：“禁止入内。”

要区分 quotation text 与 spoken dialogue。

如果 deterministic 无法确定：
交 ambiguous classifier
不要瞎归人物。


CASE 14
“他说：‘不要过去。’”

嵌套引号。


CASE 15
欧阳修远缓缓说道：“没问题。”

支持 >4 字角色名。


CASE 16
王说：“好。”

支持单字姓名。


CASE 17
Alice said: “OK.”

如果 characters 中有 Alice，
至少不要由于中文 regex 限制失败。


CASE 18
短章节 <200 字：

林夜说：“走。”
苏婉说：“好。”

仍必须正确切 dialogue。


CASE 19
长章节 >8000 字：

中间的 dialogue 必须存在。

coverage=100%。


CASE 20
LLM timeout：

deterministic dialogue 仍保留。


CASE 21
API key 不存在：

deterministic dialogue 仍保留。


CASE 22
同一个 paragraph 内 3 个 speaker。


CASE 23
连续纯 narration。


CASE 24
引号超过 240 字。


CASE 25
相同台词在不同 source span 重复出现。


==================================================
19. 修正错误测试
==================================================

当前有两个测试本身把 bug 当正确行为：

test_unknown_speaker_degrades_to_narration

要改成：

unknown speaker
仍为 dialogue
speaker_resolution=unresolved


test_duplicate_text_dropped

要改成：

相同 text 不同 occurrence
全部保留。


不要为了兼容旧错误测试保留错误业务逻辑。


==================================================
20. Legacy compatibility
==================================================

必须兼容历史：

ChapterScriptRevision

可能没有：

utterances


要求：

旧 revision：
仍可读取
仍可 VNGraph 编译
仍可发布


兼容逻辑：

if utterances exists:
    新 canonical path
else:
    canonical segmentation service
    从原始 paragraph/source text 派生

不要：

legacy → 全 narration


不得做 destructive DB migration。


如果完全可以 JSON additive 完成：
不要加数据库 migration。


==================================================
21. chapter_voice_service 处理
==================================================

backend/app/services/chapter_voice_service.py

目前里面有：
- 第二套 LLM splitter
- seen_texts
- trim_content
- fallback_split
等历史逻辑。


本轮不要继续让它承担 canonical segmentation。


可以：

A.
保留 TTS 兼容函数；
B.
把旧 _slice_chapter 标记 deprecated；
C.
旧调用统一代理到 canonical segmentation；
D.
删除 production caller。

不要在这里再维护另一套真正独立算法。


如果删除函数会导致大量 legacy test / import 断裂：
优先 wrapper / compatibility，
不要激进删除。


==================================================
22. 观测信息
==================================================

每次 segmentation 生成至少应能得到：

segmentation_version
segmentation_source
utterance_count
dialogue_count
narration_count
unresolved_speaker_count
coverage_ratio
llm_resolution_used
fallback_used


例如：

segmentation_version = "canonical-utterance-v1"


让日志 / generation report 能看出：

本次用了：
deterministic
deterministic+llm
legacy compatibility

避免以后错误又静默发生。


==================================================
23. 不允许的做法
==================================================

禁止：

1.
只修 import。

2.
只改 prompt。

3.
增加新的 splitter 文件却继续保留旧三套 production path。

4.
unknown speaker → narration。

5.
LLM failure → 全 narration。

6.
text 相同 → dedupe。

7.
长正文直接截掉中间。

8.
Script IR 和 VoiceLine 各自调用 LLM 重新切正文。

9.
VNGraph 按 text 全等找语音。

10.
复制 character_id hash 算法。

11.
为了测试通过修改正确需求。

12.
大规模无关重构。

13.
修改 /home/workspace/aivn Godot 项目。
本轮仅保证 JSON/graph contract 向后兼容，不直接修改外部运行时。

14.
破坏已有：
scene
background
portrait
keyframe
stage_event
release
publication
逻辑。


==================================================
24. 实施顺序
==================================================

严格按以下顺序做。


STEP 1
补 regression tests。

先让新测试失败。

记录失败原因。


STEP 2
实现 canonical segmentation service。

先保证：

boundary
source span
coverage
speaker resolver
duplicate preservation


STEP 3
接 Script IR utterances。

旧 paragraph metadata 保持。


STEP 4
VoiceLine 改消费 utterances。

删除 production 独立切分。


STEP 5
统一 character_id。


STEP 6
VNGraph 改 utterance 粒度。


STEP 7
VNGraph ↔ VoiceLine 改 occurrence_id 关联。


STEP 8
修正 LLM ambiguity resolver。


STEP 9
legacy compatibility。


STEP 10
全量 regression。


==================================================
25. 重点测试文件
==================================================

至少检查并修改/新增：

backend/tests/test_deterministic_quote_splitter.py
backend/tests/test_chapter_voice_service.py
backend/tests/test_v2_voice_lines.py
backend/tests/test_chapter_script_pipeline.py

并搜索：

grep -Rni "voices_by_text" backend
grep -Rni "_text_only_specs" backend
grep -Rni "_infer_speaker" backend
grep -Rni "_slice_chapter" backend
grep -Rni "seen_texts" backend
grep -Rni "speaker_character_id" backend
grep -Rni "paragraph.text" backend/app
grep -Rni "make_occurrence_id" backend
grep -Rni "character_id" backend/app/services backend/app/application


==================================================
26. 测试指标
==================================================

新增测试中必须能体现：

source coverage = 100%

重复 occurrence preservation = 100%

明确 speaker attribution：
必须正确

明确 dialogue boundary：
必须正确

unknown speaker：
保留 dialogue

legacy revisions：
可读取

VNGraph：
一段多 utterance 能展开多个 Line

VoiceLine：
与 VNGraph order 一致

Audio：
按 occurrence identity 绑定

而不是按 text。


==================================================
27. 回归测试
==================================================

先运行定向测试。

例如：

cd backend

pytest -q \
  tests/test_deterministic_quote_splitter.py \
  tests/test_chapter_voice_service.py \
  tests/test_v2_voice_lines.py \
  tests/test_chapter_script_pipeline.py


然后搜索 VNGraph 相关测试并跑：

pytest -q tests -k "vn_graph or vngraph or voice_line or chapter_script"


最后如果时间允许：

pytest -q


如果全量有历史失败：

明确区分：

本次新增失败
和
pre-existing failure

不得为了消除历史失败改无关业务。


==================================================
28. 最终必须做真实 before / after 验证
==================================================

使用至少下面文本实际打印 canonical segmentation：

文本 A：

林夜推开门，朝里面看了一眼：“有人吗？”苏婉从桌后站起来：“你终于来了。”


文本 B：

“我……”苏婉低下头，“不知道。”


文本 C：

林夜：“好。”
苏婉：“好。”
林夜：“好。”


文本 D：

苏姑娘轻声道：“谢谢。”


文本 E：

“你终于来了。”林夜说道。


打印：

order
kind
source_text
spoken_text
speaker
speaker_character_id
speaker_resolution
source_start
source_end
attribution_source


然后验证：

拼接 source spans 可以重新对应原文。


==================================================
29. 完成后给我报告
==================================================

不要只说“已修复”。

最后输出：

一、Root Cause
简洁列出真正原因。

二、修改文件
逐文件说明。

三、核心架构变化
旧：
paragraph / independent voice split / text join

新：
canonical utterances / shared VoiceLine / occurrence join

四、Before / After
至少给上述 A-E。

五、character_id 收口方式

六、legacy compatibility

七、新增/修改测试

八、实际 pytest 结果

九、git diff --stat

十、git status --short

十一、仍存在的风险

十二、明确告诉我：
现在生产 VoiceLine 是否还会走
_text_only_specs /
chapter_voice_service._slice_chapter
作为独立 splitter。

答案应该是：
不会。

十三、明确告诉我：
VNGraph 是否还按 text 全等查找音频。

答案应该是：
不会。


现在开始执行。

不要停在设计阶段。
先写 regression tests，再实现修复。
中途遇到小的实现选择自行根据现有代码做最小兼容决策，不要每一步都来问我。
只有发现会导致：
- destructive migration
- 大范围 API breaking change
- 必须修改外部 Godot runtime

这三种情况之一时才暂停说明。