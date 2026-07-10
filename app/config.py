from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://aqlchat:aqlchat@localhost:5433/aqlchat"
    redis_url: str = "redis://localhost:6380/0"
    telegram_api_base: str = "https://api.telegram.org"
    hf_home: str = ".hf_cache"
    embedding_model_name: str = "BAAI/bge-m3"


settings = Settings()
