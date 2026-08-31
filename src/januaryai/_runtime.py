# pyright: reportUnnecessaryIsInstance=false
"""Generic HTTP runtime. No operation paths or domain schemas live here."""

from __future__ import annotations

import json
import math
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from importlib.resources import files
from threading import Event
from types import MappingProxyType
from typing import Any, ClassVar, cast
from urllib.parse import quote, urlsplit

import anyio
import httpx
from anyio.to_thread import run_sync
from pydantic import BaseModel, ConfigDict, PrivateAttr, TypeAdapter, ValidationError

from ._backoff import classify_transport_error, compute_delay, should_retry_response
from ._constants import DEFAULT_MAX_RETRIES, MAX_HONORED_RETRY_AFTER, MAX_TOTAL_RETRY_AFTER_WAIT
from ._images import prepare_image
from ._version import __version__
from .errors import (
    JanuaryAPIError,
    JanuaryCancelledError,
    JanuaryConfigurationError,
    JanuaryConnectionError,
    JanuaryError,
    JanuaryResponseError,
    JanuaryTimeoutError,
    JanuaryValidationError,
    api_error_type,
)


class UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = UnsetType()
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ResponseMetadata:
    status_code: int
    headers: Mapping[str, str] = field(repr=False)

    @property
    def request_id(self) -> str | None:
        return self.headers.get("x-request-id") or self.headers.get("request-id")

    @property
    def revoked_count(self) -> int | None:
        value = self.headers.get("x-revoked-count")
        return int(value) if value is not None and value.isdigit() else None

    @property
    def retry_after(self) -> str | None:
        return self.headers.get("retry-after")


class APIModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        populate_by_name=True, extra="allow", frozen=True, strict=True
    )
    _response: ResponseMetadata | None = PrivateAttr(default=None)
    _response_origin: bool = PrivateAttr(default=False)

    @property
    def response(self) -> ResponseMetadata | None:
        return self._response

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


def user_context(end_user_id: str, end_user_timezone: str | None = None) -> Mapping[str, str]:
    if (
        not isinstance(end_user_id, str)
        or not end_user_id.strip()
        or any(c in end_user_id for c in "\r\n")
    ):
        raise JanuaryValidationError("end_user_id must be a non-empty header-safe string")
    context = {"end_user_id": end_user_id}
    if end_user_timezone is not None:
        if (
            not isinstance(end_user_timezone, str)
            or not end_user_timezone.strip()
            or any(c in end_user_timezone for c in "\r\n")
        ):
            raise JanuaryValidationError("end_user_timezone must be a non-empty header-safe string")
        context["end_user_timezone"] = end_user_timezone
    return MappingProxyType(context)


def bounded_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise JanuaryConfigurationError("timeout must be a finite positive number of seconds")
    return float(value)


def parse_api_datetime(value: str) -> datetime | None:
    """Parse an aware ISO timestamp, returning None for opaque/naive values."""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except ValueError:
        return None


def phase_timeout(value: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        for phase in value.as_dict().values():
            if phase is None:
                raise JanuaryConfigurationError("Timeout phases must be finite positive seconds")
            bounded_timeout(phase)
        return value
    return httpx.Timeout(bounded_timeout(value))


def _mark_response(value: Any) -> None:
    if isinstance(value, APIModel):
        value._response_origin = True  # pyright: ignore[reportPrivateUsage] -- runtime-owned provenance
        for name in type(value).model_fields:
            _mark_response(getattr(value, name))
    elif isinstance(value, list):
        for item in cast(list[Any], value):
            _mark_response(item)


def _snake(value: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).replace("-", "_").lower()


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise JanuaryValidationError("Datetimes must include a timezone")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        return {key: _json_value(v) for key, v in mapping.items() if not isinstance(v, UnsetType)}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in cast(Sequence[Any], value)]
    return value


class Contract:
    def __init__(self) -> None:
        self.data: dict[str, Any] = json.loads(
            files("januaryai").joinpath("_contract.json").read_text()
        )

    def resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in schema:
            return {
                **self.resolve(self.data["schemas"][schema["$ref"].split("/")[-1]]),
                **{k: v for k, v in schema.items() if k != "$ref"},
            }
        return schema

    def encode(
        self, value: Any, schema: dict[str, Any], label: str, *, allow_response_fields: bool = False
    ) -> Any:
        schema = self.resolve(schema)
        response_model = isinstance(value, APIModel) and getattr(value, "_response_origin", False)
        if isinstance(value, BaseModel):
            value = {
                **{
                    field.alias or name: getattr(value, name)
                    for name, field in type(value).model_fields.items()
                    if name in value.model_fields_set
                },
                **(value.model_extra or {}),
            }
        elif isinstance(value, datetime) and schema.get("format") == "date":
            # A day range uses the caller's calendar date, not a UTC conversion.
            value = value.date().isoformat()
        elif isinstance(value, (datetime, date)):
            value = _json_value(value)
        if value is None:
            if schema.get("nullable"):
                return None
            raise JanuaryValidationError(f"{label} does not accept null; omit it instead")
        for variant in schema.get("allOf", []):
            value = self.encode(value, variant, label, allow_response_fields=allow_response_fields)
        kind = schema.get("type")
        if (
            kind == "array"
            and isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
        ):
            value = list(cast(Sequence[Any], value))
        valid = (
            (kind == "string" and isinstance(value, str))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (
                kind == "number"
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
            or (kind == "boolean" and isinstance(value, bool))
            or (kind == "array" and isinstance(value, list))
            or (kind == "object" and isinstance(value, dict))
            or kind is None
        )
        if not valid:
            raise JanuaryValidationError(f"{label} has an invalid type")
        if "enum" in schema and value not in schema["enum"]:
            raise JanuaryValidationError(f"{label} is not an accepted enum value")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0) or len(value) > schema.get(
                "maxLength", math.inf
            ):
                raise JanuaryValidationError(f"{label} is outside the allowed length")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise JanuaryValidationError(f"{label} has an invalid format")
            if schema.get("format") == "date-time":
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise JanuaryValidationError(
                        f"{label} must be an ISO timestamp with timezone"
                    ) from None
            if schema.get("format") == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise JanuaryValidationError(f"{label} must be an ISO calendar date") from None
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (
                value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf)
            )
        ):
            raise JanuaryValidationError(f"{label} is outside the allowed range")
        if isinstance(value, list):
            items = cast(list[Any], value)
            if len(items) < schema.get("minItems", 0) or len(items) > schema.get(
                "maxItems", math.inf
            ):
                raise JanuaryValidationError(f"{label} has an invalid number of items")
            return [
                self.encode(
                    v,
                    schema.get("items", {}),
                    f"{label}[]",
                    allow_response_fields=allow_response_fields,
                )
                for v in items
            ]
        if isinstance(value, dict) and "properties" in schema:
            mapping = cast(dict[str, Any], value)
            result: dict[str, Any] = {}
            properties = schema["properties"]
            for wire, child in properties.items():
                public = _snake(wire)
                if wire in mapping or public in mapping:
                    item = mapping.get(wire, mapping.get(public))
                    if (
                        response_model
                        and allow_response_fields
                        and item is None
                        and wire not in schema.get("required", [])
                        and not self.resolve(child).get("nullable")
                    ):
                        continue
                    result[wire] = self.encode(
                        item,
                        child,
                        f"{label}.{public}",
                        allow_response_fields=allow_response_fields,
                    )
                elif wire in schema.get("required", []):
                    raise JanuaryValidationError(f"{label}.{public} is required")
            extras = set(mapping) - set(properties) - {_snake(k) for k in properties}
            if extras and not (allow_response_fields and response_model):
                raise JanuaryValidationError(f"{label} contains unknown request fields")
            for extra in extras:
                result[extra] = _json_value(mapping[extra])
            return result
        return cast(Any, value)


class HTTPBase:
    def __init__(
        self,
        secret_key: str | None,
        base_url: str,
        timeout: float | httpx.Timeout | None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._explicit_timeout = timeout is not None
        self._timeout = phase_timeout(
            timeout if timeout is not None else httpx.Timeout(60, connect=5)
        )
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise JanuaryConfigurationError("max_retries must be a nonnegative integer")
        self._max_retries = max_retries
        self._rng = random.Random()
        self._default_headers = dict(default_headers or {})
        for name, value in self._default_headers.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not name.isascii()
                or not value.isascii()
                or any(ord(c) < 32 or ord(c) == 127 for c in name + value)
            ):
                raise JanuaryConfigurationError(
                    "default_headers must contain ASCII header-safe strings"
                )
            if name.lower() in {
                "authorization",
                "proxy-authorization",
                "cookie",
                "host",
                "content-length",
                "transfer-encoding",
            } or name.lower().startswith("x-end-user-"):
                raise JanuaryConfigurationError(
                    "Authentication and user-context headers are controlled by the SDK"
                )
        if secret_key is not None and (
            not isinstance(secret_key, str)
            or not secret_key.startswith("sk-")
            or not secret_key[3:].strip()
            or any(c.isspace() for c in secret_key)
        ):
            raise JanuaryConfigurationError(
                "secret_key must be an sk- server credential, never a client token"
            )
        parsed = urlsplit(base_url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise JanuaryConfigurationError(
                "base_url must be an absolute URL without credentials, query, or fragment"
            )
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise JanuaryConfigurationError("Use HTTPS; HTTP is allowed only for loopback testing")
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._contract = Contract()

    def prepare(
        self, operation_id: str, values: dict[str, Any], context: Mapping[str, str]
    ) -> dict[str, Any]:
        if self._secret_key is None:
            raise JanuaryConfigurationError("Configure secret_key for server API operations")
        op = self._contract.data["operations"][operation_id]
        path = op["path"]
        headers = {
            k: v
            for k, v in self._default_headers.items()
            if k.lower() not in {"accept", "user-agent"}
        }
        user_agent = next(
            (v for k, v in self._default_headers.items() if k.lower() == "user-agent"),
            f"januaryai-server-python/{__version__}",
        )
        headers.update(
            {
                "Accept": "application/json",
                "User-Agent": user_agent,
            }
        )
        query: list[tuple[str, str]] = []
        for parameter in op["parameters"]:
            public = parameter["publicName"]
            value = values.get(public, UNSET)
            if parameter["in"] == "header" and public in context:
                value = context[public]
            if isinstance(value, UnsetType):
                if parameter.get("required"):
                    raise JanuaryValidationError(f"{public} is required (or bind for_user)")
                continue
            value = self._contract.encode(value, parameter["schema"], public)

            def scalar(x: Any) -> str:
                return str(x).lower() if isinstance(x, bool) else str(x)

            if parameter["in"] == "path":
                encoded = quote(scalar(value), safe="")
                if encoded in {".", ".."}:
                    raise JanuaryValidationError("Path identifiers cannot be dot segments")
                path = path.replace("{" + parameter["name"] + "}", encoded)
            elif parameter["in"] == "header":
                if not scalar(value).isascii() or any(
                    ord(c) < 32 or ord(c) == 127 for c in scalar(value)
                ):
                    raise JanuaryValidationError(
                        f"{public} must contain printable ASCII characters"
                    )
                headers[parameter["name"]] = scalar(value)
            elif parameter["in"] == "query":
                parts: list[Any] = cast(list[Any], value) if isinstance(value, list) else [value]
                if not parameter.get("explode", True):
                    parts = [",".join(map(scalar, parts))]
                query.extend((parameter["name"], scalar(v)) for v in parts)
            else:
                raise JanuaryConfigurationError("Unsupported contract parameter location")
        request: dict[str, Any] = {
            "method": op["method"],
            "url": self._base_url + path,
            "headers": headers,
            "params": query,
        }
        if op["bodySchema"] is not None:
            body = {
                p["name"]: values[p["publicName"]]
                for p in op["fields"]
                if not isinstance(values.get(p["publicName"], UNSET), UnsetType)
            }
            # Only returned detection models may preserve additive response fields.
            # Raw dictionaries, other requests, and known fields stay schema-validated.
            request["json"] = self._contract.encode(
                body,
                op["bodySchema"],
                "request",
                allow_response_fields=operation_id == "correctPhotoScan",
            )
        return request

    def decode(
        self,
        operation_id: str,
        response: httpx.Response,
        response_type: Any,
        sensitive: dict[str, Any],
    ) -> Any:
        def redact_credentials(value: str) -> str:
            if self._secret_key:
                value = value.replace(self._secret_key, "[redacted]")
            return re.sub(r"\b(?:sk|ct)-[A-Za-z0-9_-]+", "[redacted]", value)

        def redact(value: str) -> str:
            strings: list[str] = [self._secret_key or ""]

            def collect(x: Any) -> None:
                if isinstance(x, str):
                    strings.append(x)
                elif isinstance(x, dict):
                    for v in cast(dict[str, Any], x).values():
                        collect(v)
                elif isinstance(x, (list, tuple)):
                    for v in cast(Sequence[Any], x):
                        collect(v)

            collect(sensitive.get("json", {}))
            collect(sensitive.get("params", []))
            collect(
                {
                    k: v
                    for k, v in sensitive["headers"].items()
                    if k.lower().startswith("x-end-user-")
                }
            )
            for secret in sorted(set(strings), key=len, reverse=True):
                if len(secret) >= 4:
                    value = value.replace(secret, "[redacted]")
            return redact_credentials(value)

        def safe_header(name: str) -> bool:
            name = name.lower().replace("_", "-")
            return not any(
                part in name
                for part in (
                    "authorization",
                    "cookie",
                    "token",
                    "api-key",
                    "apikey",
                    "secret",
                    "end-user",
                )
            )

        metadata = ResponseMetadata(
            response.status_code,
            MappingProxyType(
                {k: redact_credentials(v) for k, v in response.headers.items() if safe_header(k)}
            ),
        )
        if 300 <= response.status_code < 400:
            # Do not echo Location: it can contain tokens or other sensitive data.
            raise JanuaryError(
                f"January returned HTTP {response.status_code} redirect. Redirects are not "
                "followed; check the configured API origin."
            )
        if not 200 <= response.status_code < 300:
            try:
                payload = json.loads(redact_credentials(response.text))
            except ValueError:
                payload = redact(response.text)
            payload_data = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}

            def string(k: str) -> str | None:
                value = payload_data.get(k)
                return value if isinstance(value, str) and value.strip() else None

            def redact_body(value: Any) -> Any:
                if isinstance(value, str):
                    return redact(value)
                if isinstance(value, dict):
                    return {
                        redact(k): redact_body(v) for k, v in cast(dict[str, Any], value).items()
                    }
                if isinstance(value, list):
                    return [redact_body(v) for v in cast(list[Any], value)]
                return value

            message = string("message")
            if message is None:
                message = (
                    redact(response.text).strip() if not isinstance(payload, dict) else ""
                ) or f"HTTP {response.status_code}"

            raise api_error_type(response.status_code, string("code"))(
                redact(message),
                status_code=response.status_code,
                code=string("code"),
                docs_url=string("docs_url"),
                request_id=metadata.request_id,
                response=metadata,
                body=redact_body(payload) if response.content else None,
            )
        if (
            str(response.status_code)
            not in self._contract.data["operations"][operation_id]["responses"]
        ):
            raise JanuaryResponseError(
                "Unexpected success status",
                status_code=response.status_code,
                request_id=metadata.request_id,
                response=metadata,
            )
        if response_type is ResponseMetadata:
            return metadata
        try:
            model = TypeAdapter(response_type).validate_json(response.content)
        except (ValidationError, ValueError) as error:
            raise JanuaryResponseError(
                "Response did not match the contract",
                status_code=response.status_code,
                request_id=metadata.request_id,
                response=metadata,
                cause=error,
            ) from None
        if isinstance(model, APIModel):
            _mark_response(model)
            model._response = metadata  # pyright: ignore[reportPrivateUsage] -- runtime-owned metadata
        return model

    def request_timeout(
        self, operation_id: str, timeout: float | httpx.Timeout | None
    ) -> httpx.Timeout:
        if timeout is not None:
            return phase_timeout(timeout)
        if not self._explicit_timeout and self._contract.data["operations"][operation_id].get(
            "photoPreparation", False
        ):
            return httpx.Timeout(120, connect=5)
        if not self._explicit_timeout and operation_id in {
            "searchFoodsByNaturalLanguage",
            "correctPhotoScan",
        }:
            return httpx.Timeout(120, connect=5)
        return self._timeout

    def retry_delay(
        self,
        operation_id: str,
        error: JanuaryAPIError | JanuaryConnectionError,
        attempt: int,
        waited: float,
    ) -> float | None:
        if attempt >= self._max_retries:
            return None
        operation = self._contract.data["operations"][operation_id]
        if operation.get("retryNever", False):
            return None
        if isinstance(error, JanuaryAPIError):
            if not should_retry_response(error.status_code, error.code):
                return None
            if error.status_code != 429 and not operation.get("retryAmbiguous", False):
                return None
            delay = error.retry_after
            if delay is not None:
                if delay > MAX_HONORED_RETRY_AFTER:
                    error.add_note(
                        f"The server requested {delay:g}s, above the SDK's "
                        f"{MAX_HONORED_RETRY_AFTER:g}s per-wait limit. No wait was made; "
                        "retry later using .retry_after."
                    )
                    return None
                if waited + delay > MAX_TOTAL_RETRY_AFTER_WAIT:
                    error.add_note(
                        f"The SDK already honored {waited:g}s of server-requested waiting. "
                        f"Another {delay:g}s exceeds the {MAX_TOTAL_RETRY_AFTER_WAIT:g}s "
                        "total limit; retry later using .retry_after."
                    )
                    return None
                return delay
        else:
            kind = (
                classify_transport_error(error.cause)
                if isinstance(error.cause, Exception)
                else "fatal"
            )
            if kind != "pre_send" and not (
                kind == "ambiguous" and operation.get("retryAmbiguous", False)
            ):
                return None
        return compute_delay(attempt, rng=self._rng)


class SyncHTTP(HTTPBase):
    def __init__(
        self,
        secret_key: str | None,
        base_url: str,
        timeout: float | httpx.Timeout | None,
        client: httpx.Client | None = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            secret_key, base_url, timeout, max_retries=max_retries, default_headers=default_headers
        )
        self._client = client or httpx.Client(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        )
        self._owns_client = client is None
        self._sleep: Callable[[float], None] = time.sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request(
        self,
        operation_id: str,
        values: dict[str, Any],
        response_type: Any,
        context: Mapping[str, str],
        timeout: float | httpx.Timeout | None,
        cancel_event: Event | None = None,
    ) -> Any:
        if self._client.is_closed:
            raise JanuaryError(
                "This January client is closed; create a new client to make requests."
            )
        phases = self.request_timeout(operation_id, timeout)
        deadline = time.monotonic() + max(cast(float, v) for v in phases.as_dict().values())

        def check() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise JanuaryCancelledError("January request cancelled")
            if time.monotonic() >= deadline:
                raise JanuaryTimeoutError()

        check()
        if self._contract.data["operations"][operation_id].get("photoPreparation", False):
            values = {
                **values,
                "image": prepare_image(values["image"], preprocess=values.get("preprocess", True)),
            }
        request = self.prepare(operation_id, values, context)
        waited = 0.0
        for attempt in range(self._max_retries + 1):
            check()
            remaining = deadline - time.monotonic()
            bounded = httpx.Timeout(
                **{k: min(cast(float, v), remaining) for k, v in phases.as_dict().items()}
            )
            try:
                return self._once(operation_id, request, response_type, bounded, check)
            except (JanuaryAPIError, JanuaryConnectionError) as error:
                delay = self.retry_delay(operation_id, error, attempt, waited)
                if delay is None:
                    raise
                if delay >= deadline - time.monotonic():
                    error.add_note(
                        "Retry waiting would exceed the request timeout; no wait was made."
                    )
                    raise
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        raise JanuaryCancelledError("January request cancelled") from None
                else:
                    self._sleep(delay)
                if isinstance(error, JanuaryAPIError) and error.retry_after is not None:
                    waited += delay
        raise AssertionError("Unreachable retry state")

    def _once(
        self,
        operation_id: str,
        request: dict[str, Any],
        response_type: Any,
        timeout: httpx.Timeout,
        check: Any,
    ) -> Any:
        chunks: list[bytes] = []
        chunk = b""
        try:
            with self._client.stream(
                # Never retain an Authorization-bearing dictionary in an SDK frame.
                **cast(
                    dict[str, Any],
                    {
                        **request,
                        "headers": {
                            **request["headers"],
                            "Authorization": f"Bearer {self._secret_key}",
                        },
                    },
                ),
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                size = 0
                for chunk in response.iter_bytes():
                    check()
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise JanuaryResponseError(
                            "Response size limit exceeded", status_code=response.status_code
                        )
                    chunks.append(chunk)
                check()
                materialized = httpx.Response(
                    response.status_code, headers=response.headers, content=b"".join(chunks)
                )
            return self.decode(operation_id, materialized, response_type, request)
        except httpx.TimeoutException as error:
            raise JanuaryTimeoutError(error) from None
        except httpx.HTTPError as error:
            raise JanuaryConnectionError(error) from None
        finally:
            # A server may echo a credential. Do not retain raw body buffers in
            # exception-frame locals, including interrupted streamed responses.
            chunks.clear()
            chunk = b""


class AsyncHTTP(HTTPBase):
    def __init__(
        self,
        secret_key: str | None,
        base_url: str,
        timeout: float | httpx.Timeout | None,
        client: httpx.AsyncClient | None = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            secret_key, base_url, timeout, max_retries=max_retries, default_headers=default_headers
        )
        self._client = client or httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, trust_env=False
        )
        self._owns_client = client is None
        self._sleep = anyio.sleep

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        operation_id: str,
        values: dict[str, Any],
        response_type: Any,
        context: Mapping[str, str],
        timeout: float | httpx.Timeout | None,
    ) -> Any:
        if self._client.is_closed:
            raise JanuaryError(
                "This AsyncJanuary client is closed; create a new client to make requests."
            )
        phases = self.request_timeout(operation_id, timeout)
        budget = max(cast(float, v) for v in phases.as_dict().values())
        deadline = time.monotonic() + budget
        try:
            with anyio.fail_after(budget):
                if self._contract.data["operations"][operation_id].get("photoPreparation", False):
                    prepared = await run_sync(
                        partial(
                            prepare_image,
                            values["image"],
                            preprocess=values.get("preprocess", True),
                        ),
                        abandon_on_cancel=True,
                    )
                    values = {**values, "image": prepared}
                request = self.prepare(operation_id, values, context)
                waited = 0.0
                for attempt in range(self._max_retries + 1):
                    try:
                        return await self._once(operation_id, request, response_type, phases)
                    except (JanuaryAPIError, JanuaryConnectionError) as error:
                        delay = self.retry_delay(operation_id, error, attempt, waited)
                        if delay is None:
                            raise
                        if delay >= deadline - time.monotonic():
                            error.add_note(
                                "Retry waiting would exceed the request timeout; no wait was made."
                            )
                            raise
                        await self._sleep(delay)
                        if isinstance(error, JanuaryAPIError) and error.retry_after is not None:
                            waited += delay
        except TimeoutError as error:
            raise JanuaryTimeoutError(error) from None
        raise AssertionError("Unreachable retry state")

    async def _once(
        self, operation_id: str, request: dict[str, Any], response_type: Any, timeout: httpx.Timeout
    ) -> Any:
        chunks: list[bytes] = []
        chunk = b""
        try:
            async with self._client.stream(
                **cast(
                    dict[str, Any],
                    {
                        **request,
                        "headers": {
                            **request["headers"],
                            "Authorization": f"Bearer {self._secret_key}",
                        },
                    },
                ),
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise JanuaryResponseError(
                            "Response size limit exceeded", status_code=response.status_code
                        )
                    chunks.append(chunk)
                materialized = httpx.Response(
                    response.status_code, headers=response.headers, content=b"".join(chunks)
                )
            return self.decode(operation_id, materialized, response_type, request)
        except httpx.TimeoutException as error:
            raise JanuaryTimeoutError(error) from None
        except httpx.HTTPError as error:
            raise JanuaryConnectionError(error) from None
        finally:
            # A server may echo a credential. Do not retain raw body buffers in
            # exception-frame locals, including interrupted streamed responses.
            chunks.clear()
            chunk = b""


@dataclass(frozen=True, repr=False)
class SyncResource:
    _transport: SyncHTTP
    _context: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, repr=False)
class AsyncResource:
    _transport: AsyncHTTP
    _context: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
