#!/usr/bin/env python3
"""Probe whether the configured image proxy really supports OpenAI
``images/edits`` (i.e. reference-image editing) for ``gpt-image-2``.

Reads AI_IMAGE_* from backend/.env, takes a single reference PNG, posts a
minimal edit request, and prints the raw response. It is intentionally
self-contained so it can run as a smoke test without touching the backend.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
import urllib.parse
from pathlib import Path

import urllib.request
import urllib.error


REPO_ROOT = Path(__file__).resolve().parent
ENV_PATH = REPO_ROOT / "backend" / ".env"
REF_IMAGE = (
    REPO_ROOT
    / "backend/static/assets/portraits/b954343ffa395bd95ed09ef66f6494ed.png.source.png"
)
OUT_PATH = Path("/tmp/probe_edit_out.png")


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def build_multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes]], boundary: str
) -> bytes:
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(value.encode())
    for name, (filename, payload) in files.items():
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts.append(f"--{boundary}".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        )
        parts.append(f"Content-Type: {mime}".encode())
        parts.append(b"")
        parts.append(payload)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    return crlf.join(parts)


def main() -> int:
    env = load_env(ENV_PATH)
    base_url = (env.get("AI_IMAGE_BASE_URL") or "").rstrip("/")
    api_key = env.get("AI_IMAGE_API_KEY") or ""
    model = env.get("AI_IMAGE_MODEL") or "gpt-image-2"

    if not base_url or not api_key:
        print("ERROR: AI_IMAGE_BASE_URL / AI_IMAGE_API_KEY missing in backend/.env")
        return 2

    endpoint = f"{base_url}/images/edits"
    if not base_url.endswith("/v1"):
        endpoint = f"{base_url}/v1/images/edits"

    if not REF_IMAGE.exists():
        print(f"ERROR: reference image missing: {REF_IMAGE}")
        return 2

    ref_bytes = REF_IMAGE.read_bytes()
    boundary = "----probe-edit-" + hex(int(time.time() * 1e6))
    body = build_multipart(
        fields={
            "model": model,
            "prompt": (
                "A man in brown jacket and glasses leaning toward a laptop "
                "screen in a dimly lit laboratory at night, lightning outside "
                "the window, cinematic wide shot"
            ),
            "size": "1024x576",
            "n": "1",
        },
        files={"image": ("ref.png", ref_bytes)},
        boundary=boundary,
    )

    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "if_line_probe/0.1",
        },
    )

    print(f"POST {endpoint}")
    print(f"model={model} ref_bytes={len(ref_bytes)} payload_bytes={len(body)}")

    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
            elapsed = time.time() - t0
            print(f"HTTP {resp.status} {resp.reason} in {elapsed:.2f}s")
            print(f"Content-Type: {resp.headers.get('Content-Type')}")
            head = raw[:300]
            try:
                text_preview = head.decode("utf-8", errors="replace")
            except Exception:
                text_preview = repr(head)
            print("response preview (first 300 bytes):")
            print(text_preview)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "json" in ctype:
                try:
                    data = json.loads(raw)
                    item = (data.get("data") or [{}])[0]
                    b64 = item.get("b64_json")
                    url = item.get("url")
                    if b64:
                        OUT_PATH.write_bytes(base64.b64decode(b64))
                        print(f"saved decoded image -> {OUT_PATH} ({len(base64.b64decode(b64))} bytes)")
                    elif url:
                        print(f"got URL (no b64): {url}")
                    print("usage:", data.get("usage"))
                except Exception as e:
                    print(f"json parse failed: {e}")
            elif "image" in ctype:
                OUT_PATH.write_bytes(raw)
                print(f"saved raw image -> {OUT_PATH} ({len(raw)} bytes)")
            return 0
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"HTTPError {e.code} {e.reason}")
        print(f"body: {body_text[:600]}")
        return 1
    except Exception as e:
        print(f"EXC {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
