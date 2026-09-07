"""Read-only comparison of Writer choices with the native exported boundary."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


def compare_choices(writer_choices: list, exported_choices: list) -> dict:
    """Model only the frozen director's filter/id/dedup, never repair output."""
    valid, filtered, deduplicated = [], [], []
    for index, choice in enumerate(writer_choices):
        if not isinstance(choice, dict):
            return {"status": "unknown_nonobject_writer_choice", "writer_choice_count": len(writer_choices)}
        label, effect = choice.get("label"), choice.get("effect")
        if not isinstance(label, str) or not label:
            filtered.append({"writer_index": index, "reason": "empty_or_nonstring_label"})
        elif not isinstance(effect, dict) or effect.get("kind") != "change-scene":
            filtered.append({"writer_index": index, "reason": "not_change_scene"})
        else:
            valid.append((index, deepcopy(choice)))
    expected, seen = [], set()
    for position, (index, choice) in enumerate(valid):
        # Native assigns missing ids before label deduplication.
        choice["id"] = choice.get("id") or f"sc{position + 1}"
        if choice["label"] in seen:
            deduplicated.append({"writer_index": index, "reason": "duplicate_label"})
            continue
        seen.add(choice["label"])
        expected.append(choice)
    actual = [{k: deepcopy(v) for k, v in choice.items()
               if k not in ("native_source", "native_pointer")} for choice in exported_choices]
    matched = bool(expected) and expected == actual
    return {
        "status": "matches_frozen_native_normalization" if matched else "difference_requires_review",
        "frozen_normalization_source": "lib/engine/director.ts:487-525",
        "writer_choice_count": len(writer_choices), "exported_choice_count": len(actual),
        "writer_choices": deepcopy(writer_choices), "exported_choices": actual,
        "predicted_native_filter_rejections": filtered,
        "predicted_native_label_deduplications": deduplicated,
        "matches_frozen_filter_id_and_dedup": matched,
        "native_dropped_writer_indexes_confirmed_by_exact_match":
            [row["writer_index"] for row in filtered + deduplicated] if matched else None,
        "scope": "first_exported_choice_boundary; earlier native choice or fallback requires separate review",
        "task_choice_semantics_evaluated": False,
        "output_modified": False,
    }


def _writer_text(raw: str, streaming: bool) -> str:
    if not streaming:
        response = json.loads(raw)
        return response["choices"][0]["message"]["content"]
    parts, data_lines = [], []

    def flush():
        if not data_lines:
            return
        data = "\n".join(data_lines)
        data_lines.clear()
        if data.strip() == "[DONE]":
            return
        # Frozen ai-client/chat.ts reads chunk.choices[0] only. Other provider
        # candidates are not concatenated into the accepted Writer evidence.
        for choice in json.loads(data).get("choices", [])[:1]:
            content = choice.get("delta", {}).get("content")
            if isinstance(content, str):
                parts.append(content)

    for line in raw.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        elif not line:
            flush()
    flush()
    return "".join(parts)


def observe_choices(trace_dir: Path, exported_choices: list) -> dict:
    """Unknown is explicit when the exact accepted Writer cannot be established."""
    trace_dir = Path(trace_dir).resolve()
    observation = {"boundary": "provider_writer_choices_to_native_export", "output_modified": False}
    try:
        candidates = {}
        for path in trace_dir.glob("calls-*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("stage") == "writer" and event.get("event") == "completed":
                    candidates[event["call_id"]] = event
        observation["completed_writer_candidates"] = len(candidates)
        if len(candidates) != 1:
            return {**observation, "status": "unknown_no_unique_completed_writer"}
        event = next(iter(candidates.values()))
        source = (trace_dir / event["response_file"]).resolve()
        if not source.is_relative_to(trace_dir):
            return {**observation, "status": "unknown_response_path_outside_trace"}
        raw = source.read_bytes()
        text = _writer_text(raw.decode("utf-8"), bool(event.get("request_schema_and_sampling", {}).get("stream")))
        if text.count("<choices>") != 1 or text.count("</choices>") != 1:
            return {**observation, "status": "unknown_choices_tag_not_unique"}
        match = re.search(r"<choices>(.*?)</choices>", text, re.S)
        choices = json.loads(match.group(1)) if match else None
        if not isinstance(choices, list):
            return {**observation, "status": "unknown_writer_choices_not_array"}
        return {**observation, "writer_call_id": event["call_id"],
                "writer_response_file": str(source.relative_to(trace_dir)),
                "writer_response_sha256": hashlib.sha256(raw).hexdigest(),
                "writer_choices_parse_scope": "strict JSON in one choices tag; no repair or evaluation",
                **compare_choices(choices, exported_choices)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        # Native parseJsonLoose accepts additional syntax. We do not recreate
        # its repairs or assert that a strict-parser failure is a native error.
        return {**observation, "status": "unknown_unreadable_or_non_strict_writer_choices"}
