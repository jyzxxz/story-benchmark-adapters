from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.application.asset_service import (
    asset_read_dict,
    delete_project_asset,
    get_project_asset,
    get_project_asset_version,
    list_asset_bindings,
    list_asset_versions,
    list_logical_assets,
)
from app.application.visual_asset_service import upload_existing_asset_version
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.project_permissions import require_project_owner
from app.schemas_asset import (
    AssetBindingRead,
    AssetPage,
    AssetRead,
    AssetVersionRead,
)
from app.schemas_visual_asset import VisualAssetUploadRead
router = APIRouter(prefix="/projects/{project_id}", tags=["assets"])


# GET /api/projects/{project_id}/assets
@router.get("/assets", response_model=AssetPage)
def read_assets(
    project_id: int,
    asset_type: str | None = None,
    asset_status: str | None = Query(default=None, alias="status"),
    logical_key: str | None = None,
    chapter_index: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    assets, total = list_logical_assets(
        db,
        project_id=project_id,
        asset_type=asset_type,
        status=asset_status,
        logical_key=logical_key,
        chapter_index=chapter_index,
        limit=limit,
        offset=offset,
    )
    return AssetPage(
        items=[AssetRead(**asset) for asset in assets],
        total=total,
        limit=limit,
        offset=offset,
    )


# GET /api/projects/{project_id}/assets/{asset_id}
@router.get("/assets/{asset_id}", response_model=AssetRead)
def read_asset(
    project_id: int,
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    asset = get_project_asset(db, project_id, asset_id)
    return AssetRead(**asset_read_dict(db, asset))


# DELETE /api/projects/{project_id}/assets/{asset_id}
@router.delete("/assets/{asset_id}", response_model=AssetRead)
def delete_asset(
    project_id: int,
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    asset = delete_project_asset(db, project_id, asset_id)
    deleted = AssetRead(**asset_read_dict(db, asset))
    db.commit()
    return deleted


# GET /api/projects/{project_id}/assets/{asset_id}/versions
@router.get("/assets/{asset_id}/versions", response_model=list[AssetVersionRead])
def read_asset_versions(
    project_id: int,
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    return list_asset_versions(db, project_id, asset_id)


# GET /api/projects/{project_id}/asset-versions/{asset_version_id}
@router.get("/asset-versions/{asset_version_id}", response_model=AssetVersionRead)
def read_asset_version(
    project_id: int,
    asset_version_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    return get_project_asset_version(db, project_id, asset_version_id)


# POST /api/projects/{project_id}/assets/{asset_id}/versions/uploads
@router.post(
    "/assets/{asset_id}/versions/uploads",
    response_model=VisualAssetUploadRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset_version(
    project_id: int,
    asset_id: int,
    request: Request,
    file: UploadFile = File(...),
    description: str = Form(default=""),
    rights_attested: bool = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    settings = request.app.state.settings
    raw = await file.read(settings.visual_asset_upload_max_bytes + 1)
    if len(raw) > settings.visual_asset_upload_max_bytes:
        raise HTTPException(status_code=413, detail="图片不能超过 25 MB")
    result = upload_existing_asset_version(
        db,
        user_id=user.id,
        project_id=project_id,
        asset_id=asset_id,
        raw=raw,
        filename=file.filename or "upload",
        declared_content_type=file.content_type or "application/octet-stream",
        description=description,
        rights_attested=rights_attested,
        idempotency_key=idempotency_key or "",
    )
    db.commit()
    return result


# GET /api/projects/{project_id}/asset-bindings
@router.get("/asset-bindings", response_model=list[AssetBindingRead])
def read_bindings(
    project_id: int,
    asset_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    return list_asset_bindings(db, project_id=project_id, asset_id=asset_id)
