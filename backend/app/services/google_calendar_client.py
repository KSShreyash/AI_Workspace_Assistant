"""Google Calendar API client.

A third incremental model, different again from the other two:

                    Graph              Gmail            Google Calendar
  cursor            deltaLink (URL)    historyId (int)  syncToken (opaque)
  expired cursor    410                404              410
  deletions         @removed marker    TRASH label      status=cancelled

Two of Google Calendar's rules are easy to miss and both fail loudly only in
production:

1. syncToken cannot be combined with timeMin, timeMax or orderBy - the API
   returns 400, so an incremental sync that also passes a window never runs.
2. Without singleEvents the response contains recurrence masters and no
   occurrences, so a weekly standup blocks nothing and never appears.

normalize_event emits the same keys a Graph event produces, so
CalendarEventsRepository and everything above it stay provider-agnostic.
"""

import asyncio
import logging
from datetime import datetime, time, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"

# Google's own video entry point types, in preference order. A phone bridge is
# not a join URL.
_VIDEO_ENTRY_TYPES = ("video", "hangoutsMeet")


class SyncTokenExpired(Exception):
    """The syncToken is too old to serve; the caller must resync in full."""


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict) -> dict:
    response = await client.get(url, headers=headers)
    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", "1"))
        await asyncio.sleep(retry_after)
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_events_page(
    access_token: str,
    calendar_id: str = "primary",
    *,
    sync_token: str | None = None,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    page_token: str | None = None,
) -> dict:
    """One page of events.list.

    Returns items / next_page_token / next_sync_token. next_sync_token is
    present only on the final page - storing a mid-walk token would skip
    everything after it.
    """
    url = (
        f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{quote(calendar_id)}/events"
        # Expand recurrences into instances. Without this the response is
        # recurrence masters, which have no start time to block anything with.
        "?singleEvents=true&maxResults=250"
    )

    if sync_token:
        # Deliberately nothing else: Google rejects syncToken alongside
        # timeMin, timeMax or orderBy with a 400.
        url += f"&syncToken={quote(sync_token)}"
    else:
        if time_min is not None:
            url += f"&timeMin={quote(_rfc3339(time_min))}"
        if time_max is not None:
            url += f"&timeMax={quote(_rfc3339(time_max))}"

    if page_token:
        url += f"&pageToken={quote(page_token)}"

    async with httpx.AsyncClient() as client:
        try:
            body = await _get_json(
                client, url, {"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 410:
                raise SyncTokenExpired("calendar syncToken is no longer valid") from exc
            raise

    return {
        "items": body.get("items") or [],
        "next_page_token": body.get("nextPageToken"),
        "next_sync_token": body.get("nextSyncToken"),
    }


def _parse_time(value: dict | None) -> datetime | None:
    if not value:
        return None

    if date_time := value.get("dateTime"):
        parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Stored and queried in UTC everywhere else; keeping the organiser's
        # offset would put the event in the wrong place in every comparison.
        return parsed.astimezone(timezone.utc)

    if date := value.get("date"):
        # All-day events carry `date` instead. Reading only dateTime leaves both
        # ends null, and a null-timed event is invisible to every availability
        # query - a week of leave would block nothing. Treated as UTC midnight;
        # the Microsoft side has the same simplification.
        return datetime.combine(datetime.fromisoformat(date).date(), time.min, tzinfo=timezone.utc)

    return None


def _join_url(raw: dict) -> str | None:
    if hangout := raw.get("hangoutLink"):
        return hangout

    # Meetings created through conferenceData - including third-party ones like
    # Zoom - have no hangoutLink.
    for entry in (raw.get("conferenceData") or {}).get("entryPoints") or []:
        if entry.get("entryPointType") in _VIDEO_ENTRY_TYPES and entry.get("uri"):
            return entry["uri"]

    return None


def normalize_event(raw: dict) -> dict:
    """A Google event as CalendarEventsRepository expects one.

    `removed` is part of the contract because Google reports deletions as events
    with status=cancelled rather than as a removal marker. Upserting one would
    put a deleted meeting back on the calendar.
    """
    attendees = [
        {"address": attendee.get("email"), "name": attendee.get("displayName") or None}
        for attendee in (raw.get("attendees") or [])
        if attendee.get("email")
    ]

    join_url = _join_url(raw)

    return {
        "id": raw.get("id"),
        "removed": raw.get("status") == "cancelled",
        "subject": raw.get("summary"),
        "organizer": (raw.get("organizer") or {}).get("email"),
        "attendees": attendees,
        "start_time": _parse_time(raw.get("start")),
        "end_time": _parse_time(raw.get("end")),
        "is_online_meeting": join_url is not None,
        "online_meeting_join_url": join_url,
        "body_text": raw.get("description"),
    }
