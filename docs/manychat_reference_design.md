# ManyChat: Working Design (reverse-engineered from documentation)

Reference architecture for DukanAI planning. Sources: ManyChat help center,
templates docs, Dev Tools docs. Last checked 2026-07-18.

---

## 1. The core engine: trigger → flow → action

Everything in ManyChat reduces to one shape:

```
TRIGGER                     FLOW (tree of nodes)              ACTIONS
─────────                   ────────────────────              ─────────
IG comment w/ keyword   →   message node                  →   send DM/reply
first DM                    button row (quick replies)        set tag / field
keyword in DM               condition (if-then)               notify human
story reply/mention         delay                             external request
button click                AI step (free-text)               add to sequence
```

- A **trigger** wakes an automation: ~a dozen types per channel. Most used:
  comment-with-keyword under a post/Reel, first DM, keyword from an existing
  contact, story reply/mention, button click inside another flow.
- A **flow** is a visual tree of nodes (Flow Builder). Node types: text,
  image/video, quick-reply buttons, condition, delay, tag/field action,
  integration step, AI step.
- **Keywords** match two ways: exact word/phrase, or "intention" — the
  merchant writes a one-line description of intent and ManyChat AI matches
  free text against it. (This is their equivalent of our intent anchors +
  embedding match.)

## 2. How they handle merchants (the account model)

- **Workspace = one connected social account/page.** A merchant signs up
  with their Facebook account (OAuth to Meta), then connects channels:
  Instagram, Messenger, WhatsApp, Telegram, SMS, email. Each connected
  page/account gets its own bot, contacts, flows, and billing.
- **Contact record is the tenant-scoped data unit.** Every person who
  messages becomes a contact with system fields + tags + custom user fields
  (text limit 4,000 chars via card, ~20,000 via message capture, unbounded
  via API).
- **Two field scopes:** *user fields* (per-contact) and *bot fields*
  (per-workspace globals — e.g. shop address, price list URL). Bot fields
  are the personalization hook that makes templates reusable (see §4).
- **Billing is per-contact-count** on the workspace, free tier ~1,000
  contacts, Pro unlocks channels/features. Merchant identity, channel
  connection, and billing all hang off the workspace — there is no
  cross-workspace merchant object.
- **Isolation:** flows, keywords, contacts, fields never cross workspaces.
  Sharing happens only through the template mechanism, never live data.

## 3. Channel integration steps (merchant onboarding path)

1. Sign up (Facebook OAuth — identity comes from Meta, not email/password).
2. Connect a channel in Settings: IG/Messenger via Meta OAuth + page pick +
   permission grant; Telegram via bot token; WhatsApp via Meta embedded
   signup.
3. Redirected to dashboard → build or install automations.
4. First automation typically from a template or the default welcome flow.

Compare DukanAI: we invert step 1 (email/password signup first, socials
second) — better for Uzbekistan where not every merchant runs ads from a
personal FB account, and it gives us an owned identity not dependent on Meta.

## 4. Templates: their multi-merchant distribution mechanism

This is how one bot design is reused across many merchants without shared
infrastructure:

1. Profile → **My Templates** → **New Template**, pick the source workspace.
2. Select elements: flows, keywords, sequences, welcome messages. Selecting
   an automation auto-includes connected elements (dependency closure).
3. **Bot fields config per field:** pre-fill with current value / require
   installer to fill / rename. This is the parameterization step — the
   template is code, bot fields are its config.
4. Name, avatar, description; optional **Protect Template** (blocks
   re-copying/redistribution).
5. Generate a **permanent link** (many installs) or **single-use link**
   (one client).
6. Installer clicks the link → picks their own workspace → required bot
   fields prompt on install → the flows materialize into their workspace as
   independent copies (no live link back to the author).

Key design decision: **copy, don't reference.** Installed templates fork;
updates to the original don't propagate. Simple, safe, offline-friendly —
at the cost of no centralized upgrades.

## 5. Extensibility surface

- **External Request node** (paid plans): arbitrary HTTP POST/GET/PUT/DELETE
  from inside a flow, 10s hard timeout, response-mapping via JSON path into
  user fields.
- **Public API** (`api.manychat.com`, key in Settings → API): manage
  contacts, set fields, trigger flows from outside.
- **Native integrations:** Google Sheets, Calendly, email providers, Zapier
  etc. — packaged as flow nodes.

## 6. What DukanAI should take from this

| ManyChat concept | DukanAI equivalent | Status / action |
|---|---|---|
| Workspace per channel account | `Merchant` row + `webhook_slug` capability token | Built — ours unifies TG+IG under one merchant, which ManyChat doesn't |
| Trigger → flow → action | webhook → intent/FAQ retrieval → reply | Built (embedding-matched, stronger than exact keywords for uz/ru mixed script) |
| Keywords "intention" matching | intent anchors + BGE-M3 embeddings | Built — our cross-lingual match is the differentiator |
| Bot fields (workspace globals) | `merchant.profile` (hours, tone, payment) | Built via onboarding state machine |
| **Templates with bot-field prompts** | **vertical presets** (`_TOPICS_BY_VERTICAL` etc.) | Partially built — our onboarding *is* a template installer in disguise: vertical pick selects the preset, FAQ-topic answers are the "required bot fields." Formalizing shareable presets per vertical (clothing/cosmetics/course) is the cheap version of ManyChat templates |
| Copy-don't-reference installs | seed defaults into merchant rows at onboarding | Matches our current design — keep it |
| External Request node | not needed for v1 | Skip — our flows are code, not merchant-built |
| Contact-count billing | TBD | Their model proves per-contact pricing is accepted in this market |

Biggest structural difference: ManyChat merchants **build** their own flows;
DukanAI merchants **answer onboarding questions** and get a working bot. For
Uzbek SMB sellers, ours is the right call — ManyChat's flow builder is the #1
complexity complaint in their own ecosystem, which is why templates exist at
all.
