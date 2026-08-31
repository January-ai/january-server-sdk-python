# Contributing

For installation and SDK usage, see the [README](README.md).

## Development

Use Python 3.11+ and run these commands from the repository root:

```sh
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[test]'
python -m pytest
python -m ruff check src tests examples scripts
python -m ruff format --check src tests examples scripts
pyright
```

Tests use local fixtures and do not require an API key. Include regression tests
with bug fixes. Files marked as generated are maintained through January's API
contract; request API or model changes in an issue rather than editing them.

## Live API checks

Optional: copy [.env.example](.env.example) to an ignored `.env` file without
overwriting an existing file, and set `JANUARY_API_KEY`.
[Enable client tokens](https://dashboard.january.ai/dashboard/client-tokens)
before running the full workflow.

```sh
python examples/live/main.py --mode both
# Include additional photo input formats:
python examples/live/main.py --mode both --image-matrix
```

These checks call production, consume credits, and create food logs and client
tokens for temporary test users. Cleanup runs automatically; review any reported
cleanup failures. Results are written to `.e2e-results/latest.json`. Never commit
credentials or result files.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
