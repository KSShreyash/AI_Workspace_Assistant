import json
import uuid
from datetime import datetime

import asyncpg


class CalendarEventsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(
        self,
        user_id: uuid.UUID,
        graph_event_id: str,
        subject: str | None,
        organizer: str | None,
        attendees: list[dict],
        start_time: datetime | None,
        end_time: datetime | None,
        is_online_meeting: bool,
        online_meeting_join_url: str | None,
        body_text: str | None,
        conn: asyncpg.Connection | None = None,
    ) -> uuid.UUID:
        executor = conn or self._pool
        row = await executor.fetchrow(
            """
            insert into public.calendar_events
                (user_id, graph_event_id, subject, organizer, attendees,
                 start_time, end_time, is_online_meeting, online_meeting_join_url,
                 body_text, synced_at)
            values ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, now())
            on conflict (user_id, graph_event_id) do update
            set subject = excluded.subject,
                organizer = excluded.organizer,
                attendees = excluded.attendees,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                is_online_meeting = excluded.is_online_meeting,
                online_meeting_join_url = excluded.online_meeting_join_url,
                body_text = excluded.body_text,
                synced_at = now()
            returning id
            """,
            user_id,
            graph_event_id,
            subject,
            organizer,
            json.dumps(attendees),
            start_time,
            end_time,
            is_online_meeting,
            online_meeting_join_url,
            body_text,
        )
        return row["id"]

    async def delete(self, user_id: uuid.UUID, graph_event_id: str) -> None:
        await self._pool.execute(
            "delete from public.calendar_events where user_id = $1 and graph_event_id = $2",
            user_id,
            graph_event_id,
        )

    async def count(self, user_id: uuid.UUID) -> int:
        return await self._pool.fetchval(
            "select count(*) from public.calendar_events where user_id = $1",
            user_id,
        )

    async def list_busy_between(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select start_time, end_time from public.calendar_events
            where user_id = $1
              and start_time is not null and end_time is not null
              and start_time < $3 and end_time > $2
            """,
            user_id,
            start,
            end,
        )

    async def list_upcoming(self, user_id: uuid.UUID, limit: int = 50) -> list[asyncpg.Record]:
        """Events starting from now, soonest first.

        start_time is nullable - Graph can return an event whose start does not
        parse - and an event with no start cannot be placed on a calendar, so
        those are excluded rather than rendered at an arbitrary position.
        """
        return await self._pool.fetch(
            """
            select * from public.calendar_events
            where user_id = $1
              and start_time is not null
              and start_time >= now()
            order by start_time asc
            limit $2
            """,
            user_id,
            limit,
        )
