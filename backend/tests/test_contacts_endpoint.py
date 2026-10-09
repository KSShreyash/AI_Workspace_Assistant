import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.repositories.action_items import ActionItemsRepository
from app.repositories.calendar_events import CalendarEventsRepository
from app.repositories.contacts import ContactsRepository
from app.repositories.profiles import ProfilesRepository
from tests.helpers import primary_account_id


def _override_auth(user_id, email):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)


@pytest.mark.asyncio
async def test_list_contacts_returns_current_users_contacts(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await ContactsRepository(pool).upsert_by_email(user_id, "alice@example.com", "Alice", "notes")
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/contacts")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["display_name"] == "Alice"
        assert body[0]["open_action_item_count"] == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_contacts_requires_authentication():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/contacts")

    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_contacts_search_filters_by_query_param(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    await contacts.upsert_by_email(user_id, "bob@example.com", "Bob Smith", None)
    await contacts.upsert_by_email(user_id, "carol@example.com", "Carol Jones", None)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/contacts", params={"q": "smith"})

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["display_name"] == "Bob Smith"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_contact_returns_detail(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contact_id = await ContactsRepository(pool).upsert_by_email(user_id, "dana@example.com", "Dana", "Some notes")
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/contacts/{contact_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(contact_id)
        assert body["notes"] == "Some notes"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_contact_404_for_missing_contact(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/contacts/{uuid.uuid4()}")

        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_contact_404_for_contact_owned_by_another_user(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_user_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_user_id, other_email)
    contact_id = await ContactsRepository(pool).upsert_by_email(other_user_id, "eve@example.com", "Eve", None)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/contacts/{contact_id}")

        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_contact_action_items_returns_items_for_that_contact(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "frank@example.com", "Frank", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Send the deck", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/contacts/{contact_id}/action-items")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["text"] == "Send the deck"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_contact_action_items_404_for_missing_contact(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/contacts/{uuid.uuid4()}/action-items")

        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_contact_action_items_includes_scheduled_fields(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contacts = ContactsRepository(pool)
    action_items = ActionItemsRepository(pool)
    calendar_events = CalendarEventsRepository(pool)
    contact_id = await contacts.upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await action_items.insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    calendar_event_id = await calendar_events.upsert(
        user_id=user_id,
        account_id=await primary_account_id(pool, user_id),
        provider_event_id="evt-2", subject="Call Gina", organizer=None,
        attendees=[], start_time=datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc),
        is_online_meeting=False, online_meeting_join_url=None, body_text=None,
    )
    await action_items.set_scheduled_calendar_event_id(user_id, item_row["id"], calendar_event_id)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/contacts/{contact_id}/action-items")

        body = response.json()
        assert body[0]["scheduled_calendar_event_id"] == str(calendar_event_id)
        assert body[0]["scheduled_start_time"] == "2026-07-20T14:00:00+00:00"
    finally:
        app.dependency_overrides.clear()
