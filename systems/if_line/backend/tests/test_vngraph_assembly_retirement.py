from __future__ import annotations

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"


def test_legacy_index_assembly_modules_are_removed():
    assert not (APP_ROOT / "services" / "vn_graph_assembler.py").exists()
    assert not (
        APP_ROOT / "services" / "chapter_vngraph_assembly_service.py"
    ).exists()


def test_active_backend_cannot_restore_index_based_assembly_entrypoints():
    forbidden = (
        "VNGraphAssembler",
        "ChapterVNGraphAssemblyService",
        "assemble_full_chapter",
        "vn_graph_assembler",
        "chapter_vngraph_assembly_service",
    )
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in content:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}: {token}")

    assert violations == []
