"""Input validation and abuse heuristics.

Deliberately conservative and dependency-free. Nothing here tries to be clever:
the goal is to reject the obvious junk before it reaches the database, while
never mangling legitimate text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches the blog's own slugs (build.mjs derives them from filenames).
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$")

URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
NULL_RE = re.compile(r"\x00")
CONTROL_RE = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]")
MORE_THAN_TWO_BLANKS = re.compile(r"\n{3,}")


class Invalid(Exception):
    """Rejected input. The message is safe to return to the client."""


def clean_slug(raw: object) -> str:
    if not isinstance(raw, str):
        raise Invalid("post must be a string")
    slug = raw.strip()
    if not SLUG_RE.match(slug):
        raise Invalid(
            "post must be a slug: lowercase letters, digits, dot, dash or underscore"
        )
    return slug


def clean_body(raw: object, *, max_length: int, max_links: int) -> str:
    if not isinstance(raw, str):
        raise Invalid("body must be a string")

    text = NULL_RE.sub("", raw)
    text = CONTROL_RE.sub("", text)
    # Normalise line endings and collapse runs of blank lines.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = MORE_THAN_TWO_BLANKS.sub("\n\n", text).strip()

    if not text:
        raise Invalid("body is empty")
    if len(text) < 2:
        raise Invalid("body is too short")
    if len(text) > max_length:
        raise Invalid(f"body is longer than {max_length} characters")

    links = URL_RE.findall(text)
    if len(links) > max_links:
        raise Invalid(f"body contains more than {max_links} links")

    return text


def is_spammy(text: str) -> str | None:
    """Return a reason string when the text looks like junk, else None."""
    if NULL_RE.search(text):
        return "body contains a null byte"
    # Ignore short bodies so emoji-only replies ("🙏") are not punished; the
    # ratio test is only meaningful once there is enough text to judge.
    if len(text) >= 8:
        letters = sum(1 for c in text if c.isalnum())
        if letters / len(text) < 0.3:
            return "body is mostly punctuation"
    return None


@dataclass
class RateDecision:
    allowed: bool
    retry_after: int = 0
    reason: str = ""