"""Fail-closed Linux bubblewrap adapter for untrusted project tests.

No host execution fallback. Tests run in a disposable copy, without home, ambient
credentials, host network/process namespaces or writable host mounts. Host security
policy is never changed. macOS/Windows currently report confinement unavailable.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .governor import is_secret


class SandboxUnavailable(RuntimeError):
    pass


def _binary() -> str:
    if sys.platform != "linux":
        raise SandboxUnavailable("verified test confinement unavailable on this platform")
    for value in ("/usr/bin/bwrap", "/bin/bwrap"):
        p = Path(value).resolve()
        if not p.is_file():
            continue
        # Never trust a project/PATH-provided sandbox shim or writable parent directory.
        if all(q.stat().st_uid == 0 and not q.stat().st_mode & 0o022 for q in (p, *p.parents)):
            return str(p)
    raise SandboxUnavailable("verified test confinement requires a trusted Linux bubblewrap installation")


def _prefix(binary: str, work: Path) -> list[str]:
    args = [binary, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp/home",
            "--setenv", "TMPDIR", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    for path in ("/usr/bin", "/usr/lib", "/usr/lib64", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
             "--bind", str(work), "/work", "--chdir", "/work", "--"]
    return args


def _probe(binary: str, work: Path, outside: Path) -> None:
    if not Path("/usr/bin/python3").is_file():
        raise SandboxUnavailable("confinement verification requires /usr/bin/python3")
    # Prove actual denial against a disposable canary; a version string is insufficient.
    script = """
import os, pathlib, sys
p = pathlib.Path(sys.argv[1])
assert not p.exists()
try:
    p.write_text('escaped')
except OSError:
    pass
else:
    raise AssertionError('outside write allowed')
assert os.readlink('/proc/self/ns/net') != sys.argv[2]
assert os.readlink('/proc/self/ns/pid') != sys.argv[3]
assert 'ALTER_PROBE_CREDENTIAL' not in os.environ
pathlib.Path('/work/probe-ok').write_text('ok')
"""
    env = {"PATH": "/usr/bin:/bin", "ALTER_PROBE_CREDENTIAL": "synthetic-only"}
    try:
        r = subprocess.run([*_prefix(binary, work), "/usr/bin/python3", "-I", "-c", script,
                            str(outside), os.readlink('/proc/self/ns/net'), os.readlink('/proc/self/ns/pid')],
                           check=False, env=env, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise SandboxUnavailable("confinement probe could not run") from None
    if r.returncode or outside.read_text() != "unchanged" or not (work / "probe-ok").is_file():
        raise SandboxUnavailable("confinement probe failed; project code was not executed")
    (work / "probe-ok").unlink()


def _snapshot(root: Path, dest: Path) -> None:
    from .tools import SKIP_DIRS
    count = size = 0
    for parent, dirs, files, parent_fd in os.fwalk(root, follow_symlinks=False):
        rel = Path(parent).relative_to(root)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not is_secret(rel / d)]
        for name in [*dirs, *files]:
            if stat.S_ISLNK(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode):
                raise SandboxUnavailable("test snapshot refuses symlinks")
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            if is_secret(rel / name):
                continue
            # Pin the containing directory and refuse a raced symlink/FIFO/device.
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            with os.fdopen(fd, "rb") as source:
                s = os.fstat(source.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                    raise SandboxUnavailable("test snapshot requires ordinary, unlinked files")
                count += 1
                size += s.st_size
                if count > 20000 or size > 100_000_000:
                    raise SandboxUnavailable("test snapshot exceeds bounded size")
                data = source.read(min(100_000_000 - size + s.st_size, s.st_size) + 1)
                if len(data) != s.st_size:
                    raise SandboxUnavailable("test snapshot changed while being copied")
            (dest / rel / name).write_bytes(data)
            (dest / rel / name).chmod(s.st_mode & 0o777)


@contextmanager
def confined_command(root: Path, argv: list[str]):
    binary = _binary()
    if not argv or not all(isinstance(a, str) and a and '\x00' not in a for a in argv):
        raise SandboxUnavailable("invalid test argv")
    # Absolute host/project paths cannot silently acquire a different meaning in /work.
    executable = argv[0]
    if '/' in executable and not executable.startswith(("/usr/bin/", "/bin/", "./")):
        raise SandboxUnavailable("test executable must be a system runner or relative project executable")
    with tempfile.TemporaryDirectory(prefix="alter-tests-") as temporary:
        base = Path(temporary)
        work = base / "work"
        work.mkdir()
        outside = base / "outside-canary"
        outside.write_text("unchanged")
        _probe(binary, work, outside)
        try:
            _snapshot(root, work)
        except OSError:
            raise SandboxUnavailable("test snapshot could not be verified") from None
        yield [*_prefix(binary, work), *argv], {
            "backend": "bubblewrap", "verified": True, "network": "isolated",
            "filesystem": "disposable-copy", "host_writes": False,
        }
