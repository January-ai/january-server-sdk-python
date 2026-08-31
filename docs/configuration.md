# Configuration, errors and retries

[Back to the SDK README](../README.md).

- Use `January()` / `AsyncJanuary()` with `JANUARY_API_KEY`, or pass
  `api_key=` explicitly. Existing `secret_key=` calls remain supported.
  Do not supply both. Client-token `ct-` credentials are rejected.
- Reuse one client with a context manager. A supplied HTTPX client remains owned
  by your application. `default_headers=` can add tracing headers but cannot
  override authentication or user identity.
- Async calls work with asyncio and Trio. Cancellation propagates through
  requests and retry sleeps. Synchronous calls accept `cancel_event=`.
- Omitted request values remain unset; `None` is accepted only where nullable.
  Known fields are schema-validated. Only typed models returned by an analysis
  may carry new response fields back into a correction; arbitrary dictionaries
  do not bypass validation.
- Declared date/time schemas use native Python types. Opaque wire timestamps
  stay unchanged: `log.timestamp_utc_datetime`, `token.expires_at_datetime`, and
  `balance.resets_at_datetime` return an aware datetime when parseable, else
  `None`. JSON serialization uses `model_dump(mode="json")`.
- Calendar-date ranges accept `date`, `datetime`, or `YYYY-MM-DD` strings. A
  `datetime` contributes its local calendar date without timezone conversion.
  Food-log creation/update timestamps accept aware `datetime` values, serialized
  to UTC; naive timestamps remain invalid.

### Errors you can catch

All API-status exceptions remain subclasses of `JanuaryAPIError`, so existing
handlers continue working:

| Exception | Meaning |
| --- | --- |
| `BadRequestError` | Invalid request |
| `AuthenticationError` | Missing, expired, or invalid key |
| `PermissionDeniedError` | Insufficient access |
| `NotFoundError` | Resource not found |
| `PayloadTooLargeError` | Request/image too large |
| `RateLimitError` | Temporary rate limiting |
| `CreditLimitExceededError` | Credit allowance exhausted; never retried |
| `InternalServerError` | Server/upstream error |

Errors expose `status_code`, `code`, `request_id`, `docs_url`, and parsed
`retry_after` seconds when supplied. The response metadata retains the raw
Retry-After header. Exception strings and model representations stay redacted.
Connection, timeout, cancellation and invalid-response errors retain their
existing `JanuaryConnectionError`, `JanuaryTimeoutError`,
`JanuaryCancelledError` and `JanuaryResponseError` types.

`JanuaryResponseError` is separate from `JanuaryAPIError`: it describes an invalid
success response, not a server rejection. Catch `JanuaryError` to handle both,
including closed-client and redirect failures. Supplied HTTPX clients remain
usable after the SDK wrapper is closed, until their owner closes the transport.

Status classification follows HTTP status except for `rate_limited` and
`credit_limit_exceeded`, whose codes distinguish rate limits from exhausted credits.
Retry decisions remain code-aware independently of the exception class.

`message` contains at most 200 characters plus a truncation marker. `body` retains
redacted JSON or diagnostic text, including gateway HTML/plain-text failures;
neither is a raw response. `response` remains safe HTTP metadata. Validation and
transport details remain inspectable through `cause`, without printing sensitive
values in chained tracebacks. Standard exception `__notes__` explain retry waits
refused due to a per-wait limit, cumulative server-wait limit, or request timeout.

### Retries and timeouts

Clients default to `max_retries=2` (at most three attempts). Set
`max_retries=0` for one attempt, as the quickstarts and live-test runner do.

Retry decisions use the stable API error code: transient rate/server/upstream
failures can retry; exhausted credits and known permanent errors cannot.
Unknown/missing codes fall back to HTTP 429/500/502/503/504.
Backoff uses jitter, respects Retry-After seconds or HTTP dates, and honors at
most 60 seconds of server-requested waiting per call. Longer waits raise the
original typed error with `retry_after` so the application can schedule work.
Ordinary jittered backoff does not count toward that server-requested allowance;
all waits still count toward the overall request timeout.

A token mint or food-log creation is never replayed after an ambiguous network
failure or 5xx, which could duplicate the write. Failures known to occur before
sending, or a 429 rejection, may retry. Revocation always makes one request.
Retried reads/analyses may consume extra credits if the previous attempt
succeeded but its response was lost. No idempotency keys or API paths are invented.

Default limits are 60 seconds (5-second connection timeout), or 120 seconds for
food analysis. Explicit client/per-call `timeout=` wins. It accepts seconds or
`httpx.Timeout` with finite positive values for every phase. The longest phase
also bounds the total request/retry duration. Cancellation interrupts retry waits.

### Troubleshooting

| Symptom | Next step |
| --- | --- |
| Missing/invalid key | Set the full server `sk-` key in `.env`; a dashboard login or client token is not a server key. |
| HTTP 401 | Check that the key is active and belongs to the correct organization. |
| HTTP 403 | Check key permissions; token minting also requires **Enable client tokens**. |
| HTTP 429, `rate_limited` | Respect `retry_after` if retry attempts are exhausted. |
| HTTP 429, `credit_limit_exceeded` | Check [Billing](https://dashboard.january.ai/billing), not a sleep-and-retry loop. |
| Timeout/connection failure | Check connectivity. An interrupted request may already have reached the service. |

Share safe status/code/request-ID fields with
[support@january.ai](mailto:support@january.ai), never keys, tokens or food payloads.
