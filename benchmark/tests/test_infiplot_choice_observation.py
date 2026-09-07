"""Choice diagnostics never normalize or repair the actual native artifact."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from native_shims.infiplot.choice_observation import compare_choices, observe_choices
from story_benchmark.adapters.infiplot import InfiPlotAdapter


def option(label, identifier=None, kind="change-scene"):
    value = {"label": label, "effect": {"kind": kind, "nextSceneSeed": "unexecuted"}}
    if identifier is not None:
        value["id"] = identifier
    return value


class ChoiceComparisonTests(unittest.TestCase):
    def test_exact_native_filter_id_and_dedup_observed_without_editing(self):
        raw = [option("进入", "a"), option("不支持的内部目标", "x", "advance-beat"),
               option("进入", "duplicate"), option("先保护并检查")]
        native = [option("进入", "a"), option("先保护并检查", "sc3")]
        native[0].update(native_source="native/start-response.json", native_pointer="/scene/beats/1/next/choices/0")
        before = deepcopy((raw, native))
        result = compare_choices(raw, native)
        self.assertEqual(result["status"], "matches_frozen_native_normalization")
        self.assertEqual(result["native_dropped_writer_indexes_confirmed_by_exact_match"], [1, 2])
        self.assertEqual((raw, native), before)
        self.assertFalse(result["output_modified"])
        self.assertFalse(result["task_choice_semantics_evaluated"])

    def test_missing_native_option_is_difference_and_never_filled(self):
        raw = [option("进入", "a"), option("先保护并检查", "b")]
        native = [option("进入", "a")]
        result = compare_choices(raw, native)
        self.assertEqual(result["status"], "difference_requires_review")
        self.assertIsNone(result["native_dropped_writer_indexes_confirmed_by_exact_match"])
        self.assertEqual(result["exported_choices"], native)
        self.assertEqual(len(native), 1)


class ChoiceTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.trace = Path(self.tmp.name)
        (self.trace / "responses").mkdir()
        self.choices = [option("进入", "a"), option("先保护并检查", "b")]

    def tearDown(self):
        self.tmp.cleanup()

    def save_writer(self, content, streaming=False, count=1):
        response_file = "responses/writer.sse" if streaming else "responses/writer.json"
        if streaming:
            parts = [content[:12], content[12:]]
            raw = "".join("data: " + json.dumps({"choices": [{"delta": {"content": part}}]}, ensure_ascii=False) + "\n\n" for part in parts)
            raw += "data: [DONE]\n\n"
        else:
            raw = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False)
        (self.trace / response_file).write_text(raw)
        events = [{"stage": "writer", "event": "completed", "call_id": f"writer{i}",
                   "response_file": response_file, "request_schema_and_sampling": {"stream": streaming}}
                  for i in range(count)]
        (self.trace / "calls-test.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    def test_sse_choices_trace_compares_to_native_boundary(self):
        self.save_writer("<plan>{}</plan><story>真实fixture正文</story><choices>" + json.dumps(self.choices, ensure_ascii=False) + "</choices>", streaming=True)
        result = observe_choices(self.trace, self.choices)
        self.assertEqual(result["status"], "matches_frozen_native_normalization")
        self.assertEqual(result["writer_call_id"], "writer0")
        self.assertEqual(result["native_dropped_writer_indexes_confirmed_by_exact_match"], [])

    def test_native_loose_json_syntax_is_unknown_not_repaired_or_failed(self):
        self.save_writer('<choices>[{"label":"进入",}]</choices>')
        result = observe_choices(self.trace, self.choices)
        self.assertEqual(result["status"], "unknown_unreadable_or_non_strict_writer_choices")
        self.assertEqual(len(self.choices), 2)

    def test_multiple_completed_writers_do_not_guess_accepted_response(self):
        self.save_writer("<choices>" + json.dumps(self.choices) + "</choices>", count=2)
        self.assertEqual(observe_choices(self.trace, self.choices)["status"], "unknown_no_unique_completed_writer")

    def test_missing_response_choices_are_unknown(self):
        self.save_writer("<story>正文</story>")
        self.assertEqual(observe_choices(self.trace, self.choices)["status"], "unknown_choices_tag_not_unique")

    def test_adapter_saves_sidecar_without_editing_native_response_or_choices(self):
        self.save_writer("<story>仍停在门外。</story><choices>" + json.dumps(self.choices, ensure_ascii=False) + "</choices>", streaming=True)
        native = self.trace / "native"
        native.mkdir()
        response = {"scene": {"entryBeatId": "b1", "beats": [{
            "id": "b1", "narration": "仍停在门外。", "next": {"type": "choice", "choices": self.choices},
        }]}}
        raw = json.dumps(response, ensure_ascii=False).encode()
        (native / "start-response.json").write_bytes(raw)
        exported = InfiPlotAdapter({}).export_first_artifact({"native_dir": str(native), "trace_dir": str(self.trace)})
        self.assertEqual((native / "start-response.json").read_bytes(), raw)
        self.assertEqual(exported["segments"][0]["text"], "仍停在门外。")
        self.assertEqual([choice["label"] for choice in exported["choices"]], ["进入", "先保护并检查"])
        observation = json.loads((native / "choice-provenance.json").read_text())
        self.assertEqual(observation["status"], "matches_frozen_native_normalization")
        self.assertFalse(observation["output_modified"])


if __name__ == "__main__":
    unittest.main()
