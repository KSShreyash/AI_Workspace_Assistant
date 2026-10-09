import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.services.account_tokens import get_valid_access_token
from app.services.graph_client import get_me

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/accounts")
async def list_accounts(current_user: CurrentUser = Depends(get_current_user)):
    """Every mailbox this user has connected, with its own status.

    Status is per account: one expired Gmail does not make a working Outlook
    look broken, which is what a single per-user flag used to do.
    """
    pool = await get_pool()
    accounts = await ConnectedAccountsRepository(pool).list_for_user(current_user.user_id)
    return [
        {
            "id": str(account["id"]),
            "provider": account["provider"],
            "email_address": account["email_address"],
            "display_name": account["display_name"],
            "status": account["connection_status"],
            "is_primary": account["is_primary"],
        }
        for account in accounts
    ]


@router.delete("/accounts/{account_id}", status_code=204)
async def disconnect_account(
    account_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
):
    """Disconnect a mailbox.

    Its synced mail, events and sync cursor go with it - the foreign keys
    cascade. Leaving them behind would show the user messages from an inbox they
    just removed, with no way to refresh or delete them.
    """
    pool = await get_pool()
    removed = await ConnectedAccountsRepository(pool).delete(
        current_user.user_id, account_id
    )
    if not removed:
        # Scoped by user_id, so another user's mailbox is indistinguishable from
        # one that does not exist. That is the intent: 404 rather than 403 does
        # not confirm that the id is real.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")


@router.post("/accounts/{account_id}/primary", status_code=204)
async def set_primary_account(
    account_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
):
    """Choose which mailbox calendar and Teams chat follow."""
    pool = await get_pool()
    accounts = ConnectedAccountsRepository(pool)

    account = await accounts.get(current_user.user_id, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    # Calendar and chat are Graph-only. Choosing a Gmail mailbox while an
    # Outlook one is connected would leave both permanently unsynced, so it is
    # refused rather than silently accepted. A user with only Google mailboxes
    # is unaffected: primary means nothing for them, and refusing would just
    # block a harmless choice.
    if account["provider"] != "microsoft" and await accounts.has_provider(
        current_user.user_id, "microsoft"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="calendar and chat require a Microsoft mailbox as primary",
        )

    await accounts.set_primary(current_user.user_id, account_id)


@router.get("/graph-status")
async def graph_status(current_user: CurrentUser = Depends(get_current_user)):
    """Liveness of the primary account, confirmed against the provider.

    Kept distinct from /accounts, which reads stored state: this one actually
    calls the provider, so it detects consent revoked out-of-band rather than
    reporting the last thing we happened to write down.
    """
    pool = await get_pool()
    accounts = ConnectedAccountsRepository(pool)

    account = await accounts.get_primary(current_user.user_id)
    if account is None:
        raise HTTPException(status_code=404, detail="No mailbox connected")

    access_token = await get_valid_access_token(pool, account)
    if access_token is None:
        raise HTTPException(status_code=409, detail="needs_reauth")

    try:
        graph_me = await get_me(access_token)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Graph /me failed for account %s: status=%s body=%s",
            account["id"],
            exc.response.status_code,
            exc.response.text,
        )
        if exc.response.status_code == 401:
            await accounts.set_status(current_user.user_id, account["id"], "needs_reauth")
            raise HTTPException(status_code=409, detail="needs_reauth")
        raise HTTPException(status_code=502, detail="graph_me_failed")

    await accounts.set_status(current_user.user_id, account["id"], "connected")
    return {"connected": True, "graph_me": graph_me}
