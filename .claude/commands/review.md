---
description: Review a ticket's PR in a fresh session against its acceptance criteria
argument-hint: STU-xx
---

Review the pull request for Linear ticket **$ARGUMENTS**. You did not write this code; review it as a demanding outside reviewer.

1. Fetch $ARGUMENTS from Linear (description, acceptance criteria, out of scope, comments) and the tickets it depends on.
2. Find the PR for this ticket and read the full diff, the PR description and CI results. Check out the branch locally.
3. Run lint, tests and any evals the ticket names yourself; don't rely on the PR's claims.
4. Check:
   - **Acceptance criteria:** each one met, with evidence? Anything marked "Needs Dan to verify" genuinely needs hardware or a human?
   - **Scope:** nothing out of scope added; nothing required missing.
   - **Correctness:** logic errors, edge cases, error handling, race conditions, timezone handling (Europe/London, BST/GMT).
   - **Safety:** confirmation tiers, privacy flags, budget checks and the kill switch respected; no secrets committed.
   - **Tests:** meaningful, not just passing; failure paths covered.
   - **Shared memory:** CLAUDE.md and docs updated where the change affects them.
   - **Consistency:** follows existing patterns and the conventions in CLAUDE.md.
5. Post the review on the PR as comments, grouped as **Must fix**, **Should fix** and **Nits**, with file/line references.
6. Comment on the Linear ticket with a one-paragraph verdict: ready to merge, or the must-fix items.

Do not change the code unless I ask you to. Do not merge.
