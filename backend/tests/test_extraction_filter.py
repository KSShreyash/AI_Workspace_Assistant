"""Decide which messages are worth spending LLM tokens on.

The binding constraint on a free tier is tokens per day, and most inbox volume
is automated mail that produces no action items. Filtering before the model
rather than after is what makes a real mailbox fit inside the budget.

These are pure-function tests: no database, no network.
"""

import pytest

from app.services.extraction_filter import should_extract


def _msg(**overrides):
    base = {
        "from_address": "colleague@example.com",
        "subject": "Re: Thursday review",
        "body": "Can you send the deck before Thursday?",
        "inference_classification": None,
    }
    base.update(overrides)
    return base


def test_ordinary_message_from_a_person_is_extracted():
    ok, reason = should_extract(_msg())
    assert ok is True
    assert reason is None


@pytest.mark.parametrize(
    "sender",
    [
        "noreply@example.com",
        "no-reply@example.com",
        "donotreply@example.com",
        "notifications@example.com",
        "notification@example.com",
        "mailer-daemon@example.com",
        "bounce@example.com",
    ],
)
def test_automated_senders_are_skipped(sender):
    ok, reason = should_extract(_msg(from_address=sender))
    assert ok is False
    assert reason


def test_a_person_whose_name_merely_contains_a_keyword_is_not_skipped():
    """Substring matching would skip real people - match the local part exactly."""
    ok, _ = should_extract(_msg(from_address="arnold.bouncer@example.com"))
    assert ok is True


def test_sender_matching_is_case_insensitive():
    ok, _ = should_extract(_msg(from_address="NoReply@Example.com"))
    assert ok is False


def test_message_with_almost_no_content_is_skipped():
    ok, reason = should_extract(_msg(body="ok"))
    assert ok is False
    assert reason


def test_microsoft_classifies_it_as_other_so_it_is_skipped():
    """Use the provider's own bulk classifier - it is free and already computed."""
    ok, reason = should_extract(_msg(inference_classification="other"))
    assert ok is False
    assert reason


def test_microsoft_focused_mail_is_extracted():
    ok, _ = should_extract(_msg(inference_classification="focused"))
    assert ok is True


def test_unknown_classification_does_not_skip():
    """Rows synced before classification was stored must not all be dropped."""
    ok, _ = should_extract(_msg(inference_classification=None))
    assert ok is True


def test_missing_sender_does_not_crash():
    ok, _ = should_extract(_msg(from_address=None))
    assert ok is True


def test_skip_reason_names_the_rule_that_fired():
    """The reason is stored on the row, so it has to be diagnostic."""
    _, reason = should_extract(_msg(from_address="noreply@example.com"))
    assert "sender" in reason.lower()
