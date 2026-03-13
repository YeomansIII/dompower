# Releasing dompower

## Overview

Releases are published to [PyPI](https://pypi.org/p/dompower) via GitHub Actions using trusted publishing (OIDC). The workflow is triggered by creating a GitHub release.

## Steps

1. **Ensure CI is green on `main`.**

2. **Bump the version** in three files:
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `dompower/__init__.py` → `__version__ = "X.Y.Z"`
   - `uv.lock` → run `uv lock` to update automatically

3. **Commit and push:**
   ```bash
   git add pyproject.toml dompower/__init__.py uv.lock
   git commit -m "Bump version to X.Y.Z"
   git push
   ```

4. **Wait for CI to pass** on the version bump commit.

5. **Create a GitHub release:**
   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes "Release notes here"
   ```
   Or create via the GitHub UI at https://github.com/YeomansIII/dompower/releases/new.

6. **The `Publish to PyPI` workflow runs automatically:**
   - Runs the full test suite (Python 3.12 + 3.13)
   - Builds wheel and sdist
   - Publishes to PyPI via trusted publishing

7. **Verify** the release at https://pypi.org/p/dompower.

## Testing with TestPyPI

To publish to TestPyPI without creating a release, use the workflow dispatch:

```bash
gh workflow run publish.yml --field target=testpypi
```

## Versioning

Follow [semver](https://semver.org/):
- **Patch** (0.2.x): Bug fixes, test changes
- **Minor** (0.x.0): New features, new API methods
- **Major** (x.0.0): Breaking API changes

## Downstream

After publishing to PyPI, update `ha-dominion-energy` to require the new version in `manifest.json`.
