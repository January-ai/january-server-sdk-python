# Changelog

## Unreleased

- Prevent Authorization headers from appearing in SDK traceback locals. Closed
  clients and redirects now raise JanuaryError without exposing raw HTTP details.
- Match reference status/code classification and count only server-directed waits
  against the Retry-After allowance. Explain refused retries with exception notes.
- Add bounded, redacted diagnostic messages/bodies. JanuaryResponseError is now
  separate from JanuaryAPIError; catch JanuaryError to handle both categories.
- Accept datetime calendar dates and typed native food-log update timestamps.
  Correct generated optional-field defaults and covariant sequence input hints.
- Expand lint rules and type-check coverage across every example and test module.

- Prepare food photos from paths, bytes, file objects and Pillow images, including
  orientation, resizing and compression. Keep existing analysis API names.
- Add specific API error classes and parsed Retry-After metadata while preserving
  JanuaryAPIError compatibility and redacted exception strings.
- Read JANUARY_API_KEY when no explicit credential is supplied; add api_key as an
  alternative to secret_key. Missing credentials now fail at construction unless
  using an explicit demo token issuer. No automatic .env or endpoint lookup.
- Support asyncio and Trio, bounded error-code-aware retries, per-phase timeouts,
  cancellation during retry sleeps, and optional tracing headers.
- Default to two eligible retries. Set max_retries=0 to retain single-attempt
  behavior. Token mint/log creation do not replay ambiguous failures; revocation
  remains a single request. Defaults are 60 seconds, or 120 for analysis.
- Preserve new fields in returned detection models when submitting corrections,
  without accepting unknown fields in arbitrary request dictionaries.
- Add native declared date/time types and optional parsed timestamp accessors,
  preserving opaque wire timestamps unchanged.
- Expand docstrings, workflow recipes, cross-platform tests, coverage, dependency
  maintenance and tag-gated PyPI release tooling.

FoodPortion, immutable for_user views, production defaults, API vocabulary and
contract-generated updates remain unchanged.
