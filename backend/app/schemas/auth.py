from typing import Literal

from pydantic import BaseModel, Field


class ConnectAccountIn(BaseModel):
    """A mailbox the user has just authorised.

    provider_account_id and email_address come from the provider's own identity
    on the OAuth response, not from anything the user typed. They are what make
    a second mailbox addressable: without a stable per-account id, reconnecting
    could not be told apart from connecting something new.
    """

    provider: Literal["microsoft", "google"] = "microsoft"
    provider_account_id: str = Field(min_length=1)
    email_address: str = Field(min_length=1)
    display_name: str | None = None

    provider_token: str
    provider_refresh_token: str
    expires_in: int
    scopes: list[str] = []
