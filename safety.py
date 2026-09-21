"""
safety.py - the two "best practices" for the research MCP server.

1. Input sanitization  (check_query, check_paper_id)
2. Rate limiting       (RateLimiter)

Both are plain Python with no dependencies so they are easy to read and
easy to test on their own (see run_exploits.py).
"""

import re
import time
from collections import deque


# ---------------------------------------------------------------------------
# 1. INPUT SANITIZATION
# ---------------------------------------------------------------------------

# The longest search query we will send to OpenAlex.
MAX_QUERY_LENGTH = 200

# We accept two kinds of paper IDs. Anything else is rejected BEFORE it is
# placed into a URL, which is what stops path/query injection
# (for example "../authors" or "W123?api_key=stolen").
#
#   OpenAlex work id              e.g. W7129030749
#   doi:10.<prefix>/<suffix>      e.g. doi:10.1145/3796519
#
# ID formats are documented at https://help.openalex.org/api/ (see "Get a
# single work"). The regex itself was written for this project.
PAPER_ID_PATTERN = re.compile(
    r"^(?:"
    r"W[0-9]{1,12}"                             # OpenAlex work id
    r"|DOI:10\.[0-9]{4,9}/[A-Za-z0-9._;()\-]+"  # DOI (one slash only)
    r")$",
    re.IGNORECASE,
)

# Characters that should never appear in a search query: ASCII control
# characters (newline, tab, escape, null, ...).
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def check_query(query: str) -> str:
    """Return a cleaned query, or raise ValueError if it is unsafe.

    Rules:
      * must be a non-empty string
      * control characters are replaced with spaces (they could smuggle
        extra HTTP headers or confuse logs)
      * length is capped so one call cannot send a huge request
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must be a non-empty string.")

    cleaned = CONTROL_CHARS.sub(" ", query)  # replace control chars with a space
    cleaned = " ".join(cleaned.split())      # collapse repeated whitespace

    if len(cleaned) > MAX_QUERY_LENGTH:
        raise ValueError(
            f"Query is too long ({len(cleaned)} characters). "
            f"Maximum is {MAX_QUERY_LENGTH}."
        )
    return cleaned


def check_paper_id(paper_id: str) -> str:
    """Return a normalized paper ID if it matches an allowed format,
    otherwise raise ValueError. This is an allow-list: only known-good
    shapes get through, so injection strings are rejected."""
    if not isinstance(paper_id, str) or not PAPER_ID_PATTERN.match(paper_id.strip()):
        raise ValueError(
            "Invalid paper ID. Use an OpenAlex work id like 'W7129030749' "
            "or 'doi:<doi>'."
        )
    paper_id = paper_id.strip()
    if paper_id[:4].lower() == "doi:":        # OpenAlex wants the prefix in lowercase
        paper_id = "doi:" + paper_id[4:]
    return paper_id


# ---------------------------------------------------------------------------
# 2. RATE LIMITING
# ---------------------------------------------------------------------------

class RateLimiter:
    """A simple sliding-window rate limiter.

    Keeps the timestamps of recent calls for each tool name. A call is
    allowed if fewer than `max_calls` happened in the last `per_seconds`.

    This is the standard "sliding window log" pattern. The deque usage
    follows the Python standard library docs:
    https://docs.python.org/3/library/collections.html#collections.deque
    """

    def __init__(self, max_calls: int = 10, per_seconds: int = 60):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._calls: dict[str, deque] = {}   # tool name -> timestamps

    def check(self, name: str) -> None:
        """Record a call for `name`. Raise RuntimeError if over the limit."""
        now = time.monotonic()
        history = self._calls.setdefault(name, deque())

        # Drop timestamps that are older than the window.
        while history and now - history[0] > self.per_seconds:
            history.popleft()

        if len(history) >= self.max_calls:
            wait = int(self.per_seconds - (now - history[0])) + 1
            raise RuntimeError(
                f"Rate limit reached for '{name}': "
                f"{self.max_calls} calls per {self.per_seconds}s. "
                f"Try again in about {wait}s."
            )

        history.append(now)

    def reset(self) -> None:
        """Forget all history (used by the exploit demo)."""
        self._calls.clear()
