from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def js_normalized_json(value: Any) -> Any:
    """模拟浏览器 JSON 往返的数字归一化：整值浮点（80.0）经 JS number 变成 int（80）。

    客户端草稿经 localStorage/axios 往返后，JSON 内容会发生这种归一化，
    content_hash 随之漂移。手动（物化）修订去重比较时，对存量内容先按同一
    规则归一化再比 hash，避免把同内容误判为不同（vngraph 绑定、bible/outline/
    script 的手动去重共用此规则）。
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [js_normalized_json(item) for item in value]
    if isinstance(value, dict):
        return {key: js_normalized_json(item) for key, item in value.items()}
    return value


def content_hash_js(value: Any) -> str:
    return content_hash(js_normalized_json(value))

