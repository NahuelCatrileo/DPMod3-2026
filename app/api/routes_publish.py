"""US-C1 · Subtarea 1.2 — Endpoints de publicación.

Rutas y códigos según Guía Técnica §5. No los cambien sin §5.4.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    ErrorResponse,
    ScheduleRequest,
    ScheduleResponse,
    StatusResponse,
)
from app.domain.errors import PublicationNotFoundError
from app.infra.db import get_session
from app.services import scheduling

router = APIRouter(prefix="/api/publish", tags=["publish"])

_ERROR_RESPONSES = {
    409: {"model": ErrorResponse, "description": "SCHEDULE_CONFLICT"},
    422: {"model": ErrorResponse, "description": "INVALID_METADATA"},
}


@router.post(
    "/schedule",
    response_model=ScheduleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERROR_RESPONSES,
    summary="Programar la publicación de un contenido (US-C1)",
)
def schedule(
    body: ScheduleRequest,
    session: Session = Depends(get_session),
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> ScheduleResponse:
    publication = scheduling.schedule_publication(
        session,
        content_id=body.contentId,
        schedule_at=body.scheduleAt,
        tz_name=body.timezone,
        correlation_id=x_correlation_id,
    )
    return ScheduleResponse(
        publishId=publication.id,
        state=publication.state,
        scheduleAt=publication.schedule_at,
        timezone=publication.timezone,
        correlationId=publication.correlation_id,
    )


@router.get(
    "/{publish_id}/status",
    response_model=StatusResponse,
    responses={404: {"model": ErrorResponse, "description": "CONTENT_NOT_FOUND"}},
    summary="Estado de una publicación (US-C6)",
)
def get_status(
    publish_id: str, session: Session = Depends(get_session)
) -> StatusResponse:
    publication = scheduling.get_publication(session, publish_id)
    if publication is None:
        raise PublicationNotFoundError(publish_id)
    return StatusResponse(
        publishId=publication.id,
        state=publication.state,
        scheduleAt=publication.schedule_at,
        timezone=publication.timezone,
        attempts=publication.attempts,
        youtubeVideoId=publication.youtube_video_id,
        lastError=publication.last_error,
        correlationId=publication.correlation_id,
    )
