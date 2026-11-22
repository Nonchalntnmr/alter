"""Retrieval planning: the model says what it would need to know; CI decides what it may see.

A similarity score cannot tell that "can I drive five kids to the away game" needs the
person's car, or that "what should I order at happy hour" needs "sober since 2019": the
link is an inference, not a resemblance. On the E2 development set, the best of eight local
embedding models put only 81% of needed memories in its top 10 (experiments/e2/CALIBRATION.md).

So before answering, the answering model's vendor is shown the request alone (which it
receives anyway) and writes a few searches for the personal facts that would change a good
answer. CI runs them here, against memory the egress guard already filtered for that
vendor. The vendor sees no memory to plan; it receives only what the searches select.

A remote model that ranked memories itself would have to be sent every allowed memory to
rank, which is naive history by another name. Planning keeps minimization: the model
names what it needs, and CI's local, permissioned retrieval decides what that is.

A planner that fails or replies with something unparseable returns None, and selection
falls back to the unplanned channels (similarity, standing items, recency).
"""

from __future__ import annotations

import json
import re

from .adapters.base import ProviderError, RenderedPrompt

PLANNER_SYSTEM = """Before an AI assistant answers a person, it can look up notes from that person's own memory: their preferences, constraints, situation, belongings, people in their life, projects and past events. You see only the request. Write the searches that would find the personal facts that could change a good answer.

Reply with one JSON object and nothing else: {"queries": ["<short search query>", ...]}
- At most 4 queries, each a few words about the person (e.g. "the person's home neighborhood").
- Include how they like this kind of output written or formatted, when the request produces writing or code.
- When the answer depends on which device, tool, place, vehicle or person is involved, search for it.
- When the output is about the person or sent in their name, search for their name and role.
- When the request concerns one of their projects, search for that project's stack, constraints and decisions.
- Use [] when no fact about the person could change the answer (arithmetic, translation, general knowledge)."""

MAX_QUERIES = 4
MAX_QUERY_CHARS = 120
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_plan(raw: str) -> list[str] | None:
    s = _FENCE.sub("", (raw or "").strip())
    for cand in (s, s[s.find("{"): s.rfind("}") + 1] if "{" in s else ""):
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            obj = obj.get("queries")
        if isinstance(obj, list):  # models sometimes reply with the bare list
            qs = [" ".join(str(q).split())[:MAX_QUERY_CHARS] for q in obj if str(q).strip()]
            return qs[:MAX_QUERIES]
    return None


def plan_queries(adapter, task: str) -> tuple[list[str] | None, object | None]:
    """(queries, the planner's ModelResponse). queries is None when no usable plan came back."""
    try:
        from .sovereignty.content import assert_safe_payload
        prompt = RenderedPrompt(PLANNER_SYSTEM, f"Request:\n{task}")
        assert_safe_payload(prompt)
        resp = adapter.generate(prompt)
    except ProviderError:
        return None, None
    return parse_plan(resp.text), resp
