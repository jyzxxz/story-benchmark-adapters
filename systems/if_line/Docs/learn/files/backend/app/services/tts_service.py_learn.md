# tts_service.py — learn note

> Source: `backend/app/services/tts_service.py` (429 LOC)
> Route: `high_reasoning` | Reuse target: **P2 chapter_voice_service 直接调，不重写**
> Status: `[_]` → master-validated `[x]`

## 职责
阿里云语音合成（voice）引擎封装。输入文本 + 角色音色信息，输出音频 URL，带缓存。

## ⭐ 核心复用件：`synthesize(...)`

**P2 `chapter_voice_service` 直接调这个方法，绝不重写**。签名（参考 `routers/tts.py` 调用）：

```python
async def synthesize(
    text: str,
    character_name: Optional[str] = None,
    character_voice: Optional[str] = None,   # Story Bible 角色卡里的 voice 字段
    gender: Optional[str] = None,             # male/female
    age: Optional[str] = None,                # young/middle/elderly
    emotion: Optional[str] = None,            # calm/happy/sad/angry/excited
    speaker: Optional[str] = None,            # 直接指定阿里云音色 ID
    emotion_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    # 返回 {"success": bool, "audio_url": str, "cached": bool, "speaker": str, "emotion_prompt": str, "error": str|None}
```

## P2 调用模式

`chapter_voice_service` 切出 `VoiceLine` 后，**并发**调 `synthesize`：

```python
import asyncio
tasks = [
    tts_service.synthesize(
        text=line.text,
        character_name=line.speaker_name,
        character_voice=line.character_voice,  # 从 Story Bible 角色卡查
        emotion=line.emotion,
    )
    for line in voice_lines
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

并发上限参考 `image_generation_service` 的 batch_size（默认 5）。

## ⭐ 音色卡来源
`StoryBible.characters[i].voice` —— 在 Story Bible 生成阶段由 LLM 写好的阿里云音色字段。P2 不需要自己推断音色，从 `bible.characters` 查表即可。

## ⭐ 缓存
内部带音频缓存（hash text + speaker + emotion），重复调用免费。**P2 章节重新配音时**相同对白会命中缓存，不会重复扣费。

## env / 状态
- `TTS_ENABLED`、`TTS_ENGINE` 暴露在 `routers/tts.py:/status`。P2 章节配音接口开头要 check `TTS_ENABLED`，未启用直接 503，参考 `image_generation.py:108-110` 的写法。

## worker 提示
- 不要改 `tts_service.py`，只调。
- VoiceLine 落库时 `Asset.image_url` 字段存 audio_url（虽然字段名是 image_url，但 Asset 表本来就是统一资源表）。
- `Asset.asset_type` 新增 `"voice_line"` 取值；`Asset.prompt` 存原文 text，便于 P3 装配时按 text 匹配回 vngraph dialogue 节点。
