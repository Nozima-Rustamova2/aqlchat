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

    # Resend (app/auth/email.py) - sends the website signup/login 6-digit
    # email verification code. resend_from_email defaults to Resend's
    # shared sandbox sender, which only delivers to the Resend account's
    # own verified address - swap to a verified domain sender before real
    # merchants sign up.
    resend_api_key: str = ""
    resend_from_email: str = "Dukan AI <onboarding@resend.dev>"

    # Instagram comment-to-DM automation (app/instagram/). app_secret
    # signs webhook payloads (X-Hub-Signature-256); verify_token is the
    # static challenge secret for Meta's GET verification handshake -
    # both blank by default so an unconfigured deploy fails webhook
    # verification loudly instead of accepting unsigned traffic.
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_verify_token: str = ""
    # Pinned Graph API version (Meta retires versions ~2x/year - bump
    # deliberately, with the changelog open, not implicitly).
    instagram_graph_api_version: str = "v23.0"

    # This server's own externally-reachable base URL, used to construct
    # setWebhook calls when a tenant bot self-registers during onboarding
    # (see app/onboarding/service.py) and by scripts/reregister_webhooks.py.
    # No safe default - a wrong value here silently misregisters webhooks.
    public_base_url: str = ""

    # Supabase - a SEPARATE Postgres (pgvector) backend for the Agentlar
    # feature's knowledge base (app/agents/knowledge.py), deliberately
    # apart from database_url/app/db/session.py's engine - see the
    # aqlchat-agents-knowledge-base-supabase memory. supabase_key is the
    # service-role key (server-side writes via PostgREST, not the
    # anon/public key). No safe default; a blank value surfaces as a
    # failed httpx call, not a crash.
    supabase_url: str = ""
    supabase_key: str = ""

    # Gemini embeddings for that same knowledge base - confirmed live
    # 2026-07-20 against the configured gemini_api_key (models/gemini-embedding-001
    # supports embedContent; output_dimensionality=768 confirmed to return
    # 768-dim vectors). A SEPARATE embedding space from BGE-M3's
    # EMBEDDING_DIM (app/db/models.py, app/nlp/embeddings.py), which is
    # what Faq/Product retrieval use against the local Postgres - no
    # dimension coincidence is intended between the two.
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimension: int = 768


settings = Settings()
