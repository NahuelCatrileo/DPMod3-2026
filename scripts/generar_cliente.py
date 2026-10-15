"""Crea las credenciales de un cliente de servicio (ADR-0007).

Genera un client_secret aleatorio y su hash, e imprime:
- el secreto en claro, para entregárselo al cliente por un canal privado;
- la entrada de AUTH_CLIENTS para el .env de este módulo (solo el hash).

    python scripts/generar_cliente.py --client-id gateway --role service

El secreto en claro no se guarda en ningún lado: si se pierde, se genera otro.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.client_credentials import hash_secret  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--role", default="service", help="Claim `role` del token")
    args = parser.parse_args()

    secreto = secrets.token_urlsafe(32)
    entrada = {args.client_id: {"secret_hash": hash_secret(secreto), "role": args.role}}

    print(f"client_id:     {args.client_id}")
    print(f"client_secret: {secreto}")
    print()
    print("Agregue a AUTH_CLIENTS en .env (si ya hay clientes, combine los JSON):")
    print(f"AUTH_CLIENTS='{json.dumps(entrada)}'")


if __name__ == "__main__":
    main()
