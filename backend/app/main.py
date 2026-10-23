from fastapi import FastAPI

app = FastAPI(
    title="PeriShift API",
    description="Deadline-aware perishable aid dispatch prototype.",
    version="0.1.0",
)


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}
