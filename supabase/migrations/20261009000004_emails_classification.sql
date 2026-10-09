-- The provider's own bulk verdict, stored so extraction can honour it.
--
-- extraction_filter has always documented a tier-2 rule that skips mail the
-- provider already classified as non-focused, but the rule was unreachable:
-- nothing stored the verdict, so every promotional mail still cost a full LLM
-- call. On a free tier where tokens per day is the binding limit, that is the
-- difference between a real inbox fitting in the budget and not.
--
-- Both providers supply this at no extra request cost: Microsoft returns
-- inferenceClassification on messages by default, and Gmail's CATEGORY_*
-- labels arrive with the message. Values are normalised to Microsoft's
-- vocabulary ('focused' / 'other') at the client boundary so the filter needs
-- no provider branch.
--
-- Nullable with no default and no backfill, deliberately: null means "the
-- provider never told us", and the filter treats unknown as worth extracting.
-- Defaulting to 'other' would silently drop every row synced before this
-- column existed.

alter table public.emails
    add column if not exists classification text;

comment on column public.emails.classification is
    'Provider bulk-mail verdict, normalised to focused/other. Null = unknown.';
