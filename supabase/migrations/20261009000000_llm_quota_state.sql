-- Remember when the LLM provider's quota is spent, so extraction stops calling
-- it instead of retrying into a closed door.
--
-- The free tier caps tokens per day and a daily exhaustion lasts hours, while
-- the sync cron fires every fifteen minutes. Without this the app spends all
-- night issuing requests that can only fail, burning the request allowance on
-- top of the token one.
--
-- Keyed by provider rather than by user on purpose: Groq applies its limits
-- organisation-wide, so one user hitting the cap means every user has.
--
-- Not stored in sync_state: that table's resource_type carries a check
-- constraint limiting it to mail, calendar and chat, and widening an unrelated
-- table's domain to hold this would be worse than a small dedicated one.

create table public.llm_quota_state (
  provider text primary key,
  exhausted_until timestamptz,
  updated_at timestamptz not null default now()
);

alter table public.llm_quota_state enable row level security;
-- Intentionally no policies: only the service_role connection (bypasses RLS)
-- may touch this table, consistent with every other server-owned table here.
