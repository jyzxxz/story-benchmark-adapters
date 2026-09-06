from __future__ import annotations

import pytest

from app.library_tags import extract_library_facet_tags


def _as_dict(specs):
    return {
        (spec.category, spec.value): spec.display_name
        for spec in specs
    }


def test_facet_extraction_normalizes_unicode_whitespace_case_and_arrays():
    specs = extract_library_facet_tags(
        taxonomy={
            "genre": [" Ｓｃｉ－Ｆｉ ", "Fantasy"],
            "location": "  Night   Street  ",
            "tokens": ["must", "not", "become", "facets"],
        },
    )

    assert _as_dict(specs) == {
        ("genre", "fantasy"): "Fantasy",
        ("genre", "sci-fi"): "Sci-Fi",
        ("location", "night street"): "Night Street",
    }


def test_dedicated_columns_and_explicit_categories_replace_automatic_values():
    specs = extract_library_facet_tags(
        taxonomy={
            "style": "taxonomy-style",
            "genre": ["historical", "fantasy"],
            "location": "forest",
        },
        style=" Dedicated Style ",
        explicit_facet_tags=[
            {
                "category": " Genre ",
                "value": "Mystery",
                "display_name": "悬疑",
            },
            {"category": "custom category", "value": "Value A"},
        ],
    )

    assert _as_dict(specs) == {
        ("custom_category", "value a"): "Value A",
        ("genre", "mystery"): "悬疑",
        ("location", "forest"): "forest",
        ("style", "dedicated style"): "Dedicated Style",
    }


def test_conflicting_display_names_for_one_normalized_tag_are_rejected():
    with pytest.raises(ValueError, match="conflicting display_name"):
        extract_library_facet_tags(
            taxonomy={},
            explicit_facet_tags=[
                {"category": "location", "value": "Street", "display_name": "街道"},
                {"category": "location", "value": "street", "display_name": "道路"},
            ],
        )
    with pytest.raises(ValueError, match="conflicting display_name"):
        extract_library_facet_tags(
            taxonomy={},
            explicit_facet_tags=[
                {"category": "style", "value": "XIANXIA", "display_name": "Xianxia"},
                {"category": "style", "value": "xianxia", "display_name": "xianxia"},
            ],
        )
