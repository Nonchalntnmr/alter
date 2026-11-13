"""Text that reaches the person's terminal from stored or model-written sources.

Notes written over MCP, ingested documents and model answers can carry terminal control
sequences: OSC 52 writes the clipboard, CSI sequences clear the screen or move the cursor,
and an 8-bit CSI does the same without an ESC. None of it belongs in CI's output, so every
such string is passed through `terminal_safe` before it is printed. Newlines and tabs stay.
"""

from __future__ import annotations

import re

_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")  # operating-system commands (clipboard, title)
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")  # cursor, erase, colour
_ESC = re.compile(r"\x1b[@-_]?")  # any other escape
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # C0 (except \t \n) and C1, incl. 8-bit CSI


def terminal_safe(text: str) -> str:
    if not text:
        return text or ""
    return _CTRL.sub("", _ESC.sub("", _CSI.sub("", _OSC.sub("", text))))
