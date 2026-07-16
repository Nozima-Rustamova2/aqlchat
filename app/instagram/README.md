# Instagram comment-to-DM automation

ManyChat-style comment automation for merchants: a customer comments a keyword
("narx", "цена", "link"…) on the merchant's Instagram post or reel, and the bot

1. sends the customer a **private DM** with the merchant's configured link
   (Telegram bot, catalog, promo deep-link), and
2. optionally leaves a **public reply** under the comment ("DM'ga yubordim! 📩")
   so other viewers see the account responds.

This is a completely separate integration from the Telegram pipeline — it lives
entirely in `app/instagram/` and touches shared code only at three points: the
`flows` table (rules), the `merchants` table (credentials), and
`app/nlp/transliteration.normalize()` (language detection for picking uz/ru
reply text).

It uses the **Instagram API with Instagram Login** — the merchant connects
their Instagram professional account directly; **no Facebook Page is required**
(most Uzbek merchants don't have one).

---

## How a comment becomes a DM

```
customer comments "narxi qancha?" on merchant's reel
        │
        ▼
Meta webhook  POST /instagram/webhook          (router.py)
  · verify X-Hub-Signature-256 over the RAW body (fail-closed:
    blank INSTAGRAM_APP_SECRET rejects everything)
  · route entry[].id → merchants.instagram_user_id   (the tenant boundary)
  · loop guard: comments authored by the merchant's own account
    (our public replies echo back on this webhook) are dropped at ingress
  · dedupe-insert a comment_events row
    (UNIQUE (merchant_id, external_id) — Meta redelivers, we don't)
  · COMMIT, then LPUSH the row id onto Redis list ig:comments
  · return 200 immediately — no Graph API call ever happens here
        │
        ▼
worker   uv run python -m app.instagram.worker   (worker.py → service.py)
  · BRPOP ig:comments (+ drain the ig:deferred sorted set of due retries)
  · skip if event is no longer pending/deferred (safe on redelivery)
  · drop as stale if older than the 7-day private-reply window
  · normalize() the text (script transliteration + language detection)
  · match_flow(channel="instagram_comment", media_id=…)
  · consume 2 calls from the per-merchant hourly rate budget
    (budget exhausted → ZADD to ig:deferred for the top of the next hour)
  · Graph API: send_private_reply(comment_id, dm_text)
  · Graph API: reply_to_comment(comment_id, public_text)
    — only if the rule has a public_reply AND the comment is an
      exact keyword match (see "Matching" below)
        │
        ▼
comment_events.status = "replied", replied_at set
```

## Module map

| File | Responsibility |
|---|---|
| `router.py` | Webhook verify handshake (GET), signed event ingress (POST), and the OAuth connect flow (`/instagram/connect/{slug}`, `/instagram/oauth/callback`) |
| `queue.py` | Redis list `ig:comments` (LPUSH/BRPOP) + sorted set `ig:deferred` (score = retry epoch). Jobs are just `comment_events` row ids; the row is the source of truth |
| `worker.py` | Standalone sync worker process. Fresh DB session per job; the loop never dies with a job |
| `service.py` | `process_comment_event()` — the whole per-comment pipeline above |
| `client.py` | Thin sync Graph API client: `send_private_reply()` (POST `/me/messages` with `recipient.comment_id`) and `reply_to_comment()` (POST `/{comment_id}/replies`) |
| `oauth.py` | Authorize-URL construction, code → short-lived → ~60-day long-lived token exchange, token refresh |
| `budget.py` | Per-merchant hourly Graph-call cap (Redis INCR-before-check, same design as `app/llm/budget.py`) |

Related, outside this package:

- `scripts/refresh_instagram_tokens.py` — daily cron; refreshes tokens expiring
  within 10 days, Telegram-notifies the merchant's admins if refresh fails.
- `examples/instagram_flows.example.yaml` — rule format reference.
- Migration `475cf3b5c868` — all schema below.

## Data model

**`merchants`** gains three nullable columns:

- `instagram_user_id` (BigInteger, UNIQUE) — the connected IG account's id;
  webhook routing key and the tenant boundary on this channel (one IG account
  connects to exactly one merchant).
- `instagram_access_token` — long-lived token, Fernet-encrypted at rest
  (`EncryptedString`, same key as Telegram bot tokens).
- `instagram_token_expires_at` — naive UTC, drives the refresh cron.

**`flows`** gains `channel` (default `"telegram"`). Instagram rules are
ordinary flow rows with `channel="instagram_comment"`; matching is strictly
channel-scoped in both directions, so Telegram keyword rules can never fire on
Instagram comments and vice versa. For this channel, `response_config` holds:

```json
{
  "link": "https://t.me/merchant_bot",
  "private_reply": {"uz": "…{link}…", "ru": "…{link}…"},
  "public_reply":  {"uz": "DM'ga yubordim! 📩", "ru": "…"},   // optional
  "media_ids": ["17900000000000001"]                          // optional
}
```

**`comment_events`** — one row per received comment, the worker's job record
and the audit trail. Status lifecycle:

| Status | Meaning |
|---|---|
| `pending` | Inserted by the webhook, not yet processed |
| `matched` | A rule matched; set just before the Graph calls |
| `replied` | DM sent (public reply too, if applicable); terminal success |
| `no_match` | No rule matched (or empty comment text); silent by design |
| `deferred` | Rate budget exhausted; parked in `ig:deferred` until the next hour |
| `dropped_stale` | Older than the 7-day reply window when processed |
| `failed` | No token / merchant gone / the DM call itself failed |

A failed **public** reply after a successful DM still ends `replied` — the DM
is the deliverable; the public reply downgrades to a logged warning.

## Matching

Keywords are case-insensitive **substrings** of the normalized comment text,
so `narx` also catches "narxi qancha?" and agglutinative forms. Two product
decisions shape what fires:

1. **Two-tier replies.** The private DM fires on any substring match. The
   *visible public reply* fires only when the comment is essentially *just*
   the keyword (punctuation/emoji/whitespace-insensitive equality). A
   complaint that merely contains "narx" ("narxlar juda qimmat ekan!") gets a
   quiet, relevant DM — not a cheery public bot reply under it.
2. **Optional per-post scope.** `media_ids` restricts a rule to specific
   posts/reels; empty or omitted means account-wide. When several rules match,
   a post-scoped rule beats an account-wide one, then longest keyword wins.

## Rate limiting

Meta allows ~200 Graph calls/hour per connected IG account; each matched
comment costs up to 2 (DM + public reply). We cap at **180/hour/merchant**
(`ig_budget:{merchant_id}:{YYYY-MM-DDTHH}` in Redis) and **defer, never
drop**: a capped job goes into `ig:deferred` scored at the top of the next
UTC hour. The 7-day reply window means late is fine, missing is not — a
1,000-comment viral reel drains in ~10 hours.

## Configuration

From the Meta app (see setup below), in `.env`:

| Variable | Purpose |
|---|---|
| `INSTAGRAM_APP_ID` | Instagram app ID — from the app's **Instagram → API setup with Instagram Login** page (NOT the Meta app id in Settings → Basic) |
| `INSTAGRAM_APP_SECRET` | Instagram app secret from the same page. Signs webhook payloads (HMAC-SHA256) and authenticates the OAuth token exchange |
| `INSTAGRAM_VERIFY_TOKEN` | Any random string; pasted into Meta's webhook subscription form and echoed in the GET handshake |
| `INSTAGRAM_GRAPH_API_VERSION` | Pinned Graph API version (`v23.0`); bump deliberately, changelog open |
| `PUBLIC_BASE_URL` | Shared with Telegram. Base of both the webhook callback URL and the OAuth `redirect_uri` — must match the Meta console **exactly** |

Fail-closed by design: with `INSTAGRAM_APP_SECRET` blank, every webhook POST
is rejected — there is no "dev mode" that skips signature verification.

## One-time Meta app setup

1. **Create the app** at [developers.facebook.com](https://developers.facebook.com):
   My Apps → Create App → use case **Other** → type **Business** (mandatory —
   other app types can't add the Instagram API). Then Add Product →
   **Instagram → Instagram API with Instagram Login**.
2. **Copy credentials** from the Instagram product's *API setup with Instagram
   Login* page into `.env` (see table above). Generate a verify token:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. **Webhook subscription** (app dashboard → Instagram → Webhooks):
   - Callback URL: `{PUBLIC_BASE_URL}/instagram/webhook`
   - Verify token: your `INSTAGRAM_VERIFY_TOKEN`
   - Click *Verify and save* — the server must be running and publicly
     reachable; the GET handler answers the handshake.
   - Subscribe to the **`comments`** field.
4. **OAuth redirect URI**: in the Business Login settings on the same page,
   add `{PUBLIC_BASE_URL}/instagram/oauth/callback` — exact match required.
5. **Test account**: the IG account used for testing must be a professional
   (Business/Creator) account — Instagram app → Settings → Account type and
   tools → Switch to professional account — and be added to the Meta app as an
   Instagram tester. This is **Standard Access**: everything works, but only
   for accounts attached to the app.
6. **Business Verification** (needed for Advanced Access later): Meta Business
   Suite → Business Settings → Security Center → Start Verification, with the
   legal entity's registration documents. Name/address must match the
   documents character for character. Takes days to weeks — start early;
   nothing blocks on it until App Review.
7. **App Review / Advanced Access** (to serve accounts that are *not* app
   testers, i.e. real merchants): request Advanced Access for
   `instagram_business_basic`, `instagram_business_manage_comments`,
   `instagram_business_manage_messages`, with a screencast of the working
   end-to-end demo from step 5's test account. Requires completed Business
   Verification.

## Connecting a merchant

The website's "connect Instagram" button links to:

```
GET /instagram/connect/{webhook_slug}
```

The merchant's `webhook_slug` (an unguessable random token, independently
rotatable) doubles as the capability check until the dashboard grows real
auth. The endpoint stores a single-use CSRF `state` in Redis (10-minute TTL)
and redirects to Instagram's authorize page; the callback exchanges the code
for a ~60-day long-lived token, refuses IG accounts already connected to a
different merchant (409), and stores `instagram_user_id` + encrypted token.

**Token lifecycle**: long-lived tokens last ~60 days and are refreshable any
time after 24h of age. Run `scripts/refresh_instagram_tokens.py` daily (cron);
it refreshes tokens expiring within 10 days (~50 chances before expiry) and
notifies the merchant's admins via the platform Telegram bot if refresh fails,
since an expired token silently kills the merchant's comment automation.

## Running it

Two processes, plus the shared infra:

```bash
docker compose up -d                        # Postgres + Redis
uv run uvicorn app.main:app                 # webhook + OAuth endpoints
uv run python -m app.instagram.worker       # comment processor
```

Load rules with the shared flow loader — each YAML file only replaces rules
for the channels it defines, so Instagram and Telegram rule files load
independently for the same merchant:

```bash
uv run python -m scripts.load_flows --merchant-id <id> --file examples/instagram_flows.example.yaml
```

### Live smoke test (dev)

1. Start everything above, plus the tunnel:
   `ngrok http --url=<your-static-domain> 8000` (must serve `PUBLIC_BASE_URL`).
2. Webhook subscription verified in the Meta console (setup step 3).
3. Connect the test IG account via `/instagram/connect/{webhook_slug}`.
4. Load the example rules for that merchant.
5. Comment `narx` on one of the account's posts **from a different IG
   account** — comments by the connected account itself are dropped by the
   loop guard, by design.
6. Watch the worker log: the event should arrive, match, and end `replied`;
   the commenting account receives the DM, and the public reply appears under
   the comment.

If webhook verification fails with a signature error on real deliveries, swap
`INSTAGRAM_APP_SECRET` for the *other* secret shown in the console (Meta shows
two app id/secret pairs; payloads are signed with the Instagram one).

## Tests

```bash
uv run pytest tests/test_instagram_webhook.py tests/test_instagram_service.py \
              tests/test_instagram_oauth.py tests/test_flow_schema.py
```

- `test_instagram_webhook.py` — handshake, signature enforcement (including
  blank-secret fail-closed), dedupe, loop guard, unknown-tenant drop, enqueue
  ordering.
- `test_instagram_service.py` — two-tier matching, staleness, budget deferral,
  DM/public-reply failure semantics, media scoping. Graph client is stubbed.
- `test_instagram_oauth.py` — connect redirect, single-use state,
  already-connected 409. `exchange_code` is stubbed; the real Meta exchange is
  exercised manually on Standard Access before App Review.
- `test_flow_schema.py` + `tests/test_flow_executor.py` — YAML validation and
  channel/scope matching precedence.

No test ever calls Meta.
