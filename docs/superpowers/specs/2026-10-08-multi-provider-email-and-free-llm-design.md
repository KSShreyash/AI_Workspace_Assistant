# Multi-Provider Email + Free LLM — Design

**Date:** 2026-10-08
**Status:** Design. Awaiting review. No implementation started.
**Baseline commit:** `7fe7f1a`

---

## 0. How to resume this work (read first)

This document is the durable handoff. It is committed to git, so it is readable from
any machine, any Claude Code account, and any fresh session.

**To resume with full conversation history (same machine, any account):**

```bash
cd "C:\Drive D\College\Projects\AI Workspace Assistant"
claude --resume a3fcfa6d-a852-4165-9507-16c4cc85a555
```

The transcript is a local file at
`~/.claude/projects/C--Drive-D-College-Projects-AI-Workspace-Assistant/a3fcfa6d-a852-4165-9507-16c4cc85a555.jsonl`.
Its records carry no account or org identifier, so signing in with a different account
does not invalidate it. Credentials live separately in `~/.claude/settings.json`,
`sessions/`, and `daemon-auth-status.json`.

**To resume on a different machine:** copy that `.jsonl` to the same relative path there.

**What does not survive an account switch:**

- The published audit artifact (artifact updates require ownership). Its content is
  regenerable from this repo — the findings are summarised in §8.
- MCP/plugin auth (the Supabase plugin) must be re-authorised.

**To resume with no history at all:** this document plus §8 is sufficient to execute
the work cold. Start at §9.

---

## 1. Context

A personal relationship manager. It syncs a mailbox, calendar, and Teams chat from
Microsoft Graph, uses an LLM to extract contacts and action items from those messages,
and can book follow-up meetings onto the user's calendar.

- **Frontend:** Next.js 16 static export (`output: "export"`) on Cloudflare Pages
- **Backend:** FastAPI on Render free tier
- **Data:** Supabase Postgres, Supabase Auth (Azure OAuth)
- **Scheduled:** GitHub Actions cron, every 15 min, shared-secret auth
- **LLM today:** OpenAI `gpt-4o-mini` via the `openai` SDK

### Intended outcome

Two audiences, both real:

1. **Daily personal use** — managing mail and planning daily work across the user's
   real mailboxes, including personal accounts.
2. **Interview project** — every design decision must be defensible out loud.

The second audience is a real constraint, not a nice-to-have: it rules out hacks that
work but cannot be explained, and it rewards designs where a limitation drove a
deliberate engineering choice.

### Current blocking state

- The Supabase project referenced in `backend/.env` **no longer exists**
  (`ENOTFOUND tenant/user postgres.fmzbuhyguxgxodymmojm not found`). A new Supabase
  project must be provisioned before any backend work can be verified.
- **132 of 173 backend tests error** as a direct result — they open a real connection
  to that database in the `pool` fixture.
- `render.yaml` cannot boot the service it describes (§8.1).

---

## 2. Decisions already made

| # | Decision | Rationale |
|---|---|---|
| D1 | **Several mailboxes connected per user, simultaneously** | User chose this over one-provider-per-user. Work Outlook + personal Gmail + personal Outlook all feed one unified contact and task list. This is the decision that forces the schema change in §6. |
| D2 | **LLM provider: Groq** (OpenAI-compatible endpoint) | User chose approach B. Verified: Groq's `response_format` shape is byte-identical to the existing `_SCHEMA` dict, so the client swap is `base_url` + `api_key` + `model`. |
| D3 | **Keep the `openai` SDK; do not adopt a vendor SDK** | The existing `openai_client.extract()` is already the right seam. Repointing `base_url` keeps the blast radius at one file. |
| D4 | **Provider/model/base_url come from env, not constants** | Costs ~10 lines. Free-tier quotas are not a contract (§4), so "the quota changed" must be a config change, not a code change. Assumed, not explicitly requested — flagged in §3. |
| D5 | **Three separate spec → plan → implement cycles** | The jobs interact but are independently shippable. Sequencing in §9. |

---

## 3. Open questions

### Q1 — Is Google Calendar in scope? (blocks job 2 only)

**Unanswered.** This document assumes **out of scope**: email only. Calendar and Teams
chat stay Microsoft-only.

Why it matters: the user's stated purpose includes "daily work planning," and the
scheduler's entire job is booking follow-ups onto a calendar. If the user's actual
calendar is Google, then reading Gmail while booking to Outlook is half a feature.

- **If out of scope (assumed):** job 2 is mail-only multi-provider. Google OAuth needs
  only `gmail.readonly`.
- **If in scope:** add Google Calendar read + write, a second scheduling backend behind
  a provider-agnostic interface, and `calendar_events` gains the same account FK as
  `emails`. Roughly +60% on job 2.

**Resolve before starting job 2. Job 1 is unaffected.**

### Q2 — Confirm D4 (env-driven provider seam)

Included on the reasoning in D4. Say so if it should be dropped in favour of hardcoding
Groq.

---

## 4. The binding constraint (this reframes job 1)

Groq free tier, for the three models that support `strict: true` structured output
(`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`):

| Limit | Value |
|---|---|
| Requests / minute | 30 |
| Requests / day | 1,000 |
| Tokens / minute | 8,000 |
| **Tokens / day** | **200,000** |

Limits are **organisation-wide**, not per-user. Exceeding them returns **429** with
`retry-after` and `x-ratelimit-*` headers. Cached tokens do not count toward limits.

### The arithmetic that matters

Current cost per email, as the code stands today:

```
instruction preamble            ~250 tokens
participant lines                ~50 tokens
email body                    UNBOUNDED   <-- no truncation anywhere
output (notes + action items) ~200 tokens
                              -----------
                              ~1,400 tokens at an 800-token average body
```

```
200,000 TPD / 1,400 tokens  =  ~140 emails per day
```

**Tokens/day binds, not requests/day.** The 1,000 RPD cap is never reached; the app
starves at ~140 emails while 86% of its request quota sits unused. Separately,
8,000 TPM throttles sustained throughput to ~5–6 emails/minute despite 30 RPM.

Against the 30-day backfill in D1 — 3 mailboxes at 20–50 messages/day each, so
1,800–4,500 messages — the initial sync takes **13 to 32 days**.

### Consequence

Volume reduction is a **functional requirement**, not an optimisation. Three levers,
with estimated effect:

| Lever | Mechanism | Effect |
|---|---|---|
| Truncate bodies | Cap at ~1,500 chars (~400 tokens) | 1,400 → ~900 tokens/email |
| Pre-filter bulk mail | Skip `List-Unsubscribe`, `Precedence: bulk`, `noreply@`-class senders before the LLM | Removes 60–80% of inbox volume |
| Batch per call | N emails share one preamble | Amortises the 250-token preamble; eases RPM/TPM pressure |

Combined, the 4,500-message backfill becomes roughly 1,100 messages at ~900 tokens
≈ 990K tokens ≈ **~5 days of backfill**, and steady-state daily volume
(3 × ~30 msgs, filtered to ~25) sits comfortably inside budget.

This is also the strongest interview story in the project: a hard external constraint
driving a measured architectural response.

---

## 5. Job 1 — Free LLM + extraction hardening

Smallest job, must go first: it sets the token budget job 2 designs within, and the
existing retry behaviour actively destroys a quota'd budget.

### 5.1 Provider swap

`backend/app/services/openai_client.py`:

```python
# before
MODEL = "gpt-4o-mini"
def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)

# after — module-level client (fixes the per-call connection-pool leak), env-driven
_client = AsyncOpenAI(
    api_key=settings.llm_api_key,
    base_url=settings.llm_base_url,     # https://api.groq.com/openai/v1
)
```

`backend/app/core/config.py` gains:

```python
llm_api_key: str
llm_base_url: str = "https://api.groq.com/openai/v1"
llm_model: str = "openai/gpt-oss-20b"
llm_max_body_chars: int = 1500
llm_batch_size: int = 5
extraction_daily_token_budget: int = 180_000   # 200K with headroom
```

`openai_api_key` is removed. `_SCHEMA` is unchanged — already strict-compatible
(every property in `required`, `additionalProperties: false` at every level).

**Model choice:** `openai/gpt-oss-20b`. All three strict-mode models share identical
limits, so pick the cheapest per token. Escalate to `gpt-oss-120b` via env if
extraction quality proves insufficient.

### 5.2 Kill the poison-pill loop

**The highest-priority fix in the entire codebase**, and it becomes severe rather than
merely wasteful under a quota.

Today, all three extraction loops (`ai_extraction.py:133`, `:182`, `:206`) do
`except Exception: continue` **without stamping `extracted_at`**. The item stays
`extracted_at is null` forever and is re-fetched on every sync. Because the query is
`order by received_at asc ... limit 50`, permanent failures sit at the head of the
queue: **~50 bad items starve the pipeline completely and consume the whole daily
token budget on messages that can never succeed.**

Required changes:

1. Add `extraction_attempts int not null default 0` and
   `extraction_error text` to `emails`, `calendar_events`, `chat_messages`.
2. On failure: increment `extraction_attempts`, record the error, **log it**.
3. Pending query becomes `where extracted_at is null and extraction_attempts < 3`.
4. After 3 attempts the item drains out of the queue permanently.
5. Replace every bare `except Exception: continue` with a logged handler.

### 5.3 Validate LLM output at the boundary

`_process_item` indexes LLM output directly — `person["ref"]`, `person["notes"]`,
`item["text"]`, `item["direction"]` (`ai_extraction.py:83–103`). A malformed response
raises `KeyError` inside the transaction, which the outer handler swallows, which
creates a poison pill.

Add Pydantic models mirroring `_SCHEMA` and parse before use. The project already uses
Pydantic throughout, so this follows existing convention:

```python
class ExtractedPerson(BaseModel):
    ref: str
    notes: str

class ExtractedActionItem(BaseModel):
    text: str
    direction: Literal["mine", "theirs"]
    due_date: str | None
    participant_ref: str | None

class ExtractionResult(BaseModel):
    people: list[ExtractedPerson]
    action_items: list[ExtractedActionItem]
```

`direction` as a `Literal` also closes the path where an invalid value trips the DB
check constraint and aborts the transaction.

Also handle `response.choices[0].message.content is None` (refusal) explicitly —
`json.loads(None)` currently raises `TypeError`.

### 5.4 Respect the quota instead of fighting it

Current behaviour (`openai_client.py:90–94`): catch `RateLimitError`, sleep a fixed
5 seconds, retry once. Under a 200K TPD cap, a daily exhaustion needs a wait measured
in hours — retrying burns requests against a wall.

1. Read `retry-after` and `x-ratelimit-remaining-tokens` from the 429 response.
2. Add a `extraction_quota_state` table (or reuse `sync_state`) recording
   `quota_exhausted_until timestamptz`.
3. `extract_user` returns early while the quota is exhausted — no API calls at all.
4. Track tokens consumed per day against `extraction_daily_token_budget` and stop
   proactively before hitting the hard cap.

### 5.5 Pre-filter before the LLM

A new `backend/app/services/extraction_filter.py`, unit-testable in isolation with no
database or network:

```python
def should_extract(email_row) -> tuple[bool, str | None]:
    """Return (should_extract, skip_reason)."""
```

Skip when any of:

- A `List-Unsubscribe` or `List-Id` header is present (requires storing headers — see
  below)
- `Precedence: bulk | list | junk`
- Sender local-part matches `noreply|no-reply|donotreply|notifications?|mailer|bounce`
- `auto-submitted` header present and not `no`
- Body, after HTML stripping, is under ~40 characters

This needs headers that are not currently persisted. Add
`headers jsonb not null default '{}'` to `emails` and populate from Graph /
Gmail. Record the skip reason in `extraction_error` and stamp `extracted_at` so
skipped mail does not re-queue.

### 5.6 Truncate and batch

- Truncate the stripped body to `llm_max_body_chars` (default 1500), preserving the
  head — action items cluster near the top of a message.
- Batch `llm_batch_size` messages per call. Each gets a stable `msg_id` in the prompt;
  the schema gains a per-message grouping so results map back. One failed batch must
  not poison all N messages — on batch failure, fall back to single-message calls for
  that batch so one bad message is isolated.

### 5.7 Testing (job 1)

All of this is unit-testable without a database:

- `extraction_filter.should_extract` — table-driven over header/sender fixtures
- Truncation — boundary cases, multibyte safety
- Pydantic parsing — valid, missing field, wrong `direction`, `None` content
- Quota tracking — budget arithmetic, exhaustion, reset
- Batch fallback — batch fails → N single calls, one bad message isolated

Groq calls get mocked with `respx`, matching the existing test convention.

---

## 6. Job 2 — Multi-provider email

Blocked on **Q1**. Design assumes email-only.

### 6.1 Why the schema must change

Three primary keys currently encode "one Microsoft mailbox per person":

```sql
ms_graph_tokens:  user_id uuid primary key          -- one token row per user
sync_state:       primary key (user_id, resource_type)  -- no account dimension
emails:           unique (user_id, graph_message_id)    -- no provider, no account
```

### 6.2 New schema

```sql
create table public.connected_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  provider text not null check (provider in ('microsoft', 'google')),
  provider_account_id text not null,          -- Graph user id / Google sub
  email_address text not null,
  display_name text,
  encrypted_access_token text not null,
  encrypted_refresh_token text not null,
  access_token_expires_at timestamptz not null,
  scopes text[] not null default '{}',
  connection_status text not null default 'connected'
    check (connection_status in ('connected', 'needs_reauth', 'disconnected')),
  is_primary boolean not null default false,  -- the calendar/chat account
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, provider, provider_account_id)
);

alter table public.connected_accounts enable row level security;
-- No policies: service_role only, consistent with ms_graph_tokens.

-- one primary account per user
create unique index connected_accounts_one_primary
  on public.connected_accounts (user_id) where is_primary;
```

Migrations:

- `emails`: add `account_id uuid references connected_accounts(id) on delete cascade`;
  drop `unique (user_id, graph_message_id)`, add
  `unique (account_id, provider_message_id)`; rename `graph_message_id` →
  `provider_message_id`; add `headers jsonb` (also needed by §5.5).
- `sync_state`: repoint to `primary key (account_id, resource_type)`.
- `ms_graph_tokens`: migrate rows into `connected_accounts` with
  `provider='microsoft'`, `is_primary=true`, then drop the table.
- Backfill `emails.account_id` from the migrated row before adding `not null`.

`contacts` and `action_items` stay keyed on `user_id` — deliberately. Contacts are
**unified across mailboxes**; that is the entire point of D1.

### 6.3 Auth flow

Verified: `linkIdentity(credentials: SignInWithOAuthCredentials)` exists in
`@supabase/supabase-js` 2.110.6 and accepts the same `scopes` / `queryParams` options as
`signInWithOAuth`. `Session.provider_token` is singular — one slot per session.

The key insight: **our database is the durable multi-account token store; the Supabase
session is a one-shot delivery channel for whichever provider was most recently
authorised.** The existing `/callback` → `POST /api/auth/graph-tokens` flow already has
this shape. It becomes provider-aware rather than being replaced.

```
First sign-in:  signInWithOAuth({ provider: 'azure' | 'google' })
                  → /callback captures provider_token → POST /api/auth/accounts
Add a mailbox:  linkIdentity({ provider: 'google', options: {
                    scopes: 'https://www.googleapis.com/auth/gmail.readonly',
                    queryParams: { access_type: 'offline', prompt: 'consent' }
                }})
                  → same /callback path → POST /api/auth/accounts
```

Two constraints that silently break this if missed:

1. **Google returns a refresh token only with `access_type=offline` AND
   `prompt=consent`.** Without both, the connection dies after ~1 hour with no refresh
   path.
2. **Manual identity linking must be enabled** in Supabase project settings.

Also fix here: `/callback` currently hardcodes `expires_in: 3600`
(`callback/page.tsx:53`). Have the backend derive expiry per provider instead of
trusting a client-supplied constant.

### 6.4 Gmail sync

Gmail's incremental model differs fundamentally from Graph's and needs its own client,
`backend/app/services/gmail_client.py`:

| | Microsoft Graph | Gmail API |
|---|---|---|
| Incremental cursor | `@odata.deltaLink` (opaque URL) | `historyId` (integer) |
| Pagination | `@odata.nextLink` | `pageToken` |
| Backfill | `$filter=receivedDateTime ge ...` | `q=after:YYYY/MM/DD` |
| Body | Returned inline | Separate `messages.get` per message |
| Expired cursor | 410 → full resync | 404 on `history.list` → full resync |

`sync_state.delta_link` is already `text` and nullable, so it stores a `historyId`
as a string without a schema change.

**Cost note:** Gmail requires `messages.list` then `messages.get` per message. Use
`format=metadata` for the filter pass (§5.5) and only fetch `format=full` for messages
that survive the filter. This also reduces Gmail API quota consumption, not just LLM
tokens.

Refactor `graph_sync.sync_user` into a provider-dispatching loop over the user's
`connected_accounts`, with mail sync behind a common interface and calendar/chat
running only for the `is_primary` Microsoft account.

### 6.5 Concurrency

Job 2 multiplies the duplicate-extraction race (§8.4). Fix it here at the latest: the
15-minute cron, the sign-in background task, and the manual "Sync now" button can all
overlap, and extraction has no locking. Use a Postgres advisory lock keyed on
`user_id` so only one sync per user runs at a time.

### 6.6 Testing (job 2)

- Migration round-trip: seed an `ms_graph_tokens` row, migrate, assert the
  `connected_accounts` row and the `emails.account_id` backfill
- Cross-account isolation: account A's mail never appears under account B, and neither
  leaks across users
- Gmail client against `respx` fixtures: first sync, incremental, 404 → full resync
- Unified contacts: the same person emailing two connected mailboxes resolves to one
  `contacts` row

---

## 7. Job 3 — Remaining audit fixes

Independent of jobs 1 and 2; land whenever. Detail in §8.

---

## 8. Audit findings (reference)

Full audit run against `f888dec`, re-verified at `7fe7f1a`. Measured:
frontend **108/108 pass**, `tsc` clean, `eslint` clean, backend **132 of 173 error**,
boot from `render.yaml` **fails**, indexes on `action_items` **zero**.

### 8.1 Blocking

1. **`render.yaml` cannot boot the service.** It declares 8 env vars;
   `config.py:15-16` requires `sync_secret` and `openai_api_key` with no defaults, and
   pydantic validates at import. Reproduced:
   `ValidationError: 2 validation errors for Settings`. Job 1 changes these var names —
   update the blueprint in the same commit.
2. **Supabase project gone** → 132 tests error. Provision a new project; consider a
   local Postgres for tests so the suite stops depending on remote infrastructure.

### 8.2 Data loss

3. **Contact notes overwritten unconditionally.** `contacts.py:44-46` —
   `notes = excluded.notes` with no `coalesce`, while the adjacent `display_name` line
   does coalesce. Correctness relies on the LLM echoing prior text back verbatim; an
   empty return erases history with no audit trail. **One SQL keyword.**
4. **Poison-pill starvation** — see §5.2.

### 8.3 Correctness

5. **Scheduling race reports the wrong error.** `create_meeting` returns `None` for
   three unrelated causes (no token, failed refresh, lost race —
   `scheduling.py:74, 97, 127`); the endpoint maps all three to
   `"Microsoft account needs to be reconnected"`. Return distinct sentinels or typed
   exceptions.
6. **Planner buckets by UTC date.** `planner/page.tsx:38-43` builds "today" from
   `Date.UTC(...)`, so "Today" is wrong for any non-UTC user. The date badge on the
   same row has the opposite bias (`new Date(due + 'T00:00:00Z')` rendered via
   `toLocaleDateString`), so bucket and badge can contradict each other. Compare local
   calendar dates on both sides.
7. **"Next week" is a catch-all.** `planner/page.tsx:52-53` — anything beyond 7 days
   lands there, including items months out.
8. **Recent searches record prefixes.** `search/page.tsx:116` records on every
   debounced response, so typing one query fills all five slots with its own fragments.
   Record on submit or blur.

### 8.4 Robustness

9. **Four mutation handlers lack try/catch**, so one network blip wedges the UI until
   reload: `dashboard/page.tsx:108-119` (`runTrigger` never clears `pending` — Sync and
   Extract stay disabled), `ScheduleActionItemPanel.tsx:59-75` (`confirm` never clears
   `submitting`), `:43-53` (`openPanel`), `planner/page.tsx:92-105` (`toggleDone`
   throws instead of setting the `toggleError` state that exists for it). Most likely
   trigger: `POST /api/sync/run/me` runs a full sync plus up to 50 LLM calls inline on
   Render free tier. Note `auth.py:19,35` already backgrounds this work — the
   user-facing trigger is the one that does not.
10. **Six silent exception handlers.** `sync.py:35-36`, `graph_sync.py:210-211`,
    `ai_extraction.py:133/182/206`, and `curl -sf` in `graph-sync-cron.yml:16`.
    `logging` is configured in `main.py:9` and used correctly in `me.py:54-59`, then
    unused exactly where it is needed. **Fix these first** — they are what makes every
    other bug invisible.
11. **Error and spinner render together.** `ScheduleActionItemPanel.tsx:48-51` leaves
    `slots === null` on failure, which also means "loading", so the panel shows the
    error above a permanent "Loading suggestions…".
12. **Pool-creation race.** `db/session.py:8-12` is an unguarded lazy singleton;
    concurrent cold-start requests each create a pool and orphan all but the last.
    Guard with `asyncio.Lock`. `close_pool` has no caller in `app/` — there is no
    `lifespan` hook.
13. **Duplicate action items.** No locking anywhere (`for update`, `skip locked`,
    advisory locks, `asyncio.Lock` — all absent). See §6.5.
14. **External-call edges.** `float(response.headers.get("Retry-After", "1"))`
    (`graph_client.py:60`) raises on the HTTP-date form the spec permits;
    `list_chats` paginates unbounded; the `while True` delta loops
    (`graph_sync.py:52, 102`) call `fetch_delta_page(token, None)` if a response carries
    neither link; `get_valid_access_token` has no expiry skew buffer, and `me.py:60-62`
    duplicates the refresh logic and flips to `needs_reauth` on a 401 **without trying a
    refresh**.

### 8.5 Performance

15. **`action_items` has no index but its primary key.** Read by planner, dashboard,
    search, and every contact profile — all sequential scans. `contacts` has only two
    *partial* unique indexes (`where email_address is not null` / `is null`), which
    Postgres cannot use for the `user_id`-only filters in `list_for_user`, `count`,
    `list_recent`, `search`. Worst case is `contacts.py:95-98`: a correlated subquery
    computing open-item counts, which seq-scans `action_items` **once per contact row**
    — O(contacts × action_items). Replace with a `group by` join and add:

    ```sql
    create index on public.action_items (user_id, status);
    create index on public.action_items (contact_id) where contact_id is not null;
    create index on public.action_items (scheduled_calendar_event_id)
      where scheduled_calendar_event_id is not null;
    create index on public.contacts (user_id);
    -- plus GIN indexes for the two full-text search expressions
    ```

16. **Navigation is a full page reload.** `NavBar.tsx:88` uses `<a href>`;
    `next/link` is imported nowhere. Consequences: `PageTransition`'s `AnimatePresence`
    can never run an exit animation, the `layoutId` shared-element animations
    (`NavBar.tsx:98,105`) can never animate, and the NavBar's
    `/api/me/graph-status` effect refires on every navigation — an endpoint that makes
    a live Graph call plus two DB writes.
17. **No pagination on any endpoint.** The dashboard fetches
    `/api/action-items?include_done=true` — every task ever — to count them client-side
    (`dashboard/page.tsx:85-96`), while `/api/dashboard` on the same page already
    returns `open_action_item_count` computed in SQL.

### 8.6 Security

Fundamentals are sound: Fernet-encrypted tokens at rest, JWT verified against JWKS with
`ES256` pinned, `hmac.compare_digest` for the sync secret, `user_id` on all 31
repository queries, parameterised SQL throughout, no secrets in git, RLS default-deny on
service-role tables.

18. **Prompt injection.** `openai_client.py:62-79` interpolates raw message bodies into
    the prompt with no delimiter and no instruction to disregard embedded directives,
    and the output drives DB writes. Anyone who can email the user can attempt to steer
    their CRM — and because notes are overwritten rather than merged (§8.2), a
    successful injection can also erase history. Fence content in explicit delimiters,
    declare it untrusted, and treat extracted notes as suggestions. **Carry this into
    job 1's prompt rewrite.**
19. **CORS origins not trimmed** (`main.py:15`) — `"a, b"` yields `" b"`, which never
    matches. **`.env.example` leaks the real project ref** — use a placeholder as
    `frontend/.env.local.example` already does.

### 8.7 Polish

20. **Two hydration mismatches.** `NavBar.tsx:25-28` and `search/page.tsx:77` read
    `localStorage` in a `useState` initialiser under `output: "export"`, so the first
    client render disagrees with prerendered HTML. Move to `useEffect`.
21. **Accessibility.** No `prefers-reduced-motion` handling anywhere despite Framer
    Motion on the sidebar, page transitions, every button, and the schedule popover. No
    `color-scheme: dark`, so native `<select>` and scrollbars render light against the
    dark palette. The planner's direction `<select>` (`planner/page.tsx:198`) has no
    accessible label. (Design tokens are clean — all 13 `var(--color-*)` used are
    defined, none orphaned.)
22. **Hygiene.** Five unused scaffold SVGs in `frontend/public/`; two untracked strays
    in the repo root (`favicon.ico`, a 1.4 MB PNG); `requirements.txt` pins everything
    with `>=` and has no lockfile while mixing test deps into the production install;
    `backend/.venv` has absolute paths from a previous location and should be recreated;
    `set_scheduled_calendar_event_id` (`action_items.py:104-108`) does not bump
    `updated_at` unlike `update_status`.

---

## 9. Sequencing

```
Job 0  Provision a new Supabase project; apply all 7 migrations; restore backend/.env
       (nothing is verifiable until this is done)
   |
Job 1  Free LLM + extraction hardening          <-- start here, §5
       Groq swap, poison-pill fix, Pydantic validation, quota handling,
       pre-filter, truncation, batching, prompt-injection fencing (§8.6/18),
       notes coalesce (§8.2/3), the six logging fixes (§8.4/10),
       render.yaml env vars (§8.1/1)
   |
Job 3a Quick independent wins (can run in parallel with job 1)
       indexes + subquery rewrite (§8.5/15), four try/catch handlers (§8.4/9),
       next/link (§8.5/16), timezone bucketing (§8.3/6)
   |
Q1     RESOLVE: is Google Calendar in scope?
   |
Job 2  Multi-provider email                      §6
       connected_accounts migration, linkIdentity flow, gmail_client,
       provider-dispatching sync, advisory locking (§6.5)
   |
Job 3b Remaining polish                          §8.7
```

Job 1 before job 2 is not arbitrary: job 1 establishes the token budget that job 2's
volume must fit inside, and shipping job 2 first would multiply mail volume into a
pipeline that still burns its entire quota retrying items that can never succeed.

---

## 10. Next step

Per the brainstorming workflow: this spec needs review, then the implementation plan is
produced via the `writing-plans` skill — task-by-task, TDD-ordered, one plan per job
starting with job 1.

**Review checklist:**

- §3 Q1 — is Google Calendar in or out of scope?
- §3 Q2 — keep the env-driven provider seam (D4)?
- §4 — is a ~5-day backfill acceptable, or should the filter be more aggressive?
- §5.2 — is 3 attempts before dropping an item right?
- §9 — does the sequencing match your priorities?
