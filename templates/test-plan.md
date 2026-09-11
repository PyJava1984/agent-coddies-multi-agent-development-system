# Test plan — {TICKET}

**Depth:** {DEPTH}  ·  **Environment:** {ENV} ({BASE_URL})  ·  **Round:** {ROUND}

## Acceptance criteria

| # | Criterion | Type | How it is checked | Expected | Result |
|---|-----------|------|-------------------|----------|--------|
| 1 |  | e2e / api / unit / manual |  |  | ☐ |
| 2 |  |  |  |  | ☐ |

## Regression — code paths this change touches

| Area | Why it is at risk | Check | Result |
|------|-------------------|-------|--------|
|  |  |  | ☐ |

## Negative and boundary cases

| Case | Input | Expected | Result |
|------|-------|----------|--------|
| Empty required field |  | validation error, no write | ☐ |
| Max length |  |  | ☐ |
| Unauthorised user |  | 403, no data leak | ☐ |
| Special characters (`'`, `"`, `<`, unicode) |  | stored and rendered intact | ☐ |
| Concurrent edit |  |  | ☐ |

## Risk areas flagged by coddie-dev

| Risk | Check | Result |
|------|-------|--------|
|  |  | ☐ |

## Not covered

<!-- Anything the plan deliberately or unavoidably leaves out, and why.
     This section goes into the verdict note verbatim. -->
