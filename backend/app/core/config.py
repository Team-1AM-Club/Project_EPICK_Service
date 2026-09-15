from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    database_url: str = "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_local"
    migration_database_url: str | None = None
    test_database_url: str = "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_test"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
