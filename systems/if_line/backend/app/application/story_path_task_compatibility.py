"""Cutover compatibility rules for tasks created by StoryPath authoring APIs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TaskSourceContract:
    container: str
    envelope: str
    version_field: str
    expected_version: str


@dataclass(frozen=True)
class TaskSourceCompatibility:
    managed: bool
    compatible: bool
    reason: str | None = None


STORY_PATH_TASK_SOURCE_CONTRACTS = {
    "outline.generate": TaskSourceContract(
        container="source_refs",
        envelope="outline_source",
        version_field="schema_version",
        expected_version="story-path-outline-source-v1",
    ),
    "chapter.generate": TaskSourceContract(
        container="source_refs",
        envelope="chapter_source",
        version_field="schema_version",
        expected_version="story-path-chapter-source-v1",
    ),
    "chapter.batch": TaskSourceContract(
        container="source_refs",
        envelope="chapter_batch_source",
        version_field="schema_version",
        expected_version="story-path-chapter-batch-source-v2",
    ),
    "branch.candidates.generate": TaskSourceContract(
        container="source_refs",
        envelope="candidate_set_source",
        version_field="schema_version",
        expected_version="story-path-candidate-set-source-v1",
    ),
    "chapter_script.generate": TaskSourceContract(
        container="source_refs",
        envelope="chapter_script_source",
        version_field="schema_version",
        expected_version="chapter-script-source-v1",
    ),
    "vngraph.compile": TaskSourceContract(
        container="parameters",
        envelope="binding_manifest",
        version_field="manifest_version",
        expected_version="vngraph-binding-v2",
    ),
}

STORY_PATH_TASK_KINDS = frozenset(STORY_PATH_TASK_SOURCE_CONTRACTS)


def inspect_story_path_task_source(
    kind: str,
    *,
    source_refs: Mapping[str, Any] | None,
    parameters: Mapping[str, Any] | None,
) -> TaskSourceCompatibility:
    """Classify a task without resolving any mutable authoring state."""

    contract = STORY_PATH_TASK_SOURCE_CONTRACTS.get(kind)
    if contract is None:
        return TaskSourceCompatibility(managed=False, compatible=True)

    container = source_refs if contract.container == "source_refs" else parameters
    if not isinstance(container, Mapping):
        return TaskSourceCompatibility(
            managed=True,
            compatible=False,
            reason="source_container_missing",
        )
    envelope = container.get(contract.envelope)
    if not isinstance(envelope, Mapping):
        return TaskSourceCompatibility(
            managed=True,
            compatible=False,
            reason="source_envelope_missing",
        )
    if envelope.get(contract.version_field) != contract.expected_version:
        return TaskSourceCompatibility(
            managed=True,
            compatible=False,
            reason="source_version_mismatch",
        )
    return TaskSourceCompatibility(managed=True, compatible=True)


__all__ = [
    "STORY_PATH_TASK_KINDS",
    "STORY_PATH_TASK_SOURCE_CONTRACTS",
    "TaskSourceCompatibility",
    "TaskSourceContract",
    "inspect_story_path_task_source",
]
