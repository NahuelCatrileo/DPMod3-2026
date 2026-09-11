"""Contratos de request/response. Guía Técnica §5."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleRequest(BaseModel):
    """POST /api/publish/schedule"""

    contentId: str = Field(..., min_length=1, max_length=64)
    scheduleAt: datetime = Field(
        ...,
        description="ISO-8601. Si viene sin offset se interpreta en `timezone`.",
    )
    timezone: str = Field(..., min_length=1, max_length=64, examples=["America/Santiago"])

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "contentId": "c-0001",
                    "scheduleAt": "2026-09-17T18:30:00-03:00",
                    "timezone": "America/Santiago",
                }
            ]
        }
    }


class ScheduleResponse(BaseModel):
    """202 Accepted — Guía Técnica §5: { publishId, state }"""

    publishId: str
    state: str
    scheduleAt: datetime
    timezone: str
    correlationId: str


class StatusResponse(BaseModel):
    """GET /api/publish/{id}/status — Guía Técnica §5"""

    publishId: str
    state: str
    scheduleAt: datetime
    timezone: str
    attempts: int
    youtubeVideoId: str | None = None
    lastError: str | None = None
    correlationId: str


class ErrorResponse(BaseModel):
    """Sobre de error estándar — Doc 1 §6.3"""

    status: str = "error"
    code: str
    message: str
