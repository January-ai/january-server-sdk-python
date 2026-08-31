"""Check archive member names only. Never reads credential or report contents."""
from pathlib import PurePosixPath
import sys
import tarfile
import zipfile


def check_archive(path: str) -> None:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
    if any(any(part == ".e2e-results" or part == ".env" or part.startswith(".env.")
               for part in PurePosixPath(name).parts) for name in names):
        raise ValueError("Distribution contains an excluded credential or result path")


if __name__ == "__main__":
    for artifact in sys.argv[1:]:
        check_archive(artifact)
    print("Distribution credential/result path exclusion check passed")
