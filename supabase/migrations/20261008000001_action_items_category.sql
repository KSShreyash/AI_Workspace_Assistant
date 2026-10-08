-- Category for each action item, driving the dashboard's task breakdown.
--
-- Added as part of the extraction schema rather than when the chart is built.
-- The value comes from the model, so introducing it later would require
-- re-extracting every stored message to backfill the column - on a free tier
-- that is a full day of token quota and several days of wall clock.
--
-- Defaults to 'other' so existing rows remain valid and any future writer that
-- omits it still satisfies the constraint.

alter table public.action_items
  add column category text not null default 'other'
    check (category in ('work', 'meeting', 'follow_up', 'other'));
