# Releasing

The version lives in exactly one place: `__version__` in `src/beancount_zakat/__init__.py`. `pyproject.toml` reads it from there (`[tool.hatch.version]`), so the wheel, the PyPI page and `beancount-zakat --version` cannot disagree.

## One-time setup on PyPI

The release workflow uses [Trusted Publishing][tp], so there is no API token to create, store or rotate.

1. Sign in to <https://pypi.org> and open **Your projects → Publishing**, or go straight to <https://pypi.org/manage/account/publishing/>.
2. Add a **pending publisher** for a project that does not exist yet:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `beancount-zakat` |
   | Owner | `WildeBeast2521` |
   | Repository name | `beancount-zakat` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. In the GitHub repository, **Settings → Environments → New environment**, named `pypi`. Add yourself as a required reviewer if you want a manual approval step between the build and the upload.

The first successful run creates the project on PyPI and converts the pending publisher into a real one.

[tp]: https://docs.pypi.org/trusted-publishers/

## Cutting a release

```bash
# 1. Bump the version and write the changelog entry.
$EDITOR src/beancount_zakat/__init__.py     # __version__ = "1.1.0"
$EDITOR CHANGELOG.md                        # move Unreleased -> 1.1.0, dated

# 2. Prove it is green locally.
pytest
ruff check src tests && ruff format --check src tests
mypy src/beancount_zakat

# 3. Build and read what PyPI will read.
rm -rf dist && python -m build
python -m twine check --strict dist/*

# 4. Install the built wheel somewhere clean and run it.
python -m venv /tmp/smoke
/tmp/smoke/bin/pip install "dist/beancount_zakat-1.1.0-py3-none-any.whl[fava]"
/tmp/smoke/bin/beancount-zakat --version
/tmp/smoke/bin/beancount-zakat examples/ledger/main.beancount --as-of 2026-08-20

# 5. Commit, tag, push. The tag is what triggers the upload.
git commit -am "Release 1.1.0"
git tag -a v1.1.0 -m "1.1.0"
git push origin main --follow-tags
```

The `Release` workflow builds, runs `twine check --strict`, refuses to continue if the tag and `__version__` disagree, and then publishes. Watch it under **Actions**; if you configured a required reviewer, it will wait for you there.

## Publishing by hand instead

If you would rather not use CI, the same artifacts upload with an API token:

```bash
python -m build
python -m twine check --strict dist/*
python -m twine upload dist/*     # username: __token__, password: pypi-…
```

Create the token at <https://pypi.org/manage/account/token/>, scope it to this project once the project exists, and keep it out of the repository — `twine` reads `~/.pypirc` or the `TWINE_PASSWORD` environment variable.

## After a release

- Check the rendered page at <https://pypi.org/project/beancount-zakat/>. The README's screenshot links point at `docs/screenshots/`, which the sdist deliberately excludes, so they resolve on GitHub and not on PyPI. That is intentional — the images are ~4.5 MB of repository evidence.
- Open a fresh `## Unreleased` heading in `CHANGELOG.md`.
