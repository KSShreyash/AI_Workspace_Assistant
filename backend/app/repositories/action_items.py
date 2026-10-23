import uuid
from datetime import date

import asyncpg


class ActionItemsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def insert(
        self,
        user_id: uuid.UUID,
        contact_id: uuid.UUID | None,
        text: str,
        direction: str,
        due_date: date | None,
        source_type: str,
        source_id: uuid.UUID,
        category: str = "other",
        conn: asyncpg.Connection | None = None,
    ) -> None:
        executor = conn or self._pool
        await executor.execute(
            """
            insert into public.action_items
                (user_id, contact_id, text, direction, status, due_date,
                 source_type, source_id, category)
            values ($1, $2, $3, $4, 'open', $5, $6, $7, $8)
            """,
            user_id,
            contact_id,
            text,
            direction,
            due_date,
            source_type,
            source_id,
            category,
        )

    async def list_for_contact(
        self, user_id: uuid.UUID, contact_id: uuid.UUID
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select ai.*, ce.start_time as scheduled_start_time
            from public.action_items ai
            left join public.calendar_events ce on ce.id = ai.scheduled_calendar_event_id
            where ai.user_id = $1 and ai.contact_id = $2
            order by ai.created_at desc
            """,
            user_id,
            contact_id,
        )

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        direction: str | None = None,
        include_done: bool = False,
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select ai.*,
                   c.display_name as contact_display_name,
                   c.email_address as contact_email_address,
                   ce.start_time as scheduled_start_time
            from public.action_items ai
            left join public.contacts c on c.id = ai.contact_id
            left join public.calendar_events ce on ce.id = ai.scheduled_calendar_event_id
            where ai.user_id = $1
              and ($2::text is null or ai.direction = $2)
              and ($3::boolean or ai.status = 'open')
            order by ai.due_date asc nulls last, ai.created_at asc
            """,
            user_id,
            direction,
            include_done,
        )

    async def get(self, user_id: uuid.UUID, item_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            select ai.*,
                   c.display_name as contact_display_name,
                   c.email_address as contact_email_address,
                   ce.start_time as scheduled_start_time
            from public.action_items ai
            left join public.contacts c on c.id = ai.contact_id
            left join public.calendar_events ce on ce.id = ai.scheduled_calendar_event_id
            where ai.id = $1 and ai.user_id = $2
            """,
            item_id,
            user_id,
        )

    async def set_scheduled_calendar_event_id(
        self,
        user_id: uuid.UUID,
        item_id: uuid.UUID,
        calendar_event_id: uuid.UUID,
        conn: asyncpg.Connection | None = None,
    ) -> asyncpg.Record | None:
        executor = conn or self._pool
        return await executor.fetchrow(
            """
            with updated as (
                update public.action_items
                set scheduled_calendar_event_id = $3
                where id = $1 and user_id = $2 and scheduled_calendar_event_id is null
                returning *
            )
            select updated.*,
                   c.display_name as contact_display_name,
                   c.email_address as contact_email_address,
                   ce.start_time as scheduled_start_time
            from updated
            left join public.contacts c on c.id = updated.contact_id
            left join public.calendar_events ce on ce.id = updated.scheduled_calendar_event_id
            """,
            item_id,
            user_id,
            calendar_event_id,
        )

    async def update_status(
        self, user_id: uuid.UUID, item_id: uuid.UUID, status: str
    ) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            update public.action_items
            set status = $3, updated_at = now()
            where id = $1 and user_id = $2
            returning *
            """,
            item_id,
            user_id,
            status,
        )

    async def delete_completed(self, user_id: uuid.UUID) -> int:
        """Remove this user's finished tasks. Returns how many went.

        Scoped by user_id as well as status so a bug here cannot reach across
        accounts. Any calendar event a task was scheduled onto is deliberately
        left in place: the meeting is real and already in the user's calendar,
        and deleting the task that produced it should not imply otherwise.
        """
        result = await self._pool.execute(
            "delete from public.action_items where user_id = $1 and status = 'done'",
            user_id,
        )
        # asyncpg returns the command tag, e.g. "DELETE 3".
        return int(result.split()[-1]) if result else 0

    async def count_open(self, user_id: uuid.UUID) -> int:
        return await self._pool.fetchval(
            "select count(*) from public.action_items where user_id = $1 and status = 'open'",
            user_id,
        )

    async def list_recent(self, user_id: uuid.UUID, limit: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select * from public.action_items
            where user_id = $1
            order by created_at desc
            limit $2
            """,
            user_id,
            limit,
        )

    async def count(self, user_id: uuid.UUID) -> int:
        return await self._pool.fetchval(
            "select count(*) from public.action_items where user_id = $1",
            user_id,
        )

    async def search(
        self, user_id: uuid.UUID, query: str, limit: int
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select ai.*,
                   c.display_name as contact_display_name,
                   c.email_address as contact_email_address,
                   ts_rank(to_tsvector('english', ai.text), websearch_to_tsquery('english', $2)) as rank
            from public.action_items ai
            left join public.contacts c on c.id = ai.contact_id
            where ai.user_id = $1
              and to_tsvector('english', ai.text) @@ websearch_to_tsquery('english', $2)
            order by rank desc
            limit $3
            """,
            user_id,
            query,
            limit,
        )
