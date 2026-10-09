import uuid
from datetime import datetime, timezone

import pytest

from app.repositories.action_items import ActionItemsRepository
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.contacts import ContactsRepository
from app.repositories.profiles import ProfilesRepository
from tests.helpers import primary_account_id


@pytest.mark.asyncio
async def test_list_for_contact_returns_only_that_contacts_items(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_a = await contacts.upsert_by_email(user_id, "a@example.com", "A", None)
    contact_b = await contacts.upsert_by_email(user_id, "b@example.com", "B", None)

    await action_items.insert(
        user_id=user_id, contact_id=contact_a, text="For A", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=user_id, contact_id=contact_b, text="For B", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )

    rows = await action_items.list_for_contact(user_id, contact_a)
    assert len(rows) == 1
    assert rows[0]["text"] == "For A"


@pytest.mark.asyncio
async def test_list_for_user_filters_direction_and_excludes_done_by_default(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)

    await action_items.insert(
        user_id=user_id, contact_id=None, text="Mine open", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Theirs open", direction="theirs",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    to_complete = await pool.fetchrow(
        "select id from public.action_items where user_id = $1 and text = $2", user_id, "Mine open"
    )
    await action_items.update_status(user_id, to_complete["id"], "done")

    all_open = await action_items.list_for_user(user_id)
    assert {row["text"] for row in all_open} == {"Theirs open"}

    mine_including_done = await action_items.list_for_user(user_id, direction="mine", include_done=True)
    assert {row["text"] for row in mine_including_done} == {"Mine open"}

    everything = await action_items.list_for_user(user_id, include_done=True)
    assert {row["text"] for row in everything} == {"Mine open", "Theirs open"}


@pytest.mark.asyncio
async def test_list_for_user_embeds_contact_display_fields(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "dana@example.com", "Dana", None)

    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="With contact", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=user_id, contact_id=None, text="No contact", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )

    rows = await action_items.list_for_user(user_id)
    by_text = {row["text"]: row for row in rows}
    assert by_text["With contact"]["contact_display_name"] == "Dana"
    assert by_text["With contact"]["contact_email_address"] == "dana@example.com"
    assert by_text["No contact"]["contact_display_name"] is None


@pytest.mark.asyncio
async def test_update_status_sets_status_and_updated_at(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Toggle me", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    before = await pool.fetchrow(
        "select id, updated_at from public.action_items where user_id = $1", user_id
    )

    updated = await action_items.update_status(user_id, before["id"], "done")
    assert updated is not None
    assert updated["status"] == "done"
    assert updated["updated_at"] > before["updated_at"]


@pytest.mark.asyncio
async def test_update_status_returns_none_for_missing_item(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)

    result = await action_items.update_status(user_id, uuid.uuid4(), "done")
    assert result is None


@pytest.mark.asyncio
async def test_count_open_excludes_done_items(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Open one", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Will be done", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    to_complete = await pool.fetchrow(
        "select id from public.action_items where user_id = $1 and text = $2", user_id, "Will be done"
    )
    await action_items.update_status(user_id, to_complete["id"], "done")

    assert await action_items.count_open(user_id) == 1


@pytest.mark.asyncio
async def test_list_recent_sorted_by_created_at_desc_respects_limit(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)
    for text in ("first", "second", "third"):
        await action_items.insert(
            user_id=user_id, contact_id=None, text=text, direction="mine",
            due_date=None, source_type="email", source_id=uuid.uuid4(),
        )
    await pool.execute(
        "update public.action_items set created_at = now() - interval '2 hours' where text = 'first'"
    )
    await pool.execute(
        "update public.action_items set created_at = now() - interval '1 hour' where text = 'second'"
    )

    rows = await action_items.list_recent(user_id, limit=2)
    assert [row["text"] for row in rows] == ["third", "second"]


@pytest.mark.asyncio
async def test_get_returns_item_joined_with_contact_and_schedule(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)

    row = await action_items.get(user_id, item_row["id"])

    assert row["text"] == "Call Gina"
    assert row["contact_id"] == contact_id
    assert row["contact_display_name"] == "Gina"
    assert row["contact_email_address"] == "gina@example.com"
    assert row["scheduled_calendar_event_id"] is None
    assert row["scheduled_start_time"] is None


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_or_foreign_item(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_user_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_user_id, other_email)
    action_items = ActionItemsRepository(pool)
    await action_items.insert(
        user_id=other_user_id, contact_id=None, text="Not yours", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    foreign_item = await pool.fetchrow("select id from public.action_items where user_id = $1", other_user_id)

    assert await action_items.get(user_id, uuid.uuid4()) is None
    assert await action_items.get(user_id, foreign_item["id"]) is None


@pytest.mark.asyncio
async def test_set_scheduled_calendar_event_id_returns_joined_row(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    calendar_events = CalendarEventsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    calendar_event_id = await calendar_events.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-new", subject="Call Gina", organizer=None,
        attendees=[], start_time=datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )

    updated = await action_items.set_scheduled_calendar_event_id(user_id, item_row["id"], calendar_event_id)

    assert updated["scheduled_calendar_event_id"] == calendar_event_id
    assert updated["scheduled_start_time"] == datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    assert updated["contact_display_name"] == "Gina"


@pytest.mark.asyncio
async def test_set_scheduled_calendar_event_id_returns_none_for_foreign_item(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_user_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_user_id, other_email)
    action_items = ActionItemsRepository(pool)
    calendar_events = CalendarEventsRepository(pool)
    await action_items.insert(
        user_id=other_user_id, contact_id=None, text="Not yours", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    foreign_item = await pool.fetchrow("select id from public.action_items where user_id = $1", other_user_id)
    calendar_event_id = await calendar_events.upsert(
        user_id=other_user_id,
        account_id=await primary_account_id(pool, other_user_id),
        provider_event_id="evt-x", subject=None, organizer=None,
        attendees=[], start_time=None, end_time=None,
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )

    result = await action_items.set_scheduled_calendar_event_id(user_id, foreign_item["id"], calendar_event_id)

    assert result is None


@pytest.mark.asyncio
async def test_deleting_scheduled_calendar_event_nulls_out_the_action_item_pointer(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    calendar_events = CalendarEventsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    calendar_event_id = await calendar_events.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-removed", subject="Call Gina", organizer=None,
        attendees=[], start_time=datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )
    await action_items.set_scheduled_calendar_event_id(user_id, item_row["id"], calendar_event_id)

    # Simulates the delta-sync "@removed" path: deleting the calendar event must not raise
    # a ForeignKeyViolationError now that the FK is ON DELETE SET NULL.
    await calendar_events.delete(
        account_id=await primary_account_id(pool, user_id), provider_event_id="evt-removed"
    )

    refreshed = await action_items.get(user_id, item_row["id"])
    assert refreshed["scheduled_calendar_event_id"] is None


@pytest.mark.asyncio
async def test_set_scheduled_calendar_event_id_does_not_overwrite_a_winning_concurrent_write(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    calendar_events = CalendarEventsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    first_event_id = await calendar_events.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-first", subject="Call Gina", organizer=None,
        attendees=[], start_time=datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )
    second_event_id = await calendar_events.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-second", subject="Call Gina", organizer=None,
        attendees=[], start_time=datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 20, 15, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )

    first_result = await action_items.set_scheduled_calendar_event_id(user_id, item_row["id"], first_event_id)
    second_result = await action_items.set_scheduled_calendar_event_id(user_id, item_row["id"], second_event_id)

    assert first_result["scheduled_calendar_event_id"] == first_event_id
    assert second_result is None
    unchanged = await pool.fetchrow(
        "select scheduled_calendar_event_id from public.action_items where id = $1", item_row["id"]
    )
    assert unchanged["scheduled_calendar_event_id"] == first_event_id


@pytest.mark.asyncio
async def test_search_ranks_more_term_occurrences_higher(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Discuss the roadmap once", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=user_id, contact_id=None,
        text="Roadmap roadmap roadmap - the roadmap is the main topic, always roadmap",
        direction="mine", due_date=None, source_type="email", source_id=uuid.uuid4(),
    )

    results = await action_items.search(user_id, "roadmap", 20)

    assert len(results) == 2
    assert "always roadmap" in results[0]["text"]
    assert "once" in results[1]["text"]


@pytest.mark.asyncio
async def test_search_embeds_contact_or_null(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Unique term alpha with contact",
        direction="mine", due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Unique term alpha without contact",
        direction="mine", due_date=None, source_type="email", source_id=uuid.uuid4(),
    )

    results = await action_items.search(user_id, "alpha", 20)

    by_text = {row["text"]: row for row in results}
    assert by_text["Unique term alpha with contact"]["contact_display_name"] == "Gina"
    assert by_text["Unique term alpha without contact"]["contact_id"] is None


@pytest.mark.asyncio
async def test_search_excludes_other_users_action_items(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_user_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_user_id, other_email)
    action_items = ActionItemsRepository(pool)
    await action_items.insert(
        user_id=user_id, contact_id=None, text="Findable mine item",
        direction="mine", due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    await action_items.insert(
        user_id=other_user_id, contact_id=None, text="Findable theirs item",
        direction="mine", due_date=None, source_type="email", source_id=uuid.uuid4(),
    )

    results = await action_items.search(user_id, "findable", 20)

    assert len(results) == 1
    assert results[0]["text"] == "Findable mine item"


@pytest.mark.asyncio
async def test_search_respects_limit(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    action_items = ActionItemsRepository(pool)
    for i in range(25):
        await action_items.insert(
            user_id=user_id, contact_id=None, text=f"Cappeditem number {i}",
            direction="mine", due_date=None, source_type="email", source_id=uuid.uuid4(),
        )

    results = await action_items.search(user_id, "cappeditem", 20)

    assert len(results) == 20
