from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def forbid_unmocked_release_delivery(monkeypatch):
    """No test may inherit a real .env webhook through a missed release mock."""
    def forbidden(_message):
        raise AssertionError("Release delivery must be explicitly mocked in tests")

    monkeypatch.setattr("buyeeye.status_monitor.send_release_summary", forbidden)
