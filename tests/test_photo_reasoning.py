"""The optional analysis `reasoning` input reaches the wire in both client flavours."""

import asyncio
from collections.abc import Callable
import json
from copy import deepcopy

import httpx
from installed_consumer import FIXTURES

from januaryai import AsyncJanuary, January, models

KEY = "sk-reasoning-synthetic-0011223344556677"
SCAN = next(f for f in FIXTURES["operations"] if f["operationId"] == "scanFoodPhoto")
IMAGE = "https://example.invalid/photo.jpg"


def _handler(captured: list[dict]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=deepcopy(SCAN["response"]["body"]))

    return handler


def test_sync_photo_analysis_serializes_reasoning_model_and_typed_dict() -> None:
    captured: list[dict] = []
    with (
        httpx.Client(transport=httpx.MockTransport(_handler(captured))) as http,
        January(api_key=KEY, http_client=http, max_retries=0) as client,
    ):
        user = client.for_user("reasoning-user")
        user.food_analysis.analyze_photo(image=IMAGE)
        user.food_analysis.analyze_photo(image=IMAGE, reasoning={"effort": "xhigh"})
        scan = user.food_analysis.analyze_photo(
            image=IMAGE, reasoning=models.AnalysisReasoning(effort="none")
        )

    assert isinstance(scan, models.FoodScan)
    assert "reasoning" not in captured[0]
    assert captured[1]["reasoning"] == {"effort": "xhigh"}
    assert captured[2]["reasoning"] == {"effort": "none"}
    assert all(body["image"] == IMAGE for body in captured)


def test_async_photo_analysis_serializes_reasoning() -> None:
    captured: list[dict] = []

    async def run() -> None:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(_handler(captured))) as http,
            AsyncJanuary(api_key=KEY, http_client=http, max_retries=0) as client,
        ):
            await client.for_user("reasoning-user").food_analysis.analyze_photo(
                image=IMAGE, reasoning={"effort": "xhigh"}
            )

    asyncio.run(run())
    assert captured == [{"image": IMAGE, "reasoning": {"effort": "xhigh"}}]
