"""Encrypted specialist snapshots and restart ledger on the existing SQLite store.

All public operations require owner and project. CAS prevents lost updates. Ledger
entries are immutable through this interface; local database administrators can alter
SQLite. Persist an attempt *before* dispatch: recovery escalates unknown effects and
never treats a persisted intention as proof of execution. Approval decisions remain
TaskQueue's authority; snapshots only preserve references and waiting state.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .types import new_id


class RevisionConflict(ValueError):
    pass


class LeaseConflict(ValueError):
    pass


class IdempotencyConflict(ValueError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS specialist_runs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
 revision INTEGER NOT NULL, payload_ct BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS specialist_runs_scope ON specialist_runs(user_id,project_id);
CREATE TABLE IF NOT EXISTS specialist_revisions (
 run_id TEXT NOT NULL, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
 revision INTEGER NOT NULL, payload_ct BLOB NOT NULL,
 PRIMARY KEY(run_id,revision));
INSERT OR IGNORE INTO specialist_revisions
 SELECT id,user_id,project_id,revision,payload_ct FROM specialist_runs;
CREATE TABLE IF NOT EXISTS specialist_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
 user_id TEXT NOT NULL, project_id TEXT NOT NULL, idem TEXT NOT NULL,
 payload_ct BLOB NOT NULL, UNIQUE(run_id,idem));
CREATE TABLE IF NOT EXISTS specialist_leases (
 user_id TEXT NOT NULL, project_id TEXT NOT NULL, resource TEXT NOT NULL,
 holder TEXT NOT NULL, fence INTEGER NOT NULL, expires REAL NOT NULL,
 PRIMARY KEY(user_id,project_id,resource));
"""


class RunStateStore:
    def __init__(self, store):
        self.store = store
        with store._lock:
            store._db.executescript(_SCHEMA)

    @contextmanager
    def _transaction(self):
        with self.store._lock:
            self.store.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.store.execute("COMMIT")
            except BaseException:
                self.store.execute("ROLLBACK")
                raise

    def _key(self, uid, pid):
        rows = self.store.query("SELECT key_id FROM projects WHERE id=? AND user_id=?", (pid, uid))
        if not rows:
            raise ValueError("project not found for owner")
        return rows[0]["key_id"]

    @staticmethod
    def _json(value):
        # Reject non-JSON values rather than silently converting execution evidence.
        return json.loads(json.dumps(value, allow_nan=False))

    def _aad(self, uid, pid, rid):
        return f"specialist:v1:{uid}:{pid}:{rid}"

    def create(self, user_id, project_id, snapshot):
        with self._transaction():
            key = self._key(user_id, project_id)
            value = self._json(snapshot)
            value.update(id=new_id("run"), revision=1, schema_version=1)
            self.store.execute("INSERT INTO specialist_runs VALUES (?,?,?,?,?)", (
                value["id"], user_id, project_id, 1,
                self.store.seal_json(key, value, self._aad(user_id, project_id, value["id"]))))
            self.store.execute("INSERT INTO specialist_revisions SELECT id,user_id,project_id,revision,payload_ct FROM specialist_runs WHERE id=?", (value["id"],))
            return value

    def get(self, user_id, project_id, run_id):
        with self.store._lock:
            key = self._key(user_id, project_id)
            rows = self.store.query(
                "SELECT * FROM specialist_runs WHERE id=? AND user_id=? AND project_id=?",
                (run_id, user_id, project_id))
            if not rows:
                raise ValueError("run not found for owner/project")
            return self.store.open_json(key, rows[0]["payload_ct"], self._aad(user_id, project_id, run_id))

    def list(self, user_id, project_id):
        with self.store._lock:
            self._key(user_id, project_id)
            return [self.get(user_id, project_id, r["id"]) for r in self.store.query(
                "SELECT id FROM specialist_runs WHERE user_id=? AND project_id=? ORDER BY id",
                (user_id, project_id))]

    def _save(self, uid, pid, rid, snapshot, expected_revision):
        old = self.get(uid, pid, rid)
        if old["revision"] != expected_revision:
            raise RevisionConflict("stale run revision")
        value = self._json(snapshot)
        # Generic snapshot saves must not erase/rewrite terminal receipts either.
        new_attempts = {a.get("id"): a for a in value.get("attempts", [])}
        for attempt in old.get("attempts", []):
            if (attempt.get("status") in {"succeeded", "failed", "cancelled", "escalated"}
                    and new_attempts.get(attempt.get("id")) != attempt):
                raise IdempotencyConflict("terminal attempt is immutable")
        attempts = value.get("attempts", [])
        ids = [a.get("id") for a in attempts]
        if len(ids) != len(set(ids)):
            raise IdempotencyConflict("duplicate attempt identity")
        for attempt in attempts:
            action = attempt.get("action_key")
            if not action:
                continue  # Legacy/coordinator snapshot: use record_attempt for dispatch.
            if attempt.get("status") == "succeeded" and not attempt.get("evidence_refs"):
                raise ValueError("success requires outcome provenance")
            for prior in old.get("attempts", []):
                if prior.get("id") == attempt.get("id") and prior.get("action_key") != action:
                    raise IdempotencyConflict("attempt action key changed")
                if (prior.get("id") != attempt.get("id") and prior.get("action_key") == action
                        and prior.get("status") in {"succeeded", "started", "interrupted", "escalated"}):
                    raise IdempotencyConflict("action completed or requires reconciliation")
        value.update(id=rid, revision=expected_revision + 1, schema_version=1)
        ct = self.store.seal_json(self._key(uid, pid), value, self._aad(uid, pid, rid))
        cur = self.store.execute(
            "UPDATE specialist_runs SET revision=?,payload_ct=? WHERE id=? AND user_id=? AND project_id=? AND revision=?",
            (value["revision"], ct, rid, uid, pid, expected_revision))
        if cur.rowcount != 1:
            raise RevisionConflict("stale run revision")
        self.store.execute("INSERT INTO specialist_revisions VALUES (?,?,?,?,?)",
                           (rid, uid, pid, value["revision"], ct))
        return value

    def history(self, user_id, project_id, run_id, after_revision=0, limit=100):
        """Bounded immutable snapshot revisions, including the current revision."""
        if after_revision < 0 or not 1 <= limit <= 1000:
            raise ValueError("history requires bounded limit and nonnegative revision")
        with self.store._lock:
            self.get(user_id, project_id, run_id)
            key = self._key(user_id, project_id)
            return [self.store.open_json(key, row["payload_ct"], self._aad(user_id, project_id, run_id))
                    for row in self.store.query(
                        "SELECT payload_ct FROM specialist_revisions WHERE run_id=? AND user_id=? AND project_id=? AND revision>? ORDER BY revision LIMIT ?",
                        (run_id, user_id, project_id, after_revision, limit))]

    def save(self, user_id, project_id, run_id, snapshot, expected_revision):
        with self._transaction():
            return self._save(user_id, project_id, run_id, snapshot, expected_revision)

    @contextmanager
    def lock(self, user_id, project_id):
        """Nonblocking process lock; kernel releases on death. Hold across dispatch.

        File-backed stores coordinate processes; in-memory stores coordinate threads.
        Leases below are separate durable fencing records, not substitutes for this lock.
        """
        with self.store._lock:
            self._key(user_id, project_id)
            if self.store.path == ":memory:":
                yield
                return
            digest = hashlib.sha256(f"{user_id}:{project_id}".encode()).hexdigest()
            path = Path(self.store.path).parent / f".specialist-{digest}.lock"
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise LeaseConflict("project worker already active") from exc
                yield
            finally:
                os.close(fd)

    def append_event(self, user_id, project_id, run_id, event, idempotency_key):
        """Append once. Reusing a key for different content is an error."""
        if not idempotency_key:
            raise ValueError("stable idempotency key required")
        with self._transaction():
            self.get(user_id, project_id, run_id)
            value = self._json(event)
            rows = self.store.query("SELECT * FROM specialist_events WHERE run_id=? AND idem=?",
                                    (run_id, idempotency_key))
            aad = self._aad(user_id, project_id, run_id) + ":event:" + idempotency_key
            key = self._key(user_id, project_id)
            if rows:
                old = self.store.open_json(key, rows[0]["payload_ct"], aad)
                if old != value:
                    raise IdempotencyConflict("event key already used with different content")
                return rows[0]["seq"]
            parent = value.get("parent_seq")
            if parent is not None and not self.store.query(
                    "SELECT seq FROM specialist_events WHERE seq=? AND run_id=?", (parent, run_id)):
                raise ValueError("causal parent must belong to this run")
            return self.store.execute("INSERT INTO specialist_events(run_id,user_id,project_id,idem,payload_ct) VALUES (?,?,?,?,?)",
                                      (run_id, user_id, project_id, idempotency_key,
                                       self.store.seal_json(key, value, aad))).lastrowid

    def replay(self, user_id, project_id, run_id, after_seq=0, limit=100):
        if not 1 <= limit <= 1000 or after_seq < 0:
            raise ValueError("replay requires bounded limit and nonnegative cursor")
        with self.store._lock:
            self.get(user_id, project_id, run_id)
            key = self._key(user_id, project_id)
            return [{"seq": r["seq"], "event": self.store.open_json(key, r["payload_ct"],
                     self._aad(user_id, project_id, run_id) + ":event:" + r["idem"])}
                    for r in self.store.query(
                        "SELECT * FROM specialist_events WHERE user_id=? AND project_id=? AND run_id=? AND seq>? ORDER BY seq LIMIT ?",
                        (user_id, project_id, run_id, after_seq, limit))]

    def claim_lease(self, user_id, project_id, resource, holder, ttl_s=60):
        if not resource or not holder or not 0 < ttl_s <= 3600:
            raise ValueError("resource, holder and bounded positive TTL required")
        with self._transaction():
            self._key(user_id, project_id)
            now = time.time()
            rows = self.store.query("SELECT * FROM specialist_leases WHERE user_id=? AND project_id=? AND resource=?",
                                    (user_id, project_id, resource))
            if rows and rows[0]["expires"] > now:
                raise LeaseConflict("resource already leased")
            fence = rows[0]["fence"] + 1 if rows else 1
            self.store.execute("INSERT OR REPLACE INTO specialist_leases VALUES (?,?,?,?,?,?)",
                               (user_id, project_id, resource, holder, fence, now + ttl_s))
            return fence

    def check_lease(self, user_id, project_id, resource, holder, fence):
        """Recheck immediately before dispatch; consumers must enforce fence on writes."""
        with self.store._lock:
            self._key(user_id, project_id)
            rows = self.store.query(
                "SELECT 1 FROM specialist_leases WHERE user_id=? AND project_id=? AND resource=? AND holder=? AND fence=? AND expires>?",
                (user_id, project_id, resource, holder, fence, time.time()))
            if not rows:
                raise LeaseConflict("expired or stale lease token")

    def release_lease(self, user_id, project_id, resource, holder, fence):
        with self._transaction():
            self._key(user_id, project_id)
            cur = self.store.execute("UPDATE specialist_leases SET expires=0 WHERE user_id=? AND project_id=? AND resource=? AND holder=? AND fence=?",
                                     (user_id, project_id, resource, holder, fence))
            if cur.rowcount != 1:
                raise LeaseConflict("stale lease token")

    def record_attempt(self, user_id, project_id, run_id, attempt, expected_revision):
        """Immutable attempt identity and action key; terminal outcomes cannot be replayed.

        A retry needs a new attempt id, but retains its action_key. A completed action
        blocks another attempt for that key. Success requires executor evidence refs.
        """
        value = self._json(attempt)
        if not value.get("id") or not value.get("action_key"):
            raise ValueError("attempt id and stable action_key required")
        status = value.get("status")
        if status not in {"proposed", "started", "awaiting_approval", "interrupted", "succeeded", "failed", "cancelled", "escalated"}:
            raise ValueError("invalid attempt status")
        if status == "succeeded" and not value.get("evidence_refs"):
            raise ValueError("success requires outcome provenance")
        with self._transaction():
            snapshot = self.get(user_id, project_id, run_id)
            attempts = snapshot.setdefault("attempts", [])
            for i, old in enumerate(attempts):
                if old["id"] == value["id"]:
                    if old["action_key"] != value["action_key"]:
                        raise IdempotencyConflict("attempt action key changed")
                    if old == value:
                        return snapshot
                    if old["status"] in {"succeeded", "failed", "cancelled", "escalated"}:
                        raise IdempotencyConflict("terminal attempt is immutable")
                    attempts[i] = value
                    break
                if old["action_key"] == value["action_key"] and old["status"] in {"succeeded", "started", "interrupted", "escalated"}:
                    raise IdempotencyConflict("action completed or requires reconciliation")
            else:
                attempts.append(value)
            return self._save(user_id, project_id, run_id, snapshot, expected_revision)

    def reconcile(self, user_id, project_id, run_id, expected_revision):
        """Acquires project lock after worker death; never auto-repeat effects."""
        with self.lock(user_id, project_id), self._transaction():
            snapshot = self.get(user_id, project_id, run_id)
            snapshot = copy.deepcopy(snapshot)
            ambiguous = False
            for attempt in snapshot.get("attempts", []):
                if attempt["status"] in {"started", "interrupted"}:
                    attempt.update(status="escalated", recovery_reason="unknown execution outcome; reconcile tool receipts before retry")
                    ambiguous = True
            if ambiguous:
                snapshot["status"] = "escalated"
            return self._save(user_id, project_id, run_id, snapshot, expected_revision)
