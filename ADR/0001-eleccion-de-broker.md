# ADR 0001 — Elección de Broker de Eventos

**Fecha:** 25-08-2026
**Estado:** Decidido

## Contexto
El módulo necesita un broker de eventos para comunicarse
con los otros módulos del proyecto PubTube mediante
el patrón Pub/Sub.

## Opciones evaluadas
- RabbitMQ
- Kafka
- Redis Pub/Sub

## Decisión
[Hemos decidido utilizar RabbitMQ.

Para un proyecto como PubTube, la arquitectura requiere manejar de forma confiable tareas que consumen muchos recursos (como la transcodificación de videos a múltiples resoluciones, la generación de miniaturas y el envío de notificaciones a los suscriptores). RabbitMQ fue elegido por las siguientes razones:

Garantía de entrega y retentiva (Acknowledgments): Si un nodo encargado de procesar un video falla a la mitad de la tarea, RabbitMQ no elimina el mensaje de la cola. Esto asegura que la tarea sea reasignada a otro nodo disponible, evitando la pérdida de datos o videos atascados en "procesando".

Enrutamiento avanzado (Exchanges): Permite implementar el patrón Pub/Sub de forma flexible. Por ejemplo, al subir un video, el evento puede ser enrutado simultáneamente al módulo de procesamiento (para comprimirlo), al módulo de notificaciones (para avisar a los seguidores) y al módulo de analíticas.

Gestión de fallos (Dead Letter Queues): Permite aislar fácilmente los mensajes (tareas) que fallan repetidamente sin bloquear el flujo principal, facilitando la depuración.

Aunque Kafka es excelente para analítica de flujos masivos y Redis Pub/Sub es muy rápido, RabbitMQ ofrece el equilibrio perfecto entre enrutamiento inteligente, colas de trabajo pesado y retención temporal segura para los flujos transaccionales de nuestra plataforma.
]

## Consecuencias
[Infraestructura: Es necesario desplegar y mantener instancias de RabbitMQ (por ejemplo, contenerizadas vía Docker) para los entornos de desarrollo, pruebas y producción.

Curva de aprendizaje del equipo: Los desarrolladores deberán familiarizarse con los conceptos del protocolo AMQP, específicamente la topología de Exchanges, Queues y Bindings.

Diseño de tolerancia a fallos: Los servicios consumidores deberán estar diseñados para ser idempotentes (es decir, procesar el mismo mensaje dos veces no debe corromper los datos) en caso de que ocurra una re-entrega por error de red.

Complejidad operativa: Se añade un nuevo punto único de fallo (SPOF) a la arquitectura. Si RabbitMQ se cae, los módulos no podrán comunicarse asíncronamente, aunque la plataforma de cara al usuario podría seguir funcionando parcialmente (ej. visualización en modo lectura).
]