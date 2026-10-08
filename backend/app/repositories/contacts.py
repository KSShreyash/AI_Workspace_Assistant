import uuid

import asyncpg


class ContactsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_by_email(
        self, user_id: uuid.UUID, email_address: str, conn: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        executor = conn or self._pool
        return await executor.fetchrow(
            "select * from public.contacts where user_id = $1 and email_address = $2",
            user_id,
            email_address,
        )

    async def get_by_display_name(
        self, user_id: uuid.UUID, display_name: str, conn: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        executor = conn or self._pool
        return await executor.fetchrow(
            "select * from public.contacts where user_id = $1 and display_name = $2 and email_address is null",
            user_id,
            display_name,
        )

    async def upsert_by_email(
        self,
        user_id: uuid.UUID,
        email_address: str,
        display_name: str | None,
        notes: str | None,
        conn: asyncpg.Connection | None = None,
    ) -> uuid.UUID:
        executor = conn or self._pool
        row = await executor.fetchrow(
            """
            insert into public.contacts (user_id, email_address, display_name, notes, updated_at)
            values ($1, $2, $3, $4, now())
            on conflict (user_id, email_address) where email_address is not null do update
            set display_name = coalesce(excluded.display_name, public.contacts.display_name),
                -- coalesce, not a bare assignment: extraction re-upserts every
                -- participant on every message, so a pass that produces no notes
                -- must not erase what earlier messages established.
                notes = coalesce(excluded.notes, public.contacts.notes),
                updated_at = now()
            returning id
            """,
            user_id,
            email_address,
            display_name,
            notes,
        )
        return row["id"]

    async def upsert_by_display_name(
        self,
        user_id: uuid.UUID,
        display_name: str,
        notes: str | None,
        conn: asyncpg.Connection | None = None,
    ) -> uuid.UUID:
        executor = conn or self._pool
        row = await executor.fetchrow(
            """
            insert into public.contacts (user_id, email_address, display_name, notes, updated_at)
            values ($1, null, $2, $3, now())
            on conflict (user_id, display_name) where email_address is null do update
            set notes = coalesce(excluded.notes, public.contacts.notes),
                updated_at = now()
            returning id
            """,
            user_id,
            display_name,
            notes,
        )
        return row["id"]

    async def get(
        self, user_id: uuid.UUID, contact_id: uuid.UUID
    ) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "select * from public.contacts where id = $1 and user_id = $2",
            contact_id,
            user_id,
        )

    async def list_for_user(
        self, user_id: uuid.UUID, search: str | None = None
    ) -> list[asyncpg.Record]:
        pattern = f"%{search}%" if search else None
        return await self._pool.fetch(
            """
            select c.*,
                   (
                       select count(*) from public.action_items ai
                       where ai.contact_id = c.id and ai.status = 'open'
                   ) as open_action_item_count
            from public.contacts c
            where c.user_id = $1
              and ($2::text is null or c.display_name ilike $2 or c.email_address ilike $2)
            order by c.updated_at desc
            """,
            user_id,
            pattern,
        )

    async def list_recent(self, user_id: uuid.UUID, limit: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select * from public.contacts
            where user_id = $1
            order by updated_at desc
            limit $2
            """,
            user_id,
            limit,
        )

    async def count(self, user_id: uuid.UUID) -> int:
        return await self._pool.fetchval(
            "select count(*) from public.contacts where user_id = $1",
            user_id,
        )

    async def search(
        self, user_id: uuid.UUID, query: str, limit: int
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            select *,
                   ts_rank(
                       to_tsvector('english',
                           coalesce(display_name, '') || ' ' ||
                           replace(coalesce(email_address, ''), '@', ' ') || ' ' ||
                           coalesce(notes, '')
                       ),
                       websearch_to_tsquery('english', $2)
                   ) as rank
            from public.contacts
            where user_id = $1
              and to_tsvector('english',
                      coalesce(display_name, '') || ' ' ||
                      replace(coalesce(email_address, ''), '@', ' ') || ' ' ||
                      coalesce(notes, '')
                  ) @@ websearch_to_tsquery('english', $2)
            order by rank desc
            limit $3
            """,
            user_id,
            query,
            limit,
        )
