import uuid

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.emails import EmailsRepository
from app.core.text import strip_html

router = APIRouter(prefix="/api/emails", tags=["emails"])

# Enough to read a subject line in context. Bodies are routinely megabytes of
# HTML, and sending them whole would make this the slowest endpoint in the app
# for no visible benefit - the list only renders a snippet.
_PREVIEW_CHARS = 200


@router.get("")
async def list_emails(
    current_user: CurrentUser = Depends(get_current_user),
    account_id: uuid.UUID | None = None,
    # Capped by the framework rather than clamped silently: limit is
    # user-supplied, and honouring limit=1000000 would hand anyone a way to make
    # the server read the whole table.
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """A page of the user's mail, merged across every connected mailbox.

    Each message names the mailbox it arrived in. This is the one page in the
    product that needs that: two inboxes in one list are indistinguishable
    without it, and knowing which address a message came to is the first thing
    anyone needs in order to reply from the right one.
    """
    pool = await get_pool()
    rows = await EmailsRepository(pool).list_for_user(
        current_user.user_id, limit=limit, offset=offset, account_id=account_id
    )

    # The repository fetches one extra row so "is there more" costs no second
    # query over the whole table.
    has_more = len(rows) > limit
    page = rows[:limit]

    return {
        "emails": [
            {
                "id": str(row["id"]),
                "provider_message_id": row["provider_message_id"],
                "subject": row["subject"],
                "from_address": row["from_address"],
                "from_name": row["from_name"],
                "received_at": row["received_at"].isoformat() if row["received_at"] else None,
                "preview": (strip_html(row["body_text"]) or "")[:_PREVIEW_CHARS],
                "extracted": row["extracted_at"] is not None,
                "mailbox": {
                    "id": str(row["account_id"]),
                    "email_address": row["account_email"],
                    "provider": row["account_provider"],
                },
            }
            for row in page
        ],
        "has_more": has_more,
    }
