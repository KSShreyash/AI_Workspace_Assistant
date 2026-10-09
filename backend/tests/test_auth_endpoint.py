import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from app.core.deps import CurrentUser, get_current_user
from app.core.security import decrypt_token
from app.main import app
from app.repositories.connected_accounts import ConnectedAccountsRepository

CONNECT_BODY = {
    "provider": "microsoft",
    "provider_account_id": "ms-abc",
    "email_address": "work@example.com",
    "provider_token": "access-123",
    "provider_refresh_token": "refresh-456",
    "expires_in": 3600,
    "scopes": ["Mail.Read"],
}


@pytest.fixture
def as_user(test_auth_user):
    user_id, email = test_auth_user
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)
    yield user_id, email
    app.dependency_overrides.clear()


async def _post(body: dict):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/auth/accounts", json=body)


@pytest.mark.asyncio
async def test_connecting_a_mailbox_stores_its_tokens_encrypted(pool, as_user):
    user_id, _ = as_user

    with patch("app.api.v1.auth.sync_account", new=AsyncMock()):
        response = await _post(CONNECT_BODY)

    assert response.status_code == 204
    accounts = await ConnectedAccountsRepository(pool).list_for_user(user_id)
    assert len(accounts) == 1
    stored = accounts[0]["encrypted_access_token"]
    assert stored != "access-123", "token was stored in plaintext"
    assert decrypt_token(stored) == "access-123"


@pytest.mark.asyncio
async def test_connecting_a_mailbox_kicks_off_a_sync_in_the_background(pool, as_user):
    """A first sync walks 30 days of mail; it must not hold the OAuth redirect."""
    with patch("app.api.v1.auth.sync_account", new=AsyncMock()) as mock_sync:
        response = await _post(CONNECT_BODY)

    assert response.status_code == 204
    mock_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_second_mailbox_is_added_rather_than_replacing_the_first(pool, as_user):
    user_id, _ = as_user

    with patch("app.api.v1.auth.sync_account", new=AsyncMock()):
        await _post(CONNECT_BODY)
        await _post({
            **CONNECT_BODY,
            "provider": "google",
            "provider_account_id": "g-xyz",
            "email_address": "personal@gmail.com",
        })

    accounts = await ConnectedAccountsRepository(pool).list_for_user(user_id)
    assert {a["provider"] for a in accounts} == {"microsoft", "google"}


@pytest.mark.asyncio
async def test_reconnecting_an_expired_mailbox_clears_needs_reauth(pool, as_user):
    """Reconnecting is how a user fixes an expired mailbox - it has to work."""
    user_id, _ = as_user
    repo = ConnectedAccountsRepository(pool)

    with patch("app.api.v1.auth.sync_account", new=AsyncMock()):
        await _post(CONNECT_BODY)
        account_id = (await repo.list_for_user(user_id))[0]["id"]
        await repo.set_status(user_id, account_id, "needs_reauth")

        await _post(CONNECT_BODY)

    accounts = await repo.list_for_user(user_id)
    assert len(accounts) == 1, "reconnecting created a duplicate instead of updating"
    assert accounts[0]["connection_status"] == "connected"


@pytest.mark.asyncio
async def test_rejects_a_body_with_no_account_identity(pool, as_user):
    """Without a provider account id, a reconnect cannot be told from a new mailbox."""
    body = {k: v for k, v in CONNECT_BODY.items() if k != "provider_account_id"}

    with patch("app.api.v1.auth.sync_account", new=AsyncMock()):
        response = await _post(body)

    assert response.status_code == 422
