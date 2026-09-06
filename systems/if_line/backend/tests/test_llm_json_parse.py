"""Tests for app.services.llm_json_parser JSON extraction / repair / parsing.

These three pure functions are the guardrail between raw LLM output and the
structured pydantic schemas. LLM output is messy: markdown fences, full-width
quotes “ ”, bare control chars inside strings, trailing commas, and — the
known issue #1 — truncation when ``finish_reason="length"``. These tests pin
the current contract so regressions are caught without burning API budget
(everything here is deterministic, no network).
"""
import json

import pytest

from app.services.llm_json_parser import (
    _extract_json_block,
    _repair_json_text,
    parse_llm_json,
)


# ---------------------------------------------------------------- _extract_json_block

class TestExtractJsonBlock:
    def test_plain_object_returned_verbatim(self):
        s = '{"a": 1, "b": [2, 3]}'
        assert _extract_json_block(s) == s

    def test_strips_markdown_fence_and_surrounding_prose(self):
        s = 'Sure! Here you go:\n```json\n{"title": "雨夜"}\n```\nthanks'
        assert json.loads(_extract_json_block(s)) == {"title": "雨夜"}

    def test_extracts_array_with_prose_around_it(self):
        s = 'prefix text [1, 2, {"x": 3}] trailing'
        assert json.loads(_extract_json_block(s)) == [1, 2, {"x": 3}]

    def test_no_delimiters_returns_text_as_is(self):
        assert _extract_json_block("just prose, no json") == "just prose, no json"

    def test_braces_inside_strings_do_not_affect_depth(self):
        # A brace inside a string literal must not change brace-depth tracking.
        s = '{"code": "func() { return 1; }", "ok": true}'
        assert json.loads(_extract_json_block(s)) == json.loads(s)

    def test_quotes_inside_strings_do_not_early_close(self):
        s = '{"msg": "she said \\"hi\\" } not closed", "n": 1}'
        assert json.loads(_extract_json_block(s)) == json.loads(s)


# ---------------------------------------------------------------- _repair_json_text

class TestRepairJsonText:
    def test_full_width_quotes_normalized(self):
        # “ ” (U+201C / U+201D) must be normalized to ASCII ".
        s = '{"name": “林夏”}'
        assert json.loads(_repair_json_text(s)) == {"name": "林夏"}

    def test_bare_newline_inside_string_is_escaped(self):
        content = '{"a": "line1' + chr(10) + 'line2"}'  # literal newline
        assert json.loads(_repair_json_text(content)) == {"a": "line1\nline2"}

    def test_bare_tab_and_cr_inside_string_are_escaped(self):
        content = '{"a": "x' + chr(9) + 'y' + chr(13) + 'z"}'
        assert json.loads(_repair_json_text(content)) == {"a": "x\ty\rz"}

    def test_trailing_comma_in_object_removed(self):
        assert json.loads(_repair_json_text('{"a": 1, "b": 2,}')) == {"a": 1, "b": 2}

    def test_trailing_comma_in_array_removed(self):
        assert json.loads(_repair_json_text('[1, 2, 3,]')) == [1, 2, 3]

    def test_nbsp_normalized_to_space(self):
        assert json.loads(_repair_json_text('{"a": "x y"}')) == {"a": "x y"}


# ---------------------------------------------------------------- parse_llm_json

class TestParseLlmJson:
    def test_valid_json_round_trips(self):
        assert parse_llm_json('{"k": "v", "n": 3}') == {"k": "v", "n": 3}

    def test_json_embedded_in_markdown(self):
        assert parse_llm_json('```json\n{"k": 1}\n```') == {"k": 1}

    def test_json_after_prose(self):
        assert parse_llm_json('好的，结果是：{"k": "v"}') == {"k": "v"}

    def test_repairs_full_width_quotes(self):
        assert parse_llm_json('{"k": “值”}') == {"k": "值"}

    def test_repairs_trailing_comma(self):
        assert parse_llm_json('{"a": 1,}') == {"a": 1}

    def test_repairs_control_char_inside_string(self):
        content = '{"a": "x' + chr(10) + 'y"}'  # raw newline → control-char error
        assert parse_llm_json(content) == {"a": "x\ny"}

    def test_none_input_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_llm_json(None)

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("   \n  ")

    def test_truncated_output_currently_raises(self):
        """KNOWN ISSUE #1 (Critical): when the LLM hits ``finish_reason="length"``
        the output is cut mid-string. ``parse_llm_json`` cannot reconstruct the
        missing tail, so after three rescue attempts it raises — and the caller
        drops the whole chapter/outline generation.

        The *proper* fix lives at the caller (detect ``finish_reason="length"``
        and retry with a larger ``max_tokens`` / request a continuation), not in
        this function: it never sees the finish reason, only the broken text.

        This test pins the CURRENT behavior so we notice if it silently changes
        (e.g. starts returning a half-empty dict), and to keep issue #1 visible
        in the suite.
        """
        truncated = '{"title":"雨夜咖啡馆","scene":"室内","characters":["林夏","老'
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json(truncated)
