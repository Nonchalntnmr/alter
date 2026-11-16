"""Release gate: does a diff add this machine's identifiers? Prints counts per label, never values.

    PYTHONPATH=src python scripts/scan_identity.py [BASE]      # default BASE: origin/main

The identifiers are read from this machine at run time, the same set the CLI identity probe uses:
- the account email and its local part, the account names, and the account and organization ids;
- $USER and the home path;
- git's user.name and user.email.
So nothing personal has to be written into the repository to check for it. Only lines the diff
adds are scanned. Exits 1 if any identifier is found.

Two kinds of hit are expected:
- the project's own author field;
- a commit author line.
The scan reports per label so those can be told apart.
"""
from __future__ import annotations

import subprocess
import sys

from pcim.adapters.cli_probe import markers


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    diff = subprocess.run(["git", "diff", base], capture_output=True, text=True, check=True).stdout
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    marks = {label: value.lower() for label, value in markers().items()}  # read once
    files: dict[str, set[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            low = line.lower()
            for label, value in marks.items():
                if value in low:
                    files.setdefault(label, set()).add(current)
    text = "\n".join(added).lower()
    hits = {label: text.count(value) for label, value in marks.items()}
    found = {k: v for k, v in hits.items() if v}
    print(f"scanned {len(added)} added lines against {base}")
    for label in sorted(hits):
        print(f"  {label:22} {hits[label]}" + (f"   in: {', '.join(sorted(files.get(label, ())))}" if hits[label] else ""))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
