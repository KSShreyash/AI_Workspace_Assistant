"""Text helpers shared by the extraction pipeline and the API layer.

strip_html lived in ai_extraction, which made the Emails endpoint import a
private name across a module boundary to render a preview. Two callers with
genuinely different reasons to want the same thing is what a shared module is
for.
"""

import re
from html import unescape

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def strip_html(value: str | None) -> str | None:
    """Plain text from a mail body, which is usually HTML.

    Tags become spaces rather than being deleted, so adjacent block elements do
    not run their words together ("</div><div>" must not yield "ab").
    """
    if not value:
        return value
    text = _TAG.sub(" ", value)
    text = unescape(text)
    return _WHITESPACE.sub(" ", text).strip()
