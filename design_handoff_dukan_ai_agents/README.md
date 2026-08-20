# Handoff: Dukan AI — Agents Feature (Avtomatlashtirish → Agentlar)

## Overview
Adds an "Agentlar" (Agents) section to the Dukan AI dashboard, nested under "Avtomatlashtirish" in the left nav. Users create an AI agent that auto-replies to customers, configure it through a persistent tabbed workspace (inspired by MoonAI's agent-config UX, rebuilt with original layout/copy), and connect it to a Telegram bot.

## About the Design Files
The files in this bundle (`Dukan AI.dc.html`, `Agent Workspace Proposal.dc.html`) are **HTML/JS design references** — interactive prototypes showing intended look, layout, and behavior. They are not production code to copy verbatim. The task is to **recreate these designs in the target codebase's existing environment** (React, Vue, etc.) using its established component patterns, state management, and styling system. If no frontend framework is set up yet, choose the most appropriate one for this project.

Both files are self-contained — open them directly in a browser to see the live prototype (click through nav items, tabs, toggles, the creation wizard, and Test chat).

## Fidelity
**High-fidelity.** Colors, spacing, copy (Uzbek), and interaction states below are final — recreate pixel-close using the codebase's real component library/CSS approach rather than inline styles.

## Screens / Views

### 1. Dashboard shell (sidebar + top-level pages)
File: `Dukan AI.dc.html`
- **Layout**: Fixed 260px left sidebar + flexible content area, both full viewport height. Sidebar: `#0e1319` bg, 1px right border `#1c232c`, 20px/16px padding.
- **Sidebar contents top→bottom**: logo row (32px gradient circle avatar `#2dd4bf → #0d9488` with a small orange `#f5a623` status dot, "Dukan AI" wordmark 19px/800), user chip (avatar + name), nav list, language switcher (UZ/RU/EN pills), "Chiqish" (log out) button.
- **Nav items**: Bosh sahifa (home), Suhbatlar (chats, badge "tez kunda" = "coming soon", disabled-looking), Avtomatlashtirish (automation, ⚡ icon), **Agentlar** (indented sub-item under Avtomatlashtirish, ◇ icon, 34px left padding to show nesting), Sozlamalar (settings). Active item: text color `#2dd4bf`, background `#141c22`/`#141a22`, border-radius 10px.

### 2. Avtomatlashtirish page (existing — unchanged, for reference)
- Header "Avtomatlashtirish" (32px/800) + subtitle.
- "O'RNATILGAN" (installed) section: one active-automation card with toggle switch + delete button.
- "SHABLONLAR" (templates) section: 3 template cards in a row, each with title, tag pill, description, and an action button ("Tahrirlash"/"O'rnatish").

### 3. Agentlar — empty state
- Shown when the user has zero agents.
- Centered empty-state block: dashed border container, 64px icon tile, "Hali agentlar yo'q" (no agents yet) heading, helper copy, teal "+ Agent yaratish" (create agent) button.

### 4. Agentlar — 4-step creation wizard (modal)
Triggered by "+ Agent yaratish" or the small "+" next to the Agentlar nav item.
- Modal: 640px wide, dark card `#12171e`, border `#263039`, 22px radius, centered over a blurred scrim.
- Header: title + "✕" close. Below it, a 4-segment progress bar (filled segments = `#2dd4bf`, unfilled = `#1f2731`).
- **Step 1 — Ohang (Tone)**: two selectable cards, "Muloyim" (polite/friendly) and "Rasmiy" (formal), radio-style selection (teal border + filled dot on the selected one).
- **Step 2 — Limit**: per-user daily message cap, stepper control (− / value / +), step of 5, min 1 max 200, default 20. Label "ta / kuniga" (count/day).
- **Step 3 — Ma'lumotlar (Info)**: free-text textarea where the shop owner enters everything the agent should answer from (hours, delivery, pricing, products).
- **Step 4 — Ulanish (Connect)**: Telegram bot token input + short instructions to create a bot via @BotFather. Connection status pill (Ulanmagan/Ulandi = Not linked/Linked).
- Footer: "Orqaga"/"Bekor qilish" (back/cancel) on the left, "Davom etish"/"Yakunlash" (continue/finish) teal button on the right.
- On finishing step 4, the modal closes and the app should show the created agent (list state) with a brief confirmation toast ("Yangi agent yaratildi").

### 5. Agentlar — agent list / card (after creation)
- Page header + "+ Agent yaratish" button (top right) to create additional agents.
- Agent card: icon tile, name, "Ohang: X • Limit: Y ta/kun" meta line, status pill (Faol/Sozlanmoqda = Active/Configuring, colored by `telegramConnected`), "O'chirish" (delete) action.

### 6. Agent workspace — persistent tabbed shell
File: `Agent Workspace Proposal.dc.html`
- Same sidebar as the dashboard shell, plus a workspace pane: breadcrumb ("AGENTLAR › <agent name>"), agent name as page title, and a persistent teal **"💬 Test chat"** button top-right.
- **Tab bar** (wraps to a second line on narrow widths — do not let it horizontally scroll): Sozlamalar, Prompting, Xabarlar, Nazorat, Bilimlar bazasi, Integratsiyalar, Kanallar. Active tab: teal text + 2px teal bottom border.
- **Sozlamalar (Settings) tab**: agent name field, avatar tile, "Agent faol" (active) toggle, working-hours summary row with "O'zgartirish" (change) link.
- **Prompting tab**: tone re-selection (Muloyim/Rasmiy cards, same pattern as wizard step 1), large "Asosiy yo'riqnoma" (core instructions) textarea, "Saqlash" (save) button.
- **Xabarlar (Messages) tab**: "Xabarlarni bo'lib yuborish" (split messages into separate bubbles) toggle, and the per-user daily limit stepper (same control as wizard step 2, editable post-creation).
- **Nazorat (Control) tab**: "Operator yozganda to'xtatish" (auto-pause when a human operator replies) toggle, and a text input for stop-keywords ("To'xtatuvchi kalit so'zlar").
- **Bilimlar bazasi (Knowledge Base) tab**: textarea (same content as wizard step 3, editable), a file-drop zone (PDF/DOCX — visual only in the prototype, needs real upload wiring), "Saqlash" button with a transient "Saqlandi ✓" success label.
- **Integratsiyalar (Integrations) tab**: list of connectable tools (Google Sheets, AmoCRM shown), each with description + "Ulash" (connect) button — visual only, not wired.
- **Kanallar (Channels) tab**: Telegram bot connect — status pill, token input, "Ulash" button. On click with a non-empty token, status flips to "Ulandi" (Linked) and button label becomes "Ulandi ✓".

### 7. Test chat (slide-over panel)
- Triggered by the "💬 Test chat" button from any tab.
- Right-side drawer, 400px wide, full height, dark scrim behind it.
- Header: "Test chat" title + agent name subtitle + close "✕".
- Message list: agent bubbles left-aligned (`#1c232c` bg), user bubbles right-aligned (`#2dd4bf` bg, dark text), 14px/1.5 line-height, 14px border-radius.
- Footer: text input + circular teal send button. Enter key or send button both submit. In the prototype, the "agent" replies with a canned line after ~500ms — replace with a real call to the agent's chat/completions endpoint.

## Interactions & Behavior
- All toggles are two-state, animated via `left`/`right` offset of the knob — implement as standard switch components in the target framework.
- Nav and tabs are pure client-side view switches — no page reload.
- The wizard is a controlled 4-step flow; step state and the in-progress "new agent" draft are separate from the already-created agents list, so closing/cancelling doesn't corrupt existing data.
- Toast confirmation is a 2.2s auto-dismissing message; Knowledge Base's "Saqlandi ✓" auto-reverts after 1.8s.

## State Management
Suggested state shape (adapt to the target framework's conventions):
```
agents: [{ id, name, tone: 'polite'|'formal', dailyLimit: number, knowledgeText: string,
           active: boolean, splitMessages: boolean, operatorPause: boolean,
           telegram: { token: string, connected: boolean } }]
currentAgentId
ui: { activePage, activeTab, wizard: { open, step, draft }, testChat: { open, messages, input } }
```

## Design Tokens
- **Background**: `#0b0f14` (app), `#0e1319` (sidebar), `#12171e` (cards/panels), `#141a22`/`#1c232c` (chips/active nav bg)
- **Borders**: `#1c232c`, `#1f2731`, `#263039`
- **Text**: `#f5f7f9`/`#f2f4f7` (headings), `#e7ebef` (body), `#9aa4b1`/`#8b95a1` (secondary), `#6b7480` (tertiary/meta)
- **Accent (teal)**: `#2dd4bf` (primary actions, active states), gradient `#2dd4bf → #0d9488` (avatar tiles)
- **Accent (orange)**: `#f5a623` (status dot only)
- **Status colors**: warning/pending `#c9862f` text on `#2a2013` bg; success/connected `#2dd4bf` text on `#0f2b26` bg; destructive `#e0645e` text with `#6b2d2b` border
- **Typography**: Inter, weights 400/500/600/700/800. Page titles 32px/800, section titles 19-22px/700, body 13.5-15px, labels/eyebrows 12.5px/700 uppercase with 0.06-0.08em tracking.
- **Radius**: 9-11px (buttons/inputs), 13-16px (cards), 20px (pills/toggles), 50% (avatars/circular buttons)

## Assets
No external images — avatar tiles and icons are CSS shapes / emoji glyphs placeholders (⌂ 💬 ⚡ ◇ ⚙ ✈ ➤ ✕). Replace with the target app's real icon set.

## Backend / API Integration Notes
No backend exists yet in this prototype — everything is local component state. These are **suggested** endpoints/contracts for a developer to design against the team's actual backend/API conventions:

- `POST /agents` — create agent `{ tone, dailyLimit, knowledgeText, telegramToken }` → `{ id, ...agent }`
- `GET /agents` / `GET /agents/:id` — list / fetch agent(s)
- `PATCH /agents/:id` — update any config field (settings, tone, limit, knowledge text, toggles)
- `DELETE /agents/:id`
- `POST /agents/:id/telegram/connect` — validate + store bot token, return connection status (should verify the token against Telegram's `getMe` API before marking connected)
- `POST /agents/:id/test-chat` — send a user message, return the agent's generated reply (this is where the real LLM call happens, replacing the prototype's canned setTimeout response)
- `POST /agents/:id/knowledge` — persist knowledge-base text/files (consider chunking + embeddings if search quality matters beyond a small text blob)

Auth, multi-tenancy (per-organization agent scoping), and rate limiting (the "daily limit per user" setting) need real enforcement server-side — the prototype only simulates the UI.

## Files
- `Dukan AI.dc.html` — dashboard shell, Avtomatlashtirish page, Agentlar empty/list states, creation wizard
- `Agent Workspace Proposal.dc.html` — full tabbed per-agent workspace + Test chat panel (the design to merge into the main shell once approved)
