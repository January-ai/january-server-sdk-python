"""Tests never use ambient server credentials or load the repository's .env."""

import pytest


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    monkeypatch.delenv("JANUARY_API_KEY", raising=False)
    monkeypatch.delenv("JANUARY_BASE_URL", raising=False)
