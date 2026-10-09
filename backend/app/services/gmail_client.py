"""Gmail API client, shaped to the same contract as graph_client.

Gmail's incremental model is not Graph's, and the differences are not cosmetic:

                    Microsoft Graph              Gmail
  cursor            @odata.deltaLink (opaque)    historyId (integer)
  pagination        @odata.nextLink              pageToken
  backfill          $filter=receivedDateTime ge  q=after:YYYY/MM/DD
  body              returned inline              separate messages.get per id
  expired cursor    410 -> full resync           404 -> full resync

So this module does the reassembly Graph does for us: work out what changed,
fetch each message, decode its body, and hand back a mapping with the same keys
a normalized Graph message has. Everything downstream - the extraction filter,
the repositories, the UI - then stays provider-agnostic.
"""

import asyncio
import base64
import binascii
import logging
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Gmail's own category labels. These are the equivalent of Microsoft's
# inferenceClassification=other: the provider has already decided this is bulk,
# at no extra API cost to us.
_BULK_LABELS = frozenset(
    {
        "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL",
        "CATEGORY_UPDATES",
        "CATEGORY_FORUMS",
        "SPAM",
    }
)

# A message acquiring one of these has left the inbox as far as the user is
# concerned, even though Gmail reports no deletion.
_REMOVING_LABELS = frozenset({"TRASH", "SPAM"})


class GoogleRefreshError(Exception):
    """The refresh token no longer works - the mailbox needs re-consent."""


class HistoryExpired(Exception):
    """startHistoryId is older than Gmail retains; the caller must backfill."""


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token.

    Deliberately synchronous, mirroring graph_client.refresh_access_token so the
    account-token service can treat both providers the same way.
    """
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    body: dict = {}
    try:
        body = response.json()
    except ValueError:
        pass

    if response.status_code != 200 or "access_token" not in body:
        raise GoogleRefreshError(
            body.get("error_description") or body.get("error") or "refresh failed"
        )

    return {
        "access_token": body["access_token"],
        # Google issues a refresh token only on the first consent. Treating its
        # absence as None would throw away the only credential that can renew
        # this mailbox.
        "refresh_token": body.get("refresh_token") or refresh_token,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"]),
    }


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict) -> dict:
    response = await client.get(url, headers=headers)
    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", "1"))
        await asyncio.sleep(retry_after)
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def _auth(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


async def fetch_history_page(
    access_token: str, history_id: str, page_token: str | None = None
) -> dict:
    """One page of history.list, reduced to what changed.

    Returns added_ids / removed_ids / next_page_token / history_id.

    Gmail reports change events, not state, and a single message can appear in
    several events across one page - added, then labelled, then trashed. The last
    event for a message is the one that reflects reality, so that is the one kept.
    """
    url = (
        f"{GMAIL_BASE_URL}/history?startHistoryId={quote(str(history_id))}"
        "&historyTypes=messageAdded&historyTypes=messageDeleted"
        "&historyTypes=labelAdded&historyTypes=labelRemoved"
    )
    if page_token:
        url += f"&pageToken={quote(page_token)}"

    async with httpx.AsyncClient() as client:
        try:
            body = await _get_json(client, url, _auth(access_token))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Gmail retains history for roughly a week. Past that the cursor
                # is unusable and backfilling is the only way back, so this is
                # signalled distinctly rather than as a generic HTTP failure.
                raise HistoryExpired(
                    f"historyId {history_id} is no longer available"
                ) from exc
            raise

    # dict, not two lists: insertion order gives us "last event wins" for free
    # while still preserving the order ids were first seen in.
    state: dict[str, str] = {}

    for record in body.get("history") or []:
        for entry in record.get("messagesAdded") or []:
            state[(entry.get("message") or {}).get("id")] = "added"

        for entry in record.get("labelsAdded") or []:
            message_id = (entry.get("message") or {}).get("id")
            labels = set(entry.get("labelIds") or [])
            if labels & _REMOVING_LABELS:
                state[message_id] = "removed"
            elif "INBOX" in labels:
                # Restored from trash, or filed into the inbox after the fact.
                state[message_id] = "added"

        for entry in record.get("labelsRemoved") or []:
            message_id = (entry.get("message") or {}).get("id")
            if "INBOX" in set(entry.get("labelIds") or []):
                # Archived. Gmail never reports a deletion for this, so without
                # it archived mail would sit in our inbox view forever.
                state[message_id] = "removed"

        for entry in record.get("messagesDeleted") or []:
            state[(entry.get("message") or {}).get("id")] = "removed"

    state.pop(None, None)

    return {
        "added_ids": [mid for mid, verb in state.items() if verb == "added"],
        "removed_ids": [mid for mid, verb in state.items() if verb == "removed"],
        "next_page_token": body.get("nextPageToken"),
        # An unchanged mailbox returns only historyId, which is still the right
        # cursor to store.
        "history_id": body.get("historyId"),
    }


async def fetch_message_ids_since(
    access_token: str, since: datetime, page_token: str | None = None
) -> dict:
    """One page of messages.list for the initial backfill.

    Gmail's search syntax only understands whole days (after:YYYY/MM/DD); an ISO
    timestamp silently matches nothing. Scoped to the inbox to match what the
    Microsoft side syncs (mailFolders/inbox).
    """
    query = quote(f"after:{since.strftime('%Y/%m/%d')} in:inbox")
    url = f"{GMAIL_BASE_URL}/messages?q={query}&maxResults=100"
    if page_token:
        url += f"&pageToken={quote(page_token)}"

    async with httpx.AsyncClient() as client:
        body = await _get_json(client, url, _auth(access_token))

    return {
        "message_ids": [m["id"] for m in body.get("messages") or []],
        "next_page_token": body.get("nextPageToken"),
    }


async def current_history_id(access_token: str) -> str | None:
    """The mailbox's current historyId, from users.getProfile.

    A backfill walks messages.list, which yields no history cursor. Without
    reading it here the next run would have nothing to resume from and would
    backfill all over again.
    """
    async with httpx.AsyncClient() as client:
        body = await _get_json(client, f"{GMAIL_BASE_URL}/profile", _auth(access_token))
    return body.get("historyId")


async def fetch_message(access_token: str, message_id: str, *, full: bool) -> dict | None:
    """One message. Returns None if it has since been deleted.

    `full` is the cost control: the filter pass (§5.5) only needs headers, and
    bodies are what consume both Gmail quota and the LLM token budget. Keyword-only
    so no call site can flip it by accident.
    """
    fmt = "full" if full else "metadata"
    url = f"{GMAIL_BASE_URL}/messages/{quote(message_id)}?format={fmt}"

    async with httpx.AsyncClient() as client:
        try:
            return await _get_json(client, url, _auth(access_token))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Deleted between being listed and being fetched. A small window,
                # but one vanished message must not abort the whole run.
                logger.info("gmail message %s vanished before fetch", message_id)
                return None
            raise


def _header(payload: dict, name: str) -> str | None:
    """Case-insensitive header lookup - RFC 5322 names are case-insensitive and
    real senders vary ("From", "FROM", "from")."""
    wanted = name.lower()
    for header in payload.get("headers") or []:
        if (header.get("name") or "").lower() == wanted:
            return header.get("value")
    return None


def _decode(data: str | None) -> str | None:
    if not data:
        return None
    # Gmail uses base64url and strips the padding the stdlib decoder requires.
    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        # Malformed is not fatal: the row is still worth storing without a body.
        return None
    return raw.decode("utf-8", errors="replace")


def _collect_parts(part: dict, out: list[dict]) -> None:
    """Flatten the MIME tree. multipart/mixed wrapping multipart/alternative is
    what any message with an attachment looks like, so a single-level scan over
    payload["parts"] finds nothing."""
    out.append(part)
    for child in part.get("parts") or []:
        _collect_parts(child, out)


def _body_text(payload: dict) -> str | None:
    parts: list[dict] = []
    _collect_parts(payload, parts)

    def pick(mime: str) -> str | None:
        for part in parts:
            # A filename means an attachment. A text/plain attachment would
            # otherwise shadow the real message body.
            if part.get("mimeType") == mime and not part.get("filename"):
                decoded = _decode((part.get("body") or {}).get("data"))
                if decoded is not None:
                    return decoded
        return None

    # Plain text first: the HTML alternative is mostly markup, which would eat
    # the truncation budget before the model reached any actual words.
    return pick("text/plain") or pick("text/html")


def normalize_message(raw: dict) -> dict:
    """A Gmail message as the rest of the app expects to see one.

    The keys match what the extraction filter reads and what EmailsRepository
    upserts, so neither needs to know which provider a message came from.
    """
    payload = raw.get("payload") or {}

    from_name, from_address = parseaddr(_header(payload, "From") or "")

    recipients: list[dict] = []
    raw_recipients = [
        value for name in ("To", "Cc") if (value := _header(payload, name)) is not None
    ]
    for name, address in getaddresses(raw_recipients):
        if address:
            recipients.append({"address": address, "name": name or None})

    received_at = None
    if internal_date := raw.get("internalDate"):
        # Gmail's own receive time, in milliseconds. Preferred over the Date
        # header, which is written by the sender and is routinely wrong, absent,
        # or in a bogus timezone.
        received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)

    labels = set(raw.get("labelIds") or [])

    return {
        "id": raw.get("id"),
        "subject": _header(payload, "Subject"),
        "from_address": from_address or None,
        "from_name": from_name or None,
        "to_recipients": recipients,
        "received_at": received_at,
        "body": _body_text(payload),
        # "focused" unless Gmail says otherwise. Unknown must never read as bulk
        # or real mail gets silently dropped before extraction.
        "inference_classification": "other" if labels & _BULK_LABELS else "focused",
    }
