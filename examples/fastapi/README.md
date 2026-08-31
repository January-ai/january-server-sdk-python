# FastAPI token server example

This runnable partner backend exposes `POST /api/january/token` and uses the
local `januaryai-server` package. A production service must derive the end-user
ID from its verified session or JWT, not from the demo header.

## Run locally

Use Python 3.11+ and `uv`. Starting at the SDK root, enter the example directory
and reuse the root's blank `.env.example` template without overwriting an existing
local `.env`:

```sh
cd examples/fastapi
test -e .env || cp ../../.env.example .env
# Edit this directory's .env and set JANUARY_API_KEY to your full sk- server key.
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 4020
```

Run the server from `examples/fastapi`: the example-only `python-dotenv`
dependency loads exactly the current working directory's `.env`, without
searching parent directories. Existing shell variables take precedence. The SDK
itself never loads environment files. Never commit or share `.env` or the key.

Client tokens are an optional feature, but **real mint requests require** opening
[Client tokens](https://dashboard.january.ai/dashboard/client-tokens) and choosing
**Enable client tokens** for your partner account. This is not required for the
server food-search quickstart. The SDK uses its built-in production endpoint;
this local port serves a partner backend, not a January API mock. Calling the
route with a real key creates a real client token. API use can consume credits;
check [Billing](https://dashboard.january.ai/billing) and the root `credits()`
operation for your allowance and balance.

In another terminal, make this request only when you intend to mint a token:

```sh
curl --silent --show-error --fail-with-body \
  --request POST http://127.0.0.1:4020/api/january/token \
  --header 'x-demo-user-id: demo-user'
```

Expected HTTP 200 shape (the token below is a nonfunctional placeholder; the
actual response contains a credential, so do not log or share it):

```json
{"token":"ct-example-placeholder","expiresIn":1800}
```

The relay calls the canonical async root `mint_client_token()` operation with
the authenticated user's ID, server-selected `scopes=["foods:read"]`, and
`ttl_seconds=1800`. Request bodies cannot override the user, scopes, or lifetime.
For client SDK compatibility, the relay maps the canonical result's `token` to
`token` and `expires_in` (seconds) to `expiresIn`; it does not return the other
mint-result fields. The SDK client is closed when the app shuts down.

Without `x-demo-user-id`, the route returns HTTP 401 `{"detail":"unauthorized"}`
without contacting January. Mint failures return HTTP 502
`{"detail":"Unable to mint client token"}`, never upstream details.

**Local demo only:** `x-demo-user-id` is caller-supplied, not authentication.
Keep the demo bound to `127.0.0.1`. Before deployment, replace the header dependency
with a verified session/JWT and derive the user ID on the server. Return a token
only to that authenticated user; never log credentials or token responses.
