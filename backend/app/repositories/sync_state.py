import uuid

import asyncpg


class SyncStateRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get(self, account_id: uuid.UUID, resource_type: str) -> asyncpg.Record | None:
        """The cursor for one mailbox.

        Per account, not per user: a shared cursor would make one mailbox
        resume from wherever the other left off.
        """
        return await self._pool.fetchrow(
            "select * from public.sync_state where account_id = $1 and resource_type = $2",
            account_id,
            resource_type,
        )

    async def upsert(
        self,
        account_id: uuid.UUID,
        resource_type: str,
        delta_link: str | None,
        status: str,
    ) -> None:
        await self._pool.execute(
            """
            insert into public.sync_state (account_id, user_id, resource_type, delta_link, last_synced_at, status)
            select $1, a.user_id, $2, $3, now(), $4
            from public.connected_accounts a where a.id = $1
            on conflict (account_id, resource_type) do update
            set delta_link = excluded.delta_link,
                last_synced_at = now(),
                status = excluded.status
            """,
            account_id,
            resource_type,
            delta_link,
            status,
        )
