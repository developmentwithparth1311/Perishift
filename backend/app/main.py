from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.db import database_is_ready

app = FastAPI(
    title="PeriShift API",
    description="Deadline-aware perishable aid dispatch prototype.",
    version="0.1.0",
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
