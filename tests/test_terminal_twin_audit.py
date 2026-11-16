"""`ci twin`, `ci audit`, `ci learn`: thin views over state that already exists; they must not invent any."""

from test_amplify import env, make  # noqa: F401

from pcim import terminal


def test_twin_labels_its_evidence_and_separates_alone_from_with_help(env, tmp_path, monkeypatch, capsys):
    ci, _ = make(tmp_path)
    ci.remember("u", "I prefer permutation nulls", "preference")
    for ok in (True, True, False):
        ci.probe("u", "sql joins", ok)
    ci.probe("u", "sql joins", True, assisted=True)
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    assert terminal.main(["twin"]) == 0
    out = capsys.readouterr().out
    assert "It is not you" in out and "Stated by you" in out and "hypotheses" in out
    assert "sql joins" in out and "alone 2/3" in out and "with help 1/1" in out


def test_audit_verifies_the_chain_and_fails_loudly_when_tampered(env, tmp_path, monkeypatch, capsys):
    ci, _ = make(tmp_path)
    ci.remember("u", "a note", "fact")
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    assert terminal.main(["audit"]) == 0 and "verified" in capsys.readouterr().out
    ci.store._db.execute("UPDATE audit SET payload='{}' WHERE seq=(SELECT MAX(seq) FROM audit)")
    assert terminal.main(["audit"]) == 5 and "BROKEN" in capsys.readouterr().out


def test_learn_is_learn_mode(env, tmp_path, monkeypatch, capsys):
    ci, recs = make(tmp_path)
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    monkeypatch.setattr(terminal, "_here", lambda *a, **k: None)
    assert terminal.main(["learn", "explain", "bayes", "rule"]) == 0
    assert capsys.readouterr().out.count("learn (") == 1
