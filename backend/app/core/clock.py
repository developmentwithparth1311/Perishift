from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Scenario


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ScenarioClock:
    """Reads the current UTC-aware clock value from the active simulation scenario."""

    def __init__(self, session: Session, scenario_id):
        self._session = session
        self._scenario_id = scenario_id

    def now(self) -> datetime:
        scenario = self._session.get(Scenario, self._scenario_id)
        if scenario is None or not scenario.active:
            raise LookupError("active scenario not found")
        current = scenario.current_time
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("scenario clock must be timezone-aware")
        return current.astimezone(timezone.utc)
