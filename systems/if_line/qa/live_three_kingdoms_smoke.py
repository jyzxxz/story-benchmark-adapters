#!/usr/bin/env python3
"""Run the public Three Kingdoms continuation flow without publishing test data."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from typing import Any
from urllib.parse import urljoin

import requests


TERMINAL_CONTINUATION_STATES = {"preview_ready", "confirmed", "failed", "cancelled"}
TERMINAL_IMAGE_STATES = {"awaiting_confirmation", "ready", "applied", "failed", "cancelled"}


def count_vn_beats(text: str, max_length: int = 118) -> int:
    paragraphs = [
        re.sub(r"^[-*]\s+", "", re.sub(r"^#{1,6}\s*", "", item)).strip()
        for item in re.split(r"\n{2,}|\n(?=(?:#{1,6}\s*)?(?:[\u3400-\u9fffA-Za-z]{1,12}[：:]|[-*]\s))", text)
    ]
    count = 0
    for paragraph in filter(None, paragraphs):
        speaker_match = re.match(r"^[\u3400-\u9fffA-Za-z]{1,12}[：:]\s*([\s\S]+)$", paragraph)
        body = speaker_match.group(1).strip() if speaker_match else paragraph
        sentences = re.findall(r"[^。！？；…]+(?:[。！？；…]+|$)", body) or [body]
        current_length = 0
        for sentence in filter(None, (item.strip() for item in sentences)):
            if current_length and current_length + len(sentence) > max_length:
                count += 1
                current_length = 0
            if len(sentence) <= max_length:
                current_length += len(sentence)
            else:
                if current_length:
                    count += 1
                    current_length = 0
                count += (len(sentence) + max_length - 1) // max_length
        if current_length:
            count += 1
    return count


def require(response: requests.Response, expected: set[int]) -> Any:
    if response.status_code not in expected:
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"{response.request.method} {response.url} -> {response.status_code}: {body}")
    if not response.content:
        return None
    return response.json()


def get(session: requests.Session, base_url: str, path: str, **kwargs: Any) -> Any:
    return require(session.get(urljoin(base_url, path.lstrip("/")), timeout=60, **kwargs), {200})


def post(
    session: requests.Session,
    base_url: str,
    path: str,
    *,
    expected: set[int] = {200, 201, 202},
    **kwargs: Any,
) -> Any:
    return require(
        session.post(urljoin(base_url, path.lstrip("/")), timeout=180, **kwargs),
        expected,
    )


def wait_for_continuation(
    session: requests.Session,
    base_url: str,
    session_id: str,
    continuation_id: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_seconds
    last_status = ""
    while time.monotonic() < deadline:
        continuation = get(
            session,
            base_url,
            f"v2/reading-sessions/{session_id}/continuations/{continuation_id}",
        )
        status = continuation["status"]
        if status != last_status:
            print(f"[续写] {status}")
            last_status = status
        if status in TERMINAL_CONTINUATION_STATES:
            return continuation
        time.sleep(2)
    raise TimeoutError("续写任务在限定时间内没有完成")


def wait_for_images(
    session: requests.Session,
    base_url: str,
    project_id: int,
    action_id: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_seconds
    last_progress = -1
    while time.monotonic() < deadline:
        action = get(session, base_url, f"v2/projects/{project_id}/asset-actions/{action_id}")
        progress = round(float(action.get("progress") or 0))
        if progress != last_progress:
            print(f"[生图] {action['status']} {progress}%")
            last_progress = progress
        if action["status"] in TERMINAL_IMAGE_STATES:
            return action
        time.sleep(3)
    raise TimeoutError("生图任务在限定时间内没有完成")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5173/api/")
    parser.add_argument("--deadline", type=int, default=900)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/") + "/"
    page_origin = base_url.removesuffix("api/")
    client = requests.Session()

    guest = post(client, base_url, "auth/guest", expected={201})
    assert guest["user"]["email"].endswith("@guest.ifline.local")
    print("[游客会话] 正常")

    projects = get(
        client,
        base_url,
        "v2/public/projects",
        params={"q": "三国演义·公开互动版", "limit": 10},
    )
    project = next(item for item in projects if item["title"] == "三国演义·公开互动版")
    catalog_response = client.get(urljoin(page_origin, "three-kingdoms/catalog.json"), timeout=60)
    catalog = require(catalog_response, {200})
    assert len(catalog["chapters"]) == 120
    print("[公开作品] 120 回目录正常")

    cached_visuals = get(client, base_url, "three-kingdoms/cached-visuals")
    print(
        f"[素材缓存] 背景 {len(cached_visuals['backgrounds'])}，"
        f"立绘 {len(cached_visuals['portraits'])}"
    )
    library_backgrounds = get(
        client,
        base_url,
        "v2/library-assets",
        params={"asset_type": "background", "style": "historical", "limit": 100},
    )
    library_portraits = get(
        client,
        base_url,
        "v2/library-assets",
        params={"asset_type": "portrait", "style": "historical", "limit": 100},
    )
    assert library_backgrounds["items"], "历史背景图库为空"
    assert library_portraits["items"], "历史人物图库为空"
    print(
        f"[即时图库兜底] 背景 {len(library_backgrounds['items'])}，"
        f"立绘 {len(library_portraits['items'])}"
    )

    chapter_number = 1
    public_tree = get(
        client,
        base_url,
        f"v2/public/projects/{project['id']}/releases/{project['release_id']}/continuations",
        params={"chapter_number": chapter_number},
    )
    print(f"[公开分支] 当前可见 {len(public_tree['nodes'])} 条")

    reading_session = post(
        client,
        base_url,
        "v2/reading-sessions",
        json={
            "project_id": project["id"],
            "release_id": project["release_id"],
            "initial_state": {
                "source": "自动回归测试",
                "chapter_number": chapter_number,
                "chapter_title": "第一回 桃园宴会上，三位豪杰结为兄弟；讨伐黄巾军，英雄第一次立功",
                "chapter_excerpt": "刘备、关羽、张飞桃园结义，投军讨伐黄巾。",
                "chapter_context": "桃园结义之后，三人整顿乡勇，准备赶赴战场。",
                "chapter_continuation_point": "夜色降临，营外忽然传来急促马蹄声。",
            },
        },
    )
    session_id = reading_session["id"]
    print("[阅读会话] 创建正常")

    parent_id = None
    if public_tree["nodes"]:
        selected = public_tree["nodes"][0]
        reading_session = post(
            client,
            base_url,
            f"v2/reading-sessions/{session_id}/continuations/{selected['id']}/select",
            json={},
        )
        parent_id = selected["id"]
        assert reading_session["selected_continuation_id"] == parent_id
        print("[选择公开分支] 正常")

    suggestion = post(
        client,
        base_url,
        f"v2/reading-sessions/{session_id}/direction-suggestion",
        json={},
    )
    assert len(suggestion["direction"].strip()) >= 8
    print(f"[AI 方向] {suggestion['direction'].strip()}")

    direction = (
        "斥候带来黄巾军夜袭的消息，刘备沉着布阵，关羽和张飞分路设伏；"
        "天明前揭开一名降卒隐瞒的真相，并留下可供下一段选择的悬念。"
    )
    continuation = post(
        client,
        base_url,
        f"v2/reading-sessions/{session_id}/directions",
        headers={"Idempotency-Key": f"live-smoke-{uuid.uuid4()}"},
        json={
            "direction": direction,
            "visual_mode": "system_generate",
            "uploaded_asset_version_ids": [],
            "max_generated_assets": 3,
            "parent_continuation_id": parent_id,
        },
    )
    continuation = wait_for_continuation(
        client,
        base_url,
        session_id,
        continuation["id"],
        args.deadline,
    )
    if continuation["status"] != "preview_ready":
        raise RuntimeError(json.dumps(continuation.get("error") or continuation, ensure_ascii=False))
    text_length = len(continuation["continuation_text"].strip())
    beat_count = count_vn_beats(continuation["continuation_text"])
    assert text_length >= 600, f"续写过短：{text_length} 字"
    assert beat_count >= 6, f"视觉小说分镜过少：{beat_count}"
    print(f"[续写正文] {text_length} 字，前端拆分为 {beat_count} 幕")

    action_id = continuation.get("asset_action_id")
    assert action_id, "系统生图模式没有返回 asset_action_id"
    image_action = wait_for_images(client, base_url, project["id"], action_id, args.deadline)
    if image_action["status"] in {"failed", "cancelled"}:
        raise RuntimeError(json.dumps(image_action.get("error") or image_action, ensure_ascii=False))
    assets = image_action.get("assets") or []
    assert assets, "生图完成但没有素材"
    for asset in assets:
        media = client.get(urljoin(page_origin, asset["media_url"].lstrip("/")), timeout=60)
        if media.status_code != 200 or not media.content:
            raise RuntimeError(f"生成素材不可访问：{asset['media_url']} -> {media.status_code}")
    print(f"[生成素材] {len(assets)} 张全部可访问")

    restored = requests.Session()
    restored.cookies.update(client.cookies)
    restored_session = get(restored, base_url, f"v2/reading-sessions/{session_id}")
    restored_continuation = get(
        restored,
        base_url,
        f"v2/reading-sessions/{session_id}/continuations/{continuation['id']}",
    )
    assert restored_session["id"] == session_id
    assert restored_continuation["continuation_text"] == continuation["continuation_text"]
    print("[退出后恢复] 会话、正文和续写素材引用均保留")
    print("[结果] 完整流程通过；测试续写保持待采用状态，未污染公开故事树")


if __name__ == "__main__":
    main()
