"""The Calendar page: upcoming events from synced calendars."""

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
async def test_lists_upcoming_events_soonest_first(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.calendar_events
            (user_id, graph_event_id, subject, start_time, end_time)
        values ($1, 'e-late',  'Later',   now() + interval '3 days', now() + interval '3 days 1 hour'),
               ($1, 'e-soon',  'Sooner',  now() + interval '1 hour', now() + interval '2 hours'),
               ($1, 'e-past',  'Already', now() - interval '2 days', now() - interval '2 days 1 hour')
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/calendar")).json()

    subjects = [event["subject"] for event in body]
    # Past events are not "upcoming"; the soonest comes first.
    assert subjects == ["Sooner", "Later"]


@pytest.mark.asyncio
async def test_includes_the_join_link_for_online_meetings(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.calendar_events
            (user_id, graph_event_id, subject, start_time, end_time,
             is_online_meeting, online_meeting_join_url)
        values ($1, 'e1', 'Standup', now() + interval '1 hour', now() + interval '2 hours',
                true, 'https://teams.example/join')
        """,
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/calendar")).json()

    assert body[0]["is_online_meeting"] is True
    assert body[0]["join_url"] == "https://teams.example/join"


@pytest.mark.asyncio
async def test_skips_events_with_no_start_time(pool, test_auth_user):
    """Graph can return events without a parsable start; they cannot be placed."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        "insert into public.calendar_events (user_id, graph_event_id, subject, start_time) "
        "values ($1, 'e-null', 'Undated', null)",
        user_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/calendar")).json()

    assert body == []


@pytest.mark.asyncio
async def test_calendar_never_leaks_across_users(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    profiles = ProfilesRepository(pool)
    await profiles.upsert(user_id, email)
    await profiles.upsert(other_id, other_email)
    await pool.execute(
        """
        insert into public.calendar_events (user_id, graph_event_id, subject, start_time)
        values ($1, 'mine', 'Mine', now() + interval '1 hour'),
               ($2, 'theirs', 'Theirs', now() + interval '1 hour')
        """,
        user_id,
        other_id,
    )

    async with await _client_for(user_id, email) as client:
        body = (await client.get("/api/calendar")).json()

    assert [e["subject"] for e in body] == ["Mine"]
