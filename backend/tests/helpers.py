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
    # Matches the account conftest already connects for every test user, so the
    # default call updates that mailbox rather than adding a second one. Tests
    # that actually want two pass an explicit id.
    provider_account_id: str = "fixture-account",
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


async def primary_account_id(pool: asyncpg.Pool, user_id: uuid.UUID) -> uuid.UUID:
    """The mailbox the conftest fixture connects for every test user."""
    return await pool.fetchval(
        "select id from public.connected_accounts where user_id = $1 and is_primary",
        user_id,
    )
