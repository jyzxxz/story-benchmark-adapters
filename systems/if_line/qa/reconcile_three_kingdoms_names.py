#!/usr/bin/env python3
"""Reconcile vernacular Three Kingdoms character names with a source EPUB."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


CHAPTER_COUNT = 120
EXPECTED_PARAGRAPH_COUNT = 3019

# The aliases are ordered longest-first when matched. Replacements are only made
# when source and target have the same number of mentions in an aligned block.
CHARACTER_ALIAS_GROUPS = {
    "刘备": [
        "刘玄德",
        "玄德公",
        "刘皇叔",
        "刘豫州",
        "刘先主",
        "汉中王",
        "汉王",
        "玄德",
        "皇叔",
        "先主",
        "刘备",
    ],
    "关羽": ["关云长", "美髯公", "汉寿侯", "关公", "云长", "关羽"],
    "张飞": ["张益德", "猛张飞", "益德", "张飞"],
    "曹操": ["曹孟德", "曹阿瞒", "曹丞相", "曹公", "孟德", "阿瞒", "老瞒", "曹操"],
    "袁绍": ["袁本初", "本初", "袁绍"],
    "孙策": ["孙伯符", "小霸王", "伯符", "孙策"],
    "孙权": ["孙仲谋", "碧眼儿", "仲谋", "吴侯", "孙权"],
    "董卓": ["董太师", "董贼", "太师", "董卓"],
    "吕布": ["吕奉先", "吕温侯", "奉先", "温侯", "吕布"],
    "王允": ["王司徒", "司徒", "王允"],
    "何进": ["何国舅", "国舅", "何进"],
    "陶谦": ["陶恭祖", "恭祖", "陶谦"],
    "贾诩": ["贾文和", "文和", "贾诩"],
    "祢衡": ["祢正平", "正平", "祢衡"],
    "董承": ["董国舅", "董承"],
    "徐庶": ["徐元直", "元直", "单福", "徐庶"],
    "诸葛亮": ["诸葛孔明", "武乡侯", "汉丞相", "诸葛亮", "孔明", "卧龙", "武侯"],
    "赵云": ["赵子龙", "子龙", "赵云"],
    "鲁肃": ["鲁子敬", "子敬", "鲁肃"],
    "周瑜": ["周公瑾", "公瑾", "周瑜"],
    "黄忠": ["黄汉升", "老黄忠", "汉升", "黄忠"],
    "张辽": ["张文远", "文远", "张辽"],
    "马超": ["马孟起", "孟起", "马超"],
    "庞统": ["庞士元", "士元", "凤雏", "庞统"],
    "庞德": ["庞令明", "令明", "庞德"],
    "吕蒙": ["吕子明", "子明", "吕蒙"],
    "徐晃": ["徐公明", "公明", "徐晃"],
    "姜维": ["姜伯约", "伯约", "姜维"],
    "司马懿": ["司马懿", "仲达"],
    "邓艾": ["邓士载", "士载", "邓艾"],
}

TITLE_ONLY_ALIAS_GROUPS = {
    # The source's chapter 109 title abbreviates 司马昭 as 司马.
    "司马昭": ["司马昭", "司马"],
}

TITLE_PHRASE_CORRECTIONS = [
    (70, "勇猛的猛张飞", "猛张飞"),
    (70, "老将老黄忠", "老黄忠"),
]

# These are not stylistic alias changes. They are names corrupted or invented
# by the vernacular rewrite and are therefore corrected at exact locations.
EXACT_CORRECTIONS = [
    (2, 11, "汉武帝", "灵帝", 1, "跨时代错名"),
    (2, 11, "东方朔", "刘陶", 1, "跨时代错名"),
    (9, 10, "王答应了他意怎么样", "王允的意思怎么样", 1, "姓名被动词短语替换"),
    (10, 6, "韩于是", "韩遂", 1, "姓名被连词替换"),
    (19, 8, "孙看了看徒", "孙观之徒", 1, "姓名被动词短语替换"),
    (29, 12, "即焚死在吉", "就烧死于吉", 1, "姓名被介词短语拆分"),
    (30, 17, "蒋觉得他很不一般兵", "蒋奇之兵", 1, "姓名被动词短语替换"),
    (30, 19, "蒋觉得他很不一般兵", "蒋奇之兵", 1, "姓名被动词短语替换"),
    (38, 28, "我粲", "吾粲", 1, "姓氏误译"),
    (43, 8, "韩相信了良谋", "韩信的良谋", 1, "姓名被动词短语替换"),
    (44, 33, "黄因为", "黄盖", 1, "姓名被连词替换"),
    (58, 6, "于是将出曹操书", "韩遂将出曹操书", 1, "姓名被连词替换"),
    (58, 7, "韩于是", "韩遂", 1, "姓名被连词替换"),
    (58, 16, "韩于是", "韩遂", 1, "姓名被连词替换"),
    (58, 19, "于是说：\u201c我闻曹操", "韩遂说：\u201c我闻曹操", 1, "姓名被连词替换"),
    (58, 19, "于是说：\u201c今操渡河", "韩遂说：\u201c今操渡河", 1, "姓名被连词替换"),
    (58, 19, "于是说：\u201c贤侄守寨", "韩遂说：\u201c贤侄守寨", 1, "姓名被连词替换"),
    (
        58,
        20,
        "于是说：\u201c须分派军队",
        "韩遂说：\u201c须分派军队",
        1,
        "姓名被连词替换",
    ),
    (59, 4, "韩于是", "韩遂", 1, "姓名被连词替换"),
    (59, 6, "韩于是", "韩遂", 3, "姓名被连词替换"),
    (59, 7, "韩于是", "韩遂", 3, "姓名被连词替换"),
    (59, 7, "于是说", "韩遂说", 2, "姓名被连词替换"),
    (59, 9, "于是说", "韩遂说", 4, "姓名被连词替换"),
    (59, 10, "韩于是引", "韩遂引", 1, "姓名被连词替换"),
    (59, 10, "于是说：\u201c贤侄休疑", "韩遂说：\u201c贤侄休疑", 1, "姓名被连词替换"),
    (59, 10, "于是说：\u201c我与马腾", "韩遂说：\u201c我与马腾", 1, "姓名被连词替换"),
    (
        59,
        10,
        "于是说：\u201c谁可以通消息",
        "韩遂说：\u201c谁可以通消息",
        1,
        "姓名被连词替换",
    ),
    (59, 10, "于是乃写密书", "韩遂乃写密书", 1, "姓名被连词替换"),
    (59, 10, "于是非常高兴，就令", "韩遂非常高兴，就令", 1, "姓名被连词替换"),
    (59, 11, "于是慌以手", "韩遂慌以手", 1, "姓名被连词替换"),
    (59, 15, "萧为什么事", "萧何故事", 1, "姓名被疑问短语替换"),
    (
        65,
        9,
        "令其撤调转马头超兵",
        "令其撤回马超的军队",
        1,
        "姓名被动作短语拆分",
    ),
    (117, 20, "他的儿子诸葛还在城上", "他的儿子诸葛尚在城上", 1, "姓名被副词短语替换"),
    (120, 32, "我彦", "吾彦", 1, "姓氏误译"),
    (120, 43, "韩于是", "韩遂", 1, "姓名被连词替换"),
]


def decode_html_entities(value: str) -> str:
    return html.unescape(value)


def html_to_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = decode_html_entities(value)
    return re.sub(r"[\t\r\n\u00a0\u3000 ]+", " ", value).strip()


def extract_chapter_lines(raw_html: str) -> list[str]:
    cleaned = re.sub(r"<!--[\s\S]*?-->", "", raw_html)
    cleaned = re.sub(r"<script\b[\s\S]*?</script>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style\b[\s\S]*?</style>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<sup\b[\s\S]*?</sup>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r'<span\b[^>]*class="c"[^>]*>[\s\S]*?</span>',
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^[\s\S]*?</h1>", "", cleaned, count=1, flags=re.IGNORECASE)
    cleaned = re.split(
        r'<div\b[^>]*class="fnote\d?"[^>]*>', cleaned, maxsplit=1, flags=re.IGNORECASE
    )[0]

    lines: list[str] = []
    block_pattern = re.compile(
        r"<blockquote\b[^>]*>([\s\S]*?)</blockquote>|<p\b[^>]*>([\s\S]*?)</p>",
        flags=re.IGNORECASE,
    )
    for match in block_pattern.finditer(cleaned):
        if match.group(1) is not None:
            poem_pattern = re.compile(r"<p\b[^>]*>([\s\S]*?)</p>", flags=re.IGNORECASE)
            lines.extend(
                html_to_text(poem.group(1))
                for poem in poem_pattern.finditer(match.group(1))
            )
        else:
            lines.append(html_to_text(match.group(2)))

    return [
        line
        for line in lines
        if line
        and not re.search(r"更多好书.*公众号|sanqiujun", line, flags=re.IGNORECASE)
    ]


def read_source_epub(epub_path: Path) -> tuple[list[str], list[list[str]]]:
    with zipfile.ZipFile(epub_path) as archive:
        toc_html = archive.read("text/part0000_split_000.html").decode("utf-8")
        entries = [
            (href, html_to_text(label))
            for href, label in re.findall(
                r'<a\b[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>',
                toc_html,
                flags=re.IGNORECASE,
            )
            if re.match(r"^第.+回(?:\s|　)", html_to_text(label))
        ]
        if len(entries) != CHAPTER_COUNT:
            raise ValueError(
                f"EPUB 目录应有 {CHAPTER_COUNT} 回，实际为 {len(entries)} 回"
            )

        titles: list[str] = []
        chapters: list[list[str]] = []
        for href, title in entries:
            source_file = href.split("#", maxsplit=1)[0]
            source_html = archive.read(f"text/{source_file}").decode("utf-8")
            titles.append(title)
            chapters.append(extract_chapter_lines(source_html))

    paragraph_count = sum(len(chapter) for chapter in chapters)
    if paragraph_count != EXPECTED_PARAGRAPH_COUNT:
        raise ValueError(
            f"EPUB 正文应有 {EXPECTED_PARAGRAPH_COUNT} 段，实际为 {paragraph_count} 段"
        )
    return titles, chapters


def chapter_text_fields(graph: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for node in graph.get("Nodes", []):
        if node.get("Index", 0) < 10:
            continue
        items = node.get("Data", {}).get("Lines", {}).get("Items", [])
        for item in items:
            text_field = item.get("ObjectValue", {}).get("Text")
            if not isinstance(text_field, dict) or not isinstance(
                text_field.get("StringValue"), str
            ):
                raise ValueError(f"正文节点 {node.get('Index')} 缺少 Text.StringValue")
            fields.append(text_field)
    return fields


def alias_pattern(aliases: list[str]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
    )
    return re.compile(alternatives)


def transfer_alias_mentions(
    source: str,
    target: str,
    groups: dict[str, list[str]],
    *,
    scope: str,
    chapter: int,
    paragraph: int | None,
    replacements: list[dict[str, Any]],
) -> str:
    for canonical_name, aliases in groups.items():
        pattern = alias_pattern(aliases)
        source_mentions = [match.group(0) for match in pattern.finditer(source)]
        target_matches = list(pattern.finditer(target))
        target_mentions = [match.group(0) for match in target_matches]

        if source_mentions == target_mentions or len(source_mentions) != len(
            target_mentions
        ):
            continue

        parts: list[str] = []
        cursor = 0
        for mention_index, (match, desired) in enumerate(
            zip(target_matches, source_mentions, strict=True),
            start=1,
        ):
            parts.append(target[cursor : match.start()])
            parts.append(desired)
            if match.group(0) != desired:
                replacements.append(
                    {
                        "scope": scope,
                        "chapter": chapter,
                        "paragraph": paragraph,
                        "entity": canonical_name,
                        "mentionIndex": mention_index,
                        "before": match.group(0),
                        "after": desired,
                        "reason": "回填 EPUB 对应称谓",
                    }
                )
            cursor = match.end()
        parts.append(target[cursor:])
        target = "".join(parts)
    return target


def apply_exact_corrections(
    chapter: int,
    paragraph: int,
    target: str,
    replacements: list[dict[str, Any]],
) -> str:
    for (
        correction_chapter,
        correction_paragraph,
        before,
        after,
        expected_count,
        reason,
    ) in EXACT_CORRECTIONS:
        if (chapter, paragraph) != (correction_chapter, correction_paragraph):
            continue
        occurrences = target.count(before)
        if occurrences not in (0, expected_count):
            raise ValueError(
                f"第 {chapter} 回第 {paragraph} 段的精确校正项 {before!r} "
                f"应出现 {expected_count} 次，实际为 {occurrences} 次"
            )
        if occurrences == expected_count:
            target = target.replace(before, after)
            for mention_index in range(1, expected_count + 1):
                replacements.append(
                    {
                        "scope": "body",
                        "chapter": chapter,
                        "paragraph": paragraph,
                        "entity": after,
                        "mentionIndex": mention_index,
                        "before": before,
                        "after": after,
                        "reason": reason,
                    }
                )
        elif after not in target:
            raise ValueError(
                f"第 {chapter} 回第 {paragraph} 段既没有待校正项 {before!r}，也没有结果 {after!r}"
            )
    return target


def apply_title_phrase_corrections(
    chapter: int,
    target: str,
    replacements: list[dict[str, Any]],
) -> str:
    for correction_chapter, before, after in TITLE_PHRASE_CORRECTIONS:
        if chapter != correction_chapter:
            continue
        if before in target:
            target = target.replace(before, after, 1)
            replacements.append(
                {
                    "scope": "title",
                    "chapter": chapter,
                    "paragraph": None,
                    "entity": after,
                    "mentionIndex": 1,
                    "before": before,
                    "after": after,
                    "reason": "消除称谓回填后的重复修饰",
                }
            )
        elif after not in target:
            raise ValueError(
                f"第 {chapter} 回标题既没有待校正项 {before!r}，也没有结果 {after!r}"
            )
    return target


def reconcile(
    epub_path: Path,
    target_directory: Path,
    *,
    check_only: bool,
) -> dict[str, Any]:
    source_titles, source_chapters = read_source_epub(epub_path)
    catalog_path = target_directory / "catalog.json"
    chapters_directory = target_directory / "chapters"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_entries = catalog.get("chapters", [])
    if (
        catalog.get("chapterCount") != CHAPTER_COUNT
        or len(catalog_entries) != CHAPTER_COUNT
    ):
        raise ValueError("目标目录不是完整的 120 回目录")

    replacements: list[dict[str, Any]] = []
    changed_files: list[str] = []
    total_paragraphs = 0

    for chapter_index in range(CHAPTER_COUNT):
        chapter_number = chapter_index + 1
        chapter_path = chapters_directory / f"{chapter_number:03}.json"
        graph = json.loads(chapter_path.read_text(encoding="utf-8"))
        text_fields = chapter_text_fields(graph)
        source_lines = source_chapters[chapter_index]
        if len(text_fields) != len(source_lines):
            raise ValueError(
                f"第 {chapter_number} 回段落数不一致：EPUB={len(source_lines)}，目标={len(text_fields)}"
            )

        chapter_changed = False
        for paragraph_index, (source_line, text_field) in enumerate(
            zip(source_lines, text_fields, strict=True),
            start=1,
        ):
            before = text_field["StringValue"]
            after = transfer_alias_mentions(
                source_line,
                before,
                CHARACTER_ALIAS_GROUPS,
                scope="body",
                chapter=chapter_number,
                paragraph=paragraph_index,
                replacements=replacements,
            )
            after = apply_exact_corrections(
                chapter_number,
                paragraph_index,
                after,
                replacements,
            )
            if after != before:
                text_field["StringValue"] = after
                chapter_changed = True

        if chapter_changed:
            changed_files.append(str(chapter_path))
            if not check_only:
                chapter_path.write_text(
                    json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )

        title_before = catalog_entries[chapter_index]["title"]
        title_after = transfer_alias_mentions(
            source_titles[chapter_index],
            title_before,
            CHARACTER_ALIAS_GROUPS | TITLE_ONLY_ALIAS_GROUPS,
            scope="title",
            chapter=chapter_number,
            paragraph=None,
            replacements=replacements,
        )
        title_after = apply_title_phrase_corrections(
            chapter_number,
            title_after,
            replacements,
        )
        catalog_entries[chapter_index]["title"] = title_after

        current_lines = [field["StringValue"] for field in text_fields]
        catalog_entries[chapter_index]["paragraphCount"] = len(current_lines)
        catalog_entries[chapter_index]["characterCount"] = sum(
            len(line) for line in current_lines
        )
        excerpt = current_lines[0]
        catalog_entries[chapter_index]["excerpt"] = (
            f"{excerpt[:180]}…" if len(excerpt) > 180 else excerpt
        )
        total_paragraphs += len(current_lines)

    if total_paragraphs != EXPECTED_PARAGRAPH_COUNT:
        raise ValueError(
            f"目标正文应有 {EXPECTED_PARAGRAPH_COUNT} 段，实际为 {total_paragraphs} 段"
        )

    original_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog != original_catalog:
        changed_files.append(str(catalog_path))
        if not check_only:
            catalog_path.write_text(
                f"{json.dumps(catalog, ensure_ascii=False, indent=2)}\n",
                encoding="utf-8",
            )

    body_alias_count = sum(
        item["scope"] == "body" and item["reason"] == "回填 EPUB 对应称谓"
        for item in replacements
    )
    exact_count = sum(
        item["scope"] == "body" and item["reason"] != "回填 EPUB 对应称谓"
        for item in replacements
    )
    title_count = sum(item["scope"] == "title" for item in replacements)
    return {
        "source": str(epub_path),
        "sourceSha256": hashlib.sha256(epub_path.read_bytes()).hexdigest(),
        "target": str(target_directory),
        "chapterCount": CHAPTER_COUNT,
        "paragraphCount": total_paragraphs,
        "bodyAliasReplacementCount": body_alias_count,
        "exactCorrectionCount": exact_count,
        "titleReplacementCount": title_count,
        "totalReplacementCount": len(replacements),
        "changedFiles": changed_files,
        "replacements": replacements,
    }


def merge_report(current: dict[str, Any], report_path: Path) -> dict[str, Any]:
    if not report_path.exists():
        return current

    previous = json.loads(report_path.read_text(encoding="utf-8"))
    for field in ("sourceSha256", "target"):
        if previous.get(field) != current.get(field):
            raise ValueError(f"既有报告的 {field} 与本次任务不一致")

    merged_replacements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for replacement in previous.get("replacements", []) + current["replacements"]:
        key = json.dumps(replacement, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            merged_replacements.append(replacement)

    merged = current | {
        "changedFiles": list(
            dict.fromkeys(previous.get("changedFiles", []) + current["changedFiles"])
        ),
        "replacements": merged_replacements,
    }
    merged["bodyAliasReplacementCount"] = sum(
        item["scope"] == "body" and item["reason"] == "回填 EPUB 对应称谓"
        for item in merged_replacements
    )
    merged["exactCorrectionCount"] = sum(
        item["scope"] == "body" and item["reason"] != "回填 EPUB 对应称谓"
        for item in merged_replacements
    )
    merged["titleReplacementCount"] = sum(
        item["scope"] == "title" for item in merged_replacements
    )
    merged["totalReplacementCount"] = len(merged_replacements)
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="毛宗岗批评本《三国演义》EPUB")
    parser.add_argument("target", type=Path, help="frontend/public/three-kingdoms 目录")
    parser.add_argument("--report", type=Path, help="写入 JSON 替换报告")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验；如果仍需替换或更新目录统计，则返回非零状态",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = reconcile(
        args.epub.resolve(), args.target.resolve(), check_only=args.check
    )
    if args.check:
        if result["changedFiles"] or result["totalReplacementCount"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "chapterCount": result["chapterCount"],
                    "paragraphCount": result["paragraphCount"],
                    "sourceSha256": result["sourceSha256"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        result = merge_report(result, args.report)
        args.report.write_text(
            f"{json.dumps(result, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "replacements"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
