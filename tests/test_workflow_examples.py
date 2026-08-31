"""Execute published workflow fragments offline, including real image preparation."""

import asyncio
import importlib.util
import json
import re
from copy import deepcopy
from pathlib import Path

import httpx
from example_harness import loopback_http
from installed_consumer import FIXTURES, local_service
from PIL import Image

from januaryai import AsyncJanuary

ROOT = Path(__file__).resolve().parents[1]
BY_ID = {item["operationId"]: item for item in FIXTURES["operations"]}


def test_minimal_readme_is_the_runnable_example():
    source = (ROOT / "examples/quickstart/minimal.py").read_text(encoding="utf-8").strip()
    assert source in re.findall(
        r"```python\n(.*?)\n```", (ROOT / "README.md").read_text(encoding="utf-8"), re.S
    )


def test_workflow_recipes_over_loopback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JANUARY_API_KEY", "sk-quickstart-offline-only")
    Image.new("RGB", (1200, 800), "green").save(tmp_path / "lunch.jpg")
    blocks = re.findall(
        r"```python\n(.*?)\n```", (ROOT / "docs/recipes.md").read_text(encoding="utf-8"), re.S
    )
    for block, operation_ids in zip(
        blocks[:2],
        [
            ["scanFoodPhoto", "correctPhotoScan", "createFoodLog"],
            ["searchFoods", "getFood", "createFoodLog"],
        ],
        strict=True,
    ):
        with local_service() as service, loopback_http(service["url"]):
            service["responses"] = [deepcopy(BY_ID[op]["response"]) for op in operation_ids]
            exec(compile(block, "docs/recipes.md", "exec"), {})
            assert len(service["requests"]) == 3


def test_concurrent_photo_example_bounds_work_and_isolates_failures(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "concurrent_example", ROOT / "examples/analysis/concurrent.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = [tmp_path / f"photo-{i}.png" for i in range(9)]
    for path in paths[:-1]:
        Image.new("RGB", (1200, 800), "green").save(path)
    active, peak, requests = 0, 0, []

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        requests.append(request)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=BY_ID["scanFoodPhoto"]["response"]["body"])

    async def run():
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport,
            AsyncJanuary(api_key="sk-offline", http_client=transport) as client,
        ):
            return await module.analyze_photos(client, paths)

    results = asyncio.run(run())
    assert len(results) == 9 and isinstance(results[-1], Exception)
    assert all(not isinstance(result, Exception) for result in results[:-1])
    assert 1 < peak <= 4 and len(requests) == 8
    assert all(
        json.loads(request.content)["image"].startswith("data:image/jpeg;") for request in requests
    )
