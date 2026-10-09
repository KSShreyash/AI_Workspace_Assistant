"""Managing several connected mailboxes.

Connecting one was already possible. Living with several needs two more things:
removing one, and choosing which one calendar and Teams chat follow. Both have a
failure mode that is silent rather than loud, so both are pinned here.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.core.security import encrypt_token
from app.main import app
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.emails import EmailsRepository
from app.repositories.profiles import ProfilesRepository
from tests.helpers import connect_account


@asynccontextmanager
async def as_user(user_id, email):
    """Authenticated client for one user.

    The override is cleared in a finally: leaking it would silently authenticate
    every later test in the session as this user.
    """
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, email=email
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _connect(pool, user_id, provider, pid, *, primary=False):
    return await connect_account(
        pool,
        user_id=user_id,
        provider=provider,
        provider_account_id=pid,
        email_address=f"{pid}@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=[],
        is_primary=primary,
    )


# --------------------------------------------------------------------------
# Disconnect
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnecting_removes_only_that_mailbox(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    keep = await _connect(pool, user_id, "microsoft", "ms-keep", primary=True)
    drop = await _connect(pool, user_id, "google", "g-drop")

    async with as_user(user_id, email) as client:
        response = await client.delete(f"/api/me/accounts/{drop}")

    assert response.status_code == 204
    remaining = await ConnectedAccountsRepository(pool).list_for_user(user_id)
    assert [a["id"] for a in remaining] == [keep]


@pytest.mark.asyncio
async def test_disconnecting_takes_that_mailboxs_synced_mail_with_it(pool, test_auth_user, no_accounts):
    """Leaving the mail behind would show the user messages from an inbox they
    just disconnected, with no way to refresh or remove them.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    keep = await _connect(pool, user_id, "microsoft", "ms-keep", primary=True)
    drop = await _connect(pool, user_id, "google", "g-drop")

    emails = EmailsRepository(pool)
    for account_id, message_id in ((keep, "stays"), (drop, "goes")):
        await emails.upsert(
            user_id=user_id,
            account_id=account_id,
            provider_message_id=message_id,
            subject="Hi",
            from_address="a@example.com",
            from_name="A",
            to_recipients=[],
            received_at=None,
            body_text=None,
        )

    async with as_user(user_id, email) as client:
        await client.delete(f"/api/me/accounts/{drop}")

    left = await pool.fetch(
        "select provider_message_id from public.emails where user_id = $1", user_id
    )
    assert [r["provider_message_id"] for r in left] == ["stays"]


@pytest.mark.asyncio
async def test_removing_the_primary_mailbox_promotes_another(pool, test_auth_user, no_accounts):
    """Calendar and chat follow the primary account. Leaving a user with
    mailboxes but no primary silently disables both, with nothing in the UI to
    explain why.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    primary = await _connect(pool, user_id, "microsoft", "ms-primary", primary=True)
    other = await _connect(pool, user_id, "microsoft", "ms-other")

    async with as_user(user_id, email) as client:
        response = await client.delete(f"/api/me/accounts/{primary}")

    assert response.status_code == 204
    repo = ConnectedAccountsRepository(pool)
    assert (await repo.get_primary(user_id))["id"] == other


@pytest.mark.asyncio
async def test_removing_the_last_mailbox_leaves_no_primary_and_does_not_fail(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    only = await _connect(pool, user_id, "microsoft", "ms-only", primary=True)

    async with as_user(user_id, email) as client:
        response = await client.delete(f"/api/me/accounts/{only}")

    assert response.status_code == 204
    repo = ConnectedAccountsRepository(pool)
    assert await repo.list_for_user(user_id) == []
    assert await repo.get_primary(user_id) is None


@pytest.mark.asyncio
async def test_a_mailbox_belonging_to_someone_else_cannot_be_disconnected(pool, test_auth_user, test_auth_user_2, no_accounts):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)
    await pool.execute("delete from public.connected_accounts where user_id = $1", other_id)
    theirs = await _connect(pool, other_id, "microsoft", "ms-theirs", primary=True)

    async with as_user(user_id, email) as client:
        response = await client.delete(f"/api/me/accounts/{theirs}")

    assert response.status_code == 404
    assert await ConnectedAccountsRepository(pool).get(other_id, theirs) is not None


@pytest.mark.asyncio
async def test_disconnecting_an_unknown_mailbox_is_a_404(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with as_user(user_id, email) as client:
        response = await client.delete(f"/api/me/accounts/{uuid.uuid4()}")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Choosing the primary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promoting_a_mailbox_demotes_the_old_primary(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    was = await _connect(pool, user_id, "microsoft", "ms-was", primary=True)
    now = await _connect(pool, user_id, "microsoft", "ms-now")

    async with as_user(user_id, email) as client:
        response = await client.post(
            f"/api/me/accounts/{now}/primary"
        )

    assert response.status_code == 204
    repo = ConnectedAccountsRepository(pool)
    assert (await repo.get_primary(user_id))["id"] == now
    assert (await repo.get(user_id, was))["is_primary"] is False


@pytest.mark.asyncio
async def test_promoting_someone_elses_mailbox_is_a_404(pool, test_auth_user, test_auth_user_2, no_accounts):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)
    await pool.execute("delete from public.connected_accounts where user_id = $1", other_id)
    mine = await _connect(pool, user_id, "microsoft", "ms-mine", primary=True)
    theirs = await _connect(pool, other_id, "microsoft", "ms-theirs", primary=True)

    async with as_user(user_id, email) as client:
        response = await client.post(
            f"/api/me/accounts/{theirs}/primary"
        )

    assert response.status_code == 404
    repo = ConnectedAccountsRepository(pool)
    # Neither user's primary moved.
    assert (await repo.get_primary(user_id))["id"] == mine
    assert (await repo.get_primary(other_id))["id"] == theirs


@pytest.mark.asyncio
async def test_promoting_a_google_mailbox_is_refused(pool, test_auth_user, no_accounts):
    """Calendar and Teams chat are Graph-only. A Google primary would leave both
    permanently unsynced, which is worse than refusing the change.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    ms = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)
    google = await _connect(pool, user_id, "google", "g-1")

    async with as_user(user_id, email) as client:
        response = await client.post(
            f"/api/me/accounts/{google}/primary"
        )

    assert response.status_code == 409
    assert (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"] == ms


@pytest.mark.asyncio
async def test_promoting_the_mailbox_that_is_already_primary_is_a_noop(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    primary = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)

    async with as_user(user_id, email) as client:
        response = await client.post(
            f"/api/me/accounts/{primary}/primary"
        )

    assert response.status_code == 204
    assert (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"] == primary


@pytest.mark.asyncio
async def test_a_google_only_user_can_still_choose_their_primary(
    pool, test_auth_user, no_accounts
):
    """The refusal above exists to protect calendar and chat. With no Microsoft
    mailbox connected there is nothing to protect, and blocking a harmless
    choice would just look broken.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _connect(pool, user_id, "google", "g-1", primary=True)
    second = await _connect(pool, user_id, "google", "g-2")

    async with as_user(user_id, email) as client:
        response = await client.post(f"/api/me/accounts/{second}/primary")

    assert response.status_code == 204
    assert (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"] == second


@pytest.mark.asyncio
async def test_removing_the_primary_prefers_an_outlook_mailbox_over_a_gmail_one(
    pool, test_auth_user, no_accounts
):
    """Promotion is not just "the next one". Handing primary to Gmail while an
    Outlook mailbox is connected leaves calendar and chat unsynced.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    primary = await _connect(pool, user_id, "microsoft", "ms-primary", primary=True)
    # Connected before the Outlook one, so "oldest remaining" would pick it.
    gmail = await _connect(pool, user_id, "google", "g-older")
    outlook = await _connect(pool, user_id, "microsoft", "ms-newer")

    async with as_user(user_id, email) as client:
        await client.delete(f"/api/me/accounts/{primary}")

    assert (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"] == outlook
