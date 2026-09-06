# backend/app/ — folder learn

> 应用主目录。子目录：`agent/` / `services/` / `routers/` / `config/` / `scripts/` / `utils/`。

## 顶层件
- `main.py` —— FastAPI app 实例 + router 注册。P2 voice router、P3 整章接口都要在这里注册。
- `models.py` —— SQLAlchemy 模型（Project / StoryBible / ChapterOutline / ChapterContent / Asset / VNGraph）。P2 voice_line 落 Asset 表，无需新表。
- `schemas.py` —— Pydantic response 模型 + `BackgroundSceneSpec` / `PeoplePolicy` / `StyleTag`。P3 vngraph 新字段在 response model 里要 Optional。
- `database.py` —— DB session。
- `__init__.py` —— 空。

## ⭐ Asset 表（P2 关键复用）
统一资源表，`asset_type` 区分类型。P2 加 `"voice_line"` 取值即可，**不需要新表**：
- `id / project_id / chapter_index / asset_type / target_name / prompt / image_url / status`
- `character_id / emotion / outfit / pose`（立绘用）
- `scene_location / mood / event_name / seed`（背景/关键帧用）
- `generation_time`

P2 voice_line 字段映射：
- `asset_type = "voice_line"`
- `target_name = speaker_name or "narration"`
- `prompt = line.text`（原文，P3 装配按这个匹配 vngraph dialogue）
- `image_url = audio_url`（字段名虽叫 image_url，存 audio URL）
- `character_id / emotion` 照填

## ⭐ VNGraph 表
- `id / project_id / chapter_index / graph_json / status`
- 按章存。P3 不改表，只在 graph_json 内容里加字段。

## 配置目录 `config/`
- `image_generation_profiles.json` —— scene taxonomy + people_policy 默认值。
- P1 立绘需求分析可加类似的角色 emotion taxonomy JSON。

## `scripts/`
- `run_auto_creator.py` —— 命令行触发 auto_creator。P4 验证主入口。
- `backfill_outline_scene_emotion.py` —— 历史数据回填，P4 不动。

## `utils/`
- `logging.py` —— `xlog` 包装，所有服务统一用。
- `vn_graph_validator.py` —— vngraph schema 校验，P3.1 改字段后要同步更新这里的 validator。
