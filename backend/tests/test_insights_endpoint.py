"""The Insights page.

Scoped to questions the existing schema can actually answer, rather than
inventing metrics: who owes whom, who writes most, and whether the extraction
pipeline is keeping up.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.repositories.profiles import ProfilesRepository


async def _client_for(user_id, email):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_splits_what_you_owe_from_what_you_are_owed(pool, test_auth_user):
    """The whole point of tracking direction - a flat list hides who is waiting."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    ann = await pool.fetchval(
        "insert into public.contacts (user_id, email_address, display_name) "
        "values ($1, 'ann@example.com', 'Ann') returning id",
        user_id,
    )
    bob = await pool.fetchval(
        "insert into public.contacts (user_id, email_address, display_name) "
        "values ($1, 'bob@example.com', 'Bob') returning id",
        user_id,
    )
    await pool.execute(
        """
        insert into public.action_items
            (user_id, contact_id, text, direction, status, source_type, source_id)
        values ($1, $2, 'send deck',   'mine',   'open', 'email', gen_random_uuid()),
               ($1, $2, 'send notes',  'mine',   'open', 'email', gen_random_uuid()),
               ($1, $3, 'await reply', 'theirs', 'open', 'email', gen_random_uuid())
        """,
        user_id,
        ann,
        bob,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/insights")).json()

    owed_by_me = {row["contact"]: row["count"] for row in body["you_owe"]}
    owed_to_me = {row["contact"]: row["count"] for row in body["owed_to_you"]}
    assert owed_by_me == {"Ann": 2}
    assert owed_to_me == {"Bob": 1}


@pytest.mark.asyncio
async def test_ranks_busiest_correspondents(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, received_at)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'm1', 'loud@example.com', now()),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'm2', 'loud@example.com', now()),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'm3', 'loud@example.com', now()),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'm4', 'quiet@example.com', now())
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/insights")).json()

    assert body["top_senders"][0] == {"sender": "loud@example.com", "count": 3}


@pytest.mark.asyncio
async def test_reports_pipeline_health(pool, test_auth_user):
    """Extracted, skipped and failed are three different outcomes.

    Readable only because the extraction work records why it skipped or gave up
    rather than silently moving on.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails
            (user_id, account_id, provider_message_id, received_at, extracted_at, extraction_error, extraction_attempts)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'ok1',      now(), now(), null, 0),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'skipped1', now(), now(), 'skipped: sender looks automated', 0),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'failed1',  now(), null,  'RuntimeError: boom', 3),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'pending1', now(), null,  null, 0)
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/insights")).json()

    health = body["pipeline"]
    assert health["extracted"] == 1
    assert health["skipped"] == 1
    assert health["failed"] == 1
    assert health["pending"] == 1


@pytest.mark.asyncio
async def test_insights_never_leak_across_users(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    profiles = ProfilesRepository(pool)
    await profiles.upsert(user_id, email)
    await profiles.upsert(other_id, other_email)
    await pool.execute(
        "insert into public.emails (user_id, account_id, provider_message_id, from_address, received_at) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'a', 'mine@example.com', now()), ($2, (select id from public.connected_accounts where user_id = $2 and is_primary), 'b', 'theirs@example.com', now())",
        user_id,
        other_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/insights")).json()

    senders = [row["sender"] for row in body["top_senders"]]
    assert senders == ["mine@example.com"]


@pytest.mark.asyncio
async def test_empty_account_returns_empty_sections(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with await _client_for(user_id, email) as client:
        response = await client.get("/api/insights")

    assert response.status_code == 200
    body = response.json()
    assert body["you_owe"] == []
    assert body["owed_to_you"] == []
    assert body["top_senders"] == []
    assert body["pipeline"]["extracted"] == 0
