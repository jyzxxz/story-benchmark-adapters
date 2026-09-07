# IF Line HTML 正文转换修复

2026-09-07，用户明确允许修复 IF Line 原生源码中的 HTML 正文到可播放脚本转换问题，同时要求 AI4VisualNovel 与 InfiPlot 原生源码保持不变。

上一轮实际生成的第二个新增章节带有 `</p><p>` 标签。原生模型返回的 Script IR 省略这些展示标签，而正文覆盖校验仍按带标签原文逐字比较，连续三次失败后停止。原始章节、模型响应和失败日志均保留。

修复的边界是统一正文与脚本所用的可见文本表示，继续验证实际正文没有缺失、篡改或调序。公共任务、固定开头、原生规划、审核、选择与图片生成流程不因该修复而改变。原始模型响应仍可追溯，不用适配器补写故事或放宽正文覆盖检查。

`baseline-lock.json` 保留原始上游文件哈希。`native-patches/` 保存单独的补丁与逐文件修改前后哈希；配置通过 `systems.if_line.source_patch` 显式选择该变体。每次根运行验证完整源树，保存补丁证据并记录 `source_modified`、变体名称与补丁哈希。未声明文件变化会使预检失败。

该版本应标注为“IF Line + HTML 正文转换修复”，不能称为完全未修改的上游基线。原始基线运行的失败样本保留；正式评测应固定使用同一个补丁版本，不把不同变体的重复实验合并。

v4 的 `plan.source_identity` 固定原始锁、补丁和实际源树哈希；`manifest.native_source_variant` 记录变体，`source_verification.frozen_identity_unchanged` 表示本轮一直使用冻结后的同一版本。v3 `result.json` 保留原数据结构，带补丁的 IF Line 将 `provenance.native_source_unchanged` 记为 `false`，补丁细节在其 `manifest.json` 中。外置启动器的 `source_modified=false` 仅指启动器运行期间没有写原生文件，另有 scope 字段明确该含义。

实现位于 IF Line 的 `chapter_source_text.py`、`chapter_script_ir.py` 与 `chapter_script_adapter.py`。仅识别预先列明的排版标签，实体单次解码；未知标签、注释与 script/style 内容保留为字面文本。超过片段长度限制时，不把一个实体解码出的多个字符分到不同源区间。新 IR 标记 `text_projection`，其可见正文、raw spans、段落次序与源坐标共同受校验。旧 IR 没有该标记时保留原校验兼容性，不修改历史快照。

源码补丁 SHA-256：`76e44a1d6ee1044620fc65b2bfb6a7b93c4eed2b7ebc44d5305d4087ebc5c79d`。三处源码及两处测试修改均列入清单，实际源树共验证 1202 个文件；原始基线锁不变。AI4VisualNovel 35 个、InfiPlot 927 个文件仍逐字节匹配原始基线。

2026-09-07 本地验证：96 项 IF Line 原生回归通过；上一轮三份失败响应均重现原版错误，修复版均生成 74 段并保留 2951 个 raw 字符与 2870 个可见非空白字符。独立检查确认 raw 原文、哈希和 VNGraph 文本/来源坐标一致，另 12 个正文改动负例被拒绝、4 个实体与比较符边界通过。公共记录 23 项、历史 v3 返回 38 项、补丁与证据保存 20 项、旧来源校验 3 项通过。没有为这些回归调用模型。

新真实模型补测尚待结果；离线回归通过不等于三个原生故事均已完成。
