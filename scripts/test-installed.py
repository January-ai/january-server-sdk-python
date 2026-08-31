"""Install each distribution into a clean environment and run local HTTP flows.

No API key or .env is needed. Works on Windows, macOS and Linux.
"""

import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    artifacts = sorted((root / "dist").glob("*.whl")) + sorted((root / "dist").glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise SystemExit("Expected exactly one wheel and one source distribution in dist/")
    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="january-installed-") as directory:
            target = Path(directory)
            venv.EnvBuilder(with_pip=True).create(target / "venv")
            executable = (
                target / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            subprocess.run([str(executable), "-m", "pip", "install", str(artifact)], check=True)
            environment = {
                k: os.environ[k]
                for k in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                if k in os.environ
            }
            for script in ("consumer.py", "installed_consumer.py"):
                subprocess.run(
                    [str(executable), "-I", str(root / "tests" / script)],
                    cwd=target,
                    env=environment,
                    check=True,
                )


if __name__ == "__main__":
    main()
