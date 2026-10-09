from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.deps import CurrentUser, get_current_user
from app.core.security import encrypt_token
from app.db.session import get_pool
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.profiles import ProfilesRepository
from app.schemas.auth import ConnectAccountIn
from app.services.graph_sync import sync_account

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/accounts", status_code=204)
async def connect_account(
    body: ConnectAccountIn,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Connect a mailbox, or reconnect one that expired.

    The same endpoint serves both: upsert keys on
    (user_id, provider, provider_account_id), so re-authorising a known mailbox
    refreshes its tokens and clears needs_reauth rather than creating a second
    row for the same inbox.
    """
    pool = await get_pool()
    await ProfilesRepository(pool).upsert(current_user.user_id, current_user.email)

    account_id = await ConnectedAccountsRepository(pool).upsert(
        user_id=current_user.user_id,
        provider=body.provider,
        provider_account_id=body.provider_account_id,
        email_address=body.email_address,
        display_name=body.display_name,
        encrypted_access_token=encrypt_token(body.provider_token),
        encrypted_refresh_token=encrypt_token(body.provider_refresh_token),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=body.expires_in),
        scopes=body.scopes,
    )

    # Backgrounded: a first sync walks 30 days of mail and must not hold the
    # browser on the OAuth redirect.
    background_tasks.add_task(sync_account, pool, current_user.user_id, account_id)
