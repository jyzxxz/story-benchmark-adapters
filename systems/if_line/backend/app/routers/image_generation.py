"""Image provider health endpoint.

All image writes are owned by Chapter Script resource render tasks. This
router deliberately exposes no generation operation.
"""
from fastapi import APIRouter

from app.services.image_generation_service import (
    AI_IMAGE_MODEL,
    IMAGE_GENERATION_ENABLED,
)
from app.utils import logging as xlog


router = APIRouter()


@router.get("/status")
async def get_image_generation_status():
    xlog.info(
        0,
        "[image] status enabled=%s model=%s",
        IMAGE_GENERATION_ENABLED,
        AI_IMAGE_MODEL,
    )
    return {
        "enabled": IMAGE_GENERATION_ENABLED,
        "model": AI_IMAGE_MODEL,
    }
