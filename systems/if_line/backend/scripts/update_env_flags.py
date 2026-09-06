"""Safely update a small allowlist of non-secret deployment settings.

The command deliberately never prints values and refuses keys outside the
allowlist.  It preserves comments, blank lines and every unrelated setting in
the existing ``.env`` file.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile


ALLOWED_KEYS = {
    "APP_ENV",
    "AUTH_RATELIMIT_BACKEND",
    "AUTH_RATELIMIT_REDIS_URL",
    "AUTH_TRUSTED_PROXY_IPS",
    "CELERY_BROKER_URL",
    "CELERY_LOG_LEVEL",
    "CHECK_DEPENDENCIES_ON_STARTUP",
    "LEGACY_SYNC_API_ENABLED",
    "METRICS_ENABLED",
    "REDIS_URL",
}


def _parse_assignment(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("expected KEY=VALUE")
    key, value = raw.split("=", 1)
    key = key.strip().upper()
    if key not in ALLOWED_KEYS:
        raise argparse.ArgumentTypeError(f"setting is not allowlisted: {key}")
    if "\n" in value or "\r" in value:
        raise argparse.ArgumentTypeError("setting values cannot contain newlines")
    return key, value.strip()


def update_env(path: Path, assignments: list[tuple[str, str]]) -> list[str]:
    requested = dict(assignments)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines(keepends=True)
    seen: set[str] = set()
    output: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip().upper()
        if key not in requested:
            output.append(line)
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        output.append(f"{key}={requested[key]}{newline}")
        seen.add(key)

    if output and not output[-1].endswith(("\n", "\r")):
        output[-1] += "\n"
    for key in sorted(set(requested) - seen):
        output.append(f"{key}={requested[key]}\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    # .env commonly contains provider credentials. Never preserve a legacy
    # group/world-readable mode while atomically replacing it.
    mode = 0o600
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.writelines(output)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    return sorted(requested)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--set",
        dest="assignments",
        action="append",
        required=True,
        type=_parse_assignment,
        metavar="KEY=VALUE",
    )
    args = parser.parse_args()
    changed = update_env(args.env_file, args.assignments)
    print("updated settings: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
