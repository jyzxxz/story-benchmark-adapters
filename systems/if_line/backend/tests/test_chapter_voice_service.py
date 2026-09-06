"""
P2.1 — chapter_voice_service 单元测试。

覆盖：
- TTS 关闭 / 章节不存在 → 返回空 list，不调 LLM
- LLM 返回正常 lines → 解析成 VoiceLine（dialogue / narration）
- LLM 返回未知 speaker → 该行降级为 narration
- LLM 返回相同 text 多次 → dedup
- LLM 失败 → fallback 段落切分
- API key 缺失 → fallback 段落切分
- 缓存命中 → 不再调 LLM
- _character_id 与 prompt_builder_service.generate_character_id 一致（保证下游 Asset.character_id 对齐）
- TTS 合成成功 → 落库 Asset(asset_type="voice_line", ...)
- TTS 合成失败 → line.error 填充，audio_url=None
- 已存在 Asset（同 chapter + character + text）→ 直接复用，不再合成
- _split_long 把超长旁白切到 <=200 字
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import chapter_voice_service as cvs
from app.services.chapter_voice_service import (
    ChapterVoiceService,
    VoiceLine,
)
from app.core.errors import AppError
from app.services.prompt_builder_service import prompt_builder_service


# ----------------------- fixtures -----------------------

STORY_BIBLE = {
    "worldview": "修真世界",
    "characters": [
        {"name": "林夜", "appearance": "冷峻青年", "voice": "冷峻青年男声"},
        {"name": "苏婉", "appearance": "白衣少女", "voice": "柔弱少女女声"},
    ],
}

LONG_CHAPTER = (
    "林夜踏入大殿，目光如刀。\n"
    "苏婉站在他身后，轻声说：「公子小心，殿内有埋伏。」\n"
    "林夜回头，淡淡道：「我知道。」\n"
    "殿外风雷骤起，一道剑光破空而来。\n"
) * 20  # >200 字


def _fake_llm_response(lines_payload):
    """构造 mock LLM client，返回 {"lines": [...]} JSON。"""
    fake_choice = MagicMock()
    fake_choice.message.content = json.dumps({"lines": lines_payload}, ensure_ascii=False)
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)
    return fake_client


def _fake_failing_llm():
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("network down"))
    return fake_client


@pytest.fixture
def service(tmp_path, monkeypatch):
    """干净 service：CACHE_DIR 重定向 tmp_path，DB / chapter content 走 mock。"""
    monkeypatch.setattr(cvs, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_ENABLED", True)
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_API_KEY", "sk-test-key")
    monkeypatch.setattr("app.services.tts_service.TTS_ENABLED", True)
    svc = ChapterVoiceService()
    svc._get_chapter_content = lambda pid, ci: LONG_CHAPTER
    svc._get_story_bible_raw = lambda pid: STORY_BIBLE
    return svc


# ----------------------- 早退：TTS 关闭 / 章节不存在 -----------------------

@pytest.mark.asyncio
async def test_tts_disabled_returns_explicit_service_unavailable(service, monkeypatch):
    """TTS_ENABLED=False → 明确返回 feature-disabled，不伪装成空章节。"""
    monkeypatch.setattr("app.services.tts_service.TTS_ENABLED", False)
    with pytest.raises(AppError) as exc_info:
        await service.generate_chapter_voices(project_id=1, chapter_index=1)
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "tts.feature_disabled"


@pytest.mark.asyncio
async def test_no_chapter_content_returns_empty(service):
    """章节不存在 → 返回空。"""
    service._get_chapter_content = lambda pid, ci: None
    out = await service.generate_chapter_voices(project_id=1, chapter_index=99)
    assert out == []


# ----------------------- 正常 LLM 解析 -----------------------

@pytest.mark.asyncio
async def test_llm_returns_lines_parsed(service):
    lines_payload = [
        {"kind": "narration", "text": "林夜踏入大殿，目光如刀。", "speaker_name": "", "emotion": "calm"},
        {"kind": "dialogue", "text": "公子小心，殿内有埋伏。", "speaker_name": "苏婉", "emotion": "fear"},
        {"kind": "dialogue", "text": "我知道。", "speaker_name": "林夜", "emotion": "calm"},
    ]
    fake_client = _fake_llm_response(lines_payload)
    # 跳过 TTS 落库：mock _synthesize_line 直接填 audio_url
    async def fake_synthesize(pid, ci, ln):
        ln.audio_url = f"http://example/{ln.order_index}.mp3"
        ln.asset_id = 1000 + ln.order_index
        ln.cached = False
        return ln
    with patch.object(ChapterVoiceService, "client", new=fake_client), \
         patch.object(service, "_synthesize_line", side_effect=fake_synthesize):
        out = await service.generate_chapter_voices(project_id=1, chapter_index=1)

    assert len(out) == 3
    # 顺序 = 原文顺序
    assert out[0].kind == "narration"
    assert out[1].kind == "dialogue"
    assert out[1].speaker_name == "苏婉"
    assert out[1].character_id == ChapterVoiceService._character_id("苏婉", 1)
    assert out[1].character_voice == "柔弱少女女声"
    assert out[2].speaker_name == "林夜"
    # order_index 自增
    assert [ln.order_index for ln in out] == [0, 1, 2]
    # TTS 阶段填充
    assert all(ln.audio_url for ln in out)


# ----------------------- 未知 speaker 降级 -----------------------

@pytest.mark.asyncio
async def test_unknown_speaker_degrades_to_narration(service):
    """dialogue 但 speaker 不在角色卡 → 降级 narration，speaker_name=None。"""
    lines_payload = [
        {"kind": "dialogue", "text": "你好。", "speaker_name": "神秘路人", "emotion": "happy"},
    ]
    fake_client = _fake_llm_response(lines_payload)
    async def fake_synthesize(pid, ci, ln):
        ln.audio_url = "http://example/x.mp3"
        ln.asset_id = 1
        return ln
    with patch.object(ChapterVoiceService, "client", new=fake_client), \
         patch.object(service, "_synthesize_line", side_effect=fake_synthesize):
        out = await service.generate_chapter_voices(project_id=1, chapter_index=1)

    assert len(out) == 1
    assert out[0].kind == "narration"
    assert out[0].speaker_name is None
    assert out[0].character_id is None


# ----------------------- text 去重 -----------------------

@pytest.mark.asyncio
async def test_duplicate_text_dropped(service):
    """LLM 输出同 text 多次 → 仅保留第一条。"""
    lines_payload = [
        {"kind": "narration", "text": "剑光破空。", "speaker_name": "", "emotion": "calm"},
        {"kind": "narration", "text": "剑光破空。", "speaker_name": "", "emotion": "calm"},
    ]
    fake_client = _fake_llm_response(lines_payload)
    async def fake_synthesize(pid, ci, ln):
        ln.audio_url = "http://example/x.mp3"
        ln.asset_id = 1
        return ln
    with patch.object(ChapterVoiceService, "client", new=fake_client), \
         patch.object(service, "_synthesize_line", side_effect=fake_synthesize):
        out = await service.generate_chapter_voices(project_id=1, chapter_index=1)
    assert len(out) == 1


# ----------------------- LLM 失败 → fallback -----------------------

@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_split(service, monkeypatch):
    """LLM 失败 → 走 _fallback_split（纯 narration 兜底，不切对白）。
    新架构：splitter 已废弃，对白切分完全由 LLM 负责；LLM 失败时不再有可信切分来源，
    fallback 只保证链路不死（按句号切成 narration）。
    """
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_RETRIES", 1)
    fake_client = _fake_failing_llm()
    async def fake_synthesize(pid, ci, ln):
        ln.audio_url = "http://example/x.mp3"
        ln.asset_id = 1
        return ln
    with patch.object(ChapterVoiceService, "client", new=fake_client), \
         patch.object(service, "_synthesize_line", side_effect=fake_synthesize):
        out = await service.generate_chapter_voices(project_id=1, chapter_index=1)

    assert len(out) >= 1
    # 全部应为 narration（对白切分完全交给 LLM 主路径，LLM 失败时不再恢复对白）
    for ln in out:
        assert ln.kind == "narration"


# ----------------------- API key 缺失 → fallback -----------------------

@pytest.mark.asyncio
async def test_no_api_key_uses_fallback(service, monkeypatch):
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_API_KEY", "")
    fake_client = _fake_llm_response([])
    async def fake_synthesize(pid, ci, ln):
        ln.audio_url = "http://example/x.mp3"
        ln.asset_id = 1
        return ln
    with patch.object(ChapterVoiceService, "client", new=fake_client), \
         patch.object(service, "_synthesize_line", side_effect=fake_synthesize):
        out = await service.generate_chapter_voices(project_id=1, chapter_index=1)
    assert fake_client.chat.completions.create.await_count == 0
    assert len(out) >= 1


# ----------------------- 缓存命中 -----------------------

@pytest.mark.asyncio
async def test_cache_hit_skips_llm(service):
    lines_payload = [
        {"kind": "narration", "text": "风雷骤起。", "speaker_name": "", "emotion": "calm"},
    ]
    fake_client = _fake_llm_response(lines_payload)
    async def fake_synthesize(pid, ci, ln):
        ln.audio_url = "http://example/x.mp3"
        ln.asset_id = 1
        return ln
    with patch.object(ChapterVoiceService, "client", new=fake_client), \
         patch.object(service, "_synthesize_line", side_effect=fake_synthesize):
        first = await service.generate_chapter_voices(project_id=1, chapter_index=1)
        assert len(first) == 1
        assert fake_client.chat.completions.create.await_count == 1

        second = await service.generate_chapter_voices(project_id=1, chapter_index=1)
        assert len(second) == 1
        assert fake_client.chat.completions.create.await_count == 1  # 没增加


# ----------------------- _character_id 与 prompt_builder_service 一致 -----------------------

def test_character_id_matches_prompt_builder():
    """P3 装配时 Asset.character_id 要与立绘/关键帧表对齐，必须同一算法。"""
    for name in ["林夜", "苏婉", "Alice", "路人甲"]:
        for pid in [1, 100, 99999]:
            expected = prompt_builder_service.generate_character_id(name, pid)
            actual = ChapterVoiceService._character_id(name, pid)
            assert expected == actual, f"mismatch name={name} pid={pid}"


# ----------------------- _synthesize_line：成功落库 -----------------------

@pytest.mark.asyncio
async def test_synthesize_line_success_persists_asset(service):
    """TTS 成功 → Asset(asset_type='voice_line', ...) 被落库。"""
    line = VoiceLine(
        kind="dialogue", text="公子小心。", order_index=0,
        speaker_name="苏婉", character_id="abc",
        character_voice="柔弱少女", emotion="fear",
    )
    fake_tts = MagicMock()
    fake_tts.synthesize = AsyncMock(return_value={
        "success": True, "audio_url": "/static/tts_cache/abc.mp3",
        "cached": False, "speaker": "xiaoyun", "emotion_prompt": "fear",
    })
    # mock DB：_find_existing_asset 用 .filter().filter().first() 链式调用，
    # 需要让每次 .filter() 返回同一个 query mock 才能正确 stub .first()。
    fake_db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query  # 链式：再次 filter 还是同一 query
    query.first.return_value = None    # 不存在
    fake_db.query.return_value = query

    captured_assets = []
    def fake_add(asset):
        asset.id = 42
        captured_assets.append(asset)
    fake_db.add.side_effect = fake_add
    fake_db.commit = MagicMock()
    fake_db.refresh = MagicMock()

    svc = ChapterVoiceService(db=fake_db)
    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=3, line=line)

    assert result.audio_url == "/static/tts_cache/abc.mp3"
    assert result.asset_id == 42
    assert result.cached is False
    assert len(captured_assets) == 1
    a = captured_assets[0]
    assert a.asset_type == "voice_line"
    assert a.chapter_index == 3
    assert a.project_id == 1
    assert a.prompt == "公子小心。"  # P3 装配按 text 匹配 dialogue
    assert a.image_url == "/static/tts_cache/abc.mp3"
    assert a.target_name == "苏婉"
    assert a.emotion == "fear"
    assert a.status == "completed"


# ----------------------- _synthesize_line：失败填 error -----------------------

@pytest.mark.asyncio
async def test_synthesize_line_failure_sets_error():
    """TTS 失败 → line.error 填充，audio_url=None，不落库。"""
    fake_tts = MagicMock()
    fake_tts.synthesize = AsyncMock(return_value={
        "success": False, "error": "网络故障",
    })
    fake_db = MagicMock()
    query = MagicMock()
    filt = MagicMock()
    filt.first.return_value = None
    query.filter.return_value = filt
    fake_db.query.return_value = query
    fake_db.add = MagicMock()
    fake_db.commit = MagicMock()

    line = VoiceLine(kind="narration", text="旁白一句。", order_index=0)
    svc = ChapterVoiceService(db=fake_db)
    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url is None
    assert result.error == "网络故障"
    fake_db.add.assert_not_called()


# ----------------------- _synthesize_line：复用已有 Asset -----------------------

@pytest.mark.asyncio
async def test_synthesize_line_reuses_existing_asset():
    """同 chapter + character + text 已有 Asset → 不再调 TTS，直接复用。"""
    existing = MagicMock()
    existing.id = 99
    existing.image_url = "/static/tts_cache/old.mp3"

    fake_db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query  # 链式
    query.all.return_value = [existing]
    query.first.return_value = existing
    fake_db.query.return_value = query
    fake_db.add = MagicMock()

    fake_tts = MagicMock()
    fake_tts.synthesize = AsyncMock(return_value={"success": True, "audio_url": "should-not-be-used"})

    line = VoiceLine(
        kind="dialogue", text="同句对白。", order_index=0,
        speaker_name="林夜", character_id="abc",
        character_voice="冷峻", emotion="angry",
    )
    svc = ChapterVoiceService(db=fake_db)
    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url == "/static/tts_cache/old.mp3"
    assert result.asset_id == 99
    assert result.cached is True
    fake_tts.synthesize.assert_not_called()
    fake_db.add.assert_not_called()


# ----------------------- _split_long 切超长旁白 -----------------------

def test_split_long_caps_to_tts_limit():
    # 单一长度源：与 TTS 闸门（TTS_MAX_TEXT_LENGTH，minimax/aliyun 默认 600）同源
    from app.services.chapter_script_ir import SEGMENT_MAX_TEXT_CHARS

    limit = cvs.CHAPTER_VOICE_MAX_TEXT_CHARS
    assert limit == SEGMENT_MAX_TEXT_CHARS
    assert limit >= 200
    long_text = "名字。" * 400  # 1600 字
    svc = ChapterVoiceService(db=MagicMock())
    chunks = svc._split_long(long_text)
    assert chunks
    assert all(len(c) <= limit for c in chunks)
    assert "".join(chunks).replace(" ", "") == long_text.replace(" ", "")


# ----------------------- _slice_chapter 返回 (lines, VoiceSliceReport) -----------------------

@pytest.mark.asyncio
async def test_slice_chapter_returns_tuple_with_degraded_mode_on_timeout(service, monkeypatch):
    """LLM 超时 → ``(lines, report)``，且 report.degraded_mode == "llm_timeout"。

    回归保护：旧版只返回 lines，assembler 无法区分「LLM 成功切出 narration」
    和「LLM 失败回退到 narration」。新版用 tuple + report 携带降级原因。
    """
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_API_KEY", "sk-test-key")
    # _call_llm 永远卡住，触发外层 asyncio.wait_for 超时
    async def _hang(*a, **kw):
        await asyncio.sleep(60)
    with patch.object(service, "_call_llm", side_effect=_hang):
        # 把超时压到极短，避免测试慢
        monkeypatch.setattr(cvs, "CHAPTER_VOICE_TIMEOUT", 0.05)
        lines, slice_report = await service._slice_chapter(
            project_id=1, chapter_index=1,
            chapter_content=LONG_CHAPTER, story_bible=STORY_BIBLE,
        )
    assert isinstance(lines, list)
    assert len(lines) >= 1
    assert slice_report.degraded_mode == "llm_timeout"


@pytest.mark.asyncio
async def test_slice_chapter_success_report_counts_dialogue_vs_narration(service):
    """LLM 正常返回 → report.degraded_mode is None 且 dialogue+narration 等于 len(lines)。"""
    lines_payload = [
        {"kind": "narration", "text": "林夜踏入大殿，目光如刀。", "speaker_name": "", "emotion": "calm"},
        {"kind": "dialogue", "text": "公子小心。", "speaker_name": "苏婉", "emotion": "fear"},
        {"kind": "dialogue", "text": "我知道。", "speaker_name": "林夜", "emotion": "calm"},
    ]
    fake_client = _fake_llm_response(lines_payload)
    with patch.object(ChapterVoiceService, "client", new=fake_client):
        lines, slice_report = await service._slice_chapter(
            project_id=1, chapter_index=1,
            chapter_content=LONG_CHAPTER, story_bible=STORY_BIBLE,
        )
    assert slice_report.degraded_mode is None
    assert slice_report.dialogue_count + slice_report.narration_count == len(lines)
    assert slice_report.dialogue_count == 2
    assert slice_report.narration_count == 1


@pytest.mark.asyncio
async def test_slice_chapter_no_api_key_reports_no_api_key_mode(service, monkeypatch):
    """无 API key → report.degraded_mode == "no_api_key"。"""
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_API_KEY", "")
    fake_client = _fake_llm_response([])
    with patch.object(ChapterVoiceService, "client", new=fake_client):
        lines, slice_report = await service._slice_chapter(
            project_id=1, chapter_index=1,
            chapter_content=LONG_CHAPTER, story_bible=STORY_BIBLE,
        )
    assert slice_report.degraded_mode == "no_api_key"
    assert fake_client.chat.completions.create.await_count == 0


# ----------------------- regression: 418 fallback + transient classification -----------------------

def test_is_transient_tts_error_excludes_voice_invalid():
    """418 / vcn 错误不该被当成 transient —— 原音色重试无意义。

    回归保护：曾经把 418 误判为非 transient 但没显式分支，导致 _synthesize_line
    永远走 fallback。本测试锁定 transient / voice-invalid 的边界。
    """
    assert cvs._is_transient_tts_error("timeout") is True
    assert cvs._is_transient_tts_error("429 too many requests") is True
    assert cvs._is_transient_tts_error("503 service unavailable") is True
    # 418 / vcn 必须被排除
    assert cvs._is_transient_tts_error("[400] [cosyvoice:]Engine return error code: 418") is False
    assert cvs._is_transient_tts_error("voice not found: longyuyan_v3") is False


def test_is_voice_invalid_error_detects_418():
    """418 / vcn 错误必须被识别为音色失效，从而触发 fallback。"""
    assert cvs._is_voice_invalid_error("[cosyvoice:]Engine return error code: 418") is True
    assert cvs._is_voice_invalid_error("voice not found: longyuyan_v3") is True
    assert cvs._is_voice_invalid_error("timeout") is False
    assert cvs._is_voice_invalid_error(None) is False


def test_fallback_speaker_for_error_returns_default_on_418():
    """418 → 默认旁白音色；其他错误 → None（不触发 fallback）。"""
    # 通过 patch TTS_DEFAULT_SPEAKER 锁定 fallback 目标
    import app.services.tts_service as tts_mod
    original = getattr(tts_mod, "TTS_DEFAULT_SPEAKER", None)
    tts_mod.TTS_DEFAULT_SPEAKER = "longshuo_v3"
    try:
        assert ChapterVoiceService._fallback_speaker_for_error(
            "[cosyvoice:]Engine return error code: 418"
        ) == "longshuo_v3"
        assert ChapterVoiceService._fallback_speaker_for_error("timeout") is None
        assert ChapterVoiceService._fallback_speaker_for_error(None) is None
    finally:
        if original is not None:
            tts_mod.TTS_DEFAULT_SPEAKER = original


# ============================================================
# MiniMax fallback 集成（章节配音层）
# ============================================================
#
# 测试 ChapterVoiceService._synthesize_line 的 fallback 行为：
# - 默认（fallback 关闭）：主供应商失败 → 不调 minimax，直接透传错误
# - fallback 开启 + 可兜底错误（429/5xx/timeout/voice_invalid）：
#     主供应商重试耗尽后，单次 force_engine="minimax" 调用
# - minimax 成功 → 落库正常 Asset
# - minimax 失败 → 合并错误到 line.error；再尝试 voice-fallback
# - 单句失败不阻断整章：每条 line 独立，错误只影响自己


def _make_db_mock_for_persist():
    """构造一个允许 _persist_voice_asset 落库成功的 fake db。

    _persist_voice_asset 会按 (project, chapter, order, character) 查 stale asset；
    fake_db.query(...).filter(...).first() 必须返回 None 才会走新建分支。
    """
    fake_db = MagicMock()
    # _find_existing_asset 的查询与 _persist 的查询都走 query().filter().first()
    # 统一返回 None（无 stale，无 existing）→ 走新建 Asset
    fake_db.query.return_value.filter.return_value.first.return_value = None
    fake_db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    fake_db.add = MagicMock()
    fake_db.commit = MagicMock()
    fake_db.refresh = MagicMock()
    return fake_db


@pytest.mark.asyncio
async def test_synthesize_line_no_fallback_when_global_disabled(monkeypatch):
    """fallback 全局开关关闭（默认）时，主供应商失败直接返回错误，不调 minimax。"""
    import app.services.tts_service as tts_mod
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", False)
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)  # 加速：不重试

    fake_tts = MagicMock()
    fake_tts.synthesize = AsyncMock(return_value={
        "success": False, "error": "aliyun 429 too many requests", "engine": "aliyun",
    })
    fake_tts.output_dir = "/tmp/tts"
    fake_db = _make_db_mock_for_persist()

    line = VoiceLine(kind="narration", text="测试一句。", order_index=0)
    svc = ChapterVoiceService(db=fake_db)

    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url is None
    assert "429" in (result.error or "")
    # 只调了主供应商，没调 minimax
    assert fake_tts.synthesize.call_count == 1
    kwargs = fake_tts.synthesize.call_args.kwargs
    assert kwargs.get("allow_fallback") is False


@pytest.mark.asyncio
async def test_synthesize_line_passes_minimax_primary_binding_slot(monkeypatch):
    """章节主链路必须把角色的 MiniMax 独占槽传给 TTS dispatcher。"""
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)
    binding = MagicMock(
        vcn="legacy-provider-voice",
        emotion="happy",
        speed=50,
        pitch=50,
        volume=50,
        minimax_voice_id="female-shaonv",
        minimax_speed=52,
        minimax_pitch=54,
        minimax_volume=50,
    )
    fake_tts = MagicMock()
    fake_tts.synthesize = AsyncMock(return_value={
        "success": True,
        "audio_url": "/static/tts_cache/minimax-primary.mp3",
        "engine": "minimax",
    })
    fake_db = _make_db_mock_for_persist()
    line = VoiceLine(
        kind="dialogue",
        text="公子小心。",
        order_index=0,
        speaker_name="苏婉",
        character_id="suwan",
        character_voice="温柔少女女声",
        emotion="happy",
        gender="female",
        age="young",
    )

    with patch(
        "app.services.voice_binding_service.VoiceBindingService.get_or_allocate",
        return_value=binding,
    ), patch("app.services.tts_service.tts_service", fake_tts):
        result = await ChapterVoiceService(db=fake_db)._synthesize_line(
            project_id=1,
            chapter_index=1,
            line=line,
        )

    assert result.audio_url == "/static/tts_cache/minimax-primary.mp3"
    call = fake_tts.synthesize.await_args.kwargs
    assert call["minimax_voice_id"] == "female-shaonv"
    assert call["minimax_speed"] == 52
    assert call["minimax_pitch"] == 54
    assert call["minimax_volume"] == 50


@pytest.mark.asyncio
async def test_synthesize_line_minimax_fallback_invoked_on_429(monkeypatch):
    """fallback 开启 + 429 错误 → 重试耗尽后调一次 force_engine='minimax'。"""
    import app.services.tts_service as tts_mod
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)  # 加速：不重试

    fake_tts = MagicMock()
    fake_tts.output_dir = "/tmp/tts"

    primary_result = {"success": False, "error": "aliyun 429 too many requests", "engine": "aliyun"}
    minimax_result = {
        "success": True,
        "audio_url": "/static/tts_cache/mm_xxx.mp3",
        "engine": "minimax",
        "model": "speech-2.8-hd",
        "fallback_used": True,
        "primary_engine": "aliyun",
        "primary_error": "aliyun 429 too many requests",
    }
    fake_tts.synthesize = AsyncMock(side_effect=[primary_result, minimax_result])
    fake_db = _make_db_mock_for_persist()

    line = VoiceLine(kind="narration", text="测试一句。", order_index=0)
    svc = ChapterVoiceService(db=fake_db)

    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url == "/static/tts_cache/mm_xxx.mp3"
    assert result.error is None
    # 两次调用：主供应商一次 + minimax 一次
    assert fake_tts.synthesize.call_count == 2
    # 第二次必须是 force_engine="minimax"
    second_call_kwargs = fake_tts.synthesize.call_args_list[1].kwargs
    assert second_call_kwargs.get("force_engine") == "minimax"
    assert second_call_kwargs.get("allow_fallback") is False


@pytest.mark.asyncio
async def test_synthesize_line_minimax_failure_merges_errors(monkeypatch):
    """fallback 开启 + minimax 也失败 → line.error 合并两层错误。"""
    import app.services.tts_service as tts_mod
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)

    fake_tts = MagicMock()
    fake_tts.output_dir = "/tmp/tts"
    primary_result = {"success": False, "error": "aliyun 429 too many requests", "engine": "aliyun"}
    minimax_result = {"success": False, "error": "minimax 500 internal", "engine": "minimax"}
    fake_tts.synthesize = AsyncMock(side_effect=[primary_result, minimax_result])
    fake_db = _make_db_mock_for_persist()

    line = VoiceLine(kind="narration", text="测试一句。", order_index=0)
    svc = ChapterVoiceService(db=fake_db)

    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url is None
    # 错误里既有 primary 又有 fallback=minimax
    err = result.error or ""
    assert "primary" in err.lower() or "aliyun" in err.lower()
    assert "minimax" in err.lower()


@pytest.mark.asyncio
async def test_synthesize_line_non_fallback_error_skips_minimax(monkeypatch):
    """fallback 开启但错误类型不在 allowed 范围（文本超长）→ 不调 minimax。"""
    import app.services.tts_service as tts_mod
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)

    fake_tts = MagicMock()
    fake_tts.output_dir = "/tmp/tts"
    fake_tts.synthesize = AsyncMock(return_value={
        "success": False, "error": "文本长度超过限制（最大 600 字）", "engine": "aliyun",
    })
    fake_db = _make_db_mock_for_persist()

    line = VoiceLine(kind="narration", text="x" * 800, order_index=0)
    svc = ChapterVoiceService(db=fake_db)

    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url is None
    # 只调主供应商一次，没有 minimax 调用
    assert fake_tts.synthesize.call_count == 1


@pytest.mark.asyncio
async def test_synthesize_line_voice_invalid_falls_back_to_minimax(monkeypatch):
    """voice_invalid（418）→ fallback 开启时优先尝试 minimax（不切默认 aliyun 音色）。"""
    import app.services.tts_service as tts_mod
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)

    fake_tts = MagicMock()
    fake_tts.output_dir = "/tmp/tts"
    primary_result = {
        "success": False,
        "error": "[418] CosyVoice voice not found: longshuo_v3",
        "engine": "aliyun",
    }
    minimax_result = {
        "success": True,
        "audio_url": "/static/tts_cache/mm_yyy.mp3",
        "engine": "minimax",
    }
    fake_tts.synthesize = AsyncMock(side_effect=[primary_result, minimax_result])
    fake_db = _make_db_mock_for_persist()

    line = VoiceLine(
        kind="dialogue", text="对白一句。", order_index=0,
        speaker_name="林夜", character_id="abc",
        character_voice="冷峻", emotion="angry",
    )
    svc = ChapterVoiceService(db=fake_db)

    with patch("app.services.tts_service.tts_service", fake_tts):
        result = await svc._synthesize_line(project_id=1, chapter_index=1, line=line)

    assert result.audio_url == "/static/tts_cache/mm_yyy.mp3"
    # 调了主 + minimax；没再调 voice-fallback（minimax 成功了）
    assert fake_tts.synthesize.call_count == 2


@pytest.mark.asyncio
async def test_synthesize_line_single_line_failure_does_not_block(monkeypatch):
    """单条 line 合成失败不影响其他 line（每条独立）。"""
    import app.services.tts_service as tts_mod
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", False)  # 默认行为
    monkeypatch.setattr(cvs, "CHAPTER_VOICE_TTS_RETRIES", 0)

    fake_tts = MagicMock()
    fake_tts.output_dir = "/tmp/tts"
    # 第一次失败，第二次成功
    fake_tts.synthesize = AsyncMock(side_effect=[
        {"success": False, "error": "网络异常", "engine": "aliyun"},
        {"success": True, "audio_url": "/static/tts_cache/ok.mp3", "engine": "aliyun"},
    ])
    fake_db = _make_db_mock_for_persist()

    line1 = VoiceLine(kind="narration", text="第一句。", order_index=0)
    line2 = VoiceLine(kind="narration", text="第二句。", order_index=1)
    svc = ChapterVoiceService(db=fake_db)

    with patch("app.services.tts_service.tts_service", fake_tts):
        r1 = await svc._synthesize_line(project_id=1, chapter_index=1, line=line1)
        r2 = await svc._synthesize_line(project_id=1, chapter_index=1, line=line2)

    assert r1.audio_url is None
    assert "网络异常" in (r1.error or "")
    assert r2.audio_url == "/static/tts_cache/ok.mp3"
    assert r2.error is None


def test_slice_chapter_uses_script_ir_segments_as_single_source():
    """script_ir 存在时跳过 LLM 切片，语音行与 IR paragraphs 同粒度同文本。"""
    import asyncio

    script_ir = {
        "schema_version": "script-ir-v3",
        "paragraphs": [
            {"kind": "narration", "text": "他说：", "emotion": "neutral"},
            {
                "kind": "dialogue",
                "text": "“走吧。”",
                "speaker_name": "阿宁",
                "speaker_character_id": "character-an",
                "emotion": "tense",
            },
            {"kind": "narration", "text": "她点点头。", "emotion": "neutral"},
        ],
    }
    story_bible = {"characters": [{"name": "阿宁", "voice": "v-1"}]}
    svc = ChapterVoiceService(db=MagicMock())

    async def run():
        return await svc._slice_chapter(
            project_id=1,
            chapter_index=1,
            chapter_content="他说：“走吧。”她点点头。",
            story_bible=story_bible,
            script_ir=script_ir,
        )

    lines, report = asyncio.run(run())
    assert [ln.text for ln in lines] == ["他说：", "“走吧。”", "她点点头。"]
    assert lines[1].is_dialogue()
    assert lines[1].speaker_name == "阿宁"
    assert lines[1].character_id == "character-an"
    assert report.degraded_mode is None


def test_deterministic_voice_specs_prefers_script_ir():
    """voice_line_service：script_ir 直驱 specs（不触发 asyncio/LLM）。"""
    from types import SimpleNamespace

    from app.application.voice_line_service import deterministic_voice_specs

    script_ir = {
        "paragraphs": [
            {"kind": "narration", "text": "旁白一句。", "emotion": "calm"},
            {
                "kind": "dialogue",
                "text": "“你好。”",
                "speaker_name": "阿宁",
                "speaker_character_id": "character-an",
                "emotion": "happy",
            },
        ]
    }
    chapter = SimpleNamespace(
        project_id=1, index=1, content="旁白一句。“你好。”"
    )
    bible = SimpleNamespace(content_json={"characters": [{"name": "阿宁"}]})
    specs = deterministic_voice_specs(chapter, bible, script_ir)
    assert [(s.kind, s.text) for s in specs] == [
        ("narration", "旁白一句。"),
        ("dialogue", "“你好。”"),
    ]
    assert specs[1].speaker_character_id == "character-an"
    assert specs[1].degraded_mode is None
