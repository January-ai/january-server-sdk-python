"""Defaults for image preparation and bounded request retries."""

DEFAULT_MAX_RETRIES = 2
INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
MAX_HONORED_RETRY_AFTER = 60.0
MAX_TOTAL_RETRY_AFTER_WAIT = 60.0
MAX_IMAGE_DIMENSION = 1024
MAX_IMAGE_BYTES = 3_500_000
JPEG_QUALITY_LADDER = (85, 75, 65)
MAX_ICC_PROFILE_BYTES = 65_536

# Creates that the server does not deduplicate: a retry after an ambiguous
# failure (timeout, dropped connection, 5xx) could record the entry twice.
# Token minting and food-log creation carry the same rule in the contract.
NEVER_REPLAY_AMBIGUOUS = frozenset({"createWaterLog", "createWeightLog"})
