"""moo for LlamaIndex: a retriever, a reader, and an agent tool spec over live
software search with claim-level evidence.

    from llama_index_moo import MooRetriever, MooToolSpec

    nodes = MooRetriever(k=6).retrieve("postgres connection pooling")
    tools = MooToolSpec().to_tool_list()

All three read ``MOO_BASE_URL`` and ``MOO_API_KEY`` from the environment; pass a
configured ``moo.Moo`` instance to override.
"""

from .retriever import MooReader, MooRetriever
from .tool_spec import MooToolSpec

__version__ = "0.1.0"

__all__ = ["MooRetriever", "MooReader", "MooToolSpec", "__version__"]
