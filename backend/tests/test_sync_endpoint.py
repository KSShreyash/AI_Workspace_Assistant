import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.core.config import settings
from app.core.security import encrypt_token
from app.main import app
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.profiles import ProfilesRepository
from tests.helpers import connect_account


@pytest.mark.asyncio
async def test_run_me_calls_sync_user_for_current_user(pool, test_auth_user):
    user_id, email = test_auth_user
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    try:
        with patch("app.api.v1.sync.sync_user", new=AsyncMock()) as mock_sync_user:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/sync/run/me")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_sync_user.assert_called_once()
        called_user_id = mock_sync_user.call_args[0][1]
        assert called_user_id == user_id
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_run_me_requires_authentication():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/sync/run/me")

    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_run_bulk_requires_correct_secret(pool):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/sync/run", headers={"X-Sync-Secret": "wrong"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_run_bulk_requires_secret_header_present(pool):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/sync/run")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_run_bulk_syncs_every_connected_account(pool, test_auth_user, test_auth_user_2):
    """The unit of sync is an account, not a user.

    A user with two mailboxes needs both synced, and one user's failure must not
    stop another's - the cron is the only thing that runs for everyone.
    """
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    profiles = ProfilesRepository(pool)
    await profiles.upsert(user_id, email)
    await profiles.upsert(other_id, other_email)

    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    mine_a = await connect_account(
        pool, user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="work@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )
    mine_b = await connect_account(
        pool, user_id=user_id, provider="google", provider_account_id="g-1",
        email_address="personal@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )
    theirs = await connect_account(
        pool, user_id=other_id, provider="microsoft", provider_account_id="ms-2",
        email_address="other@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )

    seen = []

    async def fake_sync_account(pool_arg, uid, account_id):
        seen.append(account_id)
        if account_id == mine_a:
            raise RuntimeError("boom")
        # True means "I did the work"; False would mean another sync held the
        # user's lock, which the endpoint counts separately from a failure.
        return True

    with patch("app.api.v1.sync.sync_account", side_effect=fake_sync_account):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/sync/run", headers={"X-Sync-Secret": settings.sync_secret}
            )

    assert response.status_code == 200
    # Both of this user's mailboxes, and the other user's, were all attempted
    # despite the first one failing.
    assert {mine_a, mine_b, theirs} <= set(seen)
    body = response.json()
    assert body["failed"] >= 1
    assert body["synced"] >= 2


@pytest.mark.asyncio
async def test_run_bulk_skips_accounts_that_need_reconnecting(pool, test_auth_user):
    """An expired mailbox cannot sync; retrying it every 15 minutes is noise."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    good = await connect_account(
        pool, user_id=user_id, provider="microsoft", provider_account_id="ms-good",
        email_address="good@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )
    stale = await connect_account(
        pool, user_id=user_id, provider="google", provider_account_id="g-stale",
        email_address="stale@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )
    await ConnectedAccountsRepository(pool).set_status(user_id, stale, "needs_reauth")

    seen = []

    async def fake_sync_account(pool_arg, uid, account_id):
        seen.append(account_id)
        return True

    with patch("app.api.v1.sync.sync_account", side_effect=fake_sync_account):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/sync/run", headers={"X-Sync-Secret": settings.sync_secret})

    assert good in seen
    assert stale not in seen


@pytest.mark.asyncio
async def test_run_bulk_logs_which_account_failed(pool, test_auth_user, caplog):
    """A count alone cannot be acted on; the log has to name the mailbox."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await connect_account(
        pool, user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="work@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scopes=[],
    )

    async def boom(pool_arg, uid, aid):
        raise RuntimeError("graph exploded")

    with caplog.at_level(logging.ERROR),          patch("app.api.v1.sync.sync_account", side_effect=boom):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/sync/run", headers={"X-Sync-Secret": settings.sync_secret}
            )

    assert response.status_code == 200
    assert response.json()["failed"] >= 1
    assert "graph exploded" in caplog.text, "the failure reason was not logged"
    assert str(account_id) in caplog.text, "the log does not say which mailbox failed"
