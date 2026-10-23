from dataclasses import dataclass

from app.contracts.enums import ApiErrorCode


@dataclass(slots=True)
class ServiceError(Exception):
    code: ApiErrorCode
    message: str
    status_code: int = 409
    details: dict[str, object] | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message
