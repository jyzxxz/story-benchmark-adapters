# 来源与源码不变保证

本仓库的工作依据是用户确认的 v2.1 适配计划，以及随后明确的“原文件完全不改，允许独立适配器在启动时处理必要接入规则”。旧的源码补丁实验只留在开发工作区，没有作为最终基线源码发布。

上游仓库：

- IF Line：https://github.com/zhending216-hub/if_line
- AI4VisualNovel：https://github.com/ttsmallHot/AI4VisualNovel
- InfiPlot：https://github.com/zonghaoyuan/infiplot

精确提交、每个发布文件的 SHA-256 和省略文件清单见根目录 `baseline-lock.json`。所有发布的上游文件从 Git 指定提交的 blob 直接导出，不从有改动的工作目录复制。运行时接入行为全部位于 `benchmark/`。

IF Line 原有大量生成素材与本次文本接入无关，未复制到新仓库；原始文档中含凭证的文件和凭证文件也未重新分发。这些是明确列出的省略，不是对保留源码的修改。上游源码内容和本仓库的外置适配器分别核验。

AI4VisualNovel 的 Apache-2.0 与 InfiPlot 的 AGPL-3.0 许可证原文各自保留在项目目录中。IF Line 冻结根目录未发现 LICENSE 文件。本仓库不以一份新许可证覆盖三个项目，也不重新声明上游所有权；发布为私有仓库。

原始 30 题来自用户对话附件，原文保存在 `benchmark/source/if_line_eval_prompts_30.v1.md`。CAMPUS-01 brief 从原文代码块逐字提取，开头和时间解释从 v2 对话里的候选代码块逐字提取；它们不是参赛系统生成成果，也未自动升级为正式批准。

原对话的 v2 压缩包未取得，因此公共编译器按可读的 v2.1 契约重新实现并重新测试，没有声称复用或继承原包的“30 项测试通过”结论。原始完整任务前缀和开发任务 profile 分别保存。
