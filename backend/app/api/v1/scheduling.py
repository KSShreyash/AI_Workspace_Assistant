import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.action_items import ActionItemsRepository
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.profiles import ProfilesRepository
from app.services import scheduling

router = APIRouter(prefix="/api/action-items", tags=["scheduling"])


class ScheduleActionItem(BaseModel):
    start: datetime
    end: datetime
    online_meeting: bool


def _serialize(row) -> dict:
    contact = None
    if row["contact_id"] is not None:
        contact = {
            "id": row["contact_id"],
            "display_name": row["contact_display_name"],
            "email_address": row["contact_email_address"],
        }
    return {
        "id": row["id"],
        "text": row["text"],
        "direction": row["direction"],
        "status": row["status"],
        "due_date": row["due_date"],
        "contact": contact,
        "scheduled_calendar_event_id": row["scheduled_calendar_event_id"],
        "scheduled_start_time": row["scheduled_start_time"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _get_schedulable_item(pool, user_id: uuid.UUID, item_id: uuid.UUID):
    item = await ActionItemsRepository(pool).get(user_id, item_id)
    if item is None or item["contact_id"] is None:
        raise HTTPException(status_code=404, detail="Action item not found")
    if item["scheduled_calendar_event_id"] is not None:
        raise HTTPException(status_code=409, detail="Action item is already scheduled")
    return item


@router.get("/{item_id}/schedule-suggestions")
async def get_schedule_suggestions(
    item_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
):
    pool = await get_pool()
    await _get_schedulable_item(pool, current_user.user_id, item_id)

    profile = await ProfilesRepository(pool).get(current_user.user_id)
    timezone_name = profile["timezone"] if profile else None

    now_utc = datetime.now(timezone.utc)
    busy_rows = await CalendarEventsRepository(pool).list_busy_between(
        current_user.user_id, now_utc, now_utc + timedelta(days=scheduling.LOOKAHEAD_DAYS)
    )
    busy = [(row["start_time"], row["end_time"]) for row in busy_rows]

    return scheduling.suggest_slots(now_utc, timezone_name, busy)


@router.post("/{item_id}/schedule")
async def schedule_action_item(
    item_id: uuid.UUID,
    body: ScheduleActionItem,
    current_user: CurrentUser = Depends(get_current_user),
):
    pool = await get_pool()
    item = await _get_schedulable_item(pool, current_user.user_id, item_id)

    try:
        updated = await scheduling.create_meeting(
            pool,
            current_user.user_id,
            item_id,
            item_text=item["text"],
            start=body.start,
            end=body.end,
            online_meeting=body.online_meeting,
            contact_email=item["contact_email_address"],
            contact_display_name=item["contact_display_name"],
        )
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=502, detail="Failed to create the meeting. Please try again.")

    if updated is None:
        # create_meeting returns None for two reasons: no usable Microsoft
        # mailbox, or one that needs reconnecting. Naming both beats asserting
        # the wrong one - a Gmail-only user was previously told to reconnect a
        # Microsoft account they had never connected.
        raise HTTPException(
            status_code=409,
            detail=(
                "Scheduling needs a connected Outlook mailbox. Connect or "
                "reconnect one in Settings."
            ),
        )

    return _serialize(updated)
