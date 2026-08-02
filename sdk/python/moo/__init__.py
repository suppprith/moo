"""moo: search infrastructure for AI agents.

    from moo import Moo

    client = Moo()
    results = client.web_search("postgres connection pooling")

Point an agent's existing ``web_search`` tool at :meth:`Moo.web_search`, or use
:meth:`Moo.research` for a cited multi-hop report that flags where sources
disagree. Every client method mirrors one HTTP endpoint; see the README.
"""

from ._transport import DEFAULT_BASE_URL
from .async_client import AsyncMoo
from .client import Moo
from .errors import (
    AuthenticationError,
    BudgetExceededError,
    ConnectionError,
    InternalError,
    InvalidRequestError,
    MooError,
    NotFoundError,
    RateLimitError,
    TimeoutError,
    UpstreamError,
)

__version__ = "0.1.0"

__all__ = [
    "Moo",
    "AsyncMoo",
    "MooError",
    "InvalidRequestError",
    "NotFoundError",
    "AuthenticationError",
    "RateLimitError",
    "BudgetExceededError",
    "TimeoutError",
    "UpstreamError",
    "InternalError",
    "ConnectionError",
    "DEFAULT_BASE_URL",
    "__version__",
]
