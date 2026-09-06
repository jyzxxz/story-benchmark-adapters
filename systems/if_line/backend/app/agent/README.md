# 自动化二创文章生成 Agent

无人值守地把"选题 → Story Bible → Outline → 全部章节正文"一气呵成跑完,题材完全交给 LLM 自由发挥,覆盖科幻 / 动漫 / 玄幻 / 言情 / 悬疑 等热门方向。

复用 `backend/app/services` 下的 `llm_service`、`workflow_engine`,**零数据库迁移**,所有产出仍然落到既有的 `Project / StoryBible / ChapterOutline / ChapterContent` 表。

---

## 三种触发方式

### 1) CLI 脚本(等价于"我的脚本")

```bash
cd backend
source venv/bin/activate
python -m app.scripts.run_auto_creator --pace fast --theme "科幻+异世界"
```

参数:
- `--pace` `fast` / `medium` / `slow`(章节量 8 / 13 / 20),默认 `medium`
- `--theme` 题材偏好(可选,留空 = 完全自由发挥)
- `--count` 连续跑几篇,默认 1
- `--word-min` / `--word-max` 单章字数区间,默认 1500 / 3000

### 2) REST 路由

```bash
curl -X POST http://localhost:8007/api/auto-agent/run \
     -H 'Content-Type: application/json' \
     -d '{"pace":"fast","theme_hint":"动漫+重生"}'
```

返回 `{"project_id": 12, "status": "completed"}`,新项目立刻出现在前端项目列表里。

### 3) Cron / Tmux 守护

```bash
bash scripts/auto_creator_cron.sh once    # 跑一篇就退
bash scripts/auto_creator_cron.sh loop    # 每 6 小时跑一篇,直到被 kill
```

`loop` 模式靠 tmux 守护进程,不写系统 crontab,先观察稳定性;后续若要沉底为正式 cron,可走 `execution-cron-builder` skill。

---

## 中间产物落盘

每次 run 在 `backend/agent_runs/<UTC时间戳>/` 下留一份完整档案,便于事后排查:

| 文件 | 内容 |
|---|---|
| `idea.json` | brainstorm 阶段的原始创意(title / characters / story_start / end / style / pace / extra_requirements / theme_tag) |
| `bible.json` | Story Bible `raw_json` |
| `outline.json` | 章节大纲数组 |
| `chapters.json` | 每章正文前 200 字摘要 |
| `manifest.json` | 本次 run 元信息(project_id / 起止时间 / 状态 / 章节数 / 错误信息) |
| `error.log` | 失败时的完整 traceback(仅失败时存在) |

数据库里的 `Project.status` 最终会停在 `pre_generating`(`chapter_generating` 之后的下一步),与人工流程完全一致。

---

## 后期接入图片 / TTS

`AutoCreator.run()` 末尾留了两个开关和对应 stub 方法,**主链路不用动**,只要把 `presets.py` 里对应开关从 `False` 改 `True` 并填上 stub 实现即可:

| 开关 | stub 方法 | 接入点 |
|---|---|---|
| `ENABLE_ASSETS` | `_generate_assets` | `llm_service.generate_asset_prompts` + `image_generation_service` |
| `ENABLE_TTS` | `_generate_tts` | `services.tts_service` |

---

## 失败保护 — 两层自我纠错

### 第一层:LLM 调用重试(`agent/resilient_llm.py`)

所有 4 个 LLM 生成方法(bible / outline / chapter / asset)都包了一层重试:

- **重试次数**:`MAX_LLM_RETRIES=3`(首次 + 2 次重试)
- **指数退避**:`LLM_RETRY_BASE_DELAY=1.0`,延迟序列 1s → 2s
- **可重试错误**:`APIError / APITimeoutError / APIConnectionError / RateLimitError / JSONDecodeError / TimeoutError / ConnectionError`
- **不可重试(直接抛)**:`AuthenticationError / BadRequestError`(配置/认证问题重试无用)
- 原有 `llm_service.py` 完全不动,这里只是外层包装,手工触发的路由调用不受影响

### 第二层:正文质量一致性检查(`agent/quality_check.py`)

章节正文 LLM 调用成功后,还要过一道质量检查才能落库:

| 检查项 | 类型 | 不达标处理 |
|---|---|---|
| content 字段非空 | 硬错误 | 触发质量重试 |
| 字数 ≥ `max(300, word_min × 70%)` | 硬错误 | 触发质量重试 |
| 标题与大纲标题一致 | 硬错误 | 触发质量重试 |
| 章节正文出现大纲角色名(中文 ≥2 字) | 软错误 | 触发质量重试 |
| `ending_hook` 关键词在章节末尾 300 字内呼应 | 软错误 | 触发质量重试 |

质量重试上限同样是 `MAX_CHAPTER_FAILURES=3`。**用尽仍不达标时接受最后一次结果**(避免某章边缘质量导致整篇全死)。

### 其它保护

- 单章连续 LLM 调用 + 质量都失败 `MAX_CHAPTER_FAILURES` 次后,放弃整篇,写 `error.log` + `manifest.status=failed`,抛出向上传播
- `Project` 状态由 `WorkflowEngine.fail_step` 兜底,不会卡在非法中间态
- `run_many` 串行跑多篇时,单篇失败不影响后续
- tmux 守护循环里 `bash ... \|\| true`,单篇挂掉不影响下一次定时跑

### 调参

所有阈值在 `app/agent/presets.py`:

```python
MAX_LLM_RETRIES = 3              # LLM 单次调用尝试次数
LLM_RETRY_BASE_DELAY = 1.0       # 指数退避基数 (秒)
MAX_CHAPTER_FAILURES = 3         # 单章质量重试上限
QUALITY_MIN_LENGTH_RATIO = 0.7   # 字数下限比例 (相对 word_min)
QUALITY_MIN_LENGTH_ABSOLUTE = 300  # 字数绝对下限
QUALITY_ENDING_HOOK_SEARCH_WINDOW = 300  # hook 呼应检查窗口
```


---

## 题材合规

brainstorm prompt 明确禁止直接复刻已有 IP 的专有名词(角色名、世界观名),鼓励**原创**设定即使借鉴题材套路。生成的作品可直接商用而无需规避版权,但请人工抽查 `idea.json` 里是否有 LLM 偶尔蹦出的近似名再发布。
