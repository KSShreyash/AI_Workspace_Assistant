import uuid

import pytest_asyncio

from app.core.security import encrypt_token

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
    # A profile and one connected mailbox. Synced mail, calendar events and
    # chat all reference an account by foreign key, so a user who has any of
    # them necessarily has a mailbox - a fixture that omitted it would be
    # arranging a state the application cannot reach.
    await p.execute(
        "insert into public.profiles (id, email) values ($1, $2) on conflict (id) do nothing",
        user_id,
        email,
    )
    await p.execute(
        """
        insert into public.connected_accounts (
            user_id, provider, provider_account_id, email_address,
            encrypted_access_token, encrypted_refresh_token,
            access_token_expires_at, scopes, is_primary
        )
        values ($1, 'microsoft', 'fixture-account', $2, $3, $4,
                now() + interval '1 hour', '{}', true)
        on conflict (user_id, provider, provider_account_id) do nothing
        """,
        user_id,
        email,
        encrypt_token("fixture-access"),
        encrypt_token("fixture-refresh"),
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


@pytest_asyncio.fixture
async def no_accounts(pool, test_auth_user):
    """Start with no connected mailboxes.

    test_auth_user connects one, which is right for tests that need synced data
    to hang off something. Tests about the accounts themselves - how many there
    are, which is primary, what happens with none - need a known starting set.
    """
    user_id, _ = test_auth_user
    await pool.execute("delete from public.connected_accounts where user_id = $1", user_id)
    return user_id
