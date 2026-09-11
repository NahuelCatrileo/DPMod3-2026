"""US-C1 · Subtarea 1.5 — Pruebas del endpoint POST /api/publish/schedule."""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.errors import ErrorCode
from app.domain.states import PublishState


def futuro(minutos: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutos)).isoformat()


def pasado(minutos: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()


def test_programar_devuelve_202_y_estado_pending(client):
    r = client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-001",
            "scheduleAt": futuro(),
            "timezone": "America/Santiago",
        },
    )
    assert r.status_code == 202
    cuerpo = r.json()
    assert cuerpo["state"] == PublishState.PENDING.value
    assert cuerpo["publishId"]
    assert cuerpo["correlationId"]


def test_fecha_pasada_devuelve_schedule_conflict(client):
    r = client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-002",
            "scheduleAt": pasado(),
            "timezone": "America/Santiago",
        },
    )
    assert r.status_code == 409
    cuerpo = r.json()
    assert cuerpo["status"] == "error"
    assert cuerpo["code"] == ErrorCode.SCHEDULE_CONFLICT.value


def test_zona_horaria_invalida_devuelve_invalid_metadata(client):
    r = client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-003",
            "scheduleAt": futuro(),
            "timezone": "Marte/Olympus_Mons",
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == ErrorCode.INVALID_METADATA.value


def test_programacion_duplicada_devuelve_conflicto(client):
    payload = {
        "contentId": "c-004",
        "scheduleAt": futuro(),
        "timezone": "America/Santiago",
    }
    assert client.post("/api/publish/schedule", json=payload).status_code == 202
    segunda = client.post("/api/publish/schedule", json=payload)
    assert segunda.status_code == 409
    assert segunda.json()["code"] == ErrorCode.SCHEDULE_CONFLICT.value


def test_emite_publish_scheduled_con_el_envelope_acordado(client, events):
    client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-005",
            "scheduleAt": futuro(),
            "timezone": "America/Santiago",
        },
    )
    emitidos = events.events_of_type("publish.scheduled")
    assert len(emitidos) == 1

    envelope = emitidos[0]
    assert set(envelope) == {
        "id",
        "type",
        "version",
        "timestamp",
        "correlationId",
        "causationId",
        "source",
        "payload",
    }
    assert envelope["version"] == 1
    assert envelope["source"] == "module-3"
    # Payload exacto del catálogo: ni un campo de más.
    assert set(envelope["payload"]) == {"contentId", "scheduleAt", "timezone"}
    assert envelope["payload"]["contentId"] == "c-005"


def test_correlation_id_de_la_cabecera_se_propaga(client, events):
    r = client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-006",
            "scheduleAt": futuro(),
            "timezone": "America/Santiago",
        },
        headers={"X-Correlation-Id": "corr-abc-123"},
    )
    assert r.json()["correlationId"] == "corr-abc-123"
    assert events.events_of_type("publish.scheduled")[0]["correlationId"] == "corr-abc-123"


def test_hora_sin_offset_se_interpreta_en_la_zona_indicada(client):
    """Chile cambia de horario dos veces al año: guardar en UTC es obligatorio."""
    local = datetime.now() + timedelta(days=2)
    r = client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-007",
            "scheduleAt": local.replace(microsecond=0).isoformat(),
            "timezone": "America/Santiago",
        },
    )
    assert r.status_code == 202
    guardado = datetime.fromisoformat(r.json()["scheduleAt"])
    assert guardado.utcoffset() == timedelta(0)


def test_status_de_publicacion_inexistente(client):
    r = client.get("/api/publish/no-existe/status")
    assert r.status_code == 404
    assert r.json()["code"] == ErrorCode.CONTENT_NOT_FOUND.value


def test_status_devuelve_la_publicacion(client):
    creada = client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-008",
            "scheduleAt": futuro(),
            "timezone": "America/Santiago",
        },
    ).json()
    r = client.get(f"/api/publish/{creada['publishId']}/status")
    assert r.status_code == 200
    assert r.json()["state"] == PublishState.PENDING.value
    assert r.json()["attempts"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"scheduleAt": "2030-01-01T00:00:00Z", "timezone": "UTC"},
        {"contentId": "c-009", "timezone": "UTC"},
        {"contentId": "c-009", "scheduleAt": "no-es-fecha", "timezone": "UTC"},
    ],
)
def test_payloads_mal_formados(client, payload):
    r = client.post("/api/publish/schedule", json=payload)
    assert r.status_code == 422
    assert r.json()["status"] == "error"
