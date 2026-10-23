from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.core.security import encrypt_token
from app.main import app
from tests.helpers import connect_account, get_connected_account
from app.repositories.profiles import ProfilesRepository


@pytest.mark.asyncio
async def test_graph_status_returns_me_when_token_valid(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    with patch("app.api.v1.me.get_me", new=AsyncMock(return_value={"mail": email})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/me/graph-status")

    assert response.status_code == 200
    body = response.json()
    # The response gained provider/email so the UI can name the right service;
    # graph_me is retained for callers that already read it.
    assert body["connected"] is True
    assert body["provider"] == "microsoft"
    assert body["graph_me"] == {"mail": email}
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "connected"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_graph_status_refreshes_expired_token(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("expired-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        scopes=["Mail.Read"],
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    refreshed = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    with patch("app.services.account_tokens.graph_refresh_access_token", return_value=refreshed), \
         patch("app.api.v1.me.get_me", new=AsyncMock(return_value={"mail": email})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/me/graph-status")

    assert response.status_code == 200
    row = await get_connected_account(pool, user_id)
    from app.core.security import decrypt_token
    assert decrypt_token(row["encrypted_access_token"]) == "new-access"
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "connected"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_graph_status_sets_needs_reauth_on_refresh_failure(pool, test_auth_user):
    from app.services.graph_client import GraphRefreshError

    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("expired-access"),
        encrypted_refresh_token=encrypt_token("dead-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        scopes=["Mail.Read"],
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    with patch("app.services.account_tokens.graph_refresh_access_token", side_effect=GraphRefreshError("expired")):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/me/graph-status")

    assert response.status_code == 409
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "needs_reauth"
    app.dependency_overrides.clear()


def _graph_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
    response = httpx.Response(status_code, request=request, text="graph error body")
    return httpx.HTTPStatusError("graph error", request=request, response=response)


@pytest.mark.asyncio
async def test_graph_status_sets_needs_reauth_when_graph_rejects_token(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    with patch("app.api.v1.me.get_me", new=AsyncMock(side_effect=_graph_error(401))):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/me/graph-status")

    assert response.status_code == 409
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "needs_reauth"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_graph_status_returns_502_on_other_graph_failure(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    with patch("app.api.v1.me.get_me", new=AsyncMock(side_effect=_graph_error(403))):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/me/graph-status")

    assert response.status_code == 502
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# A Google-only user must never be told their Microsoft connection expired
# --------------------------------------------------------------------------


async def _connect_google_only(pool, user_id, email="person@gmail.com"):
    """The only mailbox is Google, so it is also the primary one."""
    await pool.execute("delete from public.connected_accounts where user_id = $1", user_id)
    return await connect_account(
        pool,
        user_id=user_id,
        provider="google",
        provider_account_id="g-only",
        email_address=email,
        encrypted_access_token=encrypt_token("google-access"),
        encrypted_refresh_token=encrypt_token("google-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        is_primary=True,
    )


@pytest.mark.asyncio
async def test_status_never_sends_a_google_token_to_microsoft(pool, test_auth_user):
    """The endpoint took whichever account was primary and called Graph with it.

    With only a Gmail mailbox connected that account is primary, so a Google
    token went to Microsoft, came back 401, and the user was told their
    Microsoft connection had expired - having never connected Microsoft.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _connect_google_only(pool, user_id)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    try:
        with patch("app.api.v1.me.get_me", new=AsyncMock()) as graph_me, patch(
            "app.api.v1.me.gmail_profile", new=AsyncMock(return_value="42")
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/me/graph-status")

        graph_me.assert_not_awaited()
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_healthy_google_mailbox_is_not_marked_needs_reauth(pool, test_auth_user):
    """The real damage: list_syncable only returns 'connected' accounts, so a
    mailbox wrongly flagged here stops syncing altogether.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _connect_google_only(pool, user_id)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    try:
        with patch("app.api.v1.me.get_me", new=AsyncMock()), patch(
            "app.api.v1.me.gmail_profile", new=AsyncMock(return_value="42")
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/me/graph-status")
    finally:
        app.dependency_overrides.clear()

    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "connected"


@pytest.mark.asyncio
async def test_status_reports_which_provider_it_checked(pool, test_auth_user):
    """The UI said "Microsoft" unconditionally. It needs to be told."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _connect_google_only(pool, user_id, email="person@gmail.com")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    try:
        with patch("app.api.v1.me.gmail_profile", new=AsyncMock(return_value="42")):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                body = (await client.get("/api/me/graph-status")).json()
    finally:
        app.dependency_overrides.clear()

    assert body["provider"] == "google"
    assert body["email"] == "person@gmail.com"


@pytest.mark.asyncio
async def test_a_revoked_google_mailbox_is_still_detected(pool, test_auth_user):
    """Not calling Graph must not mean skipping the liveness check entirely -
    the point of this endpoint is catching consent revoked out of band.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _connect_google_only(pool, user_id)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    request = httpx.Request("GET", "https://gmail.googleapis.com/x")
    unauthorised = httpx.HTTPStatusError(
        "unauthorized", request=request, response=httpx.Response(401, request=request)
    )

    try:
        with patch("app.api.v1.me.gmail_profile", new=AsyncMock(side_effect=unauthorised)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/me/graph-status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "needs_reauth"


@pytest.mark.asyncio
async def test_a_microsoft_primary_still_checks_graph(pool, test_auth_user):
    """The existing behaviour must survive the fix."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(
        pool,
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
        is_primary=True,
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)

    try:
        with patch("app.api.v1.me.get_me", new=AsyncMock(return_value={"mail": email})) as graph_me:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                body = (await client.get("/api/me/graph-status")).json()
    finally:
        app.dependency_overrides.clear()

    graph_me.assert_awaited_once()
    assert body["provider"] == "microsoft"
    assert body["graph_me"] == {"mail": email}
