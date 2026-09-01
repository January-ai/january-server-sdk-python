"""Only local synthetic credentials/data; never invokes the real API or root .env."""

import base64
import importlib.util
import io
import json
import re
import socket
import sys
import tarfile
import threading
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from example_harness import loopback_http

SDK = Path(__file__).parents[1]
image_spec = importlib.util.spec_from_file_location(
    "image_cases", SDK / "examples/live/image_cases.py"
)
assert image_spec is not None and image_spec.loader is not None
image_cases = importlib.util.module_from_spec(image_spec)
sys.modules[image_spec.name] = image_cases
image_spec.loader.exec_module(image_cases)
spec = importlib.util.spec_from_file_location("january_python_live", SDK / "examples/live/main.py")
assert spec is not None and spec.loader is not None
live = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = live
spec.loader.exec_module(live)
archive_spec = importlib.util.spec_from_file_location(
    "january_distribution_check", SDK / "scripts/check-distribution.py"
)
assert archive_spec is not None and archive_spec.loader is not None
archives = importlib.util.module_from_spec(archive_spec)
archive_spec.loader.exec_module(archives)
FIXTURES = json.loads((SDK / "tests/fixtures/contract.json").read_text(encoding="utf-8"))[
    "operations"
]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhQAAAABJRU5ErkJggg=="
)


def synthetic_environment(root):
    image = root / "examples/live/food.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG)
    return {
        "JANUARY_API_KEY": "sk-offline-server",
        "JANUARY_E2E_USER_ID": "existing-user-must-be-ignored",
    }


@contextmanager
def service(fail=None, revoke_count=1, hide_logs=False):
    state = {
        "requests": [],
        "logs": {},
        "tokens": {},
        "users": set(),
        "fail": fail or {},
        "revocations": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            from urllib.parse import parse_qs, unquote, urlsplit

            location = urlsplit(self.path)
            fixture = next(
                f
                for f in FIXTURES
                if f["method"] == self.command
                and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", f["path"]), location.path)
            )
            op = fixture["operationId"]
            response = deepcopy(fixture["response"])
            payload = self.rfile.read(int(self.headers.get("content-length", "0")))
            body = json.loads(payload) if payload else None
            user = self.headers.get("January-End-User-ID")
            auth = self.headers.get("authorization")
            state["requests"].append(
                {
                    "operation": op,
                    "method": self.command,
                    "query": parse_qs(location.query),
                    "body": body,
                    "user": user,
                    "auth": auth,
                }
            )
            if user:
                state["users"].add(user)
            if op == "searchFoods":
                response["body"]["items"][0]["id"] = "90000011"
            if op == "lookupFoodByBarcode":
                response["body"]["id"] = "90000011"
            if op == "getFood":
                response["body"]["id"] = "90000011"
                response["body"]["servings"][0]["id"] = "80000022"
                for serving in response["body"]["servings"]:
                    serving["is_primary"] = serving["id"] == "80000022"
            if op == "createFoodLog":
                assert body is not None
                log = response["body"]
                log.update(id=str(uuid4()), name=body.get("name"), eaten_at=body["eaten_at"])
                state["logs"].setdefault(user, {})[log["id"]] = deepcopy(log)
            if op == "listFoodLogs":
                response["body"] = {"items": list(state["logs"].get(user, {}).values())}
                if hide_logs:
                    response["body"] = {"items": []}
            if op == "getFoodLog":
                log_id = unquote(location.path.rsplit("/", 1)[1])
                response["body"] = deepcopy(state["logs"][user][log_id])
            if op == "updateFoodLog":
                assert body is not None
                log_id = unquote(location.path.rsplit("/", 1)[1])
                state["logs"][user][log_id]["name"] = body["name"]
                response["body"] = deepcopy(state["logs"][user][log_id])
            if op == "deleteFoodLog" and op not in state["fail"]:
                state["logs"].get(user, {}).pop(unquote(location.path.rsplit("/", 1)[1]), None)
            if op == "createClientToken":
                assert body is not None
                token = "ct-offline-" + str(uuid4())
                state["tokens"][body["end_user_id"]] = token
                response["body"].update(
                    token=token,
                    end_user_id=body["end_user_id"],
                    scopes=body["scopes"],
                    expires_in=300,
                    expires_at=(datetime.now(UTC) + timedelta(seconds=300)).isoformat(),
                )
            if op == "revokeClientTokens":
                assert body is not None
                token_user = body["end_user_id"]
                state["revocations"].append(token_user)
                if op not in state["fail"]:
                    state["tokens"].pop(token_user, None)
                response["body"]["revoked_count"] = revoke_count
            if auth and auth.startswith("Bearer ct-"):
                assert auth[7:] in state["tokens"].values()
            if op in state["fail"]:
                response = {
                    "status": 503,
                    "headers": {"x-request-id": "req-safe"},
                    "body": {
                        "code": "service_unavailable",
                        "docs_url": "https://example.com/docs",
                        "message": "sk-offline-server ct-do-not-print private meal",
                    },
                }
            encoded = json.dumps(response["body"]).encode() if response["body"] is not None else b""
            self.send_response(response["status"])
            for key, value in response["headers"].items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run(root, state, mode="both"):
    output = []
    with loopback_http(state["url"]):
        code = live.main(
            ["--mode", mode], root=root, environ=synthetic_environment(root), emit=output.append
        )
    report = json.loads((root / ".e2e-results/latest.json").read_text(encoding="utf-8"))
    return code, report, "\n".join(output)


def test_env_loader_quotes_precedence_and_no_shell_evaluation(tmp_path):
    marker = tmp_path / "must-not-exist"
    source = (
        "# comment\nJANUARY_API_KEY='from file'\nJANUARY_E2E_QUERY=banana # comment\n"
        f'COMMAND="$(touch {marker})"\nLITERAL=\'${{HOME}} # no expansion\'\nexport QUOTED="a b"\n'
    )
    env_file = tmp_path / ".env"
    env_file.write_text(source)
    values = live.load_environment(tmp_path, {"JANUARY_API_KEY": "shell value"})
    assert values["JANUARY_API_KEY"] == "shell value"
    assert values["JANUARY_E2E_QUERY"] == "banana"
    assert values["COMMAND"] == f"$(touch {marker})"
    assert values["LITERAL"] == "${HOME} # no expansion"
    assert values["QUOTED"] == "a b"
    assert not marker.exists()
    assert env_file.read_text(encoding="utf-8") == source
    other = tmp_path / "custom.env"
    other.write_text("JANUARY_API_KEY=custom\n")
    assert (
        live.load_environment(tmp_path, {"JANUARY_ENV_FILE": "custom.env"})["JANUARY_API_KEY"]
        == "custom"
    )
    assert live.load_environment(tmp_path, {"JANUARY_API_KEY": ""})["JANUARY_API_KEY"] == ""


@pytest.mark.parametrize("text", ["echo nope", "X='unterminated", 'X="ok"; touch nope'])
def test_env_loader_rejects_non_data_syntax(text):
    with pytest.raises(live.ConfigError):
        live.dotenv_values(text)


def test_missing_key_no_network_and_explicit_not_run(tmp_path, monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", reject)
    output = []
    code = live.main([], root=tmp_path, environ={}, emit=output.append)
    result = json.loads((tmp_path / ".e2e-results/latest.json").read_text(encoding="utf-8"))
    assert code == 2
    assert result["status"] == "NOT_RUN"
    assert result["counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 40}
    assert result["results"][0]["code"] == "missing_api_key"
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize(
    "legacy_url",
    [None, "http://127.0.0.1:1", "https://unexpected.invalid"],
    ids=["default", "legacy-loopback-ignored", "legacy-host-ignored"],
)
def test_default_both_modes_all_20_local_http_and_live_ids(tmp_path, legacy_url):
    if legacy_url is not None:
        # Synthetic temporary dotenv only: an obsolete setting must not change routing.
        (tmp_path / ".env").write_text(f"JANUARY_BASE_URL={legacy_url}\n")
    with service() as state:
        code, report, output = run(tmp_path, state)
        assert code == 0, report
        assert report["counts"] == {"PASS": 40, "FAIL": 0, "BLOCKED": 0}
        assert report["cleanupFailures"] == 0
        assert len(state["requests"]) == 42  # 20 + one client-token probe per mode.
        assert len(state["revocations"]) == 2
        assert len(set(state["revocations"])) == 2
        assert all(
            re.fullmatch(r"sdk-e2e-python-[0-9a-f-]{36}", u) and len(u) <= 64
            for u in state["revocations"]
        )
        assert state["users"] == set(state["revocations"])
        assert not state["tokens"]
        assert not any(state["logs"].values())
        for request in state["requests"]:
            if request["operation"] in {"createFoodLog", "predictGlucose"}:
                assert request["body"]["foods"] == [
                    {
                        "food_id": "90000011",
                        "serving_id": "80000022",
                        "quantity": 1,
                    }
                ]
            if request["operation"] == "scanFoodPhoto":
                assert base64.b64decode(request["body"]["image"].split(",", 1)[1]) == PNG
            if request["operation"] == "searchFoodsByNaturalLanguage":
                assert request["body"] == {"text": "one banana"}
        safe = output + json.dumps(report)
        assert "sk-offline-server" not in safe and "ct-offline-" not in safe
        assert all(user not in safe for user in state["users"])


def test_expanded_live_image_matrix_over_local_http(tmp_path):
    environment = synthetic_environment(tmp_path)
    (tmp_path / "examples/live/food.png").write_bytes((SDK / "examples/live/food.png").read_bytes())
    output = []
    with service() as state, loopback_http(state["url"]):
        code = live.main(["--image-matrix"], root=tmp_path, environ=environment, emit=output.append)
        report = json.loads((tmp_path / ".e2e-results/latest.json").read_text(encoding="utf-8"))
        assert code == 0, report
        assert report["counts"] == {"PASS": 40, "FAIL": 0, "BLOCKED": 0}
        assert report["imageCounts"] == {"PASS": 34, "FAIL": 0, "BLOCKED": 0}
        assert report["expectedImageCases"] == 34 and len(state["requests"]) == 76
        assert not state["tokens"] and not any(state["logs"].values())
        assert report["cleanupFailures"] == 0


def test_unreached_image_cases_are_blocked_not_silently_passed():
    report = live.Reporter(["sync"], lambda _: None, image_matrix=True)
    report.remaining("run interrupted")
    assert report.document()["imageCounts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 17}
    assert report.document()["status"] == "FAIL"


def test_independent_operations_continue_and_dependencies_blocked(tmp_path):
    with service(
        fail={"searchFoods": True, "scanFoodPhoto": True, "searchFoodsByNaturalLanguage": True}
    ) as state:
        code, report, output = run(tmp_path, state, "sync")
        assert code == 1
        rows = {r["operation"]: r for r in report["results"]}
        assert rows["foods.search"]["status"] == "FAIL"
        assert rows["foods.get"]["status"] == "BLOCKED"
        assert rows["food_analysis.correct"]["status"] == "BLOCKED"
        assert rows["glucose.predict"]["status"] == "BLOCKED"
        assert rows["restaurants.search_menu_items"]["status"] == "PASS"
        assert rows["create_client_token"]["status"] == "PASS"
        assert rows["revoke_client_tokens"]["status"] == "PASS"
        assert rows["foods.get"]["reason"]
        assert "private meal" not in output and "ct-do-not-print" not in output
        assert not state["tokens"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_cleanup_after_ambiguous_mint_and_update_failure(tmp_path, mode):
    with service(fail={"createClientToken": True, "updateFoodLog": True}) as state:
        code, report, _output = run(tmp_path, state, mode)
        assert code == 1
        assert not state["tokens"]
        assert not any(state["logs"].values())
        assert len(state["revocations"]) == 1
        assert sum(r["operation"] == "createClientToken" for r in state["requests"]) == 1
        rows = {r["operation"]: r for r in report["results"]}
        assert rows["client_token.foods.search"]["status"] == "BLOCKED"
        assert rows["food_logs.delete"]["status"] == "PASS"
        assert rows["revoke_client_tokens"]["status"] == "PASS"
        assert report["cleanupFailures"] == 0


def test_ambiguous_create_recovers_only_run_owned_log(tmp_path):
    with service(fail={"createFoodLog": True}) as state:
        code, report, _ = run(tmp_path, state, "sync")
        assert code == 1
        assert not any(state["logs"].values())
        rows = {r["operation"]: r for r in report["results"]}
        assert rows["food_logs.create"]["status"] == "FAIL"
        assert rows["food_logs.update"]["status"] == "BLOCKED"
        assert rows["food_logs.delete"]["status"] == "PASS"


def test_ambiguous_create_not_found_is_explicit_cleanup_failure(tmp_path):
    with service(fail={"createFoodLog": True}, hide_logs=True) as state:
        code, report, _ = run(tmp_path, state, "async")
        assert code == 1
        rows = {r["operation"]: r for r in report["results"]}
        assert rows["cleanup.food_logs.confirm"]["status"] == "FAIL"
        assert rows["cleanup.food_logs.confirm"]["code"] == "ambiguous_create_cleanup_unconfirmed"
        assert report["cleanupFailures"] == 1
        assert rows["food_logs.delete"]["status"] == "BLOCKED"
        assert len(state["revocations"]) == 1


def test_cleanup_failures_fail_run_and_do_not_retry_or_loop(tmp_path):
    with service(fail={"deleteFoodLog": True, "revokeClientTokens": True}) as state:
        code, report, _ = run(tmp_path, state, "async")
        assert code == 1
        assert report["cleanupFailures"] == 2
        assert len(state["revocations"]) == 1
        assert sum(r["operation"] == "deleteFoodLog" for r in state["requests"]) == 1
    with service(revoke_count=500) as state:
        code, report, _ = run(tmp_path, state, "sync")
        assert code == 0
        assert len(state["revocations"]) == 1


def test_report_sanitizes_error_metadata(tmp_path):
    rows = []
    report = live.Reporter(["sync"], rows.append)
    report.secrets.add("sk-secret")
    report.record("sync", "get_credits", "FAIL", code="sk-secret", request_id="ct-secret")
    assert "sk-secret" not in rows[0] and "ct-secret" not in rows[0]


@pytest.mark.parametrize("member", ["sdk/.env", "sdk/.env.local", "sdk/.e2e-results/latest.json"])
@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_package_archive_guard_rejects_secret_and_results_paths(tmp_path, member, kind):
    artifact = tmp_path / ("test.whl" if kind == "wheel" else "test.tar.gz")
    if kind == "wheel":
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr(member, "synthetic-data")
    else:
        with tarfile.open(artifact, "w:gz") as archive:
            info = tarfile.TarInfo(member)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        archives.check_archive(str(artifact))


def test_package_archive_guard_accepts_public_code(tmp_path):
    artifact = tmp_path / "test.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("januaryai/client.py", "# test")
        for name in (
            "py.typed",
            "__init__.py",
            "_images.py",
            "_contract.json",
            "_generated.py",
            "models.py",
        ):
            archive.writestr(f"januaryai/{name}", "# synthetic package member")
    archives.check_archive(str(artifact))
