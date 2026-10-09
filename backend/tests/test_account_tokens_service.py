"""Token refresh, per connected account.

The unit changes from user to account. A user can now have several mailboxes,
so "refresh the user's token" is no longer a well-formed request - and a failure
on one must not disturb the others.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.security import decrypt_token, encrypt_token
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.profiles import ProfilesRepository
from app.services.account_tokens import (
    get_primary_account,
    get_valid_access_token,
    refresh_and_persist,
)
from app.services.graph_client import GraphRefreshError


async def _connect(pool, user_id, email, *, provider="microsoft", pid="ms-1", expires_in_hours=1):
    repo = ConnectedAccountsRepository(pool)
    # conftest connects a mailbox for every test user. These tests assert on
    # which account is selected, so they start from an empty set.
    await pool.execute(
        "delete from public.connected_accounts where user_id = $1 and provider_account_id = 'fixture-account'",
        user_id,
    )
    account_id = await repo.upsert(
        user_id=user_id,
        provider=provider,
        provider_account_id=pid,
        email_address=email,
        encrypted_access_token=encrypt_token("live-access"),
        encrypted_refresh_token=encrypt_token("live-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
        scopes=["Mail.Read"],
    )
    return await repo.get(user_id, account_id)


@pytest.mark.asyncio
async def test_a_live_token_is_returned_without_contacting_the_provider(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account = await _connect(pool, user_id, email)

    with patch("app.services.account_tokens.graph_refresh_access_token") as refresh:
        token = await get_valid_access_token(pool, account)

    assert token == "live-access"
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_a_token_expiring_imminently_is_refreshed_early(pool, test_auth_user):
    """A token with seconds left passes a naive expiry check, then 401s mid-call.

    Refreshing just before expiry costs one request and removes a whole class of
    spurious reauth prompts.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ConnectedAccountsRepository(pool)
    account_id = await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address=email,
        encrypted_access_token=encrypt_token("nearly-stale"),
        encrypted_refresh_token=encrypt_token("r"),
        # Not yet expired, but will be before a round trip completes.
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        scopes=[],
    )
    account = await repo.get(user_id, account_id)

    with patch(
        "app.services.account_tokens.graph_refresh_access_token",
        return_value={
            "access_token": "fresh",
            "refresh_token": "r2",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    ):
        token = await get_valid_access_token(pool, account)

    assert token == "fresh"


@pytest.mark.asyncio
async def test_a_refreshed_token_is_persisted_encrypted(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account = await _connect(pool, user_id, email, expires_in_hours=-1)

    with patch(
        "app.services.account_tokens.graph_refresh_access_token",
        return_value={
            "access_token": "fresh",
            "refresh_token": "fresh-refresh",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    ):
        await get_valid_access_token(pool, account)

    stored = await ConnectedAccountsRepository(pool).get(user_id, account["id"])
    assert decrypt_token(stored["encrypted_access_token"]) == "fresh"
    assert decrypt_token(stored["encrypted_refresh_token"]) == "fresh-refresh"


@pytest.mark.asyncio
async def test_a_failed_refresh_marks_only_that_account(pool, test_auth_user):
    """The whole point of per-account status: one bad mailbox, one bad status."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    bad = await _connect(pool, user_id, email, pid="ms-bad", expires_in_hours=-1)
    good = await _connect(pool, user_id, email, provider="google", pid="g-good")

    with patch(
        "app.services.account_tokens.graph_refresh_access_token",
        side_effect=GraphRefreshError("consent revoked"),
    ):
        token = await refresh_and_persist(pool, bad)

    assert token is None
    repo = ConnectedAccountsRepository(pool)
    assert (await repo.get(user_id, bad["id"]))["connection_status"] == "needs_reauth"
    assert (await repo.get(user_id, good["id"]))["connection_status"] == "connected"


@pytest.mark.asyncio
async def test_get_primary_account_returns_the_calendar_account(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    first = await _connect(pool, user_id, email, pid="ms-1")
    await _connect(pool, user_id, email, provider="google", pid="g-1")

    primary = await get_primary_account(pool, user_id)
    assert primary["id"] == first["id"]


@pytest.mark.asyncio
async def test_get_primary_account_is_none_when_nothing_is_connected(pool, no_accounts, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    assert await get_primary_account(pool, user_id) is None


@pytest.mark.asyncio
async def test_refreshing_an_account_that_has_been_removed_is_not_an_error(pool, test_auth_user):
    """A sync in flight when the user disconnects a mailbox must not blow up."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account = await _connect(pool, user_id, email)
    await ConnectedAccountsRepository(pool).delete(user_id, account["id"])

    with patch(
        "app.services.account_tokens.graph_refresh_access_token",
        return_value={
            "access_token": "fresh",
            "refresh_token": "r",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    ):
        # The row is gone; nothing to persist to, and no exception either.
        assert await refresh_and_persist(pool, account) is None
