"""Stop calling the provider once its quota is spent.

The free tier caps tokens per day, and a daily exhaustion is measured in hours.
Retrying into a closed door burns the request allowance too, and on a cron that
fires every fifteen minutes it does so all night.

Groq applies its limits organisation-wide rather than per user, so the state is
global, keyed by provider, not per profile.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import RateLimitError

from app.repositories.llm_quota import LlmQuotaRepository
from app.repositories.profiles import ProfilesRepository
from app.services.ai_extraction import extract_user


def _rate_limited(retry_after: str | None = None) -> RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request, headers=headers, json={"error": {}})
    return RateLimitError("rate limited", response=response, body=None)


@pytest.mark.asyncio
async def test_quota_state_round_trips(pool):
    repo = LlmQuotaRepository(pool)
    until = datetime.now(timezone.utc) + timedelta(hours=2)

    await repo.mark_exhausted(until)
    assert await repo.is_exhausted() is True

    await repo.clear()
    assert await repo.is_exhausted() is False


@pytest.mark.asyncio
async def test_a_window_that_has_passed_is_not_exhausted(pool):
    repo = LlmQuotaRepository(pool)
    await repo.mark_exhausted(datetime.now(timezone.utc) - timedelta(minutes=1))

    assert await repo.is_exhausted() is False


@pytest.mark.asyncio
async def test_extraction_does_not_call_the_model_while_quota_is_exhausted(
    pool, test_auth_user
):
    """The whole point: no request at all, not a request that fails."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'quota-1', 'colleague@example.com', 'Re: plan',
                'Could you review the draft and send feedback before Thursday?')
        """,
        user_id,
    )
    await LlmQuotaRepository(pool).mark_exhausted(
        datetime.now(timezone.utc) + timedelta(hours=1)
    )

    try:
        with patch("app.services.ai_extraction.openai_client.extract") as mock_extract:
            await extract_user(pool, user_id, 10)
        mock_extract.assert_not_called()
    finally:
        await LlmQuotaRepository(pool).clear()


@pytest.mark.asyncio
async def test_a_message_held_back_by_quota_stays_pending(pool, test_auth_user):
    """Quota is transient - the message must be retried later, not abandoned."""
    user_id, email = test_auth_user
    await ProfilesRepository(pool).upsert(user_id, email)
    await pool.execute(
        """
        insert into public.emails (user_id, account_id, provider_message_id, from_address, subject, body_text)
        values ($1, (select id from public.connected_accounts where user_id = $1 and is_primary), 'quota-2', 'colleague@example.com', 'Re: plan',
                'Could you review the draft and send feedback before Thursday?')
        """,
        user_id,
    )
    await LlmQuotaRepository(pool).mark_exhausted(
        datetime.now(timezone.utc) + timedelta(hours=1)
    )

    try:
        with patch("app.services.ai_extraction.openai_client.extract"):
            await extract_user(pool, user_id, 10)

        row = await pool.fetchrow(
            "select extracted_at, extraction_attempts from public.emails "
            "where provider_message_id = 'quota-2'"
        )
        assert row["extracted_at"] is None, "held-back message was marked done"
        assert row["extraction_attempts"] == 0, "held-back message burned a retry"
    finally:
        await LlmQuotaRepository(pool).clear()


@pytest.mark.asyncio
async def test_rate_limit_response_records_the_providers_retry_after():
    """Honour the provider's own backoff instead of guessing."""
    from app.services import openai_client

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=_rate_limited("1800"))

    with patch("app.services.openai_client._client", new=mock_client):
        with pytest.raises(openai_client.QuotaExhausted) as exc_info:
            await openai_client.extract("content", [])

    # 1800 seconds, not the fixed 5 the old code slept for.
    assert exc_info.value.retry_after_seconds == 1800


@pytest.mark.asyncio
async def test_rate_limit_without_a_retry_after_header_still_backs_off():
    """Providers do not always send the header; fall back to a sane default."""
    from app.services import openai_client

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=_rate_limited(None))

    with patch("app.services.openai_client._client", new=mock_client):
        with pytest.raises(openai_client.QuotaExhausted) as exc_info:
            await openai_client.extract("content", [])

    assert exc_info.value.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_retry_after_as_an_http_date_does_not_crash():
    """RFC 9110 permits an HTTP-date; float() on it raises ValueError."""
    from app.services import openai_client

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=_rate_limited("Wed, 21 Oct 2026 07:28:00 GMT")
    )

    with patch("app.services.openai_client._client", new=mock_client):
        with pytest.raises(openai_client.QuotaExhausted) as exc_info:
            await openai_client.extract("content", [])

    assert exc_info.value.retry_after_seconds > 0
