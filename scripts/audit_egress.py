"""What leaves the machine when CI calls the claude CLI, measured on the wire. Prints shapes, never values.

    HF_HUB_OFFLINE=1 PYTHONPATH=src python scripts/audit_egress.py [--out FILE] [--fingerprints]

The report says whether the ids sent equal this machine's own: device id, account id and
session id. Short hashes of them appear in the report only with --fingerprints.

It runs CI's own adapter (ClaudeCLIAdapter, identity isolation on) in a few scenarios. Each is
one real Haiku call costing well under a cent. Three observers watch each call:
1. **A loopback relay** at ANTHROPIC_BASE_URL, which forwards to api.anthropic.com. It records
   the request's shape: every path, the header names, the body's top-level keys, the structure
   of `metadata`, and the number and size of the system prompt, messages and tools. It also
   notes whether this machine's identifiers appear in each part. Values are never stored.
   Header values appear only for a short list of generic headers (versions, platform), with
   ids masked.
2. **A logging CONNECT proxy** (HTTPS_PROXY) for every other HTTPS destination, such as
   telemetry, error reporting or update checks. It logs the host and port and tunnels the bytes
   unread.
3. **Socket sampling.** `lsof` on the CLI process every 50 ms catches a connection that ignores
   the proxy.

The limits of each observer are stated in the report:
- sampling can miss a connection shorter than the interval;
- a host reached by an already-resolved IP shows only as an IP;
- DNS lookups are not observed.

Scenarios:
- `generate`: a plain call.
- `agent`: an agent step, with CI's tool protocol.
- `generate+quiet`: a plain call with the CLI's documented switches for non-essential traffic
  (DISABLE_TELEMETRY, DISABLE_ERROR_REPORTING, DISABLE_AUTOUPDATER,
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC).
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import re
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_identity  # noqa: E402

from pcim.adapters.base import RenderedPrompt  # noqa: E402
from pcim.adapters.claude_cli import ClaudeCLIAdapter, cli_version  # noqa: E402

UPSTREAM = "api.anthropic.com"
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host",
        "content-length", "accept-encoding"}
SHOW = {"user-agent", "anthropic-version", "anthropic-beta", "x-app", "content-type", "accept",
        "anthropic-dangerous-direct-browser-access"}
QUIET = {"DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
         "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
MARKS = scan_identity._marks()


def shape(v: str) -> str:
    """A value with ids, hashes, long numbers and this machine's identifiers masked."""
    s = str(v)
    for label, m in sorted(MARKS.items(), key=lambda kv: -len(kv[1])):
        s = re.sub(re.escape(m), f"<{label}>", s, flags=re.I)
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", s, flags=re.I)
    s = re.sub(r"\b[0-9a-f]{16,}\b", lambda m: f"<hex{len(m.group(0))}>", s, flags=re.I)
    s = re.sub(r"\d{6,}", "<num>", s)
    return s


def _fp(v) -> str:
    import hashlib
    return hashlib.sha256(str(v).encode()).hexdigest()[:10]


def fingerprints(meta: dict) -> dict:
    """A short hash of each id inside metadata.user_id, so calls can be compared without the values."""
    out = {}
    for k, v in meta.items():
        try:
            inner = json.loads(v) if isinstance(v, str) else v
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            out[k] = {ik: _fp(iv) for ik, iv in inner.items()}
        else:
            out[k] = _fp(v)
    return out


def local_ids() -> dict:
    """Fingerprints of the ids in this machine's own CLI config, to compare with what is sent."""
    cfg = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") \
        else Path.home() / ".claude.json"
    try:
        d = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return {}
    acct = d.get("oauthAccount") or {}
    return {k: _fp(v) for k, v in (("userID", d.get("userID")), ("accountUuid", acct.get("accountUuid"))) if v}


def found(text: str) -> list[str]:
    low = text.lower()
    return sorted(label for label, m in MARKS.items() if m in low)


class Relay(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.log: list[dict] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _forward(self, method: str):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        rec = {"method": method, "path": self.path.split("?")[0],
               "query_keys": sorted(re.findall(r"[?&]([^=&]+)=", self.path)), "headers": {}}
        for k, v in self.headers.items():
            kl = k.lower()
            if kl in SHOW or kl.startswith("x-stainless-"):
                rec["headers"][kl] = shape(v)
            else:
                rec["headers"][kl] = {"chars": len(v), "bearer": v.lower().startswith("bearer "),
                                      "identifiers": found(v), "uuids": len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-", v, re.I))}
        if body:
            try:
                d = json.loads(body)
                sysp = d.get("system")
                sys_text = json.dumps(sysp, ensure_ascii=False) if sysp is not None else ""
                msgs = d.get("messages") or []
                rec["body"] = {
                    "keys": sorted(d),
                    "model": d.get("model"),
                    "system_chars": len(sys_text),
                    "system_blocks": len(sysp) if isinstance(sysp, list) else (1 if sysp else 0),
                    "messages": len(msgs),
                    "message_chars": len(json.dumps(msgs, ensure_ascii=False)),
                    "tools": len(d.get("tools") or []),
                    "tool_names": sorted({t.get("name", "?") for t in d.get("tools") or []})[:20],
                    "metadata": {k: shape(v) for k, v in (d.get("metadata") or {}).items()},
                    "metadata_fingerprints": fingerprints(d.get("metadata") or {}),
                    "identifiers_in_model_input": found(sys_text + json.dumps(msgs, ensure_ascii=False)),
                    "identifiers_in_metadata": found(json.dumps(d.get("metadata") or {})),
                    "identifiers_elsewhere": found(json.dumps({k: v for k, v in d.items()
                                                               if k not in ("system", "messages", "metadata")})),
                }
            except ValueError:
                rec["body"] = {"non_json_bytes": len(body), "identifiers": found(body.decode("utf-8", "replace"))}
        self.server.log.append(rec)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=300)
        conn.request(method, self.path, body=body, headers=headers)
        r = conn.getresponse()
        rec["status"] = r.status
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while chunk := r.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):  # the CLI closed the stream once it had its answer
            pass
        conn.close()

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")


class ConnectProxy(threading.Thread):
    """Logs CONNECT host:port and tunnels bytes it never reads. Plain-HTTP proxy requests are logged, refused."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        self.hosts: list[str] = []

    def run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c: socket.socket):
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = c.recv(4096)
                if not chunk:
                    return
                head += chunk
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target = line.split(" ")[:2]
            if method != "CONNECT":
                host = re.sub(r"^https?://", "", target).split("/")[0]
                self.hosts.append(f"{host} (plain {method}, refused)")
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            self.hosts.append(target)
            host, _, port = target.rpartition(":")
            up = socket.create_connection((host, int(port or 443)), timeout=30)
            c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            socks = [c, up]
            while True:
                r, _, _ = select.select(socks, [], [], 60)
                if not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (up if s is c else c).sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            c.close()


def sampling_runner(relay_url: str, proxy_port: int, extra_env: dict, seen: set):
    def runner(argv, **kw):
        env = dict(kw.pop("env"))
        env["ANTHROPIC_BASE_URL"] = relay_url
        env["HTTPS_PROXY"] = env["https_proxy"] = f"http://127.0.0.1:{proxy_port}"
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
        env.update(extra_env)
        inp = kw.pop("input", None)
        timeout = kw.pop("timeout", None)
        kw.pop("capture_output", None)
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kw)
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                pids = [str(p.pid)] + subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True,
                                                     text=True).stdout.split()
                out = subprocess.run(["lsof", "-a", "-p", ",".join(pids), "-i", "-n", "-P", "-F", "n"],
                                     capture_output=True, text=True).stdout
                for ln in out.splitlines():
                    if ln.startswith("n") and "->" in ln:
                        seen.add(ln[1:].split("->", 1)[1])
                stop.wait(0.05)

        t = threading.Thread(target=sample, daemon=True)
        t.start()
        try:
            out, err = p.communicate(input=inp, timeout=timeout)
        finally:
            stop.set()
            t.join(2)
        return subprocess.CompletedProcess(argv, p.returncode, out, err)
    return runner


def scenario(name: str, extra_env: dict, agent: bool) -> dict:
    relay = Relay()
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    proxy = ConnectProxy()
    proxy.start()
    seen: set[str] = set()
    a = ClaudeCLIAdapter(model="haiku", runner=sampling_runner(f"http://127.0.0.1:{relay.server_address[1]}",
                                                               proxy.port, extra_env, seen),
                         max_budget_usd=0.05, isolate_identity=True, require_isolation=False)
    err = cost = None
    try:
        if agent:
            tools = [{"name": "read_file", "description": "Read a file in the project.",
                      "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]
            r = a.agent_step("You help with a small project. Answer briefly.",
                             [{"role": "user", "content": "Say OK."}], tools)
        else:
            r = a.generate(RenderedPrompt(system="You are a probe. Reply with the single word OK.", user="Say OK."))
        cost = r.cost_usd
    except Exception as e:  # noqa: BLE001 - reported
        err = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        relay.shutdown()
        proxy.sock.close()
    relay_port = str(relay.server_address[1])
    direct = sorted(x for x in seen if not x.startswith("127.0.0.1:") and not x.startswith("[::1]:"))
    names = {}
    for ep in direct:
        ip = ep.rsplit(":", 1)[0].strip("[]")
        try:
            names[ep] = socket.gethostbyaddr(ip)[0]
        except OSError:
            names[ep] = "(no reverse name)"
    return {"scenario": name, "env_added": sorted(extra_env), "error": err, "cost_usd": cost,
            "api_requests": relay.log,
            "other_https_destinations_via_proxy": sorted(set(proxy.hosts)),
            "connections_bypassing_the_proxy": [f"{ep} {names[ep]}" for ep in direct],
            "loopback_only": all(x.startswith(("127.0.0.1:", "[::1]:")) for x in seen),
            "relay_port": relay_port}


def analyse(report: dict) -> dict:
    """Equalities between the ids sent and this machine's own, so the report needs no fingerprints."""
    local = report["local_id_fingerprints"]
    sent = [q["body"]["metadata_fingerprints"].get("user_id", {}) for s in report["scenarios"]
            for q in s["api_requests"] if isinstance(q.get("body", {}).get("metadata_fingerprints", {}).get("user_id"), dict)]
    per_call = [s["api_requests"][0]["body"]["metadata_fingerprints"].get("user_id", {}) for s in report["scenarios"]
                if s["api_requests"] and isinstance(s["api_requests"][0].get("body", {}).get("metadata_fingerprints", {}).get("user_id"), dict)]
    dev = [x.get("device_id") for x in per_call]
    return {
        "device_id_differs_between_calls": len(set(dev)) == len(dev) and len(dev) > 1,
        "device_id_equals_local_cli_user_id": any(x.get("device_id") == local.get("userID") for x in sent),
        "account_uuid_is_the_real_account_id": bool(sent) and all(x.get("account_uuid") == local.get("accountUuid") for x in sent),
        "session_id_differs_between_calls": len({x.get("session_id") for x in per_call}) == len(per_call) and len(per_call) > 1,
        "datadog_contacted_by_default": any("datadoghq" in h for s in report["scenarios"] if not s["env_added"]
                                            for h in s["other_https_destinations_via_proxy"]),
        "datadog_contacted_with_quiet_switches": any("datadoghq" in h for s in report["scenarios"] if s["env_added"]
                                                     for h in s["other_https_destinations_via_proxy"]),
        "identifiers_in_model_input": sorted({x for s in report["scenarios"] for q in s["api_requests"]
                                              for x in q.get("body", {}).get("identifiers_in_model_input", [])}),
        "identifiers_in_metadata": sorted({x for s in report["scenarios"] for q in s["api_requests"]
                                           for x in q.get("body", {}).get("identifiers_in_metadata", [])}),
        "identifiers_in_headers": sorted({x for s in report["scenarios"] for q in s["api_requests"]
                                          for v in q["headers"].values() if isinstance(v, dict) for x in v["identifiers"]}),
    }


def strip_fingerprints(report: dict) -> dict:
    report = json.loads(json.dumps(report))
    report.pop("local_id_fingerprints", None)
    for s in report["scenarios"]:
        for q in s["api_requests"]:
            q.get("body", {}).pop("metadata_fingerprints", None)
    return report


def main(argv: list[str]) -> int:
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    report = {"cli_version": cli_version(os.environ.get("PCIM_CLAUDE_BIN", "claude")),
              "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "identifier_labels_checked": sorted(MARKS),
              "local_id_fingerprints": local_ids(),
              "observer_limits": ["lsof sampled every 50 ms: a shorter connection can be missed",
                                  "DNS lookups are not observed",
                                  "only traffic of the claude process tree during the call is watched"],
              "scenarios": [scenario("generate", {}, False), scenario("agent", {}, True),
                            scenario("generate+quiet", QUIET, False)]}
    report["analysis"] = analyse(report)
    if "--fingerprints" not in argv:  # the published report carries only the equalities
        report = strip_fingerprints(report)
    text = json.dumps(report, indent=1)
    if out:
        Path(out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
