"""
Step 0: 选题 brainstorm。

完全交给 LLM 自由发挥,鼓励从科幻 / 动漫 / 玄幻 / 言情 / 悬疑 等热门题材里
自由组合元素,产出**原创**设定(明示禁止直接复刻已有 IP 的角色名 / 世界观名,
规避版权)。返回字段严格对齐 schemas.ProjectCreate。
"""
import json
from typing import Optional, Dict, Any

from app.services.llm_service import llm_service
from app.agent.presets import BRAINSTORM_TEMPERATURE


def _build_brainstorm_prompt(theme_hint: Optional[str]) -> str:
    hint_block = (
        f"【用户偏好（软引导，可自由覆盖）】{theme_hint}"
        if theme_hint else "（无用户偏好，请选择你觉得最具爆款潜力的题材。）"
    )
    return f"""你是一位擅长挖掘爆款概念的轻小说资深主编。
请为中文网文/轻小说受众，生成**一个原创**故事概念。

{hint_block}

自由发挥。鼓励的题材池（可自由混搭）：
- 科幻：机甲、赛博朋克、太空歌剧、废土末日、AI 觉醒
- 动漫向：少年热血战斗、异世界、校园奇幻、魔法少女、带反转的日常
- 东方玄幻：仙侠修真、都市灵异、重生/穿越
- 言情、悬疑、推理、惊悚——任何组合皆可

【铁律（保证原创性与版权安全）】
- 禁止复用任何已有 IP 的专有名、世界观名或标志性术语（不要出现「三体」「塔图因」「霍格沃茨」「原神」等）。
- 即便借鉴某题材套路，设定本身必须原创。
- **所有字符串字段一律使用简体中文**（pace 字段除外，固定为 fast/medium/slow）。
- 3 到 5 位主要角色，每人都要有一个有记忆点的中文名。

请只返回严格 JSON（不要 markdown、不要任何解释）：
{{
  "title": "作品标题，简洁有记忆点，中文",
  "characters": ["主角全名", "重要角色2", "重要角色3"],
  "story_start": "故事开篇设定，80-150 字，中文，含主角处境和触发事件",
  "story_end": "故事结局设定，60-120 字，中文，与开篇呼应或反差",
  "style": "整体风格，中文，如 '赛博朋克+悬疑，冷峻克制'",
  "pace": "fast 或 medium 或 slow",
  "extra_requirements": "额外创作要点，50-100 字，中文，含主题与雷区",
  "theme_tag": "题材标签，1-3 个词，如 '机甲 重生 校园'"
}}"""


async def brainstorm_idea(theme_hint: Optional[str] = None) -> Dict[str, Any]:
    """让 LLM 自由产出一个原创小说设定。

    返回 dict, 字段对齐 schemas.ProjectCreate (多了 theme_tag 仅用于日志/落盘)。
    任何字段缺失时填入安全默认值,避免后续断言失败。
    """
    response = await llm_service.client.chat.completions.create(
        model=llm_service.model,
        messages=[
            {"role": "system", "content": "你是一位擅长策划爆款网文/轻小说的资深编辑。"},
            {"role": "user", "content": _build_brainstorm_prompt(theme_hint)},
        ],
        temperature=BRAINSTORM_TEMPERATURE,
        response_format={"type": "json_object"},
        timeout=90.0,
    )
    raw = response.choices[0].message.content
    idea = json.loads(raw)

    idea.setdefault("title", "未命名作品")
    idea.setdefault("characters", [])
    idea.setdefault("story_start", "主角意外卷入一场风波。")
    idea.setdefault("story_end", "主角迎来了命运的转折。")
    idea.setdefault("style", "")
    idea.setdefault("pace", "medium")
    idea.setdefault("extra_requirements", "")
    idea.setdefault("theme_tag", "")

    if idea["pace"] not in ("fast", "medium", "slow"):
        idea["pace"] = "medium"

    if not isinstance(idea["characters"], list):
        idea["characters"] = []

    return idea
