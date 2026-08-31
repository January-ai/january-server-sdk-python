"""Generic HTTP runtime. No operation paths or domain schemas live here."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from importlib.resources import files
from threading import Event
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence, cast
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, PrivateAttr, TypeAdapter, ValidationError

from .errors import (JanuaryAPIError, JanuaryCancelledError, JanuaryConfigurationError,
                     JanuaryConnectionError, JanuaryResponseError, JanuaryTimeoutError,
                     JanuaryValidationError)


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
    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="allow", frozen=True, strict=True)
    _response: ResponseMetadata | None = PrivateAttr(default=None)

    @property
    def response(self) -> ResponseMetadata | None:
        return self._response

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


def user_context(end_user_id: str, end_user_timezone: str | None = None) -> Mapping[str, str]:
    if not isinstance(end_user_id, str) or not end_user_id.strip() or any(c in end_user_id for c in "\r\n"):
        raise JanuaryValidationError("end_user_id must be a non-empty header-safe string")
    context = {"end_user_id": end_user_id}
    if end_user_timezone is not None:
        if not isinstance(end_user_timezone, str) or not end_user_timezone.strip() or any(c in end_user_timezone for c in "\r\n"):
            raise JanuaryValidationError("end_user_timezone must be a non-empty header-safe string")
        context["end_user_timezone"] = end_user_timezone
    return MappingProxyType(context)


def bounded_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise JanuaryConfigurationError("timeout must be a finite positive number of seconds")
    return float(value)


def _snake(value: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).replace("-", "_").lower()


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise JanuaryValidationError("Datetimes must include a timezone")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
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
        self.data: dict[str, Any] = json.loads(files("januaryai").joinpath("_contract.json").read_text())

    def resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in schema:
            return {**self.resolve(self.data["schemas"][schema["$ref"].split("/")[-1]]), **{k:v for k,v in schema.items() if k!="$ref"}}
        return schema

    def encode(self, value: Any, schema: dict[str, Any], label: str) -> Any:
        schema = self.resolve(schema)
        value = _json_value(value)
        if value is None:
            if schema.get("nullable"):
                return None
            raise JanuaryValidationError(f"{label} does not accept null; omit it instead")
        for variant in schema.get("allOf", []):
            value = self.encode(value, variant, label)
        kind = schema.get("type")
        valid = ((kind == "string" and isinstance(value, str)) or
                 (kind == "integer" and isinstance(value, int) and not isinstance(value, bool)) or
                 (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)) or
                 (kind == "boolean" and isinstance(value, bool)) or
                 (kind == "array" and isinstance(value, list)) or
                 (kind == "object" and isinstance(value, dict)) or kind is None)
        if not valid:
            raise JanuaryValidationError(f"{label} has an invalid type")
        if "enum" in schema and value not in schema["enum"]:
            raise JanuaryValidationError(f"{label} is not an accepted enum value")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
                raise JanuaryValidationError(f"{label} is outside the allowed length")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise JanuaryValidationError(f"{label} has an invalid format")
            if schema.get("format") == "date-time":
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise JanuaryValidationError(f"{label} must be an ISO timestamp with timezone") from None
            if schema.get("format") == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise JanuaryValidationError(f"{label} must be an ISO calendar date") from None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
                raise JanuaryValidationError(f"{label} is outside the allowed range")
        if isinstance(value, list):
            items = cast(list[Any], value)
            if len(items) < schema.get("minItems", 0) or len(items) > schema.get("maxItems", math.inf):
                raise JanuaryValidationError(f"{label} has an invalid number of items")
            return [self.encode(v, schema.get("items", {}), f"{label}[]") for v in items]
        if isinstance(value, dict) and "properties" in schema:
            mapping = cast(dict[str, Any], value)
            result: dict[str, Any] = {}
            properties = schema["properties"]
            for wire, child in properties.items():
                public = _snake(wire)
                if wire in mapping or public in mapping:
                    result[wire] = self.encode(mapping.get(wire, mapping.get(public)), child, f"{label}.{public}")
                elif wire in schema.get("required", []):
                    raise JanuaryValidationError(f"{label}.{public} is required")
            extras = set(mapping) - set(properties) - {_snake(k) for k in properties}
            if extras:
                raise JanuaryValidationError(f"{label} contains unknown request fields")
            return result
        return cast(Any, value)


class HTTPBase:
    def __init__(self, secret_key: str | None, base_url: str, timeout: float) -> None:
        self._timeout = bounded_timeout(timeout)
        if secret_key is not None and (not isinstance(secret_key, str) or not secret_key.startswith("sk-") or not secret_key[3:].strip() or any(c.isspace() for c in secret_key)):
            raise JanuaryConfigurationError("secret_key must be an sk- server credential, never a client token")
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise JanuaryConfigurationError("base_url must be an absolute URL without credentials, query, or fragment")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise JanuaryConfigurationError("Use HTTPS; HTTP is allowed only for loopback testing")
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._contract = Contract()

    def prepare(self, operation_id: str, values: dict[str, Any], context: Mapping[str, str]) -> dict[str, Any]:
        if self._secret_key is None:
            raise JanuaryConfigurationError("Configure secret_key for server API operations")
        op = self._contract.data["operations"][operation_id]
        path = op["path"]
        headers = {"Authorization": f"Bearer {self._secret_key}", "Accept": "application/json", "User-Agent": "januaryai-server-python/0.0.0"}
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
                if any(c in scalar(value) for c in "\r\n"):
                    raise JanuaryValidationError("Header values cannot contain line breaks")
                headers[parameter["name"]] = scalar(value)
            elif parameter["in"] == "query":
                parts: list[Any] = cast(list[Any], value) if isinstance(value, list) else [value]
                if not parameter.get("explode", True):
                    parts = [",".join(map(scalar, parts))]
                query.extend((parameter["name"], scalar(v)) for v in parts)
            else:
                raise JanuaryConfigurationError("Unsupported contract parameter location")
        request: dict[str, Any] = {"method": op["method"], "url": self._base_url + path, "headers": headers, "params": query}
        if op["bodySchema"] is not None:
            body = {p["name"]: values[p["publicName"]] for p in op["fields"] if not isinstance(values.get(p["publicName"], UNSET), UnsetType)}
            request["json"] = self._contract.encode(body, op["bodySchema"], "request")
        return request

    def decode(self, operation_id: str, response: httpx.Response, response_type: Any, sensitive: dict[str, Any]) -> Any:
        def redact_credentials(value: str) -> str:
            if self._secret_key:
                value = value.replace(self._secret_key, "[redacted]")
            return re.sub(r"\b(?:sk|ct)-[A-Za-z0-9_-]+", "[redacted]", value)

        def redact(value: str) -> str:
            strings: list[str] = [self._secret_key or ""]
            def collect(x: Any) -> None:
                if isinstance(x, str): strings.append(x)
                elif isinstance(x, dict):
                    for v in cast(dict[str, Any], x).values(): collect(v)
                elif isinstance(x, (list, tuple)):
                    for v in cast(Sequence[Any], x): collect(v)
            collect(sensitive.get("json", {}))
            collect(sensitive.get("params", []))
            collect({k:v for k,v in sensitive["headers"].items() if k.lower().startswith("x-end-user-")})
            for secret in sorted(set(strings), key=len, reverse=True):
                if len(secret) >= 4: value = value.replace(secret, "[redacted]")
            return redact_credentials(value)

        def safe_header(name: str) -> bool:
            name = name.lower().replace("_", "-")
            return not any(part in name for part in ("authorization", "cookie", "token", "api-key", "apikey", "secret", "end-user"))

        metadata = ResponseMetadata(response.status_code, MappingProxyType({k:redact_credentials(v) for k,v in response.headers.items() if safe_header(k)}))
        if not 200 <= response.status_code < 300:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict): payload = {}
            payload_data = cast(dict[str, Any], payload)
            def string(k: str) -> str | None:
                value = payload_data.get(k)
                return redact_credentials(value) if isinstance(value, str) else None
            raise JanuaryAPIError(redact(string("message") or "January rejected the request"), status_code=response.status_code,
                                  code=string("code"), docs_url=string("docs_url"), request_id=metadata.request_id, response=metadata)
        if str(response.status_code) not in self._contract.data["operations"][operation_id]["responses"]:
            raise JanuaryResponseError("Unexpected success status", status_code=response.status_code, request_id=metadata.request_id, response=metadata)
        if response_type is ResponseMetadata:
            return metadata
        try:
            model = TypeAdapter(response_type).validate_json(response.content)
        except (ValidationError, ValueError) as error:
            raise JanuaryResponseError("Response did not match the contract", status_code=response.status_code,
                                       request_id=metadata.request_id, response=metadata, cause=error) from None
        if isinstance(model, APIModel):
            model._response = metadata  # pyright: ignore[reportPrivateUsage] -- runtime-owned metadata
        return model


class SyncHTTP(HTTPBase):
    def __init__(self, secret_key: str | None, base_url: str, timeout: float, client: httpx.Client | None = None) -> None:
        super().__init__(secret_key, base_url, timeout)
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client: self._client.close()

    def request(self, operation_id: str, values: dict[str, Any], response_type: Any,
                context: Mapping[str, str], timeout: float | None, cancel_event: Event | None = None) -> Any:
        timeout = bounded_timeout(self._timeout if timeout is None else timeout)
        request = self.prepare(operation_id, values, context)
        deadline = time.monotonic() + timeout
        def check() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise JanuaryCancelledError("January request cancelled")
            if time.monotonic() >= deadline:
                raise JanuaryTimeoutError()
        check()
        try:
            with self._client.stream(**request, timeout=timeout, follow_redirects=False) as response:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    check()
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise JanuaryResponseError("Response size limit exceeded", status_code=response.status_code)
                    chunks.append(chunk)
                check()
                materialized = httpx.Response(response.status_code, headers=response.headers, content=b"".join(chunks))
            return self.decode(operation_id, materialized, response_type, request)
        except httpx.TimeoutException as error:
            raise JanuaryTimeoutError(error) from None
        except httpx.HTTPError as error:
            raise JanuaryConnectionError(error) from None


class AsyncHTTP(HTTPBase):
    def __init__(self, secret_key: str | None, base_url: str, timeout: float, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(secret_key, base_url, timeout)
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client: await self._client.aclose()

    async def request(self, operation_id: str, values: dict[str, Any], response_type: Any,
                      context: Mapping[str, str], timeout: float | None) -> Any:
        timeout = bounded_timeout(self._timeout if timeout is None else timeout)
        request = self.prepare(operation_id, values, context)
        async def perform() -> Any:
            async with self._client.stream(**request, timeout=timeout, follow_redirects=False) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise JanuaryResponseError("Response size limit exceeded", status_code=response.status_code)
                    chunks.append(chunk)
                materialized = httpx.Response(response.status_code, headers=response.headers, content=b"".join(chunks))
            return self.decode(operation_id, materialized, response_type, request)
        try:
            return await asyncio.wait_for(perform(), timeout)
        except (httpx.TimeoutException, asyncio.TimeoutError) as error:
            raise JanuaryTimeoutError(error) from None
        except httpx.HTTPError as error:
            raise JanuaryConnectionError(error) from None


@dataclass(frozen=True, repr=False)
class SyncResource:
    _transport: SyncHTTP
    _context: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, repr=False)
class AsyncResource:
    _transport: AsyncHTTP
    _context: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
