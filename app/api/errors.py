"""Traduce excepciones al sobre de error acordado (Doc 1 §6.3):

    { "status": "error", "code": "SCHEDULE_CONFLICT", "message": "..." }

Sin esto, FastAPI devuelve {"detail": ...} y el contrato queda roto aunque
el código funcione. Es exactamente el tipo de desajuste que se penaliza en
el criterio de Integración.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import DomainError, ErrorCode


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "status": "error",
                "code": exc.code.value,
                "message": exc.message,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        detalles = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "code": ErrorCode.INVALID_METADATA.value,
                "message": f"Solicitud inválida. {detalles}",
            },
        )
