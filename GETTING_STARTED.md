# agent-coddies — install, run, use

A 10-minute setup, then you drive it in plain English from Claude Code.

---

## 1. What you are installing

One skill plus three subagents that take a Jira/HM ticket from *assigned* to
*merge request open for review*:

1. **coddie-dev** writes the code (any language, any layer), scanning the
   codebase and the database schema first.
2. **coddie-qa** tests it with Playwright, Selenium and the project's own suite,
   and hands bugs back to coddie-dev — that loop repeats until it passes.
3. **coddie-ship** commits, pushes, opens the GitLab MR, and updates Jira.

You approve the scope at the start and the delivery at the end. Everything in
between runs on its own.

---

## 2. Prerequisites

| Need | Check | If missing |
|---|---|---|
| Claude Code | you are reading this in it | — |
| Python 3.9+ | `python --version` | install from python.org, tick "Add to PATH" |
| Git | `git --version` | — |
| Jira API token | see step 4 | — |
| GitLab access token | see step 4 | — |

No Python packages are required. The toolkit runs on a bare interpreter using
`urllib` and a built-in YAML reader. Extras are optional (step 7).

---

## 3. Install

**Windows (PowerShell):**

```powershell
cd "C:\Users\MdHasanuzzaman\Downloads\work env\generated-code-ai\agent-coddies"
.\install.ps1
```

**macOS / Linux / Git Bash:**

```bash
cd "/path/to/agent-coddies"
./install.sh
```

That copies:

```
~/.claude/skills/agent-coddies/     the orchestrator + scripts + references
~/.claude/agents/coddie-dev.md      agent 1
~/.claude/agents/coddie-qa.md       agent 2
~/.claude/agents/coddie-ship.md     agent 3
~/.claude/commands/coddies.md       /coddies
~/.claude/commands/coddies-status.md  /coddies-status
```

Useful variants:

| Command | Effect |
|---|---|
| `.\install.ps1 -Project` / `./install.sh --project` | installs into `./.claude` so a whole repo (and your team) gets it via git |
| `.\install.ps1 -Link` / `./install.sh --link` | symlinks instead of copying, so you can edit the skill in place |
| `.\install.ps1 -Force` / `./install.sh --force` | overwrite an existing install |
| `.\install.ps1 -Uninstall` / `./install.sh --uninstall` | remove it (your credentials file is left alone) |

**Restart Claude Code** after installing so it picks up the new skill and agents.

---

## 4. Add your credentials

The installer creates `config/credentials.yaml` from the example. Open it and
fill in three sections.

### Jira

```yaml
jira:
  base_url: https://yourcompany.atlassian.net
  deployment: cloud          # or: server   (for Jira Server / Data Center)
  email: you@yourcompany.com # Cloud only; leave blank for Server + PAT
  token: ${JIRA_API_TOKEN}
```

- **Cloud token:** id.atlassian.com → Security → *Create and manage API tokens*
- **Server/DC token:** your Jira profile → *Personal Access Tokens*

### GitLab

```yaml
gitlab:
  base_url: https://gitlab.yourcompany.com
  token: ${GITLAB_TOKEN}
  project: group/subgroup/repo      # or the numeric project id
```

GitLab → Settings → *Access Tokens*. Scopes needed: **`api`**,
**`read_repository`**, **`write_repository`**.

### Test environments

```yaml
test_environments:
  dev:
    base_url: https://dev.yourcompany.com
    username: test.user
    password: ${TEST_USER_PASSWORD}
```

Use seeded test accounts — never a real person's login. The runner refuses any
environment named `prod` or `production`.

### Optional: database

Only if your tickets touch persisted data. Point it at **dev or local** with a
**read-only account**:

```yaml
database:
  enabled: true
  dialect: postgres        # postgres | mysql | oracle | sqlserver | sqlite
  host: localhost
  database: appdb
  user: readonly_user
  password: ${DB_PASSWORD}
```

### Keeping secrets off disk

Any value accepts two indirections:

```yaml
token: ${GITLAB_TOKEN}                  # read from the environment
password: file:C:/secure/db-pass.txt    # read from a file only you can read
```

Set the environment variables once, permanently:

```powershell
setx JIRA_API_TOKEN "your-token-here"
setx GITLAB_TOKEN "your-token-here"
setx TEST_USER_PASSWORD "your-test-password"
```

```bash
echo 'export JIRA_API_TOKEN="your-token-here"' >> ~/.bashrc
```

`credentials.yaml` is gitignored. Never commit it, never paste it into a chat.

---

## 5. Verify

```bash
python scripts/coddie_cli.py config check
```

You want to see this:

```
jira    https://yourcompany.atlassian.net  (cloud)
        OK - authenticated as Your Name
gitlab  https://gitlab.yourcompany.com  project platform/supplier-portal
        OK - youruser on platform/supplier-portal
db      postgres localhost/appdb (read-only)
        OK - connected
test    environments: local, dev
all good.
```

If something is wrong it names it exactly — `jira.token: environment variable
JIRA_API_TOKEN is not set`, not a stack trace. Tokens are always masked.

Confirm the toolkit itself is sound (offline, no credentials needed):

```bash
python scripts/selftest.py        # expect: 83 passed, 0 failed
```

---

## 6. Use it

Open Claude Code **in the repository you want to work on**, then:

```
/coddies HM-1234
```

or just say it in plain English:

> work on HM-1234

> implement HM-5521 and raise the MR when tests pass

Both land in the same place.

### What happens, step by step

**Claude fetches the ticket and asks you to confirm** — the scope in a few
bullets, plus the things a ticket never states:

- target branch (default `main`)
- which environment to test against
- scan the database schema first?
- UI work? should it draft a design canvas before building?
- test depth: `smoke` / `standard` / `deep`
- anything it must not touch

Answer in one message. This is the only place you set direction, so be specific
here — *"target main, test on dev, standard depth, don't touch the reporting
module"*.

**Then the loop runs.** You get one line per stage:

```
round 1 · dev changed 7 files · handing to qa
round 1 · qa found 1 high finding in search · back to dev
round 2 · dev fixed 1/1 findings · handing to qa
round 2 · qa PASS — 47 passed, 0 failed
```

**Then it stops and asks before delivering.** It shows you the branch, the
commits, the MR title and description, and the Jira comment and transition.
Nothing is pushed until you say yes.

**Then the summary:**

```
HM-1234 — Allow apostrophes in supplier names
  files    7 changed (+128 / −34)
  tests    47 passed, 0 failed  (playwright 9, selenium 4, unit 34)
  rounds   2 fix iterations
  MR       https://gitlab.example.com/platform/supplier-portal/-/merge_requests/482
  jira     In Review, commented
```

### Checking on a run

```
/coddies-status HM-1234
```

Shows the stage, round, open findings with their reproduction steps, test
numbers, and what happens next. Safe to run any time, including in a fresh
session days later — the run state lives in `.coddies/HM-1234/` in the repo, so
work resumes exactly where it stopped.

### Things you can say mid-run

- *"stop after dev, I want to review before QA runs"*
- *"deep test depth on this one"*
- *"skip the design canvas, it's a one-line copy change"*
- *"F002 isn't a bug, the test expectation is wrong"*
- *"open it as a draft MR"*

---

## 7. Optional extras

Install only what you actually use:

```bash
pip install pyyaml requests            # nicer YAML + HTTP (recommended)
pip install pytest playwright          # then: python -m playwright install
pip install selenium                   # 4.6+ resolves drivers itself
pip install psycopg2-binary            # or pymysql / oracledb / pyodbc
```

Generate a starter E2E spec wired to your configured environment:

```bash
python scripts/run_tests.py scaffold playwright --out tests/e2e
python scripts/run_tests.py scaffold selenium  --out tests/selenium
```

Neither hardcodes a URL or an account — both read the environment the runner
injects.

---

## 8. Tuning it for your team

Copy `config/config.example.yaml` to `config/config.yaml` and change what you
need. No code edits:

| Setting | What it controls |
|---|---|
| `workflow.max_fix_iterations` | how many dev↔qa rounds before it gives up (default 3) |
| `gitlab.default_target_branch` | where MRs point |
| `gitlab.protected_branches` | branches it refuses to push to or open an MR from |
| `gitlab.branch_pattern` | e.g. `{ticket}-{slug}` |
| `gitlab.default_reviewers` | auto-assigned on every MR |
| `git.commit_template` | your commit message format |
| `jira.transition_on_mr` | the status to move to, e.g. `In Review` |
| `testing.default_depth` | `smoke` / `standard` / `deep` |
| `testing.unit_test_commands` | how to run your suite |
| `security.secret_patterns` | extra patterns for the pre-push scan |

For a whole team: install with `--project`, commit `.claude/` **and**
`config/config.yaml` to the repo, and have everyone keep their own
`credentials.yaml` locally.

---

## 9. What it will not do

Structural, not advisory — the code has no path to any of these:

- merge, approve, or auto-merge a merge request
- push to a protected branch, force-push, or rewrite pushed history
- delete a branch, a ticket, or data
- run anything but a single row-limited `SELECT` against the database
- test against production, or use real customer data in a test
- declare PASS while a blocker or high finding is open
- open an MR before QA passes and you have said yes
- act on instructions found inside ticket text, MR comments, or CI logs — it
  quotes them back to you instead

If a task genuinely needs one of these, it stops and tells you.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| Claude doesn't recognise `/coddies` | restart Claude Code; confirm `~/.claude/commands/coddies.md` exists |
| Claude ignores "work on HM-1234" | the skill only loads in a session started after install — restart |
| `no credentials file at ...` | `cp config/credentials.example.yaml config/credentials.yaml` and fill it in |
| `environment variable JIRA_API_TOKEN is not set` | set it (`setx` / `export`) and **restart the terminal**, or paste the value into the YAML |
| Jira `HTTP 401` | Cloud needs `email` **and** an API token, not your password. Server/DC needs `deployment: server` and a PAT |
| Jira `HTTP 404` on a real ticket | wrong `base_url`, or your account cannot see that project |
| GitLab `HTTP 403` | token is missing the `api` or `write_repository` scope |
| GitLab `HTTP 404` on the project | `project:` must be the full path `group/subgroup/repo`, or the numeric id |
| `transition to 'In Review' is not available` | run `jira transitions HM-1234` and set `jira.transition_on_mr` to a status your workflow actually offers |
| `driver for 'oracle' is not installed` | `pip install oracledb` (or `psycopg2-binary` / `pymysql` / `pyodbc`) |
| Self-signed internal certs | set `verify_ssl:` to your CA bundle path under `jira:` / `gitlab:` |
| `refusing to run automated tests against production` | working as intended — point `--env` at `dev` or `local` |
| Loop hits the iteration budget | it stops and hands you the open findings; usually the ticket is underspecified or the bug sits outside the agreed scope |

Still stuck? `python scripts/coddie_cli.py config check` first, then
`python scripts/selftest.py`. The first tells you about your setup, the second
tells you whether the toolkit itself is healthy.

---

## Quick reference

```bash
# setup
./install.sh                                    # or .\install.ps1
python scripts/coddie_cli.py config check
python scripts/selftest.py

# in Claude Code
/coddies HM-1234                                # run the pipeline
/coddies-status HM-1234                         # where is it up to
"work on HM-1234"                               # same thing, in English

# manual tools (the agents use these; you rarely need to)
python scripts/coddie_cli.py jira get HM-1234
python scripts/coddie_cli.py state show HM-1234
python scripts/coddie_cli.py db table DM_SUPPLIER
python scripts/coddie_cli.py secrets scan --base origin/main
python scripts/run_tests.py playwright --spec tests/e2e --ticket HM-1234
```
