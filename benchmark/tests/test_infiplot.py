"""Offline native-shape fixtures only, never live generation evidence."""
from copy import deepcopy
from http.client import IncompleteRead
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import error

from story_benchmark.adapters.infiplot import InfiPlotAdapter, InfiPlotError, NATIVE_ARTIFACT_ERROR_CODES, export_scene


def fixture():
    return {"sessionId": "fixture", "scene": {"entryBeatId": "entry", "scenePrompt": "NOT PLAYER PROSE", "beats": [
        {"id": "other", "narration": "OTHER BRANCH", "next": {"type": "continue", "nextBeatId": "entry"}},
        {"id": "entry", "narration": "  原文保留。 ", "next": {"type": "continue", "nextBeatId": "pick"}},
        {"id": "pick", "speaker": "沈", "line": "怎么做？", "next": {"type": "choice", "choices": [
            {"id": "a", "label": "等待", "effect": {"kind": "change-scene", "nextSceneSeed": "internal seed"}}
        ]}},
    ]}, "characters": [], "storyState": {"synopsis": "NOT PLAYER PROSE"}}


class ExportTests(unittest.TestCase):
    def test_only_visible_native_graph_and_exact_text(self):
        result = export_scene(fixture())
        self.assertEqual([s["text"] for s in result["segments"]], ["  原文保留。 ", "怎么做？"])
        self.assertEqual(result["segments"][0]["native_pointer"], "/scene/beats/1/narration")
        self.assertEqual(result["visited_beat_ids"], ["entry", "pick"])
        self.assertIs(result["selection_executed"], False)
        self.assertEqual(result["choices"][0]["effect"]["nextSceneSeed"], "internal seed")
        self.assertEqual(result["previews"], [])

    def test_cycle(self):
        value = fixture()
        value["scene"]["beats"][2]["next"] = {"type": "continue", "nextBeatId": "entry"}
        with self.assertRaisesRegex(InfiPlotError, "cycles"):
            export_scene(value)

    def test_missing_target_and_duplicate_ids(self):
        for mutation in ("missing", "duplicate"):
            value = fixture()
            if mutation == "missing":
                value["scene"]["entryBeatId"] = "missing"
            else:
                value["scene"]["beats"][0]["id"] = "entry"
            with self.assertRaises(InfiPlotError):
                export_scene(value)

    def test_empty_does_not_use_summary(self):
        value = fixture()
        del value["scene"]["beats"][1]["narration"]
        del value["scene"]["beats"][2]["line"]
        result = export_scene(value)
        self.assertEqual(result["segments"], [])
        self.assertIn("empty_prose", result["generation_issue_codes"])

    def test_v3_allows_zero_visible_body_with_native_choices(self):
        value = fixture()
        del value["scene"]["beats"][1]["narration"]
        del value["scene"]["beats"][2]["line"]
        result = export_scene(value, allow_empty_body=True)
        self.assertEqual(result["segments"], [])
        self.assertEqual(len(result["choices"]), 1)
        self.assertEqual(result["generation_issue_codes"], [])
        self.assertEqual(result["previews"], [])

    def test_v3_empty_body_cannot_hide_empty_first_choice(self):
        value = fixture()
        value["scene"]["beats"][1].pop("narration")
        value["scene"]["beats"][1]["next"] = {"type": "choice", "choices": []}
        result = export_scene(value, allow_empty_body=True)
        self.assertEqual(set(result["generation_issue_codes"]), {"empty_prose", "empty_choice_boundary"})
        self.assertEqual(result["visited_beat_ids"], ["entry"])

    def test_malformed_native_shapes_have_codes_not_python_type_errors(self):
        values = [None, []]
        for key, bad in (("entryBeatId", []), ("entryBeatId", {})):
            value = fixture()
            value["scene"][key] = bad
            values.append(value)
        for target in ([], {}, 2):
            value = fixture()
            value["scene"]["beats"][1]["next"]["nextBeatId"] = target
            values.append(value)
        for speaker in ([], {}, 2):
            value = fixture()
            value["scene"]["beats"][2]["speaker"] = speaker
            values.append(value)
        value = fixture()
        value["scene"]["beats"][2]["next"]["choices"][0]["label"] = "  "
        values.append(value)
        for value in values:
            with self.subTest(value=value), self.assertRaises(InfiPlotError) as caught:
                export_scene(value, allow_empty_body=True)
            self.assertIn(caught.exception.code, NATIVE_ARTIFACT_ERROR_CODES)

    def test_absent_scene(self):
        with self.assertRaises(InfiPlotError):
            export_scene({})

    def test_hidden_line_is_not_exported_as_visible(self):
        value = fixture()
        value["scene"]["beats"][1]["line"] = "not visible without speaker"
        result = export_scene(value)
        self.assertNotIn("not visible without speaker", [row["text"] for row in result["segments"]])
        self.assertIn("hidden_line_without_speaker", result["generation_issue_codes"])

    def test_preserve_all_first_choice_options_without_following_any_effect(self):
        value = fixture()
        options = [
            {"id": "c1a", "label": "进入实验楼", "effect": {"kind": "advance-beat", "targetBeatId": "after"}},
            {"id": "c1b", "label": "先保护周遥并检查收音机", "effect": {"kind": "change-scene", "nextSceneSeed": "not executed"}},
            {"id": "c1c", "label": "原生额外选项也保留", "effect": {"kind": "change-scene", "nextSceneSeed": "not executed either"}},
        ]
        value["scene"]["beats"][2]["next"]["choices"] = options
        value["scene"]["beats"].append({
            "id": "after", "narration": "CHOICE ALREADY EXECUTED; MUST NOT EXPORT",
            "next": {"type": "choice", "choices": [{"id": "c2", "label": "LATER CHOICE"}]},
        })
        original = deepcopy(value)
        result = export_scene(value)
        self.assertEqual(value, original)
        self.assertEqual(result["visited_beat_ids"], ["entry", "pick"])
        self.assertEqual([row["text"] for row in result["segments"]], ["  原文保留。 ", "怎么做？"])
        self.assertEqual([{k: v for k, v in row.items() if k not in ("native_source", "native_pointer")}
                          for row in result["choices"]], options)
        self.assertEqual([row["native_pointer"] for row in result["choices"]],
                         [f"/scene/beats/2/next/choices/{i}" for i in range(3)])

    def test_entry_choice_retains_both_visible_narration_and_dialogue(self):
        value = fixture()
        value["scene"]["entryBeatId"] = "pick"
        value["scene"]["beats"][2]["narration"] = "他站在门外等你决定。"
        result = export_scene(value)
        self.assertEqual(result["visited_beat_ids"], ["pick"])
        self.assertEqual([row["text"] for row in result["segments"]], ["他站在门外等你决定。", "怎么做？"])
        self.assertEqual(len(result["choices"]), 1)

    def test_empty_first_choice_does_not_skip_to_another_choice(self):
        value = fixture()
        value["scene"]["beats"][1]["next"] = {"type": "choice", "choices": []}
        result = export_scene(value)
        self.assertEqual(result["visited_beat_ids"], ["entry"])
        self.assertEqual(result["choices"], [])
        self.assertIn("empty_choice_boundary", result["generation_issue_codes"])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.native = Path(self.tmp.name) / "native"
        self.native.mkdir()
        self.handle = {"native_dir": str(self.native), "base_url": "http://127.0.0.1:3217"}
        (self.native / "start-request.json").write_text("{}")
        self.adapter = InfiPlotAdapter({"live": True, "timeout_seconds": 1})

    def tearDown(self):
        self.tmp.cleanup()

    def test_timeout_is_unknown_and_never_retried(self):
        with patch.object(self.adapter, "_launch"), patch.object(self.adapter, "_headers", return_value={}), patch("urllib.request.urlopen", side_effect=TimeoutError) as send:
            with self.assertRaises(InfiPlotError) as caught:
                self.adapter.generate_first_artifact(self.handle)
            self.assertEqual(caught.exception.code, "delivery_unknown")
            with self.assertRaises(InfiPlotError):
                self.adapter.generate_first_artifact(self.handle)
            self.assertEqual(send.call_count, 1)

    def test_saved_response_resumes_without_launch(self):
        (self.native / "start-response.json").write_text(json.dumps(fixture()))
        with patch.object(self.adapter, "_launch") as launch:
            self.assertTrue(self.adapter.generate_first_artifact(self.handle)["resumed_from_saved_response"])
            launch.assert_not_called()

    def test_native_http_error_is_preserved_redacted_and_not_retried(self):
        raw = json.dumps({"error": "native rejected fixture-private-key", "api_key": "unknown-provider-key"}).encode()
        response = error.HTTPError(self.handle["base_url"], 500, "failure", {}, BytesIO(raw))
        with patch.dict("os.environ", {"TEXT_API_KEY": "fixture-private-key"}), patch.object(self.adapter, "_launch"), patch.object(self.adapter, "_headers", return_value={}), patch("urllib.request.urlopen", side_effect=response) as send:
            with self.assertRaises(InfiPlotError) as caught:
                self.adapter.generate_first_artifact(self.handle)
            self.assertEqual(caught.exception.code, "native_http_error")
            evidence = json.loads((self.native / "http-error.json").read_text())
            self.assertEqual(evidence["native_error"]["error"], "native rejected [REDACTED]")
            self.assertEqual(evidence["native_error"]["api_key"], "[REDACTED]")
            self.assertTrue(evidence["response_complete"])
            self.assertEqual(evidence["http_status"], 500)
            with self.assertRaises(InfiPlotError) as second:
                self.adapter.generate_first_artifact(self.handle)
            self.assertEqual(second.exception.code, "delivery_unknown")
            self.assertEqual(send.call_count, 1)

    def test_native_partial_response_preserves_error_evidence_without_success(self):
        partial = IncompleteRead(b'{"scene":"\xe4\xb8', 10)
        with patch.object(self.adapter, "_launch"), patch.object(self.adapter, "_headers", return_value={}), patch("urllib.request.urlopen", side_effect=partial):
            with self.assertRaises(InfiPlotError) as caught:
                self.adapter.generate_first_artifact(self.handle)
        self.assertEqual(caught.exception.code, "delivery_unknown")
        evidence = json.loads((self.native / "http-error.json").read_text())
        self.assertFalse(evidence["response_complete"])
        self.assertEqual(evidence["original_response_bytes"], len(partial.partial))
        self.assertFalse((self.native / "start-response.json").exists())
        (self.native / "http-error-response.txt").read_text(encoding="utf-8")

    def test_live_disabled(self):
        self.adapter.config["live"] = False
        with self.assertRaises(InfiPlotError) as caught:
            self.adapter.generate_first_artifact(self.handle)
        self.assertEqual(caught.exception.code, "live_disabled")

    def test_missing_bundle_is_preflight_failure(self):
        result = self.adapter.preflight(Path(self.tmp.name))
        self.assertFalse(result["ok"])


class RuntimeSourceTests(unittest.TestCase):
    def test_unexpected_directory_symlink_cannot_escape_source_hash_inventory(self):
        from native_shims.infiplot.runtime import source_hashes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            target = Path(tmp) / "other"
            target.mkdir()
            (target / "injected.ts").write_text("unexpected source")
            (root / "extension").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "directory_symlink"):
                source_hashes(root)


if __name__ == "__main__":
    unittest.main()
