import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No Sherdog lookups from tests."""
    from fightiq import records
    monkeypatch.setattr(records, "ONLINE", False)
