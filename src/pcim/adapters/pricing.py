"""Per-model token prices, USD per million tokens.

Prices change; this table carries the date and source of every row, and an unknown model
returns cost None rather than 0 — a dashboard that silently reports $0.00 for an unpriced
model is worse than one that says "unknown". Override or extend with a JSON file at
$PCIM_PRICES (same shape as PRICES below).

Anthropic rows: the claude-api reference bundled with Claude Code, cached 2026-10-06.
Cache writes (5-minute TTL) are billed at 1.25× input. Claude Haiku 5.5's prices apply
to prompts up to 100K tokens ($0.50 / $2.50 beyond); this table does not model the step.
OpenAI and Google rows are filled from their pricing pages by docs/07's research pass;
until then those models are unpriced on purpose.
"""

from __future__ import annotations

import json
import os

from .base import Usage

PRICES: dict[str, dict] = {
    # model: {input, output, cache_read, cache_write_mult, as_of, source}
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "as_of": "2026-10-06", "source": "anthropic"},
    "claude-fable-5-2": {"input": 8.0, "output": 40.0, "cache_read": 0.20, "as_of": "2026-11-03", "source": "anthropic"},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "as_of": "2026-10-06", "source": "anthropic"},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "as_of": "2026-10-06", "source": "anthropic"},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "as_of": "2026-10-06", "source": "anthropic"},
    "mock-1": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "as_of": "n/a", "source": "mock"},
}


def _table() -> dict[str, dict]:
    path = os.environ.get("PCIM_PRICES")
    if path and os.path.exists(path):
        return {**PRICES, **json.load(open(path))}
    return PRICES


def cost_usd(model: str, usage: Usage) -> float | None:
    p = _table().get(model)
    if p is None:
        return None
    write_mult = p.get("cache_write_mult", 1.25)
    return (
        usage.input_tokens * p["input"]
        + usage.output_tokens * p["output"]
        + usage.cache_read_tokens * p.get("cache_read", p["input"])
        + usage.cache_write_tokens * p["input"] * write_mult
    ) / 1e6
