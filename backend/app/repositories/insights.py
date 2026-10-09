import uuid

import asyncpg

_TOP_N = 5


class InsightsRepository:
    """Questions the existing schema can answer about how work is flowing."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def _open_items_by_contact(self, user_id: uuid.UUID, direction: str) -> list[dict]:
        rows = await self._pool.fetch(
            """
            select coalesce(c.display_name, c.email_address) as contact,
                   count(*) as n
            from public.action_items ai
            join public.contacts c on c.id = ai.contact_id
            where ai.user_id = $1
              and ai.status = 'open'
              and ai.direction = $2
            group by 1
            order by n desc, contact asc
            limit $3
            """,
            user_id,
            direction,
            _TOP_N,
        )
        return [{"contact": row["contact"], "count": row["n"]} for row in rows]

    async def you_owe(self, user_id: uuid.UUID) -> list[dict]:
        """Open items the user committed to. 'mine' means the user's to do."""
        return await self._open_items_by_contact(user_id, "mine")

    async def owed_to_you(self, user_id: uuid.UUID) -> list[dict]:
        return await self._open_items_by_contact(user_id, "theirs")

    async def top_senders(self, user_id: uuid.UUID) -> list[dict]:
        rows = await self._pool.fetch(
            """
            select from_address as sender, count(*) as n
            from public.emails
            where user_id = $1 and from_address is not null
            group by 1
            order by n desc, sender asc
            limit $2
            """,
            user_id,
            _TOP_N,
        )
        return [{"sender": row["sender"], "count": row["n"]} for row in rows]

    async def pipeline(self, user_id: uuid.UUID) -> dict:
        """How much mail got through extraction, and what happened to the rest.

        Four outcomes, not two. Readable only because extraction records why it
        skipped a message and why it gave up on one, rather than silently
        moving on - a skipped newsletter and a message that failed three times
        are very different signals.
        """
        row = await self._pool.fetchrow(
            """
            select
                count(*) filter (
                    where extracted_at is not null
                      and (extraction_error is null or extraction_error not like 'skipped:%')
                ) as extracted,
                count(*) filter (where extraction_error like 'skipped:%') as skipped,
                count(*) filter (
                    where extracted_at is null and extraction_attempts > 0
                ) as failed,
                count(*) filter (
                    where extracted_at is null and extraction_attempts = 0
                ) as pending
            from public.emails
            where user_id = $1
            """,
            user_id,
        )
        return dict(row)
