"""One-pass display projection with offsets into the immutable chapter source.

This is a prose-formatting projection, not an HTML renderer or sanitizer. Only
known formatting tags are layout. Unknown tags, comments and script/style text
stay literal; escaped markup is decoded once and never parsed again.
"""
from __future__ import annotations

from html import unescape
from html.entities import html5
import re


CHAPTER_TEXT_PROJECTION_VERSION = "html-formatting-entities-v1"
_BLOCK_TAGS = {"p", "div", "br", "hr", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "pre"}
_INLINE_TAGS = {"span", "strong", "em", "b", "i", "u", "s", "strike", "sub", "sup", "code", "a"}
_TAG = re.compile(
    r"</?(?P<name>[A-Za-z][A-Za-z0-9]*)(?=[\s/>])"
    r"(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>",
)
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")


def chapter_text_units(source: str) -> list[tuple[str, int, int]]:
    """Return (display character, raw start, raw end), including whitespace.

Multiple characters decoded from one entity share its raw interval. A caller
must not split that interval between source spans.
    """
    units: list[tuple[str, int, int]] = []
    cursor = 0
    while cursor < len(source):
        if source[cursor] == "<":
            if source.startswith("<!--", cursor):
                end = source.find("-->", cursor + 4)
                end = len(source) if end < 0 else end + 3
                units.extend((source[idx], idx, idx + 1) for idx in range(cursor, end))
                cursor = end
                continue
            match = _TAG.match(source, cursor)
            if match and match["name"].lower() in {"script", "style"} and not source.startswith("</", cursor):
                close = re.search(r"</" + match["name"] + r"\s*>", source[match.end():], re.IGNORECASE)
                end = len(source) if close is None else match.end() + close.end()
                units.extend((source[idx], idx, idx + 1) for idx in range(cursor, end))
                cursor = end
                continue
            if match and match["name"].lower() in _BLOCK_TAGS | _INLINE_TAGS:
                if match["name"].lower() in _BLOCK_TAGS:
                    units.append(("\n", cursor, match.end()))
                cursor = match.end()
                continue
        if source[cursor] == "&":
            match = _ENTITY.match(source, cursor)
            if match:
                token = match.group()
                if token[1] == "#" or token[1:] in html5:
                    # html.unescape may discard invalid control references.
                    # Preserve such tokens literally instead of losing source.
                    decoded = unescape(token)
                    if decoded:
                        units.extend((ch, cursor, match.end()) for ch in decoded)
                        cursor = match.end()
                        continue
        units.append((source[cursor], cursor, cursor + 1))
        cursor += 1
    return units


def chapter_display_text(source: str) -> str:
    return "".join(ch for ch, _, _ in chapter_text_units(source))
