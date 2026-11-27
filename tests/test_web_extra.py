"""The web dashboard (`pcim serve`) is the optional `api` extra. The installer includes it unless
--no-web; without it, `pcim serve` says how to add it instead of a traceback, and `alter doctor`
says whether it is there. No network, no model call."""
import builtins
import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest

from pcim import cli, terminal
from pcim.cli import WEB_MODULES

REPO = Path(__file__).resolve().parents[1]


def _requirement_names(reqs):
    return {re.match(r"[A-Za-z0-9_.-]+", r).group(0).lower() for r in reqs}


def test_the_installer_includes_the_api_extra_unless_no_web():
    script = (REPO / "scripts" / "install_alter.sh").read_text()
    assert "--no-web) web=0 ;;" in script and "semantic=1 web=1" in script
    assert '[[ $web == 1 ]] && extras="${extras:+$extras,}api"' in script
    assert "--no-web" in script.split("set -euo pipefail")[0]  # in the --help text


def test_web_modules_cover_the_api_extra_and_the_lock_pins_it():
    meta = tomllib.loads((REPO / "pyproject.toml").read_text())
    api = _requirement_names(meta["project"]["optional-dependencies"]["api"])
    assert api and api <= set(WEB_MODULES)
    lock = (REPO / "requirements-lock.txt").read_text().lower()
    for name in api:
        assert re.search(rf"^{name}==\S+$", lock, re.M), f"{name} is not pinned in requirements-lock.txt"


def test_serve_without_the_web_extra_says_how_to_add_it(monkeypatch, capsys):
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", None)  # `import uvicorn` now fails
    assert cli.main(["serve"]) == 1
    err = capsys.readouterr().err
    assert "web dashboard is not installed" in err and "'uvicorn'" in err
    assert "--no-web" in err and "contextual-intelligence[api]" in err


def test_serve_does_not_hide_an_unrelated_missing_module(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "uvicorn":
            raise ModuleNotFoundError("No module named 'h2o'", name="h2o")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ModuleNotFoundError, match="h2o"):
        cli.main(["serve"])


def _doctor(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    terminal.cmd_doctor(["--json"])
    return {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}


@pytest.mark.skipif(any(importlib.util.find_spec(m) is None for m in WEB_MODULES),
                    reason="the api extra is not installed in this environment")
def test_doctor_reports_an_installed_dashboard(monkeypatch, tmp_path, capsys):
    web = _doctor(monkeypatch, tmp_path, capsys)["web_dashboard"]
    assert web["status"] == "ok" and web["detail"] == "installed (pcim serve)"


def test_doctor_reports_a_missing_dashboard_without_failing(monkeypatch, tmp_path, capsys):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "uvicorn" else real_find_spec(name, *a))
    web = _doctor(monkeypatch, tmp_path, capsys)["web_dashboard"]
    assert web["status"] == "ok"  # optional: --no-web is a choice, not a problem
    assert web["detail"].startswith("not installed (no uvicorn)") and "--no-web" in web["detail"]
