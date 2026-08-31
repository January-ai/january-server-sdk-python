"""Opt-in live demo/E2E. Importing this module never reads credentials or uses HTTP."""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar
from uuid import uuid4

import httpx
from image_cases import VALID_CASES, photo_case

from januaryai import UNSET, AsyncJanuary, January, JanuaryAPIError, JanuaryTimeoutError
from januaryai.models import FoodLogInputFoodInput

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = (
    "credits",
    "foods.search",
    "foods.autocomplete",
    "foods.get",
    "foods.lookup_barcode",
    "foods.suggest_alternatives",
    "restaurants.search",
    "restaurants.search_menu_items",
    "food_analysis.analyze_photo",
    "food_analysis.analyze_description",
    "food_analysis.correct",
    "food_logs.create",
    "food_logs.list",
    "food_logs.update",
    "food_logs.delete",
    "glucose.predict",
    "mint_client_token",
    "revoke_client_tokens",
)


class ConfigError(Exception):
    """Arguments are fixed safe error codes, never environment contents."""


class CheckFailed(Exception):
    """Arguments are fixed safe assertion labels, never API response contents."""


def dotenv_values(text: str) -> dict[str, str]:
    """Small data-only dotenv grammar: no evaluation, interpolation, or sourcing."""
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if match is None:
            raise ConfigError("invalid_env_syntax")
        key, value = match.groups()
        if value.startswith(("'", '"')):
            delimiter = value[0]
            end = value.find(delimiter, 1)
            if end < 0 or (
                value[end + 1 :].strip() and not value[end + 1 :].strip().startswith("#")
            ):
                raise ConfigError("invalid_env_quotes")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        result[key] = value
    return result


def load_environment(root: Path, environ: Mapping[str, str]) -> dict[str, str]:
    custom = environ.get("JANUARY_ENV_FILE")
    path = Path(custom) if custom else root / ".env"
    if not path.is_absolute():
        path = root / path
    values: dict[str, str] = {}
    try:
        if path.is_file():
            values = dotenv_values(path.read_text(encoding="utf-8"))
        elif custom:
            raise ConfigError("env_file_missing")
    except (OSError, UnicodeError):
        raise ConfigError("env_file_unreadable") from None
    # Even an explicitly empty shell value overrides the file.
    return {**values, **environ}


@dataclass(frozen=True, repr=False)
class Config:
    api_key: str = field(repr=False)
    timeout: float
    upc: str
    query: str
    restaurant_query: str
    latitude: float
    longitude: float
    image: str = field(repr=False)
    image_path: Path | None = field(default=None, repr=False)

    @classmethod
    def load(cls, root: Path, environ: Mapping[str, str]) -> Config:
        env = load_environment(root, environ)
        key = env.get("JANUARY_API_KEY", "")
        if not key:
            raise ConfigError("missing_api_key")
        if not key.startswith("sk-") or not key[3:] or any(c.isspace() for c in key):
            raise ConfigError("invalid_server_key")
        try:
            timeout = float(env.get("JANUARY_E2E_TIMEOUT_SECONDS", "120"))
            latitude = float(env.get("JANUARY_E2E_LATITUDE", "37.7749"))
            longitude = float(env.get("JANUARY_E2E_LONGITUDE", "-122.4194"))
            if (
                not math.isfinite(timeout)
                or timeout <= 0
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError()
        except ValueError:
            raise ConfigError("invalid_network_configuration") from None
        query = env.get("JANUARY_E2E_QUERY", "banana")
        restaurant = env.get("JANUARY_E2E_RESTAURANT_QUERY", "chicken")
        upc = env.get("JANUARY_E2E_UPC", "049000006346")
        if (
            not query.strip()
            or len(query) > 256
            or not restaurant.strip()
            or len(restaurant) > 256
            or not re.fullmatch(r"[0-9]{6,14}", upc)
        ):
            raise ConfigError("invalid_search_configuration")
        image_path = Path(env.get("JANUARY_E2E_IMAGE_PATH", "examples/live/food.png"))
        if not image_path.is_absolute():
            image_path = root / image_path
        try:
            image = image_path.read_bytes()
        except OSError:
            raise ConfigError("image_fixture_unreadable") from None
        mime = (
            "image/png"
            if image.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if image.startswith(b"\xff\xd8\xff")
            else None
        )
        if mime is None or len(image) > 3_500_000:
            raise ConfigError("image_fixture_invalid")
        return cls(
            key,
            timeout,
            upc,
            query,
            restaurant,
            latitude,
            longitude,
            f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}",
            image_path,
        )


class Reporter:
    def __init__(
        self, modes: list[str], emit: Callable[[str], None], *, image_matrix: bool = False
    ) -> None:
        self.modes = modes
        self.emit = emit
        self.results: list[dict[str, Any]] = []
        self.secrets: set[str] = set()
        self.started = time.monotonic()
        self.image_cases = (
            [name for name in VALID_CASES if name != "data_uri_png"] if image_matrix else []
        )

    def safe(self, value: Any) -> str | None:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value):
            return None
        if any(secret and secret in value for secret in self.secrets) or re.search(
            r"(?:sk|ct)-", value
        ):
            return "redacted"
        return value

    def record(
        self,
        mode: str,
        operation: str,
        status: str,
        *,
        kind: str = "operation",
        code: str | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
        duration: float = 0,
        cleanup: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "mode": mode,
            "operation": operation,
            "kind": kind,
            "status": status,
            "code": self.safe(code),
            "requestId": self.safe(request_id),
            "httpStatus": http_status,
            "durationMs": round(duration * 1000, 2),
            "cleanup": cleanup,
        }
        if reason is not None:
            row["reason"] = reason  # All callers supply static dependency labels only.
        self.results.append(row)
        self.emit(
            json.dumps({k: row[k] for k in ("mode", "operation", "status", "code", "requestId")})
        )
        return row

    def remaining(self, reason: str) -> None:
        for mode in self.modes:
            done = {
                r["operation"]
                for r in self.results
                if r["mode"] == mode and r["kind"] == "operation"
            }
            for operation in OPERATIONS:
                if operation not in done:
                    self.record(mode, operation, "BLOCKED", code="not_run", reason=reason)
            image_done = {
                r["operation"] for r in self.results if r["mode"] == mode and r["kind"] == "image"
            }
            for name in self.image_cases:
                if f"image.{name}" not in image_done:
                    self.record(
                        mode,
                        f"image.{name}",
                        "BLOCKED",
                        kind="image",
                        code="not_run",
                        reason=reason,
                    )

    def document(self) -> dict[str, Any]:
        operations = [r for r in self.results if r["kind"] == "operation"]
        counts = {
            status: sum(r["status"] == status for r in operations)
            for status in ("PASS", "FAIL", "BLOCKED")
        }
        cleanup_failures = sum(r["cleanup"] and r["status"] != "PASS" for r in self.results)
        passed = (
            len(operations) == 18 * len(self.modes)
            and counts["PASS"] == len(operations)
            and all(r["status"] == "PASS" for r in self.results)
        )
        return {
            "language": "python",
            "modes": self.modes,
            "status": "PASS" if passed else "FAIL",
            "expectedOperations": 18 * len(self.modes),
            "counts": counts,
            "cleanupFailures": cleanup_failures,
            "expectedImageCases": len(self.image_cases) * len(self.modes),
            "imageCounts": {
                status: sum(r["kind"] == "image" and r["status"] == status for r in self.results)
                for status in ("PASS", "FAIL", "BLOCKED")
            },
            "durationMs": round((time.monotonic() - self.started) * 1000, 2),
            "results": self.results,
        }


async def invoke(fn: Callable[..., Any], **kwargs: Any) -> Any:
    result = fn(**kwargs)
    return await result if inspect.isawaitable(result) else result


def require(condition: Any, code: str) -> None:
    if not condition:
        raise CheckFailed(code)


_Present = TypeVar("_Present")


def require_value(value: _Present | None) -> _Present:
    if value is None:
        raise CheckFailed("missing_step_dependency")
    return value


def metadata(value: Any) -> tuple[int | None, str | None]:
    response = getattr(value, "response", None) or value
    return getattr(response, "status_code", None), getattr(response, "request_id", None)


async def token_probe(config: Config, token: str, mode: str) -> Any:
    # One native HTTP request: server SDK constructors intentionally reject ct-.
    projection = json.loads(files("januaryai").joinpath("_contract.json").read_text())
    operation = next(
        o
        for o in projection["operations"].values()
        if o["resource"] == "foods" and o["publicMethod"] == "search"
    )
    kwargs = {
        "method": operation["method"],
        "url": "https://partners.january.ai" + operation["path"],
        "params": {"query": config.query, "limit": 1},
        "headers": {"Authorization": f"Bearer {token}", "Accept": "application/json"},
    }
    if mode == "async":
        async with httpx.AsyncClient(
            timeout=config.timeout, follow_redirects=False, trust_env=False
        ) as http:
            response = await http.request(**kwargs)
    else:
        with httpx.Client(timeout=config.timeout, follow_redirects=False, trust_env=False) as http:
            response = http.request(**kwargs)
    if response.status_code != 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raise JanuaryAPIError(
            "Client-token verification failed",
            status_code=response.status_code,
            code=payload.get("code") if isinstance(payload, dict) else None,
            request_id=response.headers.get("x-request-id"),
        )
    require(isinstance(response.json().get("items"), list), "invalid_token_probe_response")
    from januaryai import ResponseMetadata

    return ResponseMetadata(response.status_code, dict(response.headers))


async def workflow(
    config: Config, mode: str, report: Reporter, *, image_matrix: bool = False
) -> None:
    user_id = f"sdk-e2e-python-{uuid4()}"  # Always fresh; no existing-user override.
    marker = f"January SDK E2E {uuid4()}"
    report.secrets.update((user_id, marker))
    client = (AsyncJanuary if mode == "async" else January)(
        secret_key=config.api_key, max_retries=0, timeout=config.timeout
    )
    user = client.for_user(user_id, end_user_timezone="UTC")
    started = datetime.now(UTC)
    date_range = {"start": started.date().isoformat(), "end": started.date().isoformat()}
    owned_logs: set[str] = set()
    create_attempted = False
    create_acknowledged = False
    mint_attempted = False

    async def step(
        label: str,
        fn: Callable[[], Any],
        *,
        validate: Callable[[Any], None] | None = None,
        blocked: str | None = None,
        kind: str = "operation",
        cleanup: bool = False,
    ) -> Any:
        if blocked:
            report.record(
                mode,
                label,
                "BLOCKED",
                kind=kind,
                code="dependency_failed",
                reason=blocked,
                cleanup=cleanup,
            )
            return None
        clock = time.monotonic()
        value = None
        try:
            value = await invoke(fn)
            if validate:
                validate(value)
        except Exception as error:
            status, request_id = metadata(error if value is None else value)
            code = (
                error.args[0]
                if isinstance(error, CheckFailed)
                else "timeout"
                if isinstance(error, (JanuaryTimeoutError, httpx.TimeoutException))
                else error.code
                if isinstance(error, JanuaryAPIError)
                else "runner_error"
            )
            report.record(
                mode,
                label,
                "FAIL",
                kind=kind,
                code=code,
                request_id=request_id,
                http_status=status,
                duration=time.monotonic() - clock,
                cleanup=cleanup,
            )
            return None
        status, request_id = metadata(value)
        report.record(
            mode,
            label,
            "PASS",
            kind=kind,
            request_id=request_id,
            http_status=status,
            duration=time.monotonic() - clock,
            cleanup=cleanup,
        )
        return value

    try:
        await step("credits", lambda: client.credits())
        found = await step(
            "foods.search",
            lambda: user.foods.search(query=config.query),
            validate=lambda r: require(bool(r.items), "no_food_results"),
        )
        await step("foods.autocomplete", lambda: user.foods.autocomplete(query=config.query[:64]))
        food_id = found.items[0].id if found is not None and found.items else None
        food = await step(
            "foods.get",
            lambda: user.foods.get(food_id=require_value(food_id)),
            blocked="foods.search did not return a food" if food_id is None else None,
        )
        await step(
            "foods.lookup_barcode",
            lambda: user.foods.lookup_barcode(upc=config.upc),
            validate=lambda r: require(bool(r.items), "barcode_not_found"),
        )
        await step(
            "foods.suggest_alternatives",
            lambda: user.foods.suggest_alternatives(food_id=require_value(food_id)),
            blocked="foods.search did not return a food" if food_id is None else None,
        )
        location = {
            "query": config.restaurant_query,
            "latitude": config.latitude,
            "longitude": config.longitude,
        }
        await step("restaurants.search", lambda: user.restaurants.search(**location))
        await step(
            "restaurants.search_menu_items", lambda: user.restaurants.search_menu_items(**location)
        )
        photo = await step(
            "food_analysis.analyze_photo",
            lambda: user.food_analysis.analyze_photo(image=config.image),
            validate=lambda r: require(bool(r.detections), "no_photo_detections"),
        )
        description = await step(
            "food_analysis.analyze_description",
            lambda: user.food_analysis.analyze_description(query="one banana"),
            validate=lambda r: require(bool(r.detections), "no_description_detections"),
        )
        analysis = photo or description
        await step(
            "food_analysis.correct",
            lambda: user.food_analysis.correct(
                meal_name=analysis.meal_name if analysis.meal_name is not None else UNSET,
                detections=analysis.detections,
                user_input="The portion is one serving.",
            ),
            blocked="food analysis did not return detections" if analysis is None else None,
            validate=lambda r: require(bool(r.detections), "no_corrected_detections"),
        )
        if image_matrix:
            with TemporaryDirectory(prefix="january-photo-e2e-") as directory:
                for name in VALID_CASES:
                    # The normal analyze_photo operation already covers the PNG data URI.
                    if name == "data_uri_png":
                        continue

                    async def analyze_case(name=name):
                        with photo_case(
                            name, require_value(config.image_path), Path(directory)
                        ) as parameters:
                            return await invoke(user.food_analysis.analyze_photo, **parameters)

                    await step(
                        f"image.{name}",
                        analyze_case,
                        kind="image",
                        validate=lambda r: require(bool(r.detections), "no_photo_detections"),
                    )
        servings = food.servings if food is not None else []
        serving = next((s for s in servings if s.is_primary), servings[0] if servings else None)
        selection: list[FoodLogInputFoodInput] | None = (
            [{"id": food.id, "serving": {"id": serving.id, "quantity": 1}}]
            if serving is not None and food is not None
            else None
        )

        async def create_log() -> Any:
            nonlocal create_attempted, create_acknowledged
            create_attempted = True  # A timeout may still have created a log server-side.
            log = await invoke(
                user.food_logs.create,
                foods=require_value(selection),
                timestamp_utc=started,
                name=marker,
            )
            if isinstance(log.id, str) and log.id:
                owned_logs.add(log.id)
            create_acknowledged = True
            require(bool(log.id), "missing_created_log_id")
            return log

        created = await step(
            "food_logs.create",
            create_log,
            blocked="foods.get did not return a usable serving" if selection is None else None,
        )

        def check_logs(logs: Any) -> None:
            # This fresh user and unique marker restrict recovery to this one create attempt.
            for log in logs.items:
                if log.name == marker and log.id:
                    owned_logs.add(log.id)
            if created is not None:
                require(
                    any(log.id == created.id for log in logs.items), "created_log_missing_from_list"
                )

        await step(
            "food_logs.list",
            lambda: user.food_logs.list(start=date_range["start"], end=date_range["end"]),
            validate=check_logs,
        )
        await step(
            "food_logs.update",
            lambda: user.food_logs.update(
                log_id=require_value(created).id, name=marker + " updated"
            ),
            blocked="food_logs.create did not return a log" if created is None else None,
            validate=lambda r: require(
                r.id == require_value(created).id, "updated_log_id_mismatch"
            ),
        )
        await step(
            "glucose.predict",
            lambda: user.glucose.predict(
                user_profile={
                    "age": 35,
                    "sex": "female",
                    "height": {"value": 165, "unit": "cm"},
                    "weight": {"value": 65, "unit": "kg"},
                    "activity_level": "moderately_active",
                },
                foods=require_value(selection),
                start_time=started,
            ),
            blocked="foods.get did not return a usable serving" if selection is None else None,
            validate=lambda r: require(bool(r.prediction), "empty_glucose_prediction"),
        )

        async def mint_token() -> Any:
            nonlocal mint_attempted
            mint_attempted = True  # Set before HTTP, including ambiguous timeout failures.
            token = await invoke(
                client.mint_client_token,
                end_user_id=user_id,
                scopes=["foods:read"],
                ttl_seconds=300,
            )
            report.secrets.add(token.token)
            require(token.token.startswith("ct-"), "invalid_client_token")
            require(token.end_user_id == user_id, "token_user_mismatch")
            require(token.scopes == ["foods:read"], "token_scope_mismatch")
            require(
                math.isfinite(token.expires_in) and 0 < token.expires_in <= 300,
                "token_expiry_invalid",
            )
            try:
                expires_at = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00"))
                require(expires_at.utcoffset() is not None, "token_expiry_invalid")
            except ValueError:
                raise CheckFailed("token_expiry_invalid") from None
            return token

        token = await step("mint_client_token", mint_token)
        await step(
            "client_token.foods.search",
            lambda: token_probe(config, token.token, mode),
            kind="check",
            blocked="mint_client_token did not return a verified token" if token is None else None,
        )
    finally:
        try:
            if create_attempted and not create_acknowledged and not owned_logs:

                def recover(logs: Any) -> None:
                    for log in logs.items:
                        if log.name == marker and log.id:
                            owned_logs.add(log.id)

                await step(
                    "cleanup.food_logs.list",
                    lambda: user.food_logs.list(start=date_range["start"], end=date_range["end"]),
                    validate=recover,
                    kind="cleanup",
                    cleanup=True,
                )
                if not owned_logs:
                    await step(
                        "cleanup.food_logs.confirm",
                        lambda: require(False, "ambiguous_create_cleanup_unconfirmed"),
                        kind="cleanup",
                        cleanup=True,
                    )
            ids = sorted(owned_logs)
            if not ids:
                await step(
                    "food_logs.delete", lambda: None, blocked="no run-owned log ID available"
                )
            for index, log_id in enumerate(ids):
                deleted = await step(
                    "food_logs.delete" if index == 0 else "cleanup.food_logs.delete",
                    lambda log_id=log_id: user.food_logs.delete(log_id=log_id),
                    kind="operation" if index == 0 else "cleanup",
                    cleanup=True,
                    validate=lambda r: require(r.status == "deleted", "log_delete_not_confirmed"),
                )
                if deleted is not None:
                    owned_logs.discard(log_id)
        finally:
            try:
                await step(
                    "revoke_client_tokens",
                    lambda: client.revoke_client_tokens(end_user_id=user_id),
                    cleanup=True,
                    blocked="mint_client_token was not attempted" if not mint_attempted else None,
                    validate=lambda r: require(
                        r.status_code == 204 and r.revoked_count is not None,
                        "revocation_not_confirmed",
                    ),
                )
            finally:
                await invoke(client.close)


def main(
    argv: list[str] | None = None,
    *,
    root: Path = ROOT,
    environ: Mapping[str, str] | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(
        description="Explicit opt-in live API demo: creates synthetic data and consumes credits."
    )
    parser.add_argument("--mode", choices=("sync", "async", "both"), default="both")
    parser.add_argument(
        "--image-matrix",
        action="store_true",
        help="Also test every photo input and representative format/transform; adds 17 billable photo requests per mode.",
    )
    args = parser.parse_args(argv)
    modes = ["sync", "async"] if args.mode == "both" else [args.mode]
    report = Reporter(modes, emit, image_matrix=args.image_matrix)
    exit_code = 1
    try:
        config = Config.load(root, os.environ if environ is None else environ)
        report.secrets.add(config.api_key)

        async def run() -> None:
            for mode in modes:
                try:
                    await workflow(config, mode, report, image_matrix=args.image_matrix)
                except Exception:
                    report.record(mode, "runner", "FAIL", kind="check", code="runner_error")

        asyncio.run(run())
    except ConfigError as error:
        report.record("configuration", "configuration", "NOT_RUN", kind="check", code=error.args[0])
        exit_code = 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        report.record("runner", "runner", "FAIL", kind="check", code="interrupted")
        exit_code = 130
    except Exception:
        report.record("runner", "runner", "FAIL", kind="check", code="runner_error")
    report.remaining("run did not reach this operation")
    document = report.document()
    if exit_code == 2:
        document["status"] = "NOT_RUN"
    if document["status"] == "PASS":
        exit_code = 0
    try:
        destination = root / ".e2e-results" / "latest.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    except OSError:
        emit(
            json.dumps(
                {
                    "operation": "report",
                    "status": "FAIL",
                    "code": "report_write_failed",
                    "requestId": None,
                }
            )
        )
        return 1
    emit(
        json.dumps(
            {
                "operation": "summary",
                "status": document["status"],
                "counts": document["counts"],
                "cleanupFailures": document["cleanupFailures"],
                "imageCounts": document["imageCounts"],
            }
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
