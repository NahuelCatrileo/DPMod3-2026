#!/usr/bin/env bash
# Fase 2: versión nueva (migración 0004) sobre la misma base. Verifica el
# issue #11 a través del gateway. PASS/FAIL cuentan; PEND documenta lo que
# depende del Módulo 4 y no se cuenta como resultado de M3.
set -u
GW=http://gateway-parche:8000      # gateway develop + parche (PR separada al M4)
GO=http://gateway-oficial:8000     # gateway develop sin cambios
M3=http://module3-api:8000         # red interna: OpenAPI y prueba de capa
UI=https://pubtube.example         # AUTH_ALLOWED_ORIGINS del módulo
export PGPASSWORD=pubtube
PSQL=(psql -h pg-verif -U pubtube -d pubtube_m3 -tAc)
J=(-H "Content-Type: application/json")
CSRF=(-H "X-CSRF-Protection: 1")
T=/tmp/v; mkdir -p $T
pass=0; fail=0
eq() {
  if [ "$3" = "$4" ]; then echo "PASS | $1 | $2: $4"; pass=$((pass+1))
  else echo "FAIL | $1 | $2: esperado '$3', obtenido '$4'"; fail=$((fail+1)); fi
}
pend() { echo "PEND | $1 | $2: $3"; }
req() { curl -s -o $T/body -D $T/hdr -w '%{http_code}' "$@"; }
body() { jq -r "$1" $T/body; }
jwtpayload() {
  local p; p=$(echo "$1" | cut -d. -f2 | tr '_-' '/+')
  while [ $(( ${#p} % 4 )) -ne 0 ]; do p="$p="; done
  echo "$p" | base64 -d
}
cookie_val() { grep pubtube_refresh "$1" | cut -f7; }
en_una_hora() { date -u -d '+1 hour' +%FT%TZ; }
programar() {  # token contentId
  req -H "Authorization: Bearer $1" "${J[@]}" \
    -d "{\"contentId\":\"$2\",\"scheduleAt\":\"$(en_una_hora)\",\"timezone\":\"UTC\"}" $GW/api/publish/schedule
}
estado() { req -H "Authorization: Bearer $1" $GW/api/publish/$2/status; }
login() {  # email password jar
  req -c "$3" "${J[@]}" -d "{\"email\":\"$1\",\"password\":\"$2\"}" $GW/api/auth/login
}

for i in $(seq 60); do
  curl -fs $GW/api/health >/dev/null && curl -fs $GO/api/health >/dev/null && curl -fs $M3/health >/dev/null && break
  sleep 2
done

echo "== C14 · Migración 0003 → 0004 sobre la base existente"
eq C14 "versión de alembic" 0004 "$("${PSQL[@]}" 'select version_num from alembic_version')"
eq C14 "columnas de app_user (sección 2)" \
  "id:uuid:NO,email_normalized:character varying(255):NO,display_name:character varying(80):NO,password_hash:text:NO,is_active:boolean:NO,created_at:timestamp with time zone:NO,updated_at:timestamp with time zone:NO,role:character varying(30):NO" \
  "$("${PSQL[@]}" "select string_agg(column_name||':'||data_type||coalesce('('||character_maximum_length||')','')||':'||is_nullable, ',' order by ordinal_position) from information_schema.columns where table_name='app_user'")"
eq C14 "role por defecto 'user'" "'user'::character varying" "$("${PSQL[@]}" "select column_default from information_schema.columns where table_name='app_user' and column_name='role'")"
eq C14 "is_active por defecto true" true "$("${PSQL[@]}" "select column_default from information_schema.columns where table_name='app_user' and column_name='is_active'")"
eq C14 "única restricción UNIQUE: email_normalized" app_user_email_normalized_key "$("${PSQL[@]}" "select string_agg(conname, ',') from pg_constraint where conrelid='app_user'::regclass and contype='u'")"
eq C14 "refresh_token.user_id es UUID con FK" "uuid refresh_token_user_id_fkey" "$("${PSQL[@]}" "select data_type from information_schema.columns where table_name='refresh_token' and column_name='user_id'") $("${PSQL[@]}" "select conname from pg_constraint where conrelid='refresh_token'::regclass and contype='f'")"
eq C14 "publicaciones previas a 0003 intactas y sin dueño" 2 "$("${PSQL[@]}" "select count(*) from publication where id like 'pre-%' and owner_id is null")"
eq C14 "credencial OAuth intacta" 1 "$("${PSQL[@]}" "select count(*) from oauth_credential where id='youtube'")"
eq C14 "cuentas de la versión anterior conservadas" "vieja@example.com|vieja|user" "$("${PSQL[@]}" "select email_normalized||'|'||display_name||'|'||role from app_user where email_normalized='vieja@example.com'")"
VIEJA_ID=$(jwtpayload "$(cat /work/vieja.token)" | jq -r .sub)
PID_VIEJA=$(cat /work/vieja.pid)
eq C14 "la cuenta previa entra con su contraseña" 200 "$(login vieja@example.com clave-previa-1 $T/vieja2.jar)"
eq C14 "y conserva su id" "$VIEJA_ID" "$(jwtpayload "$(body .access_token)" | jq -r .sub)"
eq C14 "la sesión abierta antes de migrar sigue sirviendo" 200 "$(req -b /work/vieja.jar "${CSRF[@]}" -X POST $GW/api/auth/refresh)"
eq C14 "el token previo (sin kind) ve su publicación" 200 "$(estado "$(cat /work/vieja.token)" $PID_VIEJA)"
eq C14 "el token previo de otro usuario no la ve" 404 "$(estado "$(cat /work/otro.token)" $PID_VIEJA)"

echo "== C18 · OpenAPI del módulo"
curl -s $M3/openapi.json > $T/openapi.json
for p in /api/auth/register /api/auth/login /api/auth/me /api/auth/refresh /api/auth/logout /api/auth/token; do
  eq C18 "OpenAPI documenta $p" true "$(jq --arg p $p '.paths | has($p)' $T/openapi.json)"
done
eq C18 "OpenAPI: registro con displayName" true "$(jq '.components.schemas.RegisterRequest.properties | has("displayName")' $T/openapi.json)"

echo "== S10 · Flujo obligatorio a través del gateway"
eq S10 "POST /api/auth/register" 201 "$(req "${J[@]}" -d '{"displayName":"Ana Pérez","email":"Ana@Example.com","password":"clave-segura-1"}' $GW/api/auth/register)"
ANA_ID=$(body .data.id)
eq S10 "register guarda display_name y email normalizado" "Ana Pérez ana@example.com user" "$(body '.data.displayName + " " + .data.email + " " + .data.role')"
eq S10 "register acepta username como alias" "201 beto" "$(req "${J[@]}" -d '{"username":"beto","email":"beto@example.com","password":"clave-segura-2"}' $GW/api/auth/register) $(body .data.displayName)"
eq S10 "POST /api/auth/login" 200 "$(login ana@example.com clave-segura-1 $T/ana.jar)"
ANA=$(body .access_token)
sc=$(grep -i '^set-cookie: pubtube_refresh=' $T/hdr | tr -d '\r')
eq S10 "login deja la refresh cookie (HttpOnly, Path=/api/auth)" si "$(echo "$sc" | grep -qi httponly && echo "$sc" | grep -qi 'path=/api/auth' && echo si || echo "${sc:-sin cookie}")"
eq S10 "JWT: sub, role y kind" "$ANA_ID user user" "$(jwtpayload "$ANA" | jq -r '.sub + " " + .role + " " + .kind')"
login beto@example.com clave-segura-2 $T/beto.jar >/dev/null
BETO=$(body .access_token)
eq S10 "GET /api/auth/me" 200 "$(req -H "Authorization: Bearer $ANA" $GW/api/auth/me)"
eq S10 "/me identifica a ana" "$ANA_ID Ana Pérez" "$(body '.data.id + " " + .data.displayName')"
eq S10 "POST /api/publish/schedule" 202 "$(programar "$ANA" c-ana-1)"
PID=$(body .publishId)
eq S10 "propietario asociado en PostgreSQL" "$ANA_ID" "$("${PSQL[@]}" "select owner_id from publication where id='$PID'")"
eq S10 "GET status: 200 para el propietario" 200 "$(estado "$ANA" $PID)"
eq S10 "GET status: rechazo para otro usuario" 404 "$(estado "$BETO" $PID)"
cp $T/ana.jar $T/ana.0
eq S10 "POST /api/auth/refresh" 200 "$(req -b $T/ana.jar -c $T/ana.jar "${CSRF[@]}" -X POST $GW/api/auth/refresh)"
eq S10 "refresh entrega un JWT nuevo" true "$(jq --arg a "$ANA" '.access_token != $a and (.access_token | length) > 0' $T/body)"
eq S10 "refresh rota la cookie" distinta "$([ "$(cookie_val $T/ana.0)" != "$(cookie_val $T/ana.jar)" ] && echo distinta || echo igual)"
cp $T/ana.jar $T/ana.1
eq S10 "POST /api/auth/logout" 204 "$(req -b $T/ana.jar -c $T/ana.jar "${CSRF[@]}" -X POST $GW/api/auth/logout)"
eq S10 "POST /api/auth/refresh tras logout" 401 "$(req -b $T/ana.1 "${CSRF[@]}" -X POST $GW/api/auth/refresh)"

echo "== Casos de error"
eq C04 "correo duplicado (espacios y mayúsculas)" "409 DUPLICATE_CONTENT" "$(req "${J[@]}" -d '{"displayName":"Otra Ana","email":" ANA@example.com","password":"clave-segura-1"}' $GW/api/auth/register) $(body .code)"
eq ERR "display_name repetido sí se permite" 201 "$(req "${J[@]}" -d '{"displayName":"Ana Pérez","email":"ana.dos@example.com","password":"clave-segura-1"}' $GW/api/auth/register)"
eq ERR "registro inválido (contraseña corta)" "422 INVALID_METADATA" "$(req "${J[@]}" -d '{"displayName":"Carla","email":"carla@example.com","password":"corta"}' $GW/api/auth/register) $(body .code)"
eq ERR "registro con display_name de 81 caracteres" 422 "$(req "${J[@]}" -d "{\"displayName\":\"$(printf 'x%.0s' $(seq 81))\",\"email\":\"largo@example.com\",\"password\":\"clave-segura-1\"}" $GW/api/auth/register)"
eq ERR "el registro no deja elegir rol" "201 user" "$(req "${J[@]}" -d '{"displayName":"Dani","email":"dani@example.com","password":"clave-segura-4","role":"admin"}' $GW/api/auth/register) $(body .data.role)"
eq ERR "contraseña incorrecta" "401 UNAUTHORIZED" "$(login ana@example.com otra-clave-99 $T/x.jar) $(body .code)"
eq ERR "login fallido no deja cookie" no "$(grep -qi '^set-cookie' $T/hdr && echo si || echo no)"
eq ERR "correo inexistente" "401 UNAUTHORIZED" "$(login nadie@example.com clave-segura-1 $T/x.jar) $(body .code)"
eq C07 "/me sin token" 401 "$(req $GW/api/auth/me)"
p=$(jwtpayload "$ANA" | jq -c '.role = "admin"' | base64 -w0 | tr '+/' '-_' | tr -d '=')
ALTERADO="$(echo "$ANA" | cut -d. -f1).$p.$(echo "$ANA" | cut -d. -f3)"
eq C07 "JWT alterado (role→admin) en el gateway" 401 "$(estado "$ALTERADO" $PID)"
eq C07 "JWT alterado directo al módulo" 401 "$(req -H "Authorization: Bearer $ALTERADO" $M3/api/auth/me)"
eq C07 "JWT vencido" 401 "$(req -H "Authorization: Bearer $VENCIDO" $GW/api/auth/me)"
req -H "Authorization: Bearer $SINSUB" $GW/api/publish/$PID/status >/dev/null
eq GW "JWT sin sub: lo rechaza el gateway" gateway "$(jq -r 'if .detail then "gateway" else "módulo (" + (.code // "?") + ")" end' $T/body)"
login ana@example.com clave-segura-1 $T/r.jar >/dev/null
cp $T/r.jar $T/r.0
req -b $T/r.jar -c $T/r.jar "${CSRF[@]}" -X POST $GW/api/auth/refresh >/dev/null
eq C10 "reusar un refresh ya rotado" 401 "$(req -b $T/r.0 "${CSRF[@]}" -X POST $GW/api/auth/refresh)"
eq C10 "el reuso revoca también el refresh vigente" 401 "$(req -b $T/r.jar "${CSRF[@]}" -X POST $GW/api/auth/refresh)"

echo "== CSRF (fase 2 del issue)"
login ana@example.com clave-segura-1 $T/c.jar >/dev/null
eq CSRF "refresh sin X-CSRF-Protection" "403 UNAUTHORIZED" "$(req -b $T/c.jar -X POST $GW/api/auth/refresh) $(body .code)"
eq CSRF "logout sin X-CSRF-Protection" 403 "$(req -b $T/c.jar -X POST $GW/api/auth/logout)"
eq CSRF "Origin no permitido" 403 "$(req -b $T/c.jar "${CSRF[@]}" -H "Origin: https://atacante.example" -X POST $GW/api/auth/refresh)"
eq CSRF "la sesión sobrevive a los intentos rechazados (Origin de la UI)" 200 "$(req -b $T/c.jar -c $T/c.jar "${CSRF[@]}" -H "Origin: $UI" -X POST $GW/api/auth/refresh)"
eq CSRF "login solo acepta JSON (text/plain)" 422 "$(req -H 'Content-Type: text/plain' -d '{"email":"ana@example.com","password":"clave-segura-1"}' $GW/api/auth/login)"

echo "== C09 / C13 · Servicios (client credentials) y rol de la cuenta"
eq C13 "/api/auth/token por el gateway oficial" 200 "$(req -u "module-ci:$CLIENT_SECRET" -d grant_type=client_credentials $GO/api/auth/token)"
eq C13 "/api/auth/token por el gateway parcheado" 200 "$(req -u "module-ci:$CLIENT_SECRET" -d grant_type=client_credentials $GW/api/auth/token)"
SVC=$(body .access_token)
eq C09 "token de servicio: role y kind" "service service" "$(jwtpayload "$SVC" | jq -r '.role + " " + .kind')"
eq C13 "secreto de cliente incorrecto" 401 "$(req -u module-ci:mal -d grant_type=client_credentials $GW/api/auth/token)"
eq C13 "el servicio programa" 202 "$(programar "$SVC" c-svc-1)"
eq C13 "el servicio ve la publicación de ana" 200 "$(estado "$SVC" $PID)"
eq C13 "el servicio ve una publicación previa a 0003" 200 "$(estado "$SVC" pre-0001)"
eq C12 "un usuario no ve publicaciones sin dueño" 404 "$(estado "$ANA" pre-0001)"
eq C09 "token de servicio en /me" "403 UNAUTHORIZED" "$(req -H "Authorization: Bearer $SVC" $GW/api/auth/me) $(body .code)"
"${PSQL[@]}" "update app_user set role='admin' where id='$ANA_ID'" >/dev/null
login ana@example.com clave-segura-1 $T/admin.jar >/dev/null
ADMIN=$(body .access_token)
eq ROL "role de la columna va al JWT" "admin user" "$(jwtpayload "$ADMIN" | jq -r '.role + " " + .kind')"
eq ROL "un usuario admin ve publicaciones ajenas" 200 "$(estado "$ADMIN" $PID_VIEJA)"

echo "== C02 / C03 · PostgreSQL y Argon2id"
eq C02 "cuentas en app_user" 6 "$("${PSQL[@]}" 'select count(*) from app_user')"
eq C03 "hashes que no son \$argon2id\$" 0 "$("${PSQL[@]}" "select count(*) from app_user where password_hash not like '\$argon2id\$%'")"
eq C03 "contraseñas en claro" 0 "$("${PSQL[@]}" "select count(*) from app_user where password_hash like '%clave-%'")"
v=$(cookie_val $T/ana.1)
eq SEG "el valor de la cookie no está en la base" 0 "$("${PSQL[@]}" "select count(*) from refresh_token where token_hash='$v'")"
eq SEG "sí está su SHA-256" 1 "$("${PSQL[@]}" "select count(*) from refresh_token where token_hash='$(printf %s "$v" | sha256sum | cut -c1-64)'")"

echo "== Gateway develop sin el parche (dependencia del Módulo 4)"
pend C15 "register por el gateway oficial" "$(req "${J[@]}" -d '{"displayName":"Eva","email":"eva@example.com","password":"clave-segura-5"}' $GO/api/auth/register)"
pend C15 "login por el gateway oficial" "$(req "${J[@]}" -d '{"email":"ana@example.com","password":"clave-segura-1"}' $GO/api/auth/login)"
pend C15 "JWT de usuario por el gateway oficial (publish/status del dueño)" "$(req -H "Authorization: Bearer $ADMIN" $GO/api/publish/$PID/status)"

echo "RESULTADO M3 + gateway parcheado: $pass PASS, $fail FAIL"
