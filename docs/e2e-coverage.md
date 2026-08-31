# End-to-end coverage

There are two separate suites. A local HTTP pass is not a production API pass.
Neither suite claims every possible input or every possible provider failure.

| Coverage | Production API | Real local HTTP / deterministic fixtures |
| --- | --- | --- |
| All 18 operations | Sync + asyncio, real returned food/serving IDs | Sync + asyncio + Trio |
| Token mint, scope, expiry, token-authenticated request, revoke | Fresh synthetic user; cleanup | Success, ambiguous failures, cleanup failures |
| Food log create/list/update/delete | Fresh synthetic user; cleanup | Lifecycle and failed/ambiguous write cleanup |
| Image input types | URL, PNG/JPEG Base64 data URI, Path, string path, bytes, bytearray, memoryview, binary file, BytesIO, Pillow | Same matrix; assert HTTP payload and image validity |
| Image formats/transforms | JPEG, PNG, WebP, still GIF, large dimensions, EXIF rotation, transparency, CMYK, preprocessing disabled | Same, plus pixel-level unit assertions |
| Invalid images | Not deliberately sent to production | Empty/corrupt/truncated data, missing paths, directories, text/closed/EOF files, raw Base64, bad schemes/types, animation, byte limit, HEIC without decoder; assert zero requests |
| API errors on every endpoint | Only failures actually observed during the run | 400, 401, 403, 404, 413, 429 rate limit, 429 credits, 500, 501, 502, 503, 504 |
| Retry/replay safety on every endpoint | Retries disabled to bound cost | 429/503 recovery; no ambiguous token/log-create replay; no revocation replay |
| Request validation | Valid production requests | Contract-driven type, null, enum, range, length and date boundaries before HTTP |
| Invalid server responses | Fail if observed | Invalid JSON, wrong shape, unexpected success status; never retried. A 204 revoke response has no JSON body. |

## Run locally without a key

From an activated development environment (`python -m pip install -e '.[test]'`):

```sh
python -m pytest --cov=januaryai --cov-report=term-missing --cov-report=xml --junitxml=.e2e-results/offline.xml
```

The tests use loopback TCP HTTP and fake credentials; no production `.env` is
loaded. Pixel/codec unit tests add coverage for 16-bit/float images, ICC profiles,
decompression bombs, compression quality fallback, and lazy Pillow loading.
Timing/cancellation and transport-failure tests remain deterministic local tests.

## Run against production

Set only `JANUARY_API_KEY` in the existing ignored `.env`, following
[live-testing setup](live-testing.md), then run:

```sh
python examples/live/main.py --mode both --image-matrix
```

This makes 72 requests when all dependencies succeed: 36 endpoint operations,
2 client-token probes, and 34 additional photo analyses. The ordinary photo
operation already covers the PNG Base64 data URI, so the combined matrix covers
18 image cases in each mode. Extra cleanup requests may be needed after an
ambiguous failure. Calls may consume credits; automatic retries are disabled.

Output and `.e2e-results/latest.json` show endpoint and image results separately,
with safe error codes/request IDs. Blocked checks and failed cleanup fail the run.
An external image host can fail or deny January's fetch even when it works on
your laptop; the URL case is intentionally a real server-side download.

## Fixtures

- [food.png](../examples/live/food.png): checked-in meal photo used by local cases.
- [image_cases.py](../examples/live/image_cases.py): reproducible input/format
  factories shared by both suites. Temporary derivatives never overwrite the source.
- URL fixture: [fixed food photo on Unsplash](https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=1024&q=85&fit=crop&fm=jpg).
  A fixed photo ID with explicit JPEG format and width avoids random-image
  endpoints and redirects. Third-party availability is still outside SDK control.
  The existing public image is fetched by January; this suite does not publish
  or upload local fixture files to a hosting service.
- [contract.json](../tests/fixtures/contract.json): generated request/response
  fixtures for all 18 endpoints. Models and operation names stay contract-driven.

Base64 must be a complete `data:image/<format>;base64,...` URI. A raw Base64
string is not a separate supported input. URLs/data URIs are passed through;
their remote validity is determined by January, not by local image preparation.
