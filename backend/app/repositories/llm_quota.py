from datetime import datetime, timezone

import asyncpg

from app.core.config import settings


def current_provider() -> str:
    """Identity of the provider whose quota we are tracking.

    Derived from the configured endpoint rather than a hardcoded name, so
    repointing llm_base_url at a different provider does not inherit the
    previous one's exhaustion window.
    """
    return settings.llm_base_url


class LlmQuotaRepository:
    """Tracks when the LLM provider's quota is spent.

    Keyed by provider, not by user: Groq applies its limits organisation-wide,
    so one user exhausting the cap means every user has.

    `provider` defaults to the configured endpoint on every method so callers
    cannot drift from the key the service actually writes under - a mismatch
    there fails silently, leaving the quota check permanently false.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def mark_exhausted(self, until: datetime, provider: str | None = None) -> None:
        await self._pool.execute(
            """
            insert into public.llm_quota_state (provider, exhausted_until, updated_at)
            values ($1, $2, now())
            on conflict (provider) do update
            set exhausted_until = excluded.exhausted_until,
                updated_at = now()
            """,
            provider or current_provider(),
            until,
        )

    async def is_exhausted(self, provider: str | None = None) -> bool:
        until = await self._pool.fetchval(
            "select exhausted_until from public.llm_quota_state where provider = $1",
            provider or current_provider(),
        )
        # A window that has already passed is not exhaustion. The row is left in
        # place as a record of when it last happened.
        return until is not None and until > datetime.now(timezone.utc)

    async def clear(self, provider: str | None = None) -> None:
        await self._pool.execute(
            "update public.llm_quota_state set exhausted_until = null, updated_at = now() "
            "where provider = $1",
            provider or current_provider(),
        )
