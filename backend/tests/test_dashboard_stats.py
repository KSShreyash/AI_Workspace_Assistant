"""Aggregates behind the redesigned dashboard.

Computed in SQL rather than by shipping whole collections to the browser. The
current dashboard downloads every action item ever created just to count them,
which does not survive real volume.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.repositories.profiles import ProfilesRepository


async def _client_for(user_id, email):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, email=email
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_counts_this_week_and_the_change_from_last_week(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    # 3 emails this week, 2 last week.
    await pool.execute(
        """
        insert into public.emails (user_id, graph_message_id, received_at)
        values ($1, 'e1', now() - interval '1 day'),
               ($1, 'e2', now() - interval '2 days'),
               ($1, 'e3', now() - interval '3 days'),
               ($1, 'e4', now() - interval '9 days'),
               ($1, 'e5', now() - interval '10 days')
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        response = await client.get("/api/dashboard/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["emails"]["this_week"] == 3
    assert body["emails"]["previous_week"] == 2


@pytest.mark.asyncio
async def test_task_counts_come_from_action_items(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.action_items
            (user_id, text, direction, source_type, source_id, created_at, category)
        values ($1, 'a', 'mine', 'email', gen_random_uuid(), now() - interval '1 day', 'work'),
               ($1, 'b', 'mine', 'email', gen_random_uuid(), now() - interval '2 days', 'meeting')
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/dashboard/stats")).json()

    assert body["tasks"]["this_week"] == 2


@pytest.mark.asyncio
async def test_tasks_are_grouped_by_category(pool, test_auth_user):
    """Feeds the task breakdown; the category is set at extraction time."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.action_items
            (user_id, text, direction, source_type, source_id, category)
        values ($1, 'a', 'mine', 'email', gen_random_uuid(), 'work'),
               ($1, 'b', 'mine', 'email', gen_random_uuid(), 'work'),
               ($1, 'c', 'mine', 'email', gen_random_uuid(), 'meeting')
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/dashboard/stats")).json()

    assert body["categories"]["work"] == 2
    assert body["categories"]["meeting"] == 1
    # Every category appears, so the chart does not have to invent zeroes.
    assert body["categories"]["follow_up"] == 0
    assert body["categories"]["other"] == 0


@pytest.mark.asyncio
async def test_daily_series_covers_every_day_including_empty_ones(pool, test_auth_user):
    """A line chart with gaps misleads; absent days must be zero, not missing."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, graph_message_id, received_at)
        values ($1, 'd1', now() - interval '1 day')
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/dashboard/stats")).json()

    series = body["daily"]
    assert len(series) == 7
    assert [point["date"] for point in series] == sorted(point["date"] for point in series)
    assert sum(point["emails"] for point in series) == 1


@pytest.mark.asyncio
async def test_important_people_counts_contacts_with_open_work(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contact_id = await pool.fetchval(
        "insert into public.contacts (user_id, email_address, display_name) "
        "values ($1, 'a@example.com', 'Ann') returning id",
        user_id,
    )
    await pool.execute(
        "insert into public.contacts (user_id, email_address, display_name) "
        "values ($1, 'b@example.com', 'Bo')",
        user_id,
    )
    await pool.execute(
        """
        insert into public.action_items
            (user_id, contact_id, text, direction, status, source_type, source_id)
        values ($1, $2, 'owed', 'mine', 'open', 'email', gen_random_uuid())
        """,
        user_id,
        contact_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/dashboard/stats")).json()

    # Ann has open work; Bo does not.
    assert body["important_people"] == 1


@pytest.mark.asyncio
async def test_stats_never_include_another_users_rows(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    profiles = ProfilesRepository(pool)
    await profiles.upsert(user_id, email)
    await profiles.upsert(other_id, other_email)
    await pool.execute(
        "insert into public.emails (user_id, graph_message_id, received_at) "
        "values ($1, 'mine-1', now()), ($2, 'theirs-1', now())",
        user_id,
        other_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/dashboard/stats")).json()

    assert body["emails"]["this_week"] == 1


@pytest.mark.asyncio
async def test_empty_account_returns_zeroes_not_an_error(pool, test_auth_user):
    """A new account must render a dashboard, not an error state."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with await _client_for(user_id, email) as client:
        response = await client.get("/api/dashboard/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["emails"]["this_week"] == 0
    assert body["tasks"]["this_week"] == 0
    assert body["important_people"] == 0
    assert len(body["daily"]) == 7


@pytest.mark.asyncio
async def test_upcoming_tasks_come_from_the_server_nearest_first(pool, test_auth_user):
    """The dashboard preview must not require downloading every task to sort them."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.action_items
            (user_id, text, direction, status, due_date, source_type, source_id)
        values ($1, 'later',   'mine', 'open', current_date + 9, 'email', gen_random_uuid()),
               ($1, 'soonest', 'mine', 'open', current_date + 1, 'email', gen_random_uuid()),
               ($1, 'middle',  'mine', 'open', current_date + 4, 'email', gen_random_uuid()),
               ($1, 'done',    'mine', 'done', current_date + 2, 'email', gen_random_uuid()),
               ($1, 'undated', 'mine', 'open', null,             'email', gen_random_uuid())
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/dashboard/stats")).json()

    texts = [item["text"] for item in body["upcoming"]]
    assert texts == ["soonest", "middle", "later"]
