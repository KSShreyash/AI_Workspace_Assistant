import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from app.core.security import decrypt_token, encrypt_token
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.chat_messages import ChatMessagesRepository
from app.repositories.emails import EmailsRepository
from tests.helpers import connect_account, get_connected_account, primary_account_id
from app.repositories.profiles import ProfilesRepository
from app.repositories.sync_state import SyncStateRepository
from app.services.graph_client import GraphRefreshError
from app.services.graph_sync import _parse_graph_datetime, sync_calendar, sync_chat, sync_mail, sync_user


def test_parse_graph_datetime_handles_z_suffix():
    assert _parse_graph_datetime("2026-07-01T12:00:00Z") == datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_graph_datetime_handles_naive_utc_no_z():
    assert _parse_graph_datetime("2026-07-01T12:00:00.0000000") == datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_graph_datetime_truncates_seven_digit_fraction():
    result = _parse_graph_datetime("2026-07-01T12:00:00.1234567Z")
    assert result.microsecond == 123456


def test_parse_graph_datetime_handles_none():
    assert _parse_graph_datetime(None) is None


@pytest.mark.asyncio
async def test_sync_mail_upserts_and_stores_delta_link(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    page = {
        "items": [
            {
                "id": "msg-1",
                "subject": "Hello",
                "sender": {"emailAddress": {"address": "a@example.com", "name": "A"}},
                "toRecipients": [{"emailAddress": {"address": "b@example.com", "name": "B"}}],
                "receivedDateTime": "2026-07-01T12:00:00Z",
                "body": {"content": "Hi there"},
            }
        ],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/delta?$deltatoken=abc",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page):
        await sync_mail(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert await EmailsRepository(pool).count(user_id) == 1
    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "mail")
    assert state["delta_link"] == "https://graph.microsoft.com/v1.0/delta?$deltatoken=abc"
    assert state["status"] == "ok"


@pytest.mark.asyncio
async def test_sync_mail_resumes_from_stored_delta_link(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await SyncStateRepository(pool).upsert(await primary_account_id(pool, user_id), "mail", "https://graph.microsoft.com/v1.0/delta?$deltatoken=prev", "ok")

    page = {"items": [], "next_link": None, "delta_link": "https://graph.microsoft.com/v1.0/delta?$deltatoken=next"}
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page) as mock_fetch:
        await sync_mail(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    mock_fetch.assert_called_once_with("access-token", "https://graph.microsoft.com/v1.0/delta?$deltatoken=prev")


@pytest.mark.asyncio
async def test_sync_mail_handles_removed_items(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await EmailsRepository(pool).upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_message_id="msg-to-remove",
        subject="Old",
        from_address="a@example.com",
        from_name="A",
        to_recipients=[],
        received_at=None,
        body_text=None,
    )

    page = {
        "items": [{"id": "msg-to-remove", "@removed": {"reason": "deleted"}}],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/delta?$deltatoken=abc",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page):
        await sync_mail(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert await EmailsRepository(pool).count(user_id) == 0


@pytest.mark.asyncio
async def test_sync_mail_sets_not_available_on_403(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta")
    error = httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request))

    with patch("app.services.graph_sync.graph_client.fetch_delta_page", side_effect=error):
        await sync_mail(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "mail")
    assert state["status"] == "not_available"


@pytest.mark.asyncio
async def test_sync_mail_skips_when_already_not_available(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await SyncStateRepository(pool).upsert(await primary_account_id(pool, user_id), "mail", None, "not_available")

    with patch("app.services.graph_sync.graph_client.fetch_delta_page") as mock_fetch:
        await sync_mail(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_sync_calendar_upserts_with_online_meeting_info(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    page = {
        "items": [
            {
                "id": "evt-1",
                "subject": "Standup",
                "organizer": {"emailAddress": {"address": "a@example.com"}},
                "attendees": [{"emailAddress": {"address": "b@example.com", "name": "B"}}],
                "start": {"dateTime": "2026-07-01T09:00:00.0000000", "timeZone": "UTC"},
                "end": {"dateTime": "2026-07-01T09:30:00.0000000", "timeZone": "UTC"},
                "isOnlineMeeting": True,
                "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/xyz"},
                "body": {"content": "Daily sync"},
            }
        ],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/calendarView/delta?$deltatoken=abc",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page):
        await sync_calendar(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert await CalendarEventsRepository(pool).count(user_id) == 1
    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "calendar")
    assert state["status"] == "ok"

    row = await pool.fetchrow(
        "select organizer, attendees, is_online_meeting, online_meeting_join_url "
        "from public.calendar_events where user_id = $1 and provider_event_id = $2",
        user_id,
        "evt-1",
    )
    assert row["organizer"] == "a@example.com"
    assert json.loads(row["attendees"]) == [{"address": "b@example.com", "name": "B"}]
    assert row["is_online_meeting"] is True
    assert row["online_meeting_join_url"] == "https://teams.microsoft.com/l/meetup-join/xyz"


@pytest.mark.asyncio
async def test_sync_calendar_first_sync_uses_backfill_and_lookahead_window(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    page = {"items": [], "next_link": None, "delta_link": "https://graph.microsoft.com/v1.0/calendarView/delta?$deltatoken=abc"}

    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page), \
         patch("app.services.graph_sync.graph_client.calendar_delta_url") as mock_url:
        mock_url.return_value = "https://graph.microsoft.com/v1.0/me/calendarView/delta?start=x&end=y"
        await sync_calendar(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert mock_url.call_count == 1
    start_arg, end_arg = mock_url.call_args[0]
    assert (end_arg - start_arg).days > 100  # spans BACKFILL_DAYS behind + CALENDAR_LOOKAHEAD_DAYS ahead


@pytest.mark.asyncio
async def test_sync_calendar_sets_not_available_on_403(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/calendarView/delta")
    error = httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request))

    with patch("app.services.graph_sync.graph_client.fetch_delta_page", side_effect=error):
        await sync_calendar(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "calendar")
    assert state["status"] == "not_available"


@pytest.mark.asyncio
async def test_sync_calendar_resumes_from_stored_delta_link(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await SyncStateRepository(pool).upsert(
        await primary_account_id(pool, user_id),
        "calendar",
        "https://graph.microsoft.com/v1.0/calendarView/delta?$deltatoken=prev",
        "ok",
    )

    page = {
        "items": [],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/calendarView/delta?$deltatoken=next",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page) as mock_fetch:
        await sync_calendar(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    mock_fetch.assert_called_once_with(
        "access-token", "https://graph.microsoft.com/v1.0/calendarView/delta?$deltatoken=prev"
    )


@pytest.mark.asyncio
async def test_sync_calendar_handles_removed_items(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await CalendarEventsRepository(pool).upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-to-remove",
        subject="Old",
        organizer=None,
        attendees=[],
        start_time=None,
        end_time=None,
        is_online_meeting=False,
        online_meeting_join_url=None,
        body_text=None,
    )

    page = {
        "items": [{"id": "evt-to-remove", "@removed": {"reason": "deleted"}}],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/calendarView/delta?$deltatoken=abc",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page):
        await sync_calendar(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert await CalendarEventsRepository(pool).count(user_id) == 0


@pytest.mark.asyncio
async def test_sync_calendar_skips_when_already_not_available(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await SyncStateRepository(pool).upsert(await primary_account_id(pool, user_id), "calendar", None, "not_available")

    with patch("app.services.graph_sync.graph_client.fetch_delta_page") as mock_fetch:
        await sync_calendar(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_sync_chat_upserts_messages_across_chats(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    chats = [{"id": "chat-1"}, {"id": "chat-2"}]
    page = {
        "items": [
            {
                "id": "chat-msg-1",
                "from": {"user": {"displayName": "Alice"}},
                "body": {"content": "Hey"},
                "createdDateTime": "2026-07-01T12:00:00Z",
            }
        ],
        "next_link": None,
    }
    with patch("app.services.graph_sync.graph_client.list_chats", return_value=chats), \
         patch("app.services.graph_sync.graph_client.fetch_chat_messages_page", return_value=page):
        await sync_chat(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert await ChatMessagesRepository(pool).count(user_id) == 2  # one message per chat
    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "chat")
    assert state["status"] == "ok"
    assert state["delta_link"] is None


@pytest.mark.asyncio
async def test_sync_chat_sets_not_available_when_list_chats_forbidden(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/chats")
    error = httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request))

    with patch("app.services.graph_sync.graph_client.list_chats", side_effect=error), \
         patch("app.services.graph_sync.graph_client.fetch_chat_messages_page") as mock_messages:
        await sync_chat(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    mock_messages.assert_not_called()
    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "chat")
    assert state["status"] == "not_available"


@pytest.mark.asyncio
async def test_sync_chat_skips_restricted_chat_but_still_reports_ok(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    chats = [{"id": "chat-1"}]
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/chats/chat-1/messages")
    error = httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request))

    with patch("app.services.graph_sync.graph_client.list_chats", return_value=chats), \
         patch("app.services.graph_sync.graph_client.fetch_chat_messages_page", side_effect=error):
        await sync_chat(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    # A single chat being inaccessible is a per-chat restriction, not a
    # feature-unsupported signal (that's list_chats' job) - the resource as
    # a whole should still report "ok", just with that one chat skipped.
    assert await ChatMessagesRepository(pool).count(user_id) == 0
    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "chat")
    assert state["status"] == "ok"


@pytest.mark.asyncio
async def test_sync_chat_skips_restricted_chat_but_syncs_others(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    chats = [{"id": "chat-restricted"}, {"id": "chat-ok"}]

    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/chats/chat-restricted/messages")
    forbidden = httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request))

    ok_page = {
        "items": [
            {
                "id": "chat-msg-ok",
                "from": {"user": {"displayName": "Bob"}},
                "body": {"content": "hi"},
                "createdDateTime": "2026-07-01T12:00:00Z",
            }
        ],
        "next_link": None,
    }

    async def fake_fetch(access_token, url):
        if "chat-restricted" in url:
            raise forbidden
        return ok_page

    with patch("app.services.graph_sync.graph_client.list_chats", return_value=chats), \
         patch("app.services.graph_sync.graph_client.fetch_chat_messages_page", side_effect=fake_fetch):
        await sync_chat(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    assert await ChatMessagesRepository(pool).count(user_id) == 1
    state = await SyncStateRepository(pool).get(await primary_account_id(pool, user_id), "chat")
    assert state["status"] == "ok"


@pytest.mark.asyncio
async def test_sync_chat_skips_when_already_not_available(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await SyncStateRepository(pool).upsert(await primary_account_id(pool, user_id), "chat", None, "not_available")

    with patch("app.services.graph_sync.graph_client.list_chats") as mock_list:
        await sync_chat(pool, user_id, await primary_account_id(pool, user_id), "access-token")

    mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_sync_user_runs_all_three_resources(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read", "Calendars.ReadWrite", "Chat.Read"],
    )

    with patch("app.services.graph_sync.sync_mail") as mock_mail, \
         patch("app.services.graph_sync.sync_calendar") as mock_calendar, \
         patch("app.services.graph_sync.sync_chat") as mock_chat:
        mock_mail.return_value = None
        mock_calendar.return_value = None
        mock_chat.return_value = None
        await sync_user(pool, user_id)

    mock_mail.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "valid-access")
    mock_calendar.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "valid-access")
    mock_chat.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "valid-access")


@pytest.mark.asyncio
async def test_sync_user_refreshes_expired_token_first(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("expired-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        scopes=["Mail.Read"],
    )
    refreshed = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }

    with patch("app.services.account_tokens.graph_refresh_access_token", return_value=refreshed), \
         patch("app.services.graph_sync.sync_mail") as mock_mail, \
         patch("app.services.graph_sync.sync_calendar"), \
         patch("app.services.graph_sync.sync_chat"):
        await sync_user(pool, user_id)

    mock_mail.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "new-access")
    row = await get_connected_account(pool, user_id)
    assert decrypt_token(row["encrypted_access_token"]) == "new-access"


@pytest.mark.asyncio
async def test_sync_user_sets_needs_reauth_on_refresh_failure(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("expired-access"),
        encrypted_refresh_token=encrypt_token("dead-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        scopes=["Mail.Read"],
    )

    with patch("app.services.account_tokens.graph_refresh_access_token", side_effect=GraphRefreshError("expired")), \
         patch("app.services.graph_sync.sync_mail") as mock_mail:
        await sync_user(pool, user_id)

    mock_mail.assert_not_called()
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "needs_reauth"


@pytest.mark.asyncio
async def test_sync_user_noop_when_not_connected(pool, test_auth_user, no_accounts):
    user_id, _ = test_auth_user

    with patch("app.services.graph_sync.sync_mail") as mock_mail:
        await sync_user(pool, user_id)

    mock_mail.assert_not_called()


@pytest.mark.asyncio
async def test_sync_user_recovers_from_401_mid_sync_by_refreshing(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )
    refreshed = {
        "access_token": "recovered-access",
        "refresh_token": "recovered-refresh",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta")
    unauthorized = httpx.HTTPStatusError("unauthorized", request=request, response=httpx.Response(401, request=request))

    call_count = {"n": 0}

    async def fake_sync_mail(pool_arg, uid, account_id, token):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise unauthorized
        assert token == "recovered-access"

    with patch("app.services.account_tokens.graph_refresh_access_token", return_value=refreshed), \
         patch("app.services.graph_sync.sync_mail", side_effect=fake_sync_mail), \
         patch("app.services.graph_sync.sync_calendar") as mock_calendar, \
         patch("app.services.graph_sync.sync_chat") as mock_chat:
        await sync_user(pool, user_id)

    assert call_count["n"] == 2
    mock_calendar.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "recovered-access")
    mock_chat.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "recovered-access")
    row = await get_connected_account(pool, user_id)
    assert decrypt_token(row["encrypted_access_token"]) == "recovered-access"


@pytest.mark.asyncio
async def test_sync_user_sets_needs_reauth_when_401_mid_sync_refresh_fails(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=encrypt_token("dead-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta")
    unauthorized = httpx.HTTPStatusError("unauthorized", request=request, response=httpx.Response(401, request=request))

    with patch("app.services.account_tokens.graph_refresh_access_token", side_effect=GraphRefreshError("expired")), \
         patch("app.services.graph_sync.sync_mail", side_effect=unauthorized), \
         patch("app.services.graph_sync.sync_calendar") as mock_calendar, \
         patch("app.services.graph_sync.sync_chat") as mock_chat:
        await sync_user(pool, user_id)

    mock_calendar.assert_not_called()
    mock_chat.assert_not_called()
    account = await get_connected_account(pool, user_id)
    assert account["connection_status"] == "needs_reauth"


@pytest.mark.asyncio
async def test_sync_user_isolates_resource_failures(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    with patch("app.services.graph_sync.sync_mail", side_effect=RuntimeError("mail boom")), \
         patch("app.services.graph_sync.sync_calendar") as mock_calendar, \
         patch("app.services.graph_sync.sync_chat") as mock_chat:
        with pytest.raises(RuntimeError, match="mail boom"):
            await sync_user(pool, user_id)

    mock_calendar.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "valid-access")
    mock_chat.assert_called_once_with(pool, user_id, await primary_account_id(pool, user_id), "valid-access")


@pytest.mark.asyncio
async def test_sync_user_runs_extraction_after_sync(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    with patch("app.services.graph_sync.sync_mail"), \
         patch("app.services.graph_sync.sync_calendar"), \
         patch("app.services.graph_sync.sync_chat"), \
         patch("app.services.graph_sync.extract_user") as mock_extract_user:
        await sync_user(pool, user_id)

    mock_extract_user.assert_called_once()
    call_args = mock_extract_user.call_args[0]
    assert call_args[0] is pool
    assert call_args[1] == user_id


@pytest.mark.asyncio
async def test_sync_user_extraction_failure_does_not_propagate(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    with patch("app.services.graph_sync.sync_mail"), \
         patch("app.services.graph_sync.sync_calendar"), \
         patch("app.services.graph_sync.sync_chat"), \
         patch("app.services.graph_sync.extract_user", side_effect=RuntimeError("extraction boom")):
        await sync_user(pool, user_id)  # must not raise


@pytest.mark.asyncio
async def test_sync_user_still_attempts_extraction_when_a_resource_sync_fails(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    with patch("app.services.graph_sync.sync_mail", side_effect=RuntimeError("mail boom")), \
         patch("app.services.graph_sync.sync_calendar"), \
         patch("app.services.graph_sync.sync_chat"), \
         patch("app.services.graph_sync.extract_user") as mock_extract_user:
        with pytest.raises(RuntimeError, match="mail boom"):
            await sync_user(pool, user_id)

    mock_extract_user.assert_called_once()


@pytest.mark.asyncio
async def test_sync_user_logs_when_extraction_fails(pool, test_auth_user, caplog):
    """A broken extraction pass must not vanish silently.

    extract_user is wrapped in `except Exception: pass`, so the AI pipeline can
    fail on every sync - every 15 minutes, for every user - without producing a
    single signal. Swallowing is the right behaviour (a failed extraction should
    not fail the whole sync) but it has to say so.
    """
    import logging

    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    # sync_user iterates connected accounts now, so there has to be one.
    await connect_account(
        pool,
        user_id=user_id,
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=[],
    )

    async def no_op_sync(pool_arg, uid, account_id, token):
        return None

    with caplog.at_level(logging.ERROR), \
         patch("app.services.graph_sync.sync_mail", side_effect=no_op_sync), \
         patch("app.services.graph_sync.sync_calendar", side_effect=no_op_sync), \
         patch("app.services.graph_sync.sync_chat", side_effect=no_op_sync), \
         patch(
             "app.services.graph_sync.extract_user",
             side_effect=RuntimeError("extraction blew up"),
         ):
        await sync_user(pool, user_id)

    assert "extraction blew up" in caplog.text, "extraction failure was not logged"
