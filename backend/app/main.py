from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.batches import router as batches_router
from app.api.demo import router as demo_router
from app.api.events import router as events_router
from app.api.lots import router as lots_router
from app.api.recovery import router as recovery_router
from app.api.shelters import router as shelters_router
from app.contracts.enums import ApiErrorCode
from app.contracts.openapi import build_openapi_schema
from app.contracts.schemas import ErrorDetail, ErrorEnvelope
from app.core.db import database_is_ready
from app.core.errors import ServiceError

app = FastAPI(
    title="PeriShift API",
    description="Deadline-aware perishable aid dispatch prototype.",
    version="0.1.0",
)


def openapi_with_contracts() -> dict[str, object]:
    if app.openapi_schema is None:
        app.openapi_schema = build_openapi_schema(app)
    return app.openapi_schema


app.openapi = openapi_with_contracts
app.include_router(batches_router)
app.include_router(demo_router)
app.include_router(lots_router)
app.include_router(recovery_router)
app.include_router(events_router)
app.include_router(shelters_router)


def _error_response(
    *, code: ApiErrorCode, message: str, request_id: str, status_code: int,
    details: dict[str, object] | None = None, retryable: bool | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details, retryable=retryable),
        request_id=request_id,
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"), headers=headers)


@app.exception_handler(ServiceError)
def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
    return _error_response(
        code=exc.code,
        message=exc.message,
        request_id=request.headers.get("X-Request-ID", "") or uuid4().hex,
        status_code=exc.status_code,
        details=exc.details,
        retryable=exc.retryable,
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        401: ApiErrorCode.UNAUTHENTICATED,
        403: ApiErrorCode.FORBIDDEN_ROLE,
        404: ApiErrorCode.NOT_FOUND,
        422: ApiErrorCode.INVALID_QUANTITY,
        428: ApiErrorCode.PRECONDITION_REQUIRED,
    }.get(exc.status_code, ApiErrorCode.STATE_CONFLICT)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    details = None
    if isinstance(exc.detail, dict):
        raw_code = exc.detail.get("code")
        try:
            code = ApiErrorCode(raw_code)
        except (ValueError, TypeError):
            pass
        message = str(exc.detail.get("message", message))
        details = {key: value for key, value in exc.detail.items() if key not in {"code", "message"}}
    return _error_response(
        code=code,
        message=message,
        request_id=request.headers.get("X-Request-ID", "") or uuid4().hex,
        status_code=exc.status_code,
        details=details,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = {
        "errors": [
            {"location": list(error.get("loc", ())), "message": error.get("msg", "Invalid value"), "type": error.get("type", "value_error")}
            for error in exc.errors()
        ]
    }
    return _error_response(
        code=ApiErrorCode.INVALID_QUANTITY,
        message="Request validation failed.",
        request_id=request.headers.get("X-Request-ID", "") or uuid4().hex,
        status_code=422,
        details=details,
    )


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> JSONResponse:
    database_ready = database_is_ready()
    return JSONResponse(
        status_code=200 if database_ready else 503,
        content={
            "status": "ok" if database_ready else "unavailable",
            "checks": {"database": "ok" if database_ready else "unavailable"},
        },
    )
