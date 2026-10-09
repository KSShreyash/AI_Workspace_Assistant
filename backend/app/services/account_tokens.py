import logging
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from starlette.concurrency import run_in_threadpool

from app.core.security import decrypt_token, encrypt_token
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.services.graph_client import GraphRefreshError, refresh_access_token

logger = logging.getLogger(__name__)

# Refresh this far ahead of the stated expiry. Without it a token with seconds
# left passes the check and then 401s mid-call, which the caller reads as a
# revoked connection and surfaces as a spurious "reconnect your account".
_EXPIRY_SKEW = timedelta(seconds=60)


async def get_primary_account(
    pool: asyncpg.Pool, user_id: uuid.UUID
) -> asyncpg.Record | None:
    """The account calendar and Teams chat sync from."""
    return await ConnectedAccountsRepository(pool).get_primary(user_id)


async def refresh_and_persist(pool: asyncpg.Pool, account: asyncpg.Record) -> str | None:
    """Redeem the stored refresh token for this one account.

    Returns the new access token, or None if the account can no longer be
    refreshed - in which case it is marked needs_reauth. Only this account is
    marked: a user's other mailboxes are unaffected, which is the whole reason
    status lives on the account rather than the profile.
    """
    repo = ConnectedAccountsRepository(pool)

    try:
        refreshed = await run_in_threadpool(
            refresh_access_token,
            decrypt_token(account["encrypted_refresh_token"]),
            scopes=list(account["scopes"]),
        )
    except GraphRefreshError as exc:
        logger.warning(
            "Refresh failed for %s account %s: %s", account["provider"], account["id"], exc
        )
        await repo.set_status(account["user_id"], account["id"], "needs_reauth")
        return None

    # The account may have been disconnected while this was in flight; the
    # update then matches nothing, which is the correct outcome rather than an
    # error. Re-check so a vanished row does not look like a successful refresh.
    if await repo.get(account["user_id"], account["id"]) is None:
        logger.info("Account %s disconnected during refresh; discarding token", account["id"])
        return None

    await repo.update_tokens(
        account_id=account["id"],
        encrypted_access_token=encrypt_token(refreshed["access_token"]),
        encrypted_refresh_token=encrypt_token(refreshed["refresh_token"]),
        access_token_expires_at=refreshed["expires_at"],
    )
    return refreshed["access_token"]


async def get_valid_access_token(
    pool: asyncpg.Pool, account: asyncpg.Record
) -> str | None:
    """A usable access token for this account, refreshing first if needed."""
    if account["access_token_expires_at"] - _EXPIRY_SKEW <= datetime.now(timezone.utc):
        return await refresh_and_persist(pool, account)

    return decrypt_token(account["encrypted_access_token"])
