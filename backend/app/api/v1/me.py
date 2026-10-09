import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException

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
