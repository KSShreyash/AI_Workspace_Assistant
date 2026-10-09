"""Synced data belongs to a mailbox, not just to a person.

Without that attribution the Emails page cannot say which inbox a message came
from, two mailboxes receiving the same message id collide, and each mailbox
cannot keep its own sync cursor.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import encrypt_token
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.emails import EmailsRepository
from app.repositories.profiles import ProfilesRepository
from app.repositories.sync_state import SyncStateRepository


async def _two_accounts(pool, user_id, email):
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ConnectedAccountsRepository(pool)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    work = await repo.upsert(
        user_id=user_id, provider="microsoft", provider_account_id="ms-1",
        email_address="work@example.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )
    personal = await repo.upsert(
        user_id=user_id, provider="google", provider_account_id="g-1",
        email_address="personal@gmail.com",
        encrypted_access_token=encrypt_token("a"),
        encrypted_refresh_token=encrypt_token("r"),
        access_token_expires_at=expires, scopes=[],
    )
    return work, personal


@pytest.mark.asyncio
async def test_an_email_records_which_mailbox_it_arrived_in(pool, test_auth_user):
    user_id, email = test_auth_user
    work, _ = await _two_accounts(pool, user_id, email)

    await EmailsRepository(pool).upsert(
        user_id=user_id, account_id=work, provider_message_id="m-1",
        subject="Hello", from_address="a@example.com", from_name=None,
        to_recipients=[], received_at=datetime.now(timezone.utc), body_text="body",
    )

    stored = await pool.fetchrow(
        "select account_id from public.emails where provider_message_id = 'm-1'"
    )
    assert stored["account_id"] == work


@pytest.mark.asyncio
async def test_the_same_message_id_in_two_mailboxes_does_not_collide(pool, test_auth_user):
    """Gmail and Graph mint ids independently; so do two accounts on one provider.

    Keying on (user_id, message_id) meant the second mailbox's message silently
    overwrote the first's.
    """
    user_id, email = test_auth_user
    work, personal = await _two_accounts(pool, user_id, email)
    emails = EmailsRepository(pool)

    for account_id, subject in ((work, "From work"), (personal, "From personal")):
        await emails.upsert(
            user_id=user_id, account_id=account_id, provider_message_id="shared-id",
            subject=subject, from_address="a@example.com", from_name=None,
            to_recipients=[], received_at=datetime.now(timezone.utc), body_text="body",
        )

    rows = await pool.fetch(
        "select subject from public.emails where provider_message_id = 'shared-id' "
        "order by subject"
    )
    assert [r["subject"] for r in rows] == ["From personal", "From work"]


@pytest.mark.asyncio
async def test_re_syncing_the_same_mailbox_still_updates_in_place(pool, test_auth_user):
    user_id, email = test_auth_user
    work, _ = await _two_accounts(pool, user_id, email)
    emails = EmailsRepository(pool)

    for subject in ("Original", "Edited"):
        await emails.upsert(
            user_id=user_id, account_id=work, provider_message_id="m-1",
            subject=subject, from_address="a@example.com", from_name=None,
            to_recipients=[], received_at=datetime.now(timezone.utc), body_text="body",
        )

    rows = await pool.fetch("select subject from public.emails where provider_message_id = 'm-1'")
    assert [r["subject"] for r in rows] == ["Edited"]


@pytest.mark.asyncio
async def test_each_mailbox_keeps_its_own_sync_cursor(pool, test_auth_user):
    """A shared cursor would make one mailbox resume from the other's position."""
    user_id, email = test_auth_user
    work, personal = await _two_accounts(pool, user_id, email)
    state = SyncStateRepository(pool)

    await state.upsert(work, "mail", "cursor-work", "ok")
    await state.upsert(personal, "mail", "cursor-personal", "ok")

    assert (await state.get(work, "mail"))["delta_link"] == "cursor-work"
    assert (await state.get(personal, "mail"))["delta_link"] == "cursor-personal"


@pytest.mark.asyncio
async def test_disconnecting_a_mailbox_removes_its_synced_mail(pool, test_auth_user):
    """Its contents must not linger as orphans attributed to nothing."""
    user_id, email = test_auth_user
    work, personal = await _two_accounts(pool, user_id, email)
    emails = EmailsRepository(pool)

    for account_id, mid in ((work, "w-1"), (personal, "p-1")):
        await emails.upsert(
            user_id=user_id, account_id=account_id, provider_message_id=mid,
            subject="s", from_address="a@example.com", from_name=None,
            to_recipients=[], received_at=datetime.now(timezone.utc), body_text="b",
        )

    await ConnectedAccountsRepository(pool).delete(user_id, personal)

    remaining = await pool.fetch(
        "select provider_message_id from public.emails where user_id = $1", user_id
    )
    assert [r["provider_message_id"] for r in remaining] == ["w-1"]
