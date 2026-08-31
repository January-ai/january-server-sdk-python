# January Server SDK for Python

[![PyPI version](https://img.shields.io/pypi/v/januaryai-server.svg)](https://pypi.org/project/januaryai-server/)
[![CI](https://github.com/January-ai/january-server-sdk-python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/January-ai/january-server-sdk-python/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

The official Python server SDK for [January AI](https://january.ai).
Build applications that understand what people eat: identify foods from photos
or descriptions, look up nutrition and serving sizes, keep food diaries, and
predict glucose responses.

The SDK provides typed synchronous and asynchronous clients, automatic photo
preparation, automatic retries and immutable user-scoped views.

- **Understand food:** analyze a photo or description, then correct the result in plain language.
- **Find nutrition:** search foods, look up barcodes, explore alternatives and restaurant menus.
- **Work with portions:** recalculate nutrients locally by serving and quantity.
- **Build per-user experiences:** manage food logs and request glucose predictions.
- **Connect mobile and web apps:** mint client tokens on your backend, revoke them and check credits.

Use this SDK on trusted servers, in jobs or in local scripts. Keep server
`sk-` keys out of mobile apps and browsers.

[API reference](https://partners.january.ai/v1.2/docs#/) ·
[Developer dashboard](https://dashboard.january.ai/dashboard) ·
[Support](mailto:support@january.ai)

## Contents

- [Getting an API key](#getting-an-api-key)
- [Installation and dependencies](#installation-and-dependencies)
- [Quickstart](#quickstart)
- [All 19 operations at a glance](#all-19-operations-at-a-glance)
- [End users and user views](#end-users-and-user-views)
- [Food analysis and photos](#food-analysis-and-photos)
- [Portions and serving sizes](#portions-and-serving-sizes)
- [Server-only APIs](#server-only-apis)
- [Async usage](#async-usage)
- [Errors and retries](#errors-and-retries)
- [Configuration and type safety](#configuration-and-type-safety)
- [Examples](#examples)
- [Versioning and contributing](#versioning-and-contributing)
- [License and support](#license-and-support)

## Getting an API key

1. [Sign up](https://dashboard.january.ai/sign-up) or
   [sign in](https://dashboard.january.ai/sign-in) to the developer platform.
2. If prompted, complete **Set up your organization**.
3. Open the [dashboard](https://dashboard.january.ai/dashboard), then **API keys**
   → **Create key** → enter a **Key name** → **Create key**.
4. Copy the complete `sk-` key when shown. It is revealed only once. Store it in
   your secrets manager, or in an ignored `.env` file for local development.

A dashboard login is not an API credential. This SDK uses a server API key;
short-lived `ct-` client tokens are for mobile and web SDKs and are rejected here.
You do not need to enable client tokens to search foods or analyze a photo.

API calls may consume credits. Check your plan and allowance in
[Billing](https://dashboard.january.ai/billing), or call `client.credits()`.

## Installation and dependencies

Requires **Python 3.11+**. Install into your project's virtual environment:

```sh
python -m pip install januaryai-server python-dotenv
```

Or, with uv:

```sh
uv add januaryai-server python-dotenv
```

The package is named `januaryai-server`; Python imports use `januaryai`.
`python-dotenv` is needed only for the local-file examples, not by the SDK itself.

| Dependency | Constraint | Purpose |
| --- | --- | --- |
| `httpx` | `>=0.27,<1` | Synchronous and asynchronous HTTP |
| `pydantic` | `>=2.10,<3` | Typed response models |
| `pillow` | `>=10` | Local image decoding and preparation |
| `anyio` | `>=4,<5` | Async timeouts, retry waits and image-preparation workers |
| `typing-extensions` | `>=4.12,<5` | Type information across supported Python versions |

Pillow loads lazily when image processing needs it. Async applications can use
asyncio or Trio; install `trio` separately if your application uses that backend.

## Quickstart

Create a `.env` file beside your script, containing only your server API key:

```dotenv
JANUARY_API_KEY=your-server-api-key
```

Add it to your project's `.gitignore`:

```gitignore
.env
```

In this repository, start from [.env.example](.env.example), without overwriting
an existing file:

```sh
test -e .env || cp .env.example .env
```

Save the following as `quickstart.py`, then run `python quickstart.py` from that
directory. It makes one food-search request to January's built-in production endpoint.

<!-- quickstart:minimal.py -->
```python
from pathlib import Path

from dotenv import load_dotenv

from januaryai import January

load_dotenv(Path.cwd() / ".env", override=False)

with January(max_retries=0) as client:
    user = client.for_user("january-quickstart", end_user_timezone="UTC")
    foods = user.foods.search(query="banana")

print(f"Foods returned: {len(foods.items)}")
if foods.items:
    print(f"First food: {foods.items[0].name}")
```

You get a result count and the first food name, if any. An empty `items` list is
a valid result. Replace the example user ID with your authenticated user's ID.

`January()` reads `JANUARY_API_KEY` from the environment. Your application loads
the `.env`; the SDK never searches for one. Existing environment values take
precedence. The example disables retries to keep it to one request.

## All 19 operations at a glance

The table uses an open `client` and a `user = client.for_user(...)` view.
Calls show the main arguments; replace `...` with your application's values.
All arguments are keyword-only. Async clients expose the same methods with `await`.

| Call | What it does | Returns |
| --- | --- | --- |
| `user.food_analysis.analyze_photo(image=...)` | Identify foods in a photo | `FoodScan` |
| `user.food_analysis.analyze_description(query=...)` | Understand a written food description | `FoodScan` |
| `user.food_analysis.correct(detections=..., user_input=...)` | Revise an analysis in plain language | `FoodScan` |
| `user.foods.search(query=...)` | Search the food database | `FoodSearchResults` |
| `user.foods.autocomplete(query=...)` | Suggest food names while typing | `AutocompleteFoodsResponse` |
| `user.foods.get(food_id=...)` | Retrieve a food and its serving options | `FoodSearchItem` |
| `user.foods.lookup_barcode(upc=...)` | Find a food by barcode | `FoodSearchResults` |
| `user.foods.suggest_alternatives(food_id=...)` | Find food alternatives with dietary filters | `SuggestFoodAlternativesResponse` |
| `user.restaurants.search(query=..., latitude=..., longitude=...)` | Find nearby restaurants | `SearchRestaurantsResponse` |
| `user.restaurants.search_menu_items(query=..., latitude=..., longitude=...)` | Find dishes across nearby restaurant menus | `SearchRestaurantMenuItemsResponse` |
| `user.restaurants.get_menu_items(restaurant_id=..., limit=100, offset=0)` | Load the selected restaurant’s menu by ID | `SearchRestaurantMenuItemsResponse` |
| `user.food_logs.create(foods=...)` | Record a meal for a user | `FoodLog` |
| `user.food_logs.list(start=..., end=...)` | List food logs within a date range | `ListFoodLogsResponse` |
| `user.food_logs.update(log_id=..., name=...)` | Update a food log's supplied fields | `FoodLog` |
| `user.food_logs.delete(log_id=...)` | Delete a food log | `DeleteFoodLogResponse` |
| `user.glucose.predict(user_profile=..., foods=..., start_time=...)` | Predict a meal's glucose response | `GlucosePrediction` |
| `client.credits()` | Read the account's credit balance | `CreditsResponseDto` |
| `client.mint_client_token(end_user_id=...)` | Mint a short-lived token for an end user | `ClientTokenResponseDto` |
| `client.revoke_client_tokens(end_user_id=...)` | Revoke an end user's client tokens | `ResponseMetadata` |

The first 15 operations also exist directly on `client`, with explicit
`end_user_id=` and, where applicable, `end_user_timezone=`. The last three are
server-only and are not exposed by a user view. Your editor shows optional
arguments and typed response fields through autocomplete.

## End users and user views

An end-user ID is your application's stable identifier for the person the
request belongs to. Food logs need that identity; it keeps one user's diary
separate from another's.

Reuse one client and call `client.for_user(user_id, end_user_timezone="UTC")`
when handling a user's request. The returned view is immutable: its bound
identity and timezone take precedence over per-call values, without changing the
client or other views.

Use an IANA timezone such as `America/New_York` when calendar days should follow
the user's local time. Only operations declaring that header receive it.

## Food analysis and photos

A photo and a written description both return `FoodScan`: recognized foods,
serving options and nutrition. `food_analysis.correct` accepts the returned
detections and a description of what should change.

Within an open client context, for an existing `user` view:

```python
analysis = user.food_analysis.analyze_photo(image="lunch.jpg")
revised = user.food_analysis.correct(
    detections=analysis.detections,
    user_input="The rice portion was half as much",
)
```

Use `analyze_description(query="one banana and a bowl of oatmeal")` for text.
An empty `detections` list is valid. Nutrient entries may be absent: check for
`None` before reading a nutrient's `value`.

Photo inputs can be a public URL, Base64 **data URI**, trusted local path, bytes,
bytearray, memoryview, binary file, BytesIO or Pillow image. JPEG, PNG, WebP and
still GIF are supported. Raw Base64 needs the `data:image/...;base64,` prefix.

Local preparation applies EXIF rotation, caps the longest side at 1024 pixels,
and compresses when needed to a 3.5 MB encoded-image budget. It never upscales or
mutates a supplied Pillow image. Async preparation runs off the event loop.

URLs/data URIs and compliant encoded files pass through unchanged, including
any metadata. Re-encoding strips EXIF/GPS, while a modest RGB ICC profile may be
retained. Pass a Pillow image to force re-encoding when metadata removal matters.
`preprocess=False` skips resizing, not byte-limit or animation checks.
HEIC/HEIF needs a decoder plugin or conversion first.

Never treat an untrusted user's string as a local file path. Invalid local
images raise `ValueError`, `TypeError` or `FileNotFoundError` before HTTP.
See the [analysis → correction → logging recipe](docs/recipes.md#analyze-correct-then-log).

## Portions and serving sizes

`FoodPortion` recalculates nutrition locally, without API calls or credits.
Given a `food` returned by the API:

```python
from januaryai import FoodPortion

portion = FoodPortion.from_food(food, quantity=2)
nutrition = portion.nutrition
selection = portion.selection  # Use in foods=[selection] when logging or predicting.
```

Pass `serving_id=` to select a specific serving; otherwise the first primary
serving is used, falling back to the first available one. Quantity must be finite,
positive and at most 10,000. Nutrients and glycemic load scale with the serving;
glycemic index stays unchanged. The original food is never modified.

The utility also works with async results. See the
[serving-selection recipe](docs/recipes.md#search-choose-a-serving-calculate-locally-then-log)
and [runnable portion example](examples/portions/main.py).

## Server-only APIs

`client.credits()`, `client.mint_client_token(...)` and
`client.revoke_client_tokens(...)` operate on the root client, never a user view.

For client-token minting, first open
[Client tokens](https://dashboard.january.ai/dashboard/client-tokens) and choose
**Enable client tokens** for your partner account. Mint on your backend with the
authenticated user's ID; optionally supply `scopes=["foods:read"]` and
`ttl_seconds=1800`. Return the token only to that authenticated user, never to logs.

Revoke separately when intentionally invalidating that user's tokens—not
immediately after minting. Revocation makes one DELETE request and exposes
`revoked_count` on the returned metadata. It is never automatically retried.

For mobile/web integration, see the [authenticated token-relay example](examples/fastapi/README.md).

## Async usage

Use `AsyncJanuary` with an async context manager and `await` the same resource
methods. User views, models and error types are shared with the sync client.
The client works with asyncio and Trio; cancellation propagates through requests
and retry waits.

Copy the complete [async quickstart](examples/quickstart/async_main.py) to
`quickstart_async.py`, then run `python quickstart_async.py` with the same `.env`.
For multiple photos, see the [bounded-concurrency example](examples/analysis/concurrent.py).

## Errors and retries

Catch specific API errors when your application can act on them:

| Error | What to do |
| --- | --- |
| `BadRequestError` | Check the request parameters |
| `AuthenticationError` | Check that the server key is valid and active |
| `PermissionDeniedError` | Check permissions; token minting also requires enabling client tokens |
| `NotFoundError` | Check the resource identifier |
| `PayloadTooLargeError` | Reduce or prepare the image |
| `RateLimitError` | Respect `retry_after` when retries are exhausted |
| `CreditLimitExceededError` | Check [Billing](https://dashboard.january.ai/billing); retrying will not fix an exhausted allowance |
| `InternalServerError` | Handle a server or upstream failure |

All of these inherit from `JanuaryAPIError`. Errors expose safe metadata such as
`status_code`, `code` and `request_id`; exception strings do not dump credentials.
Do not log keys, tokens or food payloads.

Clients default to two bounded, error-code-aware retries with jitter and
Retry-After support. Permanent errors and credit exhaustion are not retried.
Token minting and food-log creation are not replayed after ambiguous network
failures or 5xx responses. Retried analysis calls may consume additional credits.

Defaults are 60 seconds per call and 120 seconds for analysis, with a 5-second
connection timeout. Override using `timeout=`; use `max_retries=0` for one attempt.
See [configuration and error details](docs/configuration.md) for retry budgets,
HTTPX phase timeouts, cancellation and transport error types.

## Configuration and type safety

The only required configuration is your API key. `January()` reads
`JANUARY_API_KEY`; `api_key=` overrides it. Existing `secret_key=` calls remain
supported, but do not supply both. Production is built in.

Reuse a context-managed client. If you provide an HTTPX client, your application
owns closing it. `default_headers=` can add tracing headers, not override identity.

Models are typed and preserve unknown response fields. Passing returned
detections into a correction preserves those additions; arbitrary request
dictionaries still undergo strict validation. Omitted fields stay omitted, and
`None` is accepted only where the contract allows null.

Use `model_dump(mode="json", exclude_unset=True)` for JSON-ready output that
preserves omissions. See [configuration and type-safety details](docs/configuration.md)
for native datetimes and parsed accessors that preserve opaque timestamps.

## Examples

| Example or guide | Start here |
| --- | --- |
| One-request quickstart | [minimal.py](examples/quickstart/minimal.py) |
| Food search with error handling | [Sync](examples/quickstart/main.py) · [Async](examples/quickstart/async_main.py) |
| Analyze, correct and log; select a serving and log | [Workflow recipes](docs/recipes.md) |
| Concurrent photo analysis | [concurrent.py](examples/analysis/concurrent.py) |
| Local portion calculation | [main.py](examples/portions/main.py) |
| Issue client tokens from your backend | [FastAPI example](examples/fastapi/README.md) |

Follow the [quickstart setup](#quickstart), then run examples from the directory
containing your `.env` file. The FastAPI example includes its own setup instructions.

## Versioning and contributing

The SDK targets API `/v1.2`; package versions are separate from API versions.
See the [changelog](CHANGELOG.md) before upgrading and pin a compatible package
version in your application.

To contribute a fix, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License and support

Released under the [MIT license](LICENSE). For help, contact
[support@january.ai](mailto:support@january.ai) with a minimal reproduction and
safe request IDs. Report sensitive issues privately using the
[security policy](SECURITY.md).

The new `get_menu_items` operation requires the backend restaurant-ID menu endpoint. Deployment is pending for this unreleased change. Use `offset` to page through the returned `total_count`; the async client exposes the same method.
