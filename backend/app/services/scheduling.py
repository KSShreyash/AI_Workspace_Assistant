import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from app.repositories.action_items import ActionItemsRepository
from app.repositories.calendar_events import CalendarEventsRepository
from app.services import graph_client
from app.services.account_tokens import (
    get_primary_account,
    get_valid_access_token,
    refresh_and_persist,
)

SLOT_MINUTES = 30
LOOKAHEAD_DAYS = 14
WORK_START_HOUR = 9
WORK_END_HOUR = 17
MAX_SUGGESTIONS = 10


def suggest_slots(
    now_utc: datetime,
    timezone_name: str | None,
    busy: list[tuple[datetime, datetime]],
) -> list[dict]:
    tz = ZoneInfo(timezone_name) if timezone_name else ZoneInfo("UTC")
    local_now = now_utc.astimezone(tz)

    slots: list[dict] = []
    for day_offset in range(LOOKAHEAD_DAYS):
        current_date = (local_now + timedelta(days=day_offset)).date()
        if current_date.weekday() >= 5:
            continue

        slot_start = datetime.combine(current_date, time(WORK_START_HOUR, 0), tzinfo=tz)
        day_end = datetime.combine(current_date, time(WORK_END_HOUR, 0), tzinfo=tz)

        while slot_start + timedelta(minutes=SLOT_MINUTES) <= day_end:
            slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
            if slot_start >= local_now and not _overlaps_any(slot_start, slot_end, busy):
                slots.append({
                    "start": slot_start.astimezone(timezone.utc),
                    "end": slot_end.astimezone(timezone.utc),
                })
                if len(slots) >= MAX_SUGGESTIONS:
                    return slots
            slot_start += timedelta(minutes=SLOT_MINUTES)

    return slots


def _overlaps_any(
    start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]
) -> bool:
    return any(start < busy_end and end > busy_start for busy_start, busy_end in busy)


def _graph_datetime(dt: datetime) -> dict:
    naive_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return {"dateTime": naive_utc.isoformat(), "timeZone": "UTC"}


async def create_meeting(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    item_text: str,
    start: datetime,
    end: datetime,
    online_meeting: bool,
    contact_email: str | None,
    contact_display_name: str | None,
) -> asyncpg.Record | None:
    # Meetings go on the primary account's calendar - the one the user
    # designated as their main one.
    account = await get_primary_account(pool, user_id)
    if account is None:
        return None
    access_token = await get_valid_access_token(pool, account)
    if access_token is None:
        return None

    payload: dict = {
        "subject": item_text,
        "start": _graph_datetime(start),
        "end": _graph_datetime(end),
        "isOnlineMeeting": online_meeting,
    }
    if online_meeting:
        payload["onlineMeetingProvider"] = "teamsForBusiness"
    if contact_email:
        payload["attendees"] = [{
            "emailAddress": {"address": contact_email, "name": contact_display_name or contact_email},
            "type": "required",
        }]

    try:
        graph_event = await graph_client.create_calendar_event(access_token, payload)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            access_token = await refresh_and_persist(pool, account)
            if access_token is None:
                return None
            graph_event = await graph_client.create_calendar_event(access_token, payload)
        else:
            raise

    online_meeting_info = graph_event.get("onlineMeeting") or {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            calendar_event_id = await CalendarEventsRepository(pool).upsert(
                user_id=user_id,
                account_id=account["id"],
                provider_event_id=graph_event["id"],
                subject=graph_event.get("subject"),
                organizer=((graph_event.get("organizer") or {}).get("emailAddress") or {}).get("address"),
                attendees=[(a.get("emailAddress") or {}) for a in graph_event.get("attendees", [])],
                start_time=start,
                end_time=end,
                is_online_meeting=graph_event.get("isOnlineMeeting", False),
                online_meeting_join_url=online_meeting_info.get("joinUrl"),
                body_text=None,
                conn=conn,
            )
            updated_item = await ActionItemsRepository(pool).set_scheduled_calendar_event_id(
                user_id, item_id, calendar_event_id, conn=conn,
            )
            if updated_item is None:
                # Another concurrent request already won the race and set the pointer
                # first. The Graph event above was still created and can't be undone
                # here, but we must not silently overwrite the winning request's local
                # pointer. Signal the caller so it can return a 409 instead of a false
                # success.
                return None

    return updated_item
