from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from app.models import (
    Asset,
    ChapterContent,
    ChapterOutline,
    Project,
    StoryBible,
    User,
    VNGraph,
)
from scripts.preflight_legacy_database import preflight
from scripts.update_env_flags import _parse_assignment, update_env


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent


def test_update_env_flags_preserves_secrets_and_comments(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# keep me\nOPENAI_API_KEY=super-secret\nTASK_SYSTEM_ENABLED=false\n",
        encoding="utf-8",
    )

    changed = update_env(
        env_file,
        [
            ("TASK_SYSTEM_ENABLED", "true"),
            ("REVISION_READ_ENABLED", "true"),
        ],
    )

    assert changed == ["REVISION_READ_ENABLED", "TASK_SYSTEM_ENABLED"]
    content = env_file.read_text(encoding="utf-8")
    assert "# keep me" in content
    assert "OPENAI_API_KEY=super-secret" in content
    assert "TASK_SYSTEM_ENABLED=true" in content
    assert "REVISION_READ_ENABLED=true" in content
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_update_env_flags_rejects_secret_or_unknown_keys():
    with pytest.raises(Exception):
        _parse_assignment("OPENAI_API_KEY=do-not-touch")


def test_complete_v2_openapi_snapshot_is_current(tmp_path: Path):
    actual_path = tmp_path / "openapi.json"
    subprocess.run(
        [
            sys.executable,
            str(BACKEND_ROOT / "scripts" / "export_openapi.py"),
            "--output",
            str(actual_path),
        ],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    expected = json.loads((BACKEND_ROOT / "openapi.v2.json").read_text(encoding="utf-8"))
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    assert actual == expected


def test_systemd_units_load_env_and_do_not_depend_on_archive_execute_bits():
    unit_dir = BACKEND_ROOT / "deploy" / "systemd"
    expected_scripts = {
        "if-line-api.service": "start.sh",
        "if-line-worker.service": "start_worker_text.sh",
        "if-line-worker-image.service": "start_worker_image.sh",
        "if-line-beat.service": "start_beat.sh",
    }

    for unit_name, script_name in expected_scripts.items():
        content = (unit_dir / unit_name).read_text(encoding="utf-8")
        assert "EnvironmentFile=-/home/workspace/fengbohan/if_line/backend/.env" in content
        assert (
            "ExecStart=/bin/bash "
            f"/home/workspace/fengbohan/if_line/backend/{script_name}"
        ) in content


def test_tts_launcher_delegates_to_the_canonical_backend_port():
    launcher = (REPOSITORY_ROOT / "start_tts.sh").read_text(encoding="utf-8")
    backend_launcher = (BACKEND_ROOT / "start.sh").read_text(encoding="utf-8")

    assert 'PORT="${PORT:-60002}"' in launcher
    assert "bash backend/start.sh" in launcher
    assert "--port 8002" not in launcher
    assert '--port "${PORT:-60002}"' in backend_launcher


def test_legacy_database_preflight_accepts_only_structurally_safe_baseline():
    engine = create_engine("sqlite://")
    for model in (
        User,
        Project,
        StoryBible,
        ChapterOutline,
        ChapterContent,
        Asset,
        VNGraph,
    ):
        model.__table__.create(engine)

    report = preflight(engine)
    assert report["ok"] is True
    assert report["database_state"] == "legacy_unmanaged"
    assert report["required_table_counts"]["projects"] == 0

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE generation_tasks (id VARCHAR PRIMARY KEY)"))
    unsafe = preflight(engine)
    assert unsafe["ok"] is False
    assert any("without alembic_version" in item for item in unsafe["errors"])
    engine.dispose()
