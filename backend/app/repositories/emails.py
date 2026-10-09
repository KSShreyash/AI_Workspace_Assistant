import json
import uuid
from datetime import datetime

import asyncpg


class EmailsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(
        self,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        provider_message_id: str,
        subject: str | None,
        from_address: str | None,
        from_name: str | None,
        to_recipients: list[dict],
        received_at: datetime | None,
        body_text: str | None,
        classification: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            insert into public.emails
                (user_id, account_id, provider_message_id, subject, from_address, from_name,
                 to_recipients, received_at, body_text, classification, synced_at)
            values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, now())
            on conflict (account_id, provider_message_id) do update
            set subject = excluded.subject,
                from_address = excluded.from_address,
                from_name = excluded.from_name,
                to_recipients = excluded.to_recipients,
                received_at = excluded.received_at,
                body_text = excluded.body_text,
                -- coalesce, not overwrite: a resync that omits the field (or a
                -- metadata-only fetch) must not erase a verdict we already
                -- have, which would re-admit known bulk mail and cost a full
                -- LLM call on the next run.
                classification = coalesce(excluded.classification, public.emails.classification),
                synced_at = now()
            """,
            user_id,
            account_id,
            provider_message_id,
            subject,
            from_address,
            from_name,
            json.dumps(to_recipients),
            received_at,
            body_text,
            classification,
        )

    async def delete(self, *, account_id: uuid.UUID, provider_message_id: str) -> None:
        """Scoped to the account, not the user.

        Keyword-only on purpose: this used to be keyed by user_id, and a
        stale positional call would silently match no rows rather than fail.
        """
        await self._pool.execute(
            "delete from public.emails where account_id = $1 and provider_message_id = $2",
            account_id,
            provider_message_id,
        )

    async def count(self, user_id: uuid.UUID) -> int:
        return await self._pool.fetchval(
            "select count(*) from public.emails where user_id = $1",
            user_id,
        )
