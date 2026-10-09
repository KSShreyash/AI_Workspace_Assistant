import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx

from app.core.config import settings
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.chat_messages import ChatMessagesRepository
from app.repositories.emails import EmailsRepository
from app.repositories.sync_state import SyncStateRepository
from app.services import graph_client
from app.services.ai_extraction import extract_user
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.services.account_tokens import get_valid_access_token, refresh_and_persist

logger = logging.getLogger(__name__)

BACKFILL_DAYS = 30
CALENDAR_LOOKAHEAD_DAYS = 90

_FRACTION_RE = re.compile(r"(\.\d{7,})")


def _parse_graph_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    match = _FRACTION_RE.search(value)
    if match:
        value = value.replace(match.group(1), match.group(1)[:7])
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def sync_mail(
    pool: asyncpg.Pool, user_id: uuid.UUID, account_id: uuid.UUID, access_token: str
) -> None:
    sync_state = SyncStateRepository(pool)
    emails = EmailsRepository(pool)

    state = await sync_state.get(account_id, "mail")
    if state is not None and state["status"] == "not_available":
        return

    url = (
        state["delta_link"]
        if state and state["delta_link"]
        else graph_client.mail_delta_url(datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS))
    )

    try:
        delta_link = None
        while True:
            page = await graph_client.fetch_delta_page(access_token, url)
            for item in page["items"]:
                if "@removed" in item:
                    await emails.delete(account_id=account_id, provider_message_id=item["id"])
                    continue
                sender = (item.get("sender") or {}).get("emailAddress") or {}
                await emails.upsert(
                    user_id=user_id,
                    account_id=account_id,
                    provider_message_id=item["id"],
                    subject=item.get("subject"),
                    from_address=sender.get("address"),
                    from_name=sender.get("name"),
                    to_recipients=[
                        (r.get("emailAddress") or {}) for r in item.get("toRecipients", [])
                    ],
                    received_at=_parse_graph_datetime(item.get("receivedDateTime")),
                    body_text=(item.get("body") or {}).get("content"),
                )
            if page["delta_link"]:
                delta_link = page["delta_link"]
                break
            url = page["next_link"]
        await sync_state.upsert(account_id, "mail", delta_link, "ok")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 403):
            await sync_state.upsert(account_id, "mail", None, "not_available")
        else:
            raise


async def sync_calendar(
    pool: asyncpg.Pool, user_id: uuid.UUID, account_id: uuid.UUID, access_token: str
) -> None:
    sync_state = SyncStateRepository(pool)
    events = CalendarEventsRepository(pool)

    state = await sync_state.get(account_id, "calendar")
    if state is not None and state["status"] == "not_available":
        return

    if state and state["delta_link"]:
        url = state["delta_link"]
    else:
        now = datetime.now(timezone.utc)
        url = graph_client.calendar_delta_url(
            now - timedelta(days=BACKFILL_DAYS),
            now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS),
        )

    try:
        delta_link = None
        while True:
            page = await graph_client.fetch_delta_page(access_token, url)
            for item in page["items"]:
                if "@removed" in item:
                    await events.delete(account_id=account_id, provider_event_id=item["id"])
                    continue
                online_meeting = item.get("onlineMeeting") or {}
                await events.upsert(
                    user_id=user_id,
                    account_id=account_id,
                    provider_event_id=item["id"],
                    subject=item.get("subject"),
                    organizer=((item.get("organizer") or {}).get("emailAddress") or {}).get("address"),
                    attendees=[
                        (a.get("emailAddress") or {}) for a in item.get("attendees", [])
                    ],
                    start_time=_parse_graph_datetime((item.get("start") or {}).get("dateTime")),
                    end_time=_parse_graph_datetime((item.get("end") or {}).get("dateTime")),
                    is_online_meeting=item.get("isOnlineMeeting", False),
                    online_meeting_join_url=online_meeting.get("joinUrl"),
                    body_text=(item.get("body") or {}).get("content"),
                )
            if page["delta_link"]:
                delta_link = page["delta_link"]
                break
            url = page["next_link"]
        await sync_state.upsert(account_id, "calendar", delta_link, "ok")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 403):
            await sync_state.upsert(account_id, "calendar", None, "not_available")
        else:
            raise


async def sync_chat(
    pool: asyncpg.Pool, user_id: uuid.UUID, account_id: uuid.UUID, access_token: str
) -> None:
    sync_state = SyncStateRepository(pool)
    messages = ChatMessagesRepository(pool)

    state = await sync_state.get(account_id, "chat")
    if state is not None and state["status"] == "not_available":
        return

    since = (
        state["last_synced_at"]
        if state and state["last_synced_at"]
        else datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
    )

    try:
        chats = await graph_client.list_chats(access_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 403):
            await sync_state.upsert(account_id, "chat", None, "not_available")
            return
        raise

    for chat in chats:
        chat_id = chat["id"]
        url = graph_client.chat_messages_url(chat_id, since)
        try:
            while url:
                page = await graph_client.fetch_chat_messages_page(access_token, url)
                for item in page["items"]:
                    from_user = (item.get("from") or {}).get("user") or {}
                    await messages.upsert(
                        user_id=user_id,
                        account_id=account_id,
                        provider_chat_id=chat_id,
                        provider_message_id=item["id"],
                        from_user=from_user.get("displayName"),
                        content=(item.get("body") or {}).get("content"),
                        sent_at=_parse_graph_datetime(item.get("createdDateTime")),
                    )
                url = page["next_link"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 403):
                # This specific chat isn't accessible (e.g. a per-chat policy
                # restriction) - skip it rather than disabling chat sync for
                # the whole account, since list_chats already succeeded.
                continue
            raise

    await sync_state.upsert(account_id, "chat", None, "ok")


async def sync_account(
    pool: asyncpg.Pool, user_id: uuid.UUID, account_id: uuid.UUID
) -> None:
    """Sync one connected mailbox.

    Calendar and chat run only for the primary account: a user's main calendar
    is a single thing, and Teams chat has no equivalent on a secondary Gmail
    mailbox. Mail runs for every account.
    """
    accounts = ConnectedAccountsRepository(pool)
    account = await accounts.get(user_id, account_id)
    if account is None:
        logger.info("Account %s is no longer connected; nothing to sync", account_id)
        return

    access_token = await get_valid_access_token(pool, account)
    if access_token is None:
        return

    sync_fns = [sync_mail]
    if account["is_primary"]:
        sync_fns += [sync_calendar, sync_chat]

    errors: list[Exception] = []
    for sync_fn in sync_fns:
        try:
            await sync_fn(pool, user_id, account_id, access_token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                access_token = await refresh_and_persist(pool, account)
                if access_token is None:
                    return
                try:
                    await sync_fn(pool, user_id, account_id, access_token)
                except Exception as retry_exc:
                    errors.append(retry_exc)
            else:
                errors.append(exc)
        except Exception as exc:
            errors.append(exc)

    try:
        await extract_user(pool, user_id, settings.extraction_batch_limit)
    except Exception:
        # Deliberately not re-raised: a failed extraction should not fail the
        # sync that already succeeded in fetching the messages. But it must be
        # visible, or the AI pipeline can break on every run for every user and
        # produce no signal at all.
        logger.exception("Extraction failed after sync for user %s", user_id)

    if errors:
        raise errors[0]


async def sync_user(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    """Sync every mailbox this user has connected.

    One failing account does not stop the others - a user with a working
    Outlook and an expired Gmail should still get their Outlook synced - but
    the first error is re-raised afterwards so the caller can count the failure.
    """
    accounts = await ConnectedAccountsRepository(pool).list_for_user(user_id)

    errors: list[Exception] = []
    for account in accounts:
        if account["connection_status"] != "connected":
            continue
        try:
            await sync_account(pool, user_id, account["id"])
        except Exception as exc:
            logger.exception(
                "Sync failed for %s account %s", account["provider"], account["id"]
            )
            errors.append(exc)

    if errors:
        raise errors[0]
