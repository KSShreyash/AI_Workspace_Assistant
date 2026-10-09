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
    assert response.json() == {"connected": True, "graph_me": {"mail": email}}
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
    with patch("app.services.account_tokens.refresh_access_token", return_value=refreshed), \
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

    with patch("app.services.account_tokens.refresh_access_token", side_effect=GraphRefreshError("expired")):
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
