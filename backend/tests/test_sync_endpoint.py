from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.main import app


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


from app.core.config import settings
from app.repositories.profiles import ProfilesRepository


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
async def test_run_bulk_syncs_all_connected_users_with_isolation(
    pool, test_auth_user, test_auth_user_2
):
    user_id, email = test_auth_user
    second_user_id, second_email = test_auth_user_2

    profiles = ProfilesRepository(pool)
    try:
        for uid, mail in ((user_id, email), (second_user_id, second_email)):
            await profiles.upsert(uid, mail)
            await profiles.set_graph_connection_status(uid, "connected")

        seen: list = []

        async def fake_sync_user(pool_arg, uid):
            seen.append(uid)
            if uid == user_id:
                raise RuntimeError("boom")
            # second_user_id succeeds silently (no exception)

        with patch("app.api.v1.sync.sync_user", side_effect=fake_sync_user):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/sync/run", headers={"X-Sync-Secret": settings.sync_secret}
                )

        assert response.status_code == 200
        body = response.json()

        # Assert on which users were actually dispatched rather than on the
        # aggregate counts. Counts alone cannot distinguish "both of mine ran"
        # from "one of mine plus someone else's", and this proves one failure
        # does not abort the batch for the remaining users.
        assert user_id in seen
        assert second_user_id in seen
        assert body["synced"] >= 1
        assert body["failed"] >= 1
    finally:
        # The fixtures delete the auth.users rows, which cascades to profiles,
        # but reset the status anyway so a failure mid-test cannot leave a
        # connected profile behind for the next test's bulk run.
        for uid in (user_id, second_user_id):
            await profiles.set_graph_connection_status(uid, "disconnected")
