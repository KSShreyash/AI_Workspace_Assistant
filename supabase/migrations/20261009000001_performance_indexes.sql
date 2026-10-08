-- Indexes for the columns every query actually filters on.
--
-- action_items had no index but its primary key, despite being read by the
-- planner, the dashboard, search and every contact profile. contacts had only
-- two PARTIAL unique indexes (one for rows with an email address, one for rows
-- without), and Postgres can only use a partial index when the query implies
-- its predicate - so the list, count, recent and search queries, which filter
-- on user_id alone, all fell back to sequential scans.
--
-- Measured before this migration on 1,000 contacts and 10,000 action items,
-- the contacts page ran a sequential scan of action_items once per contact
-- row - loops=1000 - for 572 ms.

-- Every action_items query filters on user_id; most also filter on status.
create index if not exists action_items_user_status_idx
  on public.action_items (user_id, status);

-- Supports both the open-item count per contact and the on-delete-set-null
-- cascade when a contact is removed.
create index if not exists action_items_contact_idx
  on public.action_items (contact_id)
  where contact_id is not null;

-- Same for the cascade from calendar_events.
create index if not exists action_items_scheduled_event_idx
  on public.action_items (scheduled_calendar_event_id)
  where scheduled_calendar_event_id is not null;

-- Ordering for the planner and dashboard.
create index if not exists action_items_user_created_idx
  on public.action_items (user_id, created_at desc);

-- contacts: a plain, non-partial index so user_id-only filters can use it.
create index if not exists contacts_user_updated_idx
  on public.contacts (user_id, updated_at desc);

-- The extraction queue: "pending and not exhausted, oldest first". Partial, so
-- it stays small - it only contains rows still awaiting extraction.
create index if not exists emails_pending_extraction_idx
  on public.emails (user_id, received_at)
  where extracted_at is null;

create index if not exists calendar_events_pending_extraction_idx
  on public.calendar_events (user_id, start_time)
  where extracted_at is null;

create index if not exists chat_messages_pending_extraction_idx
  on public.chat_messages (user_id, sent_at)
  where extracted_at is null;

-- Full-text search currently recomputes to_tsvector for every row at query
-- time. GIN indexes over the same expressions let Postgres use the index
-- instead; the expressions must match the queries exactly.
create index if not exists action_items_text_fts_idx
  on public.action_items
  using gin (to_tsvector('english', text));

create index if not exists contacts_fts_idx
  on public.contacts
  using gin (
    to_tsvector(
      'english',
      coalesce(display_name, '') || ' ' ||
      replace(coalesce(email_address, ''), '@', ' ') || ' ' ||
      coalesce(notes, '')
    )
  );
