"""Back up and restore ALTER's data folder ($PCIM_HOME), consistently (safety audit R7).

A plain `cp pcim.db`, taken while another process has the store open, can miss committed data that
is still in the write-ahead log. The audit lost 1 of 4 memory items that way. So:

- **Backup** copies the database through SQLite's online backup API, which is consistent even
  while the store is in use. Alongside it go the token, the goal lock and log files (ids and
  statuses only) and the probe record.
  - It writes into a new folder that is the owner's only (0700, files 0600), with a manifest
    giving each file's sha256.
  - **master.key is left out unless you ask for it.** A backup holding the key opens every sealed
    record in it. Keep the key apart, for example in a password manager.
  - A goal that is running is refused unless forced: its next checkpoint would not be in the copy.
- **Restore** writes only into an empty or new folder, never over existing data. It then opens
  the restored store and checks the audit chain. Projects are found by their absolute folders, so
  restore on the same paths.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from .types import utcnow

PLAIN = ("token", "cli_identity_probe.json", "mesh_bench.json")
KEY = "master.key"


class BackupError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def live_goals(home: Path) -> list[str]:
    """Goals a worker holds right now (the goals' OS locks), found without opening the store."""
    out = []
    for lock in sorted((home / "goals").glob("*.lock")) if (home / "goals").is_dir() else []:
        with open(lock, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                out.append(lock.stem)
                continue
            fcntl.flock(f, fcntl.LOCK_UN)
    # Specialist workers use kernel-released project locks next to pcim.db.
    for lock in sorted(home.glob(".specialist-*.lock")):
        with open(lock, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                out.append(lock.stem)
                continue
            fcntl.flock(f, fcntl.LOCK_UN)
    return out


def _empty_or_new(p: Path) -> None:
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise BackupError(f"{p} exists and is not empty; choose a new folder (nothing is ever written over)")


def backup(home: Path, dest: Path, *, with_key: bool = False, force: bool = False) -> dict:
    home, dest = Path(home), Path(dest)
    if not (home / "pcim.db").is_file():
        raise BackupError(f"no ALTER data at {home}")
    running = live_goals(home)
    if running and not force:
        raise BackupError(f"goal(s) running: {', '.join(running)}; pause them (alter pause <goal>) or pass --force")
    _empty_or_new(dest)
    old = os.umask(0o077)
    try:
        dest.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(dest, 0o700)
        src = sqlite3.connect(f"file:{home / 'pcim.db'}?mode=ro", uri=True)
        dst = sqlite3.connect(dest / "pcim.db")
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        files = ["pcim.db"]
        for name in PLAIN + ((KEY,) if with_key else ()):
            if (home / name).is_file():
                shutil.copy2(home / name, dest / name)
                files.append(name)
        if (home / "goals").is_dir():
            shutil.copytree(home / "goals", dest / "goals")
            files += [str(p.relative_to(dest)) for p in sorted((dest / "goals").rglob("*")) if p.is_file()]
        for f in files:
            os.chmod(dest / f, 0o600)
        manifest = {"created_at": utcnow().isoformat(), "with_key": with_key, "running_goals_at_backup": running,
                    "files": {f: _sha(dest / f) for f in files}}
        (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
        os.chmod(dest / "MANIFEST.json", 0o600)
    finally:
        os.umask(old)
    return manifest


def restore(src: Path, home: Path, *, key: Path | None = None) -> dict:
    """Copy a backup into an empty or new data folder, then open it and check it."""
    src, home = Path(src), Path(home)
    mf = src / "MANIFEST.json"
    if not mf.is_file():
        raise BackupError(f"{src} has no MANIFEST.json: not an ALTER backup")
    manifest = json.loads(mf.read_text())
    bad = [f for f, h in manifest["files"].items() if not (src / f).is_file() or _sha(src / f) != h]
    if bad:
        raise BackupError(f"the backup is damaged or incomplete: {', '.join(bad[:5])}")
    key_src = key if key is not None else (src / KEY if (src / KEY).is_file() else None)
    if key_src is None or not Path(key_src).is_file():
        raise BackupError("this backup holds no master.key; pass --key <path to the master.key you kept apart>")
    _empty_or_new(home)
    old = os.umask(0o077)
    try:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        for f in manifest["files"]:
            if f == KEY:
                continue
            (home / f).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(src / f, home / f)
            os.chmod(home / f, 0o600)
        shutil.copy2(key_src, home / KEY)
        os.chmod(home / KEY, 0o600)
    finally:
        os.umask(old)
    from .engine import PersonalCI
    ci = PersonalCI(home)
    try:
        ok, err = ci.audit.verify()
        n_items = ci.store.query("SELECT COUNT(*) FROM items")[0][0]
    finally:
        ci.store.close()
    if not ok:
        raise BackupError(f"restored, but the audit chain does not verify: {err}")
    return {"home": str(home), "audit_chain": "verified", "memory_rows": int(n_items), "files": len(manifest["files"])}
