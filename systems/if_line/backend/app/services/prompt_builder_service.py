"""
Prompt 构建服务 - 根据 StoryBible 和 ChapterOutline 自动构建图像生成 prompt
"""
import asyncio
import hashlib
import logging
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from app.services.image_generation_service import image_generation_service
from app.services.visual_style_profile_service import (
    VisualStyleProfile,
    visual_style_profile_service,
)
from app.services.character_visual_profile_service import (
    normalize_character,
    normalize_story_bible,
)
from app.services.keyframe_character_binding import (
    KeyframeCharacterBinding,
    binding_from_contract,
)
from app.services.character_identity_contract import CharacterIdentityContract
from app.services.canonical_identity_service import canonical_identity_prompt
from app.services.character_age_contract import (
    age_contract_from_profile,
    apply_age_contract_to_appearance,
    apply_age_contract_to_character,
    identity_variant_id,
)
from app.services.source_visual_profile_service import source_visual_profile_service
from app.services.portrait_prompt_identity_service import build_portrait_rewriter_identity

if TYPE_CHECKING:
    from app.schemas import BackgroundSceneSpec

logger = logging.getLogger("prompt_builder")


class PromptBuilderService:
    """Prompt 构建服务"""

    BACKGROUND_UNSAFE_VISUAL_KEYWORDS = [
        "人", "人物", "人群", "脸", "人脸", "面孔", "肖像", "照片", "合照", "群像",
        "背影", "身影", "剪影", "眼睛", "表情", "显示器映出", "屏幕映出", "镜子映出",
        "person", "people", "human", "face", "portrait", "photo", "group photo",
        "silhouette", "figure", "reflection of",
    ]

    def generate_seed(
        self,
        character_id: str,
        emotion: str = "neutral",
        style_fingerprint: str = "",
    ) -> int:
        """生成固定 seed。

        CogView-4 当前不接收 seed 参数，这个值主要用于缓存和资产契约记录。
        为降低同一角色不同表情的身份漂移，项目风格可用时不再把 emotion
        放进随机源。
        """
        data = f"{character_id}|{style_fingerprint or 'legacy'}"
        hash_val = hashlib.md5(data.encode("utf-8")).hexdigest()
        return int(hash_val[:8], 16)

    def generate_character_id(self, name: str, project_id: int,
                              resolver: Any = None) -> str:
        """生成角色唯一 ID。

        优先用注入的 ``CanonicalCharacterResolver``（支持 alias 归并）；
        未注入时降级为 ``md5(name|project_id)[:12]``，与历史行为完全一致。
        """
        if resolver is not None:
            try:
                return resolver.resolve(name).character_id
            except Exception:
                pass
        data = f"{name}|{project_id}"
        return hashlib.md5(data.encode("utf-8")).hexdigest()[:12]

    def build_portrait_prompts(
        self,
        story_bible: Dict[str, Any],
        project_id: int,
        variations: Optional[List[Dict[str, Any]]] = None,
        visual_style_profile: Optional[VisualStyleProfile] = None,
        project_style: str = "",
        project_title: str = "",
    ) -> List[Dict[str, Any]]:
        """
        从 StoryBible 提取角色设定，生成所有立绘 prompt

        Args:
            story_bible: Story Bible JSON
            project_id: 项目 ID
            variations: 变体配置 [{"emotion": "happy", "outfit": "casual"}, ...]

        Returns:
            [
                {
                    "character_id": "abc123",
                    "character_name": "张三",
                    "appearance_prompt": "...",
                    "emotion": "neutral",
                    "outfit": "default",
                    "pose": "standing",
                    "genre": "historical",
                    "seed": 12345
                },
                ...
            ]
        """
        prompts = []
        characters = story_bible.get("characters", [])
        style_profile = visual_style_profile or visual_style_profile_service.build_profile(
            story_bible,
            project_style=project_style,
            project_title=project_title,
        )

        # A11 — 默认变体：5 个（neutral/happy/angry/sad/surprise），覆盖核心情绪光谱
        if not variations:
            variations = [
                {"emotion": "neutral", "outfit": "default", "pose": "standing"},
                {"emotion": "happy", "outfit": "default", "pose": "standing"},
                {"emotion": "angry", "outfit": "default", "pose": "combat"},
                {"emotion": "sad", "outfit": "mourning", "pose": "standing"},
                {"emotion": "surprise", "outfit": "default", "pose": "reaching"},
            ]

        for char in characters:
            if isinstance(char, str):
                char = {"name": char}
            if not isinstance(char, dict):
                continue
            char = normalize_character(
                char,
                context_text=" ".join((
                    str(story_bible.get("worldview") or ""),
                    str(story_bible.get("style_rules") or ""),
                )),
            )
            char_name = char.get("name", "unknown")
            char_id = self.generate_character_id(char_name, project_id)
            appearance = source_visual_profile_service.effective_character_appearance(
                char,
                style_profile.source_profile_id,
                self._extract_appearance(char),
                source_work=str(story_bible.get("source_work") or ""),
                worldview=str(story_bible.get("worldview") or ""),
                style_rules=str(story_bible.get("style_rules") or ""),
            )
            gender = self._extract_gender(char)
            gender_prompt = self.build_gender_prompt(gender)
            genre = style_profile.project_genre or self._extract_genre_from_character(char, story_bible)
            for var in variations:
                emotion = var.get("emotion", "neutral")
                outfit = var.get("outfit", "default")
                pose = var.get("pose", "standing")
                requested_age = var.get("age_group") or var.get("age_stage")
                age_contract = age_contract_from_profile(
                    char.get("visual_profile"),
                    override=requested_age,
                    age_source="variation_request",
                    source_excerpt=str(var.get("age_source_excerpt") or ""),
                )
                variant_char = apply_age_contract_to_character(char, age_contract)
                variant_id = identity_variant_id(char_id, age_contract.age_group)
                variant_appearance = apply_age_contract_to_appearance(appearance, age_contract)
                variant_anchor = visual_style_profile_service.character_visual_anchor(
                    char_name,
                    variant_appearance,
                    gender,
                )

                seed = self.generate_seed(
                    variant_id,
                    emotion,
                    style_fingerprint=style_profile.fingerprint,
                )

                prompts.append({
                    "character_id": char_id,
                    "character_name": char_name,
                    "appearance_prompt": variant_appearance,
                    "character_visual_profile": variant_char.get("visual_profile"),
                    "visual_description_cn": variant_char.get("visual_description_cn"),
                    "visual_fingerprint": variant_char.get("visual_fingerprint"),
                    "canonical_identity": variant_char.get("canonical_identity"),
                    "canonical_identity_fingerprint": variant_char.get("canonical_identity_fingerprint"),
                    "character_visual_anchor": variant_anchor,
                    "identity_variant_id": variant_id,
                    "age_group": age_contract.age_group,
                    "age_contract": age_contract.to_dict(),
                    "gender": gender,
                    "gender_prompt": gender_prompt,
                    "emotion": emotion,
                    "outfit": outfit,
                    "pose": pose,
                    "genre": genre,
                    "project_genre": style_profile.project_genre,
                    "visual_style_profile": style_profile.to_dict(),
                    "visual_style_prompt": style_profile.portrait_prompt_en,
                    "style_fingerprint": style_profile.fingerprint,
                    "seed": seed
                })

        return prompts

    def _extract_appearance(self, character: Dict[str, Any]) -> str:
        """Extract the generic or legacy appearance fallback.

        Source-character generation resolves ``canonical_identity`` before
        calling this helper.  ``visual_profile`` is authoritative only for
        this gallery-compatible fallback, and legacy cards are normalized on
        demand so old projects keep working without a migration gate.
        """
        if isinstance(character, dict) and character.get("visual_profile"):
            return str(normalize_character(character).get("visual_prompt_en") or "")
        appearance = character.get("appearance", "")
        # 如果已有英文描述，直接使用
        if appearance and not self._is_chinese(appearance):
            return appearance

        # 否则需要翻译或使用默认描述
        # 这里简化处理，实际项目中可以调用翻译 API
        return appearance

    def _extract_gender(self, character: Dict[str, Any]) -> str:
        """从角色卡显式字段优先抽取性别；缺失时用外貌/身份关键词弱推断。"""
        if not isinstance(character, dict):
            return ""

        for key in ("gender", "sex", "性别"):
            gender = self.normalize_gender(character.get(key))
            if gender:
                return gender

        for key in ("role", "identity", "appearance", "description"):
            gender = self.normalize_gender(character.get(key))
            if gender:
                return gender

        biographical_text = " ".join(
            str(character.get(k) or "")
            for k in (
                "internal_conflict",
                "personality",
                "motivation",
                "motivation_and_goal",
                "background",
                "voice",
            )
        )
        gender = self._infer_gender_from_biographical_text(biographical_text)
        if gender:
            return gender

        return self._infer_gender_from_name(character.get("name") or character.get("name_cn") or "")

    def enrich_story_bible_genders(
        self,
        story_bible: Dict[str, Any],
        context_text: str = "",
    ) -> Dict[str, Any]:
        """给 StoryBible.characters 补齐规范化 gender 字段。

        旧版 Story Bible 模板没有要求 LLM 输出 gender，前端和素材链路会把缺字段显示
        为“未设定”。这里做非破坏性兜底：显式字段优先，其次角色卡文本/姓名，最后用
        正文邻近代词上下文推断。无法判断时保持缺失，不硬猜。
        """
        if not isinstance(story_bible, dict):
            return story_bible

        out = dict(story_bible)
        out["characters"] = self.ensure_character_genders(
            out.get("characters") or [],
            context_text=context_text,
        )
        # Character visuals are normalized in the same compatibility choke
        # point used by every Bible creation/read path.  This guarantees that
        # generated and legacy cards expose one common contract.
        return normalize_story_bible(out, context_text=context_text)

    def ensure_character_genders(
        self,
        characters: List[Any],
        context_text: str = "",
    ) -> List[Dict[str, Any]]:
        """规范化角色卡列表，补齐可推断的 ``gender`` 字段。"""
        enriched: List[Dict[str, Any]] = []
        for raw in characters or []:
            if isinstance(raw, str):
                char: Dict[str, Any] = {"name": raw}
            elif isinstance(raw, dict):
                char = dict(raw)
            else:
                continue

            gender = self._extract_gender(char)
            if not gender:
                name = char.get("name") or char.get("name_cn") or ""
                gender = self._infer_gender_from_context(name, context_text)

            if gender:
                char["gender"] = gender
            enriched.append(char)
        return enriched

    def normalize_gender(self, value: Any) -> str:
        """归一化性别字段，返回 male/female/空串。"""
        if value is None:
            return ""
        s = str(value).strip().lower()
        if not s:
            return ""
        compact = (
            s.replace(" ", "")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
        )

        # 先判 female，避免 "female" / "woman" 被 male/man 子串误伤。
        female_hits = {
            "female", "woman", "women", "girl", "feminine", "f",
            "女", "女性", "女人", "女子", "少女", "女孩", "女主", "女主角",
            "女儿", "母亲", "妈妈", "母", "妻子", "姐姐", "妹妹", "姐妹",
            "阿姨", "姨妈", "姑姑", "婶婶", "奶奶", "外婆", "婆婆",
            "女班长", "女生", "她", "她的",
        }
        male_hits = {
            "male", "man", "men", "boy", "masculine", "m",
            "男", "男性", "男人", "男子", "少年", "青年", "男孩", "男主", "男主角",
            "父亲", "爸爸", "父", "丈夫", "哥哥", "弟弟", "兄弟", "儿子",
            "独子", "叔叔", "伯父", "舅舅", "爷爷", "外公", "男生",
            "他", "他的",
        }
        if compact in female_hits or any(hit in compact for hit in female_hits if len(hit) > 1):
            return "female"
        if compact in male_hits or any(hit in compact for hit in male_hits if len(hit) > 1):
            return "male"
        return ""

    def _infer_gender_from_name(self, name: Any) -> str:
        """用中文姓名/称谓做最后一层弱推断。"""
        if not name:
            return ""
        s = str(name).strip()
        if not s:
            return ""

        title_gender = self.normalize_gender(s)
        if title_gender:
            return title_gender

        female_name_chars = set("婉娟娜婷静丽芳美慧燕莉怡萍雯倩玲琳妍媛莲梅珊琪霞")
        male_name_chars = set("志远建国伟强军勇刚磊杰斌超峰宇轩明飞龙亮东勋豪雄忠义")
        if any(ch in s for ch in female_name_chars):
            return "female"
        if any(ch in s for ch in male_name_chars):
            return "male"
        return ""

    def _infer_gender_from_biographical_text(self, text: str) -> str:
        """从长段角色说明中只识别强自指线索，避免把亲属描述误当本人性别。"""
        if not text:
            return ""
        s = str(text)
        female_markers = ("独女", "女儿", "女生", "女孩", "少女", "她", "她的")
        male_markers = ("独子", "儿子", "男生", "男孩", "少年", "青年", "他", "他的")
        female_score = sum(s.count(marker) for marker in female_markers)
        male_score = sum(s.count(marker) for marker in male_markers)
        if female_score > male_score:
            return "female"
        if male_score > female_score:
            return "male"
        return ""

    def _infer_gender_from_context(self, name: Any, context_text: str) -> str:
        """从正文中角色名邻近的中文代词推断性别。"""
        if not name or not context_text:
            return ""

        needle = str(name).strip()
        text = str(context_text)
        if not needle or needle not in text:
            return ""

        male_score = 0
        female_score = 0
        start = 0
        checked = 0
        while checked < 12:
            idx = text.find(needle, start)
            if idx < 0:
                break
            window = text[idx:idx + 160]
            male_score += window.count("他") + window.count("他的")
            female_score += window.count("她") + window.count("她的")
            start = idx + len(needle)
            checked += 1

        if male_score > female_score:
            return "male"
        if female_score > male_score:
            return "female"
        return ""

    def build_gender_prompt(self, gender: str) -> str:
        """把归一化性别转成图像模型能稳定理解的英文视觉锚点。"""
        normalized = self.normalize_gender(gender)
        if normalized == "male":
            return "clearly male character, masculine facial structure and body silhouette, not feminine"
        if normalized == "female":
            return "clearly female character, feminine facial structure and body silhouette, not masculine"
        return ""

    def _is_chinese(self, text: str) -> bool:
        """判断是否包含中文"""
        for char in text:
            if '一' <= char <= '鿿':
                return True
        return False

    def _extract_genre_from_character(
        self,
        character: Dict[str, Any],
        story_bible: Dict[str, Any]
    ) -> str:
        """从角色和 StoryBible 推断题材风格"""
        worldview = story_bible.get("worldview", "")
        style = story_bible.get("style_rules", "")

        # 合并相关文本进行推断
        profile = character.get("visual_profile") if isinstance(character, dict) else None
        if isinstance(profile, dict) and profile.get("world_style"):
            return str(profile["world_style"])
        text_to_analyze = f"{worldview} {style} {character.get('appearance', '')}"
        return image_generation_service.infer_genre(text_to_analyze)

    def build_background_prompts(
        self,
        chapter_outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        moods: Optional[List[str]] = None,
        visual_style_profile: Optional[VisualStyleProfile] = None,
        project_style: str = "",
        project_title: str = "",
    ) -> List[Dict[str, Any]]:
        """
        从章节大纲提取场景，生成背景 prompt

        Args:
            chapter_outline: 章节大纲 JSON 或对象
            story_bible: Story Bible JSON
            moods: 氛围列表，默认 ["day"]

        Returns:
            [
                {
                    "scene_name": "古城集市",
                    "scene_description": "...",
                    "mood": "day",
                    "genre": "historical"
                },
                ...
            ]
        """
        if not moods:
            moods = ["day"]

        prompts = []
        style_profile = visual_style_profile or visual_style_profile_service.build_profile(
            story_bible,
            project_style=project_style,
            project_title=project_title,
        )

        # 提取场景地点
        scene = chapter_outline.get("scene", "")

        # 如果没有明确场景，尝试从视觉关键词或摘要提取
        if not scene:
            visual_keywords = chapter_outline.get("visual_keywords", [])
            if visual_keywords:
                if isinstance(visual_keywords, list):
                    scene = visual_keywords[0] if visual_keywords else ""
                else:
                    scene = str(visual_keywords)

            # 如果还是没有，尝试从 summary 提取地点关键词
            summary = chapter_outline.get("summary", "")
            if not scene and summary:
                # 提取可能的地点关键词
                location_keywords = ["帐中", "帐外", "营帐", "战场", "城墙", "城门",
                                     "宫殿", "府邸", "街道", "山", "河", "原", "谷"]
                for kw in location_keywords:
                    if kw in summary:
                        scene = kw
                        break

        if not scene:
            return prompts

        # 提取视觉关键词
        visual_keywords = chapter_outline.get("visual_keywords", [])
        if isinstance(visual_keywords, list):
            visual_text = ", ".join(visual_keywords)
        else:
            visual_text = str(visual_keywords)

        # 构建场景描述
        scene_description = scene
        if visual_text:
            scene_description = f"{scene}, {visual_text}"

        # 使用项目级风格，避免同一项目不同章节按 scene 各自漂移。
        genre = style_profile.project_genre

        for mood in moods:
            prompts.append({
                "scene_name": scene,
                "scene_description": scene_description,
                "mood": mood,
                "genre": genre,
                "project_genre": style_profile.project_genre,
                "visual_style_profile": style_profile.to_dict(),
                "visual_style_prompt": style_profile.background_prompt_zh,
                "style_fingerprint": style_profile.fingerprint,
            })

        return prompts

    def build_keyframe_prompts(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        max_keyframes: int = 3,
        visual_style_profile: Optional[VisualStyleProfile] = None,
        project_style: str = "",
        project_title: str = "",
    ) -> List[Dict[str, Any]]:
        """
        从章节内容提取关键时刻，生成关键帧 prompt

        Args:
            chapter_content: 章节正文内容
            chapter_outline: 章节大纲
            story_bible: Story Bible JSON
            max_keyframes: 最大关键帧数量

        Returns:
            [
                {
                    "event_name": "主角拔剑",
                    "scene_description": "...",
                    "characters": [...],
                    "action": "drawing sword",
                    "emotion": "intense",
                    "genre": "historical"
                },
                ...
            ]
        """
        prompts = []
        style_profile = visual_style_profile or visual_style_profile_service.build_profile(
            story_bible,
            project_style=project_style,
            project_title=project_title,
        )

        # 从大纲提取关键事件
        conflict = chapter_outline.get("conflict", "")
        scene = chapter_outline.get("scene", "")
        emotion = chapter_outline.get("emotion", "neutral")
        title = chapter_outline.get("title", "")
        summary = chapter_outline.get("summary", "")
        visual_keywords = chapter_outline.get("visual_keywords", [])

        # 如果没有明确场景，从视觉关键词提取
        if not scene:
            if visual_keywords:
                if isinstance(visual_keywords, list):
                    scene = visual_keywords[0] if visual_keywords else ""
                else:
                    scene = str(visual_keywords)

        # 使用项目级风格，避免 keyframe 与立绘/背景分裂。
        genre = style_profile.project_genre

        # 从冲突或摘要提取动作
        action_source = conflict if conflict else summary
        action = self._extract_action_from_conflict(action_source)

        # 提取角色（B02 — 现在拿到 list[KeyframeCharacterBinding]）
        character_bindings = self._extract_characters_for_keyframe(chapter_outline, story_bible)
        # 兼容 dict：保留 name/appearance 给旧 rewriter，同时暴露 binding 字段
        characters = [
            {
                "name": b.character_name,
                "appearance": b.identity_prompt or b.identity_anchor,
                # B02 新增字段，下游 rewriter / asset_management_service 可以读到
                "character_id": b.character_id,
                "visual_fingerprint": b.visual_fingerprint,
                "identity_contract_version": b.identity_contract_version,
                "identity_prompt": b.identity_prompt,
                "identity_anchor": b.identity_anchor,
                "gender_prompt": b.gender_prompt,
                "signature_features": list(b.signature_features),
                "accessories": list(b.accessories),
                "canonical_outfit": dict(b.canonical_outfit),
                "reference_asset_id": b.reference_asset_id,
                "reference_image_url": b.reference_image_url,
                "reference_image_sha256": b.reference_image_sha256,
                "has_identity_reference": b.has_reference(),
                # B05 — 把整个 binding 也带上，给上游直接构造 rewriter payload
                "binding": b,
            }
            for b in character_bindings
        ]

        # 生成关键帧 - 放宽条件，只要有标题或摘要就可以生成
        event_name = title if title else "关键场景"
        scene_description = scene if scene else (visual_keywords[0] if visual_keywords and isinstance(visual_keywords, list) else summary[:50] if summary else "")

        if scene_description:  # 只要有描述就生成
            identity_lock = self.build_character_identity_lock(character_bindings)
            prompts.append({
                "event_name": event_name,
                "scene_description": scene_description,
                "characters": characters,
                "character_bindings": character_bindings,
                "character_identity_lock": identity_lock,
                "action": action,
                "emotion": self._map_emotion_to_keyframe(emotion),
                "genre": genre,
                "project_genre": style_profile.project_genre,
                "visual_style_profile": style_profile.to_dict(),
                "visual_style_prompt": style_profile.keyframe_prompt_en,
                "style_fingerprint": style_profile.fingerprint,
            })

        # 可以进一步用 LLM 提取更多关键时刻
        # 这里简化处理，只生成一个关键帧

        return prompts[:max_keyframes]

    def _extract_action_from_conflict(self, conflict: str) -> str:
        """
        C02/C08 — 从冲突描述提取动作。
        - 命中关键词时，从 image_generation_profiles.action_verbs 取视觉强动词
        - 未命中时返回默认对峙描述
        - LLM 路径在 build_keyframe_prompts_async 中处理（rewriter 直接吃 conflict_zh）
        """
        if not conflict:
            return "confronting each other, tense standoff"

        # 单字 → action_verbs key
        action_keywords = {
            "打": "fighting",
            "杀": "attacking",
            "逃": "running",
            "追": "chasing",
            "站": "standing",
            "跪": "kneeling",
            "哭": "crying",
            "笑": "laughing",
            "喊": "shouting",
            "拔": "drawing_sword",
            "指": "pointing_sword",
            "抱": "embracing",
            "推": "pushing_away",
            "晕": "fainting",
            "望": "gazing_back",
            "献": "presenting_gift",
            "读": "reading_scroll",
        }

        from app.services.image_generation_service import image_generation_service
        action_verbs = image_generation_service.profiles.get("action_verbs", {})

        for cn, key in action_keywords.items():
            if cn in conflict:
                # C08 — 用视觉强动词版本（如 "clashing swords, sparks flying"）
                if key in action_verbs:
                    return action_verbs[key]
                return key

        return "confronting each other, tense standoff"

    def build_character_identity_lock(
        self,
        bindings: List[KeyframeCharacterBinding],
    ) -> str:
        """B03 — 蓝图第 6 节：拼装 ``CHARACTER IDENTITY LOCK`` 块。

        这是关键帧 prompt 里**唯一**描述角色身份的部分，由后端确定性组装，
        不交给 LLM 自由发挥。允许多角色，每个角色声明 ``Image N = <name>``,
        位于 left|center|right，并列出 preserve exactly / allowed changes /
        forbidden 三组规则。
        """
        if not bindings:
            return ""

        lines: List[str] = ["CHARACTER IDENTITY LOCK", ""]
        for idx, b in enumerate(bindings, start=1):
            position = (b.expected_position or "center").strip().lower() or "center"
            lines.append(
                f"Image {idx} is {b.character_name} (character_id={b.character_id}, "
                f"identity_variant_id={b.identity_variant_id}, age_group={b.age_group}, "
                f"visual_fingerprint={b.visual_fingerprint}), located {position}."
            )
        lines.append("")
        lines.append("Preserve exactly for each character:")
        for b in bindings:
            lines.append(
                f"- {b.character_name}: same facial identity, same face shape, "
                f"same hairstyle and hair color, apparent age must remain {b.age_group}, "
                f"same gender presentation, same body build, "
                f"same signature accessories, same canonical outfit design."
            )
            if b.age_contract:
                lines.append(
                    f"  Age visual lock: {b.age_contract.get('prompt_anchor') or b.age_group}; "
                    f"forbid {', '.join(b.age_contract.get('forbidden_age_traits') or [])}."
                )
        lines.append("")
        lines.append("Allowed changes:")
        lines.append("- facial expression")
        lines.append("- body pose")
        lines.append("- viewing angle")
        lines.append("- action consistent with the scene")
        lines.append("- minor cloth movement caused by the action")
        lines.append("")
        lines.append("Forbidden:")
        lines.append("- redesigning the character")
        lines.append("- changing hairstyle, hair color, face shape or eye color")
        lines.append("- changing apparent age, gender presentation or body build")
        lines.append("- changing signature outfit or accessories")
        for b in bindings:
            lines.append(f"- {b.character_name} swapping identity with any other character")
        if len(bindings) > 1:
            lines.append("- identity swap, face blending, merged characters")
            lines.append("- one reference character replacing another")
        return "\n".join(lines)

    def _extract_characters_for_keyframe(
        self,
        chapter_outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        *,
        contracts: Optional[Dict[str, CharacterIdentityContract]] = None,
    ) -> List[KeyframeCharacterBinding]:
        """提取关键帧涉及的角色（B02 重写：返回 KeyframeCharacterBinding）。

        Stage_Keyframe_Identity_AR_Blueprint §5 — 关键帧角色绑定必须携带完整
        身份数据（character_id / visual_fingerprint / identity_prompt /
        reference_asset_id / reference_image_url / reference_image_sha256），
        不得再退化为 ``{name, appearance}``。

        Args:
            contracts: 可选 ``character_id -> CharacterIdentityContract`` 映射。
                传入时，每个匹配的角色会从对应契约派生完整 binding（含 reference
                字段）；未传入或角色未在映射里时，binding 退化版仍携带
                character_id / visual_fingerprint / identity_prompt，但 reference
                字段为空，``has_reference()`` 为 False —— 上游必须先调用
                ``identity_master_resolver.generate_or_resolve_identity_master_portraits``
                再传 contracts 进来，否则下游关键帧生成会拒绝。
        """
        bindings: List[KeyframeCharacterBinding] = []
        contracts = contracts or {}

        outline_chars = chapter_outline.get("characters", [])
        if not isinstance(outline_chars, list):
            return bindings

        for char_name in outline_chars[:3]:
            normalized = self._find_character_record(char_name, story_bible)
            if normalized is None:
                # 故事圣经里没有这个角色，无法构造身份；跳过避免污染关键帧
                continue
            char_id = self.generate_character_id(char_name, int(story_bible.get("project_id") or 0))
            contract = contracts.get(char_id) or contracts.get(char_name)
            if contract is not None:
                bindings.append(binding_from_contract(contract))
                continue

            # 退化路径：契约未传入时，至少把 visual_fingerprint / identity_prompt 锁住
            profile = normalized.get("visual_profile") or {}
            fingerprint = str(normalized.get("visual_fingerprint") or "")
            identity_prompt = canonical_identity_prompt(normalized) or str(
                normalized.get("visual_prompt_en") or ""
            )
            gender = str(normalized.get("gender") or "")
            from app.services.image_generation_service import ImageGenerationService
            gender_prompt = ImageGenerationService()._build_portrait_gender_anchor(gender)
            anchor = visual_style_profile_service.character_visual_anchor(
                char_name,
                identity_prompt or str(normalized.get("appearance") or ""),
                gender,
            )
            bindings.append(KeyframeCharacterBinding(
                character_id=char_id,
                character_name=char_name,
                visual_fingerprint=fingerprint,
                identity_contract_version="character-identity-v2",
                identity_prompt=identity_prompt,
                gender_prompt=gender_prompt,
                visual_profile=dict(profile) if profile else {},
                identity_anchor=anchor,
                canonical_outfit=dict(profile.get("outfit") or {}) if isinstance(profile.get("outfit"), dict) else {},
                signature_features=tuple(profile.get("signature_features") or []),
                accessories=tuple(profile.get("accessories") or []),
                canonical_identity=(
                    dict(normalized.get("canonical_identity"))
                    if isinstance(normalized.get("canonical_identity"), dict)
                    else {}
                ),
                canonical_identity_fingerprint=str(
                    normalized.get("canonical_identity_fingerprint") or ""
                ),
                asymmetric_traits=tuple(
                    (normalized.get("canonical_identity") or {}).get("asymmetric_traits") or []
                ),
                # reference 字段全部为空，has_reference() 返回 False
            ))

        return bindings

    def _find_character_appearance(
        self,
        char_name: str,
        story_bible: Dict[str, Any]
    ) -> str:
        """[DEPRECATED after B02] 从 StoryBible 查找角色外貌。

        保留以兼容历史调用点；新代码请用 ``_find_character_record``，它会
        返回完整的 normalize 后角色 dict，含 visual_profile /
        visual_fingerprint / visual_prompt_en / gender 等身份契约需要的字段。
        """
        record = self._find_character_record(char_name, story_bible)
        if record is None:
            return ""
        return str(record.get("appearance") or record.get("visual_description_cn") or "")

    def _find_character_record(
        self,
        char_name: str,
        story_bible: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """从 StoryBible 查找 normalize 后的完整角色记录。

        匹配优先级：精确 name -> character_id (md5(name|project_id)[:12])。
        返回值已经过 :func:`character_visual_profile_service.normalize_character`
        处理，所以 ``visual_profile`` / ``visual_fingerprint`` /
        ``visual_prompt_en`` / ``gender`` 等字段保证存在。
        """
        characters = story_bible.get("characters", []) or []
        if not isinstance(characters, list):
            return None
        project_id = int(story_bible.get("project_id") or 0)
        char_id = self.generate_character_id(char_name, project_id)
        for char in characters:
            if not isinstance(char, dict):
                continue
            if (char.get("name") or "").strip() == char_name.strip():
                return char
            if (char.get("character_id") or "").strip() == char_id:
                return char
        return None

    def _map_emotion_to_keyframe(self, emotion: str) -> str:
        """映射章节情绪到关键帧情绪"""
        mapping = {
            "neutral": "calm",
            "happy": "happy",
            "sad": "sad",
            "angry": "intense",
            "fear": "intense",
            "surprise": "intense"
        }
        return mapping.get(emotion, "intense")

    def build_batch_generation_plan(
        self,
        story_bible: Dict[str, Any],
        chapter_outlines: List[Dict[str, Any]],
        project_id: int,
        portrait_variations: Optional[List[Dict[str, str]]] = None,
        background_moods: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        构建批量生成计划

        Returns:
            {
                "portraits": [...],
                "backgrounds": [...],
                "keyframes": [...],
                "total_count": int
            }
        """
        portraits = self.build_portrait_prompts(story_bible, project_id, portrait_variations)

        backgrounds = []
        for outline in chapter_outlines:
            bg_prompts = self.build_background_prompts(outline, story_bible, background_moods)
            backgrounds.extend(bg_prompts)

        keyframes = []
        for outline in chapter_outlines:
            kf_prompts = self.build_keyframe_prompts("", outline, story_bible)
            keyframes.extend(kf_prompts)

        return {
            "portraits": portraits,
            "backgrounds": backgrounds,
            "keyframes": keyframes,
            "total_count": len(portraits) + len(backgrounds) + len(keyframes)
        }

    # ======================================================================
    # A01/B01/C01 — LLM Rewriter 集成层（async 包装）
    # ======================================================================
    # 这三个 async 方法在同步 build_*_prompts 之上加一层 LLM rewrite。
    # Portrait 失败时不设置 final_prompt，调用方必须停止生成；background/keyframe
    # 仍保留各自的兼容降级路径。
    # 详见 Docs/researches/Stage_Prompt_AR/00_philosophy.md 和 D01。

    async def build_portrait_prompts_async(
        self,
        story_bible: Dict[str, Any],
        project_id: int,
        variations: Optional[List[Dict[str, Any]]] = None,
        visual_style_profile: Optional[VisualStyleProfile] = None,
        project_style: str = "",
        project_title: str = "",
    ) -> List[Dict[str, Any]]:
        """A01 — 同步版 + LLM rewrite。"""
        from app.services.prompt_rewriter_service import prompt_rewriter_service

        prompts = self.build_portrait_prompts(
            story_bible,
            project_id,
            variations,
            visual_style_profile=visual_style_profile,
            project_style=project_style,
            project_title=project_title,
        )

        batch_fields = []
        for p in prompts:
            identity_fields = build_portrait_rewriter_identity(p)
            batch_fields.append({
                "asset_type": "portrait",
                "character_id": p["character_id"],
                **identity_fields,
                "age_group": p.get("age_group"),
                "age_contract": p.get("age_contract"),
                "gender": p.get("gender"),
                "gender_prompt": p.get("gender_prompt"),
                "emotion": p["emotion"],
                "outfit": p.get("outfit"),
                "pose": p.get("pose"),
                "genre": p.get("genre"),
                "project_genre": p.get("project_genre"),
                "visual_style_profile": p.get("visual_style_profile"),
                "visual_style_prompt": p.get("visual_style_prompt"),
                "style_fingerprint": p.get("style_fingerprint"),
                "shot": "full_body",
            })
        rewritten = await prompt_rewriter_service.rewrite_many("portrait", batch_fields)
        for p, r in zip(prompts, rewritten):
            if r is not None:
                p["final_prompt"] = r.to_cogview_prompt()
                p["final_prompt_source"] = "llm_rewriter"
            else:
                p["prompt_rewrite_error"] = "portrait_llm_rewrite_failed"
        return prompts

    async def build_background_prompts_async(
        self,
        chapter_outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        moods: Optional[List[str]] = None,
        environment_hint_en: Optional[str] = None,
        specs: Optional[List["BackgroundSceneSpec"]] = None,
        visual_style_profile: Optional[VisualStyleProfile] = None,
        project_style: str = "",
        project_title: str = "",
    ) -> List[Dict[str, Any]]:
        """B01 — 同步版 + LLM rewrite。

        BG-ENFORCE — 调用 rewriter 前先清洗掉 summary/scene_desc 里的角色动作描述，
        防止 rewriter 把"林夜站在车站"这类句子翻译成正向的人物主体 prompt。
        角色名本身会被独立用作 forbidden_characters（在 generate_background 处统一注入），
        不应进入正向 prompt。

        ENV-DISTILL (2026-06-15): 优先使用调用方传入的 environment_hint_en
        （由 scene_segmenter 那次 LLM 调用顺便输出的纯环境英文描述）。
        若调用方未传或为空，回退到旧的 _strip_character_actions 正则清洗路径。

        STAGE-AR (L3.29): 若调用方传入 specs（List[BackgroundSceneSpec]），
        则用 BackgroundPromptAssembler 直接产出 final_prompt，不再走 rewriter，
       也不再从 summary 兜底。这是新的 schema-driven 路径。
        """
        # L3.29: schema-driven path（不走 rewriter，不再从 summary 兜底）
        if specs:
            from app.services.background_prompt_assembler_service import BackgroundPromptAssembler
            assembler = BackgroundPromptAssembler()
            style_profile = visual_style_profile or visual_style_profile_service.build_profile(
                story_bible,
                project_style=project_style,
                project_title=project_title,
            )
            out: List[Dict[str, Any]] = []
            for spec in specs:
                spec.style_tags = visual_style_profile_service.background_style_tags(style_profile, spec)
                prompt = assembler.assemble(spec)
                out.append({
                    "asset_type": "background",
                    "scene_name": spec.scene_name,
                    "scene_description": spec.environment_description[:200],
                    "scene_selector": spec.scene_selector,
                    "scene_type": spec.scene_type,
                    "people_policy_mode": spec.people_policy.mode,
                    "mood": spec.atmosphere[:60] if spec.atmosphere else "default",
                    "genre": style_profile.project_genre,
                    "project_genre": style_profile.project_genre,
                    "visual_style_profile": style_profile.to_dict(),
                    "visual_style_prompt": style_profile.background_prompt_zh,
                    "style_fingerprint": style_profile.fingerprint,
                    "final_prompt": prompt,
                    "specs": spec,  # 透传给下游 generate_background_with_validation
                })
            return out

        from app.services.prompt_rewriter_service import prompt_rewriter_service

        prompts = self.build_background_prompts(
            chapter_outline,
            story_bible,
            moods,
            visual_style_profile=visual_style_profile,
            project_style=project_style,
            project_title=project_title,
        )

        # 收集本章所有角色名（用于 fallback 清洗）
        cast_names = self._collect_cast_names_from_outline_and_bible(chapter_outline, story_bible)

        # 优先用 segmenter 蒸馏的纯环境描述；为空走旧清洗路径
        env_hint_clean = (environment_hint_en or "").strip()

        batch_fields = []
        for p in prompts:
            scene_desc = p.get("scene_description", "") or p.get("scene_name", "")
            needs_no_humans = self._infer_needs_no_humans(scene_desc)
            # scene_zh 永远走清洗（location 也可能含角色名）
            # 注意：这里只删角色名，不做剧情过滤。剧情污染由 segmenter 的 LLM 蒸馏
            # （environment_hint_en）兜底 —— 让模型分析本章场景，输出英文环境描述。
            clean_scene = self._strip_character_actions(scene_desc, cast_names)
            clean_scene = self._strip_background_unsafe_visual_segments(clean_scene, cast_names)
            # summary_environment_hint：优先用蒸馏的纯环境；
            # env_hint 为空时补调 segmenter 单段蒸馏（基于 outline）。
            if env_hint_clean:
                clean_summary = env_hint_clean
            else:
                # SCENE-DISTILL-FALLBACK: env_hint 缺失（segmenter 整体失败），
                # 再调一次 segmenter._call_llm_single，让 LLM 基于 outline 分析场景，
                # 输出英文环境描述。失败则留空。
                distilled = await self._distill_env_hint_from_outline(chapter_outline)
                clean_summary = distilled
            batch_fields.append({
                "asset_type": "background",
                "scene_zh": clean_scene,
                "visual_keywords": self._filter_background_visual_keywords(
                    chapter_outline.get("visual_keywords", []),
                    cast_names,
                ),
                "summary_environment_hint": clean_summary,
                "mood": p["mood"],
                "genre": p.get("genre"),
                "project_genre": p.get("project_genre"),
                "visual_style_profile": p.get("visual_style_profile"),
                "visual_style_prompt": p.get("visual_style_prompt"),
                "style_fingerprint": p.get("style_fingerprint"),
                "needs_no_humans": needs_no_humans,
            })
        rewritten = await prompt_rewriter_service.rewrite_many("background", batch_fields)
        for p, r in zip(prompts, rewritten):
            if r is not None:
                p["final_prompt"] = r.to_background_cogview_prompt(
                    locked_style=str(p.get("visual_style_prompt") or ""),
                )
        return prompts

    def _filter_background_visual_keywords(
        self,
        visual_keywords: Any,
        cast_names: List[str],
    ) -> List[str]:
        """Remove character/photo/face/screen-portrait cues from background prompts."""
        if not isinstance(visual_keywords, list):
            visual_keywords = [visual_keywords] if visual_keywords else []
        out: List[str] = []
        for raw in visual_keywords:
            text = str(raw or "").strip()
            if not text:
                continue
            lower = text.lower()
            if any(name and name in text for name in cast_names):
                continue
            if any(kw.lower() in lower for kw in self.BACKGROUND_UNSAFE_VISUAL_KEYWORDS):
                continue
            out.append(text)
        return out[:4]

    def _strip_background_unsafe_visual_segments(
        self,
        text: str,
        cast_names: List[str],
    ) -> str:
        """Drop comma-separated positive prompt fragments that would induce people."""
        import re
        if not text:
            return ""
        segments = re.split(r"([,，、；;])", text)
        kept: List[str] = []
        for segment in segments:
            stripped = segment.strip()
            if not stripped or re.fullmatch(r"[,，、；;]", stripped):
                continue
            lower = stripped.lower()
            if any(name and name in stripped for name in cast_names):
                continue
            if any(kw.lower() in lower for kw in self.BACKGROUND_UNSAFE_VISUAL_KEYWORDS):
                continue
            kept.append(stripped)
        return ", ".join(kept).strip()

    def _collect_cast_names_from_outline_and_bible(
        self,
        chapter_outline: Dict[str, Any],
        story_bible: Dict[str, Any],
    ) -> List[str]:
        """从 outline.characters + story_bible.characters 合并角色名清单。"""
        names = []
        # outline
        for c in (chapter_outline.get("characters") or []):
            if isinstance(c, str):
                names.append(c)
            elif isinstance(c, dict):
                names.append(c.get("name") or "")
        # story_bible
        for c in (story_bible.get("characters") or []):
            if isinstance(c, str):
                names.append(c)
            elif isinstance(c, dict):
                names.append(c.get("name") or "")
                if c.get("name_en"):
                    names.append(c["name_en"])
                for alias in (c.get("aliases") or c.get("nicknames") or []):
                    if alias:
                        names.append(alias)
        # 去重 + 过滤空
        seen = set()
        out = []
        for n in names:
            n = (n or "").strip()
            if not n or n.lower() in seen:
                continue
            if len(n) > 30:
                continue
            seen.add(n.lower())
            out.append(n)
        return out

    async def _distill_env_hint_from_outline(self, chapter_outline: Dict[str, Any]) -> str:
        """
        SCENE-DISTILL-FALLBACK (2026-06-15) — environment_hint_en 缺失时（segmenter
        整体失败），调 segmenter._call_llm_single 让 LLM 基于 outline 分析场景，
        输出纯英文环境视觉描述。失败返回空串。

        这样即使 segmenter 主流程失败，背景图仍有 LLM 推断的场景描述，
        不会退回被污染的 outline.scene 中文剧情。
        """
        try:
            from app.services.scene_segmenter_service import scene_segmenter_service
            seg = await scene_segmenter_service._call_llm_single("", chapter_outline)
            if seg and seg.environment_hint_en:
                return seg.environment_hint_en.strip()
        except Exception as e:
            logger.warning("_distill_env_hint_from_outline failed: %s", e)
        return ""

    def _strip_character_actions(self, text: str, character_names: List[str]) -> str:
        """
        BG-ENFORCE — 从 text 中移除角色名和明显的"角色动作"句段。

        Example:
          '林夜站在废弃车站，苏晚晴看着他，外面下着雨'
          → ' ,  ,外面下着雨'

        这是粗清洗，目的是不让 rewriter 把角色动作翻译成画面主体。
        最终送 CogView-4 的 prompt 由 _enforce_background_environment_focus 兜底。
        """
        if not text or not character_names:
            return text or ""
        import re
        cleaned = text
        # 按长度降序排序，避免短名字先替换破坏长名字的子串
        for name in sorted(set(character_names), key=len, reverse=True):
            if not name:
                continue
            # 整词替换（中文名直接替换，英文用 \b 边界）
            if re.fullmatch(r"[A-Za-z\s]+", name):
                cleaned = re.sub(rf"\b{re.escape(name)}\b", "", cleaned, flags=re.IGNORECASE)
            else:
                cleaned = cleaned.replace(name, "")
        # 压缩多余空白和逗号
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\s*,\s*,+\s*", ", ", cleaned)
        cleaned = re.sub(r"^\s*,\s*|\s*,\s*$", "", cleaned)
        return cleaned.strip()

    async def build_keyframe_prompts_async(
        self,
        chapter_content: str,
        chapter_outline: Dict[str, Any],
        story_bible: Dict[str, Any],
        max_keyframes: int = 3,
        visual_style_profile: Optional[VisualStyleProfile] = None,
        project_style: str = "",
        project_title: str = "",
    ) -> List[Dict[str, Any]]:
        """C01 — 同步版 + LLM rewrite。"""
        from app.services.prompt_rewriter_service import prompt_rewriter_service

        prompts = self.build_keyframe_prompts(
            chapter_content,
            chapter_outline,
            story_bible,
            max_keyframes,
            visual_style_profile=visual_style_profile,
            project_style=project_style,
            project_title=project_title,
        )

        batch_fields = []
        for p in prompts:
            chars_for_llm = []
            for c in p.get("characters", []):
                chars_for_llm.append({
                    "name": c.get("name", ""),
                    "role": "aggressor" if "fighting" in p.get("action", "") else "bystander",
                    "appearance_zh": c.get("appearance", ""),
                })
            batch_fields.append({
                "asset_type": "keyframe",
                "event_zh": p.get("event_name", ""),
                "scene_zh": p.get("scene_description", ""),
                "conflict_zh": chapter_outline.get("conflict", ""),
                "summary_zh": chapter_outline.get("summary", ""),
                "characters": chars_for_llm[:3],
                "emotion": p.get("emotion"),
                "genre": p.get("genre"),
                "project_genre": p.get("project_genre"),
                "visual_style_profile": p.get("visual_style_profile"),
                "visual_style_prompt": p.get("visual_style_prompt"),
                "style_fingerprint": p.get("style_fingerprint"),
            })
        rewritten = await prompt_rewriter_service.rewrite_many("keyframe", batch_fields)
        for p, r in zip(prompts, rewritten):
            if r is not None:
                p["final_prompt"] = r.to_cogview_prompt(
                    locked_style=str(p.get("visual_style_prompt") or ""),
                    use_final_prompt=False,
                )
        return prompts

    def _infer_needs_no_humans(self, scene_description: str) -> bool:
        """
        B06 — 判断场景是否需要严禁人物。

        2026-06-14 升级（用户硬要求）：所有背景图强制完全无人。
        本函数永远返回 True，无论场景描述是什么。
        旧的 soldier_co_location_words 关键词逻辑不再适用 ——
        不论是公共场景还是私人场景，都必须无人。
        """
        return True



# 全局实例
prompt_builder_service = PromptBuilderService()
