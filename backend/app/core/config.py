from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+psycopg://perishift:local-dev-only@localhost:5432/perishift"
    demo_mode: bool = True
    jwt_secret: str = "local-dev-only-change-this-secret"


settings = Settings()
