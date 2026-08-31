#!/usr/bin/env bash
set -euo pipefail
sdk_root=$(cd "$(dirname "$0")/.." && pwd)
consumer_dir=$(mktemp -d -t january-python-consumer)
trap 'rm -rf -- "$consumer_dir"' EXIT
cd "$sdk_root"
uv build --out-dir "$consumer_dir/artifacts"
for artifact in "$consumer_dir"/artifacts/*.whl "$consumer_dir"/artifacts/*.tar.gz; do
  uv run python scripts/check-distribution.py "$artifact"
  consumer_env=$(mktemp -d "$consumer_dir/env-XXXXXX")
  uv venv "$consumer_env"
  uv pip install --python "$consumer_env/bin/python" "$artifact"
  "$consumer_env/bin/python" -I "$sdk_root/tests/consumer.py"
  "$consumer_env/bin/python" -I "$sdk_root/tests/installed_consumer.py"
  (cd "$consumer_dir" && PATH="$consumer_env/bin:$PATH" "$sdk_root/.venv/bin/pyright" --pythonpath "$consumer_env/bin/python" --verifytypes januaryai --ignoreexternal)
done
