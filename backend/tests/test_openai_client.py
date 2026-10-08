import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import RateLimitError

from app.core.config import settings
from app.services.openai_client import extract


def _mock_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return response


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})
    return RateLimitError("rate limited", response=response, body=None)


@pytest.mark.asyncio
async def test_extract_returns_parsed_json():
    payload = {"people": [{"ref": "p0", "notes": "Works at Acme"}], "action_items": []}
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(payload))

    with patch("app.services.openai_client._client", new=mock_client):
        result = await extract(
            "Hi, I work at Acme.",
            [{"ref": "p0", "email": "a@example.com", "name": None, "notes": None}],
        )

    assert result == payload
    mock_client.chat.completions.create.assert_called_once()
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == settings.llm_model
    assert "response_format" in kwargs


@pytest.mark.asyncio
async def test_extract_retries_once_on_rate_limit():
    payload = {"people": [], "action_items": []}
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[_rate_limit_error(), _mock_response(payload)]
    )

    with patch("app.services.openai_client._client", new=mock_client), \
         patch("app.services.openai_client.asyncio.sleep", new=AsyncMock()):
        result = await extract("content", [])

    assert result == payload
    assert mock_client.chat.completions.create.call_count == 2


def test_client_is_a_single_shared_instance_not_a_per_call_factory():
    """One client, one connection pool.

    Building an AsyncOpenAI per call leaks an httpx connection pool every time,
    and extraction runs once per message. This asserts the module holds an
    instance rather than a factory function.
    """
    from openai import AsyncOpenAI

    from app.services import openai_client

    assert isinstance(openai_client._client, AsyncOpenAI)


def test_client_points_at_the_configured_provider():
    """base_url and model come from config, so switching provider is a env change."""
    from app.core.config import settings
    from app.services import openai_client

    assert str(openai_client._client.base_url).rstrip("/") == settings.llm_base_url.rstrip("/")


@pytest.mark.asyncio
async def test_extract_sends_the_configured_model():
    from app.core.config import settings

    payload = {"people": [], "action_items": []}
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(payload))

    with patch("app.services.openai_client._client", new=mock_client):
        await extract("content", [])

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == settings.llm_model
