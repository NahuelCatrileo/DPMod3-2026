"""Issue #11 · Registro, login y sesiones de usuarios (ADR-0009)."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

import jwt
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from app.api.routes_users import REFRESH_COOKIE
from app.config import get_settings
from app.infra.models import RefreshToken, User
from app.services import user_auth
from tests.conftest import make_token

CUENTA = {"displayName": "Ana Pérez", "email": "Ana@Example.com", "password": "clave-segura-1"}


@pytest.fixture(autouse=True)
def _argon2_rapido(monkeypatch):
    # Mismo algoritmo (Argon2id) con el mínimo de memoria y pasadas, para que
    # la suite no tarde 50 ms por hash.
    monkeypatch.setattr(
        user_auth, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )


@pytest.fixture(autouse=True)
def _cookie_sin_secure(monkeypatch):
    # El TestClient habla http://testserver y no reenvía cookies Secure.
    monkeypatch.setattr(get_settings(), "AUTH_COOKIE_SECURE", False)


def _registrar(c, **cambios):
    return c.post("/api/auth/register", json={**CUENTA, **cambios})


def _login(c, email="ana@example.com", password="clave-segura-1", **extra):
    return c.post("/api/auth/login", json={"email": email, "password": password, **extra})


def _cookie(r):
    cookie = SimpleCookie()
    cookie.load(r.headers["set-cookie"])
    return cookie[REFRESH_COOKIE]


def _con_cookie(c, ruta, valor):
    # Se vacía el jar del cliente para mandar exactamente esta cookie.
    c.cookies.clear()
    headers = {"Cookie": f"{REFRESH_COOKIE}={valor}"} if valor else {}
    return c.post(ruta, headers=headers)


def _claims(token):
    settings = get_settings()
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def _assert_unauthorized(r):
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def _assert_forbidden(r):
    assert r.status_code == 403
    assert r.json()["code"] == "UNAUTHORIZED"


# --- Registro -----------------------------------------------------------


def test_registro_guarda_las_columnas_del_issue(anon_client, session):
    r = _registrar(anon_client)

    assert r.status_code == 201
    data = r.json()["data"]
    assert data["displayName"] == "Ana Pérez"
    assert data["email"] == "ana@example.com"
    assert data["role"] == "user"
    assert "password" not in str(r.json())

    user = session.get(User, uuid.UUID(data["id"]))
    assert user.email_normalized == "ana@example.com"
    assert user.display_name == "Ana Pérez"
    assert user.role == "user"
    assert user.is_active is True
    assert user.password_hash.startswith("$argon2id$")
    assert "clave-segura-1" not in user.password_hash


@pytest.mark.parametrize("campo", ["displayName", "display_name", "username"])
def test_registro_acepta_los_alias_del_nombre(anon_client, campo):
    cuerpo = {"email": "ana@example.com", "password": "clave-segura-1", campo: "  ana_canal "}
    r = anon_client.post("/api/auth/register", json=cuerpo)
    assert r.status_code == 201
    assert r.json()["data"]["displayName"] == "ana_canal"


def test_dos_cuentas_pueden_compartir_nombre(anon_client):
    assert _registrar(anon_client).status_code == 201
    assert _registrar(anon_client, email="otra@example.com").status_code == 201


def test_registro_ignora_el_rol_que_manda_el_cliente(anon_client):
    r = _registrar(anon_client, role="admin")
    assert r.status_code == 201
    assert r.json()["data"]["role"] == "user"
    token = _login(anon_client).json()["access_token"]
    assert _claims(token)["role"] == "user"


def test_correo_duplicado_devuelve_409(anon_client):
    assert _registrar(anon_client).status_code == 201
    r = _registrar(anon_client, email="  ANA@example.COM")
    assert r.status_code == 409
    assert r.json()["code"] == "DUPLICATE_CONTENT"


@pytest.mark.parametrize(
    "cambio",
    [
        {"password": "corta"},
        {"password": "x" * 129},
        {"email": "sin-arroba"},
        {"displayName": "   "},
        {"displayName": "x" * 81},
    ],
)
def test_registro_invalido_devuelve_422(anon_client, cambio):
    r = _registrar(anon_client, **cambio)
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_METADATA"


def test_registro_sin_nombre_devuelve_422(anon_client):
    r = anon_client.post(
        "/api/auth/register", json={"email": "ana@example.com", "password": "clave-segura-1"}
    )
    assert r.status_code == 422


# --- Login --------------------------------------------------------------


def test_login_entrega_jwt_de_usuario_y_cookie_de_sesion(anon_client):
    user_id = _registrar(anon_client).json()["data"]["id"]
    r = _login(anon_client, email="  ANA@example.com")

    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == get_settings().JWT_ACCESS_TOKEN_MINUTES * 60
    assert r.headers["Cache-Control"] == "no-store"
    claims = _claims(body["access_token"])
    assert claims["sub"] == user_id
    assert claims["role"] == "user"
    assert claims["jti"]

    cookie = _cookie(r)
    assert cookie["httponly"]
    assert cookie["path"] == "/api/auth"
    assert cookie["samesite"].lower() == "strict"
    # Sin "Recordar sesión" la cookie muere al cerrar el navegador.
    assert cookie["max-age"] == ""


def test_el_rol_de_la_cuenta_va_en_el_jwt(anon_client, session):
    user_id = _registrar(anon_client).json()["data"]["id"]
    session.get(User, uuid.UUID(user_id)).role = "admin"
    session.commit()

    claims = _claims(_login(anon_client).json()["access_token"])
    assert claims["role"] == "admin"


def test_recordar_sesion_hace_persistente_la_cookie(anon_client):
    _registrar(anon_client)
    r = _login(anon_client, remember=True)
    assert int(_cookie(r)["max-age"]) == get_settings().AUTH_REFRESH_TOKEN_DAYS * 86400


def test_cookie_secure_por_defecto(anon_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "AUTH_COOKIE_SECURE", True)
    _registrar(anon_client)
    assert _cookie(_login(anon_client))["secure"]


def test_en_la_base_queda_solo_el_hash_del_refresh(anon_client, session):
    _registrar(anon_client)
    valor = _cookie(_login(anon_client)).value

    fila = session.execute(select(RefreshToken)).scalars().one()
    assert fila.token_hash == hashlib.sha256(valor.encode()).hexdigest()
    assert valor not in fila.token_hash


@pytest.mark.parametrize(
    ("email", "password"),
    [("ana@example.com", "otra-clave-123"), ("nadie@example.com", "clave-segura-1")],
)
def test_login_fallido_no_dice_que_fallo(anon_client, email, password):
    _registrar(anon_client)
    r = _login(anon_client, email=email, password=password)
    _assert_unauthorized(r)
    assert r.json()["message"] == "Correo o contraseña incorrectos."
    assert "set-cookie" not in r.headers


def test_cuenta_desactivada_no_inicia_sesion(anon_client, session):
    user_id = _registrar(anon_client).json()["data"]["id"]
    session.get(User, uuid.UUID(user_id)).is_active = False
    session.commit()
    _assert_unauthorized(_login(anon_client))


def test_login_actualiza_el_hash_si_cambian_los_parametros(anon_client, session, monkeypatch):
    user_id = uuid.UUID(_registrar(anon_client).json()["data"]["id"])
    antes = session.get(User, user_id).password_hash

    monkeypatch.setattr(
        user_auth, "_hasher", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    )
    assert _login(anon_client).status_code == 200

    session.expire_all()
    despues = session.get(User, user_id).password_hash
    assert despues != antes
    assert "t=2" in despues


# --- /me ----------------------------------------------------------------


def test_me_devuelve_la_cuenta_del_token(anon_client):
    _registrar(anon_client)
    token = _login(anon_client).json()["access_token"]
    r = anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"]["displayName"] == "Ana Pérez"


def test_me_sin_token(anon_client):
    _assert_unauthorized(anon_client.get("/api/auth/me"))


def test_me_con_token_de_servicio_es_403(anon_client):
    token = make_token(role="service")
    _assert_forbidden(
        anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    )


@pytest.mark.parametrize("sub", ["no-es-uuid", str(uuid.uuid4())])
def test_me_de_cuenta_inexistente(anon_client, sub):
    token = make_token(sub=sub, role="user")
    r = anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    _assert_unauthorized(r)


def test_token_de_usuario_no_conecta_youtube(anon_client):
    token = make_token(role="user")
    r = anon_client.get("/oauth2/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# --- Refresh ------------------------------------------------------------


def test_refresh_rota_la_cookie_y_entrega_jwt_nuevo(anon_client):
    user_id = _registrar(anon_client).json()["data"]["id"]
    viejo = _cookie(_login(anon_client, remember=True)).value

    r = _con_cookie(anon_client, "/api/auth/refresh", viejo)

    assert r.status_code == 200
    assert _claims(r.json()["access_token"])["sub"] == user_id
    nuevo = _cookie(r)
    assert nuevo.value != viejo
    # La rotación conserva "Recordar sesión".
    assert int(nuevo["max-age"]) == get_settings().AUTH_REFRESH_TOKEN_DAYS * 86400


def test_reusar_un_refresh_rotado_cierra_toda_la_sesion(anon_client):
    _registrar(anon_client)
    viejo = _cookie(_login(anon_client)).value
    nuevo = _cookie(_con_cookie(anon_client, "/api/auth/refresh", viejo)).value

    # Alguien presenta el token viejo: se asume robado.
    r = _con_cookie(anon_client, "/api/auth/refresh", viejo)
    _assert_unauthorized(r)
    assert _cookie(r)["max-age"] == "0"

    # El legítimo también queda revocado.
    _assert_unauthorized(_con_cookie(anon_client, "/api/auth/refresh", nuevo))


def test_refresh_sin_cookie_o_desconocida(anon_client):
    _assert_unauthorized(_con_cookie(anon_client, "/api/auth/refresh", None))
    _assert_unauthorized(_con_cookie(anon_client, "/api/auth/refresh", "inventado"))


def test_refresh_vencido(anon_client, session):
    _registrar(anon_client)
    valor = _cookie(_login(anon_client)).value
    fila = session.execute(select(RefreshToken)).scalars().one()
    fila.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()

    _assert_unauthorized(_con_cookie(anon_client, "/api/auth/refresh", valor))


def test_refresh_de_cuenta_desactivada(anon_client, session):
    user_id = _registrar(anon_client).json()["data"]["id"]
    valor = _cookie(_login(anon_client)).value
    session.get(User, uuid.UUID(user_id)).is_active = False
    session.commit()

    _assert_unauthorized(_con_cookie(anon_client, "/api/auth/refresh", valor))


# --- Logout -------------------------------------------------------------


def test_logout_revoca_la_sesion_y_borra_la_cookie(anon_client):
    _registrar(anon_client)
    valor = _cookie(_login(anon_client)).value

    r = _con_cookie(anon_client, "/api/auth/logout", valor)
    assert r.status_code == 204
    assert _cookie(r)["max-age"] == "0"

    _assert_unauthorized(_con_cookie(anon_client, "/api/auth/refresh", valor))


def test_logout_sin_sesion_es_idempotente(anon_client):
    assert _con_cookie(anon_client, "/api/auth/logout", None).status_code == 204
    assert _con_cookie(anon_client, "/api/auth/logout", "inventado").status_code == 204


def test_logout_solo_cierra_su_propia_sesion(anon_client):
    _registrar(anon_client)
    celular = _cookie(_login(anon_client)).value
    notebook = _cookie(_login(anon_client)).value

    _con_cookie(anon_client, "/api/auth/logout", celular)

    assert _con_cookie(anon_client, "/api/auth/refresh", notebook).status_code == 200
