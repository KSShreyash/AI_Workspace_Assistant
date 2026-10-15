# AI Workspace Assistant

A workspace assistant for your email and calendar. Connect as many Outlook and Gmail mailboxes as you like; it reads your mail, Calendar and Teams chats, works out **who you deal with**, **what you owe them (or they owe you)**, and **when you should meet next** — using an LLM to turn raw messages into structured contacts and action items.

Sign in with Microsoft, and it does the rest: pulls your recent communications, extracts commitments, keeps a running summary of every person you talk to, and books follow-up meetings straight onto your calendar.

---

## Contents

- [What it does](#what-it-does)
- [Stack](#stack)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [How signing in works](#how-signing-in-works)
- [How syncing works](#how-syncing-works)
- [How extraction works](#how-extraction-works)
- [How scheduling works](#how-scheduling-works)
- [Data model](#data-model)
- [API reference](#api-reference)
- [Running it locally](#running-it-locally)
- [Testing](#testing)
- [Design decisions and tradeoffs](#design-decisions-and-tradeoffs)
- [Known limitations](#known-limitations)

---

## What it does

1. You sign in with your Microsoft account (personal or work/school).
2. It pulls your recent emails, calendar events and Teams chat messages.
3. An LLM reads each message and produces two things:
   - **Contacts** — the people you email, meet and chat with, each with a short synthesised summary of what's known about them, rewritten as new information arrives.
   - **Action items** — commitments, tagged as *mine* (things you promised someone) or *theirs* (things someone promised you), with an optional due date.
4. Four screens to work with the result:
   - **Dashboard** — connection status, counts, recent activity, manual sync/extract triggers.
   - **Contacts** — searchable table of everyone it has learned about, each with notes and their open items.
   - **Planner** — action items bucketed into Overdue / Today / Tomorrow / This week / Next week / No date / Completed.
   - **Search** — one box across contacts and action items, filterable to People or Tasks.
5. From an action item you can hit **Schedule**: it suggests free 30-minute slots and books the meeting on Microsoft Calendar, optionally as a Teams meeting.
6. A background job repeats the sync-and-extract pass every 15 minutes for every connected user, so the data stays current on its own.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript | Statically exported (`output: "export"`) so it ships as plain files |
| Styling | Tailwind CSS v4 | Utility classes, design tokens in `globals.css` |
| Frontend host | Cloudflare Pages | CDN-served static export |
| Frontend tests | Vitest + React Testing Library | Component tests in jsdom |
| Backend | FastAPI (async Python) | All business logic and database access |
| Backend host | Render | `uvicorn app.main:app` |
| Backend tests | pytest + pytest-asyncio + respx | Outbound HTTP to Graph/OpenAI is mocked |
| Database | Postgres, hosted by Supabase | Users, tokens, synced messages, contacts, action items |
| Auth | Supabase Auth, Azure AD (Entra ID) provider | Handles the Microsoft OAuth dance, issues our session JWT |
| Communications | Microsoft Graph API | Source of truth for mail, calendar, chat; also where meetings get created |
| LLM | OpenAI `gpt-4o-mini` | Structured extraction with strict JSON Schema |
| Token refresh | MSAL (Python) | Redeems the stored refresh token for a new Graph access token |
| Secrets at rest | Fernet (`cryptography`) | Graph tokens are encrypted before they touch Postgres |
| Scheduling | GitHub Actions cron | Calls the bulk-sync endpoint every 15 minutes |

## Architecture

```
┌──────────────────────┐        ┌──────────────────────┐        ┌──────────────────────┐
│   Cloudflare Pages   │        │        Render        │        │       Supabase       │
│  Next.js static      │ HTTPS  │   FastAPI backend    │  TCP   │   Postgres + Auth    │
│  export — the UI     │───────▶│                      │───────▶│                      │
└──────────┬───────────┘        └──────────┬───────────┘        └──────────────────────┘
           │                               │
           │  1. Sign in with Microsoft    │  3. Store / refresh Graph tokens (encrypted)
           │     goes through Supabase     │  4. Read + write emails, calendar_events,
           │     Auth directly             │     chat_messages, contacts, action_items
           │                               │  5. Call out to Microsoft Graph and OpenAI
           │  2. Every other API call      │
           │     goes to Render with the   │
           │     Supabase JWT as a Bearer  │
           └───────────────────────────────┘
```

Three independently hosted pieces, glued together by two tokens: a **Supabase JWT** (frontend → backend) and a **Microsoft Graph access/refresh pair** (backend → Graph). They do different jobs and never mix — the JWT proves "a logged-in user of this app sent this request"; the Graph tokens prove "this backend may read this specific mailbox". The Graph tokens are stored server-side, encrypted, and never return to the browser after login.

## Repository layout

```
.
├── frontend/                    Next.js app (static export → Cloudflare Pages)
│   ├── app/
│   │   ├── (auth)/login/        Sign in with Microsoft
│   │   ├── (auth)/callback/     Where the OAuth flow lands
│   │   ├── dashboard/           Home after login
│   │   ├── contacts/            List + contacts/view/ for one person
│   │   ├── planner/             Action items bucketed by due date
│   │   ├── search/              Combined search
│   │   └── components/          NavBar, ScheduleActionItemPanel, ui/ primitives
│   └── lib/
│       ├── api.ts               apiFetch() — every backend call goes through here
│       ├── supabase/client.ts   Browser client, used only for auth
│       └── graph-scopes.ts      The exact Graph permissions requested
│
├── backend/                     FastAPI app (→ Render)
│   └── app/
│       ├── main.py              Entrypoint, routers, CORS
│       ├── api/v1/              One file per endpoint group
│       ├── services/            Graph sync, AI extraction, scheduling, token refresh
│       ├── repositories/        All SQL, one file per table
│       ├── core/                Settings, JWT verification, Fernet helpers
│       └── db/session.py        asyncpg connection pool
│
├── supabase/migrations/         Schema changes in order, as plain SQL
├── render.yaml                  Backend deployment config
├── .github/workflows/           Scheduled sync trigger
└── docs/                        Design specs and implementation plans
```

The layering is strict: routers parse and serialize, services hold logic, repositories hold every line of SQL. No SQL in a service, no HTTP in a repository. Every query filters by `user_id`.

## How signing in works

Files: `frontend/app/(auth)/login/page.tsx`, `frontend/app/(auth)/callback/page.tsx`, `backend/app/api/v1/auth.py`, `backend/app/core/deps.py`

1. The user clicks **Sign in with Microsoft**, which calls `supabase.auth.signInWithOAuth({ provider: 'azure', ... })` with the scopes in `lib/graph-scopes.ts`: `openid email profile offline_access` plus `User.Read Mail.Read Chat.Read Calendars.ReadWrite OnlineMeetings.ReadWrite`.
2. The browser goes to Microsoft, the user authenticates and consents.
3. Microsoft redirects to **Supabase's** callback (that's the URI registered on the Azure app registration, not ours). Supabase exchanges the code for Microsoft tokens.
4. Supabase creates its own session and redirects to **our** `/callback`, carrying the Microsoft `provider_token` and `provider_refresh_token` inside that session.
5. `CallbackPage` reads them off the session and POSTs them to `/api/auth/graph-tokens`, authenticated with the Supabase JWT.
6. The backend verifies the JWT, encrypts both Graph tokens with Fernet, upserts them into `ms_graph_tokens`, marks the profile `connected`, and kicks off a background sync so data starts arriving without blocking the response.
7. The browser lands on `/dashboard`.

**Verifying requests.** Every protected endpoint depends on `get_current_user`, which pulls Supabase's public signing keys from its JWKS endpoint, verifies the JWT's ES256 signature and checks `aud` is `authenticated`. No per-request network call to Supabase — that's the point of a JWT.

## How syncing works

Files: `backend/app/services/graph_sync.py`, `graph_client.py`, `graph_tokens_service.py`

`sync_user(pool, user_id)` is the entry point:

1. Get a valid access token via `get_valid_access_token`. If the stored one has expired, this transparently calls `refresh_and_persist`, which uses MSAL to redeem the refresh token and re-encrypts the result.
2. Run `sync_mail`, `sync_calendar` and `sync_chat`. Each one:
   - Loads its saved `delta_link` from `sync_state`, or starts from a backfill window on the first run (30 days back; calendar also looks 90 days forward).
   - Pages through the Graph delta endpoint, upserting each item and deleting anything Graph reports as `@removed`.
   - Saves the new `deltaLink` for next time.
   - If Graph returns 400/403 for a resource — say a mailbox with no licence for that feature — the resource is marked `not_available` and skipped from then on, rather than retried forever.
3. If any call returns 401 mid-sync, refresh once and retry that call.
4. Chain straight into extraction (`extract_user`), capped at `extraction_batch_limit`, so every sync also processes a bounded slice of the backlog.

**A delta query** is Graph's incremental sync: you save an opaque `deltaLink` URL, and hitting it later returns only what changed. A sync of an unchanged mailbox is nearly free instead of a full re-download.

Two triggers: the GitHub Actions cron every 15 minutes (`POST /api/sync/run`, all connected users), and the dashboard's **Sync now** button (`POST /api/sync/run/me`, just you).

## How extraction works

Files: `backend/app/services/ai_extraction.py`, `openai_client.py`

For each source table, `extract_user` finds rows where `extracted_at is null`, oldest first, and for each one:

1. **Builds plain text.** HTML is stripped; for calendar events the organiser and attendees are merged into one deduplicated participant list, collapsing by lowercased email and preferring whichever occurrence carries a display name (the organiser is nearly always an attendee too).
2. **Resolves identity — before the model runs.** Participants come from Graph's structured fields (`from_address`, `organizer`/`attendees`, `from_user`), are matched against existing contacts, and are handed to the LLM as opaque refs (`p0`, `p1`) alongside their current notes. Your own address is filtered out, so you never become your own contact.
3. **Calls the model** with a strict JSON Schema requiring `people` (a rewritten notes summary per ref) and `action_items` (`text`, `direction`, optional `due_date`, optional `participant_ref`).
4. **Writes in one transaction per item**: upsert each contact, insert each action item, stamp `extracted_at` on the source row. All or nothing.
5. If one item fails, it's skipped and the batch continues — a single malformed message can't block a user's whole run.

The important part is step 2. **The LLM decides what was said, never who was in the conversation.** Identity is settled deterministically from Graph's own fields, and the model can only map results back onto refs it was given — it cannot invent a person. Notes are *rewritten* each time rather than appended, so a contact's summary stays readable instead of growing into a log.

## How scheduling works

Files: `backend/app/services/scheduling.py`, `api/v1/scheduling.py`, `frontend/app/components/ScheduleActionItemPanel.tsx`

Only action items linked to a contact and not already scheduled can be scheduled.

1. **Suggesting times** (`GET /api/action-items/{id}/schedule-suggestions`): looks 14 days ahead, generates every 30-minute slot between 9am and 5pm on weekdays, drops anything overlapping an existing calendar event, returns up to 10.
2. **Booking** (`POST /api/action-items/{id}/schedule`): builds a Graph event payload (subject from the item text, optionally `isOnlineMeeting` with `teamsForBusiness` for a Teams link, the contact as an attendee if we have their email), creates it, saves the event locally and points the action item at it — inside one transaction.
3. **Race protection**: the pointer is set with a conditional `UPDATE ... WHERE scheduled_calendar_event_id IS NULL`. If two requests race, the loser's update matches no rows and the API returns 409 instead of silently overwriting the winner. The meeting the loser created on Graph still exists — that side effect can't be rolled back from inside a Postgres transaction — but the app reports the conflict rather than a false success.

## Data model

All tables live in `public`, created by the migrations in `supabase/migrations/` in filename order.

| Table | Purpose | Key columns |
|---|---|---|
| `profiles` | One row per signed-in user | `id` (= auth user id), `email`, `timezone`, `graph_connection_status` (`connected` / `needs_reauth` / `disconnected`) |
| `ms_graph_tokens` | Encrypted Graph tokens, one row per user | `encrypted_access_token`, `encrypted_refresh_token`, `access_token_expires_at`, `scopes` |
| `emails`, `calendar_events`, `chat_messages` | Raw synced data | Graph's own id for upsert/delete matching, `extracted_at` (null until processed) |
| `sync_state` | One row per user per resource | `delta_link`, `status` (`ok` / `not_available` / `error`) |
| `contacts` | People learned about | `email_address` or `display_name`, `notes` (LLM-synthesised) |
| `action_items` | Extracted commitments | `direction` (`mine`/`theirs`), `status`, `due_date`, `source_type`/`source_id`, `scheduled_calendar_event_id` |

Contact identity is enforced by two partial unique indexes: `(user_id, email_address)` where an email exists, and `(user_id, display_name)` where it doesn't. `action_items.source_id` is deliberately not a foreign key — it points into one of three tables depending on `source_type`.

RLS is enabled everywhere. `profiles` has real per-user policies; the rest have none, so only the service-role connection (which bypasses RLS) can reach them. The browser never queries Postgres directly, so this is defence in depth rather than the primary access control.

## API reference

Every route needs a valid Supabase JWT unless noted.

| Method & path | Purpose |
|---|---|
| `POST /api/auth/graph-tokens` | Save the Microsoft tokens from login; starts a background sync |
| `GET /api/me/graph-status` | Is Microsoft connected? Refreshes if needed, proves it with a Graph `/me` call |
| `GET /api/dashboard` | Counts plus a merged recent-activity feed |
| `GET /api/contacts?q=` | List / search contacts |
| `GET /api/contacts/{id}` | One contact's detail |
| `GET /api/contacts/{id}/action-items` | That contact's items |
| `GET /api/action-items?direction=&include_done=` | List action items |
| `PATCH /api/action-items/{id}` | Toggle open / done |
| `GET /api/action-items/{id}/schedule-suggestions` | Suggested meeting slots |
| `POST /api/action-items/{id}/schedule` | Book the meeting on Microsoft Calendar |
| `GET /api/search?q=` | Search contacts and action items together |
| `POST /api/sync/run/me` | Sync just the current user |
| `POST /api/sync/run` | **No JWT** — requires `X-Sync-Secret`, compared with `hmac.compare_digest`. Syncs every connected user. This is what the cron calls |
| `POST /api/extraction/run/me` | Run extraction for the current user, uncapped |
| `GET /health` | Liveness, no auth |

Frontend routes: `/` (redirect), `/login`, `/callback`, `/dashboard`, `/contacts`, `/contacts/view?id=`, `/planner`, `/search`.

## Two environments: local and deployed

The project runs in two self-consistent configurations, and they must never be
mixed.

| | Database + Auth | Backend | Frontend |
|---|---|---|---|
| **Local** | Supabase CLI on Docker | `uvicorn` on `:8000` | `next dev` on `:3000` |
| **Deployed** | Supabase cloud project | Render | Cloudflare Pages |

**Why they cannot be mixed.** `profiles.id` has a foreign key to
`auth.users(id)`, so the application database and the Auth service have to be the
same Supabase instance — a user id minted by cloud Auth violates the foreign key
against a local `auth.users`. JWT verification follows `SUPABASE_URL` for the
same reason. Switch `DATABASE_URL`, `SUPABASE_URL` and the service-role key
together, or not at all.

Templates for both are committed:

```
backend/.env.example          local values
backend/.env.cloud.example    cloud values (set in the Render dashboard)
frontend/.env.local.example   local values
frontend/.env.cloud.example   cloud values (set in Cloudflare Pages)
```

Start the local stack with:

```bash
npm install
npx supabase start --debug -x realtime,storage-api,imgproxy,mailpit,edge-runtime,logflare,vector,supavisor
npx supabase db reset     # replays every migration from empty
```

Two things that waste an hour if missed: pass `--debug` on the first run, or the
CLI buffers all output and looks hung while downloading; and one invalid name in
`-x` silently voids the whole exclusion list (it is `postgres-meta`, not
`pg-meta`).

## Running it locally

**Backend:**

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env            # fill in DATABASE_URL, SUPABASE_URL, secrets
uvicorn app.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
cp .env.local.example .env.local   # fill in the NEXT_PUBLIC_* values
npm run dev
```

Note that `NEXT_PUBLIC_*` variables are inlined into the JavaScript bundle **at build time**, not read at runtime — changing one in a hosting dashboard does nothing until the next deploy.

## Testing

```bash
cd backend && .venv/Scripts/python.exe -m pytest -q    # 173 tests
cd frontend && npm test                                # 108 tests
```

Built test-first throughout (the plans in `docs/` show the sequence): a failing test before the implementation, every time. Outbound HTTP to Graph and OpenAI is mocked with `respx`; the frontend mocks Supabase and `apiFetch` at the module boundary and asserts on behaviour — 401 redirects to login, a thrown fetch renders an inline error, the right query parameters go out.

## Design decisions and tradeoffs

**Why a static export instead of server rendering.** Every page fetches its own data client-side after load, so there's nothing for a Node server to render ahead of time. That makes the whole frontend a folder of files on a CDN — simpler and cheaper than running a server.

**Why encrypt the tokens.** Defence in depth. A leaked backup or a compromised read replica doesn't hand over live access to anyone's mailbox, because the tokens are useless without the separate `FERNET_KEY`.

**Why verify JWTs locally.** Checking the signature against Supabase's public JWKS keys avoids a network round trip on every single request, and means Supabase being slow doesn't make this app slow.

**Why identity resolution sits outside the LLM.** Asking a model "who are the people here?" produces hallucinated and near-duplicate contacts. Graph already knows exactly who was on the message, so identity is resolved deterministically first and the model only ever sees opaque refs. It interprets content; it never decides who exists.

**Why Teams contacts don't merge with Outlook ones.** Graph's chat payload gives a display name but no email address. Merging "Sarah Chen" from Teams into an email-keyed contact would be guessing, and a wrong merge here is unrecoverable — once the model has rewritten one summary across two people, you can't split them back apart. A visible duplicate is a worse-looking but far better-behaved failure than a silent false merge.

**Why sync also runs extraction.** One scheduled trigger drives the whole pipeline instead of two jobs whose relative timing has to be reasoned about. The batch limit keeps any single run bounded even against a large backlog.

**Why the bulk sync endpoint uses a shared secret.** There's no logged-in user in a cron job. The secret is compared with `hmac.compare_digest` so the comparison time doesn't leak the value.

## Known limitations

Honest about what this doesn't do yet:

- **Teams-only contacts key on display name**, so two different people sharing one would merge into a single contact. Accepted as better than dropping the information, but it's the first thing worth fixing.
- **Email extraction only considers the sender.** Recipients are synced and stored but not yet fed into extraction, so people you email without a reply don't become contacts.
- **`profiles.timezone` is never populated**, so suggested meeting slots are 9–5 UTC rather than 9–5 local. The scheduler reads the column correctly; nothing writes it yet.
- **No way to correct the AI.** You can mark an item done to dismiss it, but there's no edit, reject, or reassign flow, and no feedback signal from a correction.
- **The bulk sync is sequential.** One HTTP request iterates every connected user, which is fine at current scale and would need a real queue or bounded parallelism beyond it. The connection pool is also capped at 5.
- **Free-tier cold starts.** The backend spins down after about 15 minutes idle and takes tens of seconds to wake.
