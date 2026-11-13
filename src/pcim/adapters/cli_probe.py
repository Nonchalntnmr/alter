"""Measure what the claude CLI actually sends to the model, through CI's own adapter.

The CLI is pointed at a loopback relay with ANTHROPIC_BASE_URL, for the probe's calls only.
- The relay records the JSON body of every /v1/messages request. That body is exactly the
  model's input.
- It forwards each request unchanged to https://api.anthropic.com and streams the answer back.
  So anything else the CLI does on its own (a profile lookup, say) happens for real.
- Request headers carry the login token. They are forwarded, never stored or printed.

The recorded system prompt and messages are checked for this machine's identifiers:
- the account email and its local part;
- the account's display name, organization name and account/organization ids;
- $USER and the home path;
- git's user.name and user.email.

Only presence is printed, never the values. API metadata (`metadata`, which the model does not
see) is reported separately.

Each trial is one real call on Haiku; a trial costs well under a cent.

    python -m pcim.adapters.cli_probe [--trials N] [--baseline] [--record]

With --record, the result for the installed CLI version is written to
$PCIM_HOME/cli_identity_probe.json. ClaudeCLIAdapter treats isolation as verified only for a
version recorded clean.
"""
from __future__ import annotations

import argparse
import datetime
import getpass
import http.client
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

from .claude_cli import PROBE_RECORD, ClaudeCLIAdapter, cli_version, profile_keys, sanitized_profile

UPSTREAM = "api.anthropic.com"
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host",
        "content-length", "accept-encoding"}


class Relay(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.bodies: list[dict] = []
        self.paths: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _forward(self, method: str):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        self.server.paths.append(f"{method} {self.path.split('?')[0]}")
        if method == "POST" and self.path.startswith("/v1/messages") and body:
            try:
                d = json.loads(body)
                self.server.bodies.append({k: d.get(k) for k in ("system", "messages", "metadata", "model")})
            except ValueError:
                pass
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=300)
        conn.request(method, self.path, body=body, headers=headers)
        r = conn.getresponse()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        while chunk := r.read1(65536):
            self.wfile.write(chunk)
            self.wfile.flush()
        conn.close()

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")


def markers() -> dict[str, str]:
    """This machine's identifiers, by label. The values are never printed or stored."""
    out: dict[str, str] = {}
    cfg = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") \
        else Path.home() / ".claude.json"
    try:
        acct = json.loads(cfg.read_text()).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        acct = {}
    if acct.get("emailAddress"):
        out["account email"] = acct["emailAddress"]
        local = acct["emailAddress"].split("@")[0]
        if len(local) >= 4:
            out["email local part"] = local
    for key, label in (("displayName", "account display name"), ("fullName", "account full name"),
                       ("organizationName", "organization name"), ("accountUuid", "account id"),
                       ("organizationUuid", "organization id")):
        if isinstance(acct.get(key), str) and len(acct[key]) >= 4:
            out[label] = acct[key]
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    if len(user) >= 3:
        out["username ($USER)"] = user
    out["home path"] = str(Path.home())
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ""
    if len(host) >= 4 and host.lower() not in ("localhost", "localhost.localdomain"):
        out["hostname"] = host
    for key, label in (("user.name", "git user.name"), ("user.email", "git user.email")):
        try:
            v = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            v = ""
        if len(v) >= 4:
            out[label] = v
    return out


def scan(bodies: list[dict], marks: dict[str, str]) -> dict:
    model_input = json.dumps([[b.get("system"), b.get("messages")] for b in bodies], ensure_ascii=False).lower()
    meta = json.dumps([b.get("metadata") for b in bodies], ensure_ascii=False).lower()
    return {"model_input": {k: v.lower() in model_input for k, v in marks.items()},
            "api_metadata": {k: v.lower() in meta for k, v in marks.items()}}


def trial(relay: Relay, marks: dict[str, str], isolate: bool, agent: bool = False) -> dict:
    from .base import RenderedPrompt

    def runner(argv, **kw):
        env = dict(kw.pop("env"))
        env["ANTHROPIC_BASE_URL"] = relay.url  # this probe's loopback relay, which forwards to Anthropic
        return subprocess.run(argv, env=env, **kw)

    relay.bodies.clear()
    relay.paths.clear()
    a = ClaudeCLIAdapter(model="haiku", runner=runner, max_budget_usd=0.05, isolate_identity=isolate,
                         require_isolation=False)
    err, cost, refetched = None, None, None
    try:
        if agent:  # the agent path: CI's protocol, a tool catalogue and a longer prompt
            tools = [{"name": "read_file", "description": "Read a file in the project.",
                      "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]
            r = a.agent_step("You help with a small project. Answer briefly.",
                             [{"role": "user", "content": "Who am I, and where are you running? Answer from what you were "
                                                          "given, then say OK."}], tools)
        else:
            r = a.generate(RenderedPrompt(system="You are a probe. Reply with the single word OK.", user="Say OK."))
        cost = r.cost_usd
        refetched = (r.provider_context or {}).get("profile_refetched_during_call")
    except Exception as e:  # noqa: BLE001 - reported, not hidden
        err = f"{type(e).__name__}: {str(e)[:200]}"
    s = scan(relay.bodies, marks)
    return {"isolate": isolate, "agent_path": agent, "error": err, "cost_usd": cost, "requests": len(relay.bodies),
            "paths": sorted(set(relay.paths)), "refetched": refetched,
            "found_in_model_input": sorted(k for k, v in s["model_input"].items() if v),
            "found_in_api_metadata": sorted(k for k, v in s["api_metadata"].items() if v)}


def run(trials: int = 3, baseline: bool = False) -> dict:
    marks = markers()
    relay = Relay()
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    try:
        iso = [trial(relay, marks, True, agent=(i % 2 == 1)) for i in range(trials)]
        base = [trial(relay, marks, False)] if baseline else []
    finally:
        relay.shutdown()
    ok = [t for t in iso if not t["error"] and t["requests"] > 0]
    found = sorted({f for t in iso for f in t["found_in_model_input"]})
    return {"cli_version": cli_version(os.environ.get("PCIM_CLAUDE_BIN", "claude")),
            "probed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "markers_checked": sorted(marks), "profile_keys": profile_keys(sanitized_profile()),
            "trials": iso, "baseline": base,
            "clean": bool(ok) and len(ok) == len(iso) and not found, "found": found}


def record(result: dict) -> Path:
    home = Path(os.environ.get("PCIM_HOME", Path.home() / ".pcim"))
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = home / PROBE_RECORD
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d[result["cli_version"] or "unknown"] = {
        k: result[k] for k in ("probed_at", "clean", "found", "markers_checked", "profile_keys")
    } | {"trials": len(result["trials"])}
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh, indent=1)
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pcim.adapters.cli_probe")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--baseline", action="store_true", help="also one call with isolation off, for comparison")
    ap.add_argument("--record", action="store_true", help="write the result for this CLI version to $PCIM_HOME")
    a = ap.parse_args(argv)
    res = run(a.trials, a.baseline or a.record)
    print(json.dumps(res, indent=1))
    if a.record:
        has_positive_control = any("account email" in b.get("found_in_model_input", ()) for b in res["baseline"])
        if not res["clean"] or not res["cli_version"] or not has_positive_control:
            print("not recorded: probe must be clean with a baseline showing the account email", file=sys.stderr)
            return 1
        print(f"recorded: {record(res)}", file=sys.stderr)
    return 0 if res["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
