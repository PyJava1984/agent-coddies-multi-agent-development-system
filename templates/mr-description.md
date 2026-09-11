## Summary

<!-- Two or three sentences: what this changes and why. Written for a reviewer
     who has not read the ticket. -->

Closes {TICKET} — {JIRA_URL}

## What changed

<!-- One bullet per meaningful change, grouped by area. Reference files where
     it helps the reviewer find their way. -->

- **Backend** — …
- **Frontend** — …
- **Database** — …
- **Config / infra** — …

## How it was tested

<!-- Real numbers from coddie-qa. If something was not tested, say so here. -->

| Suite | Result |
|---|---|
| Unit | {UNIT_PASSED} passed, {UNIT_FAILED} failed |
| Playwright | {PW_PASSED} passed, {PW_FAILED} failed |
| Selenium | {SEL_PASSED} passed, {SEL_FAILED} failed |

Acceptance criteria verified:

- [ ] AC1 — …
- [ ] AC2 — …

Not covered: <!-- be honest, or write "nothing" -->

## Risk and rollback

- **Risk:** <!-- what could break, and who would notice -->
- **Rollback:** <!-- revert the MR / feature flag / migration down -->
- **Migrations:** <!-- none, or: name + whether it is reversible -->
- **Config changes needed on deploy:** <!-- none, or list them -->

## Screenshots

<!-- UI changes only: before / after, or the approved design canvas link. -->

## Reviewer notes

<!-- Where to look first, decisions you want challenged, anything deliberately
     left for a follow-up ticket. -->
