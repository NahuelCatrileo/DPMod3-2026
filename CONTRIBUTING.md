# Guía de Contribución — Equipo C (Módulo 3)

Basada en la Guía Técnica §9 (estándares de código y Git) y en la Guía del
Estudiante §4.3 (nomenclatura). No es una preferencia del equipo: es lo que
la rúbrica evalúa en el criterio de CI/CD y Git (20 % de Calidad de Ingeniería)
y en el criterio individual de Calidad de las tareas (30 % de la nota individual).

---

## 1. Estrategia de ramas

**Trunk-based con ramas de feature cortas.** `main` siempre debe estar en
estado demostrable: es la rama desde la que se clona en el Sprint Review.

```
main                    ← protegida · siempre verde · siempre demostrable
 ├── feat/US-C4-publicador-mock
 ├── feat/US-C1-programar-publicacion
 ├── fix/US-C2-misfire-grace
 └── docs/US-C1-contrato-eventos
```

### Reglas

| Regla | Detalle |
|---|---|
| Nunca se hace commit directo a `main` | Todo entra por Pull Request |
| Una rama por historia o subtarea | No mezclar US-C1 y US-C2 en la misma rama |
| Ramas cortas | Menos de 3 días de vida. Una rama de dos semanas es un conflicto de merge garantizado |
| Se borra tras el merge | GitHub lo ofrece al aceptar el PR |
| Antes de abrir PR, actualizar desde `main` | `git pull --rebase origin main` |

### Nomenclatura

```
<tipo>/<ID-historia>-<descripcion-en-kebab-case>
```

| Tipo | Cuándo |
|---|---|
| `feat/` | Funcionalidad nueva |
| `fix/` | Corrección de un error |
| `test/` | Solo pruebas |
| `docs/` | Solo documentación, ADR o contratos |
| `refactor/` | Reorganización sin cambio de comportamiento |
| `chore/` | Infraestructura, CI, dependencias |

Ejemplos válidos:

```
feat/US-C4-interfaz-publisher
feat/US-C1-endpoint-schedule
test/US-C2-idempotencia-job
docs/US-C1-openapi-contrato
chore/TECH-C3-docker-compose
fix/US-C1-timezone-utc
```

No válidos: `arreglos`, `rama-hector`, `nuevo`, `main2`, `feature/cambios-varios`.

### Configurar la protección de `main`

En GitHub: Settings → Branches → Add rule sobre `main`.

- ☑ Require a pull request before merging
- ☑ Require approvals: **1**
- ☑ Require status checks to pass → seleccionar `test` y `build`
- ☑ Require branches to be up to date before merging

Esto es requisito explícito de US-X1: *"la rama principal está protegida y
requiere pipeline verde"*. Sin configurarlo, la historia no cumple su criterio
de aceptación aunque el CI exista.

---

## 2. Commits: Conventional Commits

Formato obligatorio (Guía Técnica §9):

```
<tipo>(<alcance>): <descripción en imperativo, minúscula, sin punto final>

[cuerpo opcional: por qué, no qué]

[pie opcional: refs US-CN]
```

### Tipos permitidos

| Tipo | Uso |
|---|---|
| `feat` | Funcionalidad nueva visible para el usuario del módulo |
| `fix` | Corrección de un comportamiento incorrecto |
| `test` | Añadir o corregir pruebas |
| `docs` | Documentación, ADR, contratos, README |
| `refactor` | Cambio interno sin alterar comportamiento |
| `chore` | Dependencias, Docker, CI, configuración |

### Alcances sugeridos para este módulo

`publisher` · `scheduler` · `api` · `domain` · `events` · `db` · `oauth` · `ci` · `docs`

### Ejemplos

```
feat(publisher): agrega interfaz Publisher y PublishResult

La interfaz devuelve un resultado en vez de emitir eventos, para que
mock y publicador real compartan un único camino de emisión.

refs US-C4
```

```
feat(api): implementa POST /api/publish/schedule

refs US-C1
```

```
fix(domain): valida zona horaria IANA antes de convertir a UTC

America/Santiago cambia de offset dos veces al año; sin validar, una
zona inexistente reventaba con KeyError en vez de devolver 422.

refs US-C1
```

```
test(scheduler): verifica idempotencia del job handler

refs US-C2
```

```
docs(contratos): documenta el mapeo de codigos de error

refs US-C1
```

### Lo que no se acepta

| Commit | Problema |
|---|---|
| `cambios` | No dice nada |
| `Update main.py` | Describe el archivo, no el cambio |
| `arregle el bug` | Pasado, sin tipo, sin alcance |
| `feat: todo el sprint` | Un commit gigante no es revisable |
| `wip` | No llega a `main`; aplastar antes del PR |

### Tamaño

Un commit = un cambio coherente. Si en la descripción necesitas la palabra
"y", probablemente son dos commits. Es mejor tener 6 commits pequeños y
legibles que uno de 40 archivos: el criterio individual se evalúa sobre el
historial, y un único commit masivo no demuestra qué hizo cada persona.

---

## 3. Pull Requests

### Título

```
[US-C1] Programar publicación: endpoint, validaciones y evento
```

Siempre con el ID de la historia entre corchetes (Guía §4.3).

### Descripción — plantilla

```markdown
## Historia
US-C1 · Programar publicación (5 SP)
Subtareas cubiertas: 1.2, 1.3, 1.4

## Qué hace
POST /api/publish/schedule valida fecha futura y zona horaria IANA,
persiste la publicación en estado pending y emite publish.scheduled.

## Impacto en contratos
- [x] Agrega el endpoint POST /api/publish/schedule (nuevo, no rompedor)
- [x] Emite publish.scheduled con payload { contentId, scheduleAt, timezone }
- [ ] No modifica ningún contrato existente
- Documentación actualizada en docs/contratos/rest.md y eventos.md

## Cómo probarlo
1. docker compose up
2. POST /api/publish/schedule con scheduleAt a +5 minutos → 202
3. Repetir con fecha pasada → 409 SCHEDULE_CONFLICT

## Definition of Done
- [x] Tests escritos y pasando
- [x] CI verde
- [x] Contrato actualizado (OpenAPI + catálogo de eventos)
- [x] Logs con correlationId
- [ ] Revisado por: @
```

**La sección "Impacto en contratos" es obligatoria** en todo PR (Guía Técnica
§9). Un cambio de interfaz sin contrato actualizado incumple la DoD, y romper
un contrato sin versionar ni notificar descuenta 0,5 en Integración.

### Revisión

- Mínimo **una aprobación** de otro integrante antes de mergear.
- El autor no aprueba su propio PR.
- Roten quién revisa: la evidencia de "revisiones realizadas y recibidas" es
  parte del 30 % individual de Calidad de las tareas.
- Revisar de verdad: un "LGTM" en 10 segundos sobre 800 líneas no es evidencia
  de revisión, y se nota al contrastar con los comentarios del PR.

### Qué mirar al revisar

1. ¿Los tests fallan si se rompe el comportamiento? (No basta con que pasen.)
2. ¿El payload del evento tiene exactamente los campos del catálogo?
3. ¿Los códigos de error son de la lista registrada?
4. ¿Hay algún secreto, token o contraseña en el diff?
5. ¿El commit sigue Conventional Commits?

### Merge

Usar **Squash and merge** cuando la rama tenga commits de tipo `wip`, y
**Merge commit** cuando el historial de la rama ya sea limpio y cuente una
historia útil.

---

## 4. Qué nunca se sube

```
.env                 (secretos reales)
credentials/         (client_secret.json, token.json)
venv/  .venv/
__pycache__/  *.pyc
local.db  *.sqlite
.coverage  htmlcov/
```

Todo está en `.gitignore`. Antes de cada PR, verificar:

```bash
git status --short
git diff --cached --name-only
```

Si aparece algo de esa lista, **no hagas commit**. Secretos versionados son
−0,5 en Seguridad y la corrección es obligatoria e inmediata (Guía §6.4).

Auditoría del historial, una vez por sprint:

```bash
git log --all --full-history -- '*client_secret*' '*token.json*' '*.env'
```

Si aparece algo, borrarlo del working tree **no basta**: hay que reescribir el
historial con `git filter-repo` y rotar las credenciales en Google Cloud.

---

## 5. Flujo completo, paso a paso

```bash
# 1. Partir de main actualizada
git checkout main
git pull origin main

# 2. Crear la rama de la subtarea
git checkout -b feat/US-C1-endpoint-schedule

# 3. Trabajar. Commits pequeños y frecuentes.
git add app/api/routes_publish.py app/api/schemas.py
git commit -m "feat(api): implementa POST /api/publish/schedule

refs US-C1"

# 4. Antes de subir: verificar localmente lo mismo que el CI
python -m ruff check app tests
python -m pytest

# 5. Actualizar desde main y subir
git pull --rebase origin main
git push -u origin feat/US-C1-endpoint-schedule

# 6. Abrir el PR en GitHub con la plantilla de arriba

# 7. Tras la aprobación y el CI verde: merge, y borrar la rama
git checkout main
git pull origin main
git branch -d feat/US-C1-endpoint-schedule
```

---

## 6. Declaración de uso de IA

Si el código de un commit fue generado o asistido por IA, se declara en
`USO-IA.md` (Guía §8.1, Anexo D). No se declara commit por commit, sino por
sprint, indicando en qué se usó y **qué verificación humana se aplicó**.

La verificación tiene que haber ocurrido: ejecutar los tests, revisar el PR,
y que alguien del equipo pueda explicar la decisión sin leer. Declarar una
verificación que no ocurrió es falsificación de evidencia de proceso.
