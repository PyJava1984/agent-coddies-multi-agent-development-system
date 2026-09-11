# Workflow contract

The three agents never talk to each other directly. They talk through the
**run ledger** — `.coddies/<TICKET>/state.json` — and through the orchestrator's
prompt. That is what makes the loop resumable: if a session dies, the next one
reads the ledger and carries on.

## Stages

```
intake ──► dev ──► qa ──┬─► ship ──► done
            ▲           │
            └───────────┘   (findings; round += 1)
```

`blocked` is a terminal stage any agent may set when it cannot proceed.

## state.json

```jsonc
{
  "ticket": "HM-1234",
  "summary": "Allow apostrophes in supplier names",
  "created_at": "2026-09-11T10:04:00Z",
  "updated_at": "2026-09-11T11:22:00Z",
  "stage": "qa",              // intake | dev | qa | ship | done | blocked
  "round": 2,                 // increments each time qa hands back to dev
  "verdict": "FAIL",          // null | PASS | FAIL
  "verdict_note": "",
  "branch": "HM-1234-supplier-apostrophe",
  "scope": "agreed scope, verbatim",
  "findings": [
    {
      "id": "F001", "at": "...", "round": 1,
      "severity": "high",       // blocker | high | medium | low
      "status": "open",         // open | fixed | rejected
      "title": "Save fails when supplier name contains an apostrophe",
      "where": "src/api/supplier.controller.ts:88",
      "repro": "npx playwright test tests/e2e/supplier.spec.ts -g apostrophe",
      "expected": "201 created",
      "actual": "500 SQLGrammarException",
      "artifact": ".coddies/HM-1234/artifacts/trace-supplier.zip",
      "notes": [{"at": "...", "note": "root cause: string concat in the DAO"}]
    }
  ],
  "handoffs": [{"at": "...", "to": "qa", "by": "coddie-dev", "round": 1, "note": "..."}],
  "test_runs": [{"at": "...", "round": 1, "runner": "playwright", "passed": 9, "failed": 1}],
  "delivery": {"mr_url": "...", "mr_iid": "482", "commit": "abc123", "jira_status": "In Review"}
}
```

`events.jsonl` beside it is append-only — one JSON object per state change, for
audit and for reconstructing what happened.

## Rules the ledger enforces

- `state verdict --pass` **fails** while any `blocker` or `high` finding is open.
- `state ship` **fails** unless `verdict == "PASS"`.
- `state handoff --to dev` from stage `qa` increments `round` and clears the verdict.
- When `round` exceeds `workflow.max_fix_iterations`, `handoff` prints a warning.
  The orchestrator must then stop and hand the open findings to the user.

## Finding quality bar

A finding coddie-dev cannot act on is a wasted round. Every one needs:

- **where** — file:line, endpoint, or screen
- **repro** — a command or numbered steps that fail deterministically
- **expected** vs **actual** — observable, not interpreted
- **artifact** — trace, screenshot, or log excerpt when the failure is visual or flaky

Severity:

| | Meaning |
|---|---|
| `blocker` | the feature does not work, or data is corrupted / lost |
| `high` | an acceptance criterion is not met, or a regression in an existing flow |
| `medium` | wrong behaviour in an edge case, poor error handling, a11y violation |
| `low` | cosmetic, copy, or a flaky test |

`blocker` and `high` block the PASS verdict. `medium`/`low` can ship with the
user's agreement — the orchestrator asks, the agents do not decide.

## Handoff payloads

**Orchestrator → coddie-dev**

```
ticket:    HM-1234
skill_dir: <absolute path>
scope:     <agreed scope>
round:     2
findings:  <output of `state findings-md HM-1234`>   # fix rounds only
```

**Orchestrator → coddie-qa**

```
ticket:    HM-1234
skill_dir: <absolute path>
depth:     standard
env:       dev
handoff:   <coddie-dev's report, verbatim>
```

**Orchestrator → coddie-ship**

```
ticket:    HM-1234
skill_dir: <absolute path>
approved:  yes — user approved push + MR + Jira update at <time>
target:    main
```

## Resuming a run

```bash
python scripts/coddie_cli.py state show HM-1234
```

`stage` tells you which agent to dispatch next. Nothing else is needed.
