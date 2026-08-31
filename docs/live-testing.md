# Live API testing (opt-in)

[Back to the SDK README](../README.md). Run every command below from the SDK root,
not from this `docs/` directory. This is separate from the one-request quickstarts.
This live runner loads `.env` from the SDK root. Quickstarts load only their
current working directory's `.env`; the SDK itself never reads environment files.

On a fresh checkout, copy `.env.example` to `.env` only if `.env` does not already
exist, then set `JANUARY_API_KEY` in `.env` (or your shell):

```sh
test -e .env || cp .env.example .env
# Edit .env and set JANUARY_API_KEY. Never overwrite an existing .env.
```

`.env` and `.e2e-results/` are ignored; only `.env.example` is intended to be
tracked. Never commit the key. The runner reads `.env` as data, without shell execution,
variable interpolation, or escape expansion. Shell variables override file
values, including empty values. `JANUARY_ENV_FILE` selects a different file;
relative paths are resolved against this SDK root. The runner never modifies
either environment file.

Before running the all-18-operations workflow (in either mode), open
[Client tokens](https://dashboard.january.ai/dashboard/client-tokens) and choose
**Enable client tokens** for your partner account. Enabling this optional feature
is required here because the workflow mints and revokes client tokens; it is
not needed for the server food-search quickstart. Live calls consume credits:
check [Billing](https://dashboard.january.ai/billing) for your allowance and the
canonical root `credits()` operation for your balance. The runner checks credits,
but that check does not reserve credits or make subsequent calls free.

Running this command is explicit consent to API requests, credit consumption,
and creating/deleting synthetic food logs and client tokens:

```sh
uv run python examples/live/main.py
# Equivalent: uv run python examples/live/main.py --mode both
# Individual modes: --mode sync or --mode async
# All supported photo inputs and representative image transformations:
uv run python examples/live/main.py --mode both --image-matrix
```

Each mode gets a new `sdk-e2e-python-UUID` user in UTC. An existing user ID cannot
be selected. The demo exercises all 18 SDK operations, using live returned food
and serving IDs, the actual `examples/live/food.png` image, and a synthetic glucose
profile. It verifies the minted token's user, scope, and expiry and then makes
one native HTTP food-search request with that token. It never passes a client
token to the server SDK.

The runner uses the SDK's built-in production endpoint. Configuration defaults
(all optional except the key):

| Variable | Default |
| --- | --- |
| `JANUARY_E2E_TIMEOUT_SECONDS` | `120` per request |
| `JANUARY_E2E_UPC` | `049000006346` |
| `JANUARY_E2E_QUERY` | `banana` |
| `JANUARY_E2E_RESTAURANT_QUERY` | `chicken` |
| `JANUARY_E2E_LATITUDE` / `JANUARY_E2E_LONGITUDE` | `37.7749` / `-122.4194` |
| `JANUARY_E2E_IMAGE_PATH` | `examples/live/food.png` (PNG or JPEG) |

Independent calls continue after a failure. Dependencies are reported as
`BLOCKED`, never counted as passing. The command exits successfully only when
all 36 SDK calls, both token probes, and cleanup pass. Missing credentials produce
an explicit `NOT_RUN` configuration result and nonzero exit before any network request.
Output and `.e2e-results/latest.json` contain only safe labels, statuses, error
codes, request IDs, durations, counts, and static dependency reasons—not API keys,
tokens, user/log IDs, response bodies, or food descriptions.

Cleanup runs in `finally`: this run's log IDs are deleted and its new token user
is revoked once, even if minting failed ambiguously. An ambiguous log creation
can be recovered by a targeted list for this fresh user and unique log marker.
If that recovery cannot identify the log, cleanup explicitly fails with
`ambiguous_create_cleanup_unconfirmed`.
Cleanup failure fails the run. There are no transport retries or revoke-all
loops; no assertion assumes immediate token rejection after revocation because
the API permits a 60-second cache delay. Hard process termination cannot guarantee
cleanup; failed or interrupted runs must not be represented as passing.

`--image-matrix` adds 17 photo requests per mode (34 in both modes). Including
the normal PNG data-URI operation, this covers 18 image cases per mode: URL,
PNG/JPEG data URIs, paths, byte containers, files, Pillow, and format/transform
variants. Image outcomes have separate `imageCounts` in the report; missing or
failed cases fail the run. See the [coverage matrix and fixture inventory](e2e-coverage.md)
for the precise split between production checks and deterministic local faults.

Credentialed live runs are never part of offline tests, CI, or distribution checks.
The runner's deterministic tests use test-owned transport injection for both SDK
calls and native token probes, a loopback HTTP service, and synthetic credentials:

```sh
uv run pytest -q tests/test_live_runner.py
```

Root `.env`, `.env.*`, and `.e2e-results` are explicitly excluded from wheel and
sdist builds, including when this directory is not a Git repository.
