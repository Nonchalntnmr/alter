"""Local-first SQLite store.

Why SQLite and not Postgres for the prototype: Personal CI's default deployment is the
user's own machine, where a single-file database with no server is the sovereignty
argument made concrete — nothing to administer, nothing listening on a port, and the
whole store moves with `cp`. The hosted multi-tenant deployment (docs/05) uses Postgres
with pgvector and row-level security; this module's surface is what that backend must
implement.

What is plaintext and what is not:
- Encrypted (per-record key, see sovereignty/crypto.py): item content, key, value, tags,
  provenance, embeddings; task and response text; feedback comments; policy posteriors;
  skill state; the user's display name.
- Plaintext: ids, kinds, coarse source labels, trust, sensitivity, timestamps, token
  counts, costs, consent grants, claim status and author labels, exchange sender and
  recipient labels, and the audit log, which never contains content.

secure_delete is on and the WAL is truncated after every deletion, so a deleted row's
wrapped key does not linger in free pages or the WAL file.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .sovereignty.crypto import KeyRing, seal, unseal
from .types import MemoryItem, Sensitivity, Trust, new_id, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keys (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  wrapped BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS keys_user ON keys(user_id);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  key_id TEXT NOT NULL,
  profile_ct BLOB,
  settings TEXT NOT NULL DEFAULT '{}',
  token_hash TEXT
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  trust TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  confidence REAL NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  recorded_at TEXT NOT NULL,
  superseded_at TEXT,
  superseded_by TEXT,
  status TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  parents TEXT NOT NULL DEFAULT '[]',
  flags TEXT NOT NULL DEFAULT '[]',
  tokens INTEGER NOT NULL,
  payload_ct BLOB NOT NULL,
  embedding_ct BLOB
);
CREATE INDEX IF NOT EXISTS items_user ON items(user_id);

CREATE TABLE IF NOT EXISTS conflicts (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  item_a TEXT NOT NULL,
  item_b TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  status TEXT NOT NULL,
  resolution TEXT
);

CREATE TABLE IF NOT EXISTS grants (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source TEXT NOT NULL,
  purpose TEXT NOT NULL,
  provider TEXT NOT NULL,
  max_sensitivity TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  expires_at TEXT,
  revoked_at TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  task_type TEXT,
  provider TEXT,
  model TEXT,
  policy TEXT,
  policy_version INTEGER,
  assist_mode TEXT,
  selected TEXT NOT NULL,
  propensities TEXT NOT NULL,
  x_vector TEXT,
  tokens_context INTEGER,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cache_read INTEGER,
  cost_usd REAL,
  latency_ms REAL,
  stop_reason TEXT,
  packet_hash TEXT,
  payload_ct BLOB,
  reward REAL,
  feedback_ct BLOB,
  feedback_at TEXT
);
CREATE INDEX IF NOT EXISTS interactions_user ON interactions(user_id, ts);

CREATE TABLE IF NOT EXISTS policy_states (
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  parent INTEGER,
  note TEXT,
  active INTEGER NOT NULL DEFAULT 0,
  state_ct BLOB NOT NULL,
  PRIMARY KEY (user_id, name, version)
);

CREATE TABLE IF NOT EXISTS skills (
  user_id TEXT NOT NULL,
  skill TEXT NOT NULL,
  state_ct BLOB NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, skill)
);

-- Workspace slice. Project content, transcripts, task arguments and results are sealed
-- with the project's own key: deleting a project destroys that key.
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_ct BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  state_ct BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions(project_id, created_at);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  session_id TEXT,
  tool TEXT NOT NULL,
  risk TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  tool_use_id TEXT,
  authorization_id TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  approved_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  duration_ms REAL,
  args_ct BLOB NOT NULL,
  result_ct BLOB
);
CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project_id, created_at);
CREATE TABLE IF NOT EXISTS authorizations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT,
  kind TEXT NOT NULL,
  spec TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  revoked_at TEXT,
  first_used_at TEXT,
  used_actions INTEGER NOT NULL DEFAULT 0
);

-- Claim and evidence ledger, and the context exchange between models. Sealed with the
-- project key like everything else a project says; status, author label and the
-- sender/recipient labels stay plaintext so the ledger can be listed without decrypting.
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  author TEXT NOT NULL,
  payload_ct BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_project ON claims(project_id, created_at);
CREATE TABLE IF NOT EXISTS exchange_messages (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  recipient TEXT NOT NULL,
  created_at TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  payload_ct BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS exchange_project ON exchange_messages(project_id, created_at);
"""


def _ts(t: dt.datetime | None) -> str | None:
    return t.isoformat() if t is not None else None


def _dt(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str | Path, keyring: KeyRing):
        self.path = str(path)
        self.keyring = keyring
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            # Contents are encrypted, but ids, timestamps, grants and the audit log are not:
            # owner-only. SQLite gives the -wal/-shm files the database file's permissions.
            os.chmod(self.path, 0o600)
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.executescript(_SCHEMA)
        self._dek_cache: dict[str, bytes] = {}
        # Decrypted items per user. Cleared on every item write through this Store, and whenever
        # SQLite's data_version shows another connection committed (a CLI next to the server),
        # so a retracted or deleted item is never served from it.
        self._items_cache: dict[str, list[MemoryItem]] = {}
        self._own_writes = 0
        self._data_version: int | None = None

    def items_generation(self) -> tuple[int, int]:
        """Changes whenever any item may have changed, here or in another process."""
        with self._lock:
            dv = self._db.execute("PRAGMA data_version").fetchone()[0]
            if dv != self._data_version:
                self._data_version = dv
                self._items_cache.clear()
            return dv, self._own_writes

    def _items_changed(self) -> None:
        self._own_writes += 1
        self._items_cache.clear()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- keys
    def _new_key(self, user_id: str) -> tuple[str, bytes]:
        key_id = new_id("key")
        dek, wrapped = self.keyring.new_dek()
        self._db.execute(
            "INSERT INTO keys(id, user_id, wrapped) VALUES (?,?,?)", (key_id, user_id, wrapped)
        )
        self._dek_cache[key_id] = dek
        return key_id, dek

    def _dek(self, key_id: str) -> bytes:
        dek = self._dek_cache.get(key_id)
        if dek is None:
            row = self._db.execute("SELECT wrapped FROM keys WHERE id=?", (key_id,)).fetchone()
            if row is None:
                raise KeyError(f"key {key_id} has been destroyed")
            dek = self.keyring.unwrap(row["wrapped"])
            self._dek_cache[key_id] = dek
        return dek

    def _destroy_keys(self, key_ids: Iterable[str]) -> None:
        for k in key_ids:
            self._db.execute("DELETE FROM keys WHERE id=?", (k,))
            self._dek_cache.pop(k, None)

    def _flush_deleted(self) -> None:
        if self.path != ":memory:":
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ---------------------------------------------------------------- users
    def create_user(self, user_id: str, display_name: str = "", settings: dict | None = None) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                key_id, dek = self._new_key(user_id)
                profile = seal(dek, json.dumps({"display_name": display_name}).encode(), user_id)
                self._db.execute(
                    "INSERT INTO users(id, created_at, key_id, profile_ct, settings) VALUES (?,?,?,?,?)",
                    (user_id, _ts(utcnow()), key_id, profile, json.dumps(settings or {})),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                return None
            profile = json.loads(unseal(self._dek(row["key_id"]), row["profile_ct"], user_id))
            return {
                "id": row["id"],
                "created_at": row["created_at"],
                "display_name": profile.get("display_name", ""),
                "settings": json.loads(row["settings"]),
            }

    def list_user_ids(self) -> list[str]:
        with self._lock:
            return [r["id"] for r in self._db.execute("SELECT id FROM users ORDER BY created_at")]

    def update_settings(self, user_id: str, settings: dict) -> None:
        with self._lock:
            self._db.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps(settings), user_id))

    def set_token_hash(self, user_id: str, token_hash: str) -> None:
        with self._lock:
            self._db.execute("UPDATE users SET token_hash=? WHERE id=?", (token_hash, user_id))

    def user_for_token_hash(self, token_hash: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT id FROM users WHERE token_hash=?", (token_hash,)).fetchone()
            return row["id"] if row else None

    def _user_dek(self, user_id: str) -> bytes:
        row = self._db.execute("SELECT key_id FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown user {user_id}")
        return self._dek(row["key_id"])

    def delete_user(self, user_id: str) -> dict:
        """Erase a user entirely: every row, then every key. Returns counts."""
        with self._lock:
            counts = {}
            self._db.execute("BEGIN")
            try:
                key_ids = [r["id"] for r in self._db.execute("SELECT id FROM keys WHERE user_id=?", (user_id,))]
                for table in ("items", "conflicts", "grants", "interactions", "policy_states", "skills",
                              "projects", "sessions", "tasks", "authorizations", "claims", "exchange_messages"):
                    cur = self._db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
                    counts[table] = cur.rowcount
                self._db.execute("DELETE FROM users WHERE id=?", (user_id,))
                self._destroy_keys(key_ids)
                counts["keys"] = len(key_ids)
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            finally:
                self._items_changed()
            self._flush_deleted()
            return counts

    # ---------------------------------------------------------------- items
    def insert_item(self, item: MemoryItem, embedding: np.ndarray | None = None) -> None:
        payload = json.dumps(
            {
                "content": item.content,
                "key": item.key,
                "value": item.value,
                "tags": list(item.tags),
                "provenance": item.provenance,
            }
        ).encode()
        with self._lock:
            self._db.execute("BEGIN")
            try:
                key_id, dek = self._new_key(item.user_id)
                emb_ct = (
                    seal(dek, np.asarray(embedding, dtype=np.float32).tobytes(), item.id + "/emb")
                    if embedding is not None
                    else None
                )
                self._db.execute(
                    """INSERT INTO items(id, user_id, key_id, kind, source, trust, sensitivity,
                       confidence, valid_from, valid_to, recorded_at, superseded_at, superseded_by,
                       status, pinned, parents, flags, tokens, payload_ct, embedding_ct)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.id, item.user_id, key_id, item.kind, item.source, item.trust.value,
                        item.sensitivity.value, item.confidence, _ts(item.valid_from), _ts(item.valid_to),
                        _ts(item.recorded_at), _ts(item.superseded_at), item.superseded_by, item.status,
                        int(item.pinned), json.dumps(list(item.parents)), json.dumps(list(item.flags)),
                        item.tokens, seal(dek, payload, item.id), emb_ct,
                    ),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            finally:
                self._items_changed()

    _META_FIELDS = {
        "valid_to", "superseded_at", "superseded_by", "status", "pinned", "confidence",
        "flags", "trust", "kind", "sensitivity",
    }

    def update_item_meta(self, item_id: str, **fields: Any) -> None:
        bad = set(fields) - self._META_FIELDS
        if bad:
            raise ValueError(f"not updatable in place (content is immutable): {sorted(bad)}")
        cols, vals = [], []
        for k, v in fields.items():
            if isinstance(v, dt.datetime):
                v = _ts(v)
            elif isinstance(v, (Trust, Sensitivity)):
                v = v.value
            elif k == "flags":
                v = json.dumps(list(v))
            elif k == "pinned":
                v = int(v)
            cols.append(f"{k}=?")
            vals.append(v)
        with self._lock:
            try:
                self._db.execute(f"UPDATE items SET {', '.join(cols)} WHERE id=?", (*vals, item_id))
            finally:
                self._items_changed()

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        payload = json.loads(unseal(self._dek(row["key_id"]), row["payload_ct"], row["id"]))
        return MemoryItem(
            id=row["id"], user_id=row["user_id"], kind=row["kind"], content=payload["content"],
            source=row["source"], trust=Trust(row["trust"]), sensitivity=Sensitivity(row["sensitivity"]),
            confidence=row["confidence"], valid_from=_dt(row["valid_from"]), recorded_at=_dt(row["recorded_at"]),
            key=payload.get("key"), value=payload.get("value"), valid_to=_dt(row["valid_to"]),
            superseded_at=_dt(row["superseded_at"]), superseded_by=row["superseded_by"], status=row["status"],
            pinned=bool(row["pinned"]), parents=tuple(json.loads(row["parents"])),
            tags=tuple(payload.get("tags", ())), flags=tuple(json.loads(row["flags"])),
            provenance=payload.get("provenance", {}), tokens=row["tokens"],
        )

    def get_item(self, item_id: str) -> MemoryItem | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._row_to_item(row) if row else None

    def list_items(self, user_id: str) -> list[MemoryItem]:
        with self._lock:
            self.items_generation()  # drops the cache if another connection wrote
            cached = self._items_cache.get(user_id)
            if cached is None:
                rows = self._db.execute(
                    "SELECT * FROM items WHERE user_id=? ORDER BY recorded_at", (user_id,)
                ).fetchall()
                cached = self._items_cache[user_id] = [self._row_to_item(r) for r in rows]
            return [copy.copy(i) for i in cached]  # callers may mutate their copies

    def item_embeddings(self, user_id: str) -> tuple[list[str], np.ndarray | None]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, key_id, embedding_ct FROM items WHERE user_id=? AND embedding_ct IS NOT NULL",
                (user_id,),
            ).fetchall()
            if not rows:
                return [], None
            vecs = [
                np.frombuffer(unseal(self._dek(r["key_id"]), r["embedding_ct"], r["id"] + "/emb"), dtype=np.float32)
                for r in rows
            ]
            return [r["id"] for r in rows], np.vstack(vecs)

    def count_items(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def reembed_items(self, embed, batch: int = 256) -> int:
        """Replace every item's embedding with `embed(contents)`, in one transaction.

        Needed when the embedder changes: vectors from two models are not comparable, and a
        store that mixed them would rank by noise without any error."""
        with self._lock:
            rows = self._db.execute("SELECT id, key_id, payload_ct FROM items").fetchall()
            self._db.execute("BEGIN")
            try:
                for start in range(0, len(rows), batch):
                    chunk = rows[start:start + batch]
                    texts = [json.loads(unseal(self._dek(r["key_id"]), r["payload_ct"], r["id"]))["content"] for r in chunk]
                    for r, v in zip(chunk, embed(texts)):
                        ct = seal(self._dek(r["key_id"]), np.asarray(v, dtype=np.float32).tobytes(), r["id"] + "/emb")
                        self._db.execute("UPDATE items SET embedding_ct=? WHERE id=?", (ct, r["id"]))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            finally:
                self._items_changed()
            return len(rows)

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, value))

    def children_of(self, item_id: str) -> list[str]:
        """Items whose lineage names item_id as a parent (derived summaries, extractions)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT items.id FROM items, json_each(items.parents) WHERE json_each.value=?", (item_id,)
            ).fetchall()
            return [r["id"] for r in rows]

    def delete_item(self, item_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT key_id FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return False
            self._db.execute("BEGIN")
            try:
                self._db.execute("DELETE FROM items WHERE id=?", (item_id,))
                self._db.execute(
                    "DELETE FROM conflicts WHERE item_a=? OR item_b=?", (item_id, item_id)
                )
                self._destroy_keys([row["key_id"]])
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            finally:
                self._items_changed()
            self._flush_deleted()
            return True

    # ---------------------------------------------------------------- conflicts
    def add_conflict(self, user_id: str, a: str, b: str) -> str:
        cid = new_id("cfl")
        with self._lock:
            self._db.execute(
                "INSERT INTO conflicts VALUES (?,?,?,?,?,?,?)",
                (cid, user_id, a, b, _ts(utcnow()), "open", None),
            )
        return cid

    def list_conflicts(self, user_id: str, status: str | None = "open") -> list[dict]:
        q, args = "SELECT * FROM conflicts WHERE user_id=?", [user_id]
        if status:
            q += " AND status=?"
            args.append(status)
        with self._lock:
            return [dict(r) for r in self._db.execute(q, args)]

    def resolve_conflict(self, conflict_id: str, resolution: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE conflicts SET status='resolved', resolution=? WHERE id=?", (resolution, conflict_id)
            )

    # ---------------------------------------------------------------- grants
    def insert_grant(self, g: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    g["id"], g["user_id"], g["source"], g["purpose"], g["provider"], g["max_sensitivity"],
                    g["granted_at"], g.get("expires_at"), g.get("revoked_at"), g.get("note"),
                ),
            )

    def list_grants(self, user_id: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM grants WHERE user_id=? ORDER BY granted_at", (user_id,)
            )]

    def revoke_grant(self, grant_id: str, when: dt.datetime) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE grants SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (_ts(when), grant_id)
            )
            return cur.rowcount > 0

    # ---------------------------------------------------------------- audit
    def last_audit_hash(self) -> str:
        with self._lock:
            row = self._db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            return row["hash"] if row else "0" * 64

    def insert_audit(self, user_id: str, ts: str, event: str, payload: str, prev_hash: str, h: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO audit(user_id, ts, event, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                (user_id, ts, event, payload, prev_hash, h),
            )
            return cur.lastrowid

    def list_audit(self, user_id: str | None = None, limit: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM audit", []
        if user_id:
            q += " WHERE user_id=?"
            args.append(user_id)
        q += " ORDER BY seq"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            return [dict(r) for r in self._db.execute(q, args)]

    # ---------------------------------------------------------------- interactions
    def insert_interaction(self, rec: dict, task_text: str, response_text: str, receipt: dict) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                key_id, dek = self._new_key(rec["user_id"])
                payload = json.dumps({"task": task_text, "response": response_text, "receipt": receipt}).encode()
                self._db.execute(
                    """INSERT INTO interactions(id, user_id, key_id, ts, task_type, provider, model, policy,
                       policy_version, assist_mode, selected, propensities, x_vector, tokens_context,
                       tokens_in, tokens_out, cache_read, cost_usd, latency_ms, stop_reason, packet_hash,
                       payload_ct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rec["id"], rec["user_id"], key_id, rec["ts"], rec.get("task_type"), rec.get("provider"),
                        rec.get("model"), rec.get("policy"), rec.get("policy_version"), rec.get("assist_mode"),
                        json.dumps(rec.get("selected", [])), json.dumps(rec.get("propensities", [])),
                        json.dumps(rec.get("x_vector")), rec.get("tokens_context"), rec.get("tokens_in"),
                        rec.get("tokens_out"), rec.get("cache_read"), rec.get("cost_usd"), rec.get("latency_ms"),
                        rec.get("stop_reason"), rec.get("packet_hash"), seal(dek, payload, rec["id"]),
                    ),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def _row_to_interaction(self, row: sqlite3.Row, with_payload: bool) -> dict:
        d = {k: row[k] for k in row.keys() if k not in ("payload_ct", "feedback_ct", "key_id")}
        for k in ("selected", "propensities", "x_vector"):
            d[k] = json.loads(d[k]) if d[k] else None
        if with_payload:
            dek = self._dek(row["key_id"])
            d.update(json.loads(unseal(dek, row["payload_ct"], row["id"])))
            if row["feedback_ct"]:
                d["feedback"] = json.loads(unseal(dek, row["feedback_ct"], row["id"] + "/fb"))
        return d

    def get_interaction(self, interaction_id: str, with_payload: bool = True) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            return self._row_to_interaction(row, with_payload) if row else None

    def list_interactions(self, user_id: str, with_payload: bool = False, limit: int | None = None) -> list[dict]:
        q = "SELECT * FROM interactions WHERE user_id=? ORDER BY ts"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            return [self._row_to_interaction(r, with_payload) for r in self._db.execute(q, (user_id,))]

    def set_feedback(self, interaction_id: str, signals: dict, reward: float | None) -> None:
        with self._lock:
            row = self._db.execute("SELECT key_id FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            if row is None:
                raise KeyError(interaction_id)
            ct = seal(self._dek(row["key_id"]), json.dumps(signals).encode(), interaction_id + "/fb")
            self._db.execute(
                "UPDATE interactions SET reward=?, feedback_ct=?, feedback_at=? WHERE id=?",
                (reward, ct, _ts(utcnow()), interaction_id),
            )

    def delete_interaction(self, interaction_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT key_id FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            if row is None:
                return False
            self._db.execute("DELETE FROM interactions WHERE id=?", (interaction_id,))
            self._destroy_keys([row["key_id"]])
            self._flush_deleted()
            return True

    # ---------------------------------------------------------------- policy states
    def save_policy_state(self, user_id: str, name: str, state: bytes, note: str = "",
                          parent: int | None = None, activate: bool = True) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM policy_states WHERE user_id=? AND name=?",
                (user_id, name),
            ).fetchone()
            version = row["v"] + 1
            ct = seal(self._user_dek(user_id), state, f"{user_id}/{name}/{version}")
            self._db.execute("BEGIN")
            try:
                if activate:
                    self._db.execute(
                        "UPDATE policy_states SET active=0 WHERE user_id=? AND name=?", (user_id, name)
                    )
                self._db.execute(
                    "INSERT INTO policy_states VALUES (?,?,?,?,?,?,?,?)",
                    (user_id, name, version, _ts(utcnow()), parent, note, int(activate), ct),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            return version

    def delete_policy_states(self, user_id: str, name: str) -> int:
        with self._lock:
            return self._db.execute(
                "DELETE FROM policy_states WHERE user_id=? AND name=?", (user_id, name)
            ).rowcount

    def overwrite_policy_state(self, user_id: str, name: str, version: int, state: bytes) -> None:
        """Replace the state of an existing version in place (between snapshots)."""
        ct = seal(self._user_dek(user_id), state, f"{user_id}/{name}/{version}")
        with self._lock:
            cur = self._db.execute(
                "UPDATE policy_states SET state_ct=? WHERE user_id=? AND name=? AND version=?",
                (ct, user_id, name, version),
            )
            if cur.rowcount != 1:
                raise KeyError(f"no version {version} of {name} for {user_id}")

    def load_policy_state(self, user_id: str, name: str, version: int | None = None) -> tuple[int, bytes] | None:
        with self._lock:
            if version is None:
                row = self._db.execute(
                    "SELECT version, state_ct FROM policy_states WHERE user_id=? AND name=? AND active=1",
                    (user_id, name),
                ).fetchone()
            else:
                row = self._db.execute(
                    "SELECT version, state_ct FROM policy_states WHERE user_id=? AND name=? AND version=?",
                    (user_id, name, version),
                ).fetchone()
            if row is None:
                return None
            return row["version"], unseal(self._user_dek(user_id), row["state_ct"], f"{user_id}/{name}/{row['version']}")

    def list_policy_versions(self, user_id: str, name: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT version, created_at, parent, note, active FROM policy_states "
                "WHERE user_id=? AND name=? ORDER BY version", (user_id, name),
            )]

    def activate_policy_version(self, user_id: str, name: str, version: int) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            self._db.execute("UPDATE policy_states SET active=0 WHERE user_id=? AND name=?", (user_id, name))
            cur = self._db.execute(
                "UPDATE policy_states SET active=1 WHERE user_id=? AND name=? AND version=?",
                (user_id, name, version),
            )
            if cur.rowcount != 1:
                self._db.execute("ROLLBACK")
                raise KeyError(f"no version {version} of {name} for {user_id}")
            self._db.execute("COMMIT")

    # ---------------------------------------------------------------- sealed rows (workspace slice)
    def new_project_key(self, user_id: str) -> str:
        with self._lock:
            return self._new_key(user_id)[0]

    def seal_json(self, key_id: str, obj, aad: str) -> bytes:
        with self._lock:
            return seal(self._dek(key_id), json.dumps(obj, default=str).encode(), aad)

    def open_json(self, key_id: str, blob: bytes | None, aad: str):
        if blob is None:
            return None
        with self._lock:
            return json.loads(unseal(self._dek(key_id), blob, aad))

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        """Single statement under the store lock (workspace modules own their SQL)."""
        with self._lock:
            return self._db.execute(sql, args)

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def delete_project_rows(self, project_id: str, key_id: str) -> dict:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                counts = {t: self._db.execute(f"DELETE FROM {t} WHERE project_id=?", (project_id,)).rowcount
                          for t in ("sessions", "tasks", "authorizations", "claims", "exchange_messages")}
                counts["projects"] = self._db.execute("DELETE FROM projects WHERE id=?", (project_id,)).rowcount
                self._destroy_keys([key_id])
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            self._flush_deleted()
            return counts

    # ---------------------------------------------------------------- skills
    def put_skill(self, user_id: str, skill: str, state: dict) -> None:
        ct = seal(self._user_dek(user_id), json.dumps(state).encode(), f"{user_id}/skill/{skill}")
        with self._lock:
            self._db.execute(
                "INSERT INTO skills VALUES (?,?,?,?) ON CONFLICT(user_id, skill) "
                "DO UPDATE SET state_ct=excluded.state_ct, updated_at=excluded.updated_at",
                (user_id, skill, ct, _ts(utcnow())),
            )

    def delete_skill(self, user_id: str, skill: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM skills WHERE user_id=? AND skill=?", (user_id, skill))
            return cur.rowcount > 0

    def list_skills(self, user_id: str) -> dict[str, dict]:
        with self._lock:
            dek = self._user_dek(user_id)
            return {
                r["skill"]: json.loads(unseal(dek, r["state_ct"], f"{user_id}/skill/{r['skill']}"))
                for r in self._db.execute("SELECT * FROM skills WHERE user_id=?", (user_id,))
            }
