import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.action_items import ActionItemsRepository

router = APIRouter(prefix="/api/action-items", tags=["action-items"])


class UpdateActionItemStatus(BaseModel):
    status: Literal["open", "done"]


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


def _serialize_plain(row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "direction": row["direction"],
        "status": row["status"],
        "due_date": row["due_date"],
        "contact_id": row["contact_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
async def list_action_items(
    direction: Literal["mine", "theirs"] | None = None,
    include_done: bool = False,
    current_user: CurrentUser = Depends(get_current_user),
):
    pool = await get_pool()
    rows = await ActionItemsRepository(pool).list_for_user(
        current_user.user_id, direction=direction, include_done=include_done
    )
    return [_serialize(row) for row in rows]


@router.delete("/completed")
async def clear_completed_action_items(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove this user's finished tasks.

    Declared before /{item_id} on purpose: FastAPI matches routes in order, and
    a later literal path would be shadowed by the parameterised one, which would
    then try to parse "completed" as a uuid.

    Permanent rather than archived - the task has served its purpose once done,
    and an archive is just a longer list to ignore. Anything scheduled keeps its
    calendar event.
    """
    pool = await get_pool()
    removed = await ActionItemsRepository(pool).delete_completed(current_user.user_id)
    return {"removed": removed}


@router.patch("/{item_id}")
async def update_action_item_status(
    item_id: uuid.UUID,
    body: UpdateActionItemStatus,
    current_user: CurrentUser = Depends(get_current_user),
):
    pool = await get_pool()
    row = await ActionItemsRepository(pool).update_status(
        current_user.user_id, item_id, body.status
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Action item not found")
    return _serialize_plain(row)
