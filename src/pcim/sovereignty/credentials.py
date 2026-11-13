"""Credential tripwire for text CI is about to send to a model vendor.

A request typed into `ci` goes to whichever vendor routing picks. Memory that is SECRET never
leaves the device (consent.py), but a credential pasted into the request itself would. This
scan runs on the request before routing: if it finds something credential-shaped, routing
offers only vendors that keep data on this machine (`local`, test doubles), and says why.

Patterns are the published shapes of common keys. It is a tripwire, not a guarantee: a
credential with no recognisable shape passes. It reports the *kind* found, never the value.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style secret key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("password assignment", re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret)\s*[:=]\s*['\"]?[^\s'\"]{8,}")),
    # .env / shell style: a name containing PASSWORD, SECRET, TOKEN, API_KEY, ACCESS_KEY or PRIVATE_KEY
    # (DB_PASSWORD=..., export GITHUB_TOKEN=...), with a value that has a letter (MAX_TOKENS=128000 is not one).
    ("secret in an assignment", re.compile(
        r"(?im)^[ \t]*(?:export[ \t]+)?[A-Za-z0-9_]*?(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY)"
        r"(?:_[A-Za-z0-9]+)*[ \t]*[=:][ \t]*['\"]?(?=[^\s'\"]*[A-Za-z])[^\s'\"]{6,}")),
    ("secret in JSON or YAML", re.compile(
        r"(?i)[\"']?\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"private[_-]?key)[\"']?[ \t]*:[ \t]*[\"'][^\"'\s]{6,}[\"']")),
    ("password in a URL", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://[^\s/:@]{1,64}:[^\s/@]{3,128}@[^\s/]{2,}")),
)

LOCAL_VENDORS = frozenset({"local", "mock"})


def find_credentials(text: str) -> list[str]:
    """Kinds of credential-shaped strings in `text`, in pattern order, without the values."""
    return [kind for kind, pat in _PATTERNS if pat.search(text or "")]
