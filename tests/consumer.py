"""Run with python -I to prove imports come from the installed distribution."""
import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from januaryai import AsyncJanuary, January, JanuaryAPIError, JanuaryValidationError


class ConsumerTest(unittest.TestCase):
    def test_http_contract(self) -> None:
        requests: list[dict[str, object]] = []
        valid = {"token": "ct-fixture", "expires_in": 300, "expires_at": "2026-08-30T18:30:00Z", "end_user_id": "user", "scopes": ["foods:read"]}
        payload: object = {**valid, "future_field": {"enabled": True}}
        status = 201

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            client = January(secret_key="sk-local-fixture", base_url=origin)
            token = client.client_tokens.create(end_user_id="user", scopes=["foods:read"], ttl_seconds=600)
            self.assertEqual(token.to_dict(), {"token": "ct-fixture", "expiresIn": 300})
            self.assertEqual(requests[0], {"end_user_id": "user", "scopes": ["foods:read"], "ttl_seconds": 600})
            async def run() -> None:
                async with AsyncJanuary(secret_key="sk-local-fixture", base_url=origin) as async_client:
                    token = await async_client.client_tokens.create(end_user_id="user")
                    self.assertEqual(token.token, "ct-fixture")
            asyncio.run(run())
            self.assertEqual(requests[1], {"end_user_id": "user"})
            for kwargs in [{"scopes": []}, {"scopes": ["unknown"]}, {"scopes": ["foods:read"] * 7}, {"ttl_seconds": 0}, {"ttl_seconds": True}, {"ttl_seconds": 300.5}, {"end_user_id": "😀" * 33}]:
                with self.assertRaises(JanuaryValidationError):
                    client.client_tokens.create(**{"end_user_id": "user", **kwargs})
            self.assertEqual(len(requests), 2)
            for invalid in [{**valid, "expires_in": "300"}, {**valid, "expires_in": True}, {**valid, "expires_in": None}, {"expires_in": 300}, {"access_token": "old", "expires_in": 300}]:
                payload = invalid
                with self.assertRaises(JanuaryAPIError) as error:
                    client.client_tokens.create(end_user_id="user")
                self.assertEqual(error.exception.status_code, 201)
            status = 429
            payload = {"message": "Try later", "code": "rate_limited"}
            count = len(requests)
            with self.assertRaises(JanuaryAPIError) as error:
                client.client_tokens.create(end_user_id="user")
            self.assertEqual(error.exception.code, "rate_limited")
            self.assertEqual(len(requests), count + 1)
            client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
