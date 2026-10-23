from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)


def database_is_ready() -> bool:
    """Return whether PostgreSQL accepts a lightweight health query."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True
