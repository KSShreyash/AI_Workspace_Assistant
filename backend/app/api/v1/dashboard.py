from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.action_items import ActionItemsRepository
from app.repositories.contacts import ContactsRepository
from app.repositories.dashboard_stats import DashboardStatsRepository

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_ACTIVITY_LIMIT = 20


def _merge_activity(contact_rows, action_item_rows, limit: int) -> list[dict]:
    events = []
    for row in contact_rows:
        events.append({
            "type": "contact_updated",
            "id": row["id"],
            "timestamp": row["updated_at"],
            "display_name": row["display_name"],
            "email_address": row["email_address"],
        })
    for row in action_item_rows:
        events.append({
            "type": "action_item_created",
            "id": row["id"],
            "timestamp": row["created_at"],
            "text": row["text"],
            "direction": row["direction"],
        })
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


@router.get("")
async def get_dashboard(current_user: CurrentUser = Depends(get_current_user)):
    pool = await get_pool()
    contacts_repo = ContactsRepository(pool)
    action_items_repo = ActionItemsRepository(pool)

    contact_count = await contacts_repo.count(current_user.user_id)
    open_action_item_count = await action_items_repo.count_open(current_user.user_id)
    recent_contacts = await contacts_repo.list_recent(current_user.user_id, _ACTIVITY_LIMIT)
    recent_action_items = await action_items_repo.list_recent(current_user.user_id, _ACTIVITY_LIMIT)

    return {
        "contact_count": contact_count,
        "open_action_item_count": open_action_item_count,
        "activity": _merge_activity(recent_contacts, recent_action_items, _ACTIVITY_LIMIT),
    }


@router.get("/stats")
async def get_dashboard_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Aggregates for the dashboard, computed in SQL.

    Separate from GET /api/dashboard rather than folded into it: that endpoint
    is the activity feed and is already consumed as-is, while this one is a
    fixed-size summary. Keeping them apart means the feed can paginate later
    without disturbing the summary.
    """
    pool = await get_pool()
    stats = DashboardStatsRepository(pool)
    user_id = current_user.user_id

    return {
        "emails": await stats.email_counts(user_id),
        "tasks": await stats.task_counts(user_id),
        "meetings": await stats.meeting_counts(user_id),
        "categories": await stats.categories(user_id),
        "important_people": await stats.important_people(user_id),
        "daily": await stats.daily_series(user_id),
        "upcoming": await stats.upcoming(user_id),
    }
