# CI workflow

`github-actions-ci.yml` belongs at `.github/workflows/ci.yml`. It lives here only
because the session that wrote it could not push to `.github/workflows/` — that
path needs a token with the `workflow` OAuth scope, which neither `git push` nor
the GitHub contents API had.

To enable CI:

    git mv tools/ci/github-actions-ci.yml .github/workflows/ci.yml
    git rm tools/ci/README.md
    git commit -m "Enable CI"

## What it runs

* **offline** — every `tests/test_*.py`, then builds the apworld and uploads it
  as a build artifact. Seconds; no Archipelago needed.
* **apworld** — checks out the Archipelago release named by `MIN_AP_VERSION` in
  `tools/build_apworld.py`, installs the built apworld into `custom_worlds/`,
  and runs `tools/ci_apworld_check.py` against it.

The second job is the one that matters. A bare `visibility = 0` shipped to
players and broke the Launcher's "Generate Template Options" for *every*
installed game, with no traceback the player could see (#20). Nothing in the
offline suite could have caught it, because those tests deliberately never
import Archipelago.

`tools/ci_apworld_check.py` is runnable by hand against any AP checkout:

    python -m tools.ci_apworld_check ../Archipelago
