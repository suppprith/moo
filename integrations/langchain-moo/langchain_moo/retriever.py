"""moo as a LangChain retriever."""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from moo import Moo
from pydantic import ConfigDict, Field

from ._shared import async_twin, make_client, result_metadata


class MooRetriever(BaseRetriever):
    """Retrieve live software-engineering sources from moo.

        retriever = MooRetriever(k=6)
        docs = retriever.invoke("why is my postgres connection pool exhausted")

    Each :class:`~langchain_core.documents.Document` keeps the fields that make a
    moo result more than a link: the ``chk_`` handle behind it, the trust score,
    the cross-query-comparable relevance, when the page was fetched, and whether
    the page tripped moo's prompt-injection scan.

    Set ``full_text=True`` to fetch each chunk in full instead of the snippet,
    which costs one extra request per result and is usually what you want when
    the documents feed a RAG chain rather than a model's tool output.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Moo = Field(default_factory=Moo)
    k: int = 6
    depth: str = "raw"
    live: bool | None = None
    full_text: bool = False

    def __init__(self, client: Moo | None = None, **kwargs: Any):
        super().__init__(client=make_client(client), **kwargs)

    def _documents(self, payload: dict[str, Any]) -> list[Document]:
        return [
            Document(page_content=row.get("snippet") or "", metadata=result_metadata(row))
            for row in payload.get("results", [])
        ]

    def _fill_text(self, documents: list[Document]) -> list[Document]:
        for document in documents:
            handle = document.metadata.get("id")
            if not handle:
                continue
            chunk = self.client.chunk(handle)
            text = chunk.get("text")
            if text:
                document.page_content = text
        return documents

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        payload = self.client.web_search(query, max_results=self.k, depth=self.depth,
                                         live=self.live)
        documents = self._documents(payload)
        return self._fill_text(documents) if self.full_text else documents

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        client = async_twin(self.client)
        try:
            payload = await client.web_search(query, max_results=self.k, depth=self.depth,
                                              live=self.live)
            documents = self._documents(payload)
            if not self.full_text:
                return documents
            for document in documents:
                handle = document.metadata.get("id")
                if not handle:
                    continue
                chunk = await client.chunk(handle)
                if chunk.get("text"):
                    document.page_content = chunk["text"]
            return documents
        finally:
            await client.aclose()
