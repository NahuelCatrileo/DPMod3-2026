"""Issue #11 · Un usuario no accede a las publicaciones de otro (ADR-0009)."""

from datetime import datetime, timedelta, timezone

import pytest
from argon2 import PasswordHasher

from app.config import get_settings
from app.infra.models import Publication
from app.services import user_auth
from tests.conftest import make_token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ana():
    return _auth(make_token(sub="user-ana", role="user", kind="user"))


@pytest.fixture
def beto():
    return _auth(make_token(sub="user-beto", role="user", kind="user"))


@pytest.fixture
def servicio():
    return _auth(make_token(sub="module-1", role="service", kind="service"))


def _programar(c, headers, content_id="c-100"):
    return c.post(
        "/api/publish/schedule",
        headers=headers,
        json={
            "contentId": content_id,
            "scheduleAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "timezone": "UTC",
        },
    )


def _estado(c, headers, publish_id):
    return c.get(f"/api/publish/{publish_id}/status", headers=headers)


def test_la_publicacion_guarda_quien_la_programo(anon_client, session, ana):
    publish_id = _programar(anon_client, ana).json()["publishId"]
    assert session.get(Publication, publish_id).owner_id == "user-ana"


def test_el_dueno_ve_su_publicacion(anon_client, ana):
    publish_id = _programar(anon_client, ana).json()["publishId"]
    assert _estado(anon_client, ana, publish_id).status_code == 200


def test_otro_usuario_recibe_404_como_si_no_existiera(anon_client, ana, beto):
    publish_id = _programar(anon_client, ana).json()["publishId"]

    ajena = _estado(anon_client, beto, publish_id)
    inexistente = _estado(anon_client, beto, "no-existe")

    assert ajena.status_code == inexistente.status_code == 404
    assert ajena.json()["code"] == "CONTENT_NOT_FOUND"


def test_los_servicios_conservan_el_acceso_a_todas(anon_client, ana, servicio):
    publish_id = _programar(anon_client, ana).json()["publishId"]
    assert _estado(anon_client, servicio, publish_id).status_code == 200


def test_un_usuario_no_ve_lo_que_programo_un_servicio(anon_client, ana, servicio):
    publish_id = _programar(anon_client, servicio).json()["publishId"]
    assert _estado(anon_client, ana, publish_id).status_code == 404


def test_el_conflicto_no_revela_la_publicacion_de_otro(anon_client, ana, beto):
    publish_id = _programar(anon_client, ana).json()["publishId"]

    ajeno = _programar(anon_client, beto)
    propio = _programar(anon_client, ana)

    assert ajeno.status_code == propio.status_code == 409
    assert publish_id not in ajeno.json()["message"]
    assert publish_id in propio.json()["message"]


def test_kind_user_limita_aunque_role_no_sea_user(anon_client, ana):
    # kind manda sobre role: un token de usuario no se vuelve servicio por
    # traer otro rol.
    publish_id = _programar(anon_client, ana).json()["publishId"]
    otro = _auth(make_token(sub="user-otro", role="service", kind="user"))
    assert _estado(anon_client, otro, publish_id).status_code == 404


def test_un_usuario_admin_ve_todas(anon_client, ana):
    publish_id = _programar(anon_client, ana).json()["publishId"]
    admin = _auth(make_token(sub="user-admin", role="admin", kind="user"))
    assert _estado(anon_client, admin, publish_id).status_code == 200


def test_token_de_usuario_sin_kind_sigue_limitado(anon_client, ana):
    # Tokens emitidos antes del claim kind (role user, sin kind): siguen
    # viendo solo lo suyo mientras no vencen.
    publish_id = _programar(anon_client, ana).json()["publishId"]
    antiguo = _auth(make_token(sub="user-beto", role="user"))
    assert _estado(anon_client, antiguo, publish_id).status_code == 404


@pytest.fixture
def sesiones_rapidas(monkeypatch):
    monkeypatch.setattr(
        user_auth, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    monkeypatch.setattr(get_settings(), "AUTH_COOKIE_SECURE", False)


def test_flujo_completo_con_tokens_emitidos_por_el_login(anon_client, sesiones_rapidas):
    tokens = {}
    for nombre in ("ana", "beto"):
        anon_client.post(
            "/api/auth/register",
            json={"username": nombre, "email": f"{nombre}@x.cl", "password": "clave-segura-1"},
        )
        r = anon_client.post(
            "/api/auth/login", json={"email": f"{nombre}@x.cl", "password": "clave-segura-1"}
        )
        tokens[nombre] = _auth(r.json()["access_token"])

    publish_id = _programar(anon_client, tokens["ana"]).json()["publishId"]

    assert _estado(anon_client, tokens["ana"], publish_id).status_code == 200
    assert _estado(anon_client, tokens["beto"], publish_id).status_code == 404
