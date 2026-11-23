#!/usr/bin/env python3
"""Verify the five ALT-4 execution checks, never bypassing actual confinement.

Exit: 0 all PASS, 1 at least one FAIL, 2 at least one BLOCKED.
No provider calls, installs, infrastructure provisioning or secret values in the report.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

NAMES = [
    'test_three_attributable_workers_real_files_and_test_receipts[memory]',
    'test_three_attributable_workers_real_files_and_test_receipts[sqlite]',
    'test_modified_artifact_cannot_satisfy_final_completion[memory]',
    'test_modified_artifact_cannot_satisfy_final_completion[sqlite]',
    'test_sqlite_restart_continues_dependencies_without_repeating_completed_action[sqlite]',
]
CAPABILITY = ('Linux with trusted root-owned bubblewrap, allowed user/network/PID/mount namespaces, '
              '/usr/bin/python3 and system python3 visible in the confined runtime')


def fingerprint(root):
    files = ['src/pcim/sandbox.py', 'src/pcim/orchestration.py',
             'src/pcim/run_service.py', 'tests/test_specialist_execution.py']
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}


def probe(directory):
    from pcim.sandbox import confined_command
    root = directory / 'probe-project'
    root.mkdir()
    with confined_command(root, ['python3', '-I', '-c', "print('ALT4_CONFINED_OK')"]) as (argv, receipt):
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                                   env={'PATH': '/usr/bin:/bin'})
        if completed.returncode or completed.stdout.strip() != 'ALT4_CONFINED_OK':
            raise RuntimeError('confined system Python smoke did not pass')
        return receipt



def case_status(cases, name):
    found = [case for case in cases if case.get('name') == name]
    if any(c.find('failure') is not None or c.find('error') is not None for c in found):
        return 'FAIL'
    if not found or any(c.find('skipped') is not None for c in found):
        return 'BLOCKED'
    return 'PASS'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('verification/alt4-execution-report.json'))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'alter.execution-verification.v1', 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_sha256': fingerprint(root), 'platform': platform.system(), 'python': platform.python_version(),
              'provider_calls': 0, 'live_model_verified': False, 'v1_ready': False,
              'confinement': {'status': 'BLOCKED'}, 'checks': []}
    reason = None
    with tempfile.TemporaryDirectory(prefix='alt4-verify-', dir=output.parent) as temporary:
        directory = Path(temporary)
        try:
            confinement = probe(directory)
            report['confinement'] = {'status': 'PASS', 'receipt': confinement}
        except Exception as error:
            # No exception bodies: a future dependency might include credentials or paths.
            reason = {'code': 'unsupported_platform' if platform.system() != 'Linux' else 'confinement_unavailable', 'error_type': type(error).__name__,
                      'message': 'Real confinement probe did not complete successfully; project tests were not executed.'}
            report['confinement']['reason'] = reason
        if reason:
            for name in NAMES:
                report['checks'].append({'id': 'tests/test_specialist_execution.py::' + name,
                                         'status': 'BLOCKED', 'required_capability': CAPABILITY, 'reason': reason})
        else:
            junit = output.parent / (output.stem + '-junit.xml')
            log = output.parent / (output.stem + '-pytest.log')
            command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                       '--junitxml=' + str(junit),
                       *['tests/test_specialist_execution.py::' + name for name in NAMES]]
            env = dict(os.environ, PCIM_EMBEDDER='hashing-512', HF_HUB_OFFLINE='1', ALTER_REQUIRE_CONFINEMENT='1',
                       PCIM_HOME=str(directory / 'home'), TMPDIR=str(directory))
            try:
                with log.open('w') as stream:
                    result = subprocess.run(command, cwd=root, env=env, stdout=stream,
                                            stderr=subprocess.STDOUT, timeout=180)
                report['pytest_exit_code'] = result.returncode
                cases = ET.parse(junit).findall('.//testcase')
                for name in NAMES:
                    status = case_status(cases, name)
                    report['checks'].append({'id': 'tests/test_specialist_execution.py::' + name,
                                             'status': status, 'required_capability': CAPABILITY})
                report['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (junit, log)}
            except (OSError, subprocess.TimeoutExpired, ET.ParseError) as error:
                report['runner_error'] = type(error).__name__
                report['checks'] = [{'id': 'tests/test_specialist_execution.py::' + name,
                                     'status': 'BLOCKED', 'reason': {'code': 'runner_incomplete'}} for name in NAMES]
    statuses = [check['status'] for check in report['checks']]
    report['status'] = 'FAIL' if 'FAIL' in statuses else 'BLOCKED' if 'BLOCKED' in statuses else 'PASS'
    if report.get('pytest_exit_code', 0) != 0 and report['status'] == 'PASS':
        report['status'] = 'FAIL'
        report['runner_error'] = 'nonzero_pytest_exit_with_passing_cases'
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(output),
                      'counts': {status: statuses.count(status) for status in ('PASS', 'FAIL', 'BLOCKED')}}))
    return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
