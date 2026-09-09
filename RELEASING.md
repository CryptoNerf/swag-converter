# Releasing

Tagging is the whole process: `.github/workflows/release.yml` builds the
package, publishes it to PyPI and attaches the artifacts to the GitHub
release. Nothing is uploaded by hand, and no API token is stored in this
repository.

## One-time: let PyPI trust this workflow

**This is already done** — it was set up for v0.1.0 and needs no attention
unless the repository is renamed or moved. What follows is the record of what
was configured, and what to redo if it ever breaks.

PyPI verifies that an upload really came from this repository's release
workflow, which is why there is no token to leak. It has to be told once, and
only the project owner can do it.

1. Sign in at [pypi.org](https://pypi.org) (create an account if needed).
2. Go to **Your projects → Publishing**, or straight to
   <https://pypi.org/manage/account/publishing/>.
3. Under **Add a new pending publisher**, fill in exactly:

   | field | value |
   | --- | --- |
   | PyPI project name | `swag-converter` |
   | Owner | `CryptoNerf` |
   | Repository name | `swag-converter` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

4. In this repository, go to **Settings → Secrets and variables → Actions →
   Variables** and add a *repository* variable named `PUBLISH_TO_PYPI` with the
   value `true` — the name is `PUBLISH_TO_PYPI`, not `NAME`.

Step 4 doubles as a kill switch: with the variable set to anything else,
tagging still builds the package and attaches it to the GitHub release, it just
skips the PyPI upload.

The environment name in step 3 must match `environment: pypi` in the workflow,
and that environment exists under **Settings → Environments**. It carries no
protection rules; that is where you would add a required reviewer if you ever
want a human to approve uploads.

## Cutting a release

1. Land everything on `master` and make sure CI is green.
2. Bump `version` in `pyproject.toml`. The workflow refuses a tag that
   disagrees with it, so this cannot silently drift.
3. Tag and push:

   ```bash
   git tag -a v0.1.1 -m "swag-converter 0.1.1"
   git push origin v0.1.1
   ```

4. Watch it: `gh run watch $(gh run list -w Release -L1 --json databaseId --jq '.[0].databaseId')`

The workflow will:

- refuse the tag if it does not match `pyproject.toml`;
- build the wheel and the sdist;
- run `twine check --strict`, which is what catches a README that will not
  render on PyPI;
- install the built wheel into a clean virtualenv and convert an image with
  it, so a broken package cannot reach anyone;
- publish to PyPI (when `PUBLISH_TO_PYPI` is `true`);
- attach the wheel, the sdist and `SHA256SUMS.txt` to the GitHub release.

## Release notes

`softprops/action-gh-release` creates the release if the tag has none. Write
the notes for someone who landed on the release page knowing nothing about
the project: how to install it, the first command to run, and what the output
means. The v0.1.0 notes are the template.
