# Acta — Sprint 2
**Fecha:** 17-09-2026 · **Última actualización:** 24-09-2026 (integración con US-C5)
**Facilitador (SM):** [Nahuel]
**Asistentes:** [Hector], [Christian], [Alonso], [Francisco]

## Objetivos del Sprint 2
- Cerrar US-C6 · Máquina de estados de publicación (quedó ~70 % en el Sprint 1)
- Completar los pendientes del esqueleto de US-C2 (recuperación tras reinicio) y US-C5
- Formalizar en contrato la matriz de estados para los Equipos B y D

## Acuerdos

### 1. Un único punto de transición
Todo cambio de estado de una publicación pasa por
`app.domain.states.transition(publication, new_state)`, y toda **persistencia**
de ese cambio pasa por
`app.services.transitions.transition_conditionally(session, publication,
new_state, **values)`. Fuera de esas funciones ninguna parte de la aplicación
puede decidir unilateralmente el estado nuevo mediante una asignación directa.

- La validación del par contra la matriz pertenece al **dominio**
  (`app/domain/states.py`); la escritura y la transacción pertenecen a la capa
  de servicios. No se mezcló SQL con reglas de negocio.
- Se conserva el **UPDATE condicional** en SQL que ya existía desde el Sprint 1.
  Las dos protecciones son obligatorias y coexisten: el dominio rechaza el par
  inválido antes de tocar la base y el SQL evita que una carrera escriba sobre
  un estado que ya cambió.
- El error de transición inválida reutiliza la jerarquía existente
  (`InvalidTransitionError` hereda de `DomainError`) y el código registrado
  `SCHEDULE_CONFLICT`. No se propuso un código nuevo: eso obligaría a los otros
  equipos a actualizar su catálogo por un caso que hoy no cruza la API
  (Guía §5.4).
- **Un solo helper, no uno por servicio.** El job handler
  (`services/publishing.py`) y la reconciliación (`services/recovery.py`)
  repetían cada uno su propio `UPDATE` condicional y ninguno consultaba la
  matriz: la guarda de concurrencia existía, la de dominio no. Se centralizó en
  `services/transitions.py` para que no se pueda tener una sin la otra.
- **Detalle no obvio que quedó documentado en el código:** la validación se
  hace con `transition(..., apply=False)`, es decir **sin** marcar el objeto, y
  el objeto se sincroniza con `session.refresh()` después de escribir. Marcar
  el objeto antes de escribir lo deja "sucio" y el ORM lo vuelca con un
  `UPDATE ... WHERE id` sin condición de estado, pisando el estado bueno que
  dejó la otra ejecución. Hay un test que lo cubre.

### 2. Matriz ampliada
US-C6 y US-C5 se desarrollaron en paralelo y ambos tocaron la matriz. Al
integrarlos quedaron tres transiciones nuevas respecto del Sprint 1:

```text
pending    → failed     (US-C5 / ADR-0004: publicación vencida)
publishing → failed     (US-C5 / ADR-0006: fallo definitivo o intentos agotados)
publishing → pending    (US-C5 / ADR-0006: reintento automático con backoff)
```

No se agregó ningún estado nuevo. La matriz completa, par por par, con los 25
pares, cuáles se aceptan y qué ruta productiva usa cada una, está en
`docs/contratos/estados-publicacion.md` (no se duplica aquí para que no existan
dos versiones de la misma tabla).

### 3. `failed` es terminal en esta versión
Se decidió que `failed` se considera **terminal** en la matriz actual:

```text
pending    → failed     ✅
publishing → failed     ✅
failed     → pending    ❌ por ahora
```

**Precisión incorporada al integrar US-C5**, para que nadie lea esto al revés:
que `failed` sea terminal **no** quiere decir que toda publicación que falla se
quede ahí. El reintento automático ocurre **antes** de llegar a `failed`, como
`publishing → pending`. Una publicación solo llega a `failed` cuando el error
es DEFINITIVO o se agotaron los intentos, y recién entonces se emite
`publish.failed`.

El reintento **manual** de una publicación fallida es **US-C7** (Could,
Sprint 4) y todavía no está implementado. Será US-C7 el que introduzca la
transición de vuelta a `pending` y el que defina su política de intentos.
Agregarla ahora dejaría el reintento manual a medio camino. **US-C6 no
implementa US-C7.**

Queda registrado también que `failed → publishing` está permitida en la matriz
desde el Sprint 1, pero **hoy ningún camino productivo la usa**: `_claim` solo
actúa si la fila está en `pending`, así que un redisparo del job sobre una
publicación fallida se retira sin publicar. Es el hueco que US-C7 vendría a
llenar, y por eso se deja la transición declarada en lugar de borrarla.

### 4. Verificación de las transiciones contra el publicador mock
Decisión que incorpora la tarea 6.3 al alcance de US-C6:

> El criterio de que las transiciones reflejan el resultado real de la API se
> verifica durante el Sprint 2 utilizando el publicador mock. La verificación
> se repetirá contra la API real cuando se implemente US-C3.

Motivo: US-C3 (publicador real de YouTube) es Sprint 3. Mientras el publicador
sea `MockPublisher`, lo verificable es que la transición posterior a llamar al
publicador coincide con el resultado que el publicador devolvió: `published`
con éxito, y `pending` o `failed` según la clase del error y los intentos
disponibles (US-C5). Se repite contra la API real dentro del alcance de US-C3,
no antes, para no declarar verificado algo que no se ejecutó.

### 5. Pruebas exhaustivas
US-C6.2 genera los 25 pares desde la propia matriz (`VALID_TRANSITIONS`) en
lugar de una lista escrita a mano, y para cada par verifica aceptación y
**persistencia**; en los pares inválidos se verifica que la fila queda intacta,
no solo que se lanza la excepción. Si la matriz cambia, los tests cambian de
expectativa solos y siguen siendo exhaustivos.

Se agregaron además dos pruebas que cubren la integración con US-C5, porque la
intuición contraria es fácil de tener: una con error DEFINITIVO que confirma que
`failed` es terminal, y otra con error DIFERIBLE que confirma que el reintento
llega a `pending` **sin** pasar por `failed` y **sin** emitir `publish.failed`.

## Próximos pasos
- [Completar US-C2: cancelación de jobs y pruebas de reinicio real] — Responsable: [Hector] — Plazo: [24-09-2026]
- [Confirmar con Equipo B el campo `source` del envelope en la reunión de integración] — Responsable: [Alonso] — Plazo: [24-09-2026]
- [Preparar el esqueleto de US-C3 (publicador real de YouTube) para el Sprint 3] — Responsable: [Francisco] — Plazo: [01-10-2026]
