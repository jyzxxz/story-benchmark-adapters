#!/usr/bin/env python3
"""Run the original native image/text fixture tests with no real API credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from doctor import ROOT, SYSTEMS, SOURCE_NAMES, clean_environment, load_config, source_report, utc_now

TESTS = {
    "if_line": "benchmark/tests/test_if_line_batch.py::BatchNativeTests::test_native_media_path_and_frames",
    "ai4visualnovel": "benchmark/tests/test_ai4vn_batch.py::BatchAI4Tests::test_full_native_multimodal_choice_and_renderer",
    "infiplot": "benchmark/tests/test_infiplot_batch.py::NativeBrowserFixture::test_original_page_prefetch_choice_image_edit_and_source_attestation",
}


def fixture_environment(system, config, evidence):
    env = clean_environment(config)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "benchmark"), str(ROOT / "benchmark/tests")))
    env["BENCH_SHARED_BUNDLE"] = str(ROOT / "benchmark/examples/CAMPUS-01-V4")
    if system == "if_line":
        env.update(IFLINE_BATCH_E2E="1", IFLINE_BATCH_EVIDENCE=str(evidence),
                   IFLINE_PYTHON=str(config["python_executable"]), IFLINE_NODE=str(config["node_executable"]),
                   IFLINE_NODE_MODULES=str(config["node_modules"]),
                   IFLINE_REMBG_MODELS=str(config["rembg_model_dir"]),
                   IFLINE_PG_BIN=str(config.get("pg_bin") or "/usr/lib/postgresql/16/bin"),
                   IFLINE_REDIS=str(config.get("redis_executable") or "/usr/bin/redis-server"),
                   IFLINE_FIXTURE_CHARACTERS="1", IFLINE_FIXTURE_DUPLICATE_CANDIDATES="1",
                   IFLINE_FIXTURE_SIGNED_URLS="1", IFLINE_FIXTURE_WINDOW="20")
    elif system == "ai4visualnovel":
        env.update(AI4VN_TEST_PYTHON=str(config["python_executable"]), AI4VN_BATCH_EVIDENCE_DIR=str(evidence),
                   AI4VN_TEST_REPO=str(ROOT / "systems/AI4VisualNovel"),
                   AI4VN_TEST_SOURCE_LOCK=str(ROOT / "baseline-lock.json"))
    else:
        env.update(INFIPLOT_BATCH_TEST="1", INFIPLOT_BATCH_EVIDENCE=str(evidence),
                   INFIPLOT_PLAYWRIGHT_MODULE=str(config.get("playwright_module") or ROOT / "work/media-tools/node_modules/playwright"))
        if config.get("route_compatibility"):
            env["INFIPLOT_ROUTE_COMPATIBILITY"] = str(config["route_compatibility"])
    return env


def junit_counts(path):
    if not path.is_file():
        return {"tests": 0, "failures": 0, "errors": 1, "skipped": 0, "reason": "junit_report_missing"}
    document = ET.parse(path).getroot()
    suites = [document] if document.tag == "testsuite" else list(document.findall("testsuite"))
    return {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}


def run_one(system, config, out, common):
    result = {"system": system, "evidence_kind": "fixture", "model_api_endpoint_scope": "localhost_fixed_response",
              "real_provider_credentials_inherited": False, "secrets_file_read": False,
              "paid_model_calls": 0, "test": TESTS[system], "started_at": utc_now()}
    if system == "ai4visualnovel":
        result["rembg_dependency"] = "deterministic_test_double_in_existing_test; doctor checks real ONNX model separately"
    before = source_report(system, config)
    result["source_before"] = before
    if not before["ok"]:
        return {**result, "ok": False, "error": "source_verification_failed_before_dispatch"}
    if Path(config["repo_path"]).resolve() != (ROOT / "systems" / SOURCE_NAMES[system]).resolve():
        return {**result, "ok": False, "error": "fixture_tests_require_this_checkout_system_snapshot"}
    env = fixture_environment(system, config, out / "evidence")
    command = [str(common), "-m", "pytest", "-q", "-rA", TESTS[system], "--junitxml=" + str(out / "junit.xml")]
    result["command"] = command
    (out / "environment-policy.json").write_text(json.dumps({"inherited_provider_variables": [],
        "effective_variable_names": sorted(env), "secrets_file_read": False,
        "fixture_provider_routes": "hardcoded_loopback_in_selected_repository_test",
        "network_scope": "not_an_OS_network_sandbox; InfiPlot may need native font build resources"}, indent=2) + "\n")
    try:
        # Do not impose a generation deadline or kill children at an arbitrary timeout.
        # The finite fixture and native service watchdogs own their normal cleanup.
        with (out / "pytest.log").open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            result["test_process_pid"] = process.pid
            try:
                result["returncode"] = process.wait()
            except KeyboardInterrupt:
                process.wait()
                raise
        result["junit"] = junit_counts(out / "junit.xml")
        result["ok"] = (result["returncode"] == 0 and result["junit"]["tests"] == 1
                        and all(result["junit"][key] == 0 for key in ("failures", "errors", "skipped")))
    except OSError as exc:
        result.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        result["source_after"] = source_report(system, config)
        result["source_valid_after"] = result["source_after"]["ok"]
        if not result["source_valid_after"]:
            result["ok"] = False
        result["finished_at"] = utc_now()
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=("all", *SYSTEMS), default="all")
    parser.add_argument("--config", type=Path, default=ROOT / "work/config/batch.local.json")
    parser.add_argument("--out", type=Path, required=True, help="New directory; never overwrites prior evidence")
    args = parser.parse_args(argv)
    out = args.out.absolute()
    if out.exists():
        parser.error("--out must not already exist")
    out.mkdir(parents=True)
    report = {"schema_version": "deployment-smoke.1", "started_at": utc_now(), "evidence_kind": "fixture",
              "secrets_file_read": False, "paid_model_calls": 0, "systems": {},
              "scope": "one original native multimodal success fixture per selected project, serial when all",
              "limitations": ["Does not score story quality or validate remote provider credentials.",
                              "Does not replace live generation or a separate multiworker load test."]}
    try:
        config = load_config(args.config)
        common = ROOT / "work/envs/common/bin/python"
        if not common.is_file():
            raise RuntimeError("missing common Python; run bootstrap_linux.sh first")
        for system in SYSTEMS if args.system == "all" else (args.system,):
            folder = out / system; folder.mkdir()
            try:
                report["systems"][system] = run_one(system, config["systems"][system], folder, common)
            except Exception as exc:
                report["systems"][system] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
        report["ok"] = all(row["ok"] for row in report["systems"].values())
    except KeyboardInterrupt:
        report.update(ok=False, error="interrupted; inspect this run's child cleanup and retained evidence")
    except Exception as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    report["finished_at"] = utc_now()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
