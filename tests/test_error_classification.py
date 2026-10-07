"""US-C5 · Clasificación de errores: transitorio / diferible / definitivo.

La tabla de `classify()` es la fuente única de la política de reintentos
(ADR-0006): si un código queda sin clasificar, se comporta como definitivo
y el sistema prefiere fallar antes que reintentar algo que no entiende.
"""

from app.domain.errors import ERROR_CLASS_BY_CODE, ErrorClass, ErrorCode, classify


def test_los_valores_de_la_clase_son_los_del_contrato_interno():
    assert [c.value for c in ErrorClass] == ["transitorio", "diferible", "definitivo"]


def test_mapeo_aprobado_transitorio():
    assert classify(ErrorCode.PUBLISH_FAILED) is ErrorClass.TRANSITORIO
    assert classify(ErrorCode.EVENT_VALIDATION_ERROR) is ErrorClass.TRANSITORIO


def test_mapeo_aprobado_diferible():
    assert classify(ErrorCode.QUOTA_EXCEEDED) is ErrorClass.DIFERIBLE


def test_mapeo_aprobado_definitivo():
    for code in (
        ErrorCode.OAUTH_ERROR,
        ErrorCode.UNAUTHORIZED,
        ErrorCode.INVALID_METADATA,
        ErrorCode.CONTENT_NOT_FOUND,
        ErrorCode.DUPLICATE_CONTENT,
        ErrorCode.SCHEDULE_CONFLICT,
    ):
        assert classify(code) is ErrorClass.DEFINITIVO, code


def test_toda_la_tabla_de_codigos_esta_clasificada():
    """Si el catálogo crece (§5.4), hay que decidir su clase, no olvidarlo."""
    sin_clasificar = [code.value for code in ErrorCode if code not in ERROR_CLASS_BY_CODE]
    assert sin_clasificar == []


def test_codigo_desconocido_se_trata_como_definitivo(monkeypatch):
    """No se reintenta lo que no se entiende: fallar es el camino seguro."""
    monkeypatch.delitem(ERROR_CLASS_BY_CODE, ErrorCode.UNAUTHORIZED)
    assert classify(ErrorCode.UNAUTHORIZED) is ErrorClass.DEFINITIVO
