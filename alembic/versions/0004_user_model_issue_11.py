"""Issue #11 — app_user con las columnas de la sección 2 del issue (ADR-0009)

    email          -> email_normalized VARCHAR(255) UNIQUE
    username       -> display_name     VARCHAR(80), ya no es único
    password_hash  -> TEXT
    id             -> UUID (y refresh_token.user_id, que lo referencia)
    + role VARCHAR(30) NOT NULL DEFAULT 'user'

Conserva las cuentas y las sesiones existentes: se migran los datos, no se
recrean las tablas vacías. En PostgreSQL son ALTER directos. SQLite (el CI y
las pruebas) no cambia tipos ni restricciones con ALTER, así que ahí se copia
a una tabla nueva y se renombra.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Un UUID guardado como texto con guiones, armado desde los 32 hex sin guiones
# que usa SQLite para el tipo Uuid.
_CON_GUIONES = (
    "substr({c},1,8)||'-'||substr({c},9,4)||'-'||substr({c},13,4)||'-'"
    "||substr({c},17,4)||'-'||substr({c},21)"
)


def _es_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _usuario(nombre: str, nuevo: bool) -> None:
    if nuevo:
        columnas = [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("email_normalized", sa.String(255), nullable=False, unique=True),
            sa.Column("display_name", sa.String(80), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", sa.String(30), nullable=False, server_default="user"),
        ]
    else:
        columnas = [
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(254), nullable=False, unique=True),
            sa.Column("username", sa.String(32), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(255), nullable=False),
        ]
    op.create_table(
        nombre,
        *columnas,
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _refresh(nombre: str, tipo_user_id: sa.types.TypeEngine) -> None:
    op.create_table(
        nombre,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            tipo_user_id,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("persistent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def _copiar_y_reemplazar(usuarios_select: str, refresh_select: str, nuevo: bool) -> None:
    """SQLite: crea las tablas con el esquema destino, copia y renombra."""
    _usuario("app_user_tmp", nuevo)
    op.execute(f"INSERT INTO app_user_tmp {usuarios_select} FROM app_user")
    _refresh("refresh_token_tmp", sa.Uuid() if nuevo else sa.String(36))
    op.execute(f"INSERT INTO refresh_token_tmp {refresh_select} FROM refresh_token")
    op.drop_table("refresh_token")
    op.drop_table("app_user")
    op.rename_table("app_user_tmp", "app_user")
    op.rename_table("refresh_token_tmp", "refresh_token")
    op.create_index("ix_refresh_token_user_id", "refresh_token", ["user_id"])
    op.create_index("ix_refresh_token_family_id", "refresh_token", ["family_id"])


_REFRESH_COLUMNAS = (
    "id, user_id, family_id, token_hash, persistent, expires_at, revoked_at, created_at"
)


def upgrade() -> None:
    if not _es_postgresql():
        _copiar_y_reemplazar(
            "(id, email_normalized, display_name, password_hash, role, is_active,"
            " created_at, updated_at) SELECT REPLACE(id, '-', ''), email, username,"
            " password_hash, 'user', is_active, created_at, updated_at",
            f"({_REFRESH_COLUMNAS}) SELECT id, REPLACE(user_id, '-', ''), family_id,"
            " token_hash, persistent, expires_at, revoked_at, created_at",
            nuevo=True,
        )
        return

    # El tipo de app_user.id cambia, así que la FK que lo referencia sale y
    # vuelve a entrar.
    op.drop_constraint("refresh_token_user_id_fkey", "refresh_token", type_="foreignkey")
    op.alter_column("app_user", "id", type_=sa.Uuid(), postgresql_using="id::uuid")
    op.alter_column(
        "refresh_token", "user_id", type_=sa.Uuid(), postgresql_using="user_id::uuid"
    )
    op.create_foreign_key(
        "refresh_token_user_id_fkey",
        "refresh_token",
        "app_user",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.alter_column("app_user", "email", new_column_name="email_normalized")
    op.alter_column("app_user", "email_normalized", type_=sa.String(255))
    # Mismo nombre que le daría PostgreSQL a la restricción si naciera así.
    op.execute(
        "ALTER TABLE app_user RENAME CONSTRAINT app_user_email_key "
        "TO app_user_email_normalized_key"
    )

    op.drop_constraint("app_user_username_key", "app_user", type_="unique")
    op.alter_column("app_user", "username", new_column_name="display_name")
    op.alter_column("app_user", "display_name", type_=sa.String(80))

    op.alter_column("app_user", "password_hash", type_=sa.Text())
    op.add_column(
        "app_user",
        sa.Column("role", sa.String(30), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    # username era único y de hasta 32 caracteres: si dos cuentas comparten
    # display_name (en minúsculas), la restricción falla y el downgrade se
    # detiene sin cambiar nada.
    if not _es_postgresql():
        _copiar_y_reemplazar(
            "(id, email, username, password_hash, is_active, created_at, updated_at)"
            f" SELECT {_CON_GUIONES.format(c='id')}, email_normalized,"
            " substr(lower(display_name), 1, 32), password_hash, is_active,"
            " created_at, updated_at",
            f"({_REFRESH_COLUMNAS}) SELECT id, {_CON_GUIONES.format(c='user_id')},"
            " family_id, token_hash, persistent, expires_at, revoked_at, created_at",
            nuevo=False,
        )
        return

    op.drop_column("app_user", "role")
    op.alter_column("app_user", "password_hash", type_=sa.String(255))

    op.alter_column(
        "app_user",
        "display_name",
        type_=sa.String(32),
        postgresql_using="left(lower(display_name), 32)",
    )
    op.alter_column("app_user", "display_name", new_column_name="username")
    op.create_unique_constraint("app_user_username_key", "app_user", ["username"])

    op.execute(
        "ALTER TABLE app_user RENAME CONSTRAINT app_user_email_normalized_key "
        "TO app_user_email_key"
    )
    op.alter_column("app_user", "email_normalized", type_=sa.String(254))
    op.alter_column("app_user", "email_normalized", new_column_name="email")

    op.drop_constraint("refresh_token_user_id_fkey", "refresh_token", type_="foreignkey")
    op.alter_column("app_user", "id", type_=sa.String(36), postgresql_using="id::text")
    op.alter_column(
        "refresh_token", "user_id", type_=sa.String(36), postgresql_using="user_id::text"
    )
    op.create_foreign_key(
        "refresh_token_user_id_fkey",
        "refresh_token",
        "app_user",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
