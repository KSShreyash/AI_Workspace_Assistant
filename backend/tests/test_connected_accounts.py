"""Several mailboxes per user, across providers.

Replaces ms_graph_tokens, whose `user_id uuid primary key` allowed exactly one
Microsoft account per person. Tokens stay encrypted at rest exactly as before -
this changes how many rows there can be, not how they are protected.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import decrypt_token, encrypt_token
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.profiles import ProfilesRepository


async def _seed(pool, user_id, email):
    await ProfilesRepository(pool).upsert(user_id, email)
    # conftest connects one mailbox for every test user, which is right for
    # tests that need synced data to hang off something. These tests are about
    # the accounts table itself, so they start from nothing.
    await pool.execute("delete from public.connected_accounts where user_id = $1", user_id)
    return ConnectedAccountsRepository(pool)


def _expiry(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


@pytest.mark.asyncio
async def test_one_user_can_connect_several_mailboxes(pool, test_auth_user):
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="work@example.com",
        encrypted_access_token=encrypt_token("a"), encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=["Mail.Read"], is_primary=True,
    )
    await repo.upsert(
        user_id=user_id, provider="google", provider_account_id="g-1",
        email_address="personal@gmail.com",
        encrypted_access_token=encrypt_token("b"), encrypted_refresh_token=encrypt_token("r2"),
        access_token_expires_at=_expiry(), scopes=["gmail.readonly"],
    )

    accounts = await repo.list_for_user(user_id)
    assert {a["email_address"] for a in accounts} == {"work@example.com", "personal@gmail.com"}
    assert {a["provider"] for a in accounts} == {"microsoft", "google"}


@pytest.mark.asyncio
async def test_reconnecting_the_same_mailbox_updates_rather_than_duplicates(pool, test_auth_user):
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    for token in ("first", "second"):
        await repo.upsert(
            user_id=user_id, provider="google", provider_account_id="g-1",
            email_address="personal@gmail.com",
            encrypted_access_token=encrypt_token(token),
            encrypted_refresh_token=encrypt_token("r"),
            access_token_expires_at=_expiry(), scopes=["gmail.readonly"],
        )

    accounts = await repo.list_for_user(user_id)
    assert len(accounts) == 1
    assert decrypt_token(accounts[0]["encrypted_access_token"]) == "second"


@pytest.mark.asyncio
async def test_the_same_mailbox_can_belong_to_two_different_users(pool, test_auth_user, test_auth_user_2):
    """A shared inbox connected by two colleagues must not collide."""
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    repo = await _seed(pool, user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)
    await pool.execute("delete from public.connected_accounts where user_id = $1", other_id)

    for uid in (user_id, other_id):
        await repo.upsert(
            user_id=uid, provider="google", provider_account_id="shared-1",
            email_address="team@example.com",
            encrypted_access_token=encrypt_token("a"),
            encrypted_refresh_token=encrypt_token("r"),
            access_token_expires_at=_expiry(), scopes=[],
        )

    assert len(await repo.list_for_user(user_id)) == 1
    assert len(await repo.list_for_user(other_id)) == 1


@pytest.mark.asyncio
async def test_only_one_account_can_be_primary(pool, test_auth_user):
    """Calendar and chat follow the primary account, so there must be exactly one."""
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    for n in ("ms-1", "ms-2"):
        await repo.upsert(
            user_id=user_id, provider="microsoft", provider_account_id=n,
            email_address=f"{n}@example.com",
            encrypted_access_token=encrypt_token("a"),
            encrypted_refresh_token=encrypt_token("r"),
            access_token_expires_at=_expiry(), scopes=[], is_primary=True,
        )

    primaries = [a for a in await repo.list_for_user(user_id) if a["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["provider_account_id"] == "ms-2"


@pytest.mark.asyncio
async def test_the_first_account_becomes_primary_automatically(pool, test_auth_user):
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="only@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )

    accounts = await repo.list_for_user(user_id)
    assert accounts[0]["is_primary"] is True


@pytest.mark.asyncio
async def test_accounts_never_leak_between_users(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    repo = await _seed(pool, user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)
    await pool.execute("delete from public.connected_accounts where user_id = $1", other_id)

    await repo.upsert(
        user_id=other_id, provider="google", provider_account_id="g-other",
        email_address="theirs@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )

    assert await repo.list_for_user(user_id) == []


@pytest.mark.asyncio
async def test_disconnecting_removes_only_that_account(pool, test_auth_user):
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    ids = {}
    for provider, pid in (("microsoft", "ms-1"), ("google", "g-1")):
        ids[provider] = await repo.upsert(
            user_id=user_id, provider=provider, provider_account_id=pid,
            email_address=f"{pid}@example.com",
            encrypted_access_token=encrypt_token("a"),
            encrypted_refresh_token=encrypt_token("r"),
            access_token_expires_at=_expiry(), scopes=[],
        )

    await repo.delete(user_id, ids["google"])

    remaining = await repo.list_for_user(user_id)
    assert len(remaining) == 1
    assert remaining[0]["provider"] == "microsoft"


@pytest.mark.asyncio
async def test_connection_status_is_per_account(pool, test_auth_user):
    """One expired mailbox must not mark the others as needing reauth."""
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    good = await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="good@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )
    stale = await repo.upsert(
        user_id=user_id, provider="google", provider_account_id="g-1",
        email_address="stale@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )

    await repo.set_status(user_id, stale, "needs_reauth")

    by_id = {a["id"]: a for a in await repo.list_for_user(user_id)}
    assert by_id[good]["connection_status"] == "connected"
    assert by_id[stale]["connection_status"] == "needs_reauth"


@pytest.mark.asyncio
async def test_lists_every_syncable_account_across_users(pool, test_auth_user):
    """What the bulk cron iterates, replacing profiles.list_connected."""
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)

    await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="a@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )
    disconnected = await repo.upsert(
        user_id=user_id, provider="google", provider_account_id="g-1",
        email_address="b@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )
    await repo.set_status(user_id, disconnected, "disconnected")

    syncable = await repo.list_syncable()
    mine = [a for a in syncable if a["user_id"] == user_id]
    assert len(mine) == 1
    assert mine[0]["provider"] == "microsoft"


@pytest.mark.asyncio
async def test_deleting_the_user_removes_their_accounts(pool, test_auth_user):
    """Tokens must not outlive the account they belong to."""
    user_id, email = test_auth_user
    repo = await _seed(pool, user_id, email)
    await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="a@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=_expiry(), scopes=[],
    )

    await pool.execute("delete from auth.users where id = $1", user_id)

    left = await pool.fetchval(
        "select count(*) from public.connected_accounts where user_id = $1", user_id
    )
    assert left == 0
