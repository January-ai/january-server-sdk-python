"""`foods.search` forwards the new `offset` paging parameter in both client flavours."""

import asyncio
from collections.abc import Callable
from copy import deepcopy

import httpx
from installed_consumer import FIXTURES

from januaryai import AsyncJanuary, January

KEY = "sk-offset-synthetic-0011223344556677"
SEARCH = next(f for f in FIXTURES["operations"] if f["operationId"] == "searchFoods")


def _handler(captured: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=deepcopy(SEARCH["response"]["body"]))

    return handler


def test_sync_search_serializes_offset_and_omits_it_by_default() -> None:
    captured: list[httpx.Request] = []
    with (
        httpx.Client(transport=httpx.MockTransport(_handler(captured))) as http,
        January(api_key=KEY, http_client=http, max_retries=0) as client,
    ):
        client.foods.search(query="banana", limit=10)
        client.foods.search(query="banana", limit=50, offset=100)

    first, second = (dict(r.url.params) for r in captured)
    assert "offset" not in first and first["limit"] == "10"
    assert second["query"] == "banana" and second["limit"] == "50" and second["offset"] == "100"


def test_async_search_serializes_offset() -> None:
    captured: list[httpx.Request] = []

    async def run() -> None:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(_handler(captured))) as http,
            AsyncJanuary(api_key=KEY, http_client=http, max_retries=0) as client,
        ):
            await client.foods.search(query="banana", offset=20)

    asyncio.run(run())
    assert dict(captured[0].url.params)["offset"] == "20"
