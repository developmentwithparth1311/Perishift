from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.contracts.enums import ScenarioMode
from app.core.config import settings
from app.core.db import get_db
from app.models import Scenario, User


bearer = HTTPBearer(auto_error=False)


def current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub"]},
        )
        actor_id = UUID(claims["sub"])
        demo_scenario_id = None
        if "exp" not in claims:
            if not settings.demo_mode or claims.get("token_use") != "local_demo":
                raise ValueError("an expiration is required for ordinary credentials")
            demo_scenario_id = UUID(claims["scenario_id"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired bearer token", headers={"WWW-Authenticate": "Bearer"})
    actor = session.get(User, actor_id)
    if actor is None or not actor.active:
        raise HTTPException(status_code=401, detail="Actor is inactive or unknown", headers={"WWW-Authenticate": "Bearer"})
    if demo_scenario_id is not None:
        scenario = session.get(Scenario, demo_scenario_id)
        if (
            actor.scenario_id != demo_scenario_id
            or scenario is None
            or scenario.mode != ScenarioMode.SIMULATED.value
        ):
            raise HTTPException(status_code=401, detail="Demo credentials require their matching simulated scenario", headers={"WWW-Authenticate": "Bearer"})
    return actor


def require_role(actor: User, role: str) -> User:
    if actor.role != role:
        raise HTTPException(status_code=403, detail="Actor role is not permitted for this operation")
    return actor
