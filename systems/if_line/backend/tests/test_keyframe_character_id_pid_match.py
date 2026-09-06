"""
回归测试：keyframe 算出来的 character_id 必须等于 identity_master portrait
在同一 project_id 下算出来的 character_id。

历史 bug：``generate_chapter_keyframes`` 派生 ``sb_raw`` 时丢失了外层
project_id，下游 ``int(story_bible.get("project_id") or 0)`` 永远拿到 0，
导致 keyframe 引用的 cid = md5("name|0")[:12] 永远 key 不到 identity
master contracts，全部走 text-only fallback，画师模型自由发挥，发型/性别漂移。

修复：在 ``generate_chapter_keyframes`` 内对 ``sb_raw`` 浅拷贝后注入
``project_id``。本测试直接调 ``build_keyframe_prompts``（keyframe cid 计算的
同步底层），传入带 project_id 的 sb_raw，断言 cid 与 portrait 侧算法一致。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.prompt_builder_service import prompt_builder_service
from app.services.image_generation_service import ImageGenerationService


def _expected_cid(name: str, project_id: int) -> str:
    """portrait 侧 identity_master_resolver 的算法（fallback path）。"""
    return hashlib.md5(f"{name}|{project_id}".encode("utf-8")).hexdigest()[:12]


def _make_story_bible(project_id: int) -> dict:
    """构造最小可用 story_bible；project_id 是测试的核心变量。"""
    return {
        "project_id": project_id,
        "characters": [
            {
                "name": "林夜",
                "gender": "male",
                "appearance": "young man in dark coat",
                "visual_profile": {},
                "visual_fingerprint": "vf-lin",
                "visual_prompt_en": "young man, short black hair",
            },
            {
                "name": "苏晚晴",
                "gender": "female",
                "appearance": "young woman with long hair",
                "visual_profile": {},
                "visual_fingerprint": "vf-su",
                "visual_prompt_en": "young woman, long brown hair",
            },
        ],
    }


def _make_outline() -> dict:
    return {
        "title": "测试章节",
        "summary": "两人相遇。",
        "characters": ["林夜", "苏晚晴"],
        "conflict": "对视",
        "emotion": "neutral",
        "scene": "庭院",
    }


@pytest.mark.parametrize("project_id", [15, 42, 9999])
def test_keyframe_cid_matches_portrait_algorithm(project_id):
    """keyframe 算出的 character_id 必须等于 md5(name|project_id)[:12]。

    portrait 侧 identity_master_resolver 用同一算法（fallback path）算 cid，
    所以两者一致才能让 character_bindings 在 keyframe pipeline 里 key 到
    identity_contracts，从而拿到 reference image。
    """
    sb = _make_story_bible(project_id)
    prompts = prompt_builder_service.build_keyframe_prompts(
        chapter_content="林夜站在庭院里，苏晚晴走过来。",
        chapter_outline=_make_outline(),
        story_bible=sb,
        max_keyframes=1,
    )
    assert prompts, "build_keyframe_prompts returned empty"

    # 收集 prompts 里所有 character_bindings 的 cid
    seen_cids: dict[str, str] = {}
    for p in prompts:
        for b in p.get("character_bindings") or []:
            seen_cids[b.character_name] = b.character_id

    # 至少命中一个被测角色
    assert seen_cids, "no character_bindings emitted"

    for name, cid_db in seen_cids.items():
        cid_expected = _expected_cid(name, project_id)
        assert cid_db == cid_expected, (
            f"keyframe cid mismatch for {name!r}: "
            f"db={cid_db} expected(md5(name|{project_id}))={cid_expected}"
        )


def test_keyframe_cid_pid_zero_is_a_regression():
    """如果 sb_raw 没有 project_id（fallback pid=0），cid 一定算错。

    这是历史 bug 的回归保护：把 project_id 从 sb_raw 里删掉，
    cid 必须不等于真实 pid 下算出来的值。否则说明算法本身变了。
    """
    sb = _make_story_bible(project_id=15)
    del sb["project_id"]  # 模拟 bug 触发条件

    prompts = prompt_builder_service.build_keyframe_prompts(
        chapter_content="林夜站在庭院里，苏晚晴走过来。",
        chapter_outline=_make_outline(),
        story_bible=sb,
        max_keyframes=1,
    )
    seen_cids: dict[str, str] = {}
    for p in prompts:
        for b in p.get("character_bindings") or []:
            seen_cids[b.character_name] = b.character_id

    # 在 pid=0 fallback 下，cid 必须不等于真实 pid 下的 cid
    for name, cid_db in seen_cids.items():
        cid_real = _expected_cid(name, 15)
        cid_zero = _expected_cid(name, 0)
        assert cid_db == cid_zero, (
            f"expected pid=0 fallback cid for {name!r}, got {cid_db}"
        )
        assert cid_db != cid_real, (
            f"pid=0 fallback accidentally produced real cid for {name!r} "
            f"— test setup is wrong"
        )


def test_file_sha256_helper_returns_hex_digest(tmp_path: Path):
    """``_file_sha256`` 必须返回 64-char hex digest，且与 hashlib.sha256 一致。

    历史 bug：``generate_portrait`` 从未给 ``source_image_sha256`` 赋值，
    导致下游 ``KeyframeCharacterBinding.has_reference()`` 因三件套缺一而
    返回 False，keyframe 全部走 text-only fallback，画师自由发挥 → 性别漂移。
    """
    payload = b"identity-reference-bytes"
    p = tmp_path / "ref.png"
    p.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()

    got = ImageGenerationService._file_sha256(p)
    assert got == expected, f"sha256 mismatch: got={got} expected={expected}"
    assert len(got) == 64, f"sha256 hex length wrong: {len(got)}"
    assert all(c in "0123456789abcdef" for c in got), "non-hex char in digest"


def test_file_sha256_helper_handles_missing_paths():
    """``_file_sha256`` 对 None / 不存在的路径必须返回 ""，不能抛异常。

    identity_master 流程不希望因 sha 计算失败整体崩溃——空值会让 binding
    走 fallback 而非 raise，对用户更友好。
    """
    assert ImageGenerationService._file_sha256(None) == ""
    assert ImageGenerationService._file_sha256(Path("/nonexistent/no-such.png")) == ""
