"""LlamaIndex retrievers over Qdrant: `search_docs` (man pages, GNU manuals) and
`search_similar` (past Unix & Linux questions with their accepted answers)."""

from __future__ import annotations

import re

from llama_index.core import VectorStoreIndex
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from agent.config import DOCS_COLLECTION, EMBED_MODEL, HISTORY_COLLECTION, QDRANT_URL
from agent.schemas import AgentDraft, Evidence

MAX_HISTORY_ANSWER_CHARS = 2000


def embed_model() -> OpenAIEmbedding:
    return OpenAIEmbedding(model=EMBED_MODEL, embed_batch_size=100)


def vector_store(client: QdrantClient, collection: str) -> QdrantVectorStore:
    return QdrantVectorStore(collection_name=collection, client=client)


def _retriever(client: QdrantClient, collection: str, top_k: int) -> BaseRetriever:
    index = VectorStoreIndex.from_vector_store(
        vector_store(client, collection), embed_model=embed_model()
    )
    return index.as_retriever(similarity_top_k=top_k)


def _doc_evidence(hit: NodeWithScore, idx: int) -> Evidence:
    md = hit.node.metadata
    return Evidence(
        id=f"D{idx}",
        source="docs",
        title=md["title"],
        url=md["url"],
        text=hit.node.get_content(),
        score=hit.score or 0.0,
    )


def _history_evidence(hit: NodeWithScore, idx: int) -> Evidence:
    """The past question (the embedded text) plus its accepted answer, truncated, with
    attribution."""
    md = hit.node.metadata
    text = (
        f"Past question: {hit.node.get_content()}\n\n"
        f"Accepted answer (by {md['answer_author']}, {md['license']}):\n"
        f"{md['answer_text'][:MAX_HISTORY_ANSWER_CHARS]}"
    )
    authors = list(
        dict.fromkeys([md["question_author"], md["answer_author"]])
    )  # remove duplicates
    return Evidence(
        id=f"H{idx}",
        source="history",
        title=md["title"],
        url=md["url"],
        text=text,
        score=hit.score or 0.0,
        authors=authors,
        license=md.get("license"),
    )


def format_context(evidence: list[Evidence]) -> str:
    """Passages as the LLM sees them: "[D1] title" then the text."""
    return "\n\n".join(f"[{e.id}] {e.title}\n{e.text}" for e in evidence)


def cited_evidence(draft: AgentDraft, evidence: list[Evidence]) -> list[Evidence]:
    """The passages a draft relies on, in retrieval order: IDs in its citations list or
    written inline in the answer (e.g. "(D4)"), the same rule as eval/judge.py's
    cited_ids."""
    cited = set(draft.citations) | set(re.findall(r"\b[DHP]\d+\b", draft.answer))
    return [e for e in evidence if e.id in cited]


class Retriever:
    """Both vector-search tools over one Qdrant connection. IDs are D1.., H1.. per call;
    retrieve renumbers them across calls."""

    def __init__(
        self, docs_k: int = 5, history_k: int = 3, url: str = QDRANT_URL
    ) -> None:
        client = QdrantClient(url=url)
        self._docs = _retriever(client, DOCS_COLLECTION, docs_k)
        self._history = _retriever(client, HISTORY_COLLECTION, history_k)

    def search_docs(self, query: str) -> list[Evidence]:
        return [
            _doc_evidence(h, i)
            for i, h in enumerate(self._docs.retrieve(query), start=1)
        ]

    def search_similar(self, query: str) -> list[Evidence]:
        return [
            _history_evidence(h, i)
            for i, h in enumerate(self._history.retrieve(query), start=1)
        ]
