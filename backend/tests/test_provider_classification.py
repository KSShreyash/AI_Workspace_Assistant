"""The provider's own bulk verdict, carried from sync through to extraction.

extraction_filter documents a tier-2 rule that skips mail the provider already
classified as non-focused. The rule was unreachable: nothing stored the
classification and nothing passed it, so every promotional mail still cost a
full LLM call. On a free tier where tokens per day is the binding limit, that is
the difference between a real inbox fitting in the budget and not.

Both providers supply this for free with the data already being fetched -
Microsoft as inferenceClassification, Gmail as its CATEGORY_* labels.
"""

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.profiles import ProfilesRepository
from app.services import graph_sync
from app.services.ai_extraction import extract_user
from tests.helpers import primary_account_id


async def _insert_email(pool, user_id, *, classification, message_id="msg-1"):
    return await pool.fetchval(
        "insert into public.emails "
        "(user_id, account_id, provider_message_id, from_address, subject, body_text, classification) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), "
        "$2, $3, $4, $5, $6) returning id",
        user_id,
        message_id,
        "dana@example.com",
        "Hello",
        "Could you review the draft and send feedback before Thursday?",
        classification,
    )


# --------------------------------------------------------------------------
# Extraction honours it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mail_the_provider_called_bulk_never_reaches_the_model(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    row_id = await _insert_email(pool, user_id, classification="other")

    with patch("app.services.ai_extraction.openai_client.extract") as extract:
        await extract_user(pool, user_id, limit=10)

    extract.assert_not_called()
    row = await pool.fetchrow(
        "select extracted_at, extraction_error from public.emails where id = $1", row_id
    )
    # Marked done so it is not retried every run, with the rule that fired named.
    assert row["extracted_at"] is not None
    assert "non-focused" in row["extraction_error"]


@pytest.mark.asyncio
async def test_focused_mail_is_still_extracted(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _insert_email(pool, user_id, classification="focused")

    with patch(
        "app.services.ai_extraction.openai_client.extract",
        return_value={"people": [], "action_items": []},
    ) as extract:
        await extract_user(pool, user_id, limit=10)

    extract.assert_called_once()


@pytest.mark.asyncio
async def test_mail_synced_before_the_column_existed_is_still_extracted(pool, test_auth_user):
    """The backfill leaves classification null on every pre-existing row.
    Treating unknown as bulk would silently drop all of it.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await _insert_email(pool, user_id, classification=None)

    with patch(
        "app.services.ai_extraction.openai_client.extract",
        return_value={"people": [], "action_items": []},
    ) as extract:
        await extract_user(pool, user_id, limit=10)

    extract.assert_called_once()


# --------------------------------------------------------------------------
# Microsoft sync supplies it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_stores_its_inference_classification(pool, test_auth_user):
    """Graph returns inferenceClassification on messages by default, so this
    costs no extra request.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await primary_account_id(pool, user_id)
    page = {
        "items": [
            {
                "id": "msg-bulk",
                "subject": "50% off",
                "inferenceClassification": "other",
                "sender": {"emailAddress": {"address": "deals@shop.example", "name": "Shop"}},
                "toRecipients": [],
                "receivedDateTime": "2026-07-01T12:00:00Z",
                "body": {"content": "Sale ends soon"},
            }
        ],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/delta?$deltatoken=abc",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page):
        await graph_sync.sync_mail(pool, user_id, account_id, "tok")

    stored = await pool.fetchval(
        "select classification from public.emails where provider_message_id = $1", "msg-bulk"
    )
    assert stored == "other"


@pytest.mark.asyncio
async def test_a_graph_message_without_the_field_stores_null_not_a_guess(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await primary_account_id(pool, user_id)
    page = {
        "items": [
            {
                "id": "msg-plain",
                "subject": "Hi",
                "sender": {"emailAddress": {"address": "a@example.com", "name": "A"}},
                "toRecipients": [],
                "receivedDateTime": "2026-07-01T12:00:00Z",
                "body": {"content": "Hello there"},
            }
        ],
        "next_link": None,
        "delta_link": "https://graph.microsoft.com/v1.0/delta?$deltatoken=abc",
    }
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page):
        await graph_sync.sync_mail(pool, user_id, account_id, "tok")

    stored = await pool.fetchval(
        "select classification from public.emails where provider_message_id = $1", "msg-plain"
    )
    assert stored is None


@pytest.mark.asyncio
async def test_a_resync_that_omits_the_field_does_not_erase_a_stored_verdict(
    pool, test_auth_user
):
    """Overwriting with null would quietly re-admit mail already judged bulk,
    and it would cost a full LLM call on the next run.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await primary_account_id(pool, user_id)

    def page(classification):
        item = {
            "id": "msg-1",
            "subject": "Hi",
            "sender": {"emailAddress": {"address": "a@example.com", "name": "A"}},
            "toRecipients": [],
            "receivedDateTime": "2026-07-01T12:00:00Z",
            "body": {"content": "Hello there"},
        }
        if classification is not None:
            item["inferenceClassification"] = classification
        return {"items": [item], "next_link": None, "delta_link": "d"}

    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page("other")):
        await graph_sync.sync_mail(pool, user_id, account_id, "tok")
    with patch("app.services.graph_sync.graph_client.fetch_delta_page", return_value=page(None)):
        await graph_sync.sync_mail(pool, user_id, account_id, "tok")

    stored = await pool.fetchval(
        "select classification from public.emails where provider_message_id = $1", "msg-1"
    )
    assert stored == "other"


# --------------------------------------------------------------------------
# Gmail sync supplies it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_stores_its_category_verdict(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await primary_account_id(pool, user_id)

    raw = {
        "id": "g-promo",
        "internalDate": "1757000000000",
        "labelIds": ["INBOX", "CATEGORY_PROMOTIONS"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "50% off"},
                {"name": "From", "value": "Shop <deals@shop.example>"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Sale ends soon").decode().rstrip("=")},
        },
    }

    with patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="500")
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since",
        AsyncMock(return_value={"message_ids": ["g-promo"], "next_page_token": None}),
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message", AsyncMock(return_value=raw)
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    stored = await pool.fetchval(
        "select classification from public.emails where provider_message_id = $1", "g-promo"
    )
    assert stored == "other"


@pytest.mark.asyncio
async def test_both_providers_write_the_same_vocabulary(pool, test_auth_user):
    """Microsoft says "other"; Gmail says CATEGORY_PROMOTIONS. If they landed in
    the column unmapped, the filter would need a provider branch inside it.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = await primary_account_id(pool, user_id)

    raw = {
        "id": "g-personal",
        "internalDate": "1757000000000",
        "labelIds": ["INBOX", "IMPORTANT"],
        "payload": {
            "headers": [{"name": "From", "value": "a@b.com"}],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Hello there").decode().rstrip("=")},
        },
    }

    with patch(
        "app.services.graph_sync.gmail_client.current_history_id", AsyncMock(return_value="500")
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message_ids_since",
        AsyncMock(return_value={"message_ids": ["g-personal"], "next_page_token": None}),
    ), patch(
        "app.services.graph_sync.gmail_client.fetch_message", AsyncMock(return_value=raw)
    ):
        await graph_sync.sync_gmail(pool, user_id, account_id, "tok")

    stored = await pool.fetchval(
        "select classification from public.emails where provider_message_id = $1", "g-personal"
    )
    assert stored == "focused"
