# Assignment 3 · Predictions

Written before the first evaluation run under `FLAKY_WRITES`. Both arms run the six
evaluation issues twice each (12 runs per arm) with `gpt-4o-mini`. The tracker
times out about 6 write attempts in 10: about 5 of those after the write
committed, about 1 before it reached the tracker.

## Duplicate writes left in the tracker

| | Your harness | Control (`KEY_MODE=uuid VERIFY=off`) |
|---|---|---|
| Runs with a duplicate comment | **0 of 12** | **about 6 of 12** |
| Runs with a duplicate PR | **0 of 12** | **0 of 12** |

- **Harness, comments: 0.** The key is derived from the run, the plan step and
  the arguments, so a retry sends the same key. A timed-out comment is read back
  by that key before any retry, and the tracker replays a key it has seen. All 12
  development runs under `FLAKY_WRITES` left one comment each.
- **Harness, PRs: 0.** The check before every attempt finds an open PR on
  `repomind/issue-<n>`, and the derived key is a second guard behind it.
- **Control, comments: about 6 of 12.** The first comment attempt commits and
  times out about half the time. With no read-back and a fresh key, the next
  attempt's check finds nothing and posts again, and that attempt commits about
  9 times in 10. So about 0.5 × 0.9 ≈ 45 to 50% of runs end with a duplicate.
  Some runs may hold three comments, when all three attempts time out (about
  0.6³ ≈ 22%).
- **Control, PRs: 0.** The branch is a key the PR has by nature. The control
  keeps the check before every attempt, which finds its own PR by branch whatever
  key opened it.

## Escalations

- **Harness: 0 or 1 of 12.** A write escalates only if all three attempts time
  out before committing (about 0.1³ per write), or if a read-back fails, which
  `READBACK_FAIL` is off for here.
- **Control: about 2 to 3 of 12.** With `VERIFY=off`, a comment whose three
  attempts all time out is never confirmed. Its check uses a new key every time,
  so it never finds the comment it wrote, and the run ends escalated after the
  third attempt.

## The tool the model will misuse most

**`code_search`.** It is the one tool whose arguments the model must invent from
the issue's text. In the development runs, the reviser already wrote placeholders
such as `"<relevant term from issue>"` as a literal query, and `code_search`
answered with a well-formed `count: 0` that nothing flagged. That is "right tool,
wrong arguments" returning a well-formed result that is wrong.
