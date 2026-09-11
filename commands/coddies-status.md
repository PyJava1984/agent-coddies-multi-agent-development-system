---
description: Show the agent-coddies run ledger for a ticket - stage, round, findings, test runs, delivery.
argument-hint: <TICKET-KEY>
---

Show the agent-coddies run state for: $ARGUMENTS

```bash
python "<skill_dir>/scripts/coddie_cli.py" state show $ARGUMENTS
```

Then summarise in a few lines: which stage the run is in, how many findings are
open and at what severity, the latest test numbers, and what the next action is
(dispatch coddie-dev, dispatch coddie-qa, ask the user to approve delivery, or
report a blocked run). If no ledger exists, say so and offer to start one.
