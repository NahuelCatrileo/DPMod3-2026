"""US-C3.4 · Metadatos del Módulo 1 por REST.

Las pruebas no hablan con M1: reemplazan `urlopen` y comprueban el parseo de
la respuesta real documentada en `docs/contratos/rest.md`.
"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

import app.infra.publishers.youtube as youtube
from app.config import get_settings
from app.domain.errors import DomainError, ErrorCode
from app.infra.module1 import ContentMetadata, get_content_metadata


class _Respuesta:
    def __init__(self, payload: dict) -> None:
        self._cuerpo = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._cuerpo


def _payload(**metadata) -> dict:
    base = {
        "id": "version-1",
        "version": 1,
        "title": "Clase 1",
        "description": "Descripción de la clase",
        "tags": ["pubtube", "clase"],
        "visibility": "unlisted",
        "createdAt": "2026-10-23T12:00:00.000Z",
    }
    base.update(metadata)
    return {"status": 200, "contentId": "c-001", "metadata": base}


def _fake_urlopen(payload: dict, capturado: dict | None = None):
    def fake(request, timeout):
        if capturado is not None:
            capturado["url"] = request.full_url
            capturado["timeout"] = timeout
            capturado["headers"] = dict(request.header_items())
        return _Respuesta(payload)

    return fake


def test_get_content_metadata_parsea_la_respuesta_de_m1(monkeypatch):
    capturado: dict = {}
    monkeypatch.setattr(
        "app.infra.module1.urlopen", _fake_urlopen(_payload(), capturado)
    )

    metadata = get_content_metadata(
        "c-001", base_url="http://module1-api:8000/", timeout=2.5
    )

    assert metadata == ContentMetadata(
        content_id="c-001",
        version=1,
        title="Clase 1",
        description="Descripción de la clase",
        tags=("pubtube", "clase"),
        visibility="unlisted",
    )
    assert capturado["url"] == "http://module1-api:8000/api/content/c-001/metadata"
    assert capturado["timeout"] == 2.5
    assert cualquier_header(capturado["headers"], "x-correlation-id")


def cualquier_header(headers: dict, nombre: str) -> bool:
    return any(clave.lower() == nombre for clave in headers)


def test_metadata_null_es_invalid_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.infra.module1.urlopen",
        _fake_urlopen({"status": 200, "contentId": "c-001", "metadata": None}),
    )

    with pytest.raises(DomainError) as exc:
        get_content_metadata("c-001", base_url="http://module1-api:8000")

    assert exc.value.code is ErrorCode.INVALID_METADATA


def test_titulo_vacio_es_invalid_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.infra.module1.urlopen",
        _fake_urlopen(_payload(title="   ")),
    )

    with pytest.raises(DomainError) as exc:
        get_content_metadata("c-001", base_url="http://module1-api:8000")

    assert exc.value.code is ErrorCode.INVALID_METADATA


def test_visibilidad_desconocida_es_invalid_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.infra.module1.urlopen",
        _fake_urlopen(_payload(visibility="friends")),
    )

    with pytest.raises(DomainError) as exc:
        get_content_metadata("c-001", base_url="http://module1-api:8000")

    assert exc.value.code is ErrorCode.INVALID_METADATA


def test_404_de_m1_es_content_not_found(monkeypatch):
    def urlopen_404(request, timeout):
        raise HTTPError(
            request.full_url,
            404,
            "Not Found",
            {},
            io.BytesIO(b'{"statusCode":404,"message":"Video no encontrado"}'),
        )

    monkeypatch.setattr("app.infra.module1.urlopen", urlopen_404)

    with pytest.raises(DomainError) as exc:
        get_content_metadata("c-inexistente", base_url="http://module1-api:8000")

    assert exc.value.code is ErrorCode.CONTENT_NOT_FOUND
    assert "Video no encontrado" in exc.value.message


def test_m1_caido_es_publish_failed(monkeypatch):
    def urlopen_caido(request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr("app.infra.module1.urlopen", urlopen_caido)

    with pytest.raises(DomainError) as exc:
        get_content_metadata("c-001", base_url="http://module1-api:8000")

    assert exc.value.code is ErrorCode.PUBLISH_FAILED


def test_content_from_m1_combina_metadatos_y_archivo_local(monkeypatch, tmp_path):
    metadata = ContentMetadata(
        content_id="c-777",
        version=3,
        title="Título real",
        description="Descripción real",
        tags=("tag1", "tag2"),
        visibility="public",
    )
    monkeypatch.setattr(get_settings(), "YOUTUBE_VIDEO_DIR", str(tmp_path))
    monkeypatch.setattr(youtube, "get_content_metadata", lambda content_id: metadata)
    (tmp_path / "c-777.mp4").write_bytes(b"video")

    contenido = youtube.content_from_m1("c-777")

    assert contenido.title == "Título real"
    assert contenido.description == "Descripción real"
    assert contenido.tags == ("tag1", "tag2")
    assert contenido.privacy_status == "public"
    assert contenido.file_path == tmp_path / "c-777.mp4"


def test_content_from_m1_sin_archivo_es_content_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "YOUTUBE_VIDEO_DIR", str(tmp_path))
    monkeypatch.setattr(
        youtube,
        "get_content_metadata",
        lambda content_id: ContentMetadata(
            content_id=content_id,
            version=1,
            title="Título",
            description="",
            tags=(),
            visibility="private",
        ),
    )

    with pytest.raises(DomainError) as exc:
        youtube.content_from_m1("c-sin-video")

    assert exc.value.code is ErrorCode.CONTENT_NOT_FOUND
    assert "docs/contratos/rest.md" in exc.value.message
