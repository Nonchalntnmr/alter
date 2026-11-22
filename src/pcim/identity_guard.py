"""Personal identifiers in the files a goal wrote.

ALTER sees the person's context: their vault, their git history, their account. A model can copy
an identifier from that context into code. It did so once in a live run, writing the owner's name
into a test as the test user. This check runs when a goal finishes. It looks for this machine's
identifiers in every file the goal wrote, and reports each hit by label and file, never by value.

The identifiers are read at run time, the same set the CLI identity probe and
scripts/scan_identity.py use:
- the account email, its local part and its domain;
- the account's names and their tokens;
- $USER, and what is left of it once a name token is removed;
- the home path and the hostname;
- git's user.name and user.email.
Nothing personal is written into the repository to check for it. PCIM_IDENTITY_MARKERS (a JSON
file of label -> value) replaces them; only tests use it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MIN_LEN = 4


def markers() -> dict[str, str]:
    path = os.environ.get("PCIM_IDENTITY_MARKERS")
    if path:
        base = json.loads(Path(path).read_text())
    else:
        from .adapters.cli_probe import markers as machine
        base = machine()
    base = {k: str(v).lower() for k, v in base.items() if isinstance(v, str) and len(v) >= MIN_LEN}
    out = dict(base)
    email = base.get("account email") or base.get("git user.email") or ""
    if "@" in email and len(email.split("@", 1)[1]) >= 6:
        out["email domain"] = email.split("@", 1)[1]
    names = {t for k in ("account full name", "account display name", "git user.name")
             for t in base.get(k, "").replace(".", " ").split() if len(t) >= MIN_LEN}
    for i, t in enumerate(sorted(names)):
        out[f"name token {i + 1}"] = t
    user = base.get("username ($USER)", "")
    for t in names:
        rest = user.replace(t, "")
        if rest != user and len(rest) >= MIN_LEN:
            out["username fragment"] = rest
    return out


def scan(root: str | Path, files: list[str], marks: dict[str, str] | None = None) -> list[dict]:
    """[{"file", "labels"}] for each written file that holds an identifier. Files are read inside the
    workspace only (the governor's jail); a file that is gone or unreadable is skipped."""
    from .governor import PathError, jail
    marks = markers() if marks is None else marks
    hits = []
    for rel in dict.fromkeys(f for f in files if f):
        try:
            p = jail(root, rel)
            text = p.read_text(errors="replace").lower() if p.is_file() else ""
        except (PathError, OSError):
            continue
        labels = sorted(label for label, v in marks.items() if v in text)
        if labels:
            hits.append({"file": rel, "labels": labels})
    return hits


def verify_export(root: str | Path, files: list[str], marks: dict[str, str] | None = None) -> dict:
    """Fail-closed acceptance gate for explicit export manifests. Never includes values.

    Unlike the historical advisory scan, unreadable/absent/unsafe files fail verification.
    Exporters must call this on the exact bytes they package, and bind the returned hashes.
    """
    import hashlib

    from .governor import PathError, jail
    from .sovereignty.content import labels
    marks = markers() if marks is None else marks
    checked, findings = [], []
    if not files:
        return {"accepted": False, "files": [], "findings": [{"reason": "empty-manifest"}]}
    for rel in dict.fromkeys(files):
        try:
            p = jail(root, rel)
            if p.is_symlink() or not p.is_file():
                raise OSError()
            data = p.read_bytes()
            text = data.decode("utf-8", errors="strict")
            hits = labels(text, marks)
            if hits:
                findings.append({"file": rel, "reason": "private-content", "labels": hits})
            checked.append({"file": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        except (PathError, OSError, UnicodeDecodeError):
            findings.append({"file": rel, "reason": "unverifiable-file"})
    return {"accepted": not findings, "files": checked, "findings": findings}
