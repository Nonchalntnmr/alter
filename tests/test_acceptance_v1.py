"""Check harness integrity and demonstrate it catches a deliberately broken app."""
import copy
import hashlib
import json

import httpx
import pytest

from pcim.acceptance_http import check_app
from pcim.acceptance_v1 import PROMPT, PROMPT_SHA256, WEIGHTS, compare, freeze, validate_matched, verify_receipts


def matched():
    manifest = freeze()
    manifest["limits"] = {"seconds": 600, "tokens": 10000, "retries": 2, "cost_usd": 0}
    arm = {k: "fixture" for k in ("model", "model_version", "starter_sha256", "tools_sha256", "machine",
                                 "approval_policy", "network_policy", "clarification_script_sha256", "revision")}
    arm.update(parameters={}, concurrency=1)
    manifest["arms"] = {"alter": copy.deepcopy(arm), "direct": copy.deepcopy(arm)}
    return manifest


def test_exact_user_prompt_and_missing_resources_cannot_start_scored_run():
    assert hashlib.sha256(PROMPT.encode()).hexdigest() == PROMPT_SHA256
    with pytest.raises(ValueError, match="ceilings"):
        validate_matched(freeze())
    manifest = matched()
    validate_matched(manifest)
    manifest["prompt"] += "\n"
    with pytest.raises(ValueError, match="prompt bytes"):
        validate_matched(manifest)


@pytest.mark.parametrize("key", ["model", "tools_sha256", "approval_policy", "starter_sha256"])
def test_unfair_comparison_is_rejected(key):
    manifest = matched()
    manifest["arms"]["direct"][key] = "different"
    with pytest.raises(ValueError, match="unmatched"):
        compare(manifest)


def test_missing_gate_evidence_is_not_a_pass_and_tampering_is_detected(tmp_path):
    assert not verify_receipts(tmp_path, [])["all_gates_passed"]
    proof = tmp_path / "proof.txt"
    proof.write_text("independently observed fixture")
    receipt = {"id": "S01", "status": "passed", "reviewer": "fixture-checker", "check": "disposable canary",
               "evidence": [{"path": "proof.txt", "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}]}
    result = verify_receipts(tmp_path, [receipt])
    assert result["gates"]["S01"]["status"] == "passed" and not result["all_gates_passed"]
    proof.write_text("tampered")
    with pytest.raises(ValueError, match="digest"):
        verify_receipts(tmp_path, [receipt])
    receipt["evidence"][0]["path"] = "../outside"
    with pytest.raises(ValueError, match="inside"):
        verify_receipts(tmp_path, [receipt])


def test_telemetry_unknown_stays_unknown_and_small_sample_is_inconclusive():
    manifest = matched()
    run = {"elapsed_s": 5, "interventions": 0, "quality": dict.fromkeys(WEIGHTS, 3), "provider_tokens": None,
           "completed": False, "evidence_manifest_sha256": "fixture"}
    manifest["trials"] = [{"id": "1", "alter": run, "direct": run}]
    result = compare(manifest)
    assert result["general_superiority"] == "inconclusive"
    assert result["trials"][0]["alter"]["provider_tokens"] is None
    assert result["trials"][0]["alter"]["known_charge_usd"] is None
    assert result["trials"][0]["alter"]["quality"] == 75


def test_local_app_evaluator_detects_deliberately_missing_auth_without_echoing_response():
    secret = "ALTER_SECRET_" + "CANARY_" + "fixture1234"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"token": secret}))
    results = check_app("http://127.0.0.1:12345", transport=transport)
    assert results[0] == {"id": "F02", "status": "failed", "check": "anonymous-list-denied"}
    assert secret not in json.dumps(results)


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost", "http://" + "user:pass@127.0.0.1"],
                         ids=["remote", "ambiguous-localhost", "credentialed-loopback"])
def test_application_evaluator_refuses_remote_or_ambiguous_target(url):
    with pytest.raises(ValueError, match="loopback"):
        check_app(url)


@pytest.mark.parametrize("broken_ownership", [False, True])
def test_http_behavior_evaluator_detects_ownership_defect_and_does_not_overclaim(broken_ownership):
    users, sessions, tasks = {}, {}, {}
    def serve(request):
        body = json.loads(request.content) if request.content else {}
        path, method = request.url.path, request.method
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        user = sessions.get(token)
        if path.endswith("/register"):
            users[body["username"]] = body["password"]
            return httpx.Response(201)
        if path.endswith("/login"):
            if users.get(body["username"]) != body["password"]:
                return httpx.Response(401)
            new_token = "fixture-session-" + body["username"]
            sessions[new_token] = body["username"]
            return httpx.Response(200, json={"access_token": new_token})
        if not user:
            return httpx.Response(401)
        if path.endswith("/logout"):
            del sessions[token]
            return httpx.Response(204)
        if path == "/api/tasks":
            if method == "POST":
                tasks["1"] = {"id": "1", "owner": user, **body}
                return httpx.Response(201, json=tasks["1"])
            return httpx.Response(200, json=[t for t in tasks.values() if t["owner"] == user])
        task = tasks.get(path.rsplit("/", 1)[-1])
        if task is None or (not broken_ownership and task["owner"] != user):
            return httpx.Response(404)
        if method == "PATCH":
            task.update(body)
        if method == "DELETE":
            tasks.clear()
            return httpx.Response(204)
        return httpx.Response(200, json=task)
    results = check_app("http://127.0.0.1:12345", transport=httpx.MockTransport(serve))
    assert results[0]["status"] == "inconclusive"  # storage/session expiry still unreviewed
    if broken_ownership:
        assert results[1]["status"] == "failed" and results[1]["check"] == "cross-user-get-denied"
    else:
        assert results[1]["status"] == "inconclusive"
        assert "cross-user-read-mutate-list" in results[1]["checks"]
    assert not tasks


def test_unavailable_local_service_is_not_a_failed_behavioral_verdict():
    def unavailable(request):
        raise httpx.ConnectError("fixture unavailable")
    result = check_app("http://127.0.0.1:12345", transport=httpx.MockTransport(unavailable))
    assert all(row["status"] == "not-run" for row in result)
