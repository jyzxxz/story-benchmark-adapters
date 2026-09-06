"""Shared limits and validation for outline generation."""
from __future__ import annotations


MIN_OUTLINE_CHAPTER_COUNT = 1
MAX_OUTLINE_CHAPTER_COUNT = 500
DEFAULT_OUTLINE_CHAPTER_COUNT = 13
MAX_OUTLINE_INSTRUCTIONS_CHARS = 12_000


def validate_outline_chapter_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("chapter_count must be an integer")
    if not MIN_OUTLINE_CHAPTER_COUNT <= value <= MAX_OUTLINE_CHAPTER_COUNT:
        raise ValueError(
            f"chapter_count must be between {MIN_OUTLINE_CHAPTER_COUNT} "
            f"and {MAX_OUTLINE_CHAPTER_COUNT}"
        )
    return value


def validate_outline_instructions(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("instructions must be a string or null")
    if len(value) > MAX_OUTLINE_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"instructions must not exceed {MAX_OUTLINE_INSTRUCTIONS_CHARS} characters"
        )
    return value
