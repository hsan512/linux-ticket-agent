"""Shared settings: model names, prices, and paths. Secrets come from the environment
only."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
RUNS_DIR = ROOT / "eval" / "runs"
BENCHMARK_DIR = ROOT / "eval" / "benchmark"

QDRANT_URL = os.environ.get(
    "QDRANT_URL", "http://localhost:6333"
)  # docker compose up -d

GENERATOR_MODEL = "gpt-5.4-mini"
JUDGE_MODEL = "gpt-6-sol"
EMBED_MODEL = "text-embedding-3-small"

DOCS_COLLECTION = "docs"
HISTORY_COLLECTION = "history"
PACKAGE_COLLECTION = "package"

# Questions created before this date form the "similar issues" corpus; the benchmark is
# drawn from questions on or after it, so no benchmark answer can leak into retrieval.
HISTORY_CUTOFF = "2023-01-01"

# USD per 1M tokens (input, output), from https://developers.openai.com/api/docs/pricing
# (checked 2026-10-18). Reasoning tokens are billed as output.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-6-sol": (2.00, 10.00),
    "text-embedding-3-small": (0.02, 0.0),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    """Cost of one call from PRICES; raises KeyError for a model without a price."""
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000
