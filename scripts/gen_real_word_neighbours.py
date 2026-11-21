"""Regenerate `intent_compiler._REAL_WORDS`, the words that are never read as a typo of an action verb.

A word one edit from an action verb ("look" / "book", "test" / "text", "clean" / "clear") is only a
typo if it is not itself a common word. "Common word" means a whole lowercase alphabetic token in
the uncased WordPiece vocabulary of all-MiniLM-L6-v2, which is the embedding model CI already
caches. Inflections of a lexicon verb ("emails", "deleted") are left out of the list, so they
still match.

Run it whenever ACTION_VERBS or DESTRUCTIVE_VERBS change, and paste the output into
intent_compiler.py:

    PYTHONPATH=src python scripts/gen_real_word_neighbours.py
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from pcim.intent_compiler import ACTION_VERBS, DESTRUCTIVE_VERBS, _osa

VOCAB = next(Path.home().glob(".cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/"
                              "snapshots/*/vocab.txt"), None)


def main() -> None:
    if VOCAB is None:
        sys.exit("all-MiniLM-L6-v2 is not cached; its vocab.txt is the word list")
    lexicon = ACTION_VERBS | DESTRUCTIVE_VERBS
    stems = [v for v in lexicon if len(v) >= 4]
    vocab = {w for w in VOCAB.read_text().split() if w.isalpha() and w.islower() and len(w) >= 4}
    out = sorted(w for w in vocab
                 if w not in lexicon
                 and not (w[-1] in "sd" and w[:-1] in lexicon)       # emails, invited: inflections still match
                 and any(_osa(w, v) == 1 for v in stems))
    body = " ".join(out)
    print("_REAL_WORDS = frozenset(\"\"\"")
    print(textwrap.fill(body, 100))
    print("\"\"\".split())")
    print(f"# {len(out)} words", file=sys.stderr)


if __name__ == "__main__":
    main()
