"""Publicación de eventos al broker.

Dos implementaciones detrás de la misma interfaz:

  LogEventPublisher  -> doble de prueba (Guía §5.1). Escribe a stdout y guarda
                        en memoria. Nos permite avanzar sin depender del
                        Equipo B y es lo que usan los tests.
  AmqpEventPublisher -> RabbitMQ real, topic exchange 'pubtube.events'.

Se elige con EVENT_TRANSPORT=log|amqp. Mientras no confirmemos con Equipo B
que el exchange está operativo, el default es 'log'.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class EventPublisher(ABC):
    """Contrato de emisión de eventos."""

    @abstractmethod
    def publish(self, envelope: dict[str, Any]) -> None:
        """Publica un envelope ya construido usando envelope['type'] como routing key."""

    def close(self) -> None:  # pragma: no cover - opcional
        return None


class LogEventPublisher(EventPublisher):
    """Doble de prueba. No requiere broker."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, envelope: dict[str, Any]) -> None:
        self.published.append(envelope)
        logger.info(
            "evento_publicado(doble) type=%s correlationId=%s payload=%s",
            envelope["type"],
            envelope["correlationId"],
            json.dumps(envelope["payload"], ensure_ascii=False),
        )

    def clear(self) -> None:
        self.published.clear()

    def events_of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.published if e["type"] == event_type]


class AmqpEventPublisher(EventPublisher):
    """Publicador real contra RabbitMQ.

    Reconecta de forma perezosa: si el broker no está arriba al arrancar,
    la aplicación NO se cae; se reintenta en la siguiente publicación.
    """

    def __init__(self, amqp_url: str, exchange: str) -> None:
        self._amqp_url = amqp_url
        self._exchange = exchange
        self._connection = None
        self._channel = None

    def _ensure_channel(self):
        import pika  # import local: los tests no necesitan pika instalado

        if self._channel is not None and not self._channel.is_closed:
            return self._channel

        params = pika.URLParameters(self._amqp_url)
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(
            exchange=self._exchange, exchange_type="topic", durable=True
        )
        return self._channel

    def publish(self, envelope: dict[str, Any]) -> None:
        import pika

        channel = self._ensure_channel()
        channel.basic_publish(
            exchange=self._exchange,
            routing_key=envelope["type"],
            body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,  # persistente
                message_id=envelope["id"],
                correlation_id=envelope["correlationId"],
            ),
        )
        logger.info(
            "evento_publicado(amqp) type=%s correlationId=%s",
            envelope["type"],
            envelope["correlationId"],
        )

    def close(self) -> None:  # pragma: no cover
        if self._connection is not None and self._connection.is_open:
            self._connection.close()


_event_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Singleton perezoso. Se sobrescribe en tests con set_event_publisher()."""
    global _event_publisher
    if _event_publisher is None:
        settings = get_settings()
        if settings.EVENT_TRANSPORT == "amqp":
            _event_publisher = AmqpEventPublisher(
                settings.AMQP_URL, settings.AMQP_EXCHANGE
            )
        else:
            _event_publisher = LogEventPublisher()
    return _event_publisher


def set_event_publisher(publisher: EventPublisher | None) -> None:
    global _event_publisher
    _event_publisher = publisher
