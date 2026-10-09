"""Shared test setup for connected mailboxes.

Deliberately mirrors the keyword signature the old GraphTokensRepository.upsert
had, so porting the suite to multi-account was a mechanical substitution rather
than a rewrite of every arrangement block. Tests that care about multi-account
behaviour pass provider/provider_account_id explicitly.
"""

import uuid
from datetime import datetime

import asyncpg

from app.repositories.connected_accounts import ConnectedAccountsRepository


async def connect_account(
    pool: asyncpg.Pool,
    *,
    user_id: uuid.UUID,
    encrypted_access_token: str,
    encrypted_refresh_token: str,
    access_token_expires_at: datetime,
    scopes: list[str] | None = None,
    provider: str = "microsoft",
    provider_account_id: str = "test-account",
    email_address: str = "test@example.com",
    is_primary: bool = False,
) -> uuid.UUID:
    return await ConnectedAccountsRepository(pool).upsert(
        user_id=user_id,
        provider=provider,
        provider_account_id=provider_account_id,
        email_address=email_address,
        encrypted_access_token=encrypted_access_token,
        encrypted_refresh_token=encrypted_refresh_token,
        access_token_expires_at=access_token_expires_at,
        scopes=scopes or [],
        is_primary=is_primary,
    )


async def get_connected_account(
    pool: asyncpg.Pool, user_id: uuid.UUID
) -> asyncpg.Record | None:
    """The user's primary account - what a single-account test means by 'the' account."""
    return await ConnectedAccountsRepository(pool).get_primary(user_id)
