# Definition of done

1. `ruff check .` — clean (this is exactly the CI lint gate; do not run repo-wide `ruff format`).
2. `pytest -q` — green. Add or update tests for behavior changes rather than relying on manual checks.
3. Only if the user explicitly asks: `pytest -m integration`.
4. Any new runtime import declared in `[project].dependencies` (clean-install CI imports
   `tradingagents` and `cli.main`).
5. User-visible change: add a `CHANGELOG.md` entry (Keep a Changelog; breaking changes under
   "Upgrading from"). Touch `__version__` only for an actual release, and a release tag must be `v<version>`.
6. Touching dated data paths: make sure the look-ahead tests still cover it
   (`test_*_lookahead.py`, `test_undated_tools_as_of.py`, `test_date_boundaries.py`, `test_memory_pointintime.py`).
7. New report-producing agent: add to `REPORT_AGENTS` in `tests/test_i18n_coverage.py` and call
   `get_language_instruction()`.
