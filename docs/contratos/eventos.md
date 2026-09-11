# Contrato de eventos — Módulo 3 (Equipo C)

Versión 1 · Sprint 1 · Última actualización: 2026-09-10

Fuente: Doc 1 §6.1 y §6.3 · Doc 3 §3.

## Envelope

```json
{
  "id": "uuid-v4",
  "type": "publish.scheduled",
  "version": 1,
  "timestamp": "2026-09-17T21:30:00Z",
  "correlationId": "uuid",
  "causationId": "uuid",
  "source": "module-3",
  "payload": {}
}
```

`timestamp` en UTC con sufijo `Z`. Si el evento no tiene causa previa,
`causationId` == `id`.

> **Punto abierto de contrato — pendiente de acta.**
> El Doc 3 §3.1 incluye `source` y el modelo `event_store` del M2 tiene esa
> columna. El Doc 1 §6.3 lo omite. Como el Doc 1 tiene precedencia (Guía §1.2),
> hay contradicción entre documentos. Nosotros lo enviamos.
> **Acción:** confirmar con Equipo B en la reunión de integración del jueves.
> Si validan con `additionalProperties: false` sin `source`, nuestros eventos
> se van a la DLQ.

## Eventos que publicamos

| Routing key | Cuándo | Payload |
|---|---|---|
| `publish.scheduled` | Al aceptar `POST /api/publish/schedule` | `contentId`, `scheduleAt`, `timezone` |
| `publish.completed` | Publicación exitosa | `contentId`, `youtubeVideoId`, `publishedAt` |
| `publish.failed` | Publicación fallida definitiva | `contentId`, `errorCode`, `attempt`, `reason` |

Ejemplos:

```json
{ "contentId": "c-001", "scheduleAt": "2026-09-17T21:30:00Z", "timezone": "America/Santiago" }
{ "contentId": "c-001", "youtubeVideoId": "6226af224c1", "publishedAt": "2026-09-17T21:30:02Z" }
{ "contentId": "c-001", "errorCode": "QUOTA_EXCEEDED", "attempt": 1, "reason": "..." }
```

`errorCode` solo toma valores de la lista registrada (Doc 1 §6.3).

## Eventos que consumimos

| Routing key | Emisor | Estado |
|---|---|---|
| `metadata.updated` | M1 (Equipo A) | **Pendiente.** El payload declarado es `contentId, version, title, tags, visibility`. No está definido qué hacemos al recibirlo. A acordar con Equipo A. |

> El Doc 1 §2.2 dice que M3 "consume `publish.scheduled`". Es un error del
> documento: el catálogo §6.1 y el Doc 3 §2 son claros en que M3 **publica**
> `publish.*` y consume `metadata.*`. No implementamos ese consumidor.

## Estado de la conexión con el broker

`EVENT_TRANSPORT=log` por defecto: doble de prueba (Guía §5.1). Se cambia a
`amqp` cuando Equipo B confirme que `pubtube.events` está operativo y con qué
bindings. El cambio es de configuración, no de código.
