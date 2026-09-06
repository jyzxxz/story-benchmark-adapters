"""Create an unmodified runtime copy and attest source hashes before/after."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from threading import Thread

EXCLUDE_DIRS = {".git", "node_modules", ".next", ".pnpm-store", "__pycache__", ".turbo", ".open-next", ".wrangler"}
GENERATED_FILES = {"next-env.d.ts", "tsconfig.tsbuildinfo"}
OBSERVATIONS = {
    "[directScene] Writer stream was degraded": "writer_stream_degraded",
    "[proseSplitter] empty prose after cleanup, using fallback": "writer_fallback_empty_prose",
    "[proseSplitter] unexpected error, using fallback": "writer_fallback_split_error",
}


def source_hashes(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in EXCLUDE_DIRS]
        for name in files:
            if name in {".DS_Store", "tsconfig.tsbuildinfo"} or name.endswith((".pyc", ".log")):
                continue
            if name == ".env" or name.startswith(".env.") and not name.endswith(("example", "sample", "template")):
                continue
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("unexpected_source_symlink")
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def copy_source(source, destination, hashes):
    destination.mkdir(parents=True, exist_ok=False)
    for relative in hashes:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    if source_hashes(destination) != hashes:
        raise ValueError("runtime_copy_source_mismatch")


def observe_console(process, trace_dir, root_run_id):
    """Keep only literal frozen-native diagnostics; drop all other console bytes."""
    def consume():
        for line in process.stdout:
            for signature, code in OBSERVATIONS.items():
                if line.startswith(signature):
                    with (Path(trace_dir) / "native-observations.jsonl").open("a") as file:
                        file.write(json.dumps({"root_run_id": root_run_id, "system": "infiplot",
                                               "boundary": "native_console_signature", "generation_issue_code": code,
                                               "signature": signature}, ensure_ascii=False) + "\n")
    thread = Thread(target=consume, daemon=True)
    thread.start()
    return thread
