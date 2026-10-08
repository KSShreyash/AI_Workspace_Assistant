-- Bound extraction retries so a message that can never be extracted drains out
-- of the queue instead of occupying it forever.
--
-- The pending query is `where extracted_at is null order by <time> asc limit N`.
-- Failures previously left extracted_at null with nothing recorded, so a failing
-- message was re-fetched on every run, and enough of them permanently filled the
-- batch window and starved newer mail. Under a daily token budget that also
-- meant the entire quota was spent on messages that could not succeed.

alter table public.emails
  add column extraction_attempts integer not null default 0,
  add column extraction_error text;

alter table public.calendar_events
  add column extraction_attempts integer not null default 0,
  add column extraction_error text;

alter table public.chat_messages
  add column extraction_attempts integer not null default 0,
  add column extraction_error text;
