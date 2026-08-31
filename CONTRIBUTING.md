# Contributing and local development

[Back to the SDK README](README.md). Commands below run from this SDK root unless
explicitly marked as contract-repository commands.

## Setup and offline verification

Python 3.11+ and `uv` are required for these contributor commands:

```sh
uv sync --extra test
uv run pytest -q
uv run pyright
uv run ruff check src tests examples scripts
uv run ruff format --check src tests examples scripts
```

To run repository examples with `python`, activate the environment using
`source .venv/bin/activate` (macOS/Linux) or `.venv\Scripts\Activate.ps1`
(PowerShell). Alternatively, prefix each example command with `uv run`.
The test extra includes `python-dotenv` for the quickstarts only. Follow the
[README setup](README.md#quickstart) to copy `.env.example` without overwriting an
existing `.env`, fill in the key, and run examples from the SDK root.

The regular pytest suite includes subprocess tests of the exact README sync and
async quickstart sources. Children use temporary working directories and
synthetic `.env` files or fake environment credentials;
the test harness injects loopback transports programmatically. Children never
inherit credential variables or load the root `.env`.
It also covers all 18 operations, sparse nutrients, FoodPortion, user isolation,
errors, encoding, cancellation, and cleanup behavior.

Type checking includes all source, tests, examples and scripts. The SDK, public
consumer type fixtures and quickstarts use strict Pyright; dynamic HTTP fixtures
and the remaining tooling use standard checking. Negative tests suppress only
the specific error they deliberately exercise. Ruff enables the reference SDK's
E/F/W/I/UP/B/C4/SIM/RUF families (wrapping is handled by the formatter); generated
files retain the contract generator's deterministic formatting.

`tests/test_error_parity.py` covers reference error classification, credential
safety in traceback locals, closed-client ownership, mixed retry budgets, safe
diagnostics and native dates in sync, asyncio and Trio modes. No old SDK checkout
or production credential is required.

Focused quickstart checks:

```sh
uv run pytest -q tests/test_quickstart.py
```

## Build and verify distributions

After setup, use the single canonical distribution command:

```sh
bash scripts/test-distribution.sh
```

It builds wheel and sdist, checks archive member names for excluded credential and
result paths, installs each artifact into a clean temporary environment, and runs
both `tests/consumer.py` (prototype compatibility) and
`tests/installed_consumer.py` (FoodPortion plus 18 sync/18 async HTTP calls).
It checks installed `py.typed` type completeness and deletes temporary environments.
All HTTP checks use localhost and fake credentials.

To retain local build artifacts without publishing:

```sh
uv build --out-dir dist/server-sdk
```

Root `.env`, `.env.*`, and `.e2e-results` are explicitly excluded from wheel
and sdist builds even outside a Git repository. Do not add credentials or results
to package inputs. Distribution identity remains `januaryai-server` and its
import is `januaryai`.

## Contract generation

Generated files must not be edited manually. The contract repository owns paths,
models, public operation bindings, and vocabulary. Its artifact is a development
profile, not an immutable released client contract.

Run **from the partner-api-contract repository**, with the SDK path adjusted:

```sh
node tools/server-sdk/python.mjs \
  --contract artifacts/server-sdk/contract.json \
  --output /path/to/january-server-sdk-python
# Add --check to verify generated output without writing.
```

The generator uses Node builtins and emits typed models, public operation wrappers,
internal wire descriptors, `sdk-surface.json`, `sdk-contract.lock.json`, and a
test-only copy of sibling `fixtures.json`. Locks contain raw contract/generator
SHA-256 hashes and 18-operation coverage. Installed SDKs and standalone tests need
no sibling repository.

## Compatibility and legacy examples

`January` and `AsyncJanuary` remain canonical clients. Existing
`JanuaryClient` / `AsyncJanuaryClient` aliases, `client_tokens.create(...)`,
demo token issuers, and the [FastAPI example](examples/fastapi/README.md) remain.
The FastAPI example uses the SDK's built-in production endpoint and canonical
`mint_client_token` operation. Like the quickstarts, it loads `JANUARY_API_KEY`
from the current working directory's `.env`; existing environment values take
precedence. Follow its README for setup and authenticated token-relay requests.

The custom `client_token_path` override is unsupported: paths come only from the
generated contract. Do not change API names or wire semantics in SDK wrappers.

## Live testing and support

[Live testing](docs/live-testing.md) is opt-in, consumes credits, and mutates only
fresh synthetic users. Never add credentialed production runs to regular CI or
the offline distribution check. Never overwrite an existing `.env`.

For problems, share a minimal reproduction, runtime/package versions, and safe
request IDs with [support@january.ai](mailto:support@january.ai). Do not include keys, tokens,
food records, or response bodies. See [release tooling](docs/releasing.md) for the
maintainer checklist and [security policy](SECURITY.md) for private reporting.
