"""Regression tests for docs/release/SAFETY_AUDIT_0.3.0a1.md, one per fixed finding (S-numbers).
Offline: the mock agent plays the model, and every store is a throwaway PCIM_HOME."""

import pytest

from pcim import cli
from pcim import terminal as T
from pcim.engine import PersonalCI
from pcim.sovereignty.crypto import MasterKeyMissing


# S1 -----------------------------------------------------------------------------------------------
def test_s1_a_lost_master_key_is_never_replaced_by_a_new_one(tmp_path, monkeypatch, capsys):
    """master.key gone, pcim.db still there (a partial restore, a deleted file): the old code made a new
    key, and the next write was sealed under it, unreadable once the original came back."""
    monkeypatch.delenv("PCIM_MASTER_KEY", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("PCIM_HOME", str(home))
    assert cli.main(["init", "--user", "u", "--providers", "mock"]) == 0
    assert cli.main(["remember", "written under the original key"]) == 0
    key = (home / "master.key").read_bytes()
    (home / "master.key").rename(tmp_path / "master.key.backup")

    with pytest.raises(MasterKeyMissing, match="Restore master.key"):
        PersonalCI(home)
    with pytest.raises(SystemExit) as e:  # what `pcim remember` does now: refuse, and write nothing
        cli.main(["remember", "written while the key is missing"])
    assert "Restore master.key" in str(e.value.code)
    with pytest.raises(SystemExit) as e:  # `ci` and `alter` open the store through T._open
        T._open()
    assert e.value.code == 2 and "Restore master.key" in capsys.readouterr().err
    assert not (home / "master.key").exists()  # no second key was made

    capsys.readouterr()
    T.cmd_doctor(["--json"])
    assert '"master_key"' in capsys.readouterr().out

    (tmp_path / "master.key.backup").rename(home / "master.key")  # the restore
    assert (home / "master.key").read_bytes() == key
    ci, uid = T._open()
    assert [i.content for i in ci.memory.current(uid)] == ["written under the original key"]


def test_s1_a_new_or_empty_store_still_gets_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PCIM_MASTER_KEY", raising=False)
    PersonalCI(tmp_path / "fresh").store.close()
    assert (tmp_path / "fresh" / "master.key").is_file()
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "pcim.db").touch()  # a first run that stopped before writing anything
    PersonalCI(tmp_path / "empty").store.close()
    assert (tmp_path / "empty" / "master.key").is_file()


# S2 -----------------------------------------------------------------------------------------------
def _damage(ci, gid):
    ct = bytearray(ci.store.query("SELECT payload_ct FROM goals WHERE id=?", (gid,))[0][0])
    ct[-5] ^= 0xFF  # one flipped byte: the AES-GCM tag no longer matches
    ci.store.execute("UPDATE goals SET payload_ct=? WHERE id=?", (bytes(ct), gid))


def test_s2_one_damaged_goal_record_does_not_take_alter_down(tmp_path, monkeypatch, capsys):
    """The old code raised InvalidTag from every command that lists goals (status, goals, doctor,
    weekly), refused every new goal in the project, and could not forget the damaged one."""
    from pcim import alter
    from pcim import goals as G
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    repo = tmp_path / "proj"
    repo.mkdir()
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--user", "u", "--providers", "mock"]) == 0
    assert alter.main(["init", "--provider", "mock", "--tests", "true"]) == 0
    ci, uid = T._open()
    here = T._here(ci, uid)
    gs = G.GoalStore(ci)
    ok = gs.create(uid, here.project_id, "a goal whose record is intact")
    bad = gs.create(uid, here.project_id, "a goal whose record gets damaged")
    for g in (ok, bad):
        g.status = "done"
        gs.save(g)
    _damage(ci, bad.id)
    capsys.readouterr()

    assert alter.main(["goals", "list"]) == 0
    out = capsys.readouterr().out
    assert ok.id in out and f"{bad.id}  unreadable" in out
    assert alter.main(["status"]) == 0 and "1 unreadable" in capsys.readouterr().out
    assert alter.main(["goals", "show", ok.id]) == 0 and "record is intact" in capsys.readouterr().out
    assert alter.main(["goals", "show", bad.id]) == 2 and "cannot be read" in capsys.readouterr().err
    alter.main(["doctor", "--json"])
    assert "goal_records" in capsys.readouterr().out
    # a new goal in the same project is not blocked by it
    assert G.GoalRunner(ci, uid, here, out=lambda s: None).start("a new goal", provider="mock").status == "queued"
    # and it can be forgotten by its id, which is all that is left of it
    assert alter.main(["goals", "forget", bad.id]) == 0
    assert bad.id not in [r["id"] for r in ci.store.query("SELECT id FROM goals")]
    assert gs.list(uid) and not gs.unreadable


# S3 -----------------------------------------------------------------------------------------------
def _queue(tmp_path):
    from pcim.governor import Governor
    from pcim.tasks import TaskQueue
    from pcim.workspace import Workspace
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = Workspace(ci)
    p = ws.create_project("u", "P")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=["true"], allow_write=True)
    q = TaskQueue(ws, Governor(ws))

    def write(path, content):
        return q.approve("u", q.propose("u", p.id, "write_file", {"path": path, "content": content, "reason": "r"}).id)
    return q, repo, write


def test_s3_undo_never_overwrites_a_change_made_after_the_write(tmp_path):
    """The old undo restored the backup whatever the file held by then: the person's own edits since the
    write were overwritten without a trace (and a created file was deleted with them)."""
    from pcim.tasks import TaskStateError
    q, repo, write = _queue(tmp_path)
    (repo / "notes.txt").write_text("original\n")
    w = write("notes.txt", "by the agent\n")
    (repo / "notes.txt").write_text("by the agent\nand then by the person\n")
    with pytest.raises(TaskStateError, match="has changed since this write"):
        q.undo("u", w.id)
    assert (repo / "notes.txt").read_text() == "by the agent\nand then by the person\n"

    created = write("new.txt", "made by the agent\n")
    (repo / "new.txt").write_text("made by the agent\nkept by the person\n")
    with pytest.raises(TaskStateError):
        q.undo("u", created.id)
    assert (repo / "new.txt").exists()

    # unchanged since the write: undo works, latest first
    a, b = write("two.txt", "v1\n"), write("two.txt", "v2\n")
    with pytest.raises(TaskStateError, match="undo any later write"):
        q.undo("u", a.id)
    q.undo("u", b.id)
    q.undo("u", a.id)
    assert not (repo / "two.txt").exists()


# S4 -----------------------------------------------------------------------------------------------
def test_s4_a_file_undo_could_not_restore_is_not_overwritten(tmp_path):
    """The old write kept a non-UTF-8 file's previous version decoded with errors="replace", so undo
    wrote back U+FFFD in place of the original bytes: the "kept for undo" version was already lost."""
    q, repo, write = _queue(tmp_path)
    raw = "café, en latin-1\n".encode("latin-1")
    (repo / "latin.txt").write_bytes(raw)
    t = write("latin.txt", "replaced\n")
    assert t.status == "failed" and "not UTF-8 text" in (t.reason or "")
    assert (repo / "latin.txt").read_bytes() == raw

    # UTF-8 text, with a BOM and CRLF line ends: undo restores the exact bytes
    utf = "﻿café — ok\r\nsecond line\r\n".encode("utf-8")
    (repo / "utf.txt").write_bytes(utf)
    w = write("utf.txt", "replaced\n")
    assert w.status == "succeeded" and (repo / "utf.txt").read_text() == "replaced\n"
    q.undo("u", w.id)
    assert (repo / "utf.txt").read_bytes() == utf


# S5 -----------------------------------------------------------------------------------------------
def test_s5_the_alter_brief_never_carries_a_credential_to_the_model(tmp_path):
    """`ci ask` withholds cloud vendors from a request that holds a credential; ALTER's brief bypassed
    that check, so a key in a vault note, a README or a commit subject reached the goal's provider."""
    from test_alter_goals import _texts, env, runner, start  # noqa: F401
    from pcim import thirdeye
    from pcim.sovereignty.credentials import find_credentials
    from pcim.terminal import Here
    from pcim.workspace import Workspace
    aws = "AKIA" + "ABCDEFGHIJKLMNOP"  # fake, and built here so the file itself holds no key-shaped string
    gh = "ghp_" + "0123456789abcdefghij0123456789abcdef"
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(f"# Calc\nexport GITHUB_TOKEN={gh}\n")
    from test_alter_cli import git
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", f"deploy: rotate {aws}")
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "Calc deploy.md").write_text(f"# Calc deploy\nThe calc deploy user signs in with {aws} for the bucket.\n")
    ws = Workspace(ci)
    p = ws.create_project("u", "Calc")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=["true"], allow_write=True)
    thirdeye.set_source(ws, "u", "vault", True, str(vault))
    here = Here(p.id, "Calc", {"mock"}, str(repo))

    brief = thirdeye.gather(ci, "u", here, "calc deploy bucket")
    assert any(e.source == "vault" for e in brief.evidence)
    assert find_credentials(brief.for_model()) == [] and aws not in brief.for_terminal()
    assert "[REDACTED:AWS access key id]" in brief.for_model()
    assert {e.cite for e in brief.evidence if e.source == "git"} >= {"file:README.md"}
    assert sum("credential-shaped text redacted" in w for w in brief.withheld) == 3  # README, commit, vault

    r, mock = runner(ci, here, [{"text": "Nothing to change."}])
    g = start(r, "check the calc deploy bucket")
    r.run(g.id)
    sent = _texts(mock)[0]
    assert "Calc deploy" in sent and aws not in sent and gh not in sent and find_credentials(sent) == []
