"""
Prompt 模板模块 - 为 LLM 生成任务提供结构化的提示词模板

本模块包含多个 Prompt 生成函数，用于：
1. Story Bible（故事圣经）生成 - 小说世界观、角色、风格等核心设定
2. 章节大纲生成 - 戏剧结构化的章节规划
3. 章节大纲修订 - 根据反馈修改大纲
4. 章节正文生成 - 基于大纲创作具体内容
5. 视觉素材 Prompt 生成 - 为 AI 绘画工具生成英文提示词

每个函数都返回精心设计的英文 Prompt，确保 LLM 输出高质量、结构化的 JSON 结果。
"""

import json
from typing import Dict, Any, List, Optional


# P4.3: 反 AI 味负例清单（套话词表 + 模板句），供 get_chapter_content_prompt 注入
ANTI_AI_NEGATIVE_LIST = [
    "不禁", "仿佛", "宛如", "一抹", "流转", "莫名的", "淡淡的",
    "他知道这一刻", "时间仿佛静止", "心中涌起一股",
]


def get_story_bible_prompt(
    title: str,
    characters: List[Any],
    story_start: str,
    story_end: str,
    style: str,
    pace: str,
    extra_requirements: str,
    source_work: str = "",
) -> str:
    """
    生成 Story Bible（故事圣经）的 Prompt

    Story Bible 是小说的核心设定文档，包含：
    - 世界观设定：历史背景、规则体系、社会结构
    - 角色设定：外貌、性格、动机、内心冲突、声音风格
    - 关系网络：角色间的关系和利益冲突
    - 主题基调：核心主题探索和情感基调
    - 风格规则：写作风格指南和词汇建议
    - 禁忌事项：必须避免的毒点和叙事雷区

    Args:
        title: 小说标题
        characters: 核心角色名称列表
        story_start: 故事开篇设定
        story_end: 故事结局设定
        style: 整体风格（如：古风、现代、科幻）
        pace: 叙事节奏（如：快节奏、慢热、张弛有度）
        extra_requirements: 额外要求

    Returns:
        str: 完整的英文 Prompt，要求 LLM 返回 JSON 格式的 Story Bible

    Example:
        >>> prompt = get_story_bible_prompt(
        ...     title="长安乱",
        ...     characters=["李长风", "苏婉儿"],
        ...     story_start="少年剑客初入长安",
        ...     story_end="大仇得报归隐山林",
        ...     style="古风武侠",
        ...     pace="快节奏",
        ...     extra_requirements="要有江湖气息"
        ... )
    """
    character_names = []
    for character in characters:
        if isinstance(character, dict):
            value = (
                character.get("name")
                or character.get("canonical_name")
                or character.get("display_name")
            )
        else:
            value = character
        normalized = str(value or "").strip()
        if normalized:
            character_names.append(normalized)

    return f"""你是一位从业十年的白金级小说架构师与总编。请为小说《{title}》构建一套逻辑严密、张力十足、世界观自洽的「故事圣经（Story Bible）」。

【语言要求（最高优先级）】
- 除 JSON 字段名（如 worldview、characters 等键名）必须保留英文之外，**所有字段内容、描述、示例一律使用简体中文**。
- 角色名、地名等专有名词必须用中文。

【基础输入信息】
- 核心角色：{', '.join(character_names) if character_names else '（请自行设计主角）'}
- 故事开篇：{story_start}
- 故事结局：{story_end}
- 整体风格：{style}
- 二创原作：{source_work if source_work else '无（原创项目）'}
- 叙事节奏：{pace}
- 额外要求：{extra_requirements if extra_requirements else '无'}

【核心创作要求】
1. 设定必须服务于剧情，拒绝无意义的设定堆砌（info-dump）。
2. 角色动机必须有「核心驱动力」（创伤/欲望/信念等），角色之间要形成性格互补或冲突。
3. 结局必须呼应或反差开篇，体现主角的成长或堕落。
4. 如果填写了二创原作，原作角色的姓名、物种、发型、瞳色、标志服装与配饰必须忠于原作，
   不得擅自添加眼罩、角、伤疤等原作不存在的特征；原创角色则按原作世界的设计语言进行适配。

请严格按以下 JSON 格式输出（键名保持英文，所有值用中文）：
{{
  "worldview": "世界观设定（含历史背景、核心规则/力量体系、社会结构，150-200 字）",
  "theme_and_tone": "核心主题探索与情感基调说明（50-100 字）",
  "characters": [
    {{
      "name": "角色姓名（中文）",
      "role": "主角/反派/重要配角",
      "gender": "male/female/unknown（三选一，必须显式填写；男性角色填 male，女性角色填 female，确实无法判断才填 unknown）",
      "visual_profile": {{
        "schema_version": "character_visual_v1",
        "world_style": "modern/historical/xianxia/fantasy/scifi/cyberpunk/space/apocalypse/supernatural/rural",
        "species": "human/android/demon/spirit/other",
        "age_group": "toddler/child/teen/young_adult/adult/middle_aged/senior",
        "gender_presentation": "masculine/feminine/androgynous",
        "role_family": "student/office/education/medical/public_safety/service/family/leadership/professional/media/athlete/historical_court/cultivator/mystery/scifi_crew/civilian/antagonist",
        "role_key": "antagonist/athlete/barista/best_friend/boss/chef/child/colleague/delivery_rider/detective/doctor/driver/father/firefighter/gamer/grandfather/grandmother/hr_interviewer/investor/journalist/lawyer/librarian/mentor/mother/mysterious_stranger/neighbor/nurse/paramedic/patient/police/principal/rebel/researcher/security_guard/shop_clerk/startup_founder/student/teacher/therapist/office_worker/suspect/monk/romantic_lead/farmer/android/engineer/captain/medic/pilot/survivor/influencer/corporate_executive/hacker/toddler/assassin/emperor/guard/innkeeper/maid/official/princess/sect_master/sword_master/swordswoman/healer/scholar/demon/supernatural_being/civilian/other",
        "body": {{"height": "short/average/tall", "build": "slim/lean/average/athletic/strong/stocky/soft"}},
        "face": {{"shape": "round/oval/angular/square/heart/mature", "skin_tone": "pale/light/medium/tan/dark/fantasy", "eye_color": "black/dark_brown/brown/blue/green/gray/gold/red/other"}},
        "hair": {{"color": "black/dark_brown/brown/blonde/red/gray/white/blue/purple/pink/other", "length": "bald/very_short/short/medium/long/very_long", "style": "straight/wavy/curly/ponytail/bun/braided/messy/neat/spiky/bob/twin_tail/covered/other"}},
        "outfit": {{"style": "casual/school_uniform/business/formal/workwear/sportswear/streetwear/medical_uniform/public_safety_uniform/military/historical_robe/xianxia_robe/armor/cyberpunk/scifi_uniform/rural/traditional/religious/other", "primary_colors": ["black/white/gray/charcoal/navy/blue/cyan/green/olive/red/burgundy/orange/yellow/gold/brown/beige/pink/purple/silver"], "items": ["long_coat/shirt/blazer/trousers/skirt/dress/hoodie/jacket/lab_coat/scrub_top/uniform/armor/robe/cape/boots/sneakers/gloves/hat/scarf"]}},
        "signature_features": ["thin_scar_left_brow/facial_scar/freckles/beauty_mark/glasses/eyepatch/tattoo/mechanical_limbs/pointed_ears/horns/halo"],
        "accessories": ["wristwatch/glasses/earrings/necklace/hairpin/badge/stethoscope/sword/book/tablet/helmet/hat/scarf"],
        "visual_temperament": ["reserved/serious/warm/cheerful/gentle/cold/confident/mysterious/energetic/calm/stern/rebellious/professional/innocent/weary"]
      }},
      "personality": "性格特质（含优点与致命弱点/性格缺陷，中文）",
      "motivation_and_goal": "核心动机与终极目标（中文）",
      "internal_conflict": "内心挣扎与冲突（中文）",
      "voice": "说话风格总结（中文，必须包含语气、用词倾向、情感表达方式、句式习惯等，能够直接指导后续章节中该角色的对话生成）"
    }}
  ],
  "character_relations": "人物关系网络简述（突出利益冲突与情感羁绊，中文）",
  "main_conflict": "贯穿全文的核心冲突（表层冲突 + 深层冲突，中文）",
  "emotional_line": "主角的情感发展弧线（起承转合，中文）",
  "style_rules": "写作风格指南（如冷峻写实/华丽唯美/幽默等，附具体用词建议，中文）",
  "ending_constraints": "抵达既定结局必须满足的前提条件（中文）",
  "forbidden_points": ["绝对不能出现的毒点或叙事雷区（中文）", "不符合本作设定的剧情（中文）"],
  "writing_notes": ["本题材的特殊写作技巧（中文）", "伏笔建议（中文）"]
}}

【人物视觉档案硬性规则】
- 每个角色必须输出完整 visual_profile；它是通用图库检索画像，不是原作角色的最终生图身份。不要输出 appearance、visual_description_cn、visual_prompt_en 或 canonical_identity，原作身份由服务器单独识别。
- visual_profile 只使用现有枚举，描述图库中应检索的最接近外观类型。原作角色也必须映射到最接近的通用分类，不得创造 ninja_world、ninja_uniform 等作品专用值。
- visual_profile 只描述跨场景稳定的图库特征，不写当前表情、动作、受伤状态或临时换装。
- gender 是剧情身份，gender_presentation 是可视外观，二者应保持一致；确有中性造型时才使用 androgynous。
- 同一故事的主要角色应在发型、服装主色和标志特征上明显可区分。
- 所有字段严格使用上面给出的英文枚举值；没有对应项时使用空数组，不得自造标签。

【关于 voice 字段的重要说明】
每个角色的 voice 字段必须基于其年龄、性别、身份、性格、核心动机与内心冲突，总结出该角色的说话风格。voice 必须能直接指导后续章节中该角色对话的生成。

voice 应包含以下要素：
- 语气特征（温和/冷峻/激动/克制等）
- 用词倾向（正式/口语/书面/市井俚语等）
- 句式习惯（长句/短句/陈述/反问等）
- 情感外露程度（直抒胸臆/隐忍克制/讽刺反问等）
- 说话节奏（快/慢/不疾不徐/急促等）

优秀示例：
"voice": "语气冷静克制，多用短促的陈述句，很少直白表露情感；在压力之下会用反问和讽刺来掩饰真正的关切。"

不合格示例（请避免）：
"voice": "他说话很有个性。"（太笼统，无法指导对话生成）

请确保每个角色的 voice 字段具体且可操作，以便在后续章节生成时维持角色对话风格的一致性。"""


def get_chapter_outline_prompt(
    story_bible: Dict[str, Any],
    chapter_count: int,
    *,
    pace: str = "medium",
    instructions: str | None = None,
) -> str:
    """
    生成章节大纲的 Prompt

    章节大纲是小说的骨架，遵循戏剧结构原则：
    - 弧线结构：多个章节构成完整的故事弧
    - 钩子规则：每章结尾必须有悬念
    - 冲突升级：从言语冲突升级到核心利益冲突
    - 情感波动：角色情绪在希望与绝望间起伏

    Args:
        story_bible: Story Bible JSON 对象，包含世界观、角色等核心设定
        chapter_count: 要生成的章节数量
        pace: 只作为叙事节奏参考，不决定章节数量
        instructions: 用户提供的额外大纲要求

    Returns:
        str: 完整的英文 Prompt，要求 LLM 返回 JSON 格式的章节大纲

    Output JSON Structure:
        {
            "overall_arc": "整体故事弧线摘要",
            "chapters": [
                {
                    "chapter_index": 1,
                    "title": "章节标题",
                    "summary": "详细摘要（200-300字）",
                    "core_conflict": "本章核心冲突",
                    "key_revelation": "关键信息揭示",
                    "characters": ["出场角色"],
                    "scene_locations": ["场景地点"],
                    "emotion_shift": "情感转变",
                    "ending_hook": "结尾悬念设计",
                    "visual_keywords": ["视觉关键词"]
                }
            ]
        }

    Example:
        >>> story_bible = {"worldview": "古代武侠世界", ...}
        >>> prompt = get_chapter_outline_prompt(story_bible, chapter_count=10)
    """
    instruction_block = (
        f"\n【用户额外要求】\n{instructions.strip()}\n"
        if instructions and instructions.strip()
        else ""
    )
    return f"""你是一位擅长网文爽感节奏的剧情策划师。
请基于下方「故事圣经」，为 {chapter_count} 章生成一份完整的故事大纲。
章节数量只由 chapter_count 决定；节奏偏好「{pace}」仅用于调整每章事件密度，不得改变输出章数。

【语言要求（最高优先级）】
- 除 JSON 字段名（如 overall_arc、chapters 等键名）必须保留英文之外，**所有字段内容、标题、摘要一律使用简体中文**。

【故事圣经设定】
{json.dumps(story_bible, ensure_ascii=False, indent=2)}
{instruction_block}

【大纲设计准则】
1. **弧线结构**：这 {chapter_count} 章必须构成一个完整的故事弧或一个重大事件周期。
2. **钩子法则**：每章结尾必须有悬念（钩子），不允许平淡收尾——但第 {chapter_count} 章（最后一章）是全书终章，规则完全不同：
   - 终章 summary 必须覆盖本章内的**完整收束过程**：解开全部悬念、了结核心冲突、明确抵达故事圣经结局（story_end）的最终画面；
   - 终章的 ending_hook 字段填「终章收束说明」而非悬念设计；
   - 严禁在终章引入新谜团、新线索或开放式结尾；
   - 若 chapter_count=1，则这唯一一章必须包含起承转合全过程并落到 story_end 结局，不得只写开局。
3. **冲突升级**：冲突必须从口头/日常争执逐步升级到动作/生死/核心利益之争。
4. **情感起伏**：角色情绪不能是一条直线，必须在希望与绝望之间起伏。
5. **章章衔接铁律**：除第 1 章外，每章 summary 的开头必须直接承接上一章 ending_hook 交代过的结局状态——人物所在地点、刚发生的事件、时间线必须连续；严禁出现「上一章结尾已出发/已行动，本章开头却写尚未出发」之类的倒退矛盾；严禁把同一情节节点的多种可能走向写成并列的不同章节——主线大纲只保留一条因果链。
6. **角色设定一致性**：出场角色的姓名、职业、身份、阵营必须与「故事圣经」characters 数组中的设定逐字一致，不得重新发明或改写角色身份（例如圣经中是调查员，大纲中不得写成店主）。
7. **场景完整性**：scene_locations 必须枚举本章实际出现的全部地点（每次换景一处不漏），供背景图规划使用；scene 字段仍填本章最主要的场景。

请严格按以下 JSON 格式返回（键名保持英文，所有值用中文）：
{{
  "overall_arc": "这 {chapter_count} 章的整体剧情弧线摘要（约 100 字）",
  "chapters": [
    {{
      "chapter_index": 1,
      "title": "章节标题（必须抓人，暗示本章高潮）",
      "summary": "详细章节摘要（含起因、发展、高潮、结局，200-300 字）",
      "core_conflict": "本章核心冲突（人物对峙/环境危机/内心挣扎）",
      "key_revelation": "本章揭示的关键信息或埋下的伏笔",
      "characters": ["本章出场的关键角色"],
      "scene_locations": ["具体场景地点"],
      "scene": "本章主场景（单个字符串，如「朝堂大殿」/「夜林」/「城市街道」）",
      "emotion_shift": "主角在本章的情感状态转变（如从满怀希望到跌入谷底）",
      "emotion": "本章主导情绪/氛围（单个短词，如「紧张」/「沉郁」/「希望」/「白天」/「夜晚」/「黄昏」）",
      "ending_hook": "本章结尾的悬念设计",
      "visual_keywords": ["环境光影", "关键道具", "极具画面感的动作或场景描写"]
    }}
  ]
}}

最终检查：chapters 数组必须恰好包含 {chapter_count} 项，chapter_index 必须从 1 连续编号到 {chapter_count}；逐章核对：第 N 章开头与第 N-1 章结尾状态是否衔接、出场角色身份是否与故事圣经一致。"""


def get_revise_outline_prompt(
    current_outline: List[Dict[str, Any]],
    feedback: str,
    story_bible: Dict[str, Any]
) -> str:
    """
    生成修改章节大纲的 Prompt

    当大纲需要根据用户/编辑反馈进行修订时使用。
    核心原则：
    - 精准实施：完全吸收并解决反馈中的问题
    - 蝴蝶效应：修改前文后自动调整后续章节的逻辑连贯性
    - 保留优点：未被批评的优秀部分应保留或优化

    Args:
        current_outline: 当前的章节大纲列表
        feedback: 修订反馈意见
        story_bible: Story Bible JSON 对象

    Returns:
        str: 完整的英文 Prompt，要求 LLM 返回修订后的 JSON 格式大纲

    Example:
        >>> current_outline = [{"chapter_index": 1, "title": "...", ...}]
        >>> feedback = "第一章冲突不够激烈，建议加强"
        >>> prompt = get_revise_outline_prompt(current_outline, feedback, story_bible)
    """
    return f"""你是一位严格而专业的网文编辑。作者提交了一份初版大纲，但客户/总编给出了修订反馈。你需要基于原大纲与反馈重新梳理剧情。

【语言要求（最高优先级）】
- 除 JSON 字段名（如 revision_summary、chapters 等键名）必须保留英文之外，**所有字段内容一律使用简体中文**。

【修订反馈（至关重要）】
{feedback}

【基础设定（故事圣经）】
{json.dumps(story_bible, ensure_ascii=False, indent=2)}

【当前大纲】
{json.dumps(current_outline, ensure_ascii=False, indent=2)}

【修订要求】
1. **精准实施**：你必须完整吸收并解决上方「修订反馈」中提出的问题，不得敷衍。
2. **蝴蝶效应**：若修改了前文章节，你必须审查并自动调整后续章节的逻辑连贯性，确保不出现剧情漏洞（bug）。
3. **保留优点**：未被批评的原大纲优秀部分，应尽量保留或优化。
4. **角色设定一致性**：修订后的出场角色姓名、职业、身份、阵营必须与「故事圣经」characters 数组逐字一致，不得重新发明或改写身份。
5. **章章衔接**：修订后的每章开头必须仍与上一章结尾状态直接衔接，不得因局部修改引入时间线或地点矛盾。

请输出完全修订后的 {len(current_outline)} 章大纲（JSON 格式），结构必须与之前保持一致：
{{
  "revision_summary": "简述你针对反馈做出的主要改动（约 50 字）",
  "chapters": [
    {{
      "chapter_index": 1,
      "title": "...",
      "summary": "...",
      "core_conflict": "...",
      "key_revelation": "...",
      "characters": ["..."],
      "scene_locations": ["..."],
      "scene": "本章主场景（单个字符串）",
      "emotion_shift": "...",
      "emotion": "主导情绪/氛围（单个短词）",
      "ending_hook": "...",
      "visual_keywords": ["..."]
    }}
  ]
}}"""


def get_single_chapter_outline_prompt(
    story_bible: Dict[str, Any],
    existing_outlines: List[Dict[str, Any]],
    user_instruction: str,
    chapter_index: Optional[int] = None,
    reference_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    生成单个章节规划的 Prompt。

    这个 prompt 不生成整本书的章节列表，只为目标章节生成一条 ChapterOutline。
    """
    target_index_text = str(chapter_index) if chapter_index and chapter_index > 0 else "由上下文判断，若无法判断则使用下一章"
    reference_context = reference_context or {}

    return f"""你是一位擅长网文爽感节奏的剧情策划师。
请基于下方「故事圣经」、已有章节规划和用户要求，只生成一个目标章节的规划。

【语言要求（最高优先级）】
- 除 JSON 字段名必须保留英文之外，所有字段内容、标题、摘要一律使用简体中文。

【任务边界】
- 只生成一条章节规划，不要输出整本书的大纲列表。
- 不要重写已有章节规划；你只能根据上下文设计目标章节。
- 如果是分叉章节，应说明它如何从参考章节或参考节点承接出来。

【目标章节】
{target_index_text}

【用户要求】
{user_instruction}

【故事圣经设定】
{json.dumps(story_bible, ensure_ascii=False, indent=2)}

【已有章节规划摘要】
{json.dumps(existing_outlines, ensure_ascii=False, indent=2)}

【参考上下文】
{json.dumps(reference_context, ensure_ascii=False, indent=2)}

【单章规划准则】
1. **承接前文**：必须尊重已有章节事实、人物关系和世界观，不得自相矛盾。
2. **本章闭环**：summary 要包含起因、发展、高潮和本章落点，不要只写一句方向。
3. **钩子法则**：结尾必须有悬念或强烈推动力，不允许平淡收尾。
4. **冲突明确**：conflict 必须是本章核心冲突，可以是人物对峙、环境危机或内心挣扎。
5. **视觉可用**：scene、emotion、visual_keywords 要能指导后续背景、立绘、关键帧和 VNGraph 生成。

请严格按以下 JSON 格式返回：
{{
  "chapter": {{
    "chapter_index": {chapter_index if chapter_index and chapter_index > 0 else 1},
    "title": "章节标题（必须抓人，暗示本章高潮）",
    "summary": "详细章节摘要（含起因、发展、高潮、结局，200-300 字）",
    "core_conflict": "本章核心冲突",
    "conflict": "本章核心冲突",
    "key_revelation": "本章揭示的关键信息或埋下的伏笔",
    "characters": ["本章出场的关键角色"],
    "scene_locations": ["具体场景地点"],
    "scene": "本章主场景（单个字符串）",
    "emotion_shift": "主角在本章的情感状态转变",
    "emotion": "本章主导情绪/氛围（单个短词）",
    "ending_hook": "本章结尾的悬念设计",
    "visual_keywords": ["环境光影", "关键道具", "极具画面感的动作或场景描写"]
  }},
  "planning_notes": "简述为什么这样设计本章",
  "continuity_check": "简述如何承接已有剧情并避免冲突"
}}"""


def _chapter_generation_context_prompt(
    state_snapshot: Dict[str, Any] | None,
    instructions: str | None,
) -> str:
    sections: list[str] = []
    if state_snapshot is not None:
        sections.append(
            "【冻结剧情状态】\n"
            + json.dumps(state_snapshot, ensure_ascii=False, indent=2)
        )
    if instructions:
        sections.append(f"【本次生成要求】\n{instructions}")
    return "\n\n".join(sections)


def get_chapter_content_prompt(
    story_bible: Dict[str, Any],
    chapter_outline: Dict[str, Any],
    previous_chapters: List[Dict[str, Any]],
    word_count_min: int,
    word_count_max: int,
    state_snapshot: Dict[str, Any] | None = None,
    instructions: str | None = None,
    ancestor_outline_summaries: List[Dict[str, Any]] | None = None,
    total_chapters: int | None = None,
) -> str:
    """
    生成章节正文的 Prompt

    基于大纲创作具体的章节内容，遵循大师级写作原则：
    - Show, Don't Tell：展示而非讲述，用细节描写替代情感概括
    - 五感描写：视觉、听觉、嗅觉、触觉增强沉浸感
    - 自然对话：对话需符合角色身份和 voice 设定
    - 拒绝 AI 味：避免总结性、播音腔的表达
    - 节奏控制：紧张场景用短句，情感/环境渲染用长句

    Args:
        story_bible: Story Bible JSON 对象，包含角色设定、世界观等
        chapter_outline: 当前章节的大纲
        previous_chapters: 之前章节的列表（用于生成前情提要，最多取最近3章）
        word_count_min: 最小字数限制
        word_count_max: 最大字数限制

    Returns:
        str: 完整的英文 Prompt，要求 LLM 返回 JSON 格式的章节正文

    Output JSON Structure:
        {
            "chapter_index": 1,
            "title": "章节标题",
            "content": "完整的章节正文内容",
            "word_count_estimate": "预估字数",
            "ending_hook_check": "结尾悬念检查"
        }

    Example:
        >>> prompt = get_chapter_content_prompt(
        ...     story_bible=story_bible,
        ...     chapter_outline={"chapter_index": 1, "title": "初入长安", ...},
        ...     previous_chapters=[],  # 第一章无前情
        ...     word_count_min=2000,
        ...     word_count_max=3000
        ... )
    """
    # 前情提要分两层:
    # 1) 弧线层:前几章的大纲 summary(剧情计划,信息密度高)
    # 2) 落点层:紧邻上一章的结尾原文(续写的直接锚点,兼作文风样本)
    # previous_chapters 各条目的 summary 字段现承载该章结尾切片。
    if ancestor_outline_summaries:
        prev_summary = "\n".join([
            f"[第 {row.get('display_index', i + 1)} 章 {row.get('title', '')}]：{row.get('summary', '')}"
            for i, row in enumerate(ancestor_outline_summaries[-3:])
        ])
    elif previous_chapters:
        prev_summary = "\n".join([
            f"[第 {ch.get('chapter_index', i+1)} 章]：{ch.get('summary', '')[:400]}……"
            for i, ch in enumerate(previous_chapters[-3:])
        ])
    else:
        prev_summary = "（本章为第一章，无前情可参考）"
    previous_ending = ""
    ending_rules = ""
    if previous_chapters:
        last_chapter = previous_chapters[-1]
        if last_chapter.get("kind") == "branch_choice":
            # 分叉伪前章:没有正文结尾,仅用 preview_text 作衔接参照
            previous_ending = f"（本路径自此处分叉，所选剧情走向：{last_chapter.get('summary', '')[:500]}）"
            ending_rules = "- 本章第一段需承接上述分叉走向展开。"
            ending_block = f"""
【分叉衔接参照（本章必须从此处自然衔接）】
{previous_ending}

【衔接铁律】
{ending_rules}"""
        else:
            previous_ending = last_chapter.get("summary", "")
            ending_rules = (
                "- 本章第一段必须承接【上一章结尾原文】的场景与时刻，时间、地点、人物位置必须连续；"
                "- 严禁重写、复述或以概述方式重复上一章已发生的情节；"
                "- 语言风格、叙事密度、引号制式与【上一章结尾原文】保持完全一致。"
            )
            ending_block = f"""
【上一章结尾原文（本章必须从此处自然衔接）】
{previous_ending}

【衔接铁律】
{ending_rules}"""
    else:
        ending_block = ""
    generation_context = _chapter_generation_context_prompt(
        state_snapshot,
        instructions,
    )
    generation_context = f"\n\n{generation_context}" if generation_context else ""

    chapter_position_rule = ""
    if total_chapters and chapter_outline.get("chapter_index"):
        chapter_index = int(chapter_outline.get("chapter_index") or 0)
        if chapter_index >= total_chapters:
            chapter_position_rule = f"""

【收尾铁律（本章是第 {chapter_index}/{total_chapters} 章，即最后一章，优先级高于上方大纲 JSON）】
- 本章必须把故事完整收束：所有未决冲突给出结果，主线抵达故事圣经结局设定的终点。
- 大纲 JSON 中的 ending_hook 字段按悬念设计的内容**一律忽略**，改为按 story_end 结局设计本章结尾。
- 严禁留悬念、留钩子、写「未完待续」「这只是一个开始」式的开放式结尾；最后一段必须是故事的落幕。
- ending_hook_check 字段改写为「终章收束检查」，说明故事如何在本次完整落幕。"""
        else:
            chapter_position_rule = f"""

【章节进度】本章是第 {chapter_index}/{total_chapters} 章（非最后一章）：结尾保持悬念钩子，为后续章节留出推进空间。"""

    ending_banner = ""
    if total_chapters and chapter_outline.get("chapter_index"):
        chapter_index = int(chapter_outline.get("chapter_index") or 0)
        if chapter_index >= total_chapters:
            ending_banner = f"""

⚠️【全局第一铁律——终章收束】本章是第 {chapter_index}/{total_chapters} 章，即全书最后一章。本章正文必须把故事完整收束到故事圣经结局（story_end），所有悬念与冲突全部了结；结尾必须是落幕，严禁任何悬念、钩子、「未完待续」「这只是一个开始」式开放结尾。大纲中的 ending_hook 一律忽略。本条优先级高于本文其余一切规则与大纲。"""

    return f"""你是一位兼具普利策奖得主文学功底与顶级畅销书作家叙事能力的小说家，文笔细腻、画面感强；同时你在为视觉小说（VN）创作演出文本——正文将逐段驱动立绘、表情、配音与场景切换。请基于下方设定与大纲，撰写本章正文。{ending_banner}

【语言要求（最高优先级、铁律）】
- 本章正文（content 字段）**必须全部使用简体中文**撰写。
- 禁止出现任何英文段落、英文句子或英文单词（除专有名词缩写、必要的代码/公式外）。
- 若你输出英文或中英混杂，本次任务视为彻底失败。

【世界观与故事圣经】
{json.dumps(story_bible, ensure_ascii=False, indent=2)}

【本章大纲】
{json.dumps(chapter_outline, ensure_ascii=False, indent=2)}{generation_context}

【前情提要（最近 3 章剧情梗概）】
{prev_summary}{ending_block}{chapter_position_rule}

【核心写作准则（至关重要，严格遵守）】
1. **展示而非讲述（Show, Don't Tell）**：绝对不要干巴巴地概括情绪！不要写「他很害怕」，而要写「他的瞳孔骤缩，指尖不受控制地颤抖，胃里像吞了一块冰」。
2. **五感描写**：加入视觉、听觉、嗅觉、触觉等细节，增强场景沉浸感。
3. **自然对话**：人物对话必须符合其身份与性格，用动作、神态去替代平庸的对话提示词「说」。**特别提醒：每个角色的对话风格必须参照其 voice 字段描述，保持语气、用词、句式的一致性。**
4. **拒绝 AI 味**：严禁使用「总而言之」「在这个充满未知的世界里」「最后，让我们拭目以待」这类总结性、播音腔的表述。严禁说教。
5. **节奏控制**：战斗/紧张场景用短句，情感/环境渲染用长句。
6. **章节厚度**：本章必须有完整的场景推进、动作细节、心理/感官描写和自然对话。不要只写剧情摘要，不要用概括句替代具体桥段。
7. **字数限制**：content 字段正文目标为 {word_count_min} 到 {word_count_max} 个中文字符（不含空白）。内容必须扎实，严禁敷衍缩水；低于 {word_count_min} 字视为失败。
8. **对白密度（硬指标）**：每 500 字至少 3 句直接引语对白（用「」或引号完整标出）；主角在本章必须有台词；关键信息揭示与情节转折优先通过人物对话完成，而非主角内心独白；对白与动作、环境描写交替推进，严禁连续 3 段以上无对白的纯叙述。
9. **登场描写**：每个角色首次进入场景时，必须有明确的登场动作描写（如「推门走进」「从阴影中现身」「抬头看见某人站在礁石上」），供演出系统识别入场时机；只有明确写出的登场才能被搬上舞台。

请返回以下 JSON 格式（content 必须是简体中文正文）：
{{
  "chapter_index": {chapter_outline.get('chapter_index', 1)},
  "title": "{chapter_outline.get('title', '')}",
  "content": "在此输出本章完整正文，确保段落清晰、标点规范、排版美观。必须为简体中文。",
  "word_count_estimate": "正文的预估字数",
  "ending_hook_check": "简述本章结尾如何钩住读者（钩子检查）"
}}"""

def get_rewrite_chapter_content_prompt(
    story_bible: dict[str, Any],
    chapter_outline: dict[str, Any],
    previous_chapters: list[dict[str, Any]],
    draft_result: dict[str, Any],
    quality_reasons: list[str],
    flavor_issues: list[str],
    rewrite_hint: str,
    word_count_min: int,
    word_count_max: int,
) -> str:
    """生成章节正文质量修订 Prompt。

    与短文扩写不同，本 Prompt 要求模型保留草稿的剧情事实、事件顺序和
    结尾钩子，只针对规则校验与 AI 味评审指出的问题进行完整修订。
    """
    prev_summary = "\n".join([
        f"[第 {ch.get('chapter_index', i + 1)} 章]：{ch.get('summary', '')[:150]}……"
        for i, ch in enumerate(previous_chapters[-3:])
    ]) if previous_chapters else "（本章为第一章，无前情可参考）"

    quality_feedback = quality_reasons or []
    flavor_feedback = flavor_issues or []
    rewrite_instruction = (rewrite_hint or "").strip() or "无额外改写提示；请根据列出的问题完成修订。"
    anti_ai_examples = "、".join(f"「{item}」" for item in ANTI_AI_NEGATIVE_LIST)

    return f"""你是一位专业的中文小说责任编辑。下面的章节草稿已经完成，但没有完全通过质量评审。请在保留剧情事实与章节功能的前提下，重写为一版完整、自然、可直接发布的正文。

【修订目标】
- 修订现有草稿，不要另写一个不同故事。
- 保留核心事件、事件顺序、人物关系、关键信息和结尾钩子的功能。
- 保持与故事圣经、本章大纲和前情一致，不得新增冲突设定。
- 针对评审指出的问题重写相关句子和段落，不要只替换个别词语或机械删除命中词。
- 修订后的 content 目标为 {word_count_min} 到 {word_count_max} 个中文字符（不含空白）。

【硬性要求】
- content 字段必须全部使用简体中文。
- chapter_index 与 title 必须保持为本章大纲指定的值。
- 输出必须是完整章节正文，不能只输出修改片段、修改说明、差异列表或点评。
- 修订不得降低对白密度：保持每 500 字至少 3 句直接引语对白，主角保持有台词。
- 使用具体动作、环境、对话和感官细节呈现情绪，避免直接概括人物感受。
- 避免套话、模板句、排比灌水、形容词堆叠、空泛总结和说教。
- 不要为了规避评审而用近义套话替换原词；必须改善句子结构、叙述节奏和具体性。
- 常见风险表达包括：{anti_ai_examples}。这些只是风险示例，应结合上下文判断并自然重写。
- 严禁英文段落；JSON 字段名除外。

【规则质量检查发现的问题】
{json.dumps(quality_feedback, ensure_ascii=False, indent=2)}

【AI 味评审发现的问题】
{json.dumps(flavor_feedback, ensure_ascii=False, indent=2)}

【评审给出的具体改写建议】
{rewrite_instruction}

【世界观与故事圣经】
{json.dumps(story_bible, ensure_ascii=False, indent=2)}

【本章大纲】
{json.dumps(chapter_outline, ensure_ascii=False, indent=2)}

【前情提要（最近 3 章）】
{prev_summary}

【待修订草稿】
{json.dumps(draft_result, ensure_ascii=False, indent=2)}

请只返回以下 JSON 结构，不要添加 Markdown 代码块或额外说明：
{{
  "chapter_index": {chapter_outline.get('chapter_index', 1)},
  "title": {json.dumps(str(chapter_outline.get('title', '')), ensure_ascii=False)},
  "content": "在此输出修订后的完整简体中文正文，目标 {word_count_min}-{word_count_max} 个中文字符。",
  "ending_hook": "简述修订后保留或强化的本章结尾钩子",
  "appearing_characters": ["本章实际出场角色"],
  "main_scene": {json.dumps(str(chapter_outline.get('scene', '')), ensure_ascii=False)},
  "emotion": {json.dumps(str(chapter_outline.get('emotion', '')), ensure_ascii=False)}
}}"""

def get_expand_chapter_content_prompt(
    story_bible: Dict[str, Any],
    chapter_outline: Dict[str, Any],
    previous_chapters: List[Dict[str, Any]],
    draft_result: Dict[str, Any],
    word_count_min: int,
    word_count_max: int,
    current_length: int,
    state_snapshot: Dict[str, Any] | None = None,
    instructions: str | None = None,
    total_chapters: int | None = None,
) -> str:
    """生成章节正文短文补救 Prompt。"""
    prev_summary = "\n".join([
        f"[第 {ch.get('chapter_index', i+1)} 章]：{ch.get('summary', '')[:150]}……"
        for i, ch in enumerate(previous_chapters[-3:])
    ]) if previous_chapters else "（本章为第一章，无前情可参考）"
    generation_context = _chapter_generation_context_prompt(
        state_snapshot,
        instructions,
    )
    generation_context = f"\n\n{generation_context}" if generation_context else ""

    ending_rule = ""
    if total_chapters and chapter_outline.get("chapter_index"):
        chapter_index = int(chapter_outline.get("chapter_index") or 0)
        if chapter_index >= total_chapters:
            ending_rule = f"""
【收尾铁律（本章是第 {chapter_index}/{total_chapters} 章，即最后一章，优先级最高）】
- 扩写后必须收束整个故事：冲突全部了结，主线抵达故事圣经结局设定的终点。
- 忽略大纲 ending_hook 与原草稿结尾的悬念，改写为落幕式结尾；严禁「未完待续」「这只是一个开始」。
"""
        else:
            ending_rule = "\n（本章非最后一章，扩写时保留结尾钩子。）"

    return f"""你刚才生成的章节正文过短：当前 content 去掉空白后约 {current_length} 个中文字符，低于最低要求 {word_count_min}。

请基于下方设定、大纲和当前草稿，重写并扩充本章正文。目标是 {word_count_min} 到 {word_count_max} 个中文字符（不含空白）。

【硬性要求】
- content 字段必须全部使用简体中文。
- 保留原草稿中的核心事件、角色关系和结尾设计，但要把摘要式叙述扩写成具体场景。{ending_rule}
- 必须增加场景推进、人物动作、心理细节、五感描写和自然对话；对白密度不低于每 500 字 3 句直接引语，主角必须有台词，严禁扩写成无对白的独白文。
- 角色首次进入场景时补上明确的登场动作描写。
- 不要只在原文末尾追加总结或说明；请输出一版完整、连贯、可直接阅读的新正文。
- 严禁英文段落、说教总结、AI 味套话和空泛概括。

【世界观与故事圣经】
{json.dumps(story_bible, ensure_ascii=False, indent=2)}

【本章大纲】
{json.dumps(chapter_outline, ensure_ascii=False, indent=2)}{generation_context}

【前情提要（最近 3 章）】
{prev_summary}

【当前短草稿】
{json.dumps(draft_result, ensure_ascii=False, indent=2)}

请返回以下 JSON 格式：
{{
  "chapter_index": {chapter_outline.get('chapter_index', 1)},
  "title": "{chapter_outline.get('title', '')}",
  "content": "在此输出扩充后的完整正文，目标 {word_count_min}-{word_count_max} 个中文字符。",
  "ending_hook": "简述本章结尾钩子",
  "appearing_characters": ["本章出场角色"],
  "main_scene": "{chapter_outline.get('scene', '')}",
  "emotion": "{chapter_outline.get('emotion', '')}"
}}"""


def get_asset_prompt_prompt(
    story_bible: Dict[str, Any],
    chapter_content: str,
    chapter_outline: Dict[str, Any],
    genre: Optional[str] = None
) -> str:
    """
    生成视觉素材 Prompt 的 Prompt

    从章节内容中提取并生成 AI 绘画工具（如 Stable Diffusion、Midjourney）
    所需的英文提示词。生成的 prompt 遵循特定的结构公式：

    公式: (质量标签), [主体细节], [服装/姿态/表情], [环境/背景],
          [光线/氛围], [镜头语言/构图], [艺术风格/渲染引擎]

    Args:
        story_bible: Story Bible JSON 对象，包含角色设定
        chapter_content: 当前章节的正文内容（截取前 2000 字符）
        chapter_outline: 当前章节的大纲
        genre: 指定画风类型（可选），如不指定则自动检测
               可选值: historical, modern, sci-fi, fantasy, anime, realistic, oil_painting, watercolor, sketch

    Returns:
        str: 完整的英文 Prompt，要求 LLM 返回 JSON 格式的素材提示词

    Output JSON Structure:
        {
            "character_prompts": [
                {
                    "name": "角色名",
                    "description_cn": "中文外貌总结",
                    "sd_prompt": "英文 AI 绘画 prompt"
                }
            ],
            "background_prompts": [
                {
                    "scene": "场景名",
                    "description_cn": "中文场景描述",
                    "sd_prompt": "英文 AI 绘画 prompt"
                }
            ],
            "keyframe_prompts": [
                {
                    "event_name": "关键时刻名称",
                    "description_cn": "中文视觉描述",
                    "sd_prompt": "英文 AI 绘画 prompt"
                }
            ]
        }

    Generation Rules:
        1. Language: 所有绘画 prompt 必须是英文，关键词用逗号分隔
        2. Formula: 遵循结构公式组织 prompt
        3. Consistent Art Style: 所有 prompt 末尾统一风格限制词
        4. Transparent Background: 立绘类 prompt 必须包含透明背景关键词

    Example:
        >>> prompt = get_asset_prompt_prompt(
        ...     story_bible=story_bible,
        ...     chapter_content="李长风站在城墙上...",
        ...     chapter_outline={"title": "初入长安", ...}
        ... )
        >>> # LLM 会返回角色立绘、背景、关键帧的英文 prompt
    """
    # 构建画风设置文本
    genre_text = f"**指定画风**: {genre}（请使用此画风生成所有提示词）" if genre else "**自动检测**: 请根据故事内容自动选择最合适的画风"
    style_source_text = "指定画风" if genre else "检测到的画风"

    return f"""你是一位精通 Stable Diffusion 和 Midjourney 提示词工程的 AI 视觉艺术总监。
请根据最新章节内容，提取并生成用于 AI 图像生成的英文提示词。

[故事圣经参考]
{json.dumps(story_bible, ensure_ascii=False, indent=2)}

[当前章节内容（节选）]
{chapter_content[:2000]}...

[画风设置]
{genre_text}

[可选画风列表]
- historical: 历史古风 - 标签: Chinese historical style, hanfu, traditional Chinese painting, ink wash style
- modern: 现代都市 - 标签: modern realistic style, contemporary fashion, photorealistic
- sci-fi: 科幻未来 - 标签: sci-fi style, futuristic technology, cyberpunk, neon lighting
- fantasy: 玄幻奇幻 - 标签: fantasy art, magical atmosphere, ethereal lighting, mystical
- anime: 日式动漫 - 标签: anime style, Japanese animation, manga style, cel shading
- realistic: 写实风格 - 标签: realistic digital painting, photorealistic, cinematic
- oil_painting: 油画风格 - 标签: oil painting style, classical art, rich textures
- watercolor: 水彩风格 - 标签: watercolor painting, soft edges, pastel colors, artistic
- sketch: 手绘素描 - 标签: pencil sketch, hand drawn, graphite, rough lines

[生成规则（至关重要）]
1. **语言**：所有绘画提示词必须是**英文**，关键词用逗号分隔。
2. **公式**：遵循以下结构：`(质量标签), [主体细节], [服装/姿态/表情], [环境/背景], [光线/氛围], [镜头语言/构图], [艺术风格标签], [渲染引擎]`
3. **质量标签必填**：每个 prompt 开头必须包含 `(masterpiece, best quality:1.4), ultra detailed, 8k resolution`
4. **画风标签必填**：每个 prompt 结尾必须根据{style_source_text}添加对应的风格标签。
5. **渲染引擎**：根据画风选择合适的渲染提示词：
   - 写实/科幻: `cinematic lighting, volumetric lighting, ray tracing, unreal engine 5`
   - 动漫/古风: `cel shading, crisp line art, detailed character design, visual novel style`
   - 油画/水彩: `traditional art, painterly, artistic brushwork`
6. **透明背景（立绘/角色图）**：立绘类提示词必须明确包含 `fully transparent RGBA background`，禁止同时写白底、纯色底或任何场景背景。
7. **禁止水印文字**：所有 prompt 必须包含 `no text, no watermark, no signature, no logo`
8. **背景图禁止人物**：所有背景图（background_prompts）的 sd_prompt 中**绝对不能出现任何人物、角色、生物**。必须包含 `no humans, no characters, no people, no animals, empty scene` 等关键词来确保画面纯净。背景图只展示场景环境、建筑、自然景观等，不允许有任何生命体出现。

请返回以下 JSON 格式：
{{
  "detected_genre": "使用的画风类型（historical/modern/sci-fi/fantasy/anime/realistic/oil_painting/watercolor/sketch）",
  "character_prompts": [
    {{
      "name": "角色名称",
      "description_cn": "角色外貌与气质的中文总结",
      "emotion": "表情类型（neutral/smile/happy/angry/sad/cry/surprise/fear）",
      "outfit": "服装类型（default/armor/formal/mourning/injured）",
      "pose": "姿态类型（standing/pointing/combat/kneeling/reaching/defensive）",
      "sd_prompt": "(masterpiece, best quality:1.4), ultra detailed, 1boy/1girl, solo, [年龄], [发色与发型], [瞳色], [服装细节], [细腻表情], [自然非对称姿态与可信重心], fully transparent RGBA background, isolated visual novel character sprite, relaxed shoulders and hands, [风格标签], [渲染引擎], no rigid symmetrical pose, no character sheet, no text, no watermark"
    }}
  ],
  "background_prompts": [
    {{
      "scene": "场景名称",
      "description_cn": "场景的中文描述",
      "mood": "氛围类型（default/day/dusk/night/dawn/rain/snow/fog）",
      "sd_prompt": "(masterpiece, best quality:1.4), ultra detailed, 8k resolution, scenery, landscape, empty scene, no humans, no characters, no people, no animals, [建筑/景观细节], [时间/天气], [光线细节], [色调], cinematic composition, wide angle, [风格标签], [渲染引擎], no text, no watermark"
    }}
  ],
  "keyframe_prompts": [
    {{
      "event_name": "核心戏剧时刻（如主角拔剑、双人对峙）",
      "description_cn": "该时刻的中文视觉描述",
      "emotion": "整体情绪（intense/calm/sad/happy）",
      "sd_prompt": "(masterpiece, best quality:1.4), ultra detailed, 8k resolution, [角色互动], [动态姿态], [激烈表情], [具体动作], background of [场景], dramatic lighting, depth of field, action shot, [风格标签], [渲染引擎], no text, no watermark"
    }}
  ]
}}"""
