from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://perishift:local-dev-only@localhost:5432/perishift"
    demo_mode: bool = True


settings = Settings()
