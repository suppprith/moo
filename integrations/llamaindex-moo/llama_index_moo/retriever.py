"""moo as a LlamaIndex retriever and reader."""

from __future__ import annotations

from typing import Any

from llama_index.core.readers.base import BaseReader
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import Document, NodeWithScore, QueryBundle, TextNode
from moo import Moo

from ._shared import make_client, result_metadata

MAX_EXTRACT_URLS = 10


def _node(row: dict[str, Any], text: str) -> NodeWithScore:
    metadata = result_metadata(row)
    node = TextNode(text=text, id_=row.get("id"), metadata=metadata)
    node.excluded_embed_metadata_keys = list(metadata)
    return NodeWithScore(node=node, score=row.get("relevance") or row.get("score"))


class MooRetriever(BaseRetriever):
    """Retrieve live software-engineering sources from moo.

        retriever = MooRetriever(k=6)
        nodes = retriever.retrieve("why is my postgres connection pool exhausted")

    Node metadata keeps the ``chk_`` handle, trust score, source type, fetch time
    and moo's prompt-injection flag, and the node score is moo's relevance, which
    is comparable across queries rather than only within one result set.

    ``full_text=True`` fetches each chunk in full instead of the snippet, one
    extra request per hit, which is what you want when nodes feed a query engine.
    """

    def __init__(
        self,
        client: Moo | None = None,
        *,
        k: int = 6,
        depth: str = "raw",
        live: bool | None = None,
        full_text: bool = False,
        callback_manager: Any = None,
    ):
        self.client = make_client(client)
        self.k = k
        self.depth = depth
        self.live = live
        self.full_text = full_text
        super().__init__(callback_manager=callback_manager)

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        payload = self.client.web_search(query_bundle.query_str, max_results=self.k,
                                         depth=self.depth, live=self.live)
        nodes = []
        for row in payload.get("results", []):
            text = row.get("snippet") or ""
            if self.full_text and row.get("id"):
                text = self.client.chunk(row["id"]).get("text") or text
            nodes.append(_node(row, text))
        return nodes


class MooReader(BaseReader):
    """Read specific pages through moo and get clean markdown documents.

        docs = MooReader().load_data(["https://www.sqlite.org/wal.html"])

    Pages inside their cache TTL come back without touching the network, and a
    URL that fails is reported in the document metadata rather than aborting the
    batch.
    """

    def __init__(self, client: Moo | None = None, *, depth: str = "raw"):
        self.client = make_client(client)
        self.depth = depth

    def load_data(self, urls: list[str], **kwargs: Any) -> list[Document]:
        documents = []
        for start in range(0, len(urls), MAX_EXTRACT_URLS):
            payload = self.client.extract(urls[start:start + MAX_EXTRACT_URLS], depth=self.depth)
            for page in payload.get("results", []):
                if page.get("error"):
                    continue
                metadata = {key: page[key] for key in ("url", "title", "document")
                            if page.get(key) is not None}
                documents.append(Document(text=page.get("markdown") or "", metadata=metadata))
        return documents
