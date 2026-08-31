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
