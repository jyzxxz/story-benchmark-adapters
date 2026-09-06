"""Canonical character visual taxonomy, description rendering and library matching.

The profile is the source of truth.  Human-readable descriptions and image prompts
are deterministic projections so every image source speaks the same language.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


SCHEMA_VERSION = "character_visual_v1"


def _labels(**values: str) -> dict[str, str]:
    return values


TAXONOMY: dict[str, dict[str, str]] = {
    "world_style": _labels(
        modern="现代", historical="历史古代", xianxia="仙侠武侠", fantasy="奇幻",
        scifi="科幻", cyberpunk="赛博朋克", space="太空科幻", apocalypse="末日",
        supernatural="超自然", rural="乡村现实",
    ),
    "species": _labels(
        human="人类", android="仿生人", demon="魔族", spirit="灵体", other="其他物种",
    ),
    "age_group": _labels(
        toddler="幼儿", child="儿童", teen="少年", young_adult="青年", adult="成年",
        middle_aged="中年", senior="老年",
    ),
    "gender_presentation": _labels(
        masculine="男性化外观", feminine="女性化外观", androgynous="中性外观",
    ),
    "role_family": _labels(
        student="学生", office="职场", education="教育", medical="医疗",
        public_safety="公共安全", service="服务行业", family="家庭成员",
        leadership="管理者", professional="专业人士", media="媒体文娱",
        athlete="运动", historical_court="古代社会", cultivator="修行者",
        mystery="悬疑人物", scifi_crew="科幻职业", civilian="普通人",
        antagonist="对立人物",
    ),
    "role_key": _labels(
        antagonist="反派", athlete="运动员", barista="咖啡师", best_friend="挚友",
        boss="上司", chef="厨师", child="孩子", colleague="同事",
        delivery_rider="配送员", detective="侦探", doctor="医生", driver="司机",
        father="父亲", firefighter="消防员", gamer="游戏玩家", grandfather="祖父",
        grandmother="祖母", hr_interviewer="人事面试官", investor="投资人",
        journalist="记者", lawyer="律师", librarian="图书管理员", mentor="导师",
        mother="母亲", mysterious_stranger="神秘陌生人", neighbor="邻居",
        nurse="护士", paramedic="急救员", patient="病人", police="警察",
        principal="校长", rebel="叛逆者", researcher="研究员",
        security_guard="保安", shop_clerk="店员", startup_founder="创业者",
        student="学生", teacher="教师", therapist="心理咨询师",
        office_worker="职员", suspect="嫌疑人", monk="僧侣", romantic_lead="爱情主角",
        farmer="农民", android="仿生人", engineer="工程师", captain="舰长",
        medic="医疗官", pilot="飞行员", survivor="幸存者", influencer="网红",
        corporate_executive="企业高管", hacker="黑客", toddler="幼儿",
        assassin="刺客", emperor="帝王", guard="卫兵", innkeeper="客栈掌柜",
        maid="侍女", official="官员", princess="公主", sect_master="宗主",
        sword_master="剑道宗师", swordswoman="女剑客", healer="医修",
        scholar="书生", demon="魔族人物", supernatural_being="超自然人物",
        civilian="普通人", other="其他身份",
    ),
    "height": _labels(short="较矮", average="中等身高", tall="高挑"),
    "build": _labels(
        slim="纤细", lean="清瘦", average="匀称", athletic="健美", strong="强壮",
        stocky="敦实", soft="柔和丰润",
    ),
    "face_shape": _labels(
        round="圆脸", oval="椭圆脸", angular="轮廓分明", square="方脸",
        heart="心形脸", mature="成熟面容",
    ),
    "skin_tone": _labels(
        pale="苍白肤色", light="浅肤色", medium="自然肤色", tan="小麦肤色",
        dark="深肤色", fantasy="非自然肤色",
    ),
    "eye_color": _labels(
        black="黑色眼睛", dark_brown="深棕色眼睛", brown="棕色眼睛",
        blue="蓝色眼睛", green="绿色眼睛", gray="灰色眼睛", gold="金色眼睛",
        red="红色眼睛", other="特殊瞳色",
    ),
    "hair_color": _labels(
        black="黑发", dark_brown="深棕发", brown="棕发", blonde="金发", red="红发",
        gray="灰发", white="白发", blue="蓝发", purple="紫发", pink="粉发",
        other="特殊发色",
    ),
    "hair_length": _labels(
        bald="无发", very_short="极短发", short="短发", medium="中长发",
        long="长发", very_long="及腰长发",
    ),
    "hair_style": _labels(
        straight="直发", wavy="微卷发", curly="卷发", ponytail="马尾",
        bun="盘发", braided="编发", messy="略显凌乱", neat="整齐利落",
        spiky="竖刺短发", bob="波波头", twin_tail="双马尾", covered="头发被遮盖",
        other="自然发型",
    ),
    "outfit_style": _labels(
        casual="日常休闲装", school_uniform="校服", business="商务装", formal="正式礼服",
        workwear="职业工作服", sportswear="运动装", streetwear="街头服饰",
        medical_uniform="医疗制服", public_safety_uniform="公共安全制服",
        military="军装", historical_robe="古代袍服", xianxia_robe="仙侠长袍",
        armor="甲胄", cyberpunk="赛博服装", scifi_uniform="科幻制服",
        rural="乡村劳动服", traditional="传统服饰", religious="宗教服饰", other="特色服装",
    ),
    "outfit_item": _labels(
        long_coat="长外套", shirt="衬衫", blazer="西装外套", trousers="长裤", skirt="半身裙",
        dress="连衣裙", hoodie="连帽衫", jacket="夹克", lab_coat="白大褂", scrub_top="手术服",
        uniform="制服", armor="护甲", robe="长袍", cape="披风", boots="长靴",
        sneakers="运动鞋", gloves="手套", hat="帽子", scarf="围巾",
    ),
    "signature_feature": _labels(
        thin_scar_left_brow="左眉细疤", facial_scar="面部疤痕", freckles="雀斑",
        beauty_mark="美人痣", glasses="眼镜", eyepatch="眼罩", tattoo="纹身",
        mechanical_limbs="机械义肢", pointed_ears="尖耳", horns="角", halo="光环",
    ),
    "accessory": _labels(
        wristwatch="腕表", glasses="眼镜", earrings="耳环", necklace="项链", hairpin="发簪",
        badge="徽章", stethoscope="听诊器", sword="佩剑", book="书", tablet="平板设备",
        helmet="头盔", hat="帽子", scarf="围巾",
    ),
    "color": _labels(
        black="黑色", white="白色", gray="灰色", charcoal="炭灰色", navy="藏蓝色",
        blue="蓝色", cyan="青色", green="绿色", olive="橄榄绿", red="红色",
        burgundy="酒红色", orange="橙色", yellow="黄色", gold="金色",
        brown="棕色", beige="米色", pink="粉色", purple="紫色", silver="银色",
    ),
    "visual_temperament": _labels(
        reserved="内敛", serious="严肃", warm="温和", cheerful="开朗", gentle="柔和",
        cold="冷峻", confident="自信", mysterious="神秘", energetic="活力",
        calm="沉静", stern="威严", rebellious="叛逆", professional="干练",
        innocent="天真", weary="疲惫",
    ),
}

EN_LABELS: dict[str, dict[str, str]] = {
    key: {value: value.replace("_", " ") for value in values}
    for key, values in TAXONOMY.items()
}

ROLE_FAMILY_BY_KEY = {
    **{k: "student" for k in ("student",)},
    **{k: "office" for k in ("office_worker", "colleague", "hr_interviewer", "startup_founder")},
    **{k: "education" for k in ("teacher", "principal", "mentor", "librarian")},
    **{k: "medical" for k in ("doctor", "nurse", "paramedic", "patient", "therapist")},
    **{k: "public_safety" for k in ("police", "firefighter", "security_guard", "guard")},
    **{k: "service" for k in ("barista", "chef", "delivery_rider", "driver", "shop_clerk", "innkeeper", "maid")},
    **{k: "family" for k in ("father", "mother", "grandfather", "grandmother", "child", "toddler")},
    **{k: "leadership" for k in ("boss", "investor", "corporate_executive", "emperor", "sect_master", "captain")},
    **{k: "professional" for k in ("detective", "lawyer", "researcher", "engineer")},
    **{k: "media" for k in ("journalist", "influencer", "gamer")},
    **{k: "athlete" for k in ("athlete",)},
    **{k: "historical_court" for k in ("assassin", "emperor", "guard", "innkeeper", "maid", "official", "princess", "monk")},
    **{k: "cultivator" for k in ("sect_master", "sword_master", "swordswoman", "healer", "scholar", "demon")},
    **{k: "mystery" for k in ("suspect", "mysterious_stranger")},
    **{k: "scifi_crew" for k in ("android", "engineer", "captain", "medic", "pilot", "hacker")},
    **{k: "antagonist" for k in ("antagonist", "rebel")},
}

_FIELD_ALIASES = {
    "contemporary": "modern", "modern_day": "modern", "wuxia": "xianxia",
    "sci_fi": "scifi", "sci-fi": "scifi", "male": "masculine", "female": "feminine",
    "nonbinary": "androgynous", "young": "young_adult", "elderly": "senior",
    "middle_age": "middle_aged", "normal": "average", "fit": "athletic",
}

_CN_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("侦探", "刑警"), "detective"), (("医生",), "doctor"), (("护士",), "nurse"),
    (("教师", "老师"), "teacher"), (("学生", "同学"), "student"),
    (("警察",), "police"), (("律师",), "lawyer"), (("黑客",), "hacker"),
    (("工程师",), "engineer"), (("记者",), "journalist"), (("刺客",), "assassin"),
    (("公主",), "princess"), (("皇帝", "帝王"), "emperor"), (("农民", "农夫"), "farmer"),
    (("飞行员",), "pilot"), (("舰长", "船长"), "captain"), (("僧", "和尚"), "monk"),
    (("剑客", "剑修"), "sword_master"), (("宗主", "掌门"), "sect_master"),
]


def _choice(field: str, value: Any, default: str) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    raw = _FIELD_ALIASES.get(raw, raw)
    return raw if raw in TAXONOMY[field] else default


def _slug_list(values: Any, *, limit: int = 5) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values else []
    out: list[str] = []
    for value in values:
        slug = re.sub(r"[^a-z0-9_\-]", "", str(value).strip().lower().replace(" ", "_"))
        if slug and slug not in out:
            out.append(slug)
        if len(out) >= limit:
            break
    return out


def _registered_list(field: str, values: Any, *, limit: int = 5) -> list[str]:
    return [value for value in (_choice(field, item, "") for item in _slug_list(values, limit=limit)) if value]


def _infer_role(text: str) -> str:
    lowered = text.lower()
    for hints, key in _CN_HINTS:
        if any(hint in text for hint in hints):
            return key
    for key in TAXONOMY["role_key"]:
        if key != "other" and key.replace("_", " ") in lowered:
            return key
    return "civilian"


def _infer_world(text: str) -> str:
    lowered = text.lower()
    candidates = [
        (("仙侠", "修仙", "宗门", "剑修"), "xianxia"), (("赛博", "cyberpunk"), "cyberpunk"),
        (("太空", "星舰", "宇宙"), "space"), (("末日", "废土"), "apocalypse"),
        (("科幻", "机器人", "仿生人"), "scifi"), (("古代", "王朝", "宫廷"), "historical"),
        (("乡村", "农村"), "rural"), (("灵异", "超自然", "幽灵"), "supernatural"),
        (("奇幻", "魔法"), "fantasy"),
    ]
    for hints, value in candidates:
        if any(h.lower() in lowered for h in hints):
            return value
    return "modern"


def _inferred_age(text: str, role_key: str) -> str:
    if any(x in text for x in ("婴儿", "幼儿")) or role_key == "toddler":
        return "toddler"
    if any(x in text for x in ("儿童", "小孩", "男孩", "女孩")) or role_key == "child":
        return "child"
    if any(x in text for x in ("少年", "少女", "中学生", "高中生")):
        return "teen"
    if any(x in text for x in ("老人", "老年", "祖父", "祖母", "爷爷", "奶奶")) or role_key in {"grandfather", "grandmother"}:
        return "senior"
    if any(x in text for x in ("中年", "父亲", "母亲")):
        return "middle_aged"
    return "young_adult"


def _default_outfit(world_style: str, role_key: str) -> str:
    if world_style == "xianxia":
        return "xianxia_robe"
    if world_style == "historical":
        return "historical_robe"
    if world_style == "cyberpunk":
        return "cyberpunk"
    if world_style in {"scifi", "space"}:
        return "scifi_uniform"
    if role_key in {"doctor", "nurse", "paramedic", "medic"}:
        return "medical_uniform"
    if role_key in {"police", "firefighter", "security_guard", "guard"}:
        return "public_safety_uniform"
    if role_key == "student":
        return "school_uniform"
    if role_key in {"boss", "lawyer", "investor", "corporate_executive", "office_worker"}:
        return "business"
    return "casual"


def normalize_profile(
    profile: Any,
    *,
    character: dict[str, Any] | None = None,
    context_text: str = "",
) -> dict[str, Any]:
    """Return a strict, complete v1 profile from LLM, legacy or partial data."""
    source = dict(profile) if isinstance(profile, dict) else {}
    character = character or {}
    legacy_text = " ".join(
        str(character.get(key) or "")
        for key in ("role", "identity", "appearance", "description", "personality")
    )
    inference_text = f"{legacy_text} {context_text}"
    role_key = _choice("role_key", source.get("role_key"), _infer_role(inference_text))
    world_style = _choice("world_style", source.get("world_style"), _infer_world(inference_text))
    gender = str(character.get("gender") or "").lower()
    gender_default = "feminine" if gender == "female" else "masculine" if gender == "male" else "androgynous"

    body = source.get("body") if isinstance(source.get("body"), dict) else {}
    face = source.get("face") if isinstance(source.get("face"), dict) else {}
    hair = source.get("hair") if isinstance(source.get("hair"), dict) else {}
    outfit = source.get("outfit") if isinstance(source.get("outfit"), dict) else {}
    colors = [_choice("color", value, "") for value in (outfit.get("primary_colors") or [])]
    colors = [value for value in colors if value][:3] or ["navy", "white"]

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "world_style": world_style,
        "species": _choice("species", source.get("species"), "human"),
        "age_group": _choice("age_group", source.get("age_group"), _inferred_age(inference_text, role_key)),
        "gender_presentation": _choice("gender_presentation", source.get("gender_presentation"), gender_default),
        "role_family": _choice("role_family", source.get("role_family"), ROLE_FAMILY_BY_KEY.get(role_key, "civilian")),
        "role_key": role_key,
        "body": {
            "height": _choice("height", body.get("height"), "average"),
            "build": _choice("build", body.get("build"), "average"),
        },
        "face": {
            "shape": _choice("face_shape", face.get("shape"), "oval"),
            "skin_tone": _choice("skin_tone", face.get("skin_tone"), "medium"),
            "eye_color": _choice("eye_color", face.get("eye_color"), "dark_brown"),
        },
        "hair": {
            "color": _choice("hair_color", hair.get("color"), "black"),
            "length": _choice("hair_length", hair.get("length"), "short"),
            "style": _choice("hair_style", hair.get("style"), "neat"),
        },
        "outfit": {
            "style": _choice("outfit_style", outfit.get("style"), _default_outfit(world_style, role_key)),
            "primary_colors": colors,
            "items": _registered_list("outfit_item", outfit.get("items"), limit=4),
        },
        "signature_features": _registered_list("signature_feature", source.get("signature_features"), limit=4),
        "accessories": _registered_list("accessory", source.get("accessories"), limit=4),
        "visual_temperament": [
            value for value in (
                _choice("visual_temperament", item, "")
                for item in (source.get("visual_temperament") or [])
            ) if value
        ][:3] or ["calm"],
    }
    return normalized


def _cn(field: str, value: str) -> str:
    return TAXONOMY.get(field, {}).get(value, value.replace("_", " "))


def _en(field: str, value: str) -> str:
    return EN_LABELS.get(field, {}).get(value, value.replace("_", " "))


def describe_cn(profile: dict[str, Any]) -> str:
    p = normalize_profile(profile)
    body, face, hair, outfit = p["body"], p["face"], p["hair"], p["outfit"]
    identity = "、".join((
        _cn("world_style", p["world_style"]), _cn("age_group", p["age_group"]),
        _cn("gender_presentation", p["gender_presentation"]), _cn("species", p["species"]),
        _cn("role_key", p["role_key"]),
    ))
    body_text = f"{_cn('height', body['height'])}、{_cn('build', body['build'])}体型"
    face_text = f"{_cn('face_shape', face['shape'])}，{_cn('skin_tone', face['skin_tone'])}，{_cn('eye_color', face['eye_color'])}"
    hair_text = f"{_cn('hair_color', hair['color'])}，{_cn('hair_length', hair['length'])}，{_cn('hair_style', hair['style'])}"
    colors = "、".join(_cn("color", c) for c in outfit["primary_colors"])
    outfit_text = f"身穿以{colors}为主的{_cn('outfit_style', outfit['style'])}"
    temperament = "、".join(_cn("visual_temperament", x) for x in p["visual_temperament"])
    extras = [
        *(_cn("signature_feature", x) for x in p["signature_features"]),
        *(_cn("accessory", x) for x in p["accessories"]),
        *(_cn("outfit_item", x) for x in outfit["items"]),
    ]
    extra_text = f"，标志元素为{'、'.join(extras)}" if extras else ""
    return f"{identity}；{body_text}；{face_text}；{hair_text}；{outfit_text}；整体气质{temperament}{extra_text}。"


def prompt_en(profile: dict[str, Any]) -> str:
    p = normalize_profile(profile)
    body, face, hair, outfit = p["body"], p["face"], p["hair"], p["outfit"]
    pieces = [
        f"{_en('age_group', p['age_group'])} {_en('gender_presentation', p['gender_presentation'])} {_en('species', p['species'])}",
        f"{_en('role_key', p['role_key'])}, {_en('world_style', p['world_style'])} setting",
        f"{_en('height', body['height'])} height, {_en('build', body['build'])} build",
        f"{_en('face_shape', face['shape'])} face, {_en('skin_tone', face['skin_tone'])}, {_en('eye_color', face['eye_color'])}",
        f"{_en('hair_color', hair['color'])} {_en('hair_length', hair['length'])} {_en('hair_style', hair['style'])} hair",
        f"{_en('outfit_style', outfit['style'])}, {' and '.join(_en('color', c) for c in outfit['primary_colors'])} palette",
        f"{', '.join(_en('visual_temperament', x) for x in p['visual_temperament'])} visual temperament",
    ]
    extras = p["signature_features"] + p["accessories"] + outfit["items"]
    if extras:
        pieces.append("signature details: " + ", ".join(x.replace("_", " ") for x in extras))
    return ", ".join(pieces)


def fingerprint(profile: dict[str, Any]) -> str:
    canonical = json.dumps(normalize_profile(profile), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"cv1:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def match_tokens(profile: dict[str, Any]) -> list[str]:
    p = normalize_profile(profile)
    tokens = [p[k] for k in ("world_style", "species", "age_group", "gender_presentation", "role_family", "role_key")]
    tokens.extend((p["outfit"]["style"], *p["outfit"]["primary_colors"], *p["visual_temperament"]))
    return list(dict.fromkeys(tokens))


def normalize_character(character: Any, *, context_text: str = "") -> dict[str, Any]:
    char = {"name": character} if isinstance(character, str) else dict(character or {})
    supplied = isinstance(char.get("visual_profile"), dict)
    profile = normalize_profile(char.get("visual_profile"), character=char, context_text=context_text)
    char["visual_profile"] = profile
    char["visual_profile_source"] = "structured" if supplied else "legacy_inferred"
    char["visual_description_cn"] = describe_cn(profile)
    char["visual_prompt_en"] = prompt_en(profile)
    char["visual_fingerprint"] = fingerprint(profile)
    char["appearance"] = char["visual_description_cn"]
    return char


def normalize_story_bible(story_bible: dict[str, Any], *, context_text: str = "") -> dict[str, Any]:
    out = dict(story_bible or {})
    full_context = " ".join((
        context_text, str(out.get("worldview") or ""), str(out.get("style_rules") or ""),
        str(out.get("theme_and_tone") or ""),
    ))
    out["characters"] = [normalize_character(c, context_text=full_context) for c in out.get("characters") or []]
    out["character_visual_schema_version"] = SCHEMA_VERSION
    return out


@dataclass(frozen=True)
class LibraryPortrait:
    base_key: str
    profile: dict[str, Any]
    variants: dict[str, str]


_FAMILY_WORLD = {
    "modern": "modern", "historical": "historical", "xianxia": "xianxia",
    "cyberpunk": "cyberpunk", "space": "space", "scifi": "scifi",
    "apocalypse": "apocalypse", "supernatural": "supernatural", "rural": "rural",
    "mystery": "modern", "romance": "modern", "family": "modern",
    "celebrity": "modern", "priest": "historical",
}


def _filename_profile(base_key: str) -> dict[str, Any]:
    tokens = base_key.removeprefix("portrait_").split("_")
    family = tokens[0]
    role_text = "_".join(tokens[1:])
    role_key = "civilian"
    for candidate in sorted(TAXONOMY["role_key"], key=len, reverse=True):
        if candidate != "other" and candidate in role_text:
            role_key = candidate
            break
    aliases = {
        "young_f_office": "office_worker", "young_m_office": "office_worker",
        "student_f_bright": "student", "student_f_quiet": "student",
        "student_m_quiet": "student", "student_m_sunny": "student",
        "student_nb_sporty": "student", "poor_student": "student", "rich_classmate": "student",
        "female_lead_mature": "romantic_lead", "male_lead_mature": "romantic_lead",
        "corporate_exec": "corporate_executive", "sword_master": "sword_master",
        "young_scholar": "scholar", "demon_youth": "demon", "survivor": "survivor",
    }
    role_key = aliases.get(role_text, role_key)
    gender = "androgynous"
    female_markers = ("_f", "female", "mother", "girl", "maid", "princess", "swordswoman", "grandmother")
    male_markers = ("_m", "male", "father", "boy", "emperor", "monk", "grandfather")
    wrapped = f"_{role_text}"
    if any(x in wrapped for x in female_markers):
        gender = "feminine"
    elif any(x in wrapped for x in male_markers):
        gender = "masculine"
    age = _inferred_age(role_text, role_key)
    return normalize_profile({
        "world_style": _FAMILY_WORLD.get(family, "modern"), "species": "android" if role_key == "android" else "demon" if role_key == "demon" else "human",
        "age_group": age, "gender_presentation": gender, "role_key": role_key,
        "role_family": ROLE_FAMILY_BY_KEY.get(role_key, "civilian"),
    })


def load_library_portraits(root: Path) -> list[LibraryPortrait]:
    variants: dict[str, dict[str, str]] = {}
    for path in sorted(root.glob("*.png")):
        base, _, emotion = path.stem.partition("__")
        variants.setdefault(base, {})[emotion or "neutral"] = path.name
    return [LibraryPortrait(base, _filename_profile(base), choices) for base, choices in variants.items()]


_V2_WORLD_STYLE = {
    "现代": "modern",
    "历史东方": "historical",
    "仙侠/武侠": "xianxia",
    "西方奇幻": "fantasy",
    "科幻/赛博": "scifi",
    "末世": "apocalypse",
    "蒸汽朋克": "scifi",
    "悬疑/恐怖": "supernatural",
    "多题材/非人": "fantasy",
}


def _v2_age_group(value: Any) -> str:
    text = str(value or "")
    for marker, result in (
        ("幼儿", "toddler"), ("儿童", "child"), ("少年", "teen"),
        ("青少年", "teen"), ("老年", "senior"), ("中年", "middle_aged"),
        ("青年", "young_adult"), ("成年", "adult"),
    ):
        if marker in text:
            return result
    return "young_adult"


_V2_ROLE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("父亲", "爸爸"), "father"), (("母亲", "妈妈"), "mother"),
    (("祖父", "爷爷"), "grandfather"), (("祖母", "奶奶"), "grandmother"),
    (("幼儿", "儿童", "男孩", "女孩"), "child"),
    (("小学", "中学", "高中", "大学生", "学生"), "student"),
    (("厨师",), "chef"), (("咖啡师",), "barista"), (("配送",), "delivery_rider"),
    (("司机",), "driver"), (("消防", "救援"), "firefighter"), (("警察",), "police"),
    (("律师",), "lawyer"), (("教师", "老师"), "teacher"), (("医生",), "doctor"),
    (("护士", "护理"), "nurse"), (("工程师", "程序员"), "engineer"),
    (("记者",), "journalist"), (("运动员",), "athlete"), (("明星", "网红"), "influencer"),
    (("皇帝",), "emperor"), (("公主", "郡主"), "princess"), (("侍女", "婢女"), "maid"),
    (("侍卫", "捕快", "军人", "安保"), "guard"), (("书生", "文官"), "scholar"),
    (("客栈掌柜",), "innkeeper"), (("刺客",), "assassin"),
    (("剑修", "剑客", "武林宗师"), "sword_master"), (("宗门长老", "宗主"), "sect_master"),
    (("医修", "治疗者"), "healer"), (("黑客",), "hacker"),
    (("舰队", "舰长"), "captain"), (("飞行员",), "pilot"),
    (("机器人", "仿生"), "android"), (("幸存者", "拾荒者"), "survivor"),
]


def _v2_role_key(archetype: str) -> str:
    for hints, role_key in _V2_ROLE_HINTS:
        if any(hint in archetype for hint in hints):
            return role_key
    return _infer_role(archetype)


def _v2_profile(record: dict[str, Any]) -> dict[str, Any]:
    archetype = str(record.get("archetype") or "普通人")
    genre = str(record.get("genre") or "现代")
    gender_text = str(record.get("gender_expression") or "")
    gender = "female" if "女" in gender_text else "male" if "男" in gender_text else ""
    return normalize_profile(
        {
            "world_style": _V2_WORLD_STYLE.get(genre, _infer_world(genre)),
            "age_group": _v2_age_group(record.get("age_group")),
            "gender_presentation": (
                "feminine" if gender == "female" else "masculine" if gender == "male" else "androgynous"
            ),
            "role_key": _v2_role_key(archetype),
        },
        character={"role": archetype, "gender": gender},
        context_text=f"{genre} {record.get('category') or ''}",
    )


def load_v2_library_portraits(root: Path) -> list[LibraryPortrait]:
    """Load accepted V2 portraits from the manifest without relying on filenames."""
    manifest = root / "06_manifests" / "asset_manifest.jsonl"
    if not manifest.is_file():
        return []

    variants: dict[str, dict[str, str]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if record.get("status") != "accepted" or record.get("asset_type") != "portrait":
            continue
        relative = str(record.get("local_path") or "").replace("\\", "/").lstrip("/")
        if not relative or not (root / relative).is_file():
            continue

        base = "_".join((
            "v2",
            str(record.get("style_pack_id") or "style").lower(),
            str(record.get("pack_id") or "pack").lower(),
            str(record.get("identity_slot") or "a").lower(),
        ))
        emotion = str(record.get("emotion") or "neutral").strip().lower()
        choices = variants.setdefault(base, {})
        variant_key = emotion
        if variant_key in choices:
            pose = str(record.get("pose") or "variant").strip().lower()
            variant_key = f"{emotion}_{pose}"
            suffix = 2
            while variant_key in choices:
                variant_key = f"{emotion}_{pose}_{suffix}"
                suffix += 1
        choices[variant_key] = f"/library-assets-v2/{relative}"
        profiles.setdefault(base, _v2_profile(record))

    return [LibraryPortrait(base, profiles[base], choices) for base, choices in sorted(variants.items())]


_MATCH_WEIGHTS = {
    "species": 20.0, "world_style": 20.0, "role_key": 20.0,
    "age_group": 15.0, "gender_presentation": 15.0, "appearance": 10.0,
}


_AGE_ORDER = ["toddler", "child", "teen", "young_adult", "adult", "middle_aged", "senior"]


def _dimension_score(field: str, wanted: str, candidate: str, target: dict[str, Any], asset: dict[str, Any]) -> float:
    if wanted == candidate:
        return 1.0
    if field == "role_key" and target["role_family"] == asset["role_family"]:
        return 0.55
    if field == "world_style" and {wanted, candidate} <= {"scifi", "space", "cyberpunk"}:
        return 0.55
    if field == "age_group":
        distance = abs(_AGE_ORDER.index(wanted) - _AGE_ORDER.index(candidate))
        return max(0.0, 1.0 - distance * 0.3)
    if field == "gender_presentation" and "androgynous" in {wanted, candidate}:
        return 0.45
    return 0.0


_EMOTION_GROUPS = [
    {"happy", "smile", "soft_smile", "warm_smile", "encouraging", "excited"},
    {"angry", "stern", "serious", "determined", "cold", "contempt", "commanding"},
    {"sad", "worried", "weak", "tired", "nervous"},
    {"surprise", "curious", "alert"},
    {"neutral", "calm", "professional", "focused", "thinking", "confident", "talking"},
]


def choose_emotion(available: Iterable[str], requested: str) -> tuple[str, str]:
    options = list(available)
    requested = requested or "neutral"
    if requested in options:
        return requested, "exact"
    group = next((g for g in _EMOTION_GROUPS if requested in g), {requested})
    similar = next((value for value in options if value in group), None)
    if similar:
        return similar, "similar"
    if "neutral" in options:
        return "neutral", "neutral_fallback"
    return (options[0], "first_available") if options else ("neutral", "missing")


def match_library(
    profile: dict[str, Any], portraits: list[LibraryPortrait], *, emotion: str = "neutral", limit: int = 5,
) -> list[dict[str, Any]]:
    target = normalize_profile(profile)
    results: list[dict[str, Any]] = []
    for portrait in portraits:
        candidate = portrait.profile
        dimensions: dict[str, float] = {}
        for field in ("species", "world_style", "role_key", "age_group", "gender_presentation"):
            dimensions[field] = round(_dimension_score(field, target[field], candidate[field], target, candidate), 3)
        target_tags = set(match_tokens(target))
        candidate_tags = set(match_tokens(candidate))
        dimensions["appearance"] = round(len(target_tags & candidate_tags) / max(1, len(target_tags)), 3)
        score = sum(dimensions[field] * weight for field, weight in _MATCH_WEIGHTS.items())
        selected_emotion, fallback = choose_emotion(portrait.variants, emotion)
        conflicts = [field for field in ("species", "world_style", "gender_presentation") if dimensions[field] == 0]
        confidence = "high" if score >= 75 else "medium" if score >= 50 else "low"
        results.append({
            "asset_key": f"{portrait.base_key}__{selected_emotion}",
            "base_key": portrait.base_key,
            "filename": portrait.variants.get(selected_emotion),
            "selected_emotion": selected_emotion,
            "emotion_fallback": fallback,
            "score": round(score, 2),
            "confidence": confidence,
            "dimension_scores": dimensions,
            "conflicts": conflicts,
            "profile": candidate,
        })
    return sorted(results, key=lambda x: (-x["score"], x["base_key"]))[:max(1, min(limit, 20))]
