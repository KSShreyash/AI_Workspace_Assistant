"""Decide whether a message is worth spending LLM tokens on.

On a free tier the binding limit is tokens per day, not requests per day, and
most inbox volume is automated mail that yields no action items. Filtering
before the model is what makes a real mailbox fit inside the budget.

Deliberately a pure function over a plain mapping: no database, no network, no
provider SDK. That keeps it exhaustively testable and lets the same rules apply
to Microsoft and Gmail messages alike.

The rules are tiered:

  Tier 1  sender shape, body length - derivable from columns that already exist,
          so these apply to mail synced before any of this was added.
  Tier 2  the provider's own bulk classifier. Microsoft returns
          inferenceClassification (focused/other) by default, at no extra API
          cost. Gmail's labelIds categories are the equivalent and slot in here.

Unknown is never treated as bulk. A message missing a classification is
extracted, because the alternative silently drops every row synced before the
field was stored.
"""

import re

# Matched against the whole local part, not as a substring: "arnold.bouncer"
# is a person, "bounce" is a daemon.
_AUTOMATED_LOCAL_PARTS = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?|mailer([-_.]?daemon)?"
    r"|bounce[sd]?|postmaster|automated|alerts?|updates?)$",
    re.IGNORECASE,
)

# Below this there is nothing to extract and the call is pure overhead:
# "ok", "thanks", "got it", "sounds good". Kept deliberately low - a real
# request can be very short ("Can you send the deck before Thursday?" is 38
# characters) and those are exactly the messages worth catching.
_MIN_BODY_CHARS = 20


def should_extract(message: dict) -> tuple[bool, str | None]:
    """Return (should_extract, skip_reason).

    skip_reason is stored on the row, so it names the rule that fired rather
    than restating the outcome.
    """
    classification = (message.get("inference_classification") or "").lower()
    if classification == "other":
        return False, "provider classified the message as non-focused (other)"

    from_address = message.get("from_address") or ""
    local_part = from_address.split("@", 1)[0]
    if local_part and _AUTOMATED_LOCAL_PARTS.match(local_part):
        return False, f"sender looks automated: {from_address}"

    body = (message.get("body") or "").strip()
    if len(body) < _MIN_BODY_CHARS:
        return False, f"body too short to extract from ({len(body)} chars)"

    return True, None
