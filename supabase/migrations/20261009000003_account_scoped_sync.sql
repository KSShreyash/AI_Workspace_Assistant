-- Attribute synced data to the mailbox it came from.
--
-- Three problems with keying on user_id alone once a user has two mailboxes:
-- the Emails page cannot say which inbox a message arrived in; two mailboxes
-- that mint the same message id silently overwrite each other; and both share a
-- single sync cursor, so one resumes from the other's position.
--
-- account_id is added nullable, backfilled, then made NOT NULL. Adding it NOT
-- NULL in one step fails on any table that already has rows.

-- ---------------------------------------------------------------------------
-- 1. Add nullable
-- ---------------------------------------------------------------------------

alter table public.emails
  add column account_id uuid references public.connected_accounts(id) on delete cascade;

alter table public.calendar_events
  add column account_id uuid references public.connected_accounts(id) on delete cascade;

alter table public.chat_messages
  add column account_id uuid references public.connected_accounts(id) on delete cascade;

alter table public.sync_state
  add column account_id uuid references public.connected_accounts(id) on delete cascade;

-- ---------------------------------------------------------------------------
-- 2. Backfill from the user's primary account
--
-- Everything synced so far came through the single Microsoft connection that
-- migration 20261009000002 carried into connected_accounts and marked primary.
-- ---------------------------------------------------------------------------

update public.emails e
set account_id = a.id
from public.connected_accounts a
where a.user_id = e.user_id and a.is_primary and e.account_id is null;

update public.calendar_events c
set account_id = a.id
from public.connected_accounts a
where a.user_id = c.user_id and a.is_primary and c.account_id is null;

update public.chat_messages m
set account_id = a.id
from public.connected_accounts a
where a.user_id = m.user_id and a.is_primary and m.account_id is null;

update public.sync_state s
set account_id = a.id
from public.connected_accounts a
where a.user_id = s.user_id and a.is_primary and s.account_id is null;

-- Any row whose user had no connected account is unattributable - it can only
-- have come from a connection since removed. Deleting is correct: it cannot be
-- synced, refreshed, or shown against a mailbox.
delete from public.emails where account_id is null;
delete from public.calendar_events where account_id is null;
delete from public.chat_messages where account_id is null;
delete from public.sync_state where account_id is null;

-- ---------------------------------------------------------------------------
-- 3. Enforce
-- ---------------------------------------------------------------------------

alter table public.emails alter column account_id set not null;
alter table public.calendar_events alter column account_id set not null;
alter table public.chat_messages alter column account_id set not null;
alter table public.sync_state alter column account_id set not null;

-- ---------------------------------------------------------------------------
-- Re-key on the account, and drop the Microsoft-specific column names.
-- "graph_" was accurate when Graph was the only source; it is not any more.
-- ---------------------------------------------------------------------------

alter table public.emails drop constraint emails_user_id_graph_message_id_key;
alter table public.emails rename column graph_message_id to provider_message_id;
alter table public.emails add unique (account_id, provider_message_id);

alter table public.calendar_events drop constraint calendar_events_user_id_graph_event_id_key;
alter table public.calendar_events rename column graph_event_id to provider_event_id;
alter table public.calendar_events add unique (account_id, provider_event_id);

alter table public.chat_messages
  drop constraint chat_messages_user_id_graph_chat_id_graph_message_id_key;
alter table public.chat_messages rename column graph_chat_id to provider_chat_id;
alter table public.chat_messages rename column graph_message_id to provider_message_id;
alter table public.chat_messages add unique (account_id, provider_chat_id, provider_message_id);

-- sync_state's primary key moves from the user to the account, so each mailbox
-- keeps its own cursor.
alter table public.sync_state drop constraint sync_state_pkey;
alter table public.sync_state add primary key (account_id, resource_type);
-- user_id stays as a convenience for user-scoped reads, but is no longer part
-- of any key.

-- ---------------------------------------------------------------------------
-- Indexes for the new access paths. The pending-extraction indexes from
-- 20261009000001 still apply: they are keyed on user_id, which is unchanged.
-- ---------------------------------------------------------------------------

create index emails_account_idx on public.emails (account_id);
create index calendar_events_account_idx on public.calendar_events (account_id);
create index chat_messages_account_idx on public.chat_messages (account_id);
