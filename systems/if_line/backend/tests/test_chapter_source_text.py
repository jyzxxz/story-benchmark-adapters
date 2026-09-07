from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.application.hashing import content_hash
from app.services.chapter_source_text import (
    CHAPTER_TEXT_PROJECTION_VERSION,
    chapter_display_text,
    chapter_text_units,
)
from app.services.chapter_script_ir import (
    SEGMENT_MAX_TEXT_CHARS,
    ScriptIRValidationError,
    build_script_ir_from_segments,
    validate_script_ir,
)


def compact(text):
    return "".join(ch for ch in text if not ch.isspace())


def build(source, texts):
    return build_script_ir_from_segments(
        chapter_revision_id="raw-chapter", chapter_content=source,
        chapter_content_hash=content_hash(source), bible_revision_id="b",
        outline_revision_id="o", characters=[],
        llm_output={"segments": [{"text": text} for text in texts]},
    )


@pytest.mark.parametrize("source,visible", [
    ("  雨落在站台。\n阿宁说：", "  雨落在站台。\n阿宁说："),
    ("气味。</p><p>“你闻到了吗？”", "气味。\n\n“你闻到了吗？”"),
    ('<P class="story" title="1 > 0">夜<strong>雨<em>停</em></strong>了。<br/>走。</P>', '\n夜雨停了。\n走。\n'),
    ("x < y，2 > 1；a <pencil> b", "x < y，2 > 1；a <pencil> b"),
    ("<p>&lt;p&gt; &amp;lt;p&amp;gt; &#x3c;b&#62;</p>", "\n<p> &lt;p&gt; <b>\n"),
    ("&quot;走吧。&quot;&nbsp;&amp; &#x1F319;", '"走吧。"\xa0& 🌙'),
    ("<secret>不能吞</secret>", "<secret>不能吞</secret>"),
    ("<!--<p>不能吞</p>-->", "<!--<p>不能吞</p>-->"),
    ("<script><p>不能吞</p>&amp;</script>", "<script><p>不能吞</p>&amp;</script>"),
    ("<img alt='实质文字' />", "<img alt='实质文字' />"),
    ("<p title='broken>文字", "<p title='broken>文字"),
    ("&unknown; &amp &#1;", "&unknown; &amp &#1;"),
])
def test_projection_and_exact_raw_reconstruction(source, visible):
    assert chapter_display_text(source) == visible
    ir = build(source, [compact(visible)])
    assert "".join(span["text"] for span in ir["spans"]) == source
    assert ir["source"] == {
        "content_hash": content_hash(source), "character_count": len(source),
        "text_projection": CHAPTER_TEXT_PROJECTION_VERSION,
    }
    assert "".join(p["text"] for p in ir["paragraphs"]) == compact(visible)
    assert validate_script_ir(ir, expected_content=source)["coverage_ratio"] == 1


def test_inline_tags_and_entities_keep_original_coordinates_across_segments():
    source = '<p>开<strong>灯&amp;火</strong>关。</p>'
    ir = build(source, ["开灯", "&火关。"])
    for paragraph in ir["paragraphs"]:
        assert source[paragraph["source_start"]:paragraph["source_end"]] == paragraph["source_text"]
    assert ir["paragraphs"][0]["source_text"] == "开<strong>灯"
    assert ir["paragraphs"][1]["source_text"] == "&amp;火</strong>关。"
    entity = [unit for unit in chapter_text_units(source) if unit[0] == "&"]
    assert entity == [("&", source.index("&amp;"), source.index("&amp;") + 5)]


@pytest.mark.parametrize("texts", [
    ["气味。", "你闻到了吗？"],  # altered punctuation
    ["气味。"],  # omission
    ["“你闻到了吗？”", "气味。"],  # reordered
    ["气味。", "“你闻到了吗？”", "气味。"],  # duplicate
    ["气味。</p><p>“你闻到了吗？”"],  # markup is not spoken prose
])
def test_rewrites_gaps_reorder_duplicates_and_markup_are_rejected(texts):
    with pytest.raises(ScriptIRValidationError):
        build("气味。</p><p>“你闻到了吗？”", texts)


@pytest.mark.parametrize("text", ["", "文字", "<p>文字</p>", "&amp;lt;p&amp;gt;文字&amp;lt;/p&amp;gt;"])
def test_escaped_markup_is_not_parsed_or_decoded_twice(text):
    source = "&amp;lt;p&amp;gt;文字&amp;lt;/p&amp;gt;"
    if text:
        with pytest.raises(ScriptIRValidationError):
            build(source, [text])
    else:
        ir = build(source, ["&lt;p&gt;文字&lt;/p&gt;"])
        assert ir["paragraphs"][0]["source_text"] == source


def test_multi_character_entity_stays_atomic_in_length_fuse():
    source = "<p>" + "字" * (SEGMENT_MAX_TEXT_CHARS - 1) + "&fjlig;尾声。</p>"
    visible = "字" * (SEGMENT_MAX_TEXT_CHARS - 1) + "fj尾声。"
    ir = build(source, [visible])
    assert [len(p["text"]) for p in ir["paragraphs"]] == [SEGMENT_MAX_TEXT_CHARS - 1, 5]
    assert "".join(p["text"] for p in ir["paragraphs"]) == visible
    assert "".join(s["text"] for s in ir["spans"]) == source
    with pytest.raises(ScriptIRValidationError, match="实体"):
        build("&fjlig;", ["f", "j"])


@pytest.mark.parametrize("mutation", ["text", "order", "offset", "source_text", "separator", "count", "span_id"])
def test_revalidation_cannot_hide_or_rewrite_visible_source(mutation):
    source = "<p>不能吞。</p><p>后续。</p>"
    ir = deepcopy(build(source, ["不能吞。", "后续。"]))
    if mutation == "text":
        ir["paragraphs"][0]["text"] = "改写。"
    elif mutation == "order":
        ir["paragraphs"].reverse()
    elif mutation == "offset":
        ir["paragraphs"][0]["source_start"] += 1
    elif mutation == "source_text":
        ir["paragraphs"][0]["source_text"] = "假来源"
    elif mutation == "separator":
        span = next(s for s in ir["spans"] if s["kind"] == "paragraph")
        span["kind"] = "separator"
        ir["paragraphs"].pop(0)
        ir["paragraphs"][0]["order_index"] = 0
    elif mutation == "count":
        ir["source"]["character_count"] += 1
    else:
        ir["spans"][0]["span_id"] = ir["spans"][1]["span_id"]
    with pytest.raises(ScriptIRValidationError):
        validate_script_ir(ir, expected_content=source)


@pytest.mark.asyncio
async def test_real_llm_adapter_receives_same_projection_without_mutating_request(monkeypatch):
    from app.integrations.llm import chapter_script_adapter as module
    source = '<p>“走吧。”</p><p>&lt;p&gt; &amp;lt;b&amp;gt; x < y</p>'
    captured = []
    payload = {"segments": [{"text": compact(chapter_display_text(source))}]}

    async def fake_create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            id="local-no-provider", usage=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(payload)))],
        )

    monkeypatch.setattr(module, "llm_service", SimpleNamespace(
        model="fixture-model", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))),
    ))
    request = module.ChapterScriptAnnotationRequest(
        project_id=1, chapter_index=1, bible={}, outline_chapter={},
        chapter_content=source, characters=[],
    )
    result = await module.LegacyChapterScriptLLMAdapter().generate(request)
    sent = json.loads(captured[0]["messages"][1]["content"].split("INPUT:\n", 1)[1])
    assert sent["chapter_content"] == chapter_display_text(source)
    assert sent["chapter_content_projection"] == CHAPTER_TEXT_PROJECTION_VERSION
    assert request.chapter_content == source
    assert result.raw_text == json.dumps(payload)
    assert len(captured) == 1
    ir = build(source, [s["text"] for s in result.data["segments"]])
    assert ir["coverage"]["content_hash"] == content_hash(source)


def test_vn_graph_uses_display_text_and_keeps_raw_source_anchors():
    from app.services.vn_graph_compiler import VNGraphCompileInput, vn_graph_compiler
    source = '<p>开<strong>灯&amp;火</strong>关。</p><p>&lt;p&gt;</p>'
    ir = build(source, ["开灯&火关。", "<p>"])
    result = vn_graph_compiler.compile(VNGraphCompileInput(
        project_id=1, display_index=1, chapter_revision_id="raw-chapter",
        chapter_content_hash=content_hash(source), chapter_content=source,
        script_revision_id="script", script_hash=content_hash(ir), script_ir=ir,
        asset_bindings=[],
    ))
    nodes = [node for node in result.graph["Nodes"] if node.get("paragraph_id")]
    assert len(nodes) == len(ir["paragraphs"])
    for node, paragraph in zip(nodes, ir["paragraphs"]):
        line = node["Data"]["Lines"]["Items"][0]["ObjectValue"]
        assert line["Text"]["StringValue"] == paragraph["text"]
        assert line["SourceText"]["StringValue"] == paragraph["source_text"]
        assert line["SourceStart"]["NumberValue"] == paragraph["source_start"]
        assert line["SourceEnd"]["NumberValue"] == paragraph["source_end"]
