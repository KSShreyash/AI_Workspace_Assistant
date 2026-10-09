import uuid

import asyncpg

# Categories the extraction schema can produce. Listed here so every one appears
# in the response even at zero - a chart should not have to guess which slices
# exist, and "absent" and "none" are different things to a reader.
_CATEGORIES = ("work", "meeting", "follow_up", "other")

_SERIES_DAYS = 7
_UPCOMING_LIMIT = 5


class DashboardStatsRepository:
    """Aggregates for the dashboard, computed in the database.

    Every figure here was previously either unavailable or derived in the
    browser by downloading a whole collection and counting it, which does not
    survive real volume.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def _week_over_week(self, user_id: uuid.UUID, table: str, column: str) -> dict:
        # Two windows in one pass: the last seven days, and the seven before
        # that, so the dashboard can show direction of travel rather than a
        # bare number.
        row = await self._pool.fetchrow(
            f"""
            select
                count(*) filter (
                    where {column} >= now() - interval '7 days'
                ) as this_week,
                count(*) filter (
                    where {column} >= now() - interval '14 days'
                      and {column} <  now() - interval '7 days'
                ) as previous_week
            from public.{table}
            where user_id = $1
            """,
            user_id,
        )
        return {"this_week": row["this_week"], "previous_week": row["previous_week"]}

    async def email_counts(self, user_id: uuid.UUID) -> dict:
        return await self._week_over_week(user_id, "emails", "received_at")

    async def task_counts(self, user_id: uuid.UUID) -> dict:
        return await self._week_over_week(user_id, "action_items", "created_at")

    async def meeting_counts(self, user_id: uuid.UUID) -> dict:
        return await self._week_over_week(user_id, "calendar_events", "start_time")

    async def categories(self, user_id: uuid.UUID) -> dict:
        rows = await self._pool.fetch(
            """
            select category, count(*) as n
            from public.action_items
            where user_id = $1
            group by category
            """,
            user_id,
        )
        counts = {category: 0 for category in _CATEGORIES}
        for row in rows:
            # Defensive: a category added by a later migration should show up
            # rather than be silently dropped.
            counts[row["category"]] = row["n"]
        return counts

    async def important_people(self, user_id: uuid.UUID) -> int:
        """Contacts the user currently owes work to, or is owed work by.

        "Important" needs a definition the number can be defended with. Open
        work is the one signal the app already models, and it is the one that
        changes as the user works rather than only as mail arrives.
        """
        return await self._pool.fetchval(
            """
            select count(distinct ai.contact_id)
            from public.action_items ai
            where ai.user_id = $1
              and ai.status = 'open'
              and ai.contact_id is not null
            """,
            user_id,
        )

    async def daily_series(self, user_id: uuid.UUID) -> list[dict]:
        """Emails received and tasks created per day, for the last 7 days.

        Generated from a date series and left-joined, so days with no activity
        appear as zero. A line chart that simply omits empty days misrepresents
        the shape of the data.
        """
        rows = await self._pool.fetch(
            """
            with days as (
                select generate_series(
                    (now() at time zone 'utc')::date - ($2::int - 1),
                    (now() at time zone 'utc')::date,
                    interval '1 day'
                )::date as day
            ),
            email_counts as (
                select (received_at at time zone 'utc')::date as day, count(*) as n
                from public.emails
                where user_id = $1 and received_at is not null
                group by 1
            ),
            task_counts as (
                select (created_at at time zone 'utc')::date as day, count(*) as n
                from public.action_items
                where user_id = $1
                group by 1
            )
            select days.day,
                   coalesce(email_counts.n, 0) as emails,
                   coalesce(task_counts.n, 0) as tasks
            from days
            left join email_counts on email_counts.day = days.day
            left join task_counts on task_counts.day = days.day
            order by days.day
            """,
            user_id,
            _SERIES_DAYS,
        )
        return [
            {
                "date": row["day"].isoformat(),
                "emails": row["emails"],
                "tasks": row["tasks"],
            }
            for row in rows
        ]

    async def upcoming(self, user_id: uuid.UUID) -> list[dict]:
        """The next few dated, still-open tasks, nearest first.

        Ordered and limited in SQL. The previous dashboard fetched every action
        item ever created and sorted them in the browser to show five.
        """
        rows = await self._pool.fetch(
            """
            select ai.id, ai.text, ai.due_date, ai.category,
                   c.display_name as contact_display_name,
                   c.email_address as contact_email_address
            from public.action_items ai
            left join public.contacts c on c.id = ai.contact_id
            where ai.user_id = $1
              and ai.status = 'open'
              and ai.due_date is not null
            order by ai.due_date asc
            limit $2
            """,
            user_id,
            _UPCOMING_LIMIT,
        )
        return [
            {
                "id": str(row["id"]),
                "text": row["text"],
                "due_date": row["due_date"].isoformat(),
                "category": row["category"],
                "contact": row["contact_display_name"] or row["contact_email_address"],
            }
            for row in rows
        ]
