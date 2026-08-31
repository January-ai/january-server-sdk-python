# Quickstart with diagnostics

These runnable examples use exactly one request (`max_retries=0`).
For the shorter introduction, see [README](../README.md#quickstart).

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

Complete runnable source: [examples/quickstart/main.py](../examples/quickstart/main.py).

<!-- quickstart:main.py -->
```python
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from januaryai import (
    January,
    JanuaryAPIError,
    JanuaryConfigurationError,
    JanuaryConnectionError,
    JanuaryTimeoutError,
)


def main() -> int:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    key = os.environ.get("JANUARY_API_KEY", "").strip()
    if not key:
        print(
            "Set JANUARY_API_KEY in .env or your environment before running this example.",
            file=sys.stderr,
        )
        return 2
    try:
        with January(secret_key=key, max_retries=0) as client:
            # In production, derive this ID from your authenticated user.
            user = client.for_user("january-quickstart", end_user_timezone="UTC")
            foods = user.foods.search(query="banana")
    except JanuaryAPIError as error:
        # Only SDK-sanitized metadata; never print the error, message, body, or headers.
        diagnostic = {
            "status": error.status_code,
            "code": error.code,
            "request_id": error.request_id,
        }
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
        code = (
            "transport_timeout"
            if isinstance(error, JanuaryTimeoutError)
            else "transport_connection"
        )
        print(
            f"Food search failed: {code}. Check connectivity; no automatic retry.", file=sys.stderr
        )
        return 1
    except Exception:
        print(
            "Food search failed. Contact support@january.ai; do not share credentials.",
            file=sys.stderr,
        )
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
[examples/quickstart/async_main.py](../examples/quickstart/async_main.py).

<!-- quickstart:async_main.py -->
```python
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from januaryai import (
    AsyncJanuary,
    JanuaryAPIError,
    JanuaryConfigurationError,
    JanuaryConnectionError,
    JanuaryTimeoutError,
)


async def main() -> int:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    key = os.environ.get("JANUARY_API_KEY", "").strip()
    if not key:
        print(
            "Set JANUARY_API_KEY in .env or your environment before running this example.",
            file=sys.stderr,
        )
        return 2
    try:
        async with AsyncJanuary(secret_key=key, max_retries=0) as client:
            # In production, derive this ID from your authenticated user.
            user = client.for_user("january-quickstart", end_user_timezone="UTC")
            foods = await user.foods.search(query="banana")
    except JanuaryAPIError as error:
        # Only SDK-sanitized metadata; never print the error, message, body, or headers.
        diagnostic = {
            "status": error.status_code,
            "code": error.code,
            "request_id": error.request_id,
        }
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
        code = (
            "transport_timeout"
            if isinstance(error, JanuaryTimeoutError)
            else "transport_connection"
        )
        print(
            f"Food search failed: {code}. Check connectivity; no automatic retry.", file=sys.stderr
        )
        return 1
    except Exception:
        print(
            "Food search failed. Contact support@january.ai; do not share credentials.",
            file=sys.stderr,
        )
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
explicitly and never reads files. The separate [live runner](live-testing.md)
has its own environment-file rules.
