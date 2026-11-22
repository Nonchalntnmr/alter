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


def main() -> int:
    with socket.socket(fileno=int(sys.argv[1])) as caller, selectors.DefaultSelector() as events:
        events.register(caller, selectors.EVENT_READ)
        if events.select(timeout=0):
            return 1
        try:
            # Applied in this single-threaded supervisor before spawning project code.
            # Limits supplement OS confinement; they do not constitute a sandbox.
            for kind, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_FSIZE, 64 * 1024 * 1024),
                                (resource.RLIMIT_NOFILE, 256),
                                (resource.RLIMIT_CPU, 3600)):
                _, hard = resource.getrlimit(kind)
                value = min(value, hard) if hard != resource.RLIM_INFINITY else value
                resource.setrlimit(kind, (value, value))
            if sys.platform == "linux":
                _, hard = resource.getrlimit(resource.RLIMIT_NPROC)
                processes = min(64, hard) if hard != resource.RLIM_INFINITY else 64
                resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))
                _, hard = resource.getrlimit(resource.RLIMIT_AS)
                memory = 2 * 1024 * 1024 * 1024
                memory = min(memory, hard) if hard != resource.RLIM_INFINITY else memory
                resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            tests = subprocess.Popen(sys.argv[2:], start_new_session=True)
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
