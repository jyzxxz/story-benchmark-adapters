# 来源与源码校验

本仓库最初依据用户确认的 v2.1 适配计划，以及“原文件完全不改，允许独立适配器在启动时处理必要接入规则”发布。2026-09-07，用户进一步明确授权修复 IF Line 的 HTML 正文到可播放脚本转换缺陷，并要求其他两个项目原生源码不改。当前 IF Line 因此是带公开修复补丁的基线变体；不能称作完全未修改的上游版本。此前实测的原始源码记录不覆盖、不回填。

上游仓库：

- IF Line：https://github.com/zhending216-hub/if_line
- AI4VisualNovel：https://github.com/ttsmallHot/AI4VisualNovel
- InfiPlot：https://github.com/zonghaoyuan/infiplot

原始提交、上游文件的 SHA-256 和省略文件清单见根目录 `baseline-lock.json`。该锁保留原始内容。IF Line 的授权差异由 `native-patches/` 单独记录原文件哈希、修复后哈希和精确补丁；每次启动同时核对原始锁及显式补丁，记录实际变体与补丁哈希。AI4VisualNovel 与 InfiPlot 继续只接受原始锁。运行时接入行为位于 `benchmark/`，三者运行前后均检查完整源码集合，未声明的新增文件也视为漂移。

IF Line 原有大量生成素材与本次文本接入无关，未复制到新仓库；原始文档中含凭证的文件和凭证文件也未重新分发。这些是明确列出的省略，不是对保留源码的修改。上游源码内容和本仓库的外置适配器分别核验。

AI4VisualNovel 的 Apache-2.0 与 InfiPlot 的 AGPL-3.0 许可证原文各自保留在项目目录中。IF Line 冻结根目录未发现 LICENSE 文件。本仓库不以一份新许可证覆盖三个项目，也不重新声明上游所有权；发布为私有仓库。

原始 30 题来自用户对话附件，原文保存在 `benchmark/source/if_line_eval_prompts_30.v1.md`。CAMPUS-01 brief 从原文代码块逐字提取，开头和时间解释从 v2 对话里的候选代码块逐字提取；它们不是参赛系统生成成果，也未自动升级为正式批准。

原对话的 v2 压缩包未取得，因此公共编译器按可读的 v2.1 契约重新实现并重新测试，没有声称复用或继承原包的“30 项测试通过”结论。原始完整任务前缀和开发任务 profile 分别保存。
