"""Issue #11 · Las migraciones funcionan sobre una base existente (ADR-0009).

Migra una base aparte de la de las pruebas: hasta 0003, con una cuenta, una
sesión y una publicación de esa cuenta; después a head, y de vuelta a 0003.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.infra.models import Publication, RefreshToken, User

ALEMBIC = Path(__file__).resolve().parent.parent / "alembic"
USER_ID = str(uuid.uuid4())
AHORA = datetime.now(timezone.utc).isoformat()


def _migrar(engine, accion, destino):
    # Sin archivo .ini: así no se reconfigura el logging de la suite.
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        accion(cfg, destino)


def _base_en_0003(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'existente.db'}")
    _migrar(engine, command.upgrade, "0003")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_user (id, email, username, password_hash, is_active,"
                " created_at, updated_at) VALUES (:id, 'ana@example.com', 'ana',"
                " '$argon2id$v=19$hash', 1, :t, :t)"
            ),
            {"id": USER_ID, "t": AHORA},
        )
        conn.execute(
            text(
                "INSERT INTO refresh_token (id, user_id, family_id, token_hash, persistent,"
                " expires_at, created_at) VALUES ('rt-1', :id, 'fam-1', :h, 1, :t, :t)"
            ),
            {"id": USER_ID, "h": "a" * 64, "t": AHORA},
        )
        conn.execute(
            text(
                "INSERT INTO publication (id, content_id, state, schedule_at, timezone,"
                " attempts, correlation_id, owner_id, created_at, updated_at) VALUES"
                " ('pub-1', 'c-1', 'pending', :t, 'UTC', 0, 'corr-1', :id, :t, :t)"
            ),
            {"id": USER_ID, "t": AHORA},
        )
    return engine


def test_0004_conserva_cuentas_sesiones_y_duenos(tmp_path):
    engine = _base_en_0003(tmp_path)

    _migrar(engine, command.upgrade, "head")

    with Session(engine) as s:
        user = s.get(User, uuid.UUID(USER_ID))
        assert user.email_normalized == "ana@example.com"
        assert user.display_name == "ana"
        assert user.password_hash == "$argon2id$v=19$hash"
        assert user.role == "user"
        assert user.is_active is True
        sesion = s.execute(select(RefreshToken)).scalars().one()
        assert sesion.user_id == user.id
        assert sesion.persistent is True
        assert s.get(Publication, "pub-1").owner_id == USER_ID


def test_0004_se_puede_revertir(tmp_path):
    engine = _base_en_0003(tmp_path)
    _migrar(engine, command.upgrade, "head")

    _migrar(engine, command.downgrade, "0003")

    with engine.connect() as conn:
        user = conn.execute(text("SELECT id, email, username FROM app_user")).one()
        sesion = conn.execute(text("SELECT user_id FROM refresh_token")).one()
    assert tuple(user) == (USER_ID, "ana@example.com", "ana")
    assert sesion.user_id == USER_ID
