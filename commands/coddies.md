---
description: Run the agent-coddies pipeline on a Jira/HM ticket - build, test, then open a merge request.
argument-hint: <TICKET-KEY> [extra context]
---

Run the `agent-coddies` skill for ticket: $ARGUMENTS

Follow SKILL.md exactly:

1. `config check` first. Stop if credentials are missing.
2. Fetch the ticket, restate the scope in 2-4 bullets, and ask about the open
   concerns (target branch, environments, DB scan, UI design, test depth,
   anything off limits). Wait for the answer.
3. Open the run ledger, then drive coddie-dev -> coddie-qa, looping fix rounds
   until QA returns PASS or the iteration budget runs out.
4. Get explicit approval before coddie-ship pushes, opens the MR, or touches Jira.
5. Finish with the compact report.
