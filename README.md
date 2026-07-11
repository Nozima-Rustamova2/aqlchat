# aqlchat

AI customer-service bot for Telegram sellers in Uzbekistan. Merchants connect their own Telegram bot; aqlchat answers customer messages in Uzbek and Russian — including both scripts mixed in a single message — and hands anything it can't answer to the merchant.

## Features

- **Mixed-language understanding** — normalizes Uzbek Latin/Cyrillic and Russian text (transliteration + language detection) before any matching, so "Спортивные krossovka 42 razmer bormi?" just works.
- **Layered reply pipeline** — cheap deterministic layers answer first; the LLM is a fallback, not the front line:
  1. Ordinal resolution — "how much is the second one?" resolves against the last product carousel shown.
  2. Flows — merchant-defined keyword rules with canned replies.
  3. FAQ retrieval — embedding similarity (BGE-M3) against the merchant's FAQ set, with a confidence threshold.
  4. Intent classification — greeting / thanks / complaint / handoff / product inquiry, routed to the merchant's own flow copy so there is a single source of truth for reply text.
  5. LLM fallback — Claude, with per-merchant budget limits and Redis response caching.
  6. Human handoff — anything still unanswered escalates to the merchant.
- **Photo search** — a customer sends a product photo; the bot replies with a top-k carousel of catalog matches and inline confirm buttons.
- **Merchant stays in control** — a shared platform bot is the merchant's admin channel, separate from their own customer-facing tenant bot. Escalations are forwarded there; the merchant answers with `/reply`, which suppresses all automation on that conversation until it is released.
- **Self-serve onboarding** — a merchant messages the platform bot, pastes a fresh BotFather token, and their own tenant bot is registered and live within the same conversation. No manual seeding required.
- **Multi-tenant** — one deployment serves many merchants; each has its own bot token (encrypted at rest), webhook URL, and secret token.

## Stack

FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 + pgvector · Redis · sentence-transformers (BAAI/bge-m3) · Anthropic Claude · [uv](https://docs.astral.sh/uv/)

## Getting started

Requires Python 3.12+, Docker, and uv.

```bash
# 1. Infrastructure (Postgres on 5433, Redis on 6380 — non-default ports to avoid clashes)
docker compose up -d

# 2. Dependencies
uv sync

# 3. Configuration
cp .env.example .env   # fill in TOKEN_ENCRYPTION_KEY, PLATFORM_BOT_TOKEN, PLATFORM_WEBHOOK_SECRET,
                        # PUBLIC_BASE_URL - see comments in .env.example for how to generate each.
                        # DB/Redis defaults match docker-compose; adjust HF_HOME if disk space is tight.

# 4. Database schema
uv run alembic upgrade head

# 5. Run the API
uv run uvicorn app.main:app --reload

# 6. Register the platform bot's webhook, then onboard a merchant by
#    messaging it directly (see "Connecting a Telegram bot" below) -
#    or seed one manually and load its content:
uv run python scripts/seed_merchant.py --help
uv run python scripts/load_flows.py    # see examples/flows.example.yaml
uv run python scripts/load_faqs.py     # see examples/faqs.example.yaml
uv run python scripts/load_products.py # see examples/products.example.yaml
```

Note: the first FAQ/photo operation downloads the BGE-M3 embedding model (~2.2 GB) into `HF_HOME`.

### Connecting a Telegram bot

Two bots are involved: the **platform bot** (shared, merchant-facing admin channel + onboarding) and each merchant's own **tenant bot** (customer-facing).

Register the platform bot's webhook once, using `PLATFORM_WEBHOOK_SECRET`:

```
https://<your-host>/telegram/webhook/platform
```

From there, message the platform bot as a merchant would: `/start`, pick a language, paste a fresh BotFather token when prompted. Onboarding registers the new tenant bot's webhook automatically (using `PUBLIC_BASE_URL`) at:

```
https://<your-host>/telegram/webhook/tenant/<merchant.webhook_slug>
```

If `PUBLIC_BASE_URL` changes (e.g. a dev tunnel restarts), re-register every webhook with `uv run python -m scripts.reregister_webhooks`.

Each endpoint rejects requests whose `X-Telegram-Bot-Api-Secret-Token` header doesn't match. For local development, expose the server with a tunnel (e.g. ngrok).

`GET /health` is available for liveness checks.

## Configuration

All settings load from `.env` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `ENV` | `development` / `production` |
| `DATABASE_URL` | Postgres DSN (`postgresql+psycopg://...`) |
| `REDIS_URL` | Redis, used for LLM response caching and budgets |
| `TELEGRAM_API_BASE` | Override for testing against a fake Telegram API |
| `HF_HOME` | HuggingFace model cache directory |
| `TOKEN_ENCRYPTION_KEY` | Fernet key encrypting tenant bot tokens at rest |
| `PLATFORM_BOT_TOKEN` | The shared platform bot's token (onboarding + admin channel) |
| `PLATFORM_WEBHOOK_SECRET` | Verifies inbound requests to the platform bot's webhook |
| `PUBLIC_BASE_URL` | This server's externally-reachable base URL, used to auto-register tenant webhooks during onboarding |

Per-merchant secrets (bot token, webhook secret, webhook slug) live in the database, not in environment variables. The bot token is encrypted at rest (`app/db/crypto.py`); who administers a merchant lives in `merchant_admins`, not a single admin chat id.

## Project layout

```
app/
  telegram/       tenant webhook endpoint, Telegram API client, update schemas
  onboarding/     platform bot webhook + self-serve onboarding state machine
  nlp/            transliteration/normalization, language ID, embeddings
  flows/          merchant-defined keyword rules
  faq/            embedding-based FAQ retrieval
  intent/         semantic intent classifier + reply router
  image_search/   photo -> catalog matching, carousel UX, ordinal references
  llm/            Claude fallback: provider, context, cache, budget
  handoff/        escalation + merchant admin commands (/reply, ...)
  db/             SQLAlchemy models, session, at-rest encryption
alembic/          migrations
scripts/          merchant seeding, webhook re-registration, YAML content loaders
examples/         example flow/FAQ/product YAML and product photos
tests/            pytest suite (fixtures include mixed-language examples)
```

## Tests

```bash
uv run pytest
```
