"""moo for LangChain: a retriever and tools over live software search with
claim-level evidence.

    from langchain_moo import MooRetriever, moo_toolkit

    retriever = MooRetriever(k=6)
    tools = moo_toolkit()

Both read ``MOO_BASE_URL`` and ``MOO_API_KEY`` from the environment; pass a
configured ``moo.Moo`` instance to override.
"""

from .retriever import MooRetriever
from .tools import MooDeepResearchTool, MooExtractTool, MooSearchTool, moo_toolkit

__version__ = "0.1.0"

__all__ = [
    "MooRetriever",
    "MooSearchTool",
    "MooExtractTool",
    "MooDeepResearchTool",
    "moo_toolkit",
    "__version__",
]
