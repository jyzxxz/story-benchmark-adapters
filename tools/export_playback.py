#!/usr/bin/env python3
"""Export sealed story evidence for portable, offline reading; never call models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="A sealed run, a batch directory, or an experiment directory")
    parser.add_argument("--out", required=True, type=Path,
                        help="A new output directory outside the sealed source evidence")
    parser.add_argument("--no-zip", action="store_true",
                        help="Create the offline folder without a ZIP archive")
    args = parser.parse_args(argv)
    from story_benchmark.playback import export_collection

    try:
        report = export_collection(args.input, args.out, make_zip=not args.no_zip)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "stage": "playback_export",
                          "error_type": type(exc).__name__, "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
