"""Standalone installed-wheel smoke flow, including an actual loopback HTTP service."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from januaryai import AsyncJanuary, FoodPortion, FoodPortionError, January, ResponseMetadata, models

FIXTURES = json.loads(Path(__file__).with_name("fixtures").joinpath("contract.json").read_text())


def snake(value):
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).replace("-", "_").lower()


def arguments(fixture):
    result = {}
    for kind, params in fixture["request"].get("parameters", {}).items():
        for name, value in params.items():
            result[snake(fixture.get("parameterNames", {}).get(name, name))] = value
    for name, value in fixture["request"].get("body", {}).items():
        result[snake(fixture.get("bodyPropertyNames", {}).get(name, name))] = value
    return result


def method(client, fixture):
    resource = getattr(client, snake(fixture["resource"])) if fixture["resource"] else client
    return getattr(resource, snake(fixture["publicMethod"]))


def assert_request(actual, fixture):
    expected = fixture["request"]
    params = expected.get("parameters", {})
    path = fixture["path"]
    for name, value in params.get("path", {}).items():
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    assert actual["method"] == fixture["method"]
    assert urlsplit(actual["path"]).path == path
    assert parse_qs(urlsplit(actual["path"]).query) == {k:[str(v)] for k,v in params.get("query", {}).items()}
    for name, value in params.get("header", {}).items():
        assert actual["headers"][name.lower()] == value
    assert actual["headers"]["authorization"] == "Bearer sk-local-fixture"
    assert actual["body"] == expected.get("body")


@contextmanager
def local_service():
    state = {"response": {"status": 200, "headers": {}, "body": {}}, "requests": [], "delay": 0}
    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            state["requests"].append({"method":self.command,"path":self.path,
                                       "headers":{k.lower():v for k,v in self.headers.items()},
                                       "body":json.loads(body) if body else None})
            time.sleep(state["delay"])
            response = state["response"]
            data = json.dumps(response["body"]).encode() if response["body"] is not None else b""
            self.send_response(response["status"])
            for key, value in response["headers"].items(): self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    try: yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    import januaryai
    assert "/src/januaryai/" not in januaryai.__file__, "Consumer must use installed wheel, not source"
    food = models.FoodSearchItem(
        id=42, name="Offline portion food",
        nutrients=models.NutritionFacts(calories=models.NutrientAmount(value=100, unit="cal"),
                                       protein=models.NutrientAmount(value=0, unit="g")),
        servings=[models.ServingOption(id=2, quantity=2, unit="pieces", scaling_factor=3,
                                       weight_grams=120, is_primary=True)],
        glycemic_index=50, glycemic_load=8,
    )
    portion = FoodPortion.from_food(food, quantity=4)
    assert portion.nutrition.model_dump(by_alias=True, exclude_unset=True) == {
        "calories":{"value":600,"unit":"cal"}, "protein":{"value":0,"unit":"g"},
    }
    assert portion.total_weight_grams == 240
    assert portion.glycemic_index == 50 and portion.glycemic_load == 48
    assert portion.selection.model_dump() == {"id":42,"serving":{"id":2,"quantity":4}}
    assert models.CreateFoodLogBody(foods=[portion.selection]).foods[0] == portion.selection
    try:
        FoodPortion.from_food(food, quantity=0)
    except FoodPortionError as error:
        assert isinstance(error, ValueError) and error.code == "invalid_quantity"
    else:
        raise AssertionError("FoodPortion accepted zero quantity")
    with local_service() as service:
        with January(secret_key="sk-local-fixture", base_url=service["url"]) as client:
            for fixture in FIXTURES["operations"]:
                service["response"] = fixture["response"]
                result = method(client, fixture)(**arguments(fixture))
                assert_request(service["requests"][-1], fixture)
                assert isinstance(result, ResponseMetadata) or result.response is not None
        async def run():
            async with AsyncJanuary(secret_key="sk-local-fixture", base_url=service["url"]) as client:
                for fixture in FIXTURES["operations"]:
                    service["response"] = fixture["response"]
                    await method(client, fixture)(**arguments(fixture))
                    assert_request(service["requests"][-1], fixture)
        asyncio.run(run())
        assert len(service["requests"]) == 36
    print("Installed package: FoodPortion + all 18 sync + 18 async operations passed over loopback HTTP")


if __name__ == "__main__": main()
