"""US-C3.3 · Consentimiento del dueño del canal (ADR-0008).

Flujo *authorization code* de OAuth 2.0 con PKCE (RFC 7636):

1. Un administrador (JWT con `role: admin`) pide POST /oauth2/authorize y
   recibe la URL de consentimiento de Google.
2. La abre en el navegador, entra con la cuenta dueña del canal y acepta.
3. Google redirige ese navegador a GET /oauth2/callback con `code` y `state`.
4. El módulo canjea el código con el `code_verifier` y guarda los tokens
   cifrados (token_store.py).

`state` y `code_verifier` quedan en memoria del servidor y no en una cookie:
el pedido del paso 1 puede salir de curl o de Swagger, que no son el
navegador que vuelve en el paso 3. El `state` hace de llave: es aleatorio,
vale STATE_TTL_SECONDS y sirve una sola vez, así el callback solo acepta la
respuesta a un consentimiento que pidió un administrador (protección CSRF).
PKCE asegura que un código interceptado no se puede canjear: el
`code_verifier` nunca sale del servidor.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.errors import OAuthError
from app.oauth import credentials, google
from app.oauth.token_store import GoogleTokens

logger = logging.getLogger(__name__)

# Tiempo para abrir la URL, iniciar sesión en Google y aceptar.
STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class PendingConsent:
    code_verifier: str
    # `sub` del JWT que pidió la URL, para el log.
    requested_by: str
    # En segundos de `clock` (monotónico por defecto).
    expires_at: float


class PendingConsents:
    """Consentimientos pedidos y todavía sin respuesta de Google, por `state`.

    Viven en memoria: si el módulo se reinicia entre los pasos 1 y 3, hay que
    pedir la URL de nuevo. Supone una sola instancia, igual que ADR-0003.
    """

    def __init__(
        self,
        ttl_seconds: int = STATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, PendingConsent] = {}
        self._lock = threading.Lock()

    def add(self, state: str, code_verifier: str, requested_by: str) -> None:
        with self._lock:
            self._purge()
            self._items[state] = PendingConsent(
                code_verifier, requested_by, self._clock() + self._ttl
            )

    def pop(self, state: str) -> PendingConsent | None:
        """Lo entrega una sola vez, y solo si no venció."""
        with self._lock:
            self._purge()
            return self._items.pop(state, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._items)

    def _purge(self) -> None:
        ahora = self._clock()
        for state in [s for s, p in self._items.items() if p.expires_at <= ahora]:
            del self._items[state]


pending_consents = PendingConsents()


def start_consent(requested_by: str) -> str:
    """Paso 1: URL de consentimiento de Google para que la abra un humano."""
    url, state, code_verifier = google.authorization_request()
    pending_consents.add(state, code_verifier, requested_by)
    # Ni la URL ni el state van al log: con ellos se completa el consentimiento.
    logger.info("oauth_consentimiento_pedido sub=%s", requested_by)
    return url


def complete_consent(
    *, state: str | None, code: str | None, error: str | None
) -> GoogleTokens:
    """Paso 4: valida la respuesta de Google, canjea el código y guarda."""
    pendiente = pending_consents.pop(state) if state else None
    if pendiente is None:
        raise OAuthError(
            "La respuesta de Google no corresponde a un consentimiento pendiente: "
            "el state venció, ya se usó o no lo emitió este módulo. Pida una URL "
            "nueva con POST /oauth2/authorize."
        )
    if error:
        logger.warning(
            "oauth_consentimiento_rechazado sub=%s error=%s", pendiente.requested_by, error
        )
        raise OAuthError(
            f"Google no otorgó el permiso ({error}). Pida una URL nueva con "
            "POST /oauth2/authorize y acepte el acceso a YouTube."
        )
    if not code:
        raise OAuthError("La respuesta de Google no trae el código de autorización.")

    tokens = google.exchange_code(code, pendiente.code_verifier)
    if google.SCOPE_YOUTUBE_UPLOAD not in tokens.scopes:
        raise OAuthError(
            "No se otorgó el permiso para subir videos (youtube.upload). Pida una "
            "URL nueva con POST /oauth2/authorize y acepte ese permiso."
        )

    guardados = credentials.store_consent(tokens)
    logger.info(
        "oauth_cuenta_conectada sub=%s scopes=%s",
        pendiente.requested_by,
        " ".join(guardados.scopes),
    )
    return guardados
