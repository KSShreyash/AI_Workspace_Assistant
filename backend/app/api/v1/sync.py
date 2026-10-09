import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.services.graph_sync import sync_account, sync_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/run/me")
async def run_my_sync(current_user: CurrentUser = Depends(get_current_user)):
    pool = await get_pool()
    # Pressing "Sync now" while the cron is mid-run should say so rather than
    # silently reporting success for work it did not do.
    ran = await sync_user(pool, current_user.user_id)
    return {"status": "ok" if ran else "already_running"}


@router.post("/run")
async def run_bulk_sync(x_sync_secret: str | None = Header(default=None)):
    if not x_sync_secret or not hmac.compare_digest(x_sync_secret, settings.sync_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid sync secret")

    pool = await get_pool()
    # Accounts, not users. A user with a working Outlook and an expired Gmail
    # should still have their Outlook synced, and list_syncable already
    # excludes anything not in the connected state.
    accounts = await ConnectedAccountsRepository(pool).list_syncable()

    synced = 0
    failed = 0
    skipped = 0
    for account in accounts:
        try:
            if await sync_account(pool, account["user_id"], account["id"]):
                synced += 1
            else:
                # Another sync for this user holds the lock. Not a failure.
                skipped += 1
        except Exception:
            failed += 1
            # The caller is a cron using `curl -sf`, which discards the body and
            # hides non-200s. A bare count tells nobody which mailbox broke or
            # why, so one account failing every fifteen minutes stays invisible.
            logger.exception(
                "Bulk sync failed for %s account %s (user %s)",
                account["provider"],
                account["id"],
                account["user_id"],
            )

    if failed:
        logger.warning(
            "Bulk sync finished: %s synced, %s failed, %s skipped", synced, failed, skipped
        )

    return {"synced": synced, "failed": failed, "skipped": skipped}
