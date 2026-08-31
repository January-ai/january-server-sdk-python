"""Execute the README examples against loopback HTTP, never using ambient keys."""

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from installed_consumer import FIXTURES, local_service

from januaryai import JanuaryConnectionError, JanuaryTimeoutError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("main.py", "async_main.py")
FAKE_KEY = "sk-quickstart-offline-only"
SEARCH = next(item for item in FIXTURES["operations"] if item["operationId"] == "searchFoods")


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path, monkeypatch):
    # In-process examples must not resolve .env against the real SDK root either.
    monkeypatch.chdir(tmp_path)


def run_example(script, service, directory, key: str | None = FAKE_KEY):
    # Allowlist, not an os.environ copy: no inherited credentials, proxies, or dotenv paths.
    environment = {
        "PATH": os.defpath,
        "PYTHONIOENCODING": "utf-8",
        **{
            name: os.environ[name]
            for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if name in os.environ
        },
    }
    if key is not None:
        environment["JANUARY_API_KEY"] = key
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(ROOT / "tests/example_harness.py"),
            script,
            service["url"],
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def assert_one_search(service):
    assert len(service["requests"]) == 1
    request = service["requests"][0]
    assert request["method"] == "GET"
    assert urlsplit(request["path"]).path == SEARCH["path"]
    assert parse_qs(urlsplit(request["path"]).query) == {"query": ["banana"]}
    assert request["body"] is None
    assert request["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
    assert request["headers"]["x-end-user-id"] == "january-quickstart"
    # The view binds UTC, but search has no timezone parameter in the contract.
    assert "x-end-user-timezone" not in request["headers"]


@pytest.mark.parametrize("script", (*SCRIPTS, "minimal.py"))
@pytest.mark.parametrize("credentials", ["dotenv", "environment", "environment-overrides-dotenv"])
def test_quickstart_success(script, credentials, tmp_path):
    if credentials != "environment":
        file_key = FAKE_KEY if credentials == "dotenv" else "ct-file-must-not-override-environment"
        (tmp_path / ".env").write_text(f"JANUARY_API_KEY={file_key}\n")
    with local_service() as service:
        service["response"] = deepcopy(SEARCH["response"])
        body = service["response"]["body"]
        body["items"] = body["items"][:1]
        body["items"][0]["name"] = "Banana"
        body["total_count"] = 1
        result = run_example(
            script, service, tmp_path, key=None if credentials == "dotenv" else FAKE_KEY
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "Foods returned: 1\nFirst food: Banana\n"
        assert result.stderr == ""
        assert_one_search(service)


@pytest.mark.parametrize("script", SCRIPTS)
def test_quickstart_no_results(script, tmp_path):
    with local_service() as service:
        service["response"]["body"] = {"total_count": 0, "items": []}
        result = run_example(script, service, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "Foods returned: 0\nNo foods found for banana.\n"
        assert result.stderr == ""
        assert_one_search(service)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "key", [None, "", "   "], ids=["no-file", "empty-file-key", "blank-file-key"]
)
def test_quickstart_missing_key_does_not_request(script, key, tmp_path):
    if key is not None:
        (tmp_path / ".env").write_text(f'JANUARY_API_KEY="{key}"\n')
    with local_service() as service:
        result = run_example(script, service, tmp_path, key=None)
        assert result.returncode == 2
        assert result.stdout == ""
        assert (
            result.stderr
            == "Set JANUARY_API_KEY in .env or your environment before running this example.\n"
        )
        assert service["requests"] == []


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("key", ["", "   "], ids=["empty-environment", "blank-environment"])
def test_quickstart_blank_environment_overrides_dotenv(script, key, tmp_path):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    with local_service() as service:
        result = run_example(script, service, tmp_path, key=key)
        assert result.returncode == 2
        assert result.stdout == ""
        assert (
            result.stderr
            == "Set JANUARY_API_KEY in .env or your environment before running this example.\n"
        )
        assert service["requests"] == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_quickstart_never_discovers_ancestor_env(script, tmp_path):
    (tmp_path / ".env").write_text(f"JANUARY_API_KEY={FAKE_KEY}\n")
    directory = tmp_path / "child"
    directory.mkdir()
    with local_service() as service:
        result = run_example(script, service, directory, key=None)
        assert result.returncode == 2
        assert result.stdout == ""
        assert (
            result.stderr
            == "Set JANUARY_API_KEY in .env or your environment before running this example.\n"
        )
        assert service["requests"] == []


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "status,code,hint",
    [
        (401, "unauthorized", "full active server key"),
        (403, "forbidden", "organization access and key permissions"),
        (429, "rate_limited", "Wait before trying again"),
        (429, "credit_limit_exceeded", "https://dashboard.january.ai/billing"),
        (503, "service_unavailable", "Contact support@january.ai"),
    ],
)
def test_quickstart_failure_is_nonzero_safe_and_not_retried(script, status, code, hint, tmp_path):
    with local_service() as service:
        service["response"] = {
            "status": status,
            "headers": {"x-request-id": "req-offline-test", "x-private": "private-echo-header"},
            "body": {
                "code": code,
                "message": f"Server echoed {FAKE_KEY}",
                "private": "private-echo-body",
                "token": "ct-private-echo-token",
            },
        }
        result = run_example(script, service, tmp_path)
        assert result.returncode == 1
        assert result.stdout == ""
        lines = result.stderr.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("Food search failed: ")
        assert json.loads(lines[0].removeprefix("Food search failed: ")) == {
            "status": status,
            "code": code,
            "request_id": "req-offline-test",
        }
        assert lines[1].startswith("Hint: ") and hint in lines[1]
        assert FAKE_KEY not in result.stderr
        assert "private-echo" not in result.stderr
        assert "Traceback" not in result.stderr
        assert_one_search(service)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("echo", [FAKE_KEY, "ct-offline-echo-token"])
def test_quickstart_diagnostic_metadata_keeps_sdk_credential_redaction(script, echo, tmp_path):
    with local_service() as service:
        service["response"] = {
            "status": 503,
            "headers": {"x-request-id": echo},
            "body": {"code": echo, "message": "private-echo-body"},
        }
        result = run_example(script, service, tmp_path)
        assert result.returncode == 1
        assert result.stdout == ""
        diagnostic = json.loads(result.stderr.splitlines()[0].removeprefix("Food search failed: "))
        assert diagnostic == {"status": 503, "code": "[redacted]", "request_id": "[redacted]"}
        assert echo not in result.stderr
        assert "private-echo-body" not in result.stderr
        assert_one_search(service)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("key", ["ct-offline-client-token", "invalid-offline-key"])
def test_quickstart_rejects_invalid_key_safely_before_request(script, key, tmp_path):
    with local_service() as service:
        result = run_example(script, service, tmp_path, key=key)
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == "Configuration error. Set a full sk- server key.\n"
        assert key not in result.stderr
        assert service["requests"] == []


def load_example(script):
    spec = importlib.util.spec_from_file_location(
        "quickstart_under_test", ROOT / "examples/quickstart" / script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "error_type,code",
    [
        (JanuaryTimeoutError, "transport_timeout"),
        (JanuaryConnectionError, "transport_connection"),
    ],
)
def test_quickstart_transport_diagnostics_do_not_print_cause(
    script, error_type, code, monkeypatch, capsys
):
    module = load_example(script)
    with local_service() as service:
        monkeypatch.setattr(
            module,
            "os",
            SimpleNamespace(
                environ={
                    "JANUARY_API_KEY": FAKE_KEY,
                }
            ),
        )

        def offline_failure(**kwargs):
            raise error_type(RuntimeError(f"private-echo-cause {FAKE_KEY}"))

        monkeypatch.setattr(
            module, "January" if script == "main.py" else "AsyncJanuary", offline_failure
        )
        status = module.main() if script == "main.py" else asyncio.run(module.main())
        output = capsys.readouterr()
        assert status == 1
        assert output.out == ""
        assert (
            output.err == f"Food search failed: {code}. Check connectivity; no automatic retry.\n"
        )
        assert FAKE_KEY not in output.err and "private-echo" not in output.err
        assert service["requests"] == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_readme_code_is_byte_identical_to_runnable_source(script):
    readme = (ROOT / "docs/quickstart-diagnostics.md").read_bytes()
    marker = f"<!-- quickstart:{script} -->\n```python\n".encode()
    match = re.search(re.escape(marker) + rb"(.*?)```", readme, re.DOTALL)
    assert match is not None
    assert match.group(1) == (ROOT / "examples/quickstart" / script).read_bytes()


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "base_url",
    [None, "   ", "http://127.0.0.1:1", "https://unexpected.invalid"],
    ids=["unset", "blank", "legacy-loopback-ignored", "legacy-host-ignored"],
)
def test_default_base_url_without_opening_a_connection(script, base_url, monkeypatch, capsys):
    module = load_example(script)
    environment = {"JANUARY_API_KEY": FAKE_KEY}
    if base_url is not None:
        environment["JANUARY_BASE_URL"] = base_url
    monkeypatch.setattr(module, "os", SimpleNamespace(environ=environment))
    constructors = []

    def no_network_client(**kwargs):
        constructors.append(kwargs)
        raise RuntimeError("offline constructor sentinel")

    monkeypatch.setattr(
        module, "January" if script == "main.py" else "AsyncJanuary", no_network_client
    )
    status = module.main() if script == "main.py" else asyncio.run(module.main())
    assert status == 1
    assert constructors == [{"secret_key": FAKE_KEY, "max_retries": 0}]
    assert "offline constructor sentinel" not in capsys.readouterr().err


def test_local_package_metadata_points_to_real_docs():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "januaryai-server"
    assert project["readme"] == "README.md"
    assert project["requires-python"] == ">=3.11"
    assert project["urls"]["Documentation"] == "https://partners.january.ai/v1.2/docs#/"
    assert "python-dotenv>=1.0" in project["optional-dependencies"]["test"]
    assert all(not dependency.startswith("python-dotenv") for dependency in project["dependencies"])


def test_public_docs_and_examples_do_not_expose_url_overrides():
    for relative in (
        "README.md",
        "CONTRIBUTING.md",
        "docs/live-testing.md",
        ".env.example",
        "examples/fastapi/README.md",
        "examples/fastapi/main.py",
        "examples/quickstart/main.py",
        "examples/quickstart/async_main.py",
        "examples/live/main.py",
    ):
        source = (ROOT / relative).read_text()
        assert "JANUARY_BASE_URL" not in source, relative
        assert "base_url" not in source, relative
        assert "january-token-service-mock" not in source, relative


def test_readme_onboarding_links_and_order():
    readme = (ROOT / "README.md").read_text()
    assert readme.index("## Getting an API key") < readme.index(
        "## Installation and dependencies\n"
    )
    assert "python -m pip install januaryai-server python-dotenv" in readme
    assert "JANUARY_API_KEY=your-server-api-key" in readme
    assert "test -e .env || cp .env.example .env" in readme
    assert "```gitignore\n.env\n```" in readme
    assert "export JANUARY_API_KEY" not in readme
    template = (ROOT / ".env.example").read_text().splitlines()
    assert [line for line in template if line and not line.startswith("#")] == ["JANUARY_API_KEY="]
    assert "python quickstart.py" in readme
    assert "quickstart-diagnostics.md" in readme
    assert not re.search(
        r"unpublished|until publication|after publication|request SDK\s+access",
        readme,
        re.IGNORECASE,
    )
    for url in (
        "mailto:support@january.ai",
        "https://dashboard.january.ai/sign-up",
        "https://dashboard.january.ai/sign-in",
        "https://dashboard.january.ai/dashboard",
        "https://dashboard.january.ai/dashboard/client-tokens",
        "https://dashboard.january.ai/billing",
    ):
        assert f"]({url})" in readme
    assert "Set up your" in readme and "organization**" in readme
    assert "**Key name**" in readme and "**Enable client tokens**" in readme
