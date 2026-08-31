# Maintainer release checklist

1. Configure the PyPI Trusted Publisher for project `januaryai-server`, owner
   `January-ai`, repository `january-server-sdk-python`, workflow `release.yml`,
   and GitHub environment `pypi`. Configure the environment to require approval.
   No long-lived PyPI API token is needed.
2. Update `src/januaryai/_version.py` and the changelog in a reviewed PR. This is
   the single version source used by package metadata and the HTTP User-Agent.
3. Require all CI jobs to pass. Review compatibility, dependencies, example
   behavior and generated contract changes. Keep repository visibility unchanged.
4. After explicit release approval, tag the reviewed commit `v<version>` and push
   that tag. The release workflow reruns CI, checks tag/version agreement, builds
   and validates wheel/sdist, checks exclusions, and publishes the same artifacts
   through the protected PyPI environment.

The workflow does not configure PyPI or GitHub environment protections for you.
Do not push a release tag before those are configured. Package publication and
repository visibility are independent decisions. Never include local .env files
or live-test results in distributions, and never overwrite an existing version.

## Local checks (no publication)

```sh
python -m build
python -m twine check dist/*.whl dist/*.tar.gz
python scripts/check-distribution.py dist/*.whl dist/*.tar.gz
python scripts/test-installed.py
```

The installed checks use a temporary virtual environment, synthetic credentials
and a loopback HTTP service. They never call the production API.
