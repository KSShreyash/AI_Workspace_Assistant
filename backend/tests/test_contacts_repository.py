import uuid

import pytest

from app.repositories.action_items import ActionItemsRepository
from app.repositories.contacts import ContactsRepository
from app.repositories.profiles import ProfilesRepository


@pytest.mark.asyncio
async def test_get_returns_contact_owned_by_user(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)
    contact_id = await repo.upsert_by_email(user_id, "alice@example.com", "Alice", "notes")

    found = await repo.get(user_id, contact_id)
    assert found is not None
    assert found["display_name"] == "Alice"


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_contact(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)

    found = await repo.get(user_id, uuid.uuid4())
    assert found is None


@pytest.mark.asyncio
async def test_list_for_user_sorted_by_updated_at_desc_with_open_action_item_count(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)

    older_id = await contacts.upsert_by_email(user_id, "older@example.com", "Older", None)
    await pool.execute(
        "update public.contacts set updated_at = now() - interval '1 day' where id = $1", older_id
    )
    newer_id = await contacts.upsert_by_email(user_id, "newer@example.com", "Newer", None)

    await action_items.insert(
        user_id=user_id, contact_id=newer_id, text="Follow up", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )

    rows = await contacts.list_for_user(user_id)
    assert [row["id"] for row in rows] == [newer_id, older_id]
    assert rows[0]["open_action_item_count"] == 1
    assert rows[1]["open_action_item_count"] == 0


@pytest.mark.asyncio
async def test_list_for_user_search_matches_name_or_email(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)
    await repo.upsert_by_email(user_id, "bob@example.com", "Bob Smith", None)
    await repo.upsert_by_email(user_id, "carol@example.com", "Carol Jones", None)

    by_name = await repo.list_for_user(user_id, search="smith")
    assert len(by_name) == 1
    assert by_name[0]["display_name"] == "Bob Smith"

    by_email = await repo.list_for_user(user_id, search="carol@")
    assert len(by_email) == 1
    assert by_email[0]["display_name"] == "Carol Jones"

    no_match = await repo.list_for_user(user_id, search="nobody")
    assert no_match == []


@pytest.mark.asyncio
async def test_list_recent_respects_limit(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)
    await repo.upsert_by_email(user_id, "one@example.com", "One", None)
    await repo.upsert_by_email(user_id, "two@example.com", "Two", None)
    await repo.upsert_by_email(user_id, "three@example.com", "Three", None)

    rows = await repo.list_recent(user_id, limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_search_ranks_more_term_occurrences_higher(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)
    await repo.upsert_by_email(
        user_id, "alice@example.com", "Alice Johnson", "Talked about budget planning once"
    )
    await repo.upsert_by_email(
        user_id, "bob@example.com", "Bob Smith",
        "Budget is the main topic, budget budget budget, always about the budget",
    )

    results = await repo.search(user_id, "budget", 20)

    assert len(results) == 2
    assert results[0]["display_name"] == "Bob Smith"
    assert results[1]["display_name"] == "Alice Johnson"


@pytest.mark.asyncio
async def test_search_matches_display_name_email_and_notes(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)
    await repo.upsert_by_email(user_id, "zephyr@example.com", "Regular Name", None)
    await repo.upsert_by_email(user_id, "regular@example.com", "Zephyr Name", None)
    await repo.upsert_by_email(user_id, "regular2@example.com", "Regular Name Two", "mentions zephyr here")

    results = await repo.search(user_id, "zephyr", 20)

    assert {row["email_address"] for row in results} == {
        "zephyr@example.com", "regular@example.com", "regular2@example.com",
    }


@pytest.mark.asyncio
async def test_search_excludes_other_users_contacts(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_user_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_user_id, other_email)
    repo = ContactsRepository(pool)
    await repo.upsert_by_email(user_id, "mine@example.com", "Findable Mine", None)
    await repo.upsert_by_email(other_user_id, "theirs@example.com", "Findable Theirs", None)

    results = await repo.search(user_id, "findable", 20)

    assert len(results) == 1
    assert results[0]["email_address"] == "mine@example.com"


@pytest.mark.asyncio
async def test_search_respects_limit(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)
    for i in range(25):
        await repo.upsert_by_email(user_id, f"person{i}@example.com", f"Capped Person {i}", None)

    results = await repo.search(user_id, "capped", 20)

    assert len(results) == 20


@pytest.mark.asyncio
async def test_upsert_by_email_preserves_existing_notes_when_given_none(pool, test_auth_user):
    """Re-upserting a known contact without notes must not erase what is stored.

    The AI extraction pass re-upserts every participant on every message it
    processes. If the model returns nothing for a contact, that call must not
    destroy the notes accumulated from earlier messages - there is no audit
    trail and no way to recover them.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)

    contact_id = await repo.upsert_by_email(
        user_id, "alice@example.com", "Alice", "Leads the payments team."
    )
    await repo.upsert_by_email(user_id, "alice@example.com", "Alice", None)

    found = await repo.get(user_id, contact_id)
    assert found["notes"] == "Leads the payments team."


@pytest.mark.asyncio
async def test_upsert_by_email_overwrites_notes_when_given_a_new_value(pool, test_auth_user):
    """Preserving notes on None must not block genuine updates."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)

    contact_id = await repo.upsert_by_email(user_id, "bob@example.com", "Bob", "Old summary.")
    await repo.upsert_by_email(user_id, "bob@example.com", "Bob", "New summary.")

    found = await repo.get(user_id, contact_id)
    assert found["notes"] == "New summary."


@pytest.mark.asyncio
async def test_upsert_by_display_name_preserves_existing_notes_when_given_none(pool, test_auth_user):
    """Same guarantee for contacts that have a name but no email address."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    repo = ContactsRepository(pool)

    contact_id = await repo.upsert_by_display_name(user_id, "Carol", "Mentioned in standup.")
    await repo.upsert_by_display_name(user_id, "Carol", None)

    found = await repo.get(user_id, contact_id)
    assert found["notes"] == "Mentioned in standup."
