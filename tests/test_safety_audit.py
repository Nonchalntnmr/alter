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
