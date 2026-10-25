# Project
- Python tool for assisted Google Forms completion: Playwright reads pages; profile data and an AI provider propose answers; a person reviews and submits.
- The supported workflow is interactive Chromium. Never infer a successful submission from filled fields or a closed tab; only confirmation yields `RunStatus.CONFIRMED`.

# Repository Map
- `main.py`: single-form CLI, URL validation, persistent browser context, form parsing/filling, AI batching, manual review, and confirmation detection. Start here for form behavior or provider changes.
- `bulk_runner.py`: sequential multi-link CLI, SQLite run ledger (`form_runs.sqlite3`), legacy `processed_links.txt` import, status recovery, and resume decisions. Start here for queue/state changes.
- `discord_bridge.py`: standalone Discord listener that reports found Forms links; it does not queue or submit them. Start here for Discord behavior.
- `profile_mapping.py`: profile-to-label mapping used by Google Form autofill; keep personal values out of logs and documentation.
- `config.py` / `config_example.py`: `AIConfig` and `USER_PROFILE`; the example is a template, while `main.py` imports `config.py`. Keep profile and credentials out of logs/docs.
- `tests/test_core.py`: answer/provider/ledger logic; `tests/test_browser_flow.py`: local-HTML browser behavior. `test_gemini.py` is a manual provider probe, not part of pytest.
- `.github/workflows/tests.yml`: Python 3.12 CI with Chromium and required browser tests. `requirements.txt`, `requirements-dev.txt`, and `requirements-discord.txt` cover runtime, test, and optional Discord dependencies.
- `script-tampermoneky.txt`: independent legacy userscript that can auto-submit; it is not part of the Python manual-submit flow.

# Key Flows
- Single form: `main.main()` → `launch_context()` → `run_form()` → parse questions → profile/AI answers → validate/fill → human review/Submit → confirmation status.
- Bulk: `links.txt` → `bulk_runner` URL validation/SQLite ledger → `main.run_form()` per link → ledger status; the first non-confirmed result stops the run.
- Discord: message → URL regex → reply with manual instructions; no persistence or call into the bulk runner.

# Commands
- Fresh setup per README: copy `links_example.txt` to `links.txt`, `config_example.py` to `config.py`, and `.env.example` to `.env` only when each target is absent; create/activate `.venv`, then run `python -m pip install -r requirements.txt` and `python -m playwright install chromium`.
- Interactive run: `python bulk_runner.py`; inspect the ledger with `python bulk_runner.py --list-status`. After checking actual submission, resolve with `python bulk_runner.py --resolve-url URL --as confirmed` or `--as queued` only when unsent.
- Tests: install `requirements.txt`, `requirements-dev.txt`, then run `python -m pytest -q`; CI sets `REQUIRE_BROWSER_TESTS=1` after installing Chromium and compiles the Google Forms entry points.

# Conventions & Gotchas
- `HEADLESS=true` is rejected; login, unsupported inputs, email consent, corrections, and final Submit require human action. Browser tests may need OS sandbox permissions.
- `.env` is loaded with `override=True`; its values supersede process environment variables. `AI_PROVIDER` defaults to OpenRouter; Gemini is optional. Use the matching provider key without documenting its value.
- `USER_PROFILE` supplies prompt context and exact-label identity autofill. Choice answers use stable `oN` IDs; validate them before clicking Google Forms controls.
- Parser/filler targets Google Forms ARIA/DOM structure and is capped at 25 pages; check selectors and local-HTML browser tests when Forms markup changes.
- Bulk supports `accuracy` with `--repeat 1` only. `unknown` and `legacy_unverified` block retries until explicit resolution; interrupted active states (including `awaiting_submit`) recover to `unknown`.
- `--list-status` migrates legacy entries and recovers interrupted rows, so it writes the SQLite ledger; use isolated paths when testing CLI behavior.
- `test_gemini.py` is a manual provider probe that clears `USER_PROFILE` during its small validation request; it is not part of pytest.
- Preserve local `.env`, `user_data/`, SQLite data, and unrelated edits. Both `config.py` and `links.txt` are tracked despite matching ignore rules; check Git tracking before sharing changes.

# Engineering Rules
- Search the relevant source and tests before assuming behavior; surface correctness-critical ambiguity that repository evidence cannot resolve.
- For non-trivial work, state concise steps and verification criteria; reproduce bugs when practical and report checks actually run and their limits.
- Make the smallest correct change using existing patterns. Touch only task-owned files, avoid speculative features/refactors, and remove only artifacts your change made unused.

# Multi-Agent Routing
- Sol High owns task understanding, decomposition, integration, final reasoning, and reporting.
- Delegate routine exploration, path discovery, straightforward tracing, configuration/test inspection, commands, and verification to Luna Max.
- Parallelize independent scopes by module/file ownership; avoid duplicate scans or conflicting edits. Use a separate review/test worker when useful.
- Escalate to Terra High only for materially difficult cross-module, state, security, or contradictory findings; do not use it for routine scanning.
