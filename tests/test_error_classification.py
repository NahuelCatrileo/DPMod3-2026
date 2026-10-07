"""US-C5.1 · Pruebas de la clasificación de errores.

Los casos están en formato de tabla: una fila por clasificación relevante de la
historia. Así la tabla de la historia y `classify` no pueden desincronizarse sin
que alguno de estos casos falle.
"""

import pytest

from app.domain.classification import ErrorCategory, FailureKind, classify
from app.domain.errors import ErrorCode
from app.infra.publishers.base import PublishResult


@pytest.mark.parametrize(
    "resultado,esperado",
    [
        # --- Diferible: hay que esperar a que la cuota se renueve ------------
        (
            PublishResult.failure(ErrorCode.QUOTA_EXCEEDED, "Cuota agotada"),
            ErrorCategory.DEFERRABLE,
        ),
        # --- Transitorio: el problema puede desaparecer ----------------------
        (
            PublishResult.failure(ErrorCode.PUBLISH_FAILED, "Se cayó la red", FailureKind.NETWORK),
            ErrorCategory.TRANSIENT,
        ),
        (
            PublishResult.failure(
                ErrorCode.PUBLISH_FAILED, "Timeout del socket", FailureKind.TIMEOUT
            ),
            ErrorCategory.TRANSIENT,
        ),
        (
            PublishResult.failure(ErrorCode.PUBLISH_FAILED, "HTTP 503", FailureKind.HTTP_5XX),
            ErrorCategory.TRANSIENT,
        ),
        # --- Definitivo: reintentar con la misma solicitud da lo mismo -------
        (
            PublishResult.failure(ErrorCode.OAUTH_ERROR, "Token inválido"),
            ErrorCategory.PERMANENT,
        ),
        (
            PublishResult.failure(ErrorCode.PUBLISH_FAILED, "HTTP 400", FailureKind.HTTP_4XX),
            ErrorCategory.PERMANENT,
        ),
        # PUBLISH_FAILED es genérico: sin evidencia de que el fallo se
        # recupere solo, la categoría es la conservadora.
        (
            PublishResult.failure(ErrorCode.PUBLISH_FAILED, "Excepción no controlada"),
            ErrorCategory.PERMANENT,
        ),
        # Código que todavía no existe en el catálogo registrado.
        (
            PublishResult.failure("CODIGO_FUTURO", "Código desconocido"),
            ErrorCategory.PERMANENT,
        ),
        # Código desconocido manda sobre la pista de transporte: un catálogo
        # que crezca no puede traducirse en reintentos potencialmente infinitos.
        (
            PublishResult.failure(
                "CODIGO_FUTURO", "Desconocido con pista transitoria", FailureKind.TIMEOUT
            ),
            ErrorCategory.PERMANENT,
        ),
        # Código registrado que no participa de la publicación.
        (
            PublishResult.failure(ErrorCode.UNAUTHORIZED, "No aplica a publicar"),
            ErrorCategory.PERMANENT,
        ),
        # Fallo sin código: resultado mal formado, no se reintenta.
        (PublishResult(success=False), ErrorCategory.PERMANENT),
        # Un éxito no se clasifica nunca; si igual se pasara, la función es
        # total y responde lo conservador, jamás TRANSIENT.
        (PublishResult.ok("6226af224c1"), ErrorCategory.PERMANENT),
    ],
    ids=[
        "quota_exceeded_es_diferible",
        "falla_de_red_es_transitoria",
        "timeout_es_transitorio",
        "http_5xx_es_transitorio",
        "oauth_error_es_definitivo",
        "http_4xx_de_validacion_es_definitivo",
        "publish_failed_sin_detalle_es_definitivo",
        "codigo_desconocido_es_definitivo",
        "codigo_desconocido_gana_a_la_pista_transitoria",
        "codigo_registrado_no_clasificable_es_definitivo",
        "fallo_sin_codigo_es_definitivo",
        "exito_no_habilita_reintento",
    ],
)
def test_clasificacion_por_caso(resultado, esperado):
    assert classify(resultado) is esperado


def test_el_enum_tiene_exactamente_las_tres_categorias_del_contrato():
    assert {categoria.name for categoria in ErrorCategory} == {
        "TRANSIENT",
        "DEFERRABLE",
        "PERMANENT",
    }


@pytest.mark.parametrize("codigo", list(ErrorCode))
@pytest.mark.parametrize("naturaleza", [None, *FailureKind])
def test_todo_codigo_del_contrato_tiene_una_categoria_valida(codigo, naturaleza):
    """Ningún código del catálogo queda sin categoría, con o sin naturaleza."""
    categoria = classify(PublishResult.failure(codigo, "motivo", naturaleza))
    assert categoria in set(ErrorCategory)


def test_classify_es_pura_repetible_y_no_modifica_el_resultado():
    resultado = PublishResult.failure(
        ErrorCode.PUBLISH_FAILED, "Timeout del socket", FailureKind.TIMEOUT
    )
    antes = (
        resultado.success,
        resultado.error_code,
        resultado.reason,
        resultado.failure_kind,
    )

    primeras = [classify(resultado) for _ in range(3)]

    despues = (
        resultado.success,
        resultado.error_code,
        resultado.reason,
        resultado.failure_kind,
    )
    assert primeras == [ErrorCategory.TRANSIENT] * 3
    assert despues == antes


def test_classify_solo_necesita_la_vista_de_dominio():
    """`classify` depende de `ClassifiableResult`, no de `PublishResult`: si
    hiciera I/O o necesitara infraestructura, este objeto mínimo no alcanzaría.
    """

    class ResultadoMinimo:
        error_code = ErrorCode.QUOTA_EXCEEDED
        failure_kind = None

    assert classify(ResultadoMinimo()) is ErrorCategory.DEFERRABLE


# --- Compatibilidad con el resultado de US-C4 -----------------------------


def test_failure_acepta_la_naturaleza_del_fallo():
    resultado = PublishResult.failure(
        ErrorCode.PUBLISH_FAILED, "Timeout del socket", FailureKind.TIMEOUT
    )
    assert resultado.failure_kind is FailureKind.TIMEOUT


def test_publishresult_sigue_siendo_construible_como_en_us_c4():
    """`failure_kind` es aditivo: los cuatro campos de US-C4 siguen valiendo."""
    resultado = PublishResult.failure(ErrorCode.PUBLISH_FAILED, "sin detalle")
    assert resultado.failure_kind is None
    assert PublishResult(True, "6226af224c1").success is True
