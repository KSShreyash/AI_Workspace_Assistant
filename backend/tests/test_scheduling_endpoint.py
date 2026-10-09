import uuid
from datetime import datetime, timedelta, timezone

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from app.core.deps import CurrentUser, get_current_user
from app.core.security import encrypt_token
from app.main import app
from app.repositories.action_items import ActionItemsRepository
from app.repositories.contacts import ContactsRepository
from tests.helpers import connect_account, get_connected_account
from app.repositories.profiles import ProfilesRepository

GRAPH_EVENTS_URL = "https://graph.microsoft.com/v1.0/me/events"


def _override_auth(user_id, email):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=user_id, email=email)


async def _seed_connected_user_with_item(pool, user_id, email, with_contact=True):
    await ProfilesRepository(pool).upsert(user_id, email)
    await connect_account(pool, 
        user_id=user_id,
        encrypted_access_token=encrypt_token("valid-access"),
        encrypted_refresh_token=encrypt_token("valid-refresh"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["Calendars.ReadWrite"],
    )
    contact_id = None
    if with_contact:
        contact_id = await ContactsRepository(pool).upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await ActionItemsRepository(pool).insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    return item_row["id"]


@pytest.mark.asyncio
async def test_schedule_suggestions_returns_slots(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_connected_user_with_item(pool, user_id, email)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/action-items/{item_id}/schedule-suggestions")

        assert response.status_code == 200
        assert len(response.json()) > 0
        assert "start" in response.json()[0]
        assert "end" in response.json()[0]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_schedule_suggestions_404_when_no_contact(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_connected_user_with_item(pool, user_id, email, with_contact=False)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/action-items/{item_id}/schedule-suggestions")

        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_schedule_suggestions_404_for_foreign_item(pool, test_auth_user, test_auth_user_2):
    user_id, email = test_auth_user
    other_user_id, other_email = test_auth_user_2
    other_item_id = await _seed_connected_user_with_item(pool, other_user_id, other_email)
    await ProfilesRepository(pool).upsert(user_id, email)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/action-items/{other_item_id}/schedule-suggestions")

        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@respx.mock
async def test_schedule_creates_meeting_end_to_end(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_connected_user_with_item(pool, user_id, email)
    respx.post(GRAPH_EVENTS_URL).mock(
        return_value=Response(201, json={
            "id": "graph-evt-e2e", "subject": "Call Gina",
            "attendees": [{"emailAddress": {"address": "gina@example.com", "name": "Gina"}}],
            "isOnlineMeeting": True, "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/e2e"},
        })
    )
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/action-items/{item_id}/schedule",
                json={
                    "start": "2026-07-20T14:00:00Z",
                    "end": "2026-07-20T14:30:00Z",
                    "online_meeting": True,
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["scheduled_calendar_event_id"] is not None
        assert body["scheduled_start_time"] == "2026-07-20T14:00:00+00:00"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@respx.mock
async def test_schedule_409_when_already_scheduled(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_connected_user_with_item(pool, user_id, email)
    respx.post(GRAPH_EVENTS_URL).mock(
        return_value=Response(201, json={"id": "graph-evt-dupe", "subject": "Call Gina", "isOnlineMeeting": False})
    )
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                f"/api/action-items/{item_id}/schedule",
                json={"start": "2026-07-20T14:00:00Z", "end": "2026-07-20T14:30:00Z", "online_meeting": False},
            )
            assert first.status_code == 200

            second = await client.post(
                f"/api/action-items/{item_id}/schedule",
                json={"start": "2026-07-21T14:00:00Z", "end": "2026-07-21T14:30:00Z", "online_meeting": False},
            )

        assert second.status_code == 409
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_schedule_409_when_needs_reauth(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    contact_id = await ContactsRepository(pool).upsert_by_email(user_id, "gina@example.com", "Gina", None)
    await ActionItemsRepository(pool).insert(
        user_id=user_id, contact_id=contact_id, text="Call Gina", direction="mine",
        due_date=None, source_type="email", source_id=uuid.uuid4(),
    )
    item_row = await pool.fetchrow("select id from public.action_items where user_id = $1", user_id)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/action-items/{item_row['id']}/schedule",
                json={"start": "2026-07-20T14:00:00Z", "end": "2026-07-20T14:30:00Z", "online_meeting": False},
            )

        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_schedule_rejects_malformed_body(pool, test_auth_user):
    user_id, email = test_auth_user
    item_id = await _seed_connected_user_with_item(pool, user_id, email)
    _override_auth(user_id, email)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/action-items/{item_id}/schedule",
                json={"start": "not-a-date", "end": "2026-07-20T14:30:00Z", "online_meeting": False},
            )

        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
