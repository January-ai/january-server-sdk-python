"""Run the real FastAPI relay with test-owned dotenv files and no network access."""
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import tomllib
from types import SimpleNamespace

import httpx
import pytest

from januaryai import AsyncJanuary, JanuaryConfigurationError


ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "sk-fastapi-offline-only"
FAKE_TOKEN = "ct-fastapi-offline-only"
USER_ID = "demo-user"


@pytest.fixture
def relay(tmp_path, monkeypatch, capsys, caplog):
    # No ambient credentials or dotenv settings; never discover a real .env.
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)

    def forbid_network(*args, **kwargs):
        raise AssertionError("Offline relay tests forbid all network connections")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)

    state = SimpleNamespace(
        requests=[], clients=[], dotenv_paths=[], error=None, status=201,
        body={
            "token": FAKE_TOKEN, "expires_in": 1800,
            "expires_at": "2026-08-30T18:30:00Z",
            "end_user_id": USER_ID, "scopes": ["foods:read"],
        },
    )

    def upstream(request):
        state.requests.append(request)
        assert str(request.url) == "https://partners.january.ai/v1.2/auth/client-tokens"
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert json.loads(request.content) == {
            "end_user_id": USER_ID, "scopes": ["foods:read"], "ttl_seconds": 1800,
        }
        if state.error is not None:
            raise state.error
        return httpx.Response(state.status, json=state.body)

    original_async_client = httpx.AsyncClient

    def sdk_http_client(*args, **kwargs):
        client = original_async_client(
            *args, **{**kwargs, "transport": httpx.MockTransport(upstream), "trust_env": False},
        )
        state.clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", sdk_http_client)

    def forbid_prototype_alias(*args, **kwargs):
        raise AssertionError("The relay must call canonical mint_client_token")

    monkeypatch.setattr(AsyncJanuary, "client_tokens", property(forbid_prototype_alias))
    spec = importlib.util.spec_from_file_location("fastapi_example", ROOT / "examples/fastapi/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_load_dotenv = module.load_dotenv

    def load_only_test_dotenv(*, dotenv_path, override):
        # Check the explicit path before opening anything, not after a read.
        assert dotenv_path == Path.cwd() / ".env"
        assert dotenv_path.is_relative_to(tmp_path)
        assert override is False
        state.dotenv_paths.append(dotenv_path)
        return original_load_dotenv(dotenv_path=dotenv_path, override=override)

    monkeypatch.setattr(module, "load_dotenv", load_only_test_dotenv)

    async def exercise(*, headers=None, body=None):
        async with module.app.router.lifespan_context(module.app):
            async with original_async_client(
                transport=httpx.ASGITransport(app=module.app),
                base_url="http://127.0.0.1:4020", trust_env=False,
            ) as browser:
                return await browser.post("/api/january/token", headers=headers, json=body)

    state.call = lambda **kwargs: asyncio.run(exercise(**kwargs))
    yield state

    assert all(client.is_closed for client in state.clients)
    output = capsys.readouterr()
    logs = output.out + output.err + caplog.text
    for secret in (FAKE_KEY, FAKE_TOKEN, "private-upstream-detail", "ct-invalid-file"):
        assert secret not in logs


@pytest.mark.parametrize("credentials", ["cwd-dotenv", "environment-overrides-dotenv"])
def test_startup_and_canonical_mint_flow(relay, tmp_path, monkeypatch, credentials):
    file_key = FAKE_KEY if credentials == "cwd-dotenv" else "ct-invalid-file"
    (tmp_path / ".env").write_text(f'JANUARY_API_KEY="  {file_key}  "\n')
    if credentials == "environment-overrides-dotenv":
        monkeypatch.setenv("JANUARY_API_KEY", FAKE_KEY)
    response = relay.call(headers={"x-demo-user-id": USER_ID})
    assert response.status_code == 200
    assert response.json() == {"token": FAKE_TOKEN, "expiresIn": 1800}
    assert relay.dotenv_paths == [tmp_path / ".env"]
    assert len(relay.requests) == len(relay.clients) == 1


def test_request_body_cannot_select_identity_scopes_or_ttl(relay, tmp_path):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    response = relay.call(
        headers={"x-demo-user-id": USER_ID},
        body={"end_user_id": "another-user", "scopes": ["food_logs:write"], "ttl_seconds": 86400},
    )
    assert response.status_code == 200
    assert response.json() == {"token": FAKE_TOKEN, "expiresIn": 1800}
    assert len(relay.requests) == 1


@pytest.mark.parametrize("headers", [{}, {"x-demo-user-id": ""}])
def test_missing_auth_returns_401_without_minting(relay, tmp_path, headers):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    response = relay.call(headers=headers, body={"end_user_id": USER_ID})
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert relay.requests == []


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_fails_startup_without_network(relay, tmp_path, key):
    if key is not None:
        (tmp_path / ".env").write_text(f'JANUARY_API_KEY="{key}"\n')
    with pytest.raises(RuntimeError, match="Set JANUARY_API_KEY in .env or your environment"):
        relay.call()
    assert relay.requests == relay.clients == []


def test_never_loads_ancestor_dotenv(relay, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    with pytest.raises(RuntimeError, match="Set JANUARY_API_KEY"):
        relay.call()
    assert relay.dotenv_paths == [child / ".env"]
    assert relay.requests == relay.clients == []


def test_blank_environment_overrides_dotenv(relay, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    monkeypatch.setenv("JANUARY_API_KEY", "")
    with pytest.raises(RuntimeError, match="Set JANUARY_API_KEY"):
        relay.call()
    assert relay.requests == relay.clients == []


def test_invalid_key_fails_before_creating_http_client(relay, tmp_path):
    (tmp_path / ".env").write_text("JANUARY_API_KEY=ct-invalid-file\n")
    with pytest.raises(JanuaryConfigurationError) as failure:
        relay.call()
    assert "ct-invalid-file" not in str(failure.value)
    assert relay.requests == relay.clients == []


@pytest.mark.parametrize("failure", [401, 403, 429, 503, "connection", "timeout", "malformed", "unexpected"])
def test_mint_failures_are_generic_safe_and_not_retried(relay, tmp_path, failure):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    private = f"private-upstream-detail {FAKE_KEY} {FAKE_TOKEN}"
    if isinstance(failure, int):
        relay.status = failure
        relay.body = {"code": private, "message": private, "token": FAKE_TOKEN}
    elif failure == "malformed":
        relay.body = {"token": FAKE_TOKEN, "private": private}
    else:
        relay.error = {
            "connection": httpx.ConnectError,
            "timeout": httpx.ReadTimeout,
            "unexpected": RuntimeError,
        }[failure](private)
    response = relay.call(headers={"x-demo-user-id": USER_ID})
    assert response.status_code == 502
    assert response.json() == {"detail": "Unable to mint client token"}
    assert len(relay.requests) == 1


def test_example_dependency_metadata():
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    example = tomllib.loads((ROOT / "examples/fastapi/pyproject.toml").read_text())
    assert example["project"]["requires-python"] == root["requires-python"] == ">=3.11"
    assert "python-dotenv>=1.0" in example["project"]["dependencies"]
    assert "python-dotenv>=1.0" in (ROOT / "examples/fastapi/requirements.txt").read_text().splitlines()
    assert not any(dependency.startswith("python-dotenv") for dependency in root["dependencies"])
    assert example["tool"]["uv"]["sources"]["januaryai-server"] == {"path": "../..", "editable": True}


@pytest.mark.parametrize("document", ["README.md", "CONTRIBUTING.md", "examples/fastapi/README.md"])
def test_documentation_uses_the_same_api_key_setting(document):
    text = (ROOT / document).read_text()
    assert "JANUARY_API_KEY" in text
    assert ".env" in text
    assert "JANUARY_SECRET_KEY" not in text
