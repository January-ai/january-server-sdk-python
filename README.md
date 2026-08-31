# January Server SDK for Python

Typed sync and async access to January's food, restaurant, food-analysis,
food-log, and glucose APIs, plus server-only token and credit operations.
Requires **Python 3.11+**.

Use it only on trusted servers. Secret `sk-` keys must never reach browsers or
mobile apps. The quickstarts below load your key from a local .env file.

## Before you begin

1. [Sign up](https://dashboard.january.ai/sign-up) or
   [sign in](https://dashboard.january.ai/sign-in) to the developer platform.
2. If you have no active organization, complete the required **Set up your
   organization** prompt before continuing.
3. Open the [dashboard](https://dashboard.january.ai/dashboard), then **API keys**
   → **Create key** → enter a **Key name** → **Create key**.
4. Copy the full `sk-` key when it is shown. It is revealed only once; store it
   in a secrets manager. For local development, put it in the ignored `.env` file
   described below, never in browser/mobile code or source control.

Dashboard login authenticates you as a human. Backend SDK requests instead use
the server API key (`sk-`). A client token (`ct-`) is a short-lived end-user
credential for mobile/web clients and is rejected by this server SDK. Client
tokens are optional and are **not needed for server-side food search**; see
[server-only APIs](#server-only-apis) if you need to issue them.

Check [Billing](https://dashboard.january.ai/billing) for your current plan and
credit allowance. The first food-search call is billable; the SDK does not retry
automatically. Use the independent root `credits()` call below when you need to
check the balance.

## Install

In your Python project directory, create a virtual environment and install from PyPI:

```sh
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install januaryai-server python-dotenv
```

Ensure the version is 3.11 or newer. On a host with multiple interpreters, use
`python3.11 -m venv .venv` if `python3` is older. On Windows, use
`py -3.11 -m venv .venv` and activate with `.venv\Scripts\Activate.ps1` in
PowerShell instead of `source`.

This installs the `januaryai-server` distribution and its runtime
dependencies; Python imports use `januaryai`. `python-dotenv` is an example-only
dependency for local configuration, not an SDK runtime dependency. No test
dependencies are needed.

## Quickstart

Save the complete example below as `quickstart.py` in your project directory.
Create a `.env` file in that same directory, replacing the placeholder with your
full `sk-` server key:

```dotenv
JANUARY_API_KEY=your-server-api-key
```

Add `.env` to your project's `.gitignore` before storing the key:

```gitignore
.env
```

If working in this SDK repository instead, copy its `.env.example` only when
`.env` does not already exist, then fill in `JANUARY_API_KEY` in `.env`:

```sh
test -e .env || cp .env.example .env
```

The repository already ignores `.env`. Never commit or share it; it is a plain
text local secret, not encrypted storage. For deployed applications, keep using
your platform's secret manager or environment variables.

With the virtual environment active, run from the directory containing `.env`:

```sh
python quickstart.py
```

This makes exactly one billable banana-search request; it creates no logs or tokens
and makes no credit-balance calls. It uses the synthetic ID `january-quickstart`
in UTC. In production, derive the ID from your authenticated user.

Complete runnable source: [examples/quickstart/main.py](examples/quickstart/main.py).

<!-- quickstart:main.py -->
```python
import os
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from januaryai import (
    January, JanuaryAPIError, JanuaryConfigurationError,
    JanuaryConnectionError, JanuaryTimeoutError,
)


def main() -> int:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    key = os.environ.get("JANUARY_API_KEY", "").strip()
    if not key:
        print("Set JANUARY_API_KEY in .env or your environment before running this example.", file=sys.stderr)
        return 2
    try:
        with January(secret_key=key) as client:
            # In production, derive this ID from your authenticated user.
            user = client.for_user("january-quickstart", end_user_timezone="UTC")
            foods = user.foods.search(query="banana")
    except JanuaryAPIError as error:
        # Only SDK-sanitized metadata; never print the error, message, body, or headers.
        diagnostic = {"status": error.status_code, "code": error.code, "request_id": error.request_id}
        print(f"Food search failed: {json.dumps(diagnostic)}", file=sys.stderr)
        hint = {
            401: "Check that JANUARY_API_KEY is the full active server key for your organization.",
            403: "Check organization access and key permissions; see README troubleshooting.",
            429: "Check the error code and README troubleshooting before another request.",
        }.get(error.status_code, "Contact support@january.ai with these safe diagnostic fields.")
        if error.status_code == 429 and error.code == "rate_limited":
            hint = "Wait before trying again; this example does not retry automatically."
        elif error.status_code == 429 and error.code == "credit_limit_exceeded":
            hint = "Check your current plan and credit allowance at https://dashboard.january.ai/billing."
        print(f"Hint: {hint}", file=sys.stderr)
        return 1
    except JanuaryConfigurationError:
        print("Configuration error. Set a full sk- server key.", file=sys.stderr)
        return 2
    except JanuaryConnectionError as error:
        code = "transport_timeout" if isinstance(error, JanuaryTimeoutError) else "transport_connection"
        print(f"Food search failed: {code}. Check connectivity; no automatic retry.", file=sys.stderr)
        return 1
    except Exception:
        print("Food search failed. Contact support@january.ai; do not share credentials.", file=sys.stderr)
        return 1

    print(f"Foods returned: {len(foods.items)}")
    if foods.items:
        name = foods.items[0].name.replace(key, "[redacted]")
        print(f"First food: {name}")
    else:
        print("No foods found for banana.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

For async applications, save the following example as `quickstart_async.py` and
run `python quickstart_async.py` from the same directory using the same `.env`. Complete source:
[examples/quickstart/async_main.py](examples/quickstart/async_main.py).

<!-- quickstart:async_main.py -->
```python
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from januaryai import (
    AsyncJanuary, JanuaryAPIError, JanuaryConfigurationError,
    JanuaryConnectionError, JanuaryTimeoutError,
)


async def main() -> int:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    key = os.environ.get("JANUARY_API_KEY", "").strip()
    if not key:
        print("Set JANUARY_API_KEY in .env or your environment before running this example.", file=sys.stderr)
        return 2
    try:
        async with AsyncJanuary(secret_key=key) as client:
            # In production, derive this ID from your authenticated user.
            user = client.for_user("january-quickstart", end_user_timezone="UTC")
            foods = await user.foods.search(query="banana")
    except JanuaryAPIError as error:
        # Only SDK-sanitized metadata; never print the error, message, body, or headers.
        diagnostic = {"status": error.status_code, "code": error.code, "request_id": error.request_id}
        print(f"Food search failed: {json.dumps(diagnostic)}", file=sys.stderr)
        hint = {
            401: "Check that JANUARY_API_KEY is the full active server key for your organization.",
            403: "Check organization access and key permissions; see README troubleshooting.",
            429: "Check the error code and README troubleshooting before another request.",
        }.get(error.status_code, "Contact support@january.ai with these safe diagnostic fields.")
        if error.status_code == 429 and error.code == "rate_limited":
            hint = "Wait before trying again; this example does not retry automatically."
        elif error.status_code == 429 and error.code == "credit_limit_exceeded":
            hint = "Check your current plan and credit allowance at https://dashboard.january.ai/billing."
        print(f"Hint: {hint}", file=sys.stderr)
        return 1
    except JanuaryConfigurationError:
        print("Configuration error. Set a full sk- server key.", file=sys.stderr)
        return 2
    except JanuaryConnectionError as error:
        code = "transport_timeout" if isinstance(error, JanuaryTimeoutError) else "transport_connection"
        print(f"Food search failed: {code}. Check connectivity; no automatic retry.", file=sys.stderr)
        return 1
    except Exception:
        print("Food search failed. Contact support@january.ai; do not share credentials.", file=sys.stderr)
        return 1

    print(f"Foods returned: {len(foods.items)}")
    if foods.items:
        name = foods.items[0].name.replace(key, "[redacted]")
        print(f"First food: {name}")
    else:
        print("No foods found for banana.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

On success, either example prints `Foods returned: N` and `First food: <name>`.
An empty search prints `Foods returned: 0` and `No foods found for banana.`;
this is also a successful exit.

A missing key exits before any request. API failures exit nonzero with sanitized
status/code/request-ID fields and a fixed actionable hint, never the error,
message, response body, headers, or token. Both quickstarts use the SDK's built-in
production endpoint; no URL configuration is needed.
Each quickstart loads only `.env` in the current working directory, without
searching parent or source directories. Existing environment variables take
precedence, including an explicitly empty key. The SDK still receives the key
explicitly and never reads files. The separate [live runner](docs/live-testing.md)
has its own environment-file rules.

## Common tasks

All shared operations use the same resource tree on both clients:

| Resource | Methods |
| --- | --- |
| `foods` | `search`, `autocomplete`, `get`, `lookup_barcode`, `suggest_alternatives` |
| `restaurants` | `search`, `search_menu_items` |
| `food_analysis` | `analyze_photo`, `analyze_description`, `correct` |
| `food_logs` | `create`, `list`, `update`, `delete` |
| `glucose` | `predict` |

`for_user` creates an immutable, shared-only view. Its bound user/timezone override
per-call values without changing the root client. Parameters follow the existing
client SDK: barcode `upc`, description `query`, photo URL/data URI `image`,
food `food_id`, and log `log_id`. See [typed signatures](src/januaryai/_generated.py)
and [models](src/januaryai/models.py).

`FoodPortion.from_food` is local and synchronous even when food came from
`AsyncJanuary`. Python uses `from_food` because `from` is a keyword.
The following is a **fragment**, assuming `food` is an existing `FoodSearchItem`:

```python
from januaryai import FoodPortion

portion = FoodPortion.from_food(food, quantity=2)
nutrition = portion.nutrition.model_dump(by_alias=True, exclude_unset=True)
selection = portion.selection  # Pass in foods=[selection] to log/glucose requests.
```

It chooses the first primary serving, else the first; `serving_id=` selects an
exact match. Quantity defaults to the serving quantity and must be finite,
positive, and ≤10,000. All 16 nutrients and glycemic load scale by
`quantity * scaling_factor / serving.quantity`; weight scales by
`quantity / serving.quantity`, while GI stays unchanged. Units, zeros, missing
values, and source models are preserved. `FoodPortionError(ValueError).code` is
`no_servings`, `serving_not_found`, `invalid_serving`, or `invalid_quantity`.

Nutrient entries can be absent or the map empty; present entries require both
`value` and `unit`. Use `model_fields_set` and `exclude_unset=True` to preserve
omissions. Parsed detections sent to `food_analysis.correct` retain them.

## Server-only APIs

These root methods retain the OpenAPI names in Python snake_case and are not
available on a user-bound view. The following are **independent fragments, not a
sequence to run together**. Each assumes `client` is a configured `January` and
`authenticated_user_id` comes from your backend. The client-token feature must
be enabled for your partner account before minting client tokens: open
[Client tokens](https://dashboard.january.ai/dashboard/client-tokens) and choose
**Enable client tokens**, then mint on your backend. This optional setup is not
required for the server food-search quickstart.

Check the credit balance independently:

```python
balance = client.credits()
```

Mint a token when issuing credentials to your authenticated client:

```python
token = client.mint_client_token(
    end_user_id=authenticated_user_id, scopes=["foods:read"], ttl_seconds=1800,
)
```

Return it only to that authenticated user; never log it. Do not immediately
revoke a newly minted token. Revoke separately when intentionally invalidating
that user's client tokens:

```python
revoked = client.revoke_client_tokens(end_user_id=authenticated_user_id)
```

Revocation is one DELETE with `end_user_id` in the query, returning
`revoked.revoked_count` from `X-Revoked-Count`. There is no revoke-all loop.

## Configuration and errors

- Pass `secret_key=` explicitly. Client-token `ct-` credentials are rejected.
- The SDK does not read credentials or files automatically. Loading local `.env`
  is application behavior in the quickstarts, using the example-only `python-dotenv` dependency.
- Default timeout is 30 seconds; client/per-call `timeout=` overrides it.
  Async calls support task cancellation; sync calls accept `cancel_event=`
  and observe it at the next read/timeout.
- No automatic retries, pagination, or invented idempotency headers.
- Requests use the built-in production endpoint over HTTPS; redirects are not followed.
- Responses expose `.response` metadata; revocation returns it directly.
  `JanuaryAPIError` preserves `code`, `status_code`, `docs_url`, and
  `request_id`. Connection, timeout, cancellation, and decoding errors have
  separate types. Never print raw error bodies or token models.
- Omitted values remain unset; explicit `None` is sent only where nullable.
  Aware datetime inputs serialize to UTC; response timestamps remain strings.

### Troubleshooting

| Symptom | Next step |
| --- | --- |
| Missing/invalid key before any HTTP request | Set the full `sk-` key in `JANUARY_API_KEY`; a dashboard login or `ct-` token is not a server key. |
| HTTP 401 | Check that the key is complete, active, and belongs to the intended organization. |
| HTTP 403 | Check organization access and key permissions. For token minting only, also check that **Enable client tokens** is enabled; server food search does not require it. |
| HTTP 429, `rate_limited` | Wait before another attempt. There are no automatic retries. |
| HTTP 429, `credit_limit_exceeded` | Check [Billing](https://dashboard.january.ai/billing) for the current plan/allowance and use root `credits()` for balance. Do not treat this as a rate-limit retry. |
| `transport_timeout` / `transport_connection` | Check connectivity. The default request timeout is 30 seconds; an interrupted request may still have reached the service. |

For example, an API failure can produce these safe diagnostics (illustrative,
not an additional request):

```text
Food search failed: {"status": 429, "code": "rate_limited", "request_id": "req-example"}
Hint: Wait before trying again; this example does not retry automatically.
```

The examples use only the SDK's credential-redacted metadata and JSON-escape
the fields. Share those fields with [support@january.ai](mailto:support@january.ai)
if needed, not raw errors, payloads, headers, keys, or client tokens.

## Examples and testing

To run the repository examples, follow the [contributor setup](CONTRIBUTING.md#setup-and-offline-verification),
then run these commands from the repository root with its virtual environment active:

- [Food search](examples/quickstart/main.py): `python examples/quickstart/main.py`.
- [Async food search](examples/quickstart/async_main.py): `python examples/quickstart/async_main.py`.
- [Offline FoodPortion](examples/portions/main.py): `python examples/portions/main.py`
  (fake food, no key or network).
- [Live all-operation demo](docs/live-testing.md): separate opt-in setup, environment
  file rules, credit usage, result reports, and cleanup details.

Normal CI tests both quickstarts as real subprocesses against localhost with
fake credentials and test-owned transport injection. It never runs these examples against production.
See [CONTRIBUTING.md](CONTRIBUTING.md) for test setup and focused commands.

## Distribution

`januaryai-server` is distributed through PyPI as a wheel (`.whl`) and source
distribution (`.tar.gz`). Install it with `pip`; import it as `januaryai`.
Type information is included for editor completion and static checking.
Record the package version in your project's dependency file or lockfile for
reproducible environments. See [distribution checks](CONTRIBUTING.md#build-and-verify-distributions)
for contributor build and installation tests.

## Reference, support, and contributing

- [January Swagger API reference](https://partners.january.ai/v1.2/docs#/).
- [Generated Python models](src/januaryai/models.py).
- [Contributor setup, contract generation, and compatibility notes](CONTRIBUTING.md).

For support, email [support@january.ai](mailto:support@january.ai) with a minimal reproduction
plus safe request IDs—never credentials or private payloads.
