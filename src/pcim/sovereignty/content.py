"""Content checks at trust boundaries. Findings contain labels, never matched values.

This is a conservative credential detector, not a claim to detect every secret. Callers
may also supply exact synthetic/private markers. Consent never overrides a secret hit.
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping

_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----"),
    "provider-token": re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"),
    "credential-assignment": re.compile(
        r'''(?ix)["']?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret|password|passwd)\b["']?\s*[:=]\s*(?:"([^"\n]{8,})"|'([^'\n]{8,})'|([A-Za-z0-9_./+!-]{8,})(?=\s|$))'''),
    "bearer-token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    "credential-url": re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]{4,}@"),
    "synthetic-canary": re.compile(r"\bALTER_SECRET_CANARY_[A-Za-z0-9_-]{8,}\b"),
}
_PLACEHOLDER = re.compile(r"(?i)^(?:\$|\{|<|\[|os\.|process\.|getenv|environ|example|placeholder|redacted|changeme|your[_-]|none|null|false|true)")


class ContentBlocked(ValueError):
    def __init__(self, labels):
        self.labels = tuple(sorted(set(labels)))
        super().__init__("content withheld: " + ", ".join(self.labels))


def labels(text: str, markers: Mapping[str, str] | None = None) -> list[str]:
    hits = set()
    for label, rx in _PATTERNS.items():
        for match in rx.finditer(text):
            if label == "credential-assignment" and _PLACEHOLDER.match(next(g for g in match.groups() if g is not None)):
                continue
            hits.add(label)
    for label, value in (markers or {}).items():
        if isinstance(value, str) and len(value) >= 4 and value.casefold() in text.casefold():
            # Marker labels may be caller controlled; don't echo them in failure traces.
            hits.add("private-marker")
    return sorted(hits)


def _strings(payload):
    if isinstance(payload, str):
        yield payload
    elif dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        yield from _strings(dataclasses.asdict(payload))
    elif isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, str):
                yield json.dumps({key: value})  # keep credential field/value relationships
            yield from _strings(key)
            yield from _strings(value)
    elif isinstance(payload, (tuple, list)):
        for value in payload:
            yield from _strings(value)


def assert_safe_payload(payload, markers: Mapping[str, str] | None = None) -> None:
    hits = {hit for text in _strings(payload) for hit in labels(text, markers)}
    if hits:
        raise ContentBlocked(hits)


def public_payload(payload):
    """Withhold a whole contaminated string, so fragments/traces cannot echo its value."""
    if isinstance(payload, str):
        hits = labels(payload)
        return "[withheld: " + ", ".join(hits) + "]" if hits else payload
    if isinstance(payload, Mapping):
        result = {}
        for key, value in payload.items():
            hits = labels(json.dumps({key: value})) if isinstance(key, str) and isinstance(value, str) else []
            result[public_payload(key)] = "[withheld: " + ", ".join(hits) + "]" if hits else public_payload(value)
        return result
    if isinstance(payload, list):
        return [public_payload(v) for v in payload]
    if isinstance(payload, tuple):
        return tuple(public_payload(v) for v in payload)
    return payload
