"""Report classification only. These tests do not claim Linux confinement."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location('alt4_verifier', Path(__file__).parents[1] / 'scripts' / 'verify_alt4_execution.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.mark.parametrize('body,expected', [
    ('', 'PASS'), ('<failure/>', 'FAIL'), ('<error/>', 'FAIL'),
    ('<skipped/>', 'BLOCKED'), ('<failure/><skipped/>', 'FAIL'),
])
def test_report_does_not_convert_missing_or_failed_checks_to_pass(body, expected):
    case = ET.fromstring('<testcase name="target">' + body + '</testcase>')
    assert verifier.case_status([case], 'target') == expected
    assert verifier.case_status([case], 'absent') == 'BLOCKED'


def test_all_five_exact_execution_checks_are_named():
    assert len(verifier.NAMES) == len(set(verifier.NAMES)) == 5
    assert sum(name.endswith('[sqlite]') for name in verifier.NAMES) == 3
