from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://aqlchat:aqlchat@localhost:5433/aqlchat"
    redis_url: str = "redis://localhost:6380/0"
    telegram_api_base: str = "https://api.telegram.org"
    hf_home: str = ".hf_cache"
    embedding_model_name: str = "BAAI/bge-m3"

    # Fernet key encrypting merchants.telegram_bot_token at rest - see
    # app/db/crypto.py. No safe default; empty means "not configured",
    # which fails loudly the first time a token is actually read/written
    # rather than silently storing plaintext.
    token_encryption_key: str = ""

    # The shared platform bot (merchant-facing: onboarding, escalation
    # inbox, /reply /release) - see app/onboarding/. Distinct from tenant
    # bot tokens, which live per-merchant in the database, not env vars.
    platform_bot_token: str = ""
    platform_webhook_secret: str = ""

    # Gemini grounded answering (app/llm/answer.py, app/llm/gemini_provider.py) -
    # the llm_first pipeline mode's answer layer. No safe default; a blank
    # key surfaces as a provider-call failure (handoff), not a crash - see
    # GeminiProvider's lazy construction.
    gemini_api_key: str = ""

    # This server's own externally-reachable base URL, used to construct
    # setWebhook calls when a tenant bot self-registers during onboarding
    # (see app/onboarding/service.py) and by scripts/reregister_webhooks.py.
    # No safe default - a wrong value here silently misregisters webhooks.
    public_base_url: str = ""


settings = Settings()
