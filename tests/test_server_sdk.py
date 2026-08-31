import asyncio
import inspect
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from installed_consumer import FIXTURES, arguments, assert_request, local_service, method

from januaryai import (
    AsyncJanuary,
    January,
    JanuaryAPIError,
    JanuaryCancelledError,
    JanuaryConfigurationError,
    JanuaryError,
    JanuaryResponseError,
    JanuaryTimeoutError,
    JanuaryValidationError,
    ResponseMetadata,
    models,
)
from januaryai._runtime import Contract


@pytest.mark.parametrize("fixture", FIXTURES["operations"], ids=lambda f: f["operationId"])
@pytest.mark.parametrize("async_mode", [False, True])
def test_all_operations(fixture, async_mode):
    with local_service() as service:
        service["response"] = fixture["response"]

        async def run():
            async with AsyncJanuary(
                max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
            ) as client:
                return await method(client, fixture)(**arguments(fixture))

        if async_mode:
            result = asyncio.run(run())
        else:
            with January(
                max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
            ) as client:
                result = method(client, fixture)(**arguments(fixture))
        assert len(service["requests"]) == 1
        assert_request(service["requests"][0], fixture)
        if fixture["operationId"] == "revokeClientTokens":
            assert isinstance(result, ResponseMetadata)
            assert result.status_code == 204
            assert result.revoked_count == 3
        else:
            assert (
                result.model_dump(by_alias=True, exclude_unset=True) == fixture["response"]["body"]
            )
            assert result.response.request_id == fixture["response"]["headers"]["x-request-id"]


@pytest.mark.parametrize("error", FIXTURES["errors"])
@pytest.mark.parametrize("async_mode", [False, True])
def test_structured_errors_never_retry(error, async_mode):
    with local_service() as service:
        service["response"] = error

        async def run():
            async with AsyncJanuary(
                max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
            ) as client:
                await client.credits()

        with pytest.raises(JanuaryAPIError) as caught:
            if async_mode:
                asyncio.run(run())
            else:
                with January(
                    max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
                ) as client:
                    client.credits()
        e = caught.value
        assert e.code == error["body"]["code"]
        assert e.docs_url == error["body"]["docs_url"]
        assert e.status_code == error["status"]
        assert e.request_id == error["headers"]["x-request-id"]
        assert len(service["requests"]) == 1


def test_root_surface_and_immutable_user_views():
    with local_service() as service:
        fixture = FIXTURES["operations"][0]
        service["response"] = fixture["response"]
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            a, b = client.for_user("alice"), client.for_user("bob")
            for name in ["mint_client_token", "revoke_client_tokens", "credits", "client_tokens"]:
                assert not hasattr(a, name)
            with pytest.raises(FrozenInstanceError):
                a._context = {}  # pyright: ignore[reportAttributeAccessIssue] -- intentionally test immutable assignment
            with pytest.raises(TypeError):
                a._context["end_user_id"] = "bob"  # pyright: ignore[reportIndexIssue] -- intentionally test immutable mapping
            a.foods.search(query="eggs")
            b.foods.search(query="eggs")
            client.foods.search(query="eggs")
            assert [r["headers"].get("x-end-user-id") for r in service["requests"]] == [
                "alice",
                "bob",
                None,
            ]
            a.foods.search(query="eggs", end_user_id="bob")
            assert service["requests"][-1]["headers"]["x-end-user-id"] == "alice"
            client.for_user("user-" + "x" * 100).foods.search(query="eggs")
            assert len(service["requests"][-1]["headers"]["x-end-user-id"]) > 64
            assert not hasattr(client, "revoke_all_for_user")


def test_async_user_context_is_concurrency_safe():
    with local_service() as service:
        service["response"] = FIXTURES["operations"][0]["response"]

        async def run():
            async with AsyncJanuary(
                max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
            ) as client:
                await asyncio.gather(
                    *(client.for_user(str(i)).foods.search(query="eggs") for i in range(10))
                )

        asyncio.run(run())
        assert {r["headers"]["x-end-user-id"] for r in service["requests"]} == {
            str(i) for i in range(10)
        }


def test_single_revoke_even_at_500_and_encoding():
    fixture = deepcopy(
        next(f for f in FIXTURES["operations"] if f["operationId"] == "revokeClientTokens")
    )
    fixture["response"]["headers"]["X-Revoked-Count"] = "500"
    with local_service() as service:
        service["response"] = fixture["response"]
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            result = client.revoke_client_tokens(end_user_id="user & + / ?")
        assert result.revoked_count == 500
        assert len(service["requests"]) == 1
        request = service["requests"][0]
        assert request["body"] is None
        assert parse_qs(urlsplit(request["path"]).query) == {"end_user_id": ["user & + / ?"]}


def test_path_encoding_and_date_serialization():
    fixture = next(f for f in FIXTURES["operations"] if f["operationId"] == "updateFoodLog")
    with local_service() as service:
        service["response"] = fixture["response"]
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            client.for_user("user", end_user_timezone="America/New_York").food_logs.update(
                log_id="a/b ?#", name="Lunch"
            )
        request = service["requests"][0]
        assert request["path"].endswith("/a%2Fb%20%3F%23")
        assert request["headers"]["x-end-user-timezone"] == "America/New_York"
        assert request["body"] == {"name": "Lunch"}


def test_forward_compatible_responses_and_redaction():
    fixture = deepcopy(
        next(f for f in FIXTURES["operations"] if f["operationId"] == "mintClientToken")
    )
    fixture["response"]["body"]["scopes"] = ["future:scope"]
    fixture["response"]["body"]["future"] = "sensitive-extra"
    with local_service() as service:
        service["response"] = fixture["response"]
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            result = client.mint_client_token(end_user_id="user")
            assert result.scopes == ["future:scope"]
            assert result.model_dump()["future"] == "sensitive-extra"
            assert "sensitive-extra" not in repr(result)
            assert result.token not in repr(result)
            assert "sk-local-fixture" not in repr(client)
            service["response"] = {
                "status": 400,
                "headers": {},
                "body": {
                    "code": "bad_input",
                    "docs_url": "https://example.com/docs",
                    "message": "sk-local-fixture private-user private meal",
                },
            }
            with pytest.raises(JanuaryAPIError) as caught:
                client.food_analysis.analyze_description(
                    query="private meal", end_user_id="private-user"
                )
            assert "private" not in str(caught.value)
            assert "private" not in caught.value.message


def test_uncapped_balance():
    with local_service() as service:
        service["response"] = {"status": 200, "headers": {}, "body": FIXTURES["uncappedCredits"]}
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            result = client.credits()
        assert result.remaining_credits is None
        assert "remaining_credits" not in result.model_fields_set


def test_short_query_preserves_machine_metadata_and_filters_credentials():
    response = {
        "status": 429,
        "headers": {
            "x-request-id": "request-abc",
            "x-api-key": "hidden",
            "X-Token": "hidden",
            "X-Client-Token": "hidden",
            "Set-Cookie": "hidden",
            "Retry-After": "2",
        },
        "body": {
            "code": "rate_limited",
            "docs_url": "https://partners.january.ai/v1.2/docs",
            "message": "Rate limit exceeded",
        },
    }
    with local_service() as service:
        service["response"] = response
        with (
            January(
                max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
            ) as client,
            pytest.raises(JanuaryAPIError) as caught,
        ):
            client.foods.search(query="a")
        error = caught.value
        assert error.code == "rate_limited"
        assert error.docs_url == response["body"]["docs_url"]
        assert error.request_id == "request-abc"
        assert error.response is not None
        assert error.response.retry_after == "2"
        assert not any(
            "token" in k or "api-key" in k or "cookie" in k for k in error.response.headers
        )


def test_unset_null_and_typed_models():
    contract = Contract()
    assert contract.encode(
        {"value": None},
        {"type": "object", "properties": {"value": {"type": "string", "nullable": True}}},
        "request",
    ) == {"value": None}
    assert (
        contract.encode(
            {},
            {"type": "object", "properties": {"value": {"type": "string", "nullable": True}}},
            "request",
        )
        == {}
    )
    assert models.FoodLog(id="log", foods=[], timestamp_utc="opaque").model_dump(
        exclude_unset=True
    ) == {"id": "log", "foods": [], "timestamp_utc": "opaque"}
    fixture = next(f for f in FIXTURES["operations"] if f["operationId"] == "createFoodLog")
    with local_service() as service:
        service["response"] = fixture["response"]
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            client.for_user("user").food_logs.create(
                foods=[
                    models.FoodLogInputFood(
                        id=12, serving=models.FoodLogInputServing(id=5, quantity=1)
                    )
                ],
                timestamp_utc=datetime(2026, 8, 30, tzinfo=UTC),
            )
            assert service["requests"][-1]["body"] == {
                "foods": [{"id": 12, "serving": {"id": 5, "quantity": 1}}],
                "timestamp_utc": "2026-08-30T00:00:00Z",
            }
            with pytest.raises(JanuaryValidationError):
                client.for_user("user").food_logs.update(log_id="log", name=None)  # pyright: ignore[reportArgumentType] -- intentionally invalid null
            assert len(service["requests"]) == 1


@pytest.mark.parametrize("key", ["", "ct-client-token", "wrong-key", "sk-key\n"])
def test_reject_invalid_secret(key):
    with pytest.raises(JanuaryConfigurationError):
        January(max_retries=0, secret_key=key)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_reject_unbounded_timeout(timeout):
    with pytest.raises(JanuaryConfigurationError):
        January(max_retries=0, secret_key="sk-local-fixture", timeout=timeout)


def test_timeout_cancellation_and_no_retry():
    with local_service() as service:
        service["delay"] = 0.1
        service["response"] = next(
            f for f in FIXTURES["operations"] if f["operationId"] == "credits"
        )["response"]
        with January(
            max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
        ) as client:
            with pytest.raises(JanuaryTimeoutError):
                client.credits(timeout=0.01)
            event = Event()
            event.set()
            with pytest.raises(JanuaryCancelledError):
                client.credits(cancel_event=event)

        async def run():
            async with AsyncJanuary(
                max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]
            ) as client:
                with pytest.raises(JanuaryTimeoutError):
                    await client.credits(timeout=0.01)
                task = asyncio.create_task(client.credits())
                await asyncio.sleep(0.02)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        assert len(service["requests"]) == 3


def test_redirects_not_followed_and_injected_transport_not_closed():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://example.com/leak"})

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as transport:
        with (
            January(max_retries=0, secret_key="sk-local-fixture", http_client=transport) as client,
            pytest.raises(JanuaryError, match="redirect"),
        ):
            client.credits()
        assert not transport.is_closed
    assert len(requests) == 1


def test_malformed_response_and_validation_before_network():
    with (
        local_service() as service,
        January(max_retries=0, secret_key="sk-local-fixture", base_url=service["url"]) as client,
    ):
        with pytest.raises(JanuaryResponseError):
            client.credits()
        with pytest.raises(JanuaryValidationError):
            client.mint_client_token(end_user_id="user", ttl_seconds=299)
        with pytest.raises(JanuaryValidationError):
            client.foods.lookup_barcode(upc="nope")
        with pytest.raises(JanuaryValidationError):
            client.food_logs.list(start="2026-08-01", end="2026-08-30")
        assert len(service["requests"]) == 1


def test_manifest_matches_public_methods():
    manifest = json.loads(Path(__file__).parents[1].joinpath("sdk-surface.json").read_text())
    assert manifest["language"] == "python"
    assert len(manifest["operations"]) == 18
    for client_type in [January, AsyncJanuary]:
        client = client_type(secret_key="sk-local-fixture")
        for op in manifest["operations"]:
            resource = getattr(client, op["resource"]) if op["resource"] else client
            fn = getattr(resource, op["method"])
            assert callable(fn)
            assert inspect.iscoroutinefunction(fn) == (client_type is AsyncJanuary)
        if client_type is AsyncJanuary:
            asyncio.run(client.close())
        else:
            client.close()
