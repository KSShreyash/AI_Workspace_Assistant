import uuid
from datetime import datetime, timezone

import pytest

from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.chat_messages import ChatMessagesRepository
from app.repositories.emails import EmailsRepository
from app.repositories.profiles import ProfilesRepository
from tests.helpers import primary_account_id
from app.repositories.sync_state import SyncStateRepository


@pytest.mark.asyncio
async def test_sync_state_upsert_and_get(pool, test_auth_user):
    user_id, email = test_auth_user
    profiles_repo = ProfilesRepository(pool)
    await profiles_repo.upsert(user_id, email)
    repo = SyncStateRepository(pool)
    account_id = await primary_account_id(pool, user_id)

    assert await repo.get(account_id, "mail") is None

    await repo.upsert(account_id, "mail", delta_link="https://graph/delta?token=abc", status="ok")
    row = await repo.get(account_id, "mail")
    assert row["delta_link"] == "https://graph/delta?token=abc"
    assert row["status"] == "ok"

    await repo.upsert(account_id, "mail", delta_link="https://graph/delta?token=def", status="ok")
    row = await repo.get(account_id, "mail")
    assert row["delta_link"] == "https://graph/delta?token=def"


@pytest.mark.asyncio
async def test_emails_upsert_dedupes_by_provider_message_id(pool, test_auth_user):
    user_id, email = test_auth_user
    profiles_repo = ProfilesRepository(pool)
    await profiles_repo.upsert(user_id, email)
    repo = EmailsRepository(pool)

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_message_id="msg-1",
        subject="Hello",
        from_address="a@example.com",
        from_name="A",
        to_recipients=[{"address": "b@example.com", "name": "B"}],
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        body_text="Hi there",
    )
    assert await repo.count(user_id) == 1

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_message_id="msg-1",
        subject="Hello (edited)",
        from_address="a@example.com",
        from_name="A",
        to_recipients=[{"address": "b@example.com", "name": "B"}],
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        body_text="Hi there, edited",
    )
    assert await repo.count(user_id) == 1

    row = await pool.fetchrow(
        "select subject from public.emails where user_id = $1 and provider_message_id = $2",
        user_id,
        "msg-1",
    )
    assert row["subject"] == "Hello (edited)"


@pytest.mark.asyncio
async def test_emails_delete(pool, test_auth_user):
    user_id, email = test_auth_user
    profiles_repo = ProfilesRepository(pool)
    await profiles_repo.upsert(user_id, email)
    repo = EmailsRepository(pool)

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_message_id="msg-2",
        subject="Bye",
        from_address="a@example.com",
        from_name="A",
        to_recipients=[],
        received_at=None,
        body_text=None,
    )
    assert await repo.count(user_id) == 1

    await repo.delete(account_id=await primary_account_id(pool, user_id), provider_message_id="msg-2")
    assert await repo.count(user_id) == 0


@pytest.mark.asyncio
async def test_calendar_events_upsert_dedupes_by_provider_event_id(pool, test_auth_user):
    user_id, email = test_auth_user
    profiles_repo = ProfilesRepository(pool)
    await profiles_repo.upsert(user_id, email)
    repo = CalendarEventsRepository(pool)

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-1",
        subject="Standup",
        organizer="a@example.com",
        attendees=[{"address": "b@example.com", "name": "B"}],
        start_time=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        is_online_meeting=True,
        online_meeting_join_url="https://teams.microsoft.com/l/meetup-join/xyz",
        body_text="Daily sync",
    )
    assert await repo.count(user_id) == 1

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-1",
        subject="Standup (moved)",
        organizer="a@example.com",
        attendees=[{"address": "b@example.com", "name": "B"}],
        start_time=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc),
        is_online_meeting=True,
        online_meeting_join_url="https://teams.microsoft.com/l/meetup-join/xyz",
        body_text="Daily sync",
    )
    assert await repo.count(user_id) == 1

    row = await pool.fetchrow(
        "select subject from public.calendar_events where user_id = $1 and provider_event_id = $2",
        user_id,
        "evt-1",
    )
    assert row["subject"] == "Standup (moved)"


@pytest.mark.asyncio
async def test_calendar_events_delete(pool, test_auth_user):
    user_id, email = test_auth_user
    profiles_repo = ProfilesRepository(pool)
    await profiles_repo.upsert(user_id, email)
    repo = CalendarEventsRepository(pool)

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-2",
        subject="Cancelled meeting",
        organizer="a@example.com",
        attendees=[],
        start_time=None,
        end_time=None,
        is_online_meeting=False,
        online_meeting_join_url=None,
        body_text=None,
    )
    assert await repo.count(user_id) == 1

    await repo.delete(account_id=await primary_account_id(pool, user_id), provider_event_id="evt-2")
    assert await repo.count(user_id) == 0


@pytest.mark.asyncio
async def test_chat_messages_upsert_dedupes_by_provider_message_id(pool, test_auth_user):
    user_id, email = test_auth_user
    profiles_repo = ProfilesRepository(pool)
    await profiles_repo.upsert(user_id, email)
    repo = ChatMessagesRepository(pool)

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_chat_id="19:abc@thread.v2",
        provider_message_id="chat-msg-1",
        from_user="Alice",
        content="Hey there",
        sent_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert await repo.count(user_id) == 1

    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_chat_id="19:abc@thread.v2",
        provider_message_id="chat-msg-1",
        from_user="Alice",
        content="Hey there (edited)",
        sent_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert await repo.count(user_id) == 1

    row = await pool.fetchrow(
        "select content from public.chat_messages where user_id = $1 and provider_message_id = $2",
        user_id,
        "chat-msg-1",
    )
    assert row["content"] == "Hey there (edited)"


@pytest.mark.asyncio
async def test_calendar_events_upsert_returns_row_id(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = CalendarEventsRepository(pool)

    event_id = await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-return-id", subject="Test", organizer=None,
        attendees=[], start_time=None, end_time=None,
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )

    row = await pool.fetchrow("select id from public.calendar_events where user_id = $1 and provider_event_id = $2", user_id, "evt-return-id")
    assert event_id == row["id"]


@pytest.mark.asyncio
async def test_calendar_events_list_busy_between_excludes_events_outside_range(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = CalendarEventsRepository(pool)
    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-in-range", subject="In range", organizer=None,
        attendees=[], start_time=datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )
    await repo.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-out-of-range", subject="Out of range", organizer=None,
        attendees=[], start_time=datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )

    busy = await repo.list_busy_between(
        user_id, datetime(2026, 7, 19, tzinfo=timezone.utc), datetime(2026, 7, 21, tzinfo=timezone.utc)
    )

    assert len(busy) == 1
    assert busy[0]["start_time"] == datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
