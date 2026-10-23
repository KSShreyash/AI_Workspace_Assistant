# Multi-Provider Email + Free LLM — Design

**Date:** 2026-10-08
**Status:** Every job complete except the cloud deploy, which needs accounts
only you can create (§11.4). 381 backend tests and 186 frontend tests pass.
See the progress table in §0 before starting anything.
**Baseline commit:** `7fe7f1a`

---

## 0. How to resume this work (read first)

### Progress as of 2026-10-09

| | Item | State |
|---|---|---|
| Job 0 | Local Postgres via Supabase CLI, migrations, hermetic tests | **done** - 213 backend tests pass with no network |
| Job 0 | `render.yaml` boots | **done** - verified by simulating Render's env |
| Job 0 | Cloud deploy (Supabase / Render / Pages) | **not started** - needs your accounts, see §11.4 |
| Job 1 | §5.1 provider behind config (Groq) | **done** |
| Job 1 | §5.2 bounded retries, no poison-pill starvation | **done** |
| Job 1 | §5.3 Pydantic validation + `action_items.category` | **done** |
| Job 1 | §5.5 pre-filter bulk mail and trivial chat | **done** |
| Job 1 | §5.6 body truncation | **done** - ~3,900 to ~700 tokens on a long thread |
| Job 1 | §5.6 batching several messages per call | **deferred - see §5.6b** |
| Job 1 | §8.2/3 notes `coalesce`, §8.4/10 logging, §8.6/18 prompt fencing | **done** |
| Job 3a | indexes + correlated-subquery rewrite | **done** - 572ms to 4.05ms, loops=1000 to 1 |
| Job 3a | four try/catch handlers, timezone bucketing | **done** |
| Job 1 | §5.4 quota handling | **done** |
| Job 4a | palette swap, WCAG AA verified | **done** |
| Job 4b | nav shell, next/link, Settings page, lint clean | **done** |
| Job 4c | `/api/dashboard/stats` aggregates | **done** |
| Job 4d | stat tiles, activity chart, category donut | **done** - palette validated |
| Job 4e | static hero | **done** - static was chosen over parallax (§10) |
| Job 4f | `/api/emails` + Emails page | **done** - 12 backend, 15 frontend tests |
| Job 4g | Insights + Calendar | **done** - both in scope per §10 |
| Job 2 | §6.1 `connected_accounts` replaces `ms_graph_tokens` | **done** |
| Job 2 | §6.2 `account_id` on synced rows, `graph_*` to `provider_*` | **done** - chain replays clean from empty |
| Job 2 | §6.4 `gmail_client.py` | **done** - 41 tests |
| Job 2 | §6.4 `sync_gmail` + provider dispatch for sync and refresh | **done** - 16 tests |
| Job 2 | §6.5 advisory lock, one sync per user | **done** - 8 tests |
| Job 3 | provider classification reaches the filter | **done** - the §5.5 tier-2 rule was dead code |
| Job 2 | §6.4 Google Calendar + union free/busy | **done** - 25 tests |
| Job 2 | §6.3 `linkIdentity` flow, provider-aware callback | **done** - 23 tests |
| Job 2 | multi-account Settings UI, disconnect, set-primary | **done** - 12 backend, 18 frontend tests |
| Job 2 | Google sign-in, not only Microsoft | **done** |

Local stack: `docker start supabase_db_AI_Workspace_Assistant`, then
`cd backend && .venv/Scripts/python.exe -m pytest -q` - **381 tests pass**.
Frontend: `cd frontend && npx vitest run` - **186 tests pass**.
Only the database container runs; see the note in §11.3 about Auth not starting.

Migrations number 15 (through `20261009114228_revoke_data_api_access.sql`).
`npx supabase db reset --local` replays the whole chain from empty, which is
worth re-running after any migration change - it is the only check that a fresh
deploy will work.

### Data API posture

Nothing here uses the Data API: the backend connects as `postgres` over asyncpg
and supabase-js is used purely for Auth. `20261009114228` therefore revokes every
`anon` / `authenticated` privilege on `public` and sets default privileges so new
tables do not re-acquire them.

Worth knowing why that mattered, because the audit showed no live hole: RLS is on
for every table and eight of them have no policies, which already denied
everything. The risk was that the safety came from an absence rather than a
decision - the grants were in place and `public` is an exposed schema, so adding
one policy to `connected_accounts` (encrypted OAuth tokens for every mailbox)
would have published it, and nothing in that change would have looked dangerous.
`profiles` was the one genuinely reachable table and its policies were already
correct (`to authenticated`, `(select auth.uid()) = id`, UPDATE with both `USING`
and `WITH CHECK`); they are kept.

The same migration drops `ms_graph_tokens`, superseded by `connected_accounts`
back in `20261009000002` (which copies its rows across first, so the drop cannot
lose tokens). It was still shaped exactly like a live token store.

Checked and already correct, so left alone: JWT verification uses the asymmetric
JWKS endpoint rather than the legacy HS256 shared secret, which is what projects
created after 2025-05-01 require. No views, so no `security_invoker` exposure. No
`SECURITY DEFINER` functions. No use of `user_metadata` in any authorization
decision. `supabase db advisors` reports no issues.

### Deliberately not built

Named here so nobody mistakes them for oversights.

- **Multi-calendar within one Google account.** The primary Google calendar is
  synced per account; a user's extra calendars ("Family", "Holidays") are not.
  `sync_state` is keyed `(account_id, resource_type)` and `resource_type` is
  free text, so `calendar:<id>` would extend it without a schema change.
- **Booking onto a Google calendar.** Availability reads every connected
  calendar, but `scheduling.py` still creates events through Graph only, so
  follow-ups land on the Microsoft primary.
- **Metadata-first Gmail fetching.** §6.4 suggested `format=metadata` for the
  filter pass and `format=full` only for survivors. Bodies are fetched for
  everything instead: the filter's body-length rule needs the body, and Gmail's
  quota is nowhere near binding (the LLM token budget is, and the classification
  fix below is what actually addresses that).
- **All-day events are treated as UTC midnight** on both providers, so an
  all-day event can block the wrong slice of a day in a non-UTC timezone.

### Bugs found and fixed while implementing job 2

These were not in the original §8 audit; they surfaced during the port and are
worth knowing about if any of this is revisited.

1. **`scheduling.py` still wrote `graph_event_id`** after the rename - a
   production break, not a test artifact.
2. **`connected_accounts.upsert` demoted the primary mailbox** whenever it was
   called with `is_primary=False`, which is what every token refresh does. The
   user would silently lose calendar and chat. Fixed with
   `is_primary = excluded.is_primary or public.connected_accounts.is_primary`.
3. **Token refresh always called msal.** A Google account therefore failed
   refresh, was marked `needs_reauth`, and reconnecting produced the same Google
   token - an unrecoverable loop. `GoogleRefreshError` also had to be caught
   alongside `GraphRefreshError`, or a revoked Gmail consent became an unhandled
   exception that aborted the whole run.
4. **Calendar and chat ran for any primary account.** A Gmail primary would have
   called Teams with a Google token on every run.
5. **Two deletes silently matched nothing** after being rekeyed from `user_id` to
   `account_id`; the argument types are identical so nothing complained. Both are
   now keyword-only.
6. **The §5.5 tier-2 filter rule was unreachable.** Nothing stored a provider
   classification and `extract_user` never passed one, so every promotional mail
   still cost a full LLM call - on a free tier, the limit that binds first.
7. **The OAuth callback picked the first azure-or-google identity.** Correct on a
   first sign-in, wrong the moment there are two: identity order says nothing
   about which one issued `provider_token`. Linking Gmail to an existing
   Microsoft account would have overwritten the Microsoft mailbox's tokens with
   Google ones and never stored the new mailbox. The provider is now carried on
   the redirect URL by whoever started the flow.
8. **Login offered Microsoft only**, which locked out anyone whose mail lives in
   Gmail - they could not reach the product at all, making the rest of job 2
   unreachable for exactly the users it was built for.
9. **Deleting the primary mailbox left the user with none**, silently disabling
   calendar and chat with nothing in the UI to explain it. Promotion also has to
   prefer Outlook over Gmail, since both features are Graph-only.
10. **Nothing stopped two syncs overlapping** (cron, sign-in task, "Sync now"),
    and extraction has no locking, so both could read the same un-extracted rows
    and spend the token budget on them twice.


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

**To resume with no history at all:** this document is sufficient to execute the work
cold. **Start at §11 (Environments)** — nothing can be built or verified until the stack
runs, and as of 2026-10-08 every deployed piece is down. Then §9 for sequencing, §5.8
for the collisions to avoid, and §8 for the audit findings being fixed.

---

## 1. Context

A personal workspace assistant. It syncs a mailbox, calendar, and Teams chat from
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

### Q1 — Is Google Calendar in scope? RESOLVED: YES

**Decided 2026-10-09: in scope.** Job 2 covers Gmail *and* Google Calendar, not
email alone. Teams chat stays Microsoft-only, because Google has no equivalent.

Consequences, all of which widen job 2 from the original estimate:

- Google OAuth requests `gmail.readonly` **and** `calendar` scopes.
- `calendar_events` gains the same `account_id` foreign key as `emails`.
- Scheduling needs a provider-agnostic interface: creating an event is
  `POST /me/events` on Graph and `events.insert` on Google, with different payload
  shapes and different free/busy queries.
- Free-slot suggestion must union busy blocks across **every** connected calendar,
  or it will offer times the user is already booked in the other one. This is the
  part most likely to be got wrong.

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
llm_messages_per_call: int = 5
extraction_daily_token_budget: int = 180_000   # 200K with headroom
```

**Naming matters here.** `extraction_batch_limit` already exists (= items processed per
sync run, used at `graph_sync.py:209`). The new knob is messages per *LLM call* — a
different concept. Hence `llm_messages_per_call`, not `llm_batch_size`, which would be
confusable with the existing setting. Leave `extraction_batch_limit` alone.

`openai_api_key` is removed — it appears in `config.py:16`, `openai_client.py:53`,
`backend/.env`, `backend/.env.example`, and must be added to `render.yaml`, which
currently omits it entirely (§8.1). `_SCHEMA` is unchanged — already strict-compatible
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
    category: Literal["work", "meeting", "follow_up", "other"]   # for job 4, see §7.5

class ExtractionResult(BaseModel):
    people: list[ExtractedPerson]
    action_items: list[ExtractedActionItem]
```

> **`category` is here deliberately, even though nothing consumes it until job 4.**
> It feeds the Task Categories donut in the redesigned dashboard. It costs ~5 output
> tokens per item, but adding it *later* means a second migration plus re-extracting
> every stored message to backfill — a full day of token quota and several days of wall
> clock at 200K TPD. Decide LLM output shape once, in job 1. See §7.5.
>
> Mirror it in `_SCHEMA` (as an `enum`) and add
> `category text not null default 'other' check (category in ('work','meeting','follow_up','other'))`
> to `action_items` in the same migration as `extraction_attempts`.

`direction` as a `Literal` also closes the path where an invalid value trips the DB
check constraint and aborts the transaction.

Also handle `response.choices[0].message.content is None` (refusal) explicitly —
`json.loads(None)` currently raises `TypeError`.

### 5.4 Respect the quota instead of fighting it

Current behaviour (`openai_client.py:90–94`): catch `RateLimitError`, sleep a fixed
5 seconds, retry once. Under a 200K TPD cap, a daily exhaustion needs a wait measured
in hours — retrying burns requests against a wall.

1. Read `retry-after` and `x-ratelimit-remaining-tokens` from the 429 response.
2. Add a **new** `llm_quota_state` table recording `quota_exhausted_until timestamptz`
   and `tokens_used_today`, keyed by day.

   > **Correction.** An earlier draft suggested reusing `sync_state` for this. That is
   > not possible: `sync_state.resource_type` carries
   > `check (resource_type in ('mail', 'calendar', 'chat'))`
   > (`20260716000000_graph_sync_schema.sql:52`), so inserting a `'quota'` row fails the
   > constraint. Reusing it would require an `alter table ... drop constraint` migration
   > to widen an unrelated table's domain. A dedicated table is cleaner and avoids
   > overloading a column that means "which Graph resource".
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

**Design revised after checking the Graph docs.** An earlier draft made RFC-5322
headers (`List-Unsubscribe`, `Precedence`) the primary signal. That is the wrong
mechanism here: Graph documents `internetMessageHeaders` as *"Requires `$select` to
retrieve"*, and directs you to apply `$select` in a **get message** (single-item)
operation — not a collection or delta query. Depending on it would mean an extra
per-message API call, and it cannot work retroactively on the mail already synced.

Both providers already classify bulk mail themselves, for free, in fields returned by
default. Use those instead, in tiers:

**Tier 1 — provider-agnostic, works on already-synced rows, no schema change**

- Sender local-part matches `noreply|no-reply|donotreply|notifications?|mailer|bounce`
- Subject matches common bulk markers (newsletter, unsubscribe, digest, receipt)
- Body, after HTML stripping, under ~40 characters

**Tier 2 — Microsoft-native, returned by default, zero extra API cost**

- `inferenceClassification == "other"` — Microsoft's own focused/other relevance
  classifier. This is the single strongest signal available and costs nothing.
- `isDraft == true` — never extract from drafts
- `importance == "low"`

**Tier 3 — Gmail-native, free with `format=metadata`**

- `labelIds` containing `CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES`,
  or `CATEGORY_FORUMS` — Gmail's equivalent classifier
- `List-Unsubscribe`, which Gmail *does* return cheaply in the metadata payload

The symmetry is deliberate: each provider's own bulk classifier does the heavy lifting,
and Tier 1 is the shared floor that works even on historical rows.

**Schema impact:** persist the cheap classification fields rather than a whole header
blob — add `inference_classification text` and `provider_labels text[]` to `emails`.
Drop the `headers jsonb` column from the plan; it is no longer load-bearing. (If headers
are ever wanted later, fetch them per-message only for messages that survive the
filter.)

Record the skip reason in `extraction_error` and stamp `extracted_at` so skipped mail
does not re-queue.

**Retroactive application:** for rows already synced, only Tier 1 can run (the
classification fields will be null). That is acceptable — Tier 1 alone removes the
`noreply@` bulk, and a one-off re-sync repopulates the rest. The filter must therefore
treat null classification fields as "unknown, do not skip" rather than assuming bulk.

### 5.6 Truncate and batch

- Truncate the stripped body to `llm_max_body_chars` (default 1500), preserving the
  head — action items cluster near the top of a message.
- Batch `llm_messages_per_call` messages per call. Each gets a stable `msg_id` in the
  prompt; `_SCHEMA` gains a top-level `results` array keyed by `msg_id` so output maps
  back to inputs.

**Transaction boundary — specify this explicitly, it is the subtle part.** Today
`_process_item` wraps one message's contacts, action items, and `extracted_at` stamp in
a single transaction (`ai_extraction.py:79-112`). Batching must **not** widen that to
cover N messages: one bad row would roll back four good ones and leave all five
unstamped — recreating the poison-pill starvation that §5.2 exists to fix.

The required shape is:

```
1. select a batch of N pending rows
2. filter (§5.5), truncate (§5.6)
3. ONE LLM call for the surviving messages
4. parse + validate the whole response (§5.3)
5. for each msg_id in the response:
       open its OWN transaction -> write contacts + action items + stamp extracted_at
```

So: one network call per batch, one transaction per message. A malformed entry for one
`msg_id` increments only that row's `extraction_attempts`.

On a whole-call failure (429, timeout, unparseable response), fall back to
single-message calls for that batch so one pathological message cannot block the other
four — then the normal attempt-counter path retires it.

### 5.6b Batching: deferred, with the numbers

The spec committed to batching when a message cost ~3,900 tokens. The filter
(§5.5) and truncation (§5.6) have since changed the arithmetic:

```
per message now   ~971 tokens      ~185 messages/day
with the filter   covers ~616 raw messages/day
real need         3 mailboxes x 20-50/day = 60-150 raw messages/day
batching 5/call   31% saving -> ~272/day
```

That is already **4-10x headroom** over real need. Batching would buy capacity
that is not short, in exchange for: a per-message transaction boundary inside a
batched call (§5.6), a fallback path when one batch fails, a schema change to map
results back to inputs, and the quality risk of putting five unrelated messages in
one prompt and asking for per-message attribution.

Deferred on YAGNI grounds. Revisit if real usage shows the daily budget actually
binding - the quota table (§5.4) now records exactly that, so there will be
evidence rather than a guess.

### 5.7 Testing (job 1)

All of this is unit-testable without a database:

- `extraction_filter.should_extract` — table-driven over header/sender fixtures
- Truncation — boundary cases, multibyte safety
- Pydantic parsing — valid, missing field, wrong `direction`, `None` content
- Quota tracking — budget arithmetic, exhaustion, reset
- Batch fallback — batch fails → N single calls, one bad message isolated

**Mocking convention:** note that `tests/test_openai_client.py` uses
`unittest.mock.patch`, not `respx` — `respx` is used by the *Graph* client tests. Follow
the local convention of whichever module is under test rather than mixing them.

---

### 5.8 Pre-flight: existing code these changes break

Verified against the tree at `7fe7f1a`. Each item is a concrete collision that will
surface as a confusing failure if not handled deliberately. Read this before writing
code.

### Job 1 collisions

| # | Collision | Fix |
|---|---|---|
| P1 | `tests/test_openai_client.py:29` and `:50` do `patch("app.services.openai_client._client", return_value=mock_client)`. §5.1 turns `_client` from a **factory function** into a **module-level instance** — `return_value=` only makes sense for a callable, so **both existing tests break immediately.** | Change to `patch.object(openai_client, "_client", mock_client)` (i.e. `new=`, not `return_value=`). Do this in the same commit as the client change. |
| P2 | `tests/test_openai_client.py:38` asserts `kwargs["model"] == "gpt-4o-mini"`. | Assert against `settings.llm_model` so the test follows config instead of pinning a vendor. |
| P3 | `sync_state.resource_type` CHECK constraint blocks storing quota state there. | New `llm_quota_state` table — see the correction in §5.4. |
| P4 | `extraction_batch_limit` (items per run) vs the new messages-per-LLM-call knob are easily conflated. | Name the new one `llm_messages_per_call`; leave the existing setting untouched. See §5.1. |
| P5 | `render.yaml` omits `OPENAI_API_KEY` **and** `SYNC_SECRET` today (§8.1), so it already cannot boot. Job 1 renames the LLM var on top of that. | Update the blueprint to `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, **and** add the missing `SYNC_SECRET`, in the same commit. |
| P6 | Graph `internetMessageHeaders` requires `$select` and is documented for single-message GET. | Pre-filter uses provider-native classification instead — see the revised §5.5. |
| P7 | Already-synced rows have no classification fields, so the filter cannot fully apply retroactively. | Tier 1 works on existing rows; null classification means "unknown, do not skip". See §5.5. |
| P8 | Widening the transaction to cover a whole batch would re-create poison-pill starvation. | One LLM call per batch, one transaction per message. See §5.6. |
| P9 | **No existing test covers notes-merge semantics** — `tests/test_contacts_repository.py:15` only passes `"notes"` as a value. So the §8.2 `coalesce` fix breaks nothing, but nothing protects it either. | Write a new test first: upsert with notes, re-upsert with `None`, assert the original survives. This is the TDD entry point for that fix. |

### Job 2 collisions

| # | Collision | Fix |
|---|---|---|
| P10 | **`profiles.graph_connection_status` is read or written in six places**: `auth.py:34`, `me.py:39/61/65`, `graph_tokens_service.py:27`, and `profiles.py:36-49`. Connection status becomes **per-account** under D1, so this column's meaning dissolves. | Move to `connected_accounts.connection_status` (already in the §6.2 schema). Migrate each call site to take an `account_id`. |
| P11 | **`ProfilesRepository.list_connected()` (`sync.py:27`) drives the entire bulk cron.** It queries `profiles where graph_connection_status = 'connected'` — which no longer identifies what to sync. | Replace with a `connected_accounts` query returning `(user_id, account_id, provider)`, and have the cron iterate accounts rather than users. This is the single highest-risk edit in job 2: get it wrong and the cron silently syncs nothing. |
| P12 | `/api/me/graph-status` returns one connected/`needs_reauth` state. The frontend's `GraphStatus` union (`dashboard/page.tsx:12-16`), its 409 handling (`:51-53`), and `NavBar.tsx:32-46` all assume exactly one account. | Endpoint returns a list of accounts with per-account status; dashboard renders "account X needs reconnecting" rather than a single page-level state. Frontend change is larger than it looks. |
| P13 | `GraphTokensRepository` is consumed by `auth.py`, `me.py`, and `graph_tokens_service.py`; `tests/test_graph_tokens_service.py` targets it directly. Dropping `ms_graph_tokens` breaks all of them. | Introduce `ConnectedAccountsRepository`, port call sites, migrate data, *then* drop the table — in that order, as separate commits. |
| P14 | `SyncStateRepository.get/upsert` take `user_id`; the PK change to `(account_id, resource_type)` changes both signatures, which ripples into `sync_mail`, `sync_calendar`, and `sync_chat`. | Expect to touch all three sync functions. Budget for it rather than discovering it. |
| P15 | `emails.account_id` cannot be `not null` until backfilled. | Three-step migration: add nullable → backfill from the migrated `connected_accounts` row → `set not null`. Never one step. |

### Ordering constraint

P1/P2 must land **with** the client change, not after — otherwise the suite is red for
the whole job and stops being a safety net. P9 must land **before** the notes fix, since
it is the test that proves it. P13/P15 are strictly ordered as written.

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
  `provider_message_id`. Also add `provider_labels text[]` here if job 1 has not
  already added it (§5.5) — Gmail's `labelIds` land in the same column Microsoft's
  `inferenceClassification` complements. No `headers jsonb` column: §5.5 was revised to
  use provider-native classification instead, so it is not needed.
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

## 7. Job 4 — Dashboard and UI redesign

*(Job 3 = the remaining audit fixes in §8. Independent of everything else, land whenever.
Note §7.6 below: job 4 absorbs five of them naturally.)*

Added 2026-10-08 from a supplied dashboard mockup plus a set of illustration assets.

### 7.1 What the mockup specifies

A complete visual reskin and a new information architecture: warm cream ground,
forest-green accent, terracotta secondary, soft pastel icon chips, and a painted autumn
landscape behind the greeting.

The mockup is wordmarked "Candoor". **That name is not used** — the product is
*AI Workspace Assistant* (§7.9). Take the mockup's visual direction, not its branding.

**Navigation changes from 4 items to 7, plus a global search bar:**

| Mockup nav | Today | Status |
|---|---|---|
| Dashboard | `/dashboard` | Rebuild |
| Emails | — | **New page, no backend** |
| Tasks | `/planner` | Rename + restyle |
| People | `/contacts` | Rename + restyle |
| Calendar | — | **New page, no backend endpoint** |
| Insights | — | **New page, no backend** |
| Settings | — | **New page** (job 2 needs it anyway, for managing connected mailboxes) |
| *(top search bar)* | `/search` page | Promote to global chrome |

### 7.2 Assets (verified on disk at `assets/`)

| File | Size | Alpha |
|---|---|---|
| `main.webp` | 67 KB | opaque — composite base |
| `layer-clouds.webp` | 224 KB | yes |
| `layer-canopy.webp` | 198 KB | yes |
| `layer-water.webp` | 146 KB | yes |
| `layer-leaves.webp` | 43 KB | yes |
| `layer-birds.webp` | 26 KB | yes |

All six are 1672×941; **712 KB total**. One opaque composite plus five transparent
depth layers is a parallax kit, not a single banner. `main.webp` has deliberate negative
space across its left ~55%, which is where the greeting sits.

**Actions:** move `assets/` into `frontend/public/` (it is currently untracked at the
repo root and would not ship). Serve `main.webp` alone as the static/reduced-motion
fallback; stack the layers only when motion is permitted. 712 KB is acceptable on a CDN
but must not block first paint — the hero loads after the stat tiles.

### 7.3 The reskin is cheap; the data is not

Verified: **no component contains a hardcoded colour or a stock Tailwind palette class.**
Every colour resolves through one of the 13 `var(--color-*)` tokens. The dark→light
inversion is therefore a ~13-line edit in `globals.css`, not a component sweep. That is
a genuine payoff from the existing token discipline.

The real work is that **the dashboard displays data the backend does not produce.**
`/api/dashboard` returns exactly three fields today: `contact_count`,
`open_action_item_count`, `activity[]`.

| Mockup element | Available today? | Gap |
|---|---|---|
| Emails Processed 128, ↑12% this week | no | week-over-week count — computable in SQL from `emails.received_at` |
| Tasks Extracted 36, ↑24% | no | same, from `action_items.created_at` |
| Meetings Detected 8, ↑3 | no | needs email classification — see §7.4 |
| Important People 24, ↑5 new this month | partial | `contact_count` is all-time; "important" is undefined — **open question** |
| Recent Emails list | no | there is no email endpoint at all |
| Task/Meeting/FYI/Important chips | no | classification — see §7.4 |
| Upcoming Tasks, "From: X" | mostly | `action_items` already joins contact |
| Email→Task Flow, 7-day series | no | daily aggregate, computable in SQL |
| Task Categories donut | no | needs a category per action item — see §7.5 |
| Quick Actions | partly | Sync exists; the rest are navigation |

### 7.4 Email classification costs zero extra LLM tokens

The five filter chips look like they need an LLM pass. They do not — every one is
derivable from data already held or returned free by the provider:

| Chip | Derivation | Cost |
|---|---|---|
| **Task** | `exists (select 1 from action_items where source_type='email' and source_id = emails.id)` | free, already stored |
| **Meeting** | Graph exposes the `eventMessage` subtype with `meetingMessageType`; Gmail carries a `text/calendar` part or `.ics` attachment | free, provider-native |
| **FYI** | extracted, produced no action items, not a meeting | free |
| **Important** | Graph `importance == "high"` or `inferenceClassification == "focused"` | free — the same field §5.5 already stores for the pre-filter |
| **All** | — | — |

This matters because the obvious implementation — asking the LLM to classify every
email — would add output tokens to every call and worsen the §4 budget. Deriving
instead keeps the quota math in §4 unchanged.

### 7.5 Task categories must be decided during job 1, not job 4

The Task Categories donut (Work 45% / Meetings 25% / Follow-ups 20% / Other 10%) is the
one dashboard element that genuinely needs new LLM output: a category per action item.

**It is ~5 tokens per item — trivially cheap — but it must go into `_SCHEMA` during
job 1.** Adding it later means a second migration *and* re-extracting every message to
backfill the column, which costs a full day's token quota and several days of wall
clock at 200K TPD.

**Therefore: add `category` to the action-item schema in job 1 even though nothing
consumes it until job 4.** This is the single most important cross-job dependency
introduced by the redesign. Suggested enum, matching the mockup's legend:
`work | meeting | follow_up | other`.

The same logic applies to anything else job 4 wants out of the LLM — decide it in job 1
or pay for a re-extraction.

### 7.6 Audit findings absorbed by this job

Five items from §8 are fixed as a natural consequence of the rewrite. Do not schedule
them separately:

- **§8.5/16** — the new nav must use `next/link`, which retires the full-page-reload bug
  and makes `PageTransition` and the `layoutId` animations function for the first time
- **§8.7/20** — `NavBar`'s `localStorage`-in-`useState` hydration mismatch; the nav is
  being rewritten anyway
- **§8.7/21** — `prefers-reduced-motion` stops being optional the moment there is a
  parallax hero (see §7.7)
- **§8.7/21** — `color-scheme` must be declared for the light theme so native
  `<select>` and scrollbars match
- **§8.5/17** — the dashboard currently downloads every action item to count them
  client-side; the new aggregate endpoint removes the need

### 7.7 Charts and motion

**Charts.** There is no charting dependency today. Two shapes are needed: a 2-series
line/area chart and a donut. Recommendation: **hand-authored inline SVG**, not a
library. Rationale — the app is a static export where bundle size is the main cost,
two chart types do not justify ~100 KB of Recharts, and the design calls for specific
styling (soft area fill, muted grid, matched token palette) that is easier to control
directly. Load the `dataviz` skill before writing them.

**Parallax.** Layer order back-to-front is `clouds → canopy → water → leaves → birds`
over `main.webp`. Drive it from pointer position and/or scroll with small translate
offsets per layer.

**`prefers-reduced-motion` is a hard requirement here, not a nicety.** Under reduced
motion: render `main.webp` alone, no layer stack, no transform. Framer Motion is already
a dependency; `<MotionConfig reducedMotion="user">` at the root handles the existing
animations in one line. GSAP is available if the parallax wants a timeline, but Framer
alone is likely sufficient — avoid adding a second animation library for one effect.

### 7.8 Sequencing within job 4

```
4a  Palette + tokens + color-scheme  (globals.css, ~13 lines)   <- ship first, zero risk
4b  Nav shell: 7 items, next/link, global search bar, Settings page
4c  Backend: /api/dashboard aggregate contract      <- needs job 1 only
4d  Dashboard widgets + charts against the real contract
4e  Parallax hero + reduced-motion fallback
---- job 2 lands here ----
4f  /api/emails + Emails page                       <- needs job 2
4g  Insights + Calendar pages
```

> **Revised on re-review.** An earlier draft put *all* of 4c onward after job 2, on the
> grounds that the Emails page needs per-account attribution. That is true of the Emails
> page — and **only** of the Emails page. The dashboard aggregates (stat tiles,
> week-over-week deltas, the 7-day series, the category donut) sum over whatever
> accounts exist and need no schema knowledge of multi-account at all.
>
> Splitting them moves the visible, demo-able dashboard **before** the largest and
> riskiest job instead of after it. For a project that is half interview artifact, that
> ordering matters: 4a–4e delivers something worth showing without touching the schema.

4a and 4b are independent of every other job and can land immediately.

### 7.9 Naming — DECIDED

**The product name is "AI Workspace Assistant".** Matches the folder. The mockup's
"Candoor" wordmark is **not** used — substitute the product name wherever the mockup
shows it.

**User-facing strings — only four, all one-line:**

| File | Current |
|---|---|
| `backend/app/main.py:11` | `FastAPI(title="AI Assistant API")` |
| `frontend/app/layout.tsx:18` | `title: "AI Assistant"` |
| `frontend/app/components/NavBar.tsx:71` | sidebar wordmark |
| `frontend/app/(auth)/login/page.tsx:56` | hero body copy |

Plus one line in `README.md`.

> ### Infrastructure identifiers — renamed 2026-10-14
>
> Three strings carried the project's original name. They were left alone
> while the old deployment was live, because renaming them requires a coordinated
> change across Render, Cloudflare Pages and the workflow — and the cron swallowed
> failures, so a half-done rename looked exactly like a working one.
>
> That constraint is gone: the old Supabase project was deleted and everything is
> being redeployed from scratch, so there is no running service to break. All three
> now read `ai-workspace-assistant`:
>
> | File | String |
> |---|---|
> | `render.yaml` | service `ai-workspace-assistant-api` |
> | `.github/workflows/graph-sync-cron.yml` | `ai-workspace-assistant-api.onrender.com` |
> | `render.yaml` | CORS origin `ai-workspace-assistant.pages.dev` |
>
> **These must match the names you actually create.** The Pages project name decides
> the origin CORS allows, and the Render service name decides the hostname anything
> calling the bulk-sync endpoint must use.
>
> **The scheduled sync was removed on 2026-10-16.** A GitHub Actions cron calling the
> service every 15 minutes keeps a Render free instance awake continuously, consuming
> essentially the whole ~750 instance-hour monthly allowance for the account. Sync is
> now triggered on sign-in and by the "Sync now" button; `POST /api/sync/run` still
> exists for any external scheduler.
>
> The GitHub repo name is cosmetic; GitHub redirects old remote URLs, so renaming it
> is safe but optional.

**Sidebar fit.** "AI Workspace Assistant" is considerably longer than "Candoor". The
collapsed rail (72 px) already hides the wordmark entirely
(`{!collapsed && <span>…}`), so there is no truncation bug — but at the 224 px expanded
width it is tight. Consider a short mark (monogram or icon) for the collapsed state, as
the mockup's logo treatment implies.

---

## 8. Audit findings (reference)

Full audit run against `f888dec`, re-verified at `7fe7f1a`. Measured:
frontend **108/108 pass**, `tsc` clean, backend **132 of 173 error**,
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

### 8.6b Correction: eslint is not clean

The original audit reported `eslint` as clean. **That was wrong** - the run
returned empty output and was misread. Verified 2026-10-09 against a worktree at
`7fe7f1a` that **6 errors predate this session**:

- 4 x `react-hooks/set-state-in-effect` - the `useEffect(() => { load() }, [load])`
  pattern in `contacts/view`, `dashboard`, `planner` and `search`. React flags it
  because it triggers a cascading render on every mount.
- 2 x `@typescript-eslint/no-explicit-any` in `lib/api.test.ts`.

None are introduced by the work in this session. The React ones want a real fix
(move the fetch out of the effect body, or gate it), not a suppression - but that
is a refactor of four pages, so it belongs with job 4b, which rewrites them anyway.

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
    ~~`backend/.venv` has absolute paths from a previous location and should be recreated~~ - **retracted 2026-10-08.** Verified during job 0: `pytest.exe` and `uvicorn.exe` both run, and site-packages contains no stale references. The old paths seen in tracebacks came from `.pyc` bytecode caches, not the venv. Clearing `__pycache__` is sufficient; a rebuild is unnecessary churn. 
    `set_scheduled_calendar_event_id` (`action_items.py:104-108`) does not bump
    `updated_at` unlike `update_status`.

---

## 9. Sequencing

```
Job 0  Stand the stack back up - local AND deployed.          >> SEE §11 <<
       Every deployed piece is currently down. This is not a one-liner;
       it is four providers plus three external app registrations.
       Nothing below is verifiable until local dev works.
   |
Job 4a Palette + tokens + color-scheme           §7.8  <-- zero risk, ship anytime
       ~13 lines in globals.css. Independent of everything.
   |
Job 1  Free LLM + extraction hardening           §5    <-- the critical path
       Groq swap, poison-pill fix, Pydantic validation, quota handling,
       pre-filter, truncation, batching, prompt-injection fencing (§8.6/18),
       notes coalesce (§8.2/3), the six logging fixes (§8.4/10),
       render.yaml env vars (§8.1/1)
       >> INCLUDES action_item.category for job 4 (§7.5) - do not defer <<
   |
Job 3a Quick independent wins (parallel with job 1)
       indexes + subquery rewrite (§8.5/15), four try/catch handlers (§8.4/9),
       timezone bucketing (§8.3/6)
       (next/link is NOT here - job 4b absorbs it, §7.6)
   |
Job 4b Nav shell: 7 items, next/link, global search, Settings page   §7.8
       Absorbs five audit findings (§7.6). Independent of jobs 1 and 2.
   |
Job 4c Backend: /api/dashboard aggregate contract                      §7.3
       Needs job 1 only. Aggregates sum over whatever accounts exist.
   |
Job 4d Dashboard widgets, charts (hand-authored SVG, load `dataviz`)   §7.7
Job 4e Parallax hero + prefers-reduced-motion fallback                 §7.7
       >> 4a-4e gives a demo-able product before the riskiest job <<
   |
Q1     RESOLVE: is Google Calendar in scope?
   |
Job 2  Multi-provider email                      §6
       connected_accounts migration, linkIdentity flow, gmail_client,
       provider-dispatching sync, advisory locking (§6.5)
   |
Job 4f /api/emails + Emails page                                       §7.8
       AFTER job 2 - needs per-account attribution, else it gets rebuilt.
   |
Job 4g Insights + Calendar pages
       Calendar needs one new repo method (list_between) + one route;
       CalendarEventsRepository today has only upsert/delete/count/
       list_busy_between. Insights scope is undefined - see §10.
   |
Job 3b Remaining polish                          §8.7
```

Job 1 before job 2 is not arbitrary: job 1 establishes the token budget that job 2's
volume must fit inside, and shipping job 2 first would multiply mail volume into a
pipeline that still burns its entire quota retrying items that can never succeed.

**Before writing any code, read §5.8.** It lists 15 verified collisions between these
planned changes and code that exists today — two of which turn the test suite red the
moment the LLM client is touched, and two of which (P10/P11) will make the sync cron
silently process nothing if handled carelessly.

---

## 10. Next step

Per the brainstorming workflow: this spec needs review, then the implementation plan is
produced via the `writing-plans` skill — task-by-task, TDD-ordered, one plan per job
starting with job 1.

**Review checklist:**

- §3 Q1 — is Google Calendar in or out of scope? *(blocks job 2 only)*
- §3 Q2 — keep the env-driven provider seam (D4)?
- §4 — is a ~5-day backfill acceptable, or should the filter be more aggressive?
- §5.2 — is 3 attempts before dropping an item right?
- §5.5 — comfortable relying on `inferenceClassification` / Gmail `labelIds` as the
  main bulk signal? It is free and accurate, but it is the provider's judgement, so a
  genuine message misfiled as `other` would be skipped.
- §9 — does the sequencing match your priorities?
- ~~§7.9 — which name wins?~~ **Resolved: "AI Workspace Assistant".**
- ~~§7.3 — what makes a person "Important"?~~ **Decided: contacts with open
  action items.** Open work is the one signal the app already models, and the one
  that changes as the user works rather than only as mail arrives. Revisit if it
  reads wrong in practice.
- ~~§7.7 — animated parallax hero, or static `main.webp`?~~ **Decided: static.**
  `main.webp` alone (67 KB); the five `layer-*` files stay unused in `assets/` rather
  than shipping 645 KB for motion that was not wanted.
- ~~§7.1 — what goes on the Insights page?~~ **Decided: keep it.** Scope set to three
  things the existing schema can actually answer, rather than inventing metrics:
  **who you owe work to and who owes you** (action items by contact and direction),
  **busiest correspondents** (emails by sender), and **pipeline health** (how much mail
  was extracted, skipped as bulk, or failed - readable now that §5.2 and §5.5 record
  both).

**Change log for this document**

| Rev | Change |
|---|---|
| 1 | Initial design. |
| 2 | Added **§5.8**, a pre-flight list of 15 verified collisions between planned changes and existing code. Corrected **§5.4** (quota state cannot reuse `sync_state` — CHECK constraint). Rewrote **§5.5** (Graph `internetMessageHeaders` requires `$select` on single-message GET, so the filter now uses provider-native classification instead). Specified the batch/transaction boundary in **§5.6**. Renamed `llm_batch_size` → `llm_messages_per_call` to avoid collision with the existing `extraction_batch_limit`. |
| 5 | Added **§11, Environments** — how to run the app locally and deployed. Job 0 was a one-line placeholder; verification showed **every deployed piece is down**: the Supabase project is deleted (HTTP 000 on REST and Auth), the Render service returns "suspended by its owner", and Cloudflare Pages is not serving. §11 covers local setup via the Supabase CLI on Docker (which also makes the 132 erroring tests hermetic), production setup across four providers, the OAuth redirect matrix — the two-places-two-different-URLs trap — a complete env-var reference, and a job-0 definition of done. Records the constraint that forces it: `profiles.id references auth.users(id)`, so DB and Auth must be the same Supabase instance and cannot be mixed. |
| 4 | **Name resolved: "AI Workspace Assistant"** (not the mockup's "Candoor") — §7.9 now lists the four one-line code strings, and warns that the three remaining `ai-relationship-manager` strings are **live infrastructure**, not branding: renaming the Render service breaks the cron URL, which fails silently under `curl -sf`. **Re-ordered §7.8/§9** — an earlier draft deferred all dashboard work until after job 2; only the *Emails page* actually needs multi-account, so the dashboard aggregates now land before job 2, giving a demo-able product ahead of the riskiest job. Removed a duplicated Q1 gate. Added the Insights-scope question to §10. |
| 3 | Added **job 4** (**§7**) from the supplied dashboard mockup and parallax assets: new 7-item IA, warm-light reskin, four new pages, charts, parallax hero. Key findings — the reskin itself is ~13 lines because no component hardcodes a colour (verified), but the dashboard needs data the backend does not produce; the Task/Meeting/FYI chips are derivable at **zero LLM cost** (§7.4); and `action_item.category` **must be added during job 1** or job 4 forces a full re-extraction (§7.5, now reflected in §5.3). Job 4 absorbs five audit findings (§7.6). **§9** sequencing rewritten to interleave job 4. |

---

## 11. Environments — running locally and on the internet

Added 2026-10-08. Job 0 previously read "provision a new Supabase project". That
understated it considerably.

### 11.1 Verified current state

| Piece | State (checked 2026-10-08) | Action |
|---|---|---|
| Supabase project `fmzbuhyguxgxodymmojm` | **Deleted.** REST and Auth both return HTTP 000 — DNS does not resolve. | Create new |
| Render backend | **Suspended.** Returns `"This service has been suspended by its owner."` | Resume or recreate |
| Cloudflare Pages | Not serving. | Redeploy |
| `backend/.env` | All 10 keys populated — but `DATABASE_URL` / `SUPABASE_URL` point at the deleted project. | Repoint |
| `frontend/.env.local` | All 3 keys populated, same problem. | Repoint |
| `backend/.venv` | Absolute paths from a previous folder location (§8.7/22). | Recreate |
| Backend test suite | 132 of 173 error — they open a real connection to the dead project. | §11.7 fixes this |

**Local tooling present:** Docker 28.3.2, Node 22.13.1, npm 10.9.2, Python 3.11.9,
gh 2.100.0, git 2.46. **Missing:** the Supabase CLI.

### 11.2 The constraint that shapes the whole setup

`profiles.id` is declared `references auth.users(id)`
(`20260715000000_foundation_schema.sql:4`). **The application database and the Auth
service must therefore be the same Supabase instance.** A tempting hybrid — local
Postgres for speed, cloud Supabase for real OAuth — is impossible: user IDs minted by
cloud Auth would violate a foreign key against a local `auth.users`.

Reinforcing this, `deps.py:13` builds its JWKS URL from `settings.supabase_url`, so JWT
verification also follows whichever instance is configured.

**Consequence: two self-consistent environments, never mixed.**

```
LOCAL         supabase CLI (Docker)  ->  Postgres + Auth + Studio on localhost
PRODUCTION    cloud Supabase project ->  Postgres + Auth
```

### 11.3 Local setup

**Why local Supabase rather than a second cloud project:** it makes the test suite
hermetic. Those 132 erroring tests are the single largest obstacle to doing jobs 1-4
safely, and they stay broken as long as they need a remote database. Docker is already
installed, so this costs one `npm i -D supabase`.

```bash
# 1. Supabase CLI + local stack (Postgres, Auth/GoTrue, Studio)
cd "C:\Drive D\College\Projects\AI Workspace Assistant"
npm i -D supabase
npx supabase init          # creates supabase/config.toml; migrations/ already exists
# Pull the four needed images FIRST, detached - see the warning below.
npx supabase start --debug   -x realtime,storage-api,imgproxy,edge-runtime,logflare,vector,pg-meta,studio,supavisor,mailpit

# 2. Apply all migrations to the local DB
npx supabase db reset      # replays supabase/migrations/*.sql in order

# 3. Backend deps. The existing .venv works - do NOT rebuild it (see 8.7/22).
#    Just clear stale bytecode caches, which carry old folder paths into tracebacks.
cd backend
find . -name __pycache__ -type d -exec rm -rf {} +
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 4. Run backend + frontend
.venv/Scripts/uvicorn app.main:app --reload --port 8000
cd ../frontend && npm install && npm run dev        # http://localhost:3000
```

> ### First-run image pull: do this detached, and exclude what you do not need
>
> `supabase start` pulls **12 images concurrently**, and `supabase/postgres` alone is
> **1.24 GB**. On a normal connection the whole set takes long enough that any wrapper
> with a timeout will kill it mid-download, leaving zero images and no explanation.
> Verified the hard way on 2026-10-08: three separate attempts died this way while the
> registry was reachable throughout (a `docker pull hello-world` succeeded in seconds
> between failures).
>
> **This app needs four of those twelve** - `postgres`, `gotrue` (auth), `postgrest`,
> and `kong` (gateway). Storage, Realtime, imgproxy, edge-runtime, mailpit, pg-meta,
> Studio and the logflare/vector analytics pair are all unused here. Excluding them
> removes roughly half the download and most of the memory footprint.
>
> Pull them serially and detached first, then start:
>
> ```bash
> docker pull public.ecr.aws/supabase/postgres:17.11.0.004   # 1.24 GB - the slow one
> docker pull public.ecr.aws/supabase/gotrue:v2.197.0        # 62 MB
> docker pull public.ecr.aws/supabase/postgrest:v16.4        # 21 MB
> docker pull public.ecr.aws/supabase/kong:2.8.1             # 139 MB
> ```
>
> Pin the tags to whatever your CLI version reports - run `supabase start --debug` once
> and read the `<tag>: Pulling from supabase/<image>` lines. Once cached, `start` is fast.
>
> Excluding Studio means no database GUI. Connect with any Postgres client on
> `postgresql://postgres:postgres@127.0.0.1:54322/postgres`, or drop `studio,pg-meta`
> from the exclusion list and pull those two as well.
>
> **Use `--debug` on the first run.** Without a TTY, the CLI's progress renderer buffers everything: `supabase start` produces zero output and pulls zero images for many minutes while appearing to work. It is not downloading - it is stuck. `--debug` bypasses the renderer and streams real pull progress. Verified 2026-10-08; the registry itself was reachable the whole time.

`supabase start` prints the local keys — they are fixed, well-known development values,
not secrets. Put them in `backend/.env` and `frontend/.env.local` per §11.6.

**Previewing the production build locally.** `next dev` works normally, but
`next.config.ts` sets `output: "export"`, so `npm run build` emits static files to
`out/` with no server. To check what actually ships:

```bash
npm run build && npx serve out
```

Several bugs — the hydration mismatches at §8.7/20 in particular — appear only in the
exported build, never in dev.

### 11.4 Production setup

Four providers, in this order; each depends on the previous.

**1. Supabase (new project)**

- Create the project; record project ref, URL, anon key, service_role key
- Apply migrations: `npx supabase link --project-ref <ref>` then `npx supabase db push`
- Auth → Providers → enable **Azure**, paste the Azure client ID and secret
- Auth → URL Configuration → add the redirect URLs from §11.5
- **Enable manual identity linking** — job 2 depends on it (§6.3)

**2. Render (backend)**

- Resume the suspended service, or create a new one from `render.yaml`
- **`render.yaml` cannot boot as committed.** It omits `SYNC_SECRET` and the LLM key
  (§8.1/1, §5.8/P5). Fix the blueprint before deploying or the service crashes on
  startup with a pydantic `ValidationError`.
- Set every variable from §11.6 in the Render dashboard
- Free tier spins down after ~15 minutes idle and cold-starts in tens of seconds

**3. Cloudflare Pages (frontend)**

- Root directory `frontend`, build command `npm run build`, output directory `out`
- Set the three `NEXT_PUBLIC_*` variables. These are **inlined at build time**, so
  changing one requires a rebuild, not a restart.
- Record the deployed domain — it must appear in `CORS_ALLOW_ORIGINS` on Render

**4. GitHub Actions (cron)**

- Repo secret `SYNC_SECRET`, matching Render exactly
- If the Render hostname changed, update `graph-sync-cron.yml:14` — the URL is
  hardcoded, and `curl -sf` hides the resulting failure (§7.9, §8.4/10)

### 11.5 The OAuth redirect matrix

This is the part most often got wrong, because the URLs live in **two different places
and are not the same URLs**. The flow is:

```
browser -> Supabase Auth -> Microsoft/Google -> Supabase callback -> app /callback
```

The provider redirects to **Supabase**; Supabase then redirects to **your app**.

| Registered where | Local | Production |
|---|---|---|
| **Azure** → Redirect URIs | `http://localhost:54321/auth/v1/callback` | `https://<ref>.supabase.co/auth/v1/callback` |
| **Google Cloud** → Authorised redirect URIs *(job 2)* | `http://localhost:54321/auth/v1/callback` | `https://<ref>.supabase.co/auth/v1/callback` |
| **Supabase** → Auth → Redirect URLs | `http://localhost:3000/callback` | `https://<domain>/callback` |
| **Supabase** → Auth → Site URL | `http://localhost:3000` | `https://<domain>` |

The app side comes from `login/page.tsx:31` —
`redirectTo: ${window.location.origin}/callback` — which follows whatever origin the
browser is on, so **both** origins must be allow-listed in Supabase.

**Google-specific (job 2):** request `access_type=offline` **and** `prompt=consent`, or
no refresh token is issued and the connection dies after roughly an hour (§6.3). Enable
the Gmail API on the Google Cloud project, and while the consent screen is in *Testing*
status, add the account as a test user.

**Microsoft personal accounts:** `MS_AUTHORITY` is already
`https://login.microsoftonline.com/common`, which accepts both personal and work/school
accounts. No change needed for the "personal accounts" requirement — though note that
Teams chat sync has no personal-account equivalent, so `sync_chat` will mark itself
`not_available` for those, which the existing code already handles
(`graph_sync.py:151-154`).

### 11.6 Environment variables — complete reference

| Variable | Local value | Production location |
|---|---|---|
| `DATABASE_URL` | from `supabase start` | Render |
| `SUPABASE_URL` | `http://localhost:54321` | Render |
| `SUPABASE_SERVICE_ROLE_KEY` | from `supabase start` | Render |
| `FERNET_KEY` | reuse the existing value | Render — **do not rotate.** Rotating makes every stored Graph token permanently undecryptable. |
| `MS_CLIENT_ID` / `MS_CLIENT_SECRET` | same Azure app | Render |
| `MS_AUTHORITY` | `https://login.microsoftonline.com/common` | Render |
| `CORS_ALLOW_ORIGINS` | `http://localhost:3000` | Render — add the Pages domain. **No space after the comma** (§8.6/19). |
| `SYNC_SECRET` | any random string | Render **and** GitHub repo secret — must match |
| `LLM_API_KEY` | Groq key (§5.1) | Render — **absent from `render.yaml` today** |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Render |
| `LLM_MODEL` | `openai/gpt-oss-20b` | Render |
| `NEXT_PUBLIC_SUPABASE_URL` | `http://localhost:54321` | Cloudflare Pages *(build-time)* |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | from `supabase start` | Cloudflare Pages *(build-time)* |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Cloudflare Pages *(build-time)* |

`apiBaseUrl()` (`lib/api.ts:4`) applies a non-null assertion to
`NEXT_PUBLIC_API_BASE_URL`. If it is unset at build time, every request throws
`undefined.trim()` at runtime with no useful message. Verify it before building.

### 11.7 Making the test suite hermetic

Point `backend/.env` at the local stack and the 132 erroring tests get a real database.
Two further improvements, both worth doing during job 1:

1. **Wrap each test in a transaction and roll it back.** The `pool` fixture
   (`conftest.py:10-14`) creates and closes the *global* pool per test — slow, and it
   leaves rows behind between runs.
2. **Stop creating real Supabase Auth users.** `test_auth_user` and `test_auth_user_2`
   (`conftest.py:17-58`) POST to the Auth admin API. Against a local stack that is at
   least offline, but inserting directly into `auth.users` is faster and collapses the
   duplicated fixture pair — they are identical apart from their names.

Target: `pytest -q` passes with no network access at all.

### 11.8 Definition of done for job 0

- [ ] `npx supabase start` runs; Studio reachable at `http://localhost:54323`
- [ ] `npx supabase db reset` applies all 7 migrations without error
- [ ] `pytest -q` → **173 passed**, with no network
- [ ] `uvicorn` serves `/health` → `{"status":"ok"}`
- [ ] `npm run dev` → Microsoft sign-in works end to end against local Supabase
- [ ] `npm run build && npx serve out` → the exported build works too
- [ ] Render deploys from a **corrected** `render.yaml`; `/health` returns 200
- [ ] Cloudflare Pages serves the built frontend
- [ ] The cron reaches `POST /api/sync/run` with the shared secret and returns counts
