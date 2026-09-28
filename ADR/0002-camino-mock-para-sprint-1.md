# ADR-0002 — Publicador simulado como camino principal del Sprint 1

## Estado

Aceptada · 2026-09-10 · Sprint 1

## Contexto

El Módulo 3 debe llevar contenido a YouTube (US-C3), pero esa historia está
planificada para el Sprint 3. En el Sprint 1 necesitamos que el scheduler
(US-C2) tenga algo que ejecutar y que el flujo de eventos `publish.*` viaje
completo, sin que la demo dependa de credenciales de Google, de la cuota
diaria de la API ni de la conectividad durante el Sprint Review.

Fuerzas en juego:

- La cuota de la YouTube Data API v3 es limitada y compartida; una demo que
  suba videos de prueba la consume.
- La Guía del Estudiante §7.4 declara que "se cayó el servicio de YouTube" no
  constituye justificación, precisamente porque el modo simulado es alcance
  declarado (Doc 1 §1.2).
- El equipo ya configuró OAuth en el Sprint 0, así que descartar el camino
  real por completo desperdiciaría ese trabajo.

## Alternativas consideradas

1. **OAuth real desde el Sprint 1.** Máximo realismo, pero mete US-C3 (8 SP,
   Sprint 3) dentro de un sprint que ya tiene 8 SP comprometidos, y ata la
   demo a un servicio externo con cuota.
2. **Publicador simulado conmutable por configuración.** Desarrollamos contra
   una implementación local y determinista; el camino real entra en Sprint 3
   sin tocar el scheduler ni la emisión de eventos.
3. **Sandbox de YouTube.** No existe un sandbox oficial de `videos.insert`
   que evite el consumo de cuota; descartada por inviabilidad.

## Decisión

Adoptamos la alternativa 2. `PUBLISHER_MODE=mock|youtube` selecciona la
implementación en el arranque. `MockPublisher` es el camino por defecto en
desarrollo, en CI y en la demo del Sprint 1.

Dos decisiones derivadas:

- **El fallo del mock es determinista**, derivado de un hash del `contentId`,
  no aleatorio. Un fallo aleatorio hace que un test pase o falle según el día
  y que una demo se rompa sin explicación. Con hash podemos exhibir el camino
  de error a voluntad usando un `contentId` conocido.
- **El publicador no emite eventos al broker.** Devuelve un `PublishResult` y
  la emisión de `publish.completed` / `publish.failed` ocurre en la capa de
  aplicación. Así el criterio de aceptación "el mock emite los mismos eventos
  que el real" se cumple por construcción: hay un solo camino de emisión.

## Consecuencias

- El Sprint 1 y el Sprint 2 pueden completarse sin credenciales reales, y la
  demo no depende de la red ni de la cuota.
- En Sprint 3, US-C3 solo debe rellenar el cuerpo de `YouTubePublisher`. El
  scheduler, la máquina de estados y los eventos no se tocan.
- Riesgo asumido: el mock no reproduce las latencias, los errores 5xx ni los
  límites de cuota reales. Ese comportamiento se ejercita recién en US-C5
  (Sprint 3), así que hasta entonces nuestra robustez frente a la API real
  está sin verificar. Lo mitigamos parcialmente con `MOCK_FAILURE_RATE`, que
  permite ensayar el camino de fallo.
- El código OAuth ya escrito queda en `app/oauth/`, fuera del flujo del
  Sprint 1, listo para Sprint 3.
