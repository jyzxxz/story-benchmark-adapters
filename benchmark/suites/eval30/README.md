# 30 题公共故事输入：v4-30-pilot.1

六类题材，每类五题：校园、科幻、悬疑、奇幻、历史、情感。每题采用原 brief + 同一固定开头 + 统一续写要求 + 4000 个新增可见 Unicode 字符的阅读窗口。开启原生图片，不要求全篇结束，不做全分支穷举。

## 从哪里开始

完整操作见 [一键实验指南](../../../docs/EXPERIMENTS.md)。已经配置环境和自己的模型密钥时：

```bash
bash tools/experiment.sh quick --allow-pilot
bash tools/experiment.sh genres --allow-pilot
bash tools/experiment.sh full --allow-pilot
```

这些命令会要求输入 `RUN` 后才开始付费生成；自动化使用 `--yes` 明确授权。原始 30 题在 [历史原文](../../source/if_line_eval_prompts_30.v1.md)，本轮共用 [prefix.txt](prefix.txt)。不要使用原文旧前缀中的完整多结局要求或额外回忆回合。

| 题材 | 题目定义与固定开头 |
|---|---|
| 校园 | [campus.json](campus.json) |
| 科幻 | [sci-fi.json](sci-fi.json) |
| 悬疑 | [mystery.json](mystery.json) |
| 奇幻 | [fantasy.json](fantasy.json) |
| 历史 | [history.json](history.json) |
| 情感 | [emotion.json](emotion.json) |

共同章/场解释、来源、角色呈现边界及输入合同在 [catalog.json](catalog.json)。上面文件共同构成完整题目源，并非六份可以直接一次性粘贴进模型的请求。

## 免费导出全部完整提示词

```bash
python3 tools/eval30.py --out work/eval30-preview
```

无需安装原生环境或填写密钥。输出 `PROMPTS_30_COMPILED.md` 和每题独立 `compiled/<case_id>/shared_task.txt`，调用仓库真实编译器核对三个 payload 字符串一致以及与上一版测试包的逐题哈希。已有目录拒绝覆盖。编译通过不能代替真实生成。

[内容审核说明](CONTENT_REVIEW_NOTES.md) 区分原文和新增候选；[八维保存说明](EVIDENCE_8_DIMENSIONS.md) 说明哪些证据供后续评审。正式审批需要真实审阅人和内容哈希记录；本目录始终保留 pilot，不自动变成 approved。
