"""The Emails page contract.

The only page in job 4 that needs to know mail comes from several mailboxes.
Everything else aggregates, but here the user has to be able to see which inbox
a message arrived in, and filter to one.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.core.security import encrypt_token
from app.main import app
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.emails import EmailsRepository
from app.repositories.profiles import ProfilesRepository
from contextlib import asynccontextmanager


@asynccontextmanager
async def as_user(user_id, email):
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
    return await ConnectedAccountsRepository(pool).upsert(
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


async def _store(pool, user_id, account_id, message_id, *, received=None, subject="Hi"):
    await EmailsRepository(pool).upsert(
        user_id=user_id,
        account_id=account_id,
        provider_message_id=message_id,
        subject=subject,
        from_address="dana@example.com",
        from_name="Dana",
        to_recipients=[{"address": "me@example.com", "name": "Me"}],
        received_at=received,
        body_text="Could you review the draft before Thursday?",
    )


@pytest.mark.asyncio
async def test_mail_is_listed_newest_first(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)

    base = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    await _store(pool, user_id, account_id, "old", received=base, subject="Older")
    await _store(
        pool, user_id, account_id, "new", received=base + timedelta(hours=2), subject="Newer"
    )

    async with as_user(user_id, email) as client:
        response = await client.get("/api/emails")

    assert response.status_code == 200
    assert [m["subject"] for m in response.json()["emails"]] == ["Newer", "Older"]


@pytest.mark.asyncio
async def test_each_message_says_which_mailbox_it_arrived_in(
    pool, test_auth_user, no_accounts
):
    """The reason this page waited for job 2. Two mailboxes in one list are
    indistinguishable without it, and "reply from the right address" is the
    first thing anyone needs to know.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    work = await _connect(pool, user_id, "microsoft", "ms-work", primary=True)
    personal = await _connect(pool, user_id, "google", "g-personal")

    await _store(pool, user_id, work, "w1", received=datetime(2026, 7, 1, 9, tzinfo=timezone.utc))
    await _store(
        pool, user_id, personal, "p1", received=datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    )

    async with as_user(user_id, email) as client:
        body = (await client.get("/api/emails")).json()

    by_id = {m["provider_message_id"]: m for m in body["emails"]}
    assert by_id["w1"]["mailbox"] == {
        "id": str(work),
        "email_address": "ms-work@example.com",
        "provider": "microsoft",
    }
    assert by_id["p1"]["mailbox"]["provider"] == "google"


@pytest.mark.asyncio
async def test_the_list_can_be_filtered_to_one_mailbox(pool, test_auth_user, no_accounts):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    work = await _connect(pool, user_id, "microsoft", "ms-work", primary=True)
    personal = await _connect(pool, user_id, "google", "g-personal")

    await _store(pool, user_id, work, "w1", received=datetime(2026, 7, 1, 9, tzinfo=timezone.utc))
    await _store(
        pool, user_id, personal, "p1", received=datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    )

    async with as_user(user_id, email) as client:
        body = (await client.get(f"/api/emails?account_id={personal}")).json()

    assert [m["provider_message_id"] for m in body["emails"]] == ["p1"]


@pytest.mark.asyncio
async def test_filtering_by_someone_elses_mailbox_returns_nothing(
    pool, test_auth_user, test_auth_user_2, no_accounts
):
    """The filter is a query parameter, so it is user-supplied. Scoping it by
    user_id as well means a guessed id cannot read another person's mail.
    """
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)
    await pool.execute("delete from public.connected_accounts where user_id = $1", other_id)

    mine = await _connect(pool, user_id, "microsoft", "ms-mine", primary=True)
    theirs = await _connect(pool, other_id, "microsoft", "ms-theirs", primary=True)
    await _store(pool, user_id, mine, "m1", received=datetime(2026, 7, 1, tzinfo=timezone.utc))
    await _store(pool, other_id, theirs, "t1", received=datetime(2026, 7, 1, tzinfo=timezone.utc))

    async with as_user(user_id, email) as client:
        body = (await client.get(f"/api/emails?account_id={theirs}")).json()

    assert body["emails"] == []


@pytest.mark.asyncio
async def test_another_users_mail_never_appears(
    pool, test_auth_user, test_auth_user_2, no_accounts
):
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)
    await pool.execute("delete from public.connected_accounts where user_id = $1", other_id)

    theirs = await _connect(pool, other_id, "microsoft", "ms-theirs", primary=True)
    await _store(pool, other_id, theirs, "t1", received=datetime(2026, 7, 1, tzinfo=timezone.utc))

    async with as_user(user_id, email) as client:
        body = (await client.get("/api/emails")).json()

    assert body["emails"] == []


@pytest.mark.asyncio
async def test_a_page_reports_whether_there_is_more(pool, test_auth_user, no_accounts):
    """A real inbox is thousands of messages. Without this the page either loads
    all of them or silently truncates with no way to tell which.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)

    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for n in range(3):
        await _store(pool, user_id, account_id, f"m{n}", received=base + timedelta(hours=n))

    async with as_user(user_id, email) as client:
        first = (await client.get("/api/emails?limit=2")).json()
        rest = (await client.get(f"/api/emails?limit=2&offset=2")).json()

    assert len(first["emails"]) == 2
    assert first["has_more"] is True
    assert len(rest["emails"]) == 1
    assert rest["has_more"] is False


@pytest.mark.asyncio
async def test_an_absurd_limit_is_capped_rather_than_honoured(
    pool, test_auth_user, no_accounts
):
    """limit is user-supplied; honouring limit=1000000 hands anyone a way to
    make the server read the whole table.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with as_user(user_id, email) as client:
        response = await client.get("/api/emails?limit=100000")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mail_with_no_received_time_still_appears(pool, test_auth_user, no_accounts):
    """received_at is nullable and a provider can omit it. Ordering that drops
    nulls would hide the message entirely rather than merely misplace it.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)

    await _store(pool, user_id, account_id, "undated", received=None, subject="Undated")
    await _store(
        pool,
        user_id,
        account_id,
        "dated",
        received=datetime(2026, 7, 1, tzinfo=timezone.utc),
        subject="Dated",
    )

    async with as_user(user_id, email) as client:
        body = (await client.get("/api/emails")).json()

    assert {m["subject"] for m in body["emails"]} == {"Undated", "Dated"}
    # Dated mail is what the user is looking for, so it leads.
    assert body["emails"][0]["subject"] == "Dated"


@pytest.mark.asyncio
async def test_a_message_carries_what_the_list_needs_to_render(
    pool, test_auth_user, no_accounts
):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)
    await _store(
        pool,
        user_id,
        account_id,
        "m1",
        received=datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        subject="Q3 numbers",
    )

    async with as_user(user_id, email) as client:
        message = (await client.get("/api/emails")).json()["emails"][0]

    assert message["subject"] == "Q3 numbers"
    assert message["from_address"] == "dana@example.com"
    assert message["from_name"] == "Dana"
    assert message["received_at"] == "2026-07-01T09:30:00+00:00"
    assert "review the draft" in message["preview"]
    # Whether the AI pipeline has looked at it yet, so the page can say so
    # rather than appearing to have lost the message.
    assert message["extracted"] is False


@pytest.mark.asyncio
async def test_the_preview_is_short_and_free_of_markup(pool, test_auth_user, no_accounts):
    """Bodies are often megabytes of HTML. Sending them whole would make this
    endpoint the slowest in the app for no visible benefit.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect(pool, user_id, "microsoft", "ms-1", primary=True)
    await EmailsRepository(pool).upsert(
        user_id=user_id,
        account_id=account_id,
        provider_message_id="m1",
        subject="Hi",
        from_address="a@example.com",
        from_name="A",
        to_recipients=[],
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        body_text="<p>Hello &amp; welcome</p>" + ("x" * 5000),
    )

    async with as_user(user_id, email) as client:
        message = (await client.get("/api/emails")).json()["emails"][0]

    assert message["preview"].startswith("Hello & welcome")
    assert "<p>" not in message["preview"]
    assert len(message["preview"]) <= 200


@pytest.mark.asyncio
async def test_an_empty_inbox_is_an_empty_list_not_an_error(
    pool, test_auth_user, no_accounts
):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with as_user(user_id, email) as client:
        response = await client.get("/api/emails")

    assert response.status_code == 200
    assert response.json() == {"emails": [], "has_more": False}


@pytest.mark.asyncio
async def test_a_malformed_account_filter_is_rejected_not_ignored(
    pool, test_auth_user, no_accounts
):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with as_user(user_id, email) as client:
        response = await client.get("/api/emails?account_id=not-a-uuid")

    assert response.status_code == 422
