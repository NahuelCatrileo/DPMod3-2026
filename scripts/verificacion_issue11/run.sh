#!/usr/bin/env bash
# Verificación de extremo a extremo del issue #11 (docs/pruebas-issue-11.md).
#
# Simula la actualización de la VPS y prueba el flujo a través del gateway:
#   1. PostgreSQL con datos en la migración 0002.
#   2. M3 de dev antes de este cambio (commit 8bd50b2, migración 0003): crea
#      cuentas, abre una sesión y programa una publicación (fase1.sh).
#   3. M3 de este working tree sobre la misma base: al arrancar migra a 0004.
#   4. Chequeos a través del gateway develop + gateway-issue-11.patch, y del
#      gateway develop oficial para medir la dependencia del M4 (fase2.sh).
#
# Requisitos: Docker y git. Uso, desde la raíz del repo:
#   bash scripts/verificacion_issue11/run.sh
# Cuando el M4 integre el parche, GATEWAY_REF=develop GATEWAY_PATCH= prueba su
# develop tal cual.
set -euo pipefail
# Git Bash en Windows reescribe las rutas que empiezan con /. Las de docker
# son del contenedor y no se tocan; las de git sí tienen que convertirse.
docker() { MSYS_NO_PATHCONV=1 command docker "$@"; }

DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
M3_ANTERIOR_REF="${M3_ANTERIOR_REF:-8bd50b2}"
GATEWAY_REPO="${GATEWAY_REPO:-https://github.com/TilinInsano312/PubTube-Mod4.git}"
GATEWAY_REF="${GATEWAY_REF:-7b8b2b3}"
GATEWAY_PATCH="${GATEWAY_PATCH-$DIR/gateway-issue-11.patch}"
GATEWAY_OFICIAL="${GATEWAY_OFICIAL:-ghcr.io/tilininsano312/pubtube-mod4:develop}"

VIEJA=issue11-m3-anterior:local
NUEVA=issue11-m3-nueva:local
GATEWAY=issue11-gateway:local
TOOLS=issue11-tools:local
NET=issue11-net
VOL=issue11-work
DBURL=postgresql+psycopg2://pubtube:pubtube@pg-verif:5432/pubtube_m3

limpiar() {
  docker rm -f pg-verif module3-api gateway-parche gateway-oficial >/dev/null 2>&1 || true
  docker network rm $NET >/dev/null 2>&1 || true
  docker volume rm $VOL >/dev/null 2>&1 || true
}
limpiar
trap limpiar EXIT

echo "--- 0. Imágenes"
(cd "$RAIZ" && docker build -q -t $NUEVA . >/dev/null)
git -C "$RAIZ" archive "$M3_ANTERIOR_REF" | docker build -q -t $VIEJA - >/dev/null
tmp="$(mktemp -d)"
git clone -q "$GATEWAY_REPO" "$tmp/gw"
git -C "$tmp/gw" checkout -q "$GATEWAY_REF"
if [ -n "$GATEWAY_PATCH" ]; then
  tr -d '\r' < "$GATEWAY_PATCH" | git -C "$tmp/gw" apply
fi
(cd "$tmp/gw/gateway" && docker build -q -t $GATEWAY . >/dev/null)
rm -rf "$tmp"
docker pull -q "$GATEWAY_OFICIAL" >/dev/null
(cd "$DIR" && docker build -q -t $TOOLS -f - . >/dev/null <<'EOF'
FROM debian:bookworm-slim
RUN apt-get -qq update && apt-get -qq install -y curl jq postgresql-client >/dev/null
WORKDIR /verif
COPY fase1.sh fase2.sh ./
EOF
)
echo "M3 anterior: $M3_ANTERIOR_REF | M3 nuevo: working tree | gateway: $GATEWAY_REF${GATEWAY_PATCH:+ + parche}"

# Clave y cliente efímeros: no son credenciales reales.
KEY="$(docker run --rm $NUEVA python -c 'import secrets; print(secrets.token_hex(32))')"
cred="$(docker run --rm $NUEVA python scripts/generar_cliente.py --client-id module-ci)"
CLIENT_SECRET="$(echo "$cred" | awk '/^client_secret:/ {print $2}')"
AUTH_CLIENTS="$(echo "$cred" | sed -n "s/^AUTH_CLIENTS='\(.*\)'$/\1/p")"
tokens="$(docker run --rm -e K="$KEY" $NUEVA python -c "
import jwt, os, time
k, n = os.environ['K'], int(time.time())
print(jwt.encode({'sub': 'x', 'role': 'user', 'kind': 'user', 'iat': n - 600, 'exp': n - 300}, k, 'HS256'))
print(jwt.encode({'role': 'user', 'kind': 'user', 'iat': n, 'exp': n + 600}, k, 'HS256'))")"
VENCIDO="$(echo "$tokens" | sed -n 1p)"
SINSUB="$(echo "$tokens" | sed -n 2p)"

modulo() {  # imagen
  docker rm -f module3-api >/dev/null 2>&1 || true
  # AUTH_COOKIE_SECURE=false: la prueba va por HTTP, igual que la VPS hoy.
  docker run -d --name module3-api --network $NET \
    -e APP_ENV=prod -e DATABASE_URL=$DBURL -e JWT_SECRET_KEY="$KEY" \
    -e AUTH_CLIENTS="$AUTH_CLIENTS" -e AUTH_COOKIE_SECURE=false \
    -e AUTH_ALLOWED_ORIGINS=https://pubtube.example \
    -e PUBLISHER_MODE=mock -e EVENT_TRANSPORT=log -e MOCK_LATENCY_SECONDS=0 \
    -e SCHEDULER_ENABLED=true "$1" >/dev/null
}
herramientas() {  # script [opciones de docker run]
  local script="$1"; shift
  docker run --rm --network $NET -v $VOL:/work "$@" $TOOLS bash "$script"
}

echo "--- 1. PostgreSQL con datos en 0002"
docker network create $NET >/dev/null
docker volume create $VOL >/dev/null
docker run -d --name pg-verif --network $NET -e POSTGRES_USER=pubtube \
  -e POSTGRES_PASSWORD=pubtube -e POSTGRES_DB=pubtube_m3 postgres:16-alpine >/dev/null
for i in $(seq 30); do docker exec pg-verif pg_isready -U pubtube >/dev/null 2>&1 && break; sleep 1; done
sleep 2
docker run --rm --network $NET -e DATABASE_URL=$DBURL $VIEJA alembic upgrade 0002 2>&1 | grep Running
docker exec pg-verif psql -q -U pubtube -d pubtube_m3 -c "
INSERT INTO publication (id, content_id, state, schedule_at, timezone, youtube_video_id,
  attempts, last_error, correlation_id, created_at, updated_at) VALUES
 ('pre-0001','c-pre-1','published', now() - interval '2 days','UTC','yt-previo',1,NULL,'corr-pre-1',now(),now()),
 ('pre-0002','c-pre-2','pending',   now() + interval '2 days','UTC',NULL,0,NULL,'corr-pre-2',now(),now());
INSERT INTO oauth_credential (id, encrypted_tokens, scopes, expires_at, connected_at, updated_at)
VALUES ('youtube','cifrado-de-prueba','https://www.googleapis.com/auth/youtube.upload',NULL,now(),now());"

echo "--- 2. M3 anterior (0003) y gateways (MODULE3_URL=http://module3-api:8000)"
modulo $VIEJA
for gw in gateway-oficial:$GATEWAY_OFICIAL gateway-parche:$GATEWAY; do
  docker run -d --name "${gw%%:*}" --network $NET -e GATEWAY_PORT=8000 \
    -e JWT_SECRET="$KEY" -e JWT_ALGORITHM=HS256 -e MODULE3_URL=http://module3-api:8000 \
    -e RATE_LIMIT_REQUESTS=1000 "${gw#*:}" >/dev/null
done
herramientas fase1.sh

echo "--- 3. M3 nuevo sobre la misma base (migra 0003 → 0004 al arrancar)"
modulo $NUEVA
docker restart gateway-oficial gateway-parche >/dev/null
herramientas fase2.sh -e CLIENT_SECRET="$CLIENT_SECRET" -e VENCIDO="$VENCIDO" -e SINSUB="$SINSUB"

echo "--- Arranque del M3 nuevo"
docker logs module3-api 2>&1 | grep -E "Running upgrade|startup complete|ERROR|Traceback" | head
