"""Disposable S01/S02/P01 checks. Scripted requests are never live-model evidence."""
import json
import sys

import pytest

from pcim import identity_guard, sandbox
from pcim.engine import PersonalCI
from pcim.governor import Governor, PathError, jail
from pcim.sovereignty.content import ContentBlocked, assert_safe_payload, public_payload
from pcim.tasks import TaskQueue, TaskStateError
from pcim.tools import Executor, ToolError, _search
from pcim.workspace import Workspace


def canary():
    return "ALTER_SECRET_" + "CANARY_" + "disposable1234"


@pytest.fixture
def setup(tmp_path):
    ci = PersonalCI(tmp_path / "state", seed=0)
    ci.onboard("u", providers=["mock"])
    ws = Workspace(ci)
    p = ws.create_project("u", "fixture")
    root = tmp_path / "project"
    root.mkdir()
    ws.authorize_workspace("u", p.id, str(root), allow_write=True,
                           run_argv=[sys.executable, "-c", "raise SystemExit(0)"])
    yield ci, ws, p, root, TaskQueue(ws, Governor(ws))
    ci.store.close()


@pytest.mark.parametrize("path", ["../outside", "/designated/outside", "link/file"])
def test_s01_path_and_symlink_escape_denied_without_effect(setup, tmp_path, path):
    ci, ws, p, root, queue = setup
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    task = queue.propose("u", p.id, "write_file", {"path": path, "content": "changed", "reason": "fixture"})
    assert task.status == "denied"
    assert outside.read_text() == "unchanged"
    assert ci.audit.verify()[0]


def test_p01_approval_replay_and_payload_change_require_separate_decisions(setup):
    _, _, p, root, queue = setup
    args = {"path": "a.txt", "content": "first", "reason": "fixture"}
    task = queue.propose("u", p.id, "write_file", args)
    assert task.status == "awaiting_approval" and not (root / "a.txt").exists()
    assert queue.approve("u", task.id).status == "succeeded"
    (root / "a.txt").write_text("after-approved-write")
    with pytest.raises(TaskStateError):
        queue.approve("u", task.id)
    assert (root / "a.txt").read_text() == "after-approved-write"
    changed = queue.propose("u", p.id, "write_file", {**args, "content": "changed"})
    assert changed.id != task.id and changed.status == "awaiting_approval"


def test_p01_halt_and_symlink_swap_rechecked_after_approval_request(setup, tmp_path):
    _, _, p, root, queue = setup
    (root / "folder").mkdir()
    task = queue.propose("u", p.id, "write_file", {"path": "folder/a.txt", "content": "x", "reason": "fixture"})
    (root / "folder").rmdir()
    (root / "folder").symlink_to(tmp_path, target_is_directory=True)
    assert queue.approve("u", task.id).status == "denied"
    assert not (tmp_path / "a.txt").exists()


def test_cross_project_and_cross_user_task_reads_are_not_interchangeable(setup):
    ci, ws, p, _, queue = setup
    ci.onboard("other", providers=["mock"])
    other_project = ws.create_project("u", "second")
    task = queue.propose("u", p.id, "record_note", {"kind": "decision", "text": "project-private"})
    with pytest.raises(KeyError):
        queue.get("other", task.id)
    assert not queue.list("u", other_project.id)
    assert task.project_id == p.id


@pytest.mark.parametrize("payload", [lambda: {"nested": [{"text": canary()}]},
    lambda: {"api_key": "z" * 24},
    lambda: "api_" + "key = '" + "z" * 24 + "'", lambda: "Bearer " + "a" * 30,
    lambda: "-----BEGIN " + "PRIVATE KEY-----\nfixture"])
def test_s02_request_preflight_and_failure_trace_do_not_echo_values(payload):
    value = payload()
    with pytest.raises(ContentBlocked) as failure:
        assert_safe_payload(value)
    assert canary() not in str(failure.value)
    assert canary() not in json.dumps(public_payload(value))


def test_s02_ordinary_file_read_search_write_and_export_block_canary(setup):
    _, ws, p, root, queue = setup
    (root / "ordinary.txt").write_text("helpful prefix\n" + canary() + "\nuseful suffix")
    spec = ws.workspace_auth("u", p.id)["spec"]
    with pytest.raises(ContentBlocked):
        Executor().run("r", "read_file", {"path": "ordinary.txt", "offset": "1", "limit": "1"}, spec)
    assert _search(root, "helpful", "**/*")["matches"] == []
    task = queue.propose("u", p.id, "write_file", {"path": "out.txt", "content": canary(), "reason": "fixture"})
    assert task.status == "denied" and not (root / "out.txt").exists()
    receipt = identity_guard.verify_export(root, ["ordinary.txt"], marks={})
    assert not receipt["accepted"] and canary() not in json.dumps(receipt)


def test_s02_memory_with_broad_consent_still_withholds_secret_content(setup):
    ci, _, _, _, _ = setup
    ci.consent("u", "fixture", "store")
    ci.consent("u", "fixture", "egress", "mock")
    item = ci.ingest_text("u", "Synthetic context: " + canary(), source="fixture")[0].item
    decision = ci.egress.filter("u", [item], "mock", "mock", ref="fixture")
    assert decision.allowed == [] and decision.blocked == [(item.id, "secret-content")]


def test_egress_rejects_wrong_owner_and_unexamined_ancestor(setup):
    import dataclasses
    ci, _, _, _, _ = setup
    item = ci.remember("u", "A harmless personal statement.", "fact").item
    assert ci.egress.check("other", item, "mock") == (False, "wrong-owner")
    derived = dataclasses.replace(item, id="derived", parents=(item.id,))
    old_depth = ci.egress.max_depth
    ci.egress.max_depth = 0
    try:
        assert ci.egress.check("u", derived, "mock") == (False, "ancestor-deleted")
    finally:
        ci.egress.max_depth = old_depth


def test_failure_output_is_withheld_before_tail_truncation(setup):
    _, ws, p, root, _ = setup
    spec = ws.workspace_auth("u", p.id)["spec"]
    # The public run_tests path in its default (host) mode; confined runs share the same output
    # handling, and their fail-closed refusal is covered below.
    argv = [sys.executable, "-I", "-c", "import sys; print(" + repr(canary() + "x" * 7000) + "); sys.exit(1)"]
    result = Executor().run("output-fixture", "run_tests", {}, dict(spec, run_argv=argv))
    assert result["exit_code"] == 1
    assert result["stdout_tail"].startswith("[withheld:")
    assert canary() not in json.dumps(result)


def test_export_manifest_is_bound_to_content_and_fails_on_missing_binary_or_escape(tmp_path):
    (tmp_path / "clean.txt").write_text("public content")
    good = identity_guard.verify_export(tmp_path, ["clean.txt"], marks={})
    assert good["accepted"] and len(good["files"][0]["sha256"]) == 64
    for files in ([], ["missing"], ["../outside"], ["binary"]):
        (tmp_path / "binary").write_bytes(b"\xff")
        assert not identity_guard.verify_export(tmp_path, files, marks={})["accepted"]


def test_s01_unavailable_confinement_fails_before_project_code(setup, tmp_path, monkeypatch):
    _, ws, p, _, _ = setup
    outside = tmp_path / "execution-canary"
    outside.write_text("unchanged")
    spec = ws.workspace_auth("u", p.id)["spec"]
    # A workspace that requires confinement; the default host mode is covered in test_test_confinement_modes.
    spec["test_confinement"] = "required"
    spec["run_argv"] = ["python3", "-c", f"from pathlib import Path; Path({str(outside)!r}).write_text('escaped')"]
    def unavailable():
        raise sandbox.SandboxUnavailable("fixture unavailable")
    monkeypatch.setattr(sandbox, "_binary", unavailable)
    with pytest.raises(ToolError, match="unavailable"):
        Executor().run("no-execution", "run_tests", {"reason": "fixture"}, spec)
    assert outside.read_text() == "unchanged"


def test_snapshot_rejects_links_and_omits_private_files(tmp_path):
    root, copy = tmp_path / "root", tmp_path / "copy"
    root.mkdir()
    copy.mkdir()
    (root / "ok.txt").write_text("public")
    (root / ".env").write_text(canary())
    sandbox._snapshot(root, copy)
    assert (copy / "ok.txt").read_text() == "public" and not (copy / ".env").exists()
    (root / "link").symlink_to(root / "ok.txt")
    with pytest.raises(sandbox.SandboxUnavailable, match="symlinks"):
        sandbox._snapshot(root, copy)


def test_s01_actual_confinement_canary_network_namespace_and_test_receipt(tmp_path):
    """Runs real OS confinement where available; skip is explicitly not live success."""
    root = tmp_path / "project"
    root.mkdir()
    try:
        sandbox._binary()
    except sandbox.SandboxUnavailable as error:
        pytest.skip(str(error))
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    script = ("from pathlib import Path; import socket; "
              f"assert not Path({str(outside)!r}).exists(); "
              "assert not Path('/home').exists(); "
              "Path('made-in-copy').write_text('ok'); print('confined')")
    spec = {"root": str(root), "run_argv": ["python3", "-I", "-c", script], "run_timeout_s": 20}
    result = Executor().run("real-confinement", "run_tests", {"reason": "fixture"}, spec)
    assert result["exit_code"] == 0 and result["confinement"]["verified"]
    assert "confined" in result["stdout_tail"] and outside.read_text() == "unchanged"
    assert not (root / "made-in-copy").exists()


def test_jail_rejects_secret_name_and_git_write(tmp_path):
    for path in (".env", ".ssh/id_rsa", ".git/config"):
        with pytest.raises(PathError):
            jail(tmp_path, path, for_write=True)
