"""Chunk, embed, and upload the two retrieval corpora to Qdrant.

- docs:    man pages + GNU manual sections, split into ~512-token chunks
- history: Unix & Linux questions created before HISTORY_CUTOFF, one point per question
           (embedding = title + question; the accepted answer rides along as payload)

Without --yes this only prints the token count and estimated cost. Re-running resumes:
point IDs are deterministic, so chunks already in Qdrant are not embedded again.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import tiktoken
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document, MetadataMode, TextNode
from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import (  # noqa: E402
    DOCS_COLLECTION, EMBED_MODEL, HISTORY_COLLECTION, HISTORY_CUTOFF, PROCESSED_DIR, QDRANT_URL, cost_usd,
)
from agent.tools.retrieval import embed_model, vector_store  # noqa: E402

CHUNK_TOKENS = 512
CHUNK_OVERLAP = 64
MAX_QUESTION_CHARS = 4000
MAX_ANSWER_CHARS = 4000
_NAMESPACE = uuid.UUID("6f1c9a52-6a43-4c38-9d39-2f6a5b0c1e11")


def stable_id(key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, key))


def docs_nodes(path: Path) -> list[TextNode]:
    splitter = SentenceSplitter(chunk_size=CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP)
    hidden = ["doc_id", "url", "source", "package"]
    nodes: list[TextNode] = []
    with path.open() as f:
        for rec in map(json.loads, f):
            doc = Document(
                text=rec["text"],
                metadata={k: rec[k] for k in ("doc_id", "title", "url", "source", "package")},
                excluded_embed_metadata_keys=hidden,
                excluded_llm_metadata_keys=hidden,
            )
            for i, node in enumerate(splitter.get_nodes_from_documents([doc])):
                node.id_ = stable_id(f"{rec['doc_id']}#{i}")
                nodes.append(node)
    return nodes


def history_nodes(path: Path) -> Iterator[TextNode]:
    with path.open() as f:
        for rec in map(json.loads, f):
            if rec["created"] >= HISTORY_CUTOFF:
                continue
            metadata = {
                "question_id": rec["question_id"],
                "title": rec["title"],
                "url": rec["url"],
                "answer_url": rec["answer_url"],
                "answer_text": rec["answer_text"][:MAX_ANSWER_CHARS],
                "question_author": rec["question_author"]["name"],
                "answer_author": rec["answer_author"]["name"],
                "license": rec["answer_license"],
                "created": rec["created"],
                "tags": rec["tags"],
            }
            yield TextNode(
                id_=stable_id(f"q{rec['question_id']}"),
                text=f"{rec['title']}\n\n{rec['question_text'][:MAX_QUESTION_CHARS]}",
                metadata=metadata,
                excluded_embed_metadata_keys=list(metadata),
                excluded_llm_metadata_keys=list(metadata),
            )


def existing_ids(client: QdrantClient, collection: str) -> set[str]:
    if not client.collection_exists(collection):
        return set()
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(collection, limit=10_000, offset=offset, with_payload=False)
        ids.update(str(p.id) for p in points)
        if offset is None:
            return ids


def embed_tokens(nodes: list[TextNode]) -> int:
    enc = tiktoken.get_encoding("cl100k_base")  # tokenizer used by text-embedding-3-*
    return sum(len(enc.encode(n.get_content(metadata_mode=MetadataMode.EMBED))) for n in nodes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=[DOCS_COLLECTION, HISTORY_COLLECTION])
    parser.add_argument("--yes", action="store_true", help="actually call the embeddings API")
    args = parser.parse_args()

    client = QdrantClient(url=QDRANT_URL)
    corpora = {
        DOCS_COLLECTION: lambda: docs_nodes(PROCESSED_DIR / "docs.jsonl"),
        HISTORY_COLLECTION: lambda: list(history_nodes(PROCESSED_DIR / "unix_qa.jsonl")),
    }
    total_cost = 0.0
    todo: dict[str, list[TextNode]] = {}
    for name, load in corpora.items():
        if args.only and name != args.only:
            continue
        nodes = load()
        done = existing_ids(client, name)
        pending = [n for n in nodes if n.node_id not in done]
        tokens = embed_tokens(pending)
        cost = cost_usd(EMBED_MODEL, tokens)
        total_cost += cost
        todo[name] = pending
        print(f"{name}: {len(nodes)} nodes, {len(done)} already indexed, "
              f"{len(pending)} to embed = {tokens:,} tokens = ${cost:.2f}")
    print(f"Estimated embedding cost: ${total_cost:.2f} ({EMBED_MODEL})")
    if not args.yes:
        print("Dry run. Re-run with --yes to embed and upload.")
        return

    for name, pending in todo.items():
        if not pending:
            continue
        storage = StorageContext.from_defaults(vector_store=vector_store(client, name))
        # Embeds every node via the OpenAI embeddings API in batches, then upserts to Qdrant.
        VectorStoreIndex(pending, storage_context=storage, embed_model=embed_model(),
                         insert_batch_size=1000, show_progress=True)
        print(f"{name}: collection now has {client.count(name).count} points")


if __name__ == "__main__":
    main()
