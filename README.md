# Dukan AI

A ManyChat-style AI customer-service and automation platform, built for small merchants in Uzbekistan who sell over Telegram and Instagram. "Dukan" means shop — the product exists because that's how commerce actually happens here: a seller posts a product on Instagram, customers ask "narxi qancha?" in the comments and DMs all day, and answering all of it by hand doesn't scale past a handful of orders.

## The idea

Most small Uzbek and Central Asian merchants run their whole storefront through Instagram and Telegram — no separate e-commerce site, no call center. The tools built for this pattern (ManyChat, Chatfuel, etc.) are designed around English-first, Latin-script markets and don't handle mixed-script Uzbek/Russian text well, don't understand a customer typing "Спортивные krossovka 42 razmer bormi?" as one coherent question. Dukan AI is built for that reality first:

- **It understands the language customers actually type in** — Uzbek Latin, Uzbek Cyrillic, and Russian, freely mixed within a single message, normalized and matched correctly before anything else happens.
- **It automates the two channels merchants actually use** — a Telegram bot that answers customer questions directly, and Instagram comment/story-reply automation that turns "narx?" into an instant DM with the merchant's price list or order link.
- **It's cheap by design** — a layered reply pipeline answers with free, deterministic rules and embedding retrieval first, and only calls an LLM when nothing simpler can handle it, so per-merchant cost stays low at pilot scale.
- **It's self-serve** — a merchant signs up on the website, connects Telegram and Instagram themselves, and installs pre-built automations without writing a single rule by hand.

## Who it's for

Small-to-medium merchants across Uzbekistan (and the broader Uzbek/Russian-speaking region) who sell through Instagram and Telegram: clothing and footwear sellers, home goods, course/training providers running enrollment through DMs, and similar "Instagram do'kon" businesses — anyone currently retyping the same price and link into comments and DMs dozens of times a day, who wants that automated without hiring anyone or learning a new platform built for someone else's market.

## What's ready

**Telegram AI customer service**
- Self-serve onboarding — a merchant messages the shared platform bot, pastes a fresh BotFather token, and their own tenant bot is live in the same conversation.
- Layered reply pipeline: ordinal resolution ("how much is the second one?") → merchant-defined keyword flows → FAQ retrieval (BGE-M3 embeddings) → intent classification (greeting/thanks/complaint/handoff) → Claude LLM fallback with per-merchant budget caps and response caching → human handoff.
- Photo search — a customer sends a product photo, the bot replies with a top-k carousel of catalog matches.
- Merchant admin channel — escalations forward to the merchant, who answers with `/reply`, suppressing automation on that conversation until released.
- Multi-tenant from the ground up — each merchant's bot token is encrypted at rest, isolated by its own webhook.

**Merchant website & dashboard**
- Email + password signup with one-time email verification (via Resend), session-based auth (`/signup`, `/auth/*`).
- A ManyChat-style dashboard shell: Home (connection status, next-steps checklist, quick-start automation cards), Automation, Settings — with unbuilt sections (Inbox) marked "tez kunda" rather than hidden or faked.
- Telegram deep-link connect — clicking "Connect" from the dashboard binds your existing web account to your Telegram bot, no duplicate accounts.
- Instagram OAuth connect (Instagram API with Instagram Login — no Facebook Page required, since most merchants here don't have one).

**Instagram automation**
- Comment-to-DM: a customer comments a keyword on a post/reel → private DM with the merchant's link, with an optional public "DM'ga yubordim! 📩" reply on exact keyword matches. Scopable to specific posts. See [`app/instagram/README.md`](app/instagram/README.md) for the full pipeline.
- Story-reply-to-DM: the same automation surface applied to Story replies, on Meta's 24h messaging window instead of the 7-day comment private-reply window.
- Four ready-made templates (price inquiry, link request, giveaway keyword, course enrollment) — merchants fill in a link (and, for the giveaway template, their own entry keyword); trigger keywords and reply copy are pre-vetted, not free text.
- Keyword-collision warning when installing overlapping automations.

## What's pending

- **Real DM conversation automation** — today, Instagram DM automation only covers one-shot Story replies. A generic "customer messages you" trigger needs actual conversation/thread-state tracking, which isn't built yet.
- **Meta App Review / Live Mode** — Instagram automation currently works for accounts added as Instagram Testers (Development Mode). Serving real, non-tester customers needs Business Verification and App Review approval for the comment/message permissions — a multi-week Meta-side process, not a code change.
- **Unified inbox ("Suhbatlar")** — visible in the dashboard nav, not yet built.
- **Analytics/insights** — the dashboard's "See Insights" link is a placeholder.
- **Vertical-specific FAQ/flow presets** — a plan for auto-seeding starter content per merchant vertical exists but is parked, not built.
- **Billing/subscription** — no pricing or payment layer yet; this is a pre-monetization build.

## Stack

FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 + pgvector · Redis · sentence-transformers (BAAI/bge-m3) · Anthropic Claude · Resend · [uv](https://docs.astral.sh/uv/)

## Getting started

Requires Python 3.12+, Docker, and uv.

```bash
# 1. Infrastructure (Postgres on 5433, Redis on 6380 — non-default ports to avoid clashes)
docker compose up -d

# 2. Dependencies
uv sync

# 3. Configuration
cp .env.example .env   # fill in TOKEN_ENCRYPTION_KEY, PLATFORM_BOT_TOKEN, PLATFORM_WEBHOOK_SECRET,
                        # PUBLIC_BASE_URL, RESEND_API_KEY - see comments in .env.example for how
                        # to generate/obtain each. DB/Redis defaults match docker-compose.

# 4. Database schema
uv run alembic upgrade head

# 5. Run the API
uv run uvicorn app.main:app --reload

# 6. Instagram comment/story-reply automation needs a second process:
uv run python -m app.instagram.worker

# 7. Register the platform bot's webhook, then onboard a merchant by
#    messaging it directly (see "Connecting a Telegram bot" below), or
#    sign up as a merchant on the website (/signup) - or seed one
#    manually and load its content:
uv run python scripts/seed_merchant.py --help
uv run python scripts/load_flows.py    # see examples/flows.example.yaml
uv run python scripts/load_faqs.py     # see examples/faqs.example.yaml
uv run python scripts/load_products.py # see examples/products.example.yaml
```

Note: the first FAQ/photo operation downloads the BGE-M3 embedding model (~2.2 GB) into `HF_HOME`.

### The merchant website

`/signup` walks a merchant through email + password signup and one-time email verification, then lands on `/dashboard` — connection status, a next-steps checklist, and quick-start automation cards. `/dashboard/automations` is the full template gallery and install wizard for Instagram comment/story-reply automations; `/dashboard/settings` covers account settings.

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

A merchant who signed up on the website first can instead click "Connect" on the dashboard, which uses a deep link (`t.me/<bot>?start=<webhook_slug>`) to bind the new Telegram bot to their existing web account instead of creating a duplicate.

If `PUBLIC_BASE_URL` changes (e.g. a dev tunnel restarts), re-register every webhook with `uv run python -m scripts.reregister_webhooks`.

Each endpoint rejects requests whose `X-Telegram-Bot-Api-Secret-Token` header doesn't match. For local development, expose the server with a tunnel (e.g. ngrok).

`GET /health` is available for liveness checks.

## Configuration

All settings load from `.env` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `ENV` | `development` / `production` |
| `DATABASE_URL` | Postgres DSN (`postgresql+psycopg://...`) |
| `REDIS_URL` | Redis, used for LLM response caching, budgets, and the Instagram job queue |
| `TELEGRAM_API_BASE` | Override for testing against a fake Telegram API |
| `HF_HOME` | HuggingFace model cache directory |
| `TOKEN_ENCRYPTION_KEY` | Fernet key encrypting tenant bot tokens and Instagram access tokens at rest |
| `PLATFORM_BOT_TOKEN` | The shared platform bot's token (onboarding + admin channel) |
| `PLATFORM_WEBHOOK_SECRET` | Verifies inbound requests to the platform bot's webhook |
| `PUBLIC_BASE_URL` | This server's externally-reachable base URL, used to auto-register tenant webhooks during onboarding and as the base of the Instagram webhook/OAuth URLs |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | Website signup email verification (`app/auth/email.py`) |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` / `INSTAGRAM_VERIFY_TOKEN` / `INSTAGRAM_GRAPH_API_VERSION` | Instagram comment/story-reply automation — see [`app/instagram/README.md`](app/instagram/README.md) |

Per-merchant secrets (bot token, webhook secret, webhook slug, password hash, Instagram token) live in the database, not in environment variables. Bot/Instagram tokens are encrypted at rest (`app/db/crypto.py`); who administers a merchant lives in `merchant_admins`, not a single admin chat id.

## Project layout

```
app/
  telegram/       tenant webhook endpoint, Telegram API client, update schemas
  onboarding/     platform bot webhook + self-serve onboarding state machine
  nlp/            transliteration/normalization, language ID, embeddings
  flows/          merchant-defined keyword rules + installable templates (ManyChat-style presets)
  faq/            embedding-based FAQ retrieval
  intent/         semantic intent classifier + reply router
  image_search/   photo -> catalog matching, carousel UX, ordinal references
  llm/            Claude fallback: provider, context, cache, budget
  handoff/        escalation + merchant admin commands (/reply, ...)
  instagram/      comment/story-reply-to-DM automation: webhook, OAuth, queue + worker (see its README)
  auth/           website signup/login/session auth (email verification, password hashing)
  automations/    merchant-facing dashboard API for installing/managing Instagram automations
  web/            website + dashboard HTML/JS (signup, dashboard, automations, settings)
  db/             SQLAlchemy models, session, at-rest encryption
alembic/          migrations
scripts/          merchant seeding, webhook re-registration, YAML content loaders
examples/         example flow/FAQ/product YAML and product photos
docs/             product/design reference docs
tests/            pytest suite (fixtures include mixed-language examples)
```

## Tests

```bash
uv run pytest
```
