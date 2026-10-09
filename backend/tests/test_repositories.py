import pytest

from app.repositories.profiles import ProfilesRepository


@pytest.mark.asyncio
async def test_profiles_upsert_and_get(pool, test_auth_user):
    user_id, email = test_auth_user
    repo = ProfilesRepository(pool)

    await repo.upsert(user_id, email, display_name="Test User")
    row = await repo.get(user_id)

    assert row["email"] == email
    assert row["display_name"] == "Test User"
    assert row["graph_connection_status"] == "disconnected"


@pytest.mark.asyncio
async def test_profiles_set_graph_connection_status(pool, test_auth_user):
    user_id, email = test_auth_user
    repo = ProfilesRepository(pool)
    await repo.upsert(user_id, email)

    await repo.set_graph_connection_status(user_id, "connected")
    row = await repo.get(user_id)

    assert row["graph_connection_status"] == "connected"

