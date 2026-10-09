"""One sync per user at a time.

Three things can start a sync: the 15-minute cron, the background task on
sign-in, and the "Sync now" button. Nothing stopped them overlapping, and
extraction has no locking of its own - so two concurrent runs could both read
the same un-extracted rows and spend the LLM budget twice on them. Several
mailboxes per user multiplies the window, which is why this lands with job 2.

A Postgres advisory lock is the right tool here: it is held by the session, so
it cannot outlive a crashed process the way a lock row would.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.profiles import ProfilesRepository
from app.services import graph_sync
from app.services.sync_lock import user_sync_lock


@pytest.mark.asyncio
async def test_a_second_concurrent_sync_for_the_same_user_is_skipped(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    release = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def slow_sync(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    with patch("app.services.graph_sync._sync_account_unlocked", slow_sync), patch(
        "app.services.graph_sync.extract_user", AsyncMock()
    ):
        first = asyncio.create_task(graph_sync.sync_user(pool, user_id))
        await started.wait()

        # The first run is mid-flight and holding the lock.
        second = await graph_sync.sync_user(pool, user_id)

        release.set()
        await first

    assert second is False, "the overlapping run should report that it did nothing"
    assert calls == 1


@pytest.mark.asyncio
async def test_two_different_users_sync_at_the_same_time(pool, test_auth_user, test_auth_user_2):
    """The lock is per user. One shared lock would serialise the whole cron."""
    user_id, email = test_auth_user
    other_id, other_email = test_auth_user_2
    await ProfilesRepository(pool).upsert(user_id, email)
    await ProfilesRepository(pool).upsert(other_id, other_email)

    release = asyncio.Event()
    both_started = asyncio.Event()
    running = 0

    async def slow_sync(*_args, **_kwargs):
        nonlocal running
        running += 1
        if running == 2:
            both_started.set()
        await release.wait()

    with patch("app.services.graph_sync._sync_account_unlocked", slow_sync), patch(
        "app.services.graph_sync.extract_user", AsyncMock()
    ):
        tasks = [
            asyncio.create_task(graph_sync.sync_user(pool, user_id)),
            asyncio.create_task(graph_sync.sync_user(pool, other_id)),
        ]
        try:
            await asyncio.wait_for(both_started.wait(), timeout=5)
        finally:
            release.set()
            results = await asyncio.gather(*tasks)

    assert results == [True, True]


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_sync_finishes(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    with patch("app.services.graph_sync._sync_account_unlocked", AsyncMock()), patch(
        "app.services.graph_sync.extract_user", AsyncMock()
    ):
        assert await graph_sync.sync_user(pool, user_id) is True
        # A leaked lock would silently disable syncing for this user forever,
        # because pooled connections outlive the request that borrowed them.
        assert await graph_sync.sync_user(pool, user_id) is True


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_sync_raises(pool, test_auth_user):
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    with patch(
        "app.services.graph_sync._sync_account_unlocked",
        AsyncMock(side_effect=RuntimeError("boom")),
    ), patch("app.services.graph_sync.extract_user", AsyncMock()):
        with pytest.raises(RuntimeError):
            await graph_sync.sync_user(pool, user_id)

    with patch("app.services.graph_sync._sync_account_unlocked", AsyncMock()), patch(
        "app.services.graph_sync.extract_user", AsyncMock()
    ):
        assert await graph_sync.sync_user(pool, user_id) is True


@pytest.mark.asyncio
async def test_a_single_account_sync_also_takes_the_users_lock(pool, test_auth_user):
    """The cron drives sync_account directly, so locking only sync_user would
    leave the cron free to overlap a user pressing Sync now.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    account_id = (await graph_sync.ConnectedAccountsRepository(pool).get_primary(user_id))["id"]

    async with user_sync_lock(pool, user_id) as acquired:
        assert acquired is True
        with patch("app.services.graph_sync._sync_account_unlocked", AsyncMock()) as inner:
            result = await graph_sync.sync_account(pool, user_id, account_id)

    assert result is False
    inner.assert_not_awaited()


@pytest.mark.asyncio
async def test_extraction_does_not_run_for_a_skipped_sync(pool, test_auth_user):
    """Extraction is the expensive half. A skipped run must skip it too, or the
    lock buys nothing on a token budget.
    """
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)

    async with user_sync_lock(pool, user_id):
        with patch("app.services.graph_sync.extract_user", AsyncMock()) as extract:
            await graph_sync.sync_user(pool, user_id)

    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_lock_context_reports_failure_rather_than_blocking(pool, test_auth_user):
    """try-lock, not lock. Blocking would pile cron workers up behind a slow
    sync until the pool is exhausted.
    """
    user_id, _ = test_auth_user

    async with user_sync_lock(pool, user_id) as outer:
        assert outer is True
        async with user_sync_lock(pool, user_id) as inner:
            assert inner is False


@pytest.mark.asyncio
async def test_locks_for_distinct_users_do_not_interfere(pool):
    a, b = uuid.uuid4(), uuid.uuid4()
    async with user_sync_lock(pool, a) as first:
        async with user_sync_lock(pool, b) as second:
            assert first is True
            assert second is True
