from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_module_manual_login_does_not_import_scanner_or_database():
    root = Path(__file__).resolve().parents[1]
    code = """
import importlib.abc, runpy, sys, types
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'buyeeye.cli', 'buyeeye.db', 'buyeeye.pipeline',
                        'buyeeye.evaluation', 'sqlalchemy', 'sqlmodel'}:
            raise AssertionError('unnecessary import: ' + fullname)
sys.meta_path.insert(0, Guard())
calls = []
page = types.SimpleNamespace(title=lambda: 'Fixture page')
context = types.SimpleNamespace(close=lambda: calls.append('close'))
playwright = types.SimpleNamespace(stop=lambda: calls.append('stop'))
browser = types.ModuleType('buyeeye.browser_session')
browser.STATE_PATH = 'fixture-only-state.json'
browser.has_valid_session = lambda: True
browser.launch_manual_buyee_login = lambda: (calls.append('launch') or (playwright, context, page))
browser.save_session_state = lambda context: calls.append('save')
sys.modules['buyeeye.browser_session'] = browser
import builtins
builtins.input = lambda prompt: calls.append('confirm') or ''
sys.argv = ['buyeeye', 'setup-session']
try:
    runpy.run_module('buyeeye', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
assert calls == ['launch', 'confirm', 'save', 'close', 'stop'], calls
print('fixture bootstrap passed; no real browser or state accessed')
"""
    result = subprocess.run(
        [sys.executable, "-u", "-c", code], cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        text=True, capture_output=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "fixture bootstrap passed" in result.stdout
    assert "no scanner or database" in result.stdout


def test_manual_login_cancel_does_not_export_state(monkeypatch):
    from buyeeye.session_setup import main

    calls = []
    context = SimpleNamespace(close=lambda: calls.append("close"))
    playwright = SimpleNamespace(stop=lambda: calls.append("stop"))
    monkeypatch.setitem(sys.modules, "buyeeye.browser_session", SimpleNamespace(
        STATE_PATH="fixture.json", has_valid_session=lambda: True,
        launch_manual_buyee_login=lambda: (playwright, context, None),
        save_session_state=lambda context: calls.append("save"),
    ))

    def cancel(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancel)
    with pytest.raises(KeyboardInterrupt):
        main([])
    assert calls == ["close", "stop"]


def test_cli_manual_login_skips_settings_and_database_initialization(monkeypatch):
    from buyeeye.cli import main

    def forbidden(*_args, **_kwargs):
        raise AssertionError("manual login must not initialize scanner settings or database")

    monkeypatch.setattr("buyeeye.cli.load_settings", forbidden)
    monkeypatch.setattr("buyeeye.cli.init_db", forbidden)
    monkeypatch.setattr("buyeeye.session_setup.main", lambda argv: 0)
    assert main(["setup-session"]) == 0
