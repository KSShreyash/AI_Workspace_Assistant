import json as json_module
import uuid
from unittest.mock import patch

import pytest

from app.repositories.action_items import ActionItemsRepository
from app.repositories.contacts import ContactsRepository
from app.repositories.profiles import ProfilesRepository
from app.core.text import strip_html
from app.services.ai_extraction import _parse_due_date, _process_item, extract_user


def test_strip_html_removes_tags_and_unescapes_entities():
    assert strip_html("<p>Hi &amp; welcome</p>") == "Hi & welcome"


def test_strip_html_handles_none():
    assert strip_html(None) is None


def test_strip_html_collapses_whitespace():
    assert strip_html("<div>a</div>\n\n<div>b</div>") == "a b"


def test_parse_due_date_valid_iso():
    assert _parse_due_date("2026-08-01").isoformat() == "2026-08-01"


def test_parse_due_date_none_and_invalid():
    assert _parse_due_date(None) is None
    assert _parse_due_date("not-a-date") is None


@pytest.mark.asyncio
async def test_process_item_creates_contact_and_action_item(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    result = {
        "people": [{"ref": "p0", "notes": "Works at Acme"}],
        "action_items": [
            {"text": "Send the deck", "direction": "mine", "due_date": "2026-08-01", "participant_ref": "p0"}
        ],
    }
    source_id = uuid.uuid4()

    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(
            pool, user_id, "email", source_id, "content",
            [("alice@example.com", "Alice")], own_email=email,
        )

    assert await ContactsRepository(pool).count(user_id) == 1
    assert await ActionItemsRepository(pool).count(user_id) == 1

    contact = await ContactsRepository(pool).get_by_email(user_id, "alice@example.com")
    assert contact["notes"] == "Works at Acme"

    item = await pool.fetchrow(
        "select contact_id, due_date, source_type, source_id from public.action_items where user_id = $1",
        user_id,
    )
    assert item["contact_id"] == contact["id"]
    assert item["due_date"].isoformat() == "2026-08-01"
    assert item["source_type"] == "email"
    assert item["source_id"] == source_id


@pytest.mark.asyncio
async def test_process_item_marks_extracted(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    row = await pool.fetchrow(
        "insert into public.emails (user_id, account_id, provider_message_id) values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2) returning id",
        user_id, "msg-1",
    )
    source_id = row["id"]

    result = {"people": [], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(pool, user_id, "email", source_id, "content", [], own_email=email)

    row = await pool.fetchrow("select extracted_at from public.emails where id = $1", source_id)
    assert row["extracted_at"] is not None


@pytest.mark.asyncio
async def test_process_item_uses_display_name_when_no_email(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    result = {"people": [{"ref": "p0", "notes": "Chatted about launch"}], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(
            pool, user_id, "chat_message", uuid.uuid4(), "content",
            [(None, "Bob")], own_email=email,
        )

    contact = await ContactsRepository(pool).get_by_display_name(user_id, "Bob")
    assert contact is not None
    assert contact["email_address"] is None
    assert contact["notes"] == "Chatted about launch"


@pytest.mark.asyncio
async def test_process_item_excludes_own_email_from_participants(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    with patch("app.services.ai_extraction.openai_client.extract") as mock_extract:
        mock_extract.return_value = {"people": [], "action_items": []}
        await _process_item(
            pool, user_id, "calendar_event", uuid.uuid4(), "content",
            [(email, "Me"), ("other@example.com", "Other")], own_email=email,
        )

    call_args = mock_extract.call_args[0]
    participants_arg = call_args[1]
    assert all(p["email"] != email for p in participants_arg)
    assert any(p["email"] == "other@example.com" for p in participants_arg)


@pytest.mark.asyncio
async def test_process_item_no_participants_skips_openai_but_still_marks_extracted(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    row = await pool.fetchrow(
        "insert into public.emails (user_id, account_id, provider_message_id) values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2) returning id",
        user_id, "msg-2",
    )
    source_id = row["id"]

    with patch("app.services.ai_extraction.openai_client.extract") as mock_extract:
        await _process_item(pool, user_id, "email", source_id, "content", [], own_email=email)

    mock_extract.assert_not_called()
    row = await pool.fetchrow("select extracted_at from public.emails where id = $1", source_id)
    assert row["extracted_at"] is not None


@pytest.mark.asyncio
async def test_process_item_rolls_back_on_action_item_failure(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    row = await pool.fetchrow(
        "insert into public.emails (user_id, account_id, provider_message_id) values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2) returning id",
        user_id, "msg-3",
    )
    source_id = row["id"]

    result = {
        "people": [{"ref": "p0", "notes": "notes"}],
        "action_items": [
            # invalid direction violates the action_items check constraint,
            # forcing the transaction to fail after the contact upsert already ran
            {"text": "bad", "direction": "not-a-real-direction", "due_date": None, "participant_ref": "p0"}
        ],
    }

    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        with pytest.raises(Exception):
            await _process_item(
                pool, user_id, "email", source_id, "content",
                [("carl@example.com", "Carl")], own_email=email,
            )

    # Nothing committed: no contact, no action item, extracted_at still null
    assert await ContactsRepository(pool).count(user_id) == 0
    assert await ActionItemsRepository(pool).count(user_id) == 0
    row = await pool.fetchrow("select extracted_at from public.emails where id = $1", source_id)
    assert row["extracted_at"] is None


@pytest.mark.asyncio
async def test_extract_user_processes_pending_email(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    row = await pool.fetchrow(
        "insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5) returning id",
        user_id, "msg-x", "irene@example.com", "Hello", "<p>Could you review the draft and send feedback before Thursday?</p>",
    )

    result = {"people": [{"ref": "p0", "notes": "Said hi"}], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result) as mock_extract:
        await extract_user(pool, user_id, limit=10)

    mock_extract.assert_called_once()
    content_arg = mock_extract.call_args[0][0]
    assert "<p>" not in content_arg  # HTML was stripped before reaching the LLM
    assert await ContactsRepository(pool).count(user_id) == 1

    updated = await pool.fetchrow("select extracted_at from public.emails where id = $1", row["id"])
    assert updated["extracted_at"] is not None


@pytest.mark.asyncio
async def test_extract_user_processes_calendar_event_with_multiple_attendees(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        "insert into public.calendar_events (user_id, account_id, provider_event_id, organizer, attendees, subject, body_text) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4::jsonb, $5, $6)",
        user_id, "evt-x", "jane@example.com",
        json_module.dumps([{"address": "jane@example.com", "name": "Jane"}, {"address": "kyle@example.com", "name": "Kyle"}]),
        "Sync", "Weekly sync",
    )

    result = {"people": [], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result) as mock_extract:
        await extract_user(pool, user_id, limit=10)

    mock_extract.assert_called_once()
    participants_arg = mock_extract.call_args[0][1]
    # organizer (jane) also appears in attendees - must be deduped to one
    # participant ref, not two, even though the raw data mentions her twice.
    assert len(participants_arg) == 2
    jane = next(p for p in participants_arg if p["email"] == "jane@example.com")
    assert jane["name"] == "Jane"
    assert any(p["email"] == "kyle@example.com" for p in participants_arg)


@pytest.mark.asyncio
async def test_extract_user_processes_chat_message_by_display_name(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        "insert into public.chat_messages (user_id, account_id, provider_chat_id, provider_message_id, from_user, content) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
        user_id, "chat-1", "cm-1", "Laura", "Can you send that file?",
    )

    result = {"people": [{"ref": "p0", "notes": "Asked for a file"}], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await extract_user(pool, user_id, limit=10)

    contact = await ContactsRepository(pool).get_by_display_name(user_id, "Laura")
    assert contact is not None


@pytest.mark.asyncio
async def test_extract_user_respects_limit_across_tables(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    for i in range(3):
        await pool.execute(
            "insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text) "
            "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
            user_id, f"msg-limit-{i}", f"person{i}@example.com", "Hi", "Could you review the draft and send feedback before Thursday?",
        )

    result = {"people": [], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result) as mock_extract:
        await extract_user(pool, user_id, limit=2)

    assert mock_extract.call_count == 2
    remaining = await pool.fetchval(
        "select count(*) from public.emails where user_id = $1 and extracted_at is null", user_id
    )
    assert remaining == 1


@pytest.mark.asyncio
async def test_extract_user_carries_remaining_budget_into_next_table(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    for i in range(2):
        await pool.execute(
            "insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text) "
            "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
            user_id, f"msg-budget-{i}", f"person{i}@example.com", "Hi", "Could you review the draft and send feedback before Thursday?",
        )
    for i in range(2):
        await pool.execute(
            "insert into public.chat_messages (user_id, account_id, provider_chat_id, provider_message_id, from_user, content) "
            "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
            user_id, "chat-budget", f"chat-msg-budget-{i}", f"Chatter{i}",
            "Please take over the release notes for this sprint.",
        )

    result = {"people": [], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result) as mock_extract:
        # limit=3: exactly enough to process both emails (2) plus one chat
        # message (1), proving the remaining budget (1, not the original 3)
        # is what actually gets passed into the chat scan function - a
        # regression that re-passed the frozen limit=3 into the chat scan
        # would process both chat messages instead of just one.
        await extract_user(pool, user_id, limit=3)

    assert mock_extract.call_count == 3
    emails_pending = await pool.fetchval(
        "select count(*) from public.emails where user_id = $1 and extracted_at is null", user_id
    )
    chats_pending = await pool.fetchval(
        "select count(*) from public.chat_messages where user_id = $1 and extracted_at is null", user_id
    )
    assert emails_pending == 0
    assert chats_pending == 1


@pytest.mark.asyncio
async def test_extract_user_unbounded_processes_everything(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    for i in range(3):
        await pool.execute(
            "insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text) "
            "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
            user_id, f"msg-unbounded-{i}", f"person{i}@example.com", "Hi", "Could you review the draft and send feedback before Thursday?",
        )

    result = {"people": [], "action_items": []}
    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await extract_user(pool, user_id, limit=None)

    remaining = await pool.fetchval(
        "select count(*) from public.emails where user_id = $1 and extracted_at is null", user_id
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_extract_user_isolates_per_item_failures(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        "insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
        user_id, "msg-fail", "fail@example.com", "Hi", "Could you review the draft and send feedback before Thursday?",
    )
    await pool.execute(
        "insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text) "
        "values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), $2, $3, $4, $5)",
        user_id, "msg-ok", "ok@example.com", "Hi", "Could you review the draft and send feedback before Thursday?",
    )

    async def fake_extract(content, participants):
        if participants[0]["email"] == "fail@example.com":
            raise RuntimeError("boom")
        return {"people": [], "action_items": []}

    with patch("app.services.ai_extraction.openai_client.extract", side_effect=fake_extract):
        await extract_user(pool, user_id, limit=10)

    failed_row = await pool.fetchrow(
        "select extracted_at from public.emails where user_id = $1 and provider_message_id = $2", user_id, "msg-fail"
    )
    ok_row = await pool.fetchrow(
        "select extracted_at from public.emails where user_id = $1 and provider_message_id = $2", user_id, "msg-ok"
    )
    assert failed_row["extracted_at"] is None
    assert ok_row["extracted_at"] is not None


@pytest.mark.asyncio
async def test_permanently_failing_item_stops_being_retried(pool, test_auth_user):
    """A message that can never be extracted must drain out of the queue.

    Failures are swallowed without recording anything, so the row keeps
    extracted_at null and is re-fetched on every run. Because the pending query
    orders oldest-first with a limit, a handful of such rows sit permanently at
    the head of the queue and newer mail is never reached - and under a daily
    token budget they consume the whole quota on messages that cannot succeed.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'poison-1', 'sender@example.com', 'Subject', 'Could you review the draft and send feedback before Thursday?')
        """,
        user_id,
    )

    with patch(
        "app.services.ai_extraction.openai_client.extract",
        side_effect=RuntimeError("model always fails on this one"),
    ) as mock_extract:
        for _ in range(5):
            await extract_user(pool, user_id, 10)

    # After a bounded number of attempts the item must no longer be dispatched.
    assert mock_extract.call_count <= 3, (
        f"item was retried {mock_extract.call_count} times; it should stop after 3"
    )

    row = await pool.fetchrow(
        "select extraction_attempts, extraction_error, extracted_at "
        "from public.emails where provider_message_id = 'poison-1'"
    )
    assert row["extraction_attempts"] == 3, "attempts were not recorded"
    assert "model always fails" in row["extraction_error"], "failure reason was not kept"
    # extracted_at stays null on purpose: the message genuinely was never
    # extracted. Stamping it would make "extracted" untrue and would hide the
    # row from any future re-extraction pass. The attempts cap, not
    # extracted_at, is what removes it from the queue.
    assert row["extracted_at"] is None


@pytest.mark.asyncio
async def test_failing_items_do_not_starve_newer_mail(pool, test_auth_user):
    """Failures must not permanently occupy the batch window.

    The pending query is `order by received_at asc ... limit N`. With N failing
    messages older than everything else, they fill the window on every run and
    newer mail is never reached. Seeding exactly `limit` poison messages ahead
    of one healthy message reproduces that; the healthy one is only processed
    once failures stop being re-queued.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text, received_at)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'poison-a', 's@example.com', 'Bad', 'Could you review the draft and send feedback before Thursday?', now() - interval '5 days'),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'poison-b', 's@example.com', 'Bad', 'Could you review the draft and send feedback before Thursday?', now() - interval '4 days'),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'poison-c', 's@example.com', 'Bad', 'Could you review the draft and send feedback before Thursday?', now() - interval '3 days'),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'healthy-1', 'other@example.com', 'Good', 'Could you review the draft and send feedback before Thursday?', now() - interval '1 day')
        """,
        user_id,
    )

    calls = {"n": 0}

    async def flaky(content, participants):
        calls["n"] += 1
        if "Bad" in content:
            raise RuntimeError("cannot extract this one")
        return {"people": [], "action_items": []}

    # limit=3 matches the three poison messages, so they exactly fill the window.
    with patch("app.services.ai_extraction.openai_client.extract", side_effect=flaky):
        for _ in range(6):
            await extract_user(pool, user_id, 3)

    healthy_extracted = await pool.fetchval(
        "select extracted_at is not null from public.emails where provider_message_id = 'healthy-1'"
    )
    assert healthy_extracted is True, "healthy message never processed - queue was starved"


@pytest.mark.asyncio
async def test_long_message_body_is_truncated_before_the_llm_sees_it(pool, test_auth_user):
    """Message bodies must be bounded before they reach the model.

    Nothing caps body length today, so a newsletter or a long reply chain is
    sent whole. On a free tier the binding limit is tokens per day, not
    requests per day, so one unbounded message can consume a disproportionate
    share of the daily budget - and the action items are almost always near the
    top of a message anyway.
    """
    from app.core.config import settings

    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    huge_body = "word " * 20000  # ~100k characters
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'long-1', 'sender@example.com', 'Subject', $2)
        """,
        user_id,
        huge_body,
    )

    captured = {}

    async def capture(content, participants):
        captured["content"] = content
        return {"people": [], "action_items": []}

    with patch("app.services.ai_extraction.openai_client.extract", side_effect=capture):
        await extract_user(pool, user_id, 10)

    assert "content" in captured, "extraction never ran"
    # Allow headroom for the subject line and separators on top of the body cap.
    assert len(captured["content"]) <= settings.llm_max_body_chars + 500, (
        f"sent {len(captured['content'])} chars to the model; "
        f"body cap is {settings.llm_max_body_chars}"
    )


@pytest.mark.asyncio
async def test_truncation_keeps_the_start_of_the_message(pool, test_auth_user):
    """Truncate from the end: commitments cluster near the top of a message."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'long-2', 'sender@example.com', 'Subject', $2)
        """,
        user_id,
        "IMPORTANT FIRST LINE. " + ("filler " * 20000),
    )

    captured = {}

    async def capture(content, participants):
        captured["content"] = content
        return {"people": [], "action_items": []}

    with patch("app.services.ai_extraction.openai_client.extract", side_effect=capture):
        await extract_user(pool, user_id, 10)

    assert "IMPORTANT FIRST LINE." in captured["content"]


@pytest.mark.asyncio
async def test_action_item_category_is_persisted(pool, test_auth_user):
    """The category the model assigns must reach the database.

    It drives the dashboard's task breakdown. It is extracted and stored now,
    rather than when that chart is built, because adding it later would mean
    re-extracting every stored message to backfill the column - a full day of
    token quota on a free tier.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    result = {
        "people": [{"ref": "p0", "notes": "Runs the launch."}],
        "action_items": [
            {
                "text": "Book the review",
                "direction": "mine",
                "due_date": None,
                "participant_ref": "p0",
                "category": "meeting",
            }
        ],
    }

    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "content",
            [("dana@example.com", "Dana")], own_email=email,
        )

    stored = await pool.fetchval(
        "select category from public.action_items where user_id = $1", user_id
    )
    assert stored == "meeting"


@pytest.mark.asyncio
async def test_action_item_category_defaults_when_absent(pool, test_auth_user):
    """A category the database will accept must always be written."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    result = {
        "people": [],
        "action_items": [
            {"text": "Unlabelled task", "direction": "theirs", "due_date": None,
             "participant_ref": None}
        ],
    }

    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "content",
            [("eve@example.com", "Eve")], own_email=email,
        )

    stored = await pool.fetchval(
        "select category from public.action_items where user_id = $1", user_id
    )
    assert stored == "other"


@pytest.mark.asyncio
async def test_bulk_mail_never_reaches_the_model(pool, test_auth_user):
    """Automated mail must be filtered out before an LLM call is made.

    Most inbox volume is notifications and newsletters that yield no action
    items. On a free tier the binding limit is tokens per day, so spending them
    on mail that cannot produce a task is the difference between a mailbox that
    fits in the budget and one that does not.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text, received_at)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'bulk-1', 'noreply@example.com', 'Your weekly digest',
                'Here is everything that happened this week, at length.', now() - interval '2 days'),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'real-1', 'colleague@example.com', 'Re: Thursday',
                'Can you send the deck before Thursday please?', now() - interval '1 day')
        """,
        user_id,
    )

    seen = []

    async def capture(content, participants):
        seen.append(content)
        return {"people": [], "action_items": []}

    with patch("app.services.ai_extraction.openai_client.extract", side_effect=capture):
        await extract_user(pool, user_id, 10)

    assert len(seen) == 1, f"expected only the real message to be sent, got {len(seen)}"
    assert "deck before Thursday" in seen[0]


@pytest.mark.asyncio
async def test_filtered_mail_is_not_re_queued_every_run(pool, test_auth_user):
    """A skipped message must leave the queue, with the rule that skipped it.

    Skipping without recording anything would leave extracted_at null, so the
    row would be re-examined on every run forever - cheap, but it would also sit
    at the head of the oldest-first window and crowd out real mail.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'bulk-2', 'notifications@example.com', 'Alert',
                'A long enough body to clear the minimum length rule easily.')
        """,
        user_id,
    )

    with patch("app.services.ai_extraction.openai_client.extract") as mock_extract:
        await extract_user(pool, user_id, 10)
        await extract_user(pool, user_id, 10)

    mock_extract.assert_not_called()

    row = await pool.fetchrow(
        "select extracted_at, extraction_error from public.emails where provider_message_id = 'bulk-2'"
    )
    assert row["extracted_at"] is not None, "skipped mail still pending, will re-queue forever"
    assert "sender" in (row["extraction_error"] or "").lower(), "skip reason not recorded"


@pytest.mark.asyncio
async def test_trivial_chat_messages_never_reach_the_model(pool, test_auth_user):
    """"ok" and "thanks" dominate chat volume and contain nothing to extract.

    Chat is the noisiest source per message: a one-word acknowledgement costs
    the same prompt preamble as a real request.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.chat_messages
            (user_id, account_id, provider_chat_id, provider_message_id, from_user, content, sent_at)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'c1', 'm1', 'Dana', 'ok', now() - interval '3 days'),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'c1', 'm2', 'Dana', 'thanks!', now() - interval '2 days'),
               ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'c1', 'm3', 'Dana',
                'Can you own the migration rollback plan before we ship?',
                now() - interval '1 day')
        """,
        user_id,
    )

    seen = []

    async def capture(content, participants):
        seen.append(content)
        return {"people": [], "action_items": []}

    with patch("app.services.ai_extraction.openai_client.extract", side_effect=capture):
        await extract_user(pool, user_id, 10)

    assert len(seen) == 1, f"expected 1 substantive message, got {len(seen)}"
    assert "migration rollback" in seen[0]


@pytest.mark.asyncio
async def test_the_sender_becomes_a_contact_even_when_the_model_names_nobody(pool, test_auth_user):
    """Existence of a person is a fact from the headers, not a model opinion.

    Contacts were only created from the model's `people` array. A small model
    routinely returns that empty, so on real mail 127 distinct senders produced
    exactly one contact - and action items referencing those people landed with
    contact_id null, which also makes them unschedulable.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    result = {
        "people": [],
        "action_items": [
            {
                "text": "Send the deck",
                "direction": "mine",
                "due_date": None,
                "participant_ref": "p0",
                "category": "work",
            }
        ],
    }

    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "content",
            [("dana@example.com", "Dana")], own_email=email,
        )

    contact = await ContactsRepository(pool).get_by_email(user_id, "dana@example.com")
    assert contact is not None, "the sender should exist as a contact"
    assert contact["display_name"] == "Dana"

    item = await pool.fetchrow(
        "select contact_id from public.action_items where user_id = $1", user_id
    )
    assert item["contact_id"] == contact["id"], "the item should be attributed to them"


@pytest.mark.asyncio
async def test_model_notes_are_still_applied_when_it_does_describe_someone(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    result = {
        "people": [{"ref": "p0", "notes": "Runs the Northwind account"}],
        "action_items": [],
    }

    with patch("app.services.ai_extraction.openai_client.extract", return_value=result):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "content",
            [("dana@example.com", "Dana")], own_email=email,
        )

    contact = await ContactsRepository(pool).get_by_email(user_id, "dana@example.com")
    assert contact["notes"] == "Runs the Northwind account"


@pytest.mark.asyncio
async def test_a_later_message_without_notes_does_not_erase_earlier_ones(pool, test_auth_user):
    """Now that every participant is upserted on every message, the coalesce in
    the repository is load-bearing rather than theoretical.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    with patch(
        "app.services.ai_extraction.openai_client.extract",
        return_value={"people": [{"ref": "p0", "notes": "Knows the roadmap"}], "action_items": []},
    ):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "first",
            [("dana@example.com", "Dana")], own_email=email,
        )

    with patch(
        "app.services.ai_extraction.openai_client.extract",
        return_value={"people": [], "action_items": []},
    ):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "second",
            [("dana@example.com", "Dana")], own_email=email,
        )

    contact = await ContactsRepository(pool).get_by_email(user_id, "dana@example.com")
    assert contact["notes"] == "Knows the roadmap"


@pytest.mark.asyncio
async def test_the_user_themselves_never_becomes_a_contact(pool, test_auth_user):
    """Mail the user sent themselves must not create a contact for them."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    with patch(
        "app.services.ai_extraction.openai_client.extract",
        return_value={"people": [], "action_items": []},
    ):
        await _process_item(
            pool, user_id, "email", uuid.uuid4(), "content",
            [(email, "Me")], own_email=email,
        )

    assert await ContactsRepository(pool).count(user_id) == 0
