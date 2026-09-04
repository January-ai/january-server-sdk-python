# Releasing the January Server SDK for Python

PyPI publication uses GitHub Actions and PyPI Trusted Publishing. Do not create
or store a long-lived PyPI API token in this repository.

## First public release

1. Sign in to PyPI and open **Account settings → Publishing**.
2. Under **Add a new pending publisher**, enter:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `januaryai-server` |
   | GitHub owner | `January-ai` |
   | Repository | `january-server-sdk-python` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

3. Add the pending publisher. This does not reserve the name; publish promptly.
4. Confirm the GitHub repository has an environment named `pypi`.
5. Merge the release commit after CI passes.
6. Create and push the tag that exactly matches `src/januaryai/_version.py`:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

The `Release Python SDK` workflow runs the full test matrix, builds the wheel and
source distribution, validates their contents, obtains a short-lived PyPI token
through OIDC, and publishes both artifacts. The pending publisher automatically
becomes the project's permanent trusted publisher after the first successful
upload.

## Verify the release

```sh
python -m pip install --upgrade januaryai-server==0.1.0
python -c "import januaryai; print(januaryai.__version__)"
```

The printed version must be `0.1.0`. Confirm the project page and files at
<https://pypi.org/project/januaryai-server/>.

## Later releases

Update `src/januaryai/_version.py` and `CHANGELOG.md`, merge after CI passes, then
push the matching `vX.Y.Z` tag. PyPI versions are immutable; never reuse a tag or
version that has already been uploaded.
