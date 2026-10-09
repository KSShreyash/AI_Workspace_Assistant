from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.calendar_events import CalendarEventsRepository

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("")
async def list_upcoming(current_user: CurrentUser = Depends(get_current_user)):
    pool = await get_pool()
    rows = await CalendarEventsRepository(pool).list_upcoming(current_user.user_id)
    return [
        {
            "id": str(row["id"]),
            "subject": row["subject"],
            "organizer": row["organizer"],
            "start_time": row["start_time"].isoformat(),
            "end_time": row["end_time"].isoformat() if row["end_time"] else None,
            "is_online_meeting": row["is_online_meeting"],
            "join_url": row["online_meeting_join_url"],
        }
        for row in rows
    ]
