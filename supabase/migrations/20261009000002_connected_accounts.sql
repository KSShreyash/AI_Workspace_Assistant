-- Several mailboxes per user, across providers.
--
-- ms_graph_tokens had `user_id uuid primary key`, which allowed exactly one
-- Microsoft account per person. That key, not any application logic, is what
-- made the product single-account.
--
-- Tokens remain Fernet-encrypted at rest and the table stays service-role only,
-- exactly as before. This changes how many rows there can be, not how they are
-- protected.

create table public.connected_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  provider text not null check (provider in ('microsoft', 'google')),
  -- The provider's own stable id for the account (Graph user id, Google sub).
  -- Email alone is not safe: it can be reassigned or aliased.
  provider_account_id text not null,
  email_address text not null,
  display_name text,
  encrypted_access_token text not null,
  encrypted_refresh_token text not null,
  access_token_expires_at timestamptz not null,
  scopes text[] not null default '{}',
  -- Per account, not per user. One expired mailbox must not mark the others as
  -- needing reauth, which is exactly what profiles.graph_connection_status did.
  connection_status text not null default 'connected'
    check (connection_status in ('connected', 'needs_reauth', 'disconnected')),
  -- Calendar and Teams chat follow one account, since Gmail has no Teams
  -- equivalent and a user's "main" calendar is a single thing.
  is_primary boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Scoped by user: two colleagues connecting the same shared inbox are two
  -- independent connections, not a conflict.
  unique (user_id, provider, provider_account_id)
);

alter table public.connected_accounts enable row level security;
-- Intentionally no policies: service_role only, consistent with ms_graph_tokens
-- and every other server-owned table here.

-- At most one primary per user. A partial unique index rather than a trigger,
-- so the database enforces it rather than trusting every write path.
create unique index connected_accounts_one_primary
  on public.connected_accounts (user_id)
  where is_primary;

create index connected_accounts_user_idx
  on public.connected_accounts (user_id);

-- Drives the bulk sync cron, replacing profiles.graph_connection_status.
create index connected_accounts_syncable_idx
  on public.connected_accounts (connection_status)
  where connection_status = 'connected';

-- Carry across any existing single-account connections. Marked primary because
-- they were, by construction, the only account the user had.
insert into public.connected_accounts (
  user_id, provider, provider_account_id, email_address,
  encrypted_access_token, encrypted_refresh_token,
  access_token_expires_at, scopes, connection_status, is_primary,
  created_at, updated_at
)
select
  t.user_id,
  'microsoft',
  -- No provider account id was ever stored, so the user id stands in. It is
  -- unique per row and stable, which is all the constraint needs; the real id
  -- lands on the next reconnect.
  t.user_id::text,
  coalesce(p.email, ''),
  t.encrypted_access_token,
  t.encrypted_refresh_token,
  t.access_token_expires_at,
  t.scopes,
  case p.graph_connection_status
    when 'needs_reauth' then 'needs_reauth'
    when 'disconnected' then 'disconnected'
    else 'connected'
  end,
  true,
  t.created_at,
  t.updated_at
from public.ms_graph_tokens t
join public.profiles p on p.id = t.user_id
on conflict (user_id, provider, provider_account_id) do nothing;
