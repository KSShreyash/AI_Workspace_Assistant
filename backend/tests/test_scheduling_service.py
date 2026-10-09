import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.security import encrypt_token
from app.repositories.action_items import ActionItemsRepository
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.contacts import ContactsRepository
from tests.helpers import connect_account, get_connected_account
from app.repositories.profiles import ProfilesRepository
from app.services.scheduling import MAX_SUGGESTIONS, create_meeting, suggest_slots


def test_suggest_slots_empty_calendar_starts_at_current_time_and_caps_at_max():
    now_utc = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)  # Monday 1pm UTC

    slots = suggest_slots(now_utc, None, [])

    assert len(slots) == MAX_SUGGESTIONS
    assert slots[0]["start"] == datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
    assert slots[0]["end"] == datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc)
    assert slots[7]["start"] == datetime(2026, 7, 20, 16, 30, tzinfo=timezone.utc)
    assert slots[8]["start"] == datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)


def test_suggest_slots_excludes_busy_interval():
    now_utc = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
    busy = [(datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc), datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc))]

    slots = suggest_slots(now_utc, None, busy)

    starts = [s["start"] for s in slots]
    assert datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc) not in starts
    assert len(slots) == MAX_SUGGESTIONS


def test_suggest_slots_skips_weekend():
    now_utc = datetime(2026, 7, 17, 16, 45, tzinfo=timezone.utc)  # Friday 4:45pm UTC, past the last slot

    slots = suggest_slots(now_utc, None, [])

    assert slots[0]["start"] == datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)  # Monday, not Sat/Sun


def test_suggest_slots_respects_timezone():
    now_utc = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)  # 6am EDT, before the 9am local work window

    slots = suggest_slots(now_utc, "America/New_York", [])

    assert slots[0]["start"] == datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)  # 9am EDT == 1pm UTC


def test_suggest_slots_defaults_to_utc_when_timezone_name_is_none():
    now_utc = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)

    with_none = suggest_slots(now_utc, None, [])
    with_utc = suggest_slots(now_utc, "UTC", [])

    assert with_none == with_utc


async def _seed_item_and_token(pool, user_id, email, contact_email=None):
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Calendars.ReadWrite"],
    )
    contact_id = None
    if contact_email:
        contact_id = await ContactsRepository(pool).upsert_by_email(user_id, contact_email, "Gina", None)
    await ActionItemsRepository(pool).insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    return item_row["id"]


@pytest.mark.asyncio
@respx.mock
async def test_create_meeting_invites_attendee_when_email_known(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_item_and_token(pool, user_id, email, contact_email="gina@example.com")
    route = respx.post("https://graph.microsoft.com/v1.0/me/events").mock(
        return_value=Response(201, json={
            "id": "graph-evt-1", "subject": "Call Gina", "organizer": None, "attendees": [],
            "isOnlineMeeting": True, "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/x"},
        })
    )
    start = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc)

    result = await create_meeting(
        pool, user_id, item_id, item_text="Call Gina", start=start, end=end,
        online_meeting=True, contact_email="gina@example.com", contact_display_name="Gina",
    )

    assert result["scheduled_calendar_event_id"] is not None
    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body["attendees"] == [{"emailAddress": {"address": "gina@example.com", "name": "Gina"}, "type": "required"}]
    assert sent_body["isOnlineMeeting"] is True
    assert sent_body["onlineMeetingProvider"] == "teamsForBusiness"
    row = await pool.fetchrow(
        "select * from public.calendar_events where user_id = $1 and provider_event_id = $2", user_id, "graph-evt-1"
    )
    assert row["online_meeting_join_url"] == "https://teams.microsoft.com/l/x"


@pytest.mark.asyncio
@respx.mock
async def test_create_meeting_no_attendee_when_email_unknown(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_item_and_token(pool, user_id, email, contact_email=None)
    route = respx.post("https://graph.microsoft.com/v1.0/me/events").mock(
        return_value=Response(201, json={"id": "graph-evt-2", "subject": "Call Gina", "isOnlineMeeting": False})
    )
    start = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc)

    await create_meeting(
        pool, user_id, item_id, item_text="Call Gina", start=start, end=end,
        online_meeting=False, contact_email=None, contact_display_name=None,
    )

    sent_body = json.loads(route.calls.last.request.content)
    assert "attendees" not in sent_body
    assert sent_body["isOnlineMeeting"] is False
    assert "onlineMeetingProvider" not in sent_body


@pytest.mark.asyncio
async def test_create_meeting_returns_none_when_no_graph_connection(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contact_id = await ContactsRepository(pool).upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await ActionItemsRepository(pool).insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    start = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc)

    result = await create_meeting(
        pool, user_id, item_row["id"], item_text="Call Gina", start=start, end=end,
        online_meeting=False, contact_email="gina@example.com", contact_display_name="Gina",
    )

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_create_meeting_retries_once_on_401(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_item_and_token(pool, user_id, email, contact_email="gina@example.com")
    route = respx.post("https://graph.microsoft.com/v1.0/me/events")
    route.side_effect = [
        Response(401),
        Response(201, json={"id": "graph-evt-3", "subject": "Call Gina", "isOnlineMeeting": False}),
    ]
    refreshed = {
        "access_token": "refreshed-access",
        "refresh_token": "refreshed-refresh",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    start = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc)

    with patch("app.services.account_tokens.graph_refresh_access_token", return_value=refreshed):
        result = await create_meeting(
            pool, user_id, item_id, item_text="Call Gina", start=start, end=end,
            online_meeting=False, contact_email="gina@example.com", contact_display_name="Gina",
        )

    assert result["scheduled_calendar_event_id"] is not None
    assert route.call_count == 2
