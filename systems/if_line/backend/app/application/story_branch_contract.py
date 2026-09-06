from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.schemas_branch_generation import GeneratedBranchCandidateBatch


BRANCH_GENERATION_TASK_KIND = "branch.candidates.generate"
BRANCH_GENERATION_EVENT = "branch.candidates.generate.requested"
BRANCH_CANDIDATE_CREDIT_COST = Decimal("3")


class BranchGenerationOutputError(ValueError):
    pass


def validate_generated_candidate_batch(
    data: Any,
    *,
    expected_count: int,
) -> GeneratedBranchCandidateBatch:
    try:
        batch = GeneratedBranchCandidateBatch.model_validate(data)
    except ValidationError as exc:
        raise BranchGenerationOutputError("LLM 候选输出不符合 schema") from exc
    if len(batch.candidates) != expected_count:
        raise BranchGenerationOutputError("LLM 候选数量与请求不一致")
    return batch


__all__ = [
    "BRANCH_CANDIDATE_CREDIT_COST",
    "BRANCH_GENERATION_EVENT",
    "BRANCH_GENERATION_TASK_KIND",
    "BranchGenerationOutputError",
    "validate_generated_candidate_batch",
]
