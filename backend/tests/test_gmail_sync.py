"""Gmail sync against the database, and provider dispatch.

The client half (HTTP, parsing) is covered in test_gmail_client.py. What is
pinned here is everything that can go wrong once a second provider exists: the
wrong refresh endpoint, the wrong sync function, Teams chat attempted on a Gmail
mailbox, and the cursor-ordering bug that silently loses mail.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security import encrypt_token
from app.repositories.connected_accounts import ConnectedAccountsRepository
from app.repositories.emails import EmailsRepository
from app.repositories.profiles import ProfilesRepository
from app.repositories.sync_state import SyncStateRepository
from app.services import graph_sync
from app.services.account_tokens import refresh_and_persist
from app.services.gmail_client import GoogleRefreshError, HistoryExpired


async def _connect_google(pool, user_id, *, expires_in_hours=1, primary=False):
    repo = ConnectedAccountsRepository(pool)
    return await repo.upsert(
        user_id=user_id,
        provider="google",
        provider_account_id="g-1",
        email_address="personal@gmail.com",
        encrypted_access_token=encrypt_token("g-access"),
        encrypted_refresh_token=encrypt_token("g-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        is_primary=primary,
    )


def _message(message_id: str, *, subject="Hello", body="Can you review the deck by Thursday?"):
    import base64

    return {
        "id": message_id,
        "internalDate": "1757000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": f"Dana <dana@example.com>"},
                {"name": "To", "value": "me@example.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


# --------------------------------------------------------------------------
# Token refresh dispatch
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_google_account_refreshes_against_google_not_microsoft(pool, test_auth_user):
    """The one-provider version always called msal. Sending a Google refresh
    token to Microsoft fails, and the account would be marked needs_reauth even
    though its credentials are perfectly good.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    account = await ConnectedAccountsRepository(pool).get(user_id, account_id)

    with patch("app.services.account_tokens.graph_refresh_access_token") as ms_refresh, patch(
        "app.services.account_tokens.google_refresh_access_token",
        return_value={
            "access_token": "fresh-google",
            "refresh_token": "g-refresh",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    ) as google_refresh:
        token = await refresh_and_persist(pool, account)

    assert token == "fresh-google"
    google_refresh.assert_called_once()
    ms_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_a_microsoft_account_still_refreshes_against_microsoft(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account = await ConnectedAccountsRepository(pool).get_primary(user_id)

    with patch(
        "app.services.account_tokens.graph_refresh_access_token",
        return_value={
            "access_token": "fresh-ms",
            "refresh_token": "r",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    ) as ms_refresh, patch(
        "app.services.account_tokens.google_refresh_access_token"
    ) as google_refresh:
        token = await refresh_and_persist(pool, account)

    assert token == "fresh-ms"
    ms_refresh.assert_called_once()
    google_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_a_revoked_google_consent_marks_only_that_account(pool, test_auth_user):
    """GoogleRefreshError is a different type from GraphRefreshError. Catching
    only the latter turns a revoked Gmail consent into an unhandled exception
    that aborts the whole sync run.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    repo = ConnectedAccountsRepository(pool)
    account = await repo.get(user_id, account_id)

    with patch(
        "app.services.account_tokens.google_refresh_access_token",
        side_effect=GoogleRefreshError("Token has been expired or revoked."),
    ):
        assert await refresh_and_persist(pool, account) is None

    assert (await repo.get(user_id, account_id))["connection_status"] == "needs_reauth"
    # The Microsoft mailbox conftest connected is untouched.
    assert (await repo.get_primary(user_id))["connection_status"] == "connected"


# --------------------------------------------------------------------------
# Sync dispatch
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_google_account_syncs_mail_through_gmail_not_graph(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    with patch("app.services.graph_sync.sync_gmail", AsyncMock()) as gmail, patch(
        "app.services.graph_sync.sync_mail", AsyncMock()
    ) as graph_mail, patch("app.services.graph_sync.extract_user", AsyncMock()):
        await graph_sync.sync_account(pool, user_id, account_id)

    gmail.assert_awaited_once()
    graph_mail.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_chat_is_never_attempted_on_a_google_mailbox(pool, test_auth_user):
    """Chat runs for the primary account. If the primary is a Gmail mailbox,
    calling Graph with a Google token 401s on every single run.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    # Make the Google account the primary one.
    account_id = await _connect_google(pool, user_id, primary=True)

    with patch("app.services.graph_sync.sync_gmail", AsyncMock()), patch(
        "app.services.graph_sync.sync_chat", AsyncMock()
    ) as chat, patch("app.services.graph_sync.extract_user", AsyncMock()):
        await graph_sync.sync_account(pool, user_id, account_id)

    chat.assert_not_awaited()


# --------------------------------------------------------------------------
# sync_gmail: backfill
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_sync_backfills_and_stores_a_history_cursor(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    with patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="500")
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since",
        AsyncMock(return_value={"message_ids": ["m1", "m2"], "next_page_token": None}),
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message",
        AsyncMock(side_effect=lambda t, mid, **kw: _message(mid)),
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 2
    state = await SyncStateRepository(pool).get(account_id, "mail")
    assert state["delta_link"] == "500"
    assert state["status"] == "ok"


@pytest.mark.asyncio
async def test_the_cursor_is_read_before_messages_are_listed(pool, test_auth_user):
    """Ordering bug with silent data loss: read the historyId after the backfill
    and any mail that arrives during it falls in the gap and is never synced.
    Reading first means such mail is merely re-seen, which the upsert absorbs.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    order: list[str] = []

    async def history_id(_token):
        order.append("cursor")
        return "500"

    async def list_ids(_token, _since, page_token=None):
        order.append("list")
        return {"message_ids": [], "next_page_token": None}

    with patch("app.services.graph_sync.gmail_client.current_history_id", history_id), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since", list_ids
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert order == ["cursor", "list"]


@pytest.mark.asyncio
async def test_backfill_follows_every_page(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    pages = [
        {"message_ids": ["m1"], "next_page_token": "p2"},
        {"message_ids": ["m2"], "next_page_token": None},
    ]

    async def list_ids(_token, _since, page_token=None):
        return pages[1] if page_token == "p2" else pages[0]

    with patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="500")
    ), patch("app.services.graph_sync.gmail_client.fetch_message_ids_since", list_ids), patch(
        "app.services.graph_sync.gmail_client.fetch_message",
        AsyncMock(side_effect=lambda t, mid, **kw: _message(mid)),
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 2


@pytest.mark.asyncio
async def test_a_message_that_vanishes_mid_backfill_does_not_abort_the_rest(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)

    async def fetch(_token, message_id, **_kw):
        return None if message_id == "gone" else _message(message_id)

    with patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="500")
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since",
        AsyncMock(return_value={"message_ids": ["gone", "m2"], "next_page_token": None}),
    ), patch("app.services.graph_sync.gmail_client.fetch_message", fetch):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 1


# --------------------------------------------------------------------------
# sync_gmail: incremental
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stored_cursor_drives_an_incremental_sync(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "mail", "500", "ok")

    with patch(
        "app.services.graph_sync.gmail_client.fetch_history_page",
        AsyncMock(
            return_value={
                "added_ids": ["m1"],
                "removed_ids": [],
                "next_page_token": None,
                "history_id": "501",
            }
        ),
    ) as history, patch(
        "app.services.graph_sync.gmail_client.fetch_message",
        AsyncMock(side_effect=lambda t, mid, **kw: _message(mid)),
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since", AsyncMock()
    ) as backfill:
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    history.assert_awaited_once_with("tok", "500", page_token=None)
    backfill.assert_not_awaited()
    assert await EmailsRepository(pool).count(user_id) == 1
    assert (await SyncStateRepository(pool).get(account_id, "mail"))["delta_link"] == "501"


@pytest.mark.asyncio
async def test_removed_messages_are_deleted_from_the_right_account(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "mail", "500", "ok")
    await EmailsRepository(pool).upsert(
        user_id=user_id,
        account_id=account_id,
        provider_message_id="m-old",
        subject="Old",
        from_address="a@example.com",
        from_name="A",
        to_recipients=[],
        received_at=None,
        body_text=None,
    )

    with patch(
        "app.services.graph_sync.gmail_client.fetch_history_page",
        AsyncMock(
            return_value={
                "added_ids": [],
                "removed_ids": ["m-old"],
                "next_page_token": None,
                "history_id": "501",
            }
        ),
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 0


@pytest.mark.asyncio
async def test_an_expired_cursor_falls_back_to_a_backfill(pool, test_auth_user):
    """Gmail drops history after about a week. A mailbox left unsynced over a
    holiday must recover by itself, not stay broken until someone reconnects it.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "mail", "1", "ok")

    with patch(
        "app.services.graph_sync.gmail_client.fetch_history_page",
        AsyncMock(side_effect=HistoryExpired("too old")),
    ), patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="900")
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since",
        AsyncMock(return_value={"message_ids": ["m1"], "next_page_token": None}),
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message",
        AsyncMock(side_effect=lambda t, mid, **kw: _message(mid)),
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 1
    assert (await SyncStateRepository(pool).get(account_id, "mail"))["delta_link"] == "900"


@pytest.mark.asyncio
async def test_incremental_sync_follows_every_history_page(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "mail", "500", "ok")

    async def history(_token, _cursor, page_token=None):
        if page_token is None:
            return {
                "added_ids": ["m1"],
                "removed_ids": [],
                "next_page_token": "p2",
                "history_id": "501",
            }
        return {
            "added_ids": ["m2"],
            "removed_ids": [],
            "next_page_token": None,
            "history_id": "502",
        }

    with patch("app.services.graph_sync.gmail_client.fetch_history_page", history), patch(
        "app.services.graph_sync.gmail_client.fetch_message",
        AsyncMock(side_effect=lambda t, mid, **kw: _message(mid)),
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 2
    # The last page's cursor, not the first.
    assert (await SyncStateRepository(pool).get(account_id, "mail"))["delta_link"] == "502"


@pytest.mark.asyncio
async def test_an_unchanged_mailbox_keeps_its_cursor_and_syncs_nothing(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await _connect_google(pool, user_id)
    await SyncStateRepository(pool).upsert(account_id, "mail", "500", "ok")

    with patch(
        "app.services.graph_sync.gmail_client.fetch_history_page",
        AsyncMock(
            return_value={
                "added_ids": [],
                "removed_ids": [],
                "next_page_token": None,
                "history_id": "500",
            }
        ),
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    assert await EmailsRepository(pool).count(user_id) == 0
    assert (await SyncStateRepository(pool).get(account_id, "mail"))["delta_link"] == "500"


# --------------------------------------------------------------------------
# Cross-account isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_messages_are_attached_to_the_gmail_account(pool, test_auth_user):
    """Two mailboxes, two providers, same user. A message synced from Gmail must
    not be filed under the Microsoft account - every per-mailbox count and the
    delete path depend on this.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    ms_account = (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"]
    google_account = await _connect_google(pool, user_id)

    with patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="500")
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since",
        AsyncMock(return_value={"message_ids": ["m1"], "next_page_token": None}),
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message",
        AsyncMock(side_effect=lambda t, mid, **kw: _message(mid)),
    ):
        await graph_sync.sync_gmail(pool, user_id, google_account, "tok")

    owner = await pool.fetchval(
        "select account_id from public.emails where user_id = $1 and provider_message_id = $2",
        user_id,
        "m1",
    )
    assert owner == google_account
    assert owner != ms_account


@pytest.mark.asyncio
async def test_the_same_message_id_in_two_mailboxes_does_not_collide(pool, test_auth_user):
    """Gmail and Graph ids are independent namespaces, so a collision is not
    hypothetical. Before account_id this violated a unique constraint.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    ms_account = (await ConnectedAccountsRepository(pool).get_primary(user_id))["id"]
    google_account = await _connect_google(pool, user_id)

    emails = EmailsRepository(pool)
    for account_id in (ms_account, google_account):
        await emails.upsert(
            user_id=user_id,
            account_id=account_id,
            provider_message_id="same-id",
            subject="Hello",
            from_address="a@example.com",
            from_name="A",
            to_recipients=[],
            received_at=None,
            body_text=None,
        )

    assert await emails.count(user_id) == 2
