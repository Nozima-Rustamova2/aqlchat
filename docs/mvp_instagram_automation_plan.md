# MVP plan: merchant-facing Instagram automation (pre-NLP)

Decisions locked 2026-07-18: setup lives in the **web dashboard**; templates
are **fill-fields-only** (merchant fills link + picks posts; keywords and
reply copy come from the preset); this doc is the plan, build follows.

## 0. Where we stand

The hard backend is done (`app/instagram/`, `app/flows/`): signed webhook
ingress, comment_events audit trail, Redis worker, two-tier keyword matching
(substring DM / exact-match public reply), per-post `media_ids` scoping,
180/hr budget with defer-not-drop, OAuth connect keyed on `webhook_slug`,
token refresh cron. **The only way to create a rule today is
`scripts/load_flows` with hand-written YAML — no merchant can do that.**
This plan is the missing merchant layer, copying ManyChat's template UX.

## 1. Merchant journey (target, mapped to ManyChat)

| # | Step | ManyChat equivalent | DukanAI screen |
|---|---|---|---|
| 1 | Sign up web, verify email | FB OAuth signup | built |
| 2 | Connect Instagram (OAuth) | connect channel | built |
| 3 | Open **Avtomatlashtirish** page | Templates gallery | new |
| 4 | Pick a template (per vertical) | install template link | new |
| 5 | Fill required fields (link; toggle public reply) | bot-field prompts on install | new |
| 6 | Connect posts: pick reels/posts from a thumbnail grid, or "all posts" | selecting a post in a comment-trigger | new |
| 7 | Activate → live | publish flow | new |
| 8 | See "X comments → Y DMs this week" | ManyChat analytics | new (minimal) |

Design principle carried over from ManyChat: **copy, don't reference** —
installing a template materializes ordinary `flows` rows owned by the
merchant; editing the preset later never touches installed merchants.

## 2. Build items

### A. Template presets (data, no schema change)

`app/flows/templates.py` — a dict of presets, keyed `template_key`, filtered
by `merchant.vertical` (same pattern as `_TOPICS_BY_VERTICAL`):

```python
TEMPLATES = {
  "price_to_dm": {          # all verticals
    "name": {"uz": "Narx so'raganlarga DM", "ru": "DM спросившим цену"},
    "description": {"uz": "...", "ru": "..."},
    "keywords": ["narx", "narxi", "цена", "сколько", "price"],
    "private_reply": {"uz": "Assalomu alaykum! Narxlar va buyurtma: {link} 😊",
                       "ru": "Здравствуйте! Цены и заказ: {link} 😊"},
    "public_reply":  {"uz": "DM'ga yubordik! 📩", "ru": "Отправили в DM! 📩"},
    "fields": [{"key": "link", "required": True,
                "label": {"uz": "Havola (Telegram bot / katalog)", "ru": "Ссылка"}}],
  },
  "link_in_comments": {...},   # "link"/"ссылка" → DM the link
  "giveaway_keyword": {...},   # merchant-chosen keyword (the one editable-keyword exception)
  "course_enroll": {...},      # course vertical: "yozilish"/"запись"
}
```

Copy drafted in both languages up front; reply texts are NOT merchant-editable
in MVP (fewer broken `{link}` placeholders, no moderation surface).

### B. Flow CRUD API — `app/automations/router.py`

Auth: the existing session cookie / `get_current_merchant` dependency (real
auth — do NOT reuse the `webhook_slug` capability pattern here; these are
authenticated dashboard calls like `/auth/me`).

```
GET    /automations/templates          → presets filtered by merchant.vertical,
                                          localized to ?lang=
GET    /automations                    → merchant's installed IG rules + stats
POST   /automations                    {template_key, fields:{link}, media_ids:[],
                                          public_reply_enabled: bool}
                                        → validates link (http(s), <=512 chars),
                                          materializes a flows row
PATCH  /automations/{id}               {is_active | media_ids | fields}
DELETE /automations/{id}
GET    /instagram/media                → post picker feed (see C)
```

`POST` composes `response_config` from preset + fields server-side; the
client never submits reply text. One installed instance per
(merchant, template_key) in MVP — re-install = edit.

### C. Media picker endpoint (the "connect post" step)

`GET /instagram/media` → Graph API `GET /me/media?fields=id,caption,
media_type,thumbnail_url,media_url,permalink,timestamp` with the merchant's
stored token, first ~25 items + cursor passthrough. Cache 5 min in Redis
(`ig_media:{merchant_id}`) — the picker must not burn the 180/hr reply
budget (media reads count against app-level quota, still: don't hammer).
Each Graph failure returns a typed error so the UI can show "reconnect
Instagram" when the token is dead.

Requires no new permission: `instagram_business_basic` already covers
`/me/media` and is already in the App Review scope list.

### D. Schema: two columns on `flows` (one migration)

- `is_active: bool, server_default=true` — the on/off toggle; `match_flow`
  gains a one-line filter.
- `template_key: str | None` — provenance, drives "installed" badges and
  upgrade tooling later.

Keep `scripts/load_flows` working (tests + power users); it writes the same
rows with `template_key=None`.

### E. Dashboard UI — `dashboard_automations.html`

Three views on one page (same vanilla-JS pattern as existing templates):

1. **Gallery** — template cards (name, description, keyword chips, per-card
   "O'rnatish"). If IG not connected: single CTA to the existing connect
   flow instead.
2. **Install wizard (modal, 2 steps)** — step 1: link field (prefill from
   `merchant.profile` if a bot username exists → `t.me/<bot>`), public-reply
   toggle (default on). Step 2: post scope — "Barcha postlar" (default) or
   thumbnail multi-select grid from `/instagram/media`. Confirm → POST.
3. **Installed list** — per rule: name, keyword chips, scope ("hamma
   postlar" / "3 ta post"), active toggle, 7-day counters, delete.

Add an "Avtomatlashtirish" item to the sidebar + a third checklist row on the
main dashboard ("Birinchi avtomatlashtirishni o'rnating") so the happy path
funnels into it.

### F. Minimal stats (no new tables)

`comment_events` already records everything. Per-flow 7-day counts
(`replied` / total) aggregated in the `GET /automations` response; one
"Shu hafta: N ta izoh → M ta DM" line on the dashboard home. No charts in MVP.

### G. Operational track (start immediately, runs in parallel)

1. **Business Verification** in Meta Business Suite — days-to-weeks, blocks
   App Review, zero code. Start now.
2. **App Review screencast** — record the full flow on the test account once
   E ships: signup → connect IG → install template → comment `narx` from a
   second account → DM arrives. Request Advanced Access for
   `instagram_business_basic`, `instagram_business_manage_comments`,
   `instagram_business_manage_messages`.
3. Until approval, onboard pilot merchants by adding their IG accounts as
   app **testers** (Standard Access works fully for testers) — realistic
   pilot cohort: 5–10 shops.
4. Resend domain verification — signup emails for non-test users (still
   pending, unrelated but on the same critical path to pilots).

## 3. Sequencing

| Order | Item | Depends on | Size |
|---|---|---|---|
| 1 | D migration + `is_active` in `match_flow` | — | S |
| 2 | A presets + unit tests | — | S |
| 3 | B CRUD API + tests | 1, 2 | M |
| 4 | C media endpoint + Redis cache + tests | — | S/M |
| 5 | E dashboard page + wizard | 3, 4 | M/L |
| 6 | F stats aggregation | 3 | S |
| 7 | G2 screencast + App Review submit | 5 | ops |

Items 1–4 are backend-only and independently testable; 5 is the biggest
piece; the pilot can start on testers the day 5 lands.

## 4. Risks / decisions taken

- **App Review latency** is the schedule risk, not code — hence tester-based
  pilot (G3) decouples launch from Meta.
- **Fixed reply copy** may feel rigid; mitigation is more templates, not
  free-text editing (revisit post-NLP when tone-adapted generation lands).
- **Keyword collisions** across two installed templates: `match_flow`
  already resolves by post-scope-then-longest-keyword; the wizard warns if a
  chosen keyword is already used by another active rule.
- The `giveaway_keyword` template lets the merchant type one custom keyword —
  normalize it with the same `normalize()` used at match time or Cyrillic
  keywords will silently never match transliterated text.
- Media picker deliberately read-only; "create post from DukanAI" is
  explicitly out of scope.
