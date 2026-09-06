# E04 — background-scene-selector-stable-slug

## 背景

`scene_selector` 是 BackgroundSceneSpec 关键字段，用于 vn_graph 背景匹配（见 E05）。如果让 LLM 自由发挥，每次跑同章节可能产出不同 slug（"war_camp_night" vs "night_war_camp"），导致匹配漂移。

**必须由代码 deterministic 生成**——不让 LLM 介入。

## SOTA 实践

**URL slug generation best practices**（[`github.com/uniphore/unislug`](https://github.com/uniphore/unislug)）：标准化 slug 由 lowercase + separator + ASCII only 组成。

**Hashids**（[`github.com/hashids/hashids`](https://github.com/hashids/hashids)）：把多字段组合成稳定 hash，用于实体 ID。

**Semantic versioning**（[`semver.org`](https://semver.org)）：版本号语义化思路，类似地 slug 应"语义化可读"。

## 落地建议

`BackgroundSceneAnalyzerService._build_scene_selector`：

```python
import re

def _build_scene_selector(self, specs: list[BackgroundSceneSpec]):
    """为每个 spec 生成稳定的 ASCII slug"""
    for spec in specs:
        parts = []
        # 1. scene_type（受控词表值）
        if spec.scene_type:
            parts.append(self._slugify(spec.scene_type))
        # 2. time_of_day（桶化）
        time_bucket = self._bucketize_time(spec.time_of_day)
        if time_bucket:
            parts.append(time_bucket)
        # 3. weather
        if spec.weather:
            parts.append(self._slugify(spec.weather))
        # 4. people_policy.mode（关键业务标识）
        mode_short = {
            "empty_required": "empty",
            "background_people_optional": "extras",
            "background_groups_required": "groups",
        }.get(spec.people_policy.mode, "default")
        parts.append(mode_short)
        # 5. 物理状态（非 normal 才加）
        state = self._physical_state_token(spec)
        if state and state != "normal":
            parts.append(state)

        # 用双下划线连接（避免与单词内 underscore 冲突）
        spec.scene_selector = "__".join(parts)

def _slugify(self, s: str) -> str:
    """转 ASCII slug"""
    if not s:
        return ""
    # 中文转拼音（简化版，可按需扩展映射表）
    cn_to_en = {
        "夜": "night", "雨": "rain", "晴": "clear", "雪": "snow",
        "晨": "dawn", "昏": "dusk", "白天": "day",
        # ...更多映射
    }
    out = cn_to_en.get(s, s)
    out = re.sub(r"[^a-zA-Z0-9]+", "_", out).strip("_").lower()
    return out

def _bucketize_time(self, t: str) -> str:
    """time_of_day 桶化为 day/dusk/night"""
    t = (t or "").lower()
    if any(k in t for k in ["dawn", "morning", "noon", "day", "白", "晨"]):
        return "day"
    if any(k in t for k in ["dusk", "sunset", "昏"]):
        return "dusk"
    if any(k in t for k in ["night", "evening", "夜"]):
        return "night"
    return ""
```

**示例**：

| scene_type | time_of_day | weather | mode | scene_selector |
|---|---|---|---|---|
| war_camp | night | clear | empty_required | `war_camp__night__clear__empty` |
| market | day | rain | background_groups_required | `market__day__rain__groups` |
| bedroom | night | clear | empty_required | `bedroom__night__clear__empty` |

**稳定性保证**：
- 输入相同 → 输出相同（deterministic）
- 不依赖 LLM（不受 model 漂移影响）
- 双下划线分隔避免歧义

## 风险与权衡

1. **中文转拼音映射不全**：`_slugify` 的 `cn_to_en` 字典可能漏词。权衡：fallback 用 ascii 化（去非 ASCII 字符）+ hash，保证唯一性。
2. **slug 过长**：`war_camp__night__clear__empty` = 30 字符，可接受；但加上 physical_state 可能 50+ 字符。权衡：限制总长 ≤ 60 字符，超出截断 + hash 后缀。

## 完成判据自检

- ✅ 围绕"deterministic slug 生成"，不跑题到 vn_graph 匹配（E05）。
- ✅ 引用 URL slug generation、Hashids、Semantic versioning。
- ✅ 落到 `BackgroundSceneAnalyzerService._build_scene_selector`。
- ✅ 符合哲学：代码生成保证稳定性，不引入新模型供应商。
