"""
L5.14 — Stage_Background_AR empty_required ablation 测试。

测试 _bbox_verdict 在 empty_required 场景下对任何 person bbox 都判 hard_fail。
（不调用真 YOLO 模型，直接构造 BBox 喂给 _bbox_verdict）
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BBox
from app.services.background_image_validator_service import BackgroundImageValidatorService


@pytest.fixture
def svc() -> BackgroundImageValidatorService:
    return BackgroundImageValidatorService()


# -------------------- empty_required ablation --------------------

def test_empty_required_any_person_hard_fails(svc):
    """empty_required + 任何 person bbox → hard_fail"""
    bboxes = [
        BBox(x1=0.1, y1=0.1, x2=0.2, y2=0.2, conf=0.5),
        BBox(x1=0.4, y1=0.4, x2=0.6, y2=0.6, conf=0.8),
        BBox(x1=0.45, y1=0.45, x2=0.55, y2=0.55, conf=0.9),
    ]
    for b in bboxes:
        verdict, reason = svc._bbox_verdict(bboxes=[b], mode="empty_required")
        assert verdict == "hard_fail", f"empty_required 应拒绝所有 person，got {verdict}"


def test_empty_required_no_person_passes(svc):
    """empty_required + 无 person → hard_pass"""
    verdict, reason = svc._bbox_verdict(bboxes=[], mode="empty_required")
    assert verdict == "hard_pass"


def test_background_people_optional_center_person_fails(svc):
    """optional + 中心 person → hard_fail"""
    center_bbox = BBox(x1=0.4, y1=0.4, x2=0.6, y2=0.6, conf=0.9)
    verdict, reason = svc._bbox_verdict(bboxes=[center_bbox], mode="background_people_optional")
    assert verdict == "hard_fail"


def test_background_people_optional_edge_small_suspicious(svc):
    """optional + 边角小 person → suspicious 或 hard_pass"""
    edge_bbox = BBox(x1=0.05, y1=0.05, x2=0.10, y2=0.10, conf=0.4)
    verdict, reason = svc._bbox_verdict(bboxes=[edge_bbox], mode="background_people_optional")
    assert verdict in ("suspicious", "hard_pass")


def test_groups_required_allows_small_distant(svc):
    """groups_required + 小远景人群 → 不 hard_fail"""
    small_far = BBox(x1=0.05, y1=0.05, x2=0.10, y2=0.10, conf=0.5)
    verdict, reason = svc._bbox_verdict(bboxes=[small_far], mode="background_groups_required")
    assert verdict != "hard_fail"
