"""The blank pilot sheet in docs/release/pilot/ has exactly the columns PILOT.md §6 defines, in order, so a
participant who starts from it fills the sheet the analysis expects."""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_blank_pilot_sheet_matches_the_protocols_columns():
    text = (ROOT / "docs" / "release" / "PILOT.md").read_text()
    table = text.split("**The pilot sheet,**", 1)[1].split("**Time saved**", 1)[0]
    columns = re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE)
    with open(ROOT / "docs" / "release" / "pilot" / "PILOT_SHEET.csv", newline="") as f:
        header = next(csv.reader(f))
    assert len(columns) == 12 and header == columns


def test_the_consent_note_keeps_the_protocols_terms():
    note = (ROOT / "docs" / "release" / "pilot" / "CONSENT.md").read_text()
    for term in ("It cannot push, publish, send, spend or install anything.", "alter report --forget-log",
                 "Nothing goes to the facilitator except the exports", "My cost cap for the pilot"):
        assert term in note
