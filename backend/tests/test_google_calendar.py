"""Google Calendar sync, and availability across every connected calendar.

Google's incremental model is a syncToken, which behaves unlike both Graph's
deltaLink and Gmail's historyId - most importantly it cannot be combined with a
time window, and it reports deletions as events with status=cancelled rather
than as removals.

Availability is the reason this matters. Booking a follow-up against only the
Outlook calendar double-books anyone whose real life is in Google Calendar.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import httpx
import pytest

from app.core.security import encrypt_token
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.profiles import ProfilesRepository
from app.repositories.sync_state import SyncStateRepository
from app.services import google_calendar_client as gcal
from app.services import graph_sync
from app.services.google_calendar_client import SyncTokenExpired, normalize_event


async def _connect_google(pool, user_id, pid="g-1"):
    return await ConnectedAccountsRepository(pool).upsert(
        user_id=user_id,
        provider="google",
        provider_account_id=pid,
        email_address=f"{pid}@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )


def _event(event_id="evt-1", **overrides):
    event = {
        "id": event_id,
        "status": "confirmed",
        "summary": "Standup",
        "organizer": {"email": "dana@example.com"},
        "attendees": [{"email": "sam@example.com", "displayName": "Sam"}],
        "start": {"dateTime": "2026-07-01T09:00:00Z"},
        "end": {"dateTime": "2026-07-01T09:30:00Z"},
        "description": "Daily sync",
    }
    event.update(overrides)
    return event


def _response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json=body, request=httpx.Request("GET", "https://www.googleapis.com/x")
    )


# --------------------------------------------------------------------------
# Request shape
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recurring_events_are_expanded_into_instances():
    """Without singleEvents the API returns the recurrence master and no
    occurrences, so a weekly standup blocks nothing and never shows up.
    """
    with patch("app.services.google_calendar_client._get_json", AsyncMock(return_value={})) as get:
        await gcal.fetch_events_page("tok", time_min=datetime(2026, 7, 1, tzinfo=timezone.utc))

    assert "singleEvents=true" in get.await_args.args[1]


@pytest.mark.asyncio
async def test_an_incremental_fetch_sends_no_time_window():
    """Google rejects syncToken combined with timeMin/timeMax/orderBy with a
    400, so an incremental sync that also passes a window never runs at all.
    """
    with patch("app.services.google_calendar_client._get_json", AsyncMock(return_value={})) as get:
        await gcal.fetch_events_page("tok", sync_token="stok")

    url = get.await_args.args[1]
    assert "syncToken=stok" in url
    assert "timeMin" not in url
    assert "timeMax" not in url
    assert "orderBy" not in url


@pytest.mark.asyncio
async def test_a_first_fetch_sends_the_window_and_no_sync_token():
    with patch("app.services.google_calendar_client._get_json", AsyncMock(return_value={})) as get:
        await gcal.fetch_events_page(
            "tok",
            time_min=datetime(2026, 7, 1, tzinfo=timezone.utc),
            time_max=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )

    url = unquote(get.await_args.args[1])
    assert "timeMin=2026-07-01T00:00:00Z" in url
    assert "timeMax=2026-09-29T00:00:00Z" in url
    assert "syncToken" not in url


@pytest.mark.asyncio
async def test_pages_and_the_final_sync_token_are_returned():
    """nextSyncToken appears only on the last page; storing a mid-walk token
    would skip everything after it.
    """
    body = {"items": [_event()], "nextPageToken": "p2"}
    with patch("app.services.google_calendar_client._get_json", AsyncMock(return_value=body)):
        page = await gcal.fetch_events_page("tok", sync_token="stok")

    assert page["next_page_token"] == "p2"
    assert page["next_sync_token"] is None


@pytest.mark.asyncio
async def test_an_expired_sync_token_raises_rather_than_erroring():
    """410 means the token is too old. The only way back is a full resync, so
    this is signalled distinctly.
    """
    error = httpx.HTTPStatusError(
        "gone",
        request=httpx.Request("GET", "https://www.googleapis.com/x"),
        response=_response({}, status=410),
    )
    with patch("app.services.google_calendar_client._get_json", AsyncMock(side_effect=error)):
        with pytest.raises(SyncTokenExpired):
            await gcal.fetch_events_page("tok", sync_token="stale")


@pytest.mark.asyncio
async def test_other_errors_are_not_mistaken_for_an_expired_token():
    error = httpx.HTTPStatusError(
        "server error",
        request=httpx.Request("GET", "https://www.googleapis.com/x"),
        response=_response({}, status=500),
    )
    with patch("app.services.google_calendar_client._get_json", AsyncMock(side_effect=error)):
        with pytest.raises(httpx.HTTPStatusError):
            await gcal.fetch_events_page("tok", sync_token="stok")


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def test_an_event_maps_onto_the_same_fields_a_graph_event_produces():
    event = normalize_event(_event())

    assert event["id"] == "evt-1"
    assert event["subject"] == "Standup"
    assert event["organizer"] == "dana@example.com"
    assert event["attendees"] == [{"address": "sam@example.com", "name": "Sam"}]
    assert event["start_time"] == datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    assert event["end_time"] == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    assert event["body_text"] == "Daily sync"


def test_times_with_an_offset_are_converted_to_utc():
    """Google returns the organiser's local offset. Storing it unconverted puts
    the event in the wrong place in every query, which all use UTC bounds.
    """
    event = normalize_event(
        _event(
            start={"dateTime": "2026-07-01T09:00:00+05:30"},
            end={"dateTime": "2026-07-01T09:30:00+05:30"},
        )
    )

    assert event["start_time"] == datetime(2026, 7, 1, 3, 30, tzinfo=timezone.utc)
    assert event["end_time"] == datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)


def test_an_all_day_event_has_a_start_and_an_end():
    """All-day events carry `date`, not `dateTime`. Reading only dateTime leaves
    both null, and a null-timed event is invisible to every availability query -
    so a week of leave would not block anything.
    """
    event = normalize_event(
        _event(start={"date": "2026-07-01"}, end={"date": "2026-07-02"})
    )

    assert event["start_time"] == datetime(2026, 7, 1, tzinfo=timezone.utc)
    # Google's all-day end date is exclusive, which happens to be exactly the
    # instant the busy block should end.
    assert event["end_time"] == datetime(2026, 7, 2, tzinfo=timezone.utc)


def test_a_google_meet_link_is_recognised_as_an_online_meeting():
    event = normalize_event(_event(hangoutLink="https://meet.google.com/abc-defg-hij"))

    assert event["is_online_meeting"] is True
    assert event["online_meeting_join_url"] == "https://meet.google.com/abc-defg-hij"


def test_a_conference_entry_point_is_used_when_there_is_no_hangout_link():
    """Meetings created through conferenceData - including third-party ones like
    Zoom - have no hangoutLink.
    """
    event = normalize_event(
        _event(
            conferenceData={
                "entryPoints": [
                    {"entryPointType": "phone", "uri": "tel:+1234"},
                    {"entryPointType": "video", "uri": "https://zoom.example/j/1"},
                ]
            }
        )
    )

    assert event["is_online_meeting"] is True
    assert event["online_meeting_join_url"] == "https://zoom.example/j/1"
    # The phone bridge is not a join URL.
    assert "tel:" not in event["online_meeting_join_url"]


def test_an_in_person_event_is_not_an_online_meeting():
    event = normalize_event(_event())

    assert event["is_online_meeting"] is False
    assert event["online_meeting_join_url"] is None


def test_an_event_with_no_attendees_normalizes_to_an_empty_list():
    event = normalize_event(_event(attendees=None))

    assert event["attendees"] == []


def test_an_attendee_without_a_display_name_keeps_its_address():
    event = normalize_event(_event(attendees=[{"email": "x@example.com"}]))

    assert event["attendees"] == [{"address": "x@example.com", "name": None}]


def test_a_cancelled_event_is_flagged_as_removed():
    """Google reports deletions as events with status=cancelled, not as a
    removal marker. Upserting one puts a deleted meeting back on the calendar.
    """
    assert normalize_event(_event(status="cancelled"))["removed"] is True
    assert normalize_event(_event())["removed"] is False


def test_a_cancelled_event_stripped_to_just_an_id_still_normalizes():
    """An incremental page can return little more than the id for something
    deleted long ago.
    """
    event = normalize_event({"id": "evt-gone", "status": "cancelled"})

    assert event["removed"] is True
    assert event["id"] == "evt-gone"


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_calendar_sync_stores_events_and_a_sync_token(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    page = {"items": [_event()], "next_page_token": None, "next_sync_token": "stok-1"}
    with patch(
        "app.services.graph_sync.google_calendar_client.fetch_events_page",
        AsyncMock(return_value=page),
    ):
        await graph_sync.sync_google_calendar(pool, user_id, account_id, "tok")

    assert await CalendarEventsRepository(pool).count(user_id) == 1
    state = await SyncStateRepository(pool).get(account_id, "calendar")
    assert state["delta_link"] == "stok-1"
    assert state["status"] == "ok"


@pytest.mark.asyncio
async def test_a_stored_sync_token_drives_the_next_sync(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "calendar", "stok-1", "ok")

    page = {"items": [], "next_page_token": None, "next_sync_token": "stok-2"}
    with patch(
        "app.services.graph_sync.google_calendar_client.fetch_events_page",
        AsyncMock(return_value=page),
    ) as fetch:
        await graph_sync.sync_google_calendar(pool, user_id, account_id, "tok")

    assert fetch.await_args.kwargs["sync_token"] == "stok-1"
    assert (await SyncStateRepository(pool).get(account_id, "calendar"))["delta_link"] == "stok-2"


@pytest.mark.asyncio
async def test_a_cancelled_event_is_deleted_from_the_calendar(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await CalendarEventsRepository(pool).upsert(
        user_id=user_id,
        account_id=account_id,
        provider_event_id="evt-1",
        subject="Standup",
        organizer=None,
        attendees=[],
        start_time=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        is_online_meeting=False,
        online_meeting_join_url=None,
        body_text=None,
    )
    await SyncStateRepository(pool).upsert(account_id, "calendar", "stok-1", "ok")

    page = {
        "items": [_event(status="cancelled")],
        "next_page_token": None,
        "next_sync_token": "stok-2",
    }
    with patch(
        "app.services.graph_sync.google_calendar_client.fetch_events_page",
        AsyncMock(return_value=page),
    ):
        await graph_sync.sync_google_calendar(pool, user_id, account_id, "tok")

    assert await CalendarEventsRepository(pool).count(user_id) == 0


@pytest.mark.asyncio
async def test_an_expired_sync_token_triggers_a_full_resync(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "calendar", "stale", "ok")

    calls = []

    async def fetch(_token, *, sync_token=None, time_min=None, time_max=None, page_token=None):
        calls.append(sync_token)
        if sync_token == "stale":
            raise SyncTokenExpired("too old")
        return {"items": [_event()], "next_page_token": None, "next_sync_token": "stok-new"}

    with patch("app.services.graph_sync.google_calendar_client.fetch_events_page", fetch):
        await graph_sync.sync_google_calendar(pool, user_id, account_id, "tok")

    assert calls == ["stale", None]
    assert await CalendarEventsRepository(pool).count(user_id) == 1
    assert (
        await SyncStateRepository(pool).get(account_id, "calendar")
    )["delta_link"] == "stok-new"


@pytest.mark.asyncio
async def test_calendar_sync_follows_every_page(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    async def fetch(_token, *, sync_token=None, time_min=None, time_max=None, page_token=None):
        if page_token is None:
            return {"items": [_event("evt-1")], "next_page_token": "p2", "next_sync_token": None}
        return {"items": [_event("evt-2")], "next_page_token": None, "next_sync_token": "stok-last"}

    with patch("app.services.graph_sync.google_calendar_client.fetch_events_page", fetch):
        await graph_sync.sync_google_calendar(pool, user_id, account_id, "tok")

    assert await CalendarEventsRepository(pool).count(user_id) == 2
    assert (
        await SyncStateRepository(pool).get(account_id, "calendar")
    )["delta_link"] == "stok-last"


@pytest.mark.asyncio
async def test_a_missing_calendar_scope_marks_the_resource_unavailable(pool, test_auth_user):
    """The user may have granted Gmail but not Calendar. Retrying every fifteen
    minutes forever helps nobody.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    error = httpx.HTTPStatusError(
        "forbidden",
        request=httpx.Request("GET", "https://www.googleapis.com/x"),
        response=_response({}, status=403),
    )
    with patch(
        "app.services.graph_sync.google_calendar_client.fetch_events_page",
        AsyncMock(side_effect=error),
    ):
        await graph_sync.sync_google_calendar(pool, user_id, account_id, "tok")

    assert (await SyncStateRepository(pool).get(account_id, "calendar"))["status"] == "not_available"


# --------------------------------------------------------------------------
# Dispatch and availability
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_google_account_syncs_its_calendar_too(pool, test_auth_user):
    """Calendar used to run only for a Microsoft primary. A connected Gmail
    account with calendar access was simply never read.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    with patch("app.services.graph_sync.sync_gmail", AsyncMock()), patch(
        "app.services.graph_sync.sync_google_calendar", AsyncMock()
    ) as gcal_sync, patch("app.services.graph_sync.extract_user", AsyncMock()):
        await graph_sync.sync_account(pool, user_id, account_id)

    gcal_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_microsoft_account_does_not_run_the_google_calendar_sync(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"]

    with patch("app.services.graph_sync.sync_mail", AsyncMock()), patch(
        "app.services.graph_sync.sync_calendar", AsyncMock()
    ) as graph_cal, patch("app.services.graph_sync.sync_chat", AsyncMock()), patch(
        "app.services.graph_sync.sync_google_calendar", AsyncMock()
    ) as gcal_sync, patch("app.services.graph_sync.extract_user", AsyncMock()):
        await graph_sync.sync_account(pool, user_id, account_id)

    graph_cal.assert_awaited_once()
    gcal_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_availability_considers_every_connected_calendar(pool, test_auth_user):
    """The point of the whole exercise. Booking a follow-up against only the
    Outlook calendar double-books anyone whose real life is in Google Calendar.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    ms_account = (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"]
    google_account = await _connect_google(pool, user_id)

    events = CalendarEventsRepository(pool)
    for account_id, event_id, hour in ((ms_account, "ms-evt", 9), (google_account, "g-evt", 14)):
        await events.upsert(
            user_id=user_id,
            account_id=account_id,
            provider_event_id=event_id,
            subject="Busy",
            organizer=None,
            attendees=[],
            start_time=datetime(2026, 7, 1, hour, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 7, 1, hour + 1, 0, tzinfo=timezone.utc),
            is_online_meeting=False,
            online_meeting_join_url=None,
            body_text=None,
        )

    busy = await events.list_busy_between(
        user_id,
        datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc),
    )

    assert {b["start_time"].hour for b in busy} == {9, 14}
