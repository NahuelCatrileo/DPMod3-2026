"""Genera un JWT de prueba para llamar a la API en desarrollo.

En integración el token lo emite el API Gateway (Equipo D). Este script solo
sirve mientras tanto, para probar desde Swagger (/docs → Authorize) o curl:

    python scripts/generar_jwt.py --sub equipo-c --minutos 60
    curl -H "Authorization: Bearer <token>" http://localhost:8000/api/publish/<id>/status

Usa JWT_SECRET_KEY, JWT_ALGORITHM, JWT_ISSUER y JWT_AUDIENCE del .env.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sub", default="dev-local", help="Sujeto del token")
    parser.add_argument("--minutos", type=int, default=60, help="Vigencia")
    args = parser.parse_args()

    settings = get_settings()
    ahora = datetime.now(timezone.utc)
    claims = {
        "sub": args.sub,
        "iat": ahora,
        "exp": ahora + timedelta(minutes=args.minutos),
    }
    if settings.JWT_ISSUER:
        claims["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        claims["aud"] = settings.JWT_AUDIENCE

    print(jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


if __name__ == "__main__":
    main()
