"""Run an approved test command and stop its process group if the caller exits."""

import contextlib
import json
import os
import resource
import selectors
import signal
import socket
import subprocess
import sys
import time


def _stop_tests(tests: subprocess.Popen) -> None:
    # The child has not been reaped, so its process group cannot be mistaken for a later one.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(tests.pid, signal.SIGTERM)
        time.sleep(0.2)
        os.killpg(tests.pid, signal.SIGKILL)
    tests.wait()


def _limit(kind: int, value: int) -> None:
    _, hard = resource.getrlimit(kind)
    value = min(value, hard) if hard != resource.RLIM_INFINITY else value
    resource.setrlimit(kind, (value, value))


def _confined_limits() -> None:
    # Applied in this single-threaded supervisor before spawning project code.
    # Limits supplement OS confinement; they do not constitute a sandbox.
    for kind, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_FSIZE, 64 * 1024 * 1024),
                        (resource.RLIMIT_NOFILE, 256), (resource.RLIMIT_CPU, 3600)):
        _limit(kind, value)
    if sys.platform == "linux":
        # RLIMIT_NPROC counts every process of the real user, inside the sandbox or not, so it
        # is a bound on runaway forking, set well above what a busy account already runs.
        _limit(resource.RLIMIT_NPROC, 4096)
        _limit(resource.RLIMIT_AS, 2 * 1024 * 1024 * 1024)


def main() -> int:
    # argv: <socket fd> <host|confined> <test command...>. A host run behaves as in V1: no limits.
    mode = sys.argv[2] if len(sys.argv) > 2 else ""
    with socket.socket(fileno=int(sys.argv[1])) as caller, selectors.DefaultSelector() as events:
        events.register(caller, selectors.EVENT_READ)
        if events.select(timeout=0):
            return 1
        if mode not in ("host", "confined") or len(sys.argv) < 4:
            caller.sendall(json.dumps({"error": "test supervisor: bad arguments"}).encode())
            return 1
        try:
            if mode == "confined":
                _confined_limits()
            tests = subprocess.Popen(sys.argv[3:], start_new_session=True)
        except OSError as error:
            caller.sendall(json.dumps({"error": str(error)[:500]}).encode())
            return 1
        try:
            while not events.select(timeout=0.1):
                if tests.poll() is not None:
                    break
        finally:
            if tests.returncode is None:
                _stop_tests(tests)
        with contextlib.suppress(OSError):
            caller.sendall(json.dumps({"exit_code": tests.returncode}).encode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
