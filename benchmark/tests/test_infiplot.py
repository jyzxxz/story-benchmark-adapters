"""Offline native-shape fixtures only, never live generation evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from story_benchmark.adapters.infiplot import InfiPlotAdapter, InfiPlotError, export_scene


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

    def test_live_disabled(self):
        self.adapter.config["live"] = False
        with self.assertRaises(InfiPlotError) as caught:
            self.adapter.generate_first_artifact(self.handle)
        self.assertEqual(caught.exception.code, "live_disabled")

    def test_missing_bundle_is_preflight_failure(self):
        result = self.adapter.preflight(Path(self.tmp.name))
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
