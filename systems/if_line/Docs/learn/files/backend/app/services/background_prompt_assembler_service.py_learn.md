# background_prompt_assembler_service.py — learn note

> Source: `backend/app/services/background_prompt_assembler_service.py` (175 LOC)
> Route: `standard` | Reuse target: **P3 vn_graph_assembler 装配模式参考**
> Status: `[_]` → master-validated `[x]`

## 职责
把 `BackgroundSceneSpec`（结构化中文场景描述）+ style_tags + forbidden_chars 装配成英文 SDXL/Redux 画图 prompt。

## 复用点
- **装配器模式**：输入多个结构化字段（spec + tags + forbidden），输出一个目标格式字符串。P3 `vn_graph_assembler` 装配 vngraph 节点的「text + image_url + audio_url」可参考这种「读 spec → 拼字段 → 输出 dict」的模式。
- **forbidden_characters 处理**：装配时显式排除人物相关 token，避免画进人脸。P3 装配 dialogue 节点匹配 audio 时也可参考类似的「字段约束 + 黑名单」思路。

## 不复用的部分
- 英文 prompt 生成逻辑（SDXL-specific），P1 立绘/关键帧已有 `prompt_builder_service` 处理，不走这里。
