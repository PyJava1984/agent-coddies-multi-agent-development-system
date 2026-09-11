# Testing

coddie-qa owns this file. Both Playwright and Selenium are first class — use the
one the repository already standardises on, and add the other when the ticket or
the depth level calls for a cross-check.

## The runner wrapper

`scripts/run_tests.py` exists so no spec ever hardcodes a URL or an account. It
resolves `credentials.yaml → test_environments.<env>` and exports:

| Variable | From |
|---|---|
| `CODDIE_BASE_URL`, `BASE_URL`, `PLAYWRIGHT_BASE_URL` | `base_url` |
| `CODDIE_USERNAME` | `username` |
| `CODDIE_PASSWORD` | `password` |
| `CODDIE_ENV` | the environment key |
| `CODDIE_SELENIUM_BROWSER` / `_HEADLESS` / `_REMOTE_URL` | `config.yaml → testing.selenium` |

It refuses outright to run against an environment named `prod` or `production`.

```bash
python scripts/run_tests.py env --show
python scripts/run_tests.py unit --ticket HM-1234
python scripts/run_tests.py playwright --spec tests/e2e --browser chromium --ticket HM-1234
python scripts/run_tests.py selenium --spec tests/selenium --browser chrome --ticket HM-1234
python scripts/run_tests.py scaffold playwright --out tests/e2e
```

`--ticket` writes the parsed pass/fail/skip counts into the run ledger, so the MR
description and the Jira comment quote real numbers rather than your recollection.

## First run

```bash
python scripts/run_tests.py playwright --install     # downloads browsers
pip install selenium pytest                          # Selenium 4.6+ resolves drivers itself
```

## Depth levels

| Depth | Covers |
|---|---|
| `smoke` | happy path per acceptance criterion, one browser |
| `standard` (default) | criteria + negatives + regression on touched paths, two browsers |
| `deep` | + boundaries, a11y, console/network errors, a Playwright↔Selenium cross-check of the critical flow |

Depth controls breadth. It never licenses reporting an untested criterion as passing.

## Writing tests that hold up

- Select by **role, label, or `data-testid`**. A CSS chain like `div > div:nth-child(3) > span`
  is a future false failure.
- **Never sleep.** Wait for a condition: a visible element, a network response, a
  URL change. The Selenium template sets `implicitly_wait(0)` on purpose — implicit
  and explicit waits interact badly and hide real timing bugs.
- **Independent tests.** Each creates its own data with a unique key
  (`coddie-${Date.now()}`) and cleans up. The suite must pass in any order and in parallel.
- **Assert the observable outcome**, not the implementation. "A success banner
  appears and the row is in the list", not "this internal function was called".
- **One reason to fail per test.** A test that checks five things tells you little
  when it goes red.

## Flakes

Re-run every failure **twice**.

- Fails all three times → a real finding at its true severity.
- Passes on retry → a **flake finding** at `low`, filed separately, never quietly ignored.

Never mark a suite green by adding a retry, extending a timeout, or deleting the test.

## Environments and data

- Local and dev only. `run_tests.py` blocks production; also do not point a test at
  a shared environment someone is demoing on.
- Seeded test accounts from `test_environments` only — never a real user's login,
  never real customer records.
- Destructive scenarios (bulk delete, migration rollback) run against a local
  database you can recreate, or they do not run.

## Recording the result

```bash
python scripts/coddie_cli.py state tests HM-1234 --runner playwright --passed 9 --failed 1 \
  --command "npx playwright test tests/e2e" --report .coddies/HM-1234/artifacts/playwright-report.json
```

`run_tests.py --ticket` does this for you. Record it by hand only when you ran the
suite some other way.
