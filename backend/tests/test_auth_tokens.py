from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import current_actor
from app.core.config import settings


@pytest.mark.parametrize("extra_claims", [
    {},
    {"token_use": "ordinary", "scenario_id": str(uuid4())},
    {"token_use": "local_demo"},
    {"token_use": "local_demo", "scenario_id": None},
    {"token_use": "local_demo", "scenario_id": "invalid"},
    {"exp": datetime.now(timezone.utc) - timedelta(days=1)},
    {"exp": datetime.now(timezone.utc) - timedelta(days=1), "token_use": "local_demo", "scenario_id": str(uuid4())},
    {"exp": None},
])
def test_invalid_or_expired_tokens_are_rejected_before_database_access(monkeypatch, extra_claims):
    monkeypatch.setattr(settings, "demo_mode", True)
    token = jwt.encode({"sub": str(uuid4()), **extra_claims}, settings.jwt_secret, algorithm="HS256")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as rejected:
        current_actor(credentials=credentials, session=None)
    assert rejected.value.status_code == 401


def test_nonexpiring_tokens_are_rejected_when_demo_mode_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    token = jwt.encode(
        {"sub": str(uuid4()), "token_use": "local_demo", "scenario_id": str(uuid4())},
        settings.jwt_secret, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as rejected:
        current_actor(credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), session=None)
    assert rejected.value.status_code == 401
