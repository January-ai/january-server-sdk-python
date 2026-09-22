# Changelog

## Unreleased

## 0.2.0 - 2026-09-22

- Add water logs: `water_logs.create`, `water_logs.list` (daily totals in the
  requested unit) and `water_logs.delete` (idempotent).
- Add weight logs: `weight_logs.create` and `weight_logs.list` (latest weight per day).
- Add the `water_logs:read`, `water_logs:write`, `weight_logs:read` and
  `weight_logs:write` client-token scopes.
- Water and weight creation are never replayed after an ambiguous failure, like
  food-log creation and token minting.
- `food_logs.update` rejects an empty update before sending it, and only sends the
  fields you set.
- `food_analysis.correct` sends a returned `FoodScan` back as the correction input
  the API now documents (`CorrectionAnalysis`); a serving weight is optional there.
- Logged foods carry the same `ServingSummary` as analysis results, including
  `weight_grams`.

## 0.1.0 - 2026-09-04

Initial public release.

- Prepare food photos from paths, bytes, file objects and Pillow images, including
  orientation, resizing and compression.
- Add specific API error classes and parsed Retry-After metadata.
  `JanuaryResponseError` represents an invalid success response and is separate
  from `JanuaryAPIError`; catch `JanuaryError` to handle both.
- Redact credentials from exception messages, diagnostic bodies and traceback locals.
  Closed clients and redirects raise `JanuaryError`.
- Read `JANUARY_API_KEY` when no credential is supplied. Accept `api_key=` or
  `secret_key=`; do not pass both. Applications load their own `.env` files.
- Support asyncio and Trio, per-phase timeouts, cancellation during retry waits,
  and optional tracing headers.
- Default to two error-code-aware retries. Use `max_retries=0` for one attempt.
  Token minting and food-log creation do not replay ambiguous failures; revocation
  is never retried. Default timeouts are 60 seconds, or 120 seconds for analysis.
- Preserve new fields in returned detection models when submitting corrections,
  while retaining validation of request dictionaries.
- Accept native date/time inputs and add parsed timestamp accessors.
- Improve optional-field defaults, input type hints and editor documentation.
