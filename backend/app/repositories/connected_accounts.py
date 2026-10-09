import uuid
from datetime import datetime

import asyncpg


class ConnectedAccountsRepository:
    """Mailboxes a user has connected, across providers.

    Replaces GraphTokensRepository, whose table allowed one Microsoft account
    per user. Tokens are still stored encrypted by the caller; this layer never
    sees plaintext.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(
        self,
        user_id: uuid.UUID,
        provider: str,
        provider_account_id: str,
        email_address: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str,
        access_token_expires_at: datetime,
        scopes: list[str],
        display_name: str | None = None,
        is_primary: bool = False,
    ) -> uuid.UUID:
        """Connect a mailbox, or refresh the tokens of one already connected.

        Returns the account id either way, so callers can attach synced rows to
        it without a second lookup.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if is_primary:
                    # One primary per user is enforced by a partial unique
                    # index, so the old one has to be stood down first rather
                    # than relying on the insert to resolve it.
                    await conn.execute(
                        "update public.connected_accounts set is_primary = false, "
                        "updated_at = now() where user_id = $1 and is_primary",
                        user_id,
                    )
                elif not await conn.fetchval(
                    "select exists (select 1 from public.connected_accounts where user_id = $1)",
                    user_id,
                ):
                    # The first mailbox becomes primary whether or not the
                    # caller asked: calendar and chat need one, and leaving a
                    # single-account user with none would silently disable them.
                    is_primary = True

                return await conn.fetchval(
                    """
                    insert into public.connected_accounts (
                        user_id, provider, provider_account_id, email_address, display_name,
                        encrypted_access_token, encrypted_refresh_token,
                        access_token_expires_at, scopes, is_primary, updated_at
                    )
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now())
                    on conflict (user_id, provider, provider_account_id) do update
                    set email_address = excluded.email_address,
                        display_name = coalesce(excluded.display_name, public.connected_accounts.display_name),
                        encrypted_access_token = excluded.encrypted_access_token,
                        encrypted_refresh_token = excluded.encrypted_refresh_token,
                        access_token_expires_at = excluded.access_token_expires_at,
                        scopes = excluded.scopes,
                        -- is_primary=False means "not asking", not "demote".
                        -- Refreshing a mailbox's tokens must not quietly strip
                        -- it of primary status and leave the user with no
                        -- calendar. Demotion happens only when another account
                        -- is explicitly promoted, which the branch above does.
                        is_primary = excluded.is_primary or public.connected_accounts.is_primary,
                        -- Reconnecting is how a user fixes an expired mailbox,
                        -- so it has to clear needs_reauth.
                        connection_status = 'connected',
                        updated_at = now()
                    returning id
                    """,
                    user_id,
                    provider,
                    provider_account_id,
                    email_address,
                    display_name,
                    encrypted_access_token,
                    encrypted_refresh_token,
                    access_token_expires_at,
                    scopes,
                    is_primary,
                )

    async def get(self, user_id: uuid.UUID, account_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "select * from public.connected_accounts where id = $1 and user_id = $2",
            account_id,
            user_id,
        )

    async def list_for_user(self, user_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            "select * from public.connected_accounts where user_id = $1 "
            "order by is_primary desc, created_at asc",
            user_id,
        )

    async def get_primary(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        """The account calendar and Teams chat sync from."""
        return await self._pool.fetchrow(
            "select * from public.connected_accounts where user_id = $1 and is_primary",
            user_id,
        )

    async def set_status(
        self, user_id: uuid.UUID, account_id: uuid.UUID, status: str
    ) -> None:
        await self._pool.execute(
            "update public.connected_accounts set connection_status = $3, updated_at = now() "
            "where id = $1 and user_id = $2",
            account_id,
            user_id,
            status,
        )

    async def update_tokens(
        self,
        account_id: uuid.UUID,
        encrypted_access_token: str,
        encrypted_refresh_token: str,
        access_token_expires_at: datetime,
    ) -> None:
        await self._pool.execute(
            """
            update public.connected_accounts
            set encrypted_access_token = $2,
                encrypted_refresh_token = $3,
                access_token_expires_at = $4,
                connection_status = 'connected',
                updated_at = now()
            where id = $1
            """,
            account_id,
            encrypted_access_token,
            encrypted_refresh_token,
            access_token_expires_at,
        )

    async def delete(self, user_id: uuid.UUID, account_id: uuid.UUID) -> None:
        await self._pool.execute(
            "delete from public.connected_accounts where id = $1 and user_id = $2",
            account_id,
            user_id,
        )

    async def list_syncable(self) -> list[asyncpg.Record]:
        """Every account the bulk cron should sync, across all users.

        Replaces profiles.list_connected. The unit of sync is an account, not a
        user - a user with a working Outlook and an expired Gmail should still
        have their Outlook synced.
        """
        return await self._pool.fetch(
            "select * from public.connected_accounts "
            "where connection_status = 'connected' "
            "order by user_id, is_primary desc"
        )
