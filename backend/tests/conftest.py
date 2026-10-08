import uuid

import pytest_asyncio

from app.db.session import close_pool, get_pool

# Supabase's auth.users requires these for a row to look like a real signup.
# Only `id` is NOT NULL without a default, but GoTrue always sets these two and
# some queries filter on them, so mirror what a real signup produces.
_INSTANCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_AUD = "authenticated"
_ROLE = "authenticated"


@pytest_asyncio.fixture
async def pool():
    p = await get_pool()
    yield p
    await close_pool()


async def _create_auth_user(p) -> tuple[uuid.UUID, str]:
    """Insert directly into auth.users.

    Deliberately not the GoTrue admin API. That required a running Auth service
    and a network round trip per test, which made the suite non-hermetic and
    tied it to whichever Supabase instance the environment happened to point at.
    profiles.id has a foreign key to auth.users(id), so the row has to exist --
    but it does not have to be created by GoTrue.
    """
    user_id = uuid.uuid4()
    email = f"test-{user_id}@example.com"
    # Only columns that exist on BOTH the minimal auth schema shipped in the
    # supabase/postgres image and the fuller one GoTrue migrates it to. A local
    # stack started without the auth container has the minimal one, which has
    # confirmed_at but not email_confirmed_at.
    await p.execute(
        """
        insert into auth.users (id, instance_id, aud, role, email)
        values ($1, $2, $3, $4, $5)
        """,
        user_id,
        _INSTANCE_ID,
        _AUD,
        _ROLE,
        email,
    )
    return user_id, email


async def _delete_auth_user(p, user_id: uuid.UUID) -> None:
    # public.profiles cascades from auth.users, and everything else cascades
    # from profiles, so this one delete cleans up the whole tree.
    await p.execute("delete from auth.users where id = $1", user_id)


@pytest_asyncio.fixture
async def test_auth_user(pool):
    user_id, email = await _create_auth_user(pool)
    yield user_id, email
    await _delete_auth_user(pool, user_id)


@pytest_asyncio.fixture
async def test_auth_user_2(pool):
    user_id, email = await _create_auth_user(pool)
    yield user_id, email
    await _delete_auth_user(pool, user_id)
