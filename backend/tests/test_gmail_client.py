"""Gmail's incremental model, which is not Graph's.

Graph hands back an opaque deltaLink that encodes everything. Gmail hands back
an integer historyId and expects the caller to reassemble what changed, fetch
each message separately, and decode its body. Every one of those steps is a
place to get it wrong, so each is pinned here.

Pure HTTP and parsing - no database. The DB-facing half lives in
test_account_scoped_sync.py.
"""

import base64
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import httpx
import pytest

from app.services import gmail_client
from app.services.gmail_client import (
    GoogleRefreshError,
    HistoryExpired,
    normalize_message,
)


def _b64(text: str) -> str:
    """Gmail strips base64 padding. This helper mirrors that."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json=body, request=httpx.Request("GET", "https://gmail.googleapis.com/x")
    )


# --------------------------------------------------------------------------
# Token refresh
# --------------------------------------------------------------------------


def test_refresh_returns_the_new_access_token_and_an_absolute_expiry():
    with patch("app.services.gmail_client.httpx.post") as post:
        post.return_value = httpx.Response(
            200,
            json={"access_token": "fresh", "expires_in": 3599, "token_type": "Bearer"},
            request=httpx.Request("POST", gmail_client.GOOGLE_TOKEN_URL),
        )
        result = gmail_client.refresh_access_token("stored-refresh")

    assert result["access_token"] == "fresh"
    # expires_in is relative and useless once stored; callers compare against now().
    assert result["expires_at"] > datetime.now(timezone.utc)


def test_refresh_keeps_the_existing_refresh_token_when_google_omits_one():
    """Google returns refresh_token only on the first consent.

    Overwriting with None here would destroy the only credential that can renew
    this mailbox, and the account would die at the next expiry.
    """
    with patch("app.services.gmail_client.httpx.post") as post:
        post.return_value = httpx.Response(
            200,
            json={"access_token": "fresh", "expires_in": 3599},
            request=httpx.Request("POST", gmail_client.GOOGLE_TOKEN_URL),
        )
        result = gmail_client.refresh_access_token("stored-refresh")

    assert result["refresh_token"] == "stored-refresh"


def test_a_revoked_grant_raises_rather_than_returning_a_tokenless_dict():
    with patch("app.services.gmail_client.httpx.post") as post:
        post.return_value = httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
            request=httpx.Request("POST", gmail_client.GOOGLE_TOKEN_URL),
        )
        with pytest.raises(GoogleRefreshError, match="revoked"):
            gmail_client.refresh_access_token("dead-refresh")


# --------------------------------------------------------------------------
# Incremental sync: history.list
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_page_separates_added_from_removed_ids():
    body = {
        "history": [
            {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
            {"id": "102", "messagesDeleted": [{"message": {"id": "m2"}}]},
        ],
        "historyId": "102",
    }
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert page["added_ids"] == ["m1"]
    assert page["removed_ids"] == ["m2"]
    assert page["history_id"] == "102"
    assert page["next_page_token"] is None


@pytest.mark.asyncio
async def test_a_message_moved_to_trash_counts_as_removed():
    """Deleting mail in Gmail usually adds a TRASH label, not a messagesDeleted entry.

    Reading only messagesDeleted would leave trashed and spammed mail sitting in
    the inbox view forever.
    """
    body = {
        "history": [
            {"id": "101", "labelsAdded": [{"message": {"id": "m-trash"}, "labelIds": ["TRASH"]}]},
            {"id": "102", "labelsAdded": [{"message": {"id": "m-spam"}, "labelIds": ["SPAM"]}]},
            {"id": "103", "labelsAdded": [{"message": {"id": "m-read"}, "labelIds": ["UNREAD"]}]},
        ],
        "historyId": "103",
    }
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert set(page["removed_ids"]) == {"m-trash", "m-spam"}
    # Being marked unread is not a removal.
    assert "m-read" not in page["removed_ids"]


@pytest.mark.asyncio
async def test_archiving_counts_as_removed():
    """Archiving only strips the INBOX label. Gmail reports no deletion for it,
    so archived mail would otherwise stay in our inbox view permanently.
    """
    body = {
        "history": [
            {
                "id": "101",
                "labelsRemoved": [{"message": {"id": "m-archived"}, "labelIds": ["INBOX"]}],
            },
            {
                "id": "102",
                "labelsRemoved": [{"message": {"id": "m-unstarred"}, "labelIds": ["STARRED"]}],
            },
        ],
        "historyId": "102",
    }
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert page["removed_ids"] == ["m-archived"]


@pytest.mark.asyncio
async def test_restoring_a_message_to_the_inbox_counts_as_added():
    """Untrashing emits labelsAdded INBOX, not messagesAdded. Ignoring it would
    leave mail the user deliberately recovered missing from the app.
    """
    body = {
        "history": [
            {"id": "101", "labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["INBOX"]}]},
        ],
        "historyId": "101",
    }
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert page["added_ids"] == ["m1"]
    assert page["removed_ids"] == []


@pytest.mark.asyncio
async def test_a_message_added_then_removed_in_one_page_is_not_also_added():
    """Otherwise sync upserts a row it is about to delete, or worse, in that order."""
    body = {
        "history": [
            {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
            {"id": "102", "messagesDeleted": [{"message": {"id": "m1"}}]},
        ],
        "historyId": "102",
    }
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert page["added_ids"] == []
    assert page["removed_ids"] == ["m1"]


@pytest.mark.asyncio
async def test_the_same_message_is_not_reported_twice():
    """A message gaining several labels appears in several history records."""
    body = {
        "history": [
            {"id": "101", "messagesAdded": [{"message": {"id": "m1"}}]},
            {"id": "102", "messagesAdded": [{"message": {"id": "m1"}}]},
        ],
        "historyId": "102",
    }
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert page["added_ids"] == ["m1"]


@pytest.mark.asyncio
async def test_a_history_id_too_old_to_serve_raises_history_expired():
    """Gmail keeps history for about a week. Past that the cursor is unusable and
    the only correct response is a full backfill, not an error page.
    """
    error = httpx.HTTPStatusError(
        "not found",
        request=httpx.Request("GET", "https://gmail.googleapis.com/x"),
        response=_response({"error": {"code": 404}}, status=404),
    )
    with patch("app.services.gmail_client._get_json", AsyncMock(side_effect=error)):
        with pytest.raises(HistoryExpired):
            await gmail_client.fetch_history_page("tok", "1")


@pytest.mark.asyncio
async def test_other_http_errors_are_not_swallowed_as_expired_history():
    error = httpx.HTTPStatusError(
        "server error",
        request=httpx.Request("GET", "https://gmail.googleapis.com/x"),
        response=_response({}, status=500),
    )
    with patch("app.services.gmail_client._get_json", AsyncMock(side_effect=error)):
        with pytest.raises(httpx.HTTPStatusError):
            await gmail_client.fetch_history_page("tok", "1")


@pytest.mark.asyncio
async def test_an_unchanged_mailbox_reports_no_history_and_keeps_its_cursor():
    """history.list omits the array entirely when nothing changed."""
    with patch(
        "app.services.gmail_client._get_json", AsyncMock(return_value={"historyId": "100"})
    ):
        page = await gmail_client.fetch_history_page("tok", "100")

    assert page["added_ids"] == []
    assert page["removed_ids"] == []
    assert page["history_id"] == "100"


@pytest.mark.asyncio
async def test_history_pages_carry_their_page_token_forward():
    body = {"nextPageToken": "p2", "historyId": "9"}
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)) as get:
        page = await gmail_client.fetch_history_page("tok", "5", page_token="p1")

    url = get.await_args.args[1]
    assert "startHistoryId=5" in url
    assert "pageToken=p1" in url
    assert page["next_page_token"] == "p2"


# --------------------------------------------------------------------------
# Backfill: messages.list
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_queries_by_date_in_the_format_gmail_accepts():
    """Gmail's q only understands after:YYYY/MM/DD - an ISO timestamp matches nothing."""
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value={})) as get:
        await gmail_client.fetch_message_ids_since(
            "tok", datetime(2026, 9, 3, tzinfo=timezone.utc)
        )

    url = unquote(get.await_args.args[1])
    assert "after:2026/09/03" in url
    # Not the ISO form, which Gmail accepts and then matches nothing against.
    assert "2026-09-03" not in url


@pytest.mark.asyncio
async def test_backfill_returns_ids_and_its_page_token():
    body = {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"}
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value=body)):
        page = await gmail_client.fetch_message_ids_since(
            "tok", datetime(2026, 9, 3, tzinfo=timezone.utc)
        )

    assert page["message_ids"] == ["m1", "m2"]
    assert page["next_page_token"] == "p2"


@pytest.mark.asyncio
async def test_an_empty_mailbox_backfills_to_nothing_rather_than_raising():
    """messages.list omits the array when there are no matches."""
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value={})):
        page = await gmail_client.fetch_message_ids_since(
            "tok", datetime(2026, 9, 3, tzinfo=timezone.utc)
        )

    assert page["message_ids"] == []
    assert page["next_page_token"] is None


@pytest.mark.asyncio
async def test_the_mailbox_history_id_can_be_read_to_seed_the_cursor():
    """After a backfill there is no history to page through, so the cursor has to
    come from the profile - otherwise the next sync backfills all over again.
    """
    with patch(
        "app.services.gmail_client._get_json", AsyncMock(return_value={"historyId": "4242"})
    ):
        assert await gmail_client.current_history_id("tok") == "4242"


# --------------------------------------------------------------------------
# messages.get
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_format_is_requested_for_the_filter_pass():
    """Bodies are the expensive part, in both Gmail quota and LLM tokens. The
    filter only needs headers, so the first pass must not pull bodies.
    """
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value={"id": "m1"})) as get:
        await gmail_client.fetch_message("tok", "m1", full=False)

    assert "format=metadata" in get.await_args.args[1]


@pytest.mark.asyncio
async def test_full_format_is_requested_only_when_the_body_is_wanted():
    with patch("app.services.gmail_client._get_json", AsyncMock(return_value={"id": "m1"})) as get:
        await gmail_client.fetch_message("tok", "m1", full=True)

    assert "format=full" in get.await_args.args[1]


@pytest.mark.asyncio
async def test_a_message_deleted_between_list_and_get_is_none_not_an_error():
    """The window is small but real, and one vanished message must not abort the
    whole sync run.
    """
    error = httpx.HTTPStatusError(
        "not found",
        request=httpx.Request("GET", "https://gmail.googleapis.com/x"),
        response=_response({}, status=404),
    )
    with patch("app.services.gmail_client._get_json", AsyncMock(side_effect=error)):
        assert await gmail_client.fetch_message("tok", "gone", full=True) is None


# --------------------------------------------------------------------------
# Normalization - the shape the rest of the app already understands
# --------------------------------------------------------------------------


def test_headers_become_the_same_fields_graph_messages_produce():
    raw = {
        "id": "m1",
        "internalDate": "1757000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Q3 numbers"},
                {"name": "From", "value": "Dana Scully <dana@example.com>"},
                {"name": "To", "value": "me@example.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("Can you review the deck before Thursday?")},
        },
    }
    msg = normalize_message(raw)

    assert msg["id"] == "m1"
    assert msg["subject"] == "Q3 numbers"
    assert msg["from_address"] == "dana@example.com"
    assert msg["from_name"] == "Dana Scully"
    assert msg["body"] == "Can you review the deck before Thursday?"


def test_header_lookup_ignores_case():
    """Header names are case-insensitive per RFC 5322 and senders vary."""
    raw = {
        "id": "m1",
        "payload": {
            "headers": [
                {"name": "subject", "value": "lower"},
                {"name": "FROM", "value": "a@b.com"},
            ]
        },
    }
    msg = normalize_message(raw)

    assert msg["subject"] == "lower"
    assert msg["from_address"] == "a@b.com"


def test_a_bare_address_with_no_display_name_has_no_name():
    raw = {"id": "m1", "payload": {"headers": [{"name": "From", "value": "a@b.com"}]}}
    assert normalize_message(raw)["from_name"] is None


def test_every_recipient_is_kept_with_the_key_names_the_schema_uses():
    raw = {
        "id": "m1",
        "payload": {
            "headers": [
                {"name": "To", "value": "A One <a@example.com>, b@example.com"},
                {"name": "Cc", "value": "Carol <c@example.com>"},
            ]
        },
    }
    msg = normalize_message(raw)

    assert msg["to_recipients"] == [
        {"address": "a@example.com", "name": "A One"},
        {"address": "b@example.com", "name": None},
        {"address": "c@example.com", "name": "Carol"},
    ]


def test_received_at_comes_from_internal_date_not_the_date_header():
    """internalDate is Gmail's own receive time. The Date header is written by the
    sender and is routinely wrong, missing, or in a bogus timezone - trusting it
    puts mail in the wrong order and skews the activity chart.
    """
    raw = {
        "id": "m1",
        "internalDate": "1757000000000",
        "payload": {"headers": [{"name": "Date", "value": "Tue, 1 Jan 1980 00:00:00 +0000"}]},
    }
    received = normalize_message(raw)["received_at"]

    assert received == datetime.fromtimestamp(1757000000, tz=timezone.utc)
    assert received.tzinfo is not None


def test_a_message_with_no_internal_date_has_no_received_at():
    raw = {"id": "m1", "payload": {"headers": []}}
    assert normalize_message(raw)["received_at"] is None


def test_plain_text_is_preferred_over_the_html_alternative():
    """multipart/alternative carries both. The HTML part is mostly markup, which
    would be truncated away before the model ever saw the words.
    """
    raw = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>styled</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64("the words")}},
            ],
        },
    }
    assert normalize_message(raw)["body"] == "the words"


def test_html_is_used_when_there_is_no_plain_text_part():
    raw = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [],
            "parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>only this</p>")}}],
        },
    }
    assert "only this" in normalize_message(raw)["body"]


def test_the_body_is_found_inside_nested_multiparts():
    """multipart/mixed wrapping multipart/alternative is what any mail with an
    attachment looks like. A single-level scan finds nothing.
    """
    raw = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/plain", "body": {"data": _b64("buried")}}],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "deck.pdf",
                    "body": {"attachmentId": "a1"},
                },
            ],
        },
    }
    assert normalize_message(raw)["body"] == "buried"


def test_an_attachment_is_never_mistaken_for_the_body():
    raw = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "notes.txt",
                    "body": {"data": _b64("attached file")},
                },
                {"mimeType": "text/plain", "body": {"data": _b64("the actual message")}},
            ],
        },
    }
    assert normalize_message(raw)["body"] == "the actual message"


def test_unpadded_base64url_decodes_correctly():
    """Gmail omits the = padding that the stdlib decoder requires. Without
    re-padding this raises binascii.Error on most real messages.
    """
    # Lengths chosen so every padding case mod 4 is covered.
    for text in ("a", "ab", "abc", "abcd", "Hi there, quick question"):
        raw = {
            "id": "m1",
            "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": _b64(text)}},
        }
        assert normalize_message(raw)["body"] == text


def test_base64url_specific_characters_survive_decoding():
    """Gmail uses the URL-safe alphabet (- and _). The standard decoder rejects them."""
    payload = "unicode - em dash — and divide ÷ and accents ünïcode"
    raw = {
        "id": "m1",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": _b64(payload)}},
    }
    assert normalize_message(raw)["body"] == payload


def test_undecodable_bytes_do_not_abort_the_sync():
    raw = {
        "id": "m1",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": "!!!not base64!!!"}},
    }
    assert normalize_message(raw)["body"] is None


def test_a_metadata_only_message_normalizes_without_a_body():
    """format=metadata returns headers and no body at all - the filter pass relies
    on this not raising.
    """
    raw = {
        "id": "m1",
        "labelIds": ["INBOX"],
        "payload": {"headers": [{"name": "From", "value": "a@b.com"}]},
    }
    msg = normalize_message(raw)

    assert msg["from_address"] == "a@b.com"
    assert msg["body"] is None


# --------------------------------------------------------------------------
# Provider classification - feeding the existing extraction filter
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    ["CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS", "SPAM"],
)
def test_gmails_bulk_categories_map_to_the_classification_the_filter_reads(label):
    """Gmail's category labels are the equivalent of Microsoft's
    inferenceClassification=other. Emitting the same key lets one filter serve
    both providers with no provider branch inside it.
    """
    raw = {"id": "m1", "labelIds": ["INBOX", label], "payload": {"headers": []}}
    assert normalize_message(raw)["inference_classification"] == "other"


def test_personal_mail_is_classified_focused():
    raw = {"id": "m1", "labelIds": ["INBOX", "IMPORTANT"], "payload": {"headers": []}}
    assert normalize_message(raw)["inference_classification"] == "focused"


def test_a_message_with_no_labels_is_not_assumed_to_be_bulk():
    """Unknown must never read as bulk, or real mail is silently dropped."""
    raw = {"id": "m1", "payload": {"headers": []}}
    assert normalize_message(raw)["inference_classification"] == "focused"
