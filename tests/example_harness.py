"""Test-only HTTP routing for unchanged production examples; no URL environment setting."""

import runpy
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx


@contextmanager
def loopback_http(origin):
    target = httpx.URL(origin)
    if (
        target.scheme != "http"
        or target.host != "127.0.0.1"
        or target.port is None
        or target.userinfo
        or target.path != "/"
        or target.query
        or target.fragment
    ):
        raise ValueError("The offline harness requires a loopback HTTP service")

    def route(request):
        # Reject unexpected origins/credentials before any network I/O.
        assert request.url.scheme == "https" and request.url.host == "partners.january.ai"
        authorization = request.headers.get("authorization", "")
        assert authorization in {
            "Bearer sk-quickstart-offline-only",
            "Bearer sk-offline-server",
        } or authorization.startswith("Bearer ct-offline-")
        request.url = request.url.copy_with(
            scheme=target.scheme, host=target.host, port=target.port
        )
        request.headers["host"] = target.netloc.decode("ascii")

    class SyncTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            route(request)
            return super().handle_request(request)

    class AsyncTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            route(request)
            return await super().handle_async_request(request)

    sync_constructor, async_constructor = httpx.Client, httpx.AsyncClient

    def sync_client(*args, **kwargs):
        return sync_constructor(
            *args,
            **{
                **kwargs,
                "trust_env": False,
                "transport": SyncTransport(trust_env=False),
            },
        )

    def async_client(*args, **kwargs):
        return async_constructor(
            *args,
            **{
                **kwargs,
                "trust_env": False,
                "transport": AsyncTransport(trust_env=False),
            },
        )

    with (
        patch.object(httpx, "Client", sync_client),
        patch.object(httpx, "AsyncClient", async_client),
    ):
        yield


if __name__ == "__main__":
    # Arguments belong only to this test harness, never the public quickstarts.
    script, origin = sys.argv[1:]
    source = Path(__file__).resolve().parents[1] / "examples/quickstart" / script
    if script not in {"main.py", "async_main.py", "minimal.py"}:
        raise ValueError("Unknown quickstart")
    sys.argv = [str(source)]
    with loopback_http(origin):
        runpy.run_path(str(source), run_name="__main__")
