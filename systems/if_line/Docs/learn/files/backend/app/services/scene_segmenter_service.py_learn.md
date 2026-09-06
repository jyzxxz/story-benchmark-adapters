# scene_segmenter_service.py — learn note

> Source: `backend/app/services/scene_segmenter_service.py` (468 LOC)
> Route: `standard` | Reuse target: **P2 voice_line 切片可参考 LLM 切分模式**
> Status: `[_]` → master-validated `[x]`

## 职责
把章节正文切成有序的 `SceneSegment` 列表。基于「物理/时间/地点变化」分段。

## ⭐ 复用点（P2 chapter_voice_service 参考）
P2 要把章节正文切成「对白/旁白」序列。可参考这里的 LLM 切分模式：
- 输入：长文本。
- 输出：结构化 segment 列表，每个 segment 有 `summary` / `source_excerpt` 等定位字段。

**但 P2 切的不是环境场景，是对白/旁白**，所以不直接复用，只参考 prompt 设计 + 切分输出 schema 的思路。

## 不复用的部分
- 关键帧生成当前走 segmenter，但 P1.2 `KeyframeMomentSelector` 会替换/前置这层，因为关键帧选片需要「画面感判断」而不是「场景分段」。

## worker 提示
- P2 切对白不需要 LLM 也行 —— 中文小说对白有 `「」` / `“”` 引号包围，正则就能切。先试正则，切不准再上 LLM。
- 但**判断对白归属哪个角色**需要 LLM（看上下文「XX 说道」）。
