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
