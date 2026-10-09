"""One sync per user at a time.

Three things can start a sync - the cron, the background task on sign-in, and
the "Sync now" button - and extraction has no locking of its own, so two
overlapping runs could read the same un-extracted rows and spend the LLM budget
twice. Several mailboxes per user widens that window.

A Postgres advisory lock rather than a lock row: it is held by the session, so
it cannot be left behind by a crashed process the way a row would.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

logger = logging.getLogger(__name__)


@asynccontextmanager
async def user_sync_lock(
    pool: asyncpg.Pool, user_id: uuid.UUID
) -> AsyncIterator[bool]:
    """Try to take this user's sync lock. Yields whether it was acquired.

    Try-lock, not lock: blocking would stack cron workers behind one slow sync
    until the pool was exhausted. A caller that gets False should do nothing and
    say so - the work is already being done.

    The lock lives on one dedicated connection for its whole lifetime. A
    session-level advisory lock is released when its session ends, and pooled
    connections outlive the request that borrowed them, so releasing explicitly
    is not optional - a leaked lock would disable syncing for that user until
    the process restarted.
    """
    async with pool.acquire() as conn:
        # Postgres derives the key, so there is no 128-bit-uuid-into-64-bit-int
        # conversion to get wrong on this side. The 'sync:' prefix namespaces it
        # against any other advisory lock the app might take later. A hash
        # collision between two users is a 1-in-2^64 skipped sync, not a
        # correctness problem.
        acquired = await conn.fetchval(
            "select pg_try_advisory_lock(hashtextextended('sync:' || $1::uuid::text, 0))",
            user_id,
        )
        if not acquired:
            logger.info("Sync already running for user %s; skipping", user_id)
            yield False
            return

        try:
            yield True
        finally:
            await conn.fetchval(
                "select pg_advisory_unlock(hashtextextended('sync:' || $1::uuid::text, 0))",
                user_id,
            )
