import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from html import unescape

import asyncpg

from app.core.config import settings
from app.repositories.action_items import ActionItemsRepository
from app.repositories.contacts import ContactsRepository
from app.repositories.llm_quota import LlmQuotaRepository
from app.repositories.profiles import ProfilesRepository
from app.services import openai_client
from app.services.openai_client import QuotaExhausted
from app.services.extraction_filter import should_extract

logger = logging.getLogger(__name__)

_UNBOUNDED = 1_000_000

# After this many failed attempts a message is abandoned rather than re-queued.
# Without a bound, a message that can never be extracted is re-fetched forever,
# and because the pending query is oldest-first with a limit, enough of them
# permanently fill the window and starve newer mail.
_MAX_EXTRACTION_ATTEMPTS = 3

_TABLES = {
    "email": "emails",
    "calendar_event": "calendar_events",
    "chat_message": "chat_messages",
}


def _strip_html(value: str | None) -> str | None:
    if not value:
        return value
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_body(value: str | None) -> str:
    """Cap a message body before it reaches the model.

    Truncates from the end deliberately: commitments and requests cluster near
    the top of a message, while quoted reply chains and signatures accumulate at
    the bottom. Keeping the head preserves the useful part.
    """
    if not value:
        return ""
    limit = settings.llm_max_body_chars
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[truncated]"


def _parse_due_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def _process_item(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    content: str,
    participants: list[tuple[str | None, str | None]],
    own_email: str | None,
) -> None:
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)

    resolved: list[dict] = []
    for i, (email_address, display_name) in enumerate(participants):
        if email_address and own_email and email_address.lower() == own_email.lower():
            continue
        if not email_address and not display_name:
            continue
        ref = f"p{i}"
        if email_address:
            existing = await contacts.get_by_email(user_id, email_address)
        else:
            existing = await contacts.get_by_display_name(user_id, display_name)
        resolved.append(
            {
                "ref": ref,
                "email": email_address,
                "name": display_name or (existing["display_name"] if existing else None),
                "notes": existing["notes"] if existing else None,
            }
        )

    if resolved:
        result = await openai_client.extract(content, resolved)
    else:
        result = {"people": [], "action_items": []}

    table = _TABLES[source_type]
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref_to_contact_id: dict[str, uuid.UUID] = {}
            for person in result.get("people", []):
                match = next((p for p in resolved if p["ref"] == person["ref"]), None)
                if match is None:
                    continue
                if match["email"]:
                    contact_id = await contacts.upsert_by_email(
                        user_id, match["email"], match["name"], person["notes"], conn=conn
                    )
                else:
                    contact_id = await contacts.upsert_by_display_name(
                        user_id, match["name"], person["notes"], conn=conn
                    )
                ref_to_contact_id[match["ref"]] = contact_id

            for item in result.get("action_items", []):
                contact_id = ref_to_contact_id.get(item.get("participant_ref"))
                await action_items.insert(
                    user_id=user_id,
                    contact_id=contact_id,
                    text=item["text"],
                    direction=item["direction"],
                    due_date=_parse_due_date(item.get("due_date")),
                    source_type=source_type,
                    source_id=source_id,
                    # extract() guarantees this via its schema, but _process_item
                    # is also driven directly in tests - default rather than
                    # raise, since the database constraint is the real guard.
                    category=item.get("category") or "other",
                    conn=conn,
                )

            await conn.execute(
                f"update public.{table} set extracted_at = now() where id = $1",
                source_id,
            )


async def _record_extraction_failure(
    pool: asyncpg.Pool, table: str, source_id: uuid.UUID, exc: Exception
) -> None:
    """Count the attempt and keep the reason.

    Once the count reaches _MAX_EXTRACTION_ATTEMPTS the pending query stops
    selecting the row, so it leaves the queue without ever being marked
    successfully extracted.
    """
    await pool.execute(
        f"""
        update public.{table}
        set extraction_attempts = extraction_attempts + 1,
            extraction_error = $2
        where id = $1
        """,
        source_id,
        f"{type(exc).__name__}: {exc}"[:500],
    )
    logger.warning(
        "Extraction failed for %s %s: %s", table, source_id, exc, exc_info=True
    )


async def _record_skipped(
    pool: asyncpg.Pool, table: str, source_id: uuid.UUID, reason: str
) -> None:
    """Mark a message the filter rejected as handled.

    extracted_at is stamped here, unlike the failure path: the decision is
    final, not a transient error worth retrying. Leaving it null would put the
    row back at the head of the oldest-first window on every run, crowding out
    the mail actually worth reading.
    """
    await pool.execute(
        f"""
        update public.{table}
        set extracted_at = now(),
            extraction_error = $2
        where id = $1
        """,
        source_id,
        f"skipped: {reason}"[:500],
    )


async def _extract_pending_emails(
    pool: asyncpg.Pool, user_id: uuid.UUID, own_email: str | None, limit: int | None
) -> int:
    table = "emails"
    rows = await pool.fetch(
        "select id, from_address, subject, body_text from public.emails "
        "where user_id = $1 and extracted_at is null "
        "and extraction_attempts < $3 "
        "order by received_at asc nulls last limit $2",
        user_id,
        limit if limit is not None else _UNBOUNDED,
        _MAX_EXTRACTION_ATTEMPTS,
    )
    processed = 0
    for row in rows:
        try:
            body = _strip_html(row["body_text"])
            keep, reason = should_extract(
                {"from_address": row["from_address"], "body": body}
            )
            if not keep:
                # Filtered before the model, not after: the whole point is to
                # not spend tokens on it.
                await _record_skipped(pool, table, row["id"], reason)
                continue

            content = f"Subject: {row['subject'] or ''}\n\n{_truncate_body(body)}"
            await _process_item(
                pool, user_id, "email", row["id"], content,
                [(row["from_address"], None)], own_email,
            )
        except QuotaExhausted:
            # Not this message's fault. Leave it pending with its attempt count
            # untouched and stop the run - every further call would fail too.
            raise
        except Exception as exc:
            await _record_extraction_failure(pool, table, row["id"], exc)
            continue
        processed += 1
    return processed


async def _extract_pending_calendar_events(
    pool: asyncpg.Pool, user_id: uuid.UUID, own_email: str | None, limit: int | None
) -> int:
    table = "calendar_events"
    rows = await pool.fetch(
        "select id, organizer, attendees, subject, body_text from public.calendar_events "
        "where user_id = $1 and extracted_at is null "
        "and extraction_attempts < $3 "
        "order by start_time asc nulls last limit $2",
        user_id,
        limit if limit is not None else _UNBOUNDED,
        _MAX_EXTRACTION_ATTEMPTS,
    )
    processed = 0
    for row in rows:
        try:
            content = f"Subject: {row['subject'] or ''}\n\n{_truncate_body(_strip_html(row['body_text']))}"

            raw_people: list[tuple[str | None, str | None]] = []
            if row["organizer"]:
                raw_people.append((row["organizer"], None))
            attendees = json.loads(row["attendees"]) if row["attendees"] else []
            for attendee in attendees:
                attendee_email = attendee.get("address")
                attendee_name = attendee.get("name")
                if attendee_email or attendee_name:
                    raw_people.append((attendee_email, attendee_name))

            # Dedupe: the organizer is frequently also present in the attendees
            # list, and would otherwise produce two participant refs for the
            # same person - harmless (both resolve/upsert to the same contact
            # row) but wasteful and confusing to include twice in the LLM
            # prompt. Prefer whichever occurrence has a name.
            by_email: dict[str, tuple[str | None, str | None]] = {}
            no_email: list[tuple[str | None, str | None]] = []
            for person_email, person_name in raw_people:
                if person_email:
                    key = person_email.lower()
                    current = by_email.get(key)
                    if current is None or (person_name and not current[1]):
                        by_email[key] = (person_email, person_name)
                elif not any(existing_name == person_name for _, existing_name in no_email):
                    no_email.append((None, person_name))
            participants = list(by_email.values()) + no_email

            await _process_item(pool, user_id, "calendar_event", row["id"], content, participants, own_email)
        except QuotaExhausted:
            # Not this message's fault. Leave it pending with its attempt count
            # untouched and stop the run - every further call would fail too.
            raise
        except Exception as exc:
            await _record_extraction_failure(pool, table, row["id"], exc)
            continue
        processed += 1
    return processed


async def _extract_pending_chat_messages(
    pool: asyncpg.Pool, user_id: uuid.UUID, own_email: str | None, limit: int | None
) -> int:
    table = "chat_messages"
    rows = await pool.fetch(
        "select id, from_user, content from public.chat_messages "
        "where user_id = $1 and extracted_at is null "
        "and extraction_attempts < $3 "
        "order by sent_at asc nulls last limit $2",
        user_id,
        limit if limit is not None else _UNBOUNDED,
        _MAX_EXTRACTION_ATTEMPTS,
    )
    processed = 0
    for row in rows:
        try:
            body = _strip_html(row["content"])
            # No sender address on a chat message, so only the content rules
            # apply - which is where the value is anyway: acknowledgements
            # dominate chat volume and cost a full prompt preamble each.
            keep, reason = should_extract({"body": body})
            if not keep:
                await _record_skipped(pool, table, row["id"], reason)
                continue

            content = _truncate_body(body)
            await _process_item(
                pool, user_id, "chat_message", row["id"], content,
                [(None, row["from_user"])], own_email,
            )
        except QuotaExhausted:
            # Not this message's fault. Leave it pending with its attempt count
            # untouched and stop the run - every further call would fail too.
            raise
        except Exception as exc:
            await _record_extraction_failure(pool, table, row["id"], exc)
            continue
        processed += 1
    return processed


async def extract_user(pool: asyncpg.Pool, user_id: uuid.UUID, limit: int | None) -> None:
    quota = LlmQuotaRepository(pool)

    if await quota.is_exhausted():
        # Return before touching anything. The sync cron fires every fifteen
        # minutes and a daily exhaustion lasts hours, so the alternative is
        # spending the night issuing requests that can only fail.
        logger.info("Skipping extraction for user %s: provider quota exhausted", user_id)
        return

    profile = await ProfilesRepository(pool).get(user_id)
    own_email = profile["email"] if profile else None

    remaining = limit
    for scan_fn in (_extract_pending_emails, _extract_pending_calendar_events, _extract_pending_chat_messages):
        if remaining is not None and remaining <= 0:
            break
        try:
            processed = await scan_fn(pool, user_id, own_email, remaining)
        except QuotaExhausted as exc:
            until = datetime.now(timezone.utc) + timedelta(seconds=exc.retry_after_seconds)
            await quota.mark_exhausted(until)
            logger.warning(
                "Provider quota exhausted; pausing extraction until %s", until.isoformat()
            )
            return
        if remaining is not None:
            remaining -= processed
