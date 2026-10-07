"""ADR-0005 · Las rutas de /api/publish exigen un JWT válido."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import get_settings
from tests.conftest import make_token

STATUS = "/api/publish/no-existe/status"


def _get(anon_client, token: str):
    return anon_client.get(STATUS, headers={"Authorization": f"Bearer {token}"})


def _assert_unauthorized(r):
    assert r.status_code == 401
    assert r.json()["status"] == "error"
    assert r.json()["code"] == "UNAUTHORIZED"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_sin_cabecera_devuelve_unauthorized(anon_client):
    _assert_unauthorized(anon_client.get(STATUS))


def test_programar_sin_token_no_crea_nada(anon_client):
    r = anon_client.post(
        "/api/publish/schedule",
        json={
            "contentId": "c-1",
            "scheduleAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "timezone": "UTC",
        },
    )
    _assert_unauthorized(r)


def test_esquema_distinto_de_bearer(anon_client):
    r = anon_client.get(STATUS, headers={"Authorization": f"Basic {make_token()}"})
    _assert_unauthorized(r)


def test_token_valido_pasa_la_autenticacion(anon_client):
    # Pasa el filtro y llega a la ruta, que responde 404 por el id inexistente.
    r = _get(anon_client, make_token())
    assert r.status_code == 404
    assert r.json()["code"] == "CONTENT_NOT_FOUND"


def test_token_vencido(anon_client):
    vencido = datetime.now(timezone.utc) - timedelta(minutes=5)
    r = _get(anon_client, make_token(exp=vencido))
    _assert_unauthorized(r)
    assert "vencido" in r.json()["message"]


def test_firma_con_otra_clave(anon_client):
    ahora = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "x", "exp": ahora + timedelta(minutes=5)},
        "otra-clave-que-no-es-la-del-modulo-xx",
        algorithm="HS256",
    )
    _assert_unauthorized(_get(anon_client, token))


def test_alg_none_se_rechaza(anon_client):
    ahora = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "x", "exp": ahora + timedelta(minutes=5)}, key=None, algorithm="none"
    )
    _assert_unauthorized(_get(anon_client, token))


@pytest.mark.parametrize("claim", ["exp", "sub"])
def test_claims_obligatorios(anon_client, claim):
    _assert_unauthorized(_get(anon_client, make_token(**{claim: None})))


def test_token_mal_formado(anon_client):
    _assert_unauthorized(_get(anon_client, "esto-no-es-un-jwt"))


def test_issuer_y_audience_si_estan_configurados(anon_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "JWT_ISSUER", "pubtube-gateway")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "module-3")

    _assert_unauthorized(_get(anon_client, make_token()))
    _assert_unauthorized(_get(anon_client, make_token(iss="otro", aud="module-3")))
    ok = _get(anon_client, make_token(iss="pubtube-gateway", aud="module-3"))
    assert ok.status_code == 404


def test_health_sigue_publico(anon_client):
    assert anon_client.get("/api/health").status_code == 200


def test_openapi_declara_el_esquema_bearer(anon_client):
    spec = anon_client.get("/openapi.json").json()
    esquemas = spec["components"]["securitySchemes"]
    assert any(e.get("scheme") == "bearer" for e in esquemas.values())
