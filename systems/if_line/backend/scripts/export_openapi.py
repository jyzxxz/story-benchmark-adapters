"""Export the complete additive v2 OpenAPI contract deterministically."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# OpenAPI is a source contract, not a reflection of whichever provider happens
# to be selected in a deployment's private .env. These values are set before
# importing router modules whose Pydantic bounds are initialized at import.
os.environ.update(
    {
        "APP_ENV": "test",
        "AUTH_COOKIE_NAME": "sid",
        "TTS_ENGINE": "xunfei",
        "TTS_MAX_TEXT_LENGTH": "200",
        "VOICE_CLONE_MAX_TTS_CHARS": "600",
    }
)

from app.core.config import AppSettings
from app.main import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("openapi.v2.json"))
    args = parser.parse_args()

    settings = AppSettings(
        app_env="test",
        metrics_enabled=False,
    )
    schema = create_app(settings).openapi()
    payload = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    print(f"wrote {args.output} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
