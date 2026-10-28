"""Planning workspace endpoints. Members are scoped to their own organization;
admins pass ``organization_id``. Uploads send the raw file as the request body."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import CurrentActor, Db, SettingsDep
from app.errors import PayloadTooLarge
from app.schemas import (
    DocumentDetail,
    DocumentOut,
    NoteIn,
    PlansOut,
    ProfileIn,
    ProfileOut,
    SuggestedProfile,
)
from app.services import planning

router = APIRouter(prefix="/api/planning", tags=["planning"])

OrgId = Annotated[int | None, Query(alias="organization_id", ge=1)]
Filename = Annotated[str, Query(min_length=1, max_length=255)]


@router.get("/profile", response_model=ProfileOut)
async def get_profile(db: Db, actor: CurrentActor, organization_id: OrgId = None) -> ProfileOut:
    org = await planning.resolve_org(db, actor, organization_id)
    return await planning.get_profile(db, org)


@router.put("/profile", response_model=ProfileOut)
async def save_profile(
    body: ProfileIn, db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> ProfileOut:
    org = await planning.resolve_org(db, actor, organization_id)
    await planning.save_profile(db, actor, org, version=body.version, preferences=body.preferences)
    await db.commit()
    return await planning.get_profile(db, org)


@router.get("/profile/suggestion", response_model=SuggestedProfile)
async def suggest_profile(
    db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> SuggestedProfile:
    org = await planning.resolve_org(db, actor, organization_id)
    return await planning.suggest(db, org)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> list[DocumentOut]:
    org = await planning.resolve_org(db, actor, organization_id)
    return await planning.list_documents(db, org)


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    request: Request,
    filename: Filename,
    db: Db,
    actor: CurrentActor,
    settings: SettingsDep,
    organization_id: OrgId = None,
) -> DocumentOut:
    org = await planning.resolve_org(db, actor, organization_id)
    # Counted while streaming as well: chunked bodies carry no Content-Length.
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.max_upload_bytes:
            raise PayloadTooLarge(
                f"Files can be at most {settings.max_upload_bytes // (1024 * 1024)} MB"
            )
        chunks.append(chunk)
    out = await planning.upload_document(
        db,
        actor,
        org,
        filename=filename,
        content_type=request.headers.get("content-type"),
        data=b"".join(chunks),
    )
    await db.commit()
    return out


@router.post("/notes", response_model=DocumentOut, status_code=201)
async def add_note(
    body: NoteIn, db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> DocumentOut:
    org = await planning.resolve_org(db, actor, organization_id)
    out = await planning.add_note(db, actor, org, title=body.title, body=body.body)
    await db.commit()
    return out


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def document_detail(
    document_id: int, db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> DocumentDetail:
    org = await planning.resolve_org(db, actor, organization_id)
    return await planning.document_detail(db, org, document_id)


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: int, db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> Response:
    org = await planning.resolve_org(db, actor, organization_id)
    doc, content = await planning.document_content(db, org, document_id)
    name = doc.filename or "document"
    # Always a download, never rendered inline: uploaded HTML or SVG cannot run here.
    return Response(
        content,
        media_type=doc.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: int, db: Db, actor: CurrentActor, organization_id: OrgId = None
) -> Response:
    org = await planning.resolve_org(db, actor, organization_id)
    await planning.delete_document(db, actor, org, document_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/plans", response_model=PlansOut)
async def get_plans(db: Db, actor: CurrentActor, organization_id: OrgId = None) -> PlansOut:
    org = await planning.resolve_org(db, actor, organization_id)
    return await planning.plans(db, org)
