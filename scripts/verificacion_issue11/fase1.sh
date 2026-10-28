#!/usr/bin/env bash
# Fase 1 (run.sh): con la versión de dev anterior (migración 0003) se crean cuentas, sesiones y
# una publicación, como las que ya podría tener la VPS.
set -euo pipefail
GW=http://gateway-parche:8000
J=(-H "Content-Type: application/json")
for i in $(seq 60); do curl -fs $GW/api/health >/dev/null && curl -fs http://module3-api:8000/health >/dev/null && break; sleep 2; done

for u in vieja otro; do
  curl -fsS -o /dev/null "${J[@]}" -d "{\"username\":\"$u\",\"email\":\"$u@example.com\",\"password\":\"clave-previa-1\"}" $GW/api/auth/register
  curl -fsS -c /work/$u.jar "${J[@]}" -d "{\"email\":\"$u@example.com\",\"password\":\"clave-previa-1\"}" $GW/api/auth/login | jq -r .access_token > /work/$u.token
done
curl -fsS -H "Authorization: Bearer $(cat /work/vieja.token)" "${J[@]}" \
  -d "{\"contentId\":\"c-vieja-1\",\"scheduleAt\":\"$(date -u -d '+1 hour' +%FT%TZ)\",\"timezone\":\"UTC\"}" \
  $GW/api/publish/schedule | jq -r .publishId > /work/vieja.pid
echo "fase 1: cuentas 'vieja' y 'otro', sesión abierta y publicación $(cat /work/vieja.pid) creadas con la versión 0003"
