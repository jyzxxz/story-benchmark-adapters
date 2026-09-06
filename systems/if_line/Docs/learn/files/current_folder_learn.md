# Repo root — folder learn

> if_line: 小说 → 视觉小说自动生成。

## 顶层结构
- `blueprint.md` —— ⭐ 唯一权威 blueprint 源（本 cron-builder 套件的源）。execution-cron-builder 以此为需求。
- `backend/` —— FastAPI + SQLAlchemy + LLM/TTS/Image 服务。
- `frontend/` —— Vue 3 + Vite + Element Plus。
- `Docs/` —— 长期文档 + learn 笔记 + validation log。
- `docs/` —— 早期文档（小写，注意区分）。
- `scripts/` —— 顶层脚本。
- `runs/`（gitignored）—— auto_creator 跑批输出。
- `competition-runs/`（gitignored）—— compete-cron 输出。
- `.cron/`（gitignored）—— execution-cron 自动化区。
- `.ops/`（gitignored）—— cron 私有 helper。

## ⭐ 顶层文档
- `visual-generation-requirements.md` (24KB) —— 视觉生成原始需求，写蓝图前的历史背景。
- `VNNodeLibrary.txt` —— VN 节点类型库，P3.1 vngraph 扩字段参考。
- `CONFIG_REPORT.md` / `FINAL_REPORT.md` / `OPTIMIZATION_REPORT.md` / `PROJECT_SUMMARY.md` / `SYSTEM_READY.md` —— 历史 stage 报告。
- `python-backend.local.log` —— 后端运行日志（gitignored）。

## ⭐ 启动脚本
- `start_backend.sh` —— 启 FastAPI。
- `start_frontend.sh` —— 启 Vite dev。
- `start_tts.sh` —— 启 TTS 引擎（如果用本地 EmotiVoice）。

## 端到端验证流程
1. `./start_backend.sh` + `cd frontend && npm run dev`。
2. `python backend/app/scripts/run_auto_creator.py` 跑新项目（含 P4 AI 味门）。
3. 前端：项目 → 章节视图 → 一键生成素材（P1）→ 生成本章配音（P2）→ VNGraphPreview 整章 Tab（P3）。
4. `GET /full-vn-graph` 查 JSON，确认 dialogue 节点有 `audio_url`、背景节点有 `image_url`。

## 测试件
- `backend/test_*.py` / `backend/tests/test_*.py` —— 现有 pytest 套件，覆盖 background / vn_graph / scene_segmenter 等。P1/P2/P3 落地后要扩对应测试。
- 顶层 `test_api.py` / `test_system.py` / `quick_test.py` —— 冒烟脚本。

## cron-builder 落地区
- 阶段 3 `execution-cron` 装在 `.cron/scripts/` + `.cron/automation_repo_slotN/`。
- 阶段 2 `compete-cron` 输出在 `competition-runs/P{1,2,3,4}/`。
- 阶段 4 `optimization` 输出在 `Docs/optimization/`。
- 阶段 1 `learn` 输出在 `Docs/learn/`（本目录）。
