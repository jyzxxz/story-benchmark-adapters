from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_IDENTITY_MODULES = (
    "app/routers/v2/authoring_bible.py",
    "app/routers/v2/authoring_outlines.py",
    "app/routers/v2/authoring_chapters.py",
    "app/routers/v2/authoring_candidates.py",
    "app/routers/v2/authoring_scripts.py",
    "app/routers/v2/authoring_vn_graphs.py",
    "app/application/story_bible_service.py",
    "app/application/story_outline_service.py",
    "app/application/story_chapter_service.py",
    "app/application/story_chapter_batch_service.py",
    "app/application/story_generation_service.py",
    "app/application/story_context_resolver.py",
    "app/application/story_branch_contract.py",
    "app/application/story_branch_service.py",
    "app/application/candidate_set_review_service.py",
    "app/application/chapter_script_service.py",
    "app/application/vn_graph_service.py",
    "app/application/publication_readiness_service.py",
    "app/workers/branch_tasks.py",
    "app/workers/tasks.py",
    "app/services/vn_graph_compiler.py",
)

FORBIDDEN_MODERN_IMPORTS = {
    "app.application.branch_generation_service",
    "app.application.chapter_batch_service",
    "app.application.legacy_chapter_script_queries",
    "app.application.revision_service",
    "app.routers.v2.branch_generation",
    "app.routers.v2.chapter_scripts",
    "app.routers.v2.vn_graphs",
}

QUERY_METHODS = {
    "filter",
    "filter_by",
    "group_by",
    "having",
    "join",
    "order_by",
    "outerjoin",
    "where",
}


def _contains_chapter_index(node: ast.AST) -> bool:
    return any(
        (isinstance(child, ast.Attribute) and child.attr == "chapter_index")
        or (isinstance(child, ast.Name) and child.id == "chapter_index")
        or (isinstance(child, ast.Constant) and child.value == "chapter_index")
        for child in ast.walk(node)
    )


def _function_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]


def test_modern_story_path_modules_forbid_chapter_index_identity_joins():
    violations: list[str] = []
    for relative_path in ACTIVE_IDENTITY_MODULES:
        path = BACKEND_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                argument.arg == "chapter_index" for argument in _function_args(node)
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: function accepts chapter_index identity"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in QUERY_METHODS
                and _contains_chapter_index(node)
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: query uses chapter_index"
                )
            if isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MODERN_IMPORTS:
                violations.append(
                    f"{relative_path}:{node.lineno}: imports {node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_MODERN_IMPORTS:
                        violations.append(
                            f"{relative_path}:{node.lineno}: imports {alias.name}"
                        )
    assert violations == []


def test_sequence_based_authoring_router_modules_are_deleted():
    removed = (
        "app/routers/v2/branch_generation.py",
        "app/routers/v2/chapter_scripts.py",
        "app/routers/v2/vn_graphs.py",
    )
    assert [path for path in removed if (BACKEND_ROOT / path).exists()] == []
