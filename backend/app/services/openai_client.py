import asyncio
import json

from openai import AsyncOpenAI, RateLimitError

from app.core.config import settings

# Model and endpoint come from settings - see config.Settings.llm_*

_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "extraction_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "people": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["ref", "notes"],
                        "additionalProperties": False,
                    },
                },
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "direction": {"type": "string", "enum": ["mine", "theirs"]},
                            "due_date": {"type": ["string", "null"]},
                            "participant_ref": {"type": ["string", "null"]},
                        },
                        "required": ["text", "direction", "due_date", "participant_ref"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["people", "action_items"],
            "additionalProperties": False,
        },
    },
}


# Module-level, not a factory. Each AsyncOpenAI owns an httpx connection pool
# that is never closed, and extraction constructs one per message - so a factory
# leaks a pool per extracted email.
_client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def _build_prompt(content: str, participants: list[dict]) -> str:
    participant_lines = "\n".join(
        f"- {p['ref']}: {p.get('name') or p.get('email') or 'unknown'}"
        f" (existing notes: {p.get('notes') or 'none'})"
        for p in participants
    )
    return (
        "You are extracting relationship facts and action items from a piece of "
        "communication (email, calendar event, or chat message) for a personal "
        "relationship-management tool.\n\n"
        f"Known participants:\n{participant_lines}\n\n"
        f"Content:\n{content}\n\n"
        "For each participant, write an updated 'notes' field: a short, synthesized "
        "summary of what's known about them, incorporating any new information from "
        "this content into their existing notes (rewrite for clarity, don't just "
        "append). If nothing new is learned about a participant, return their notes "
        "unchanged (or an empty string if they had none).\n\n"
        "Also extract any action items/commitments mentioned: things the user (the "
        "mailbox owner) will do for someone ('mine'), or things someone else "
        "committed to do for the user ('theirs'). Include participant_ref (matching "
        "one of the refs above) when the item clearly relates to one participant, "
        "else null. Include due_date (YYYY-MM-DD) only if stated or clearly implied, "
        "else null. Return an empty action_items list if there are none."
    )


async def extract(content: str, participants: list[dict]) -> dict:
    prompt = _build_prompt(content, participants)
    messages = [{"role": "user", "content": prompt}]
    try:
        response = await _client.chat.completions.create(
            model=settings.llm_model, messages=messages, response_format=_SCHEMA
        )
    except RateLimitError:
        await asyncio.sleep(5)
        response = await _client.chat.completions.create(
            model=settings.llm_model, messages=messages, response_format=_SCHEMA
        )
    return json.loads(response.choices[0].message.content)
