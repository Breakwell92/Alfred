---
description: Review the next PR waiting for review (or a given ticket's PR); merge it if clean, otherwise leave inline comments for auto-fix
argument-hint: "[STU-xx]"
---

Review an Alfred pull request, following section 5 of CLAUDE.md. You did not write this code; review it as a demanding outside reviewer. Use the GitHub REST API (`gh api repos/Breakwell92/Alfred/...`), since GraphQL isn't available in cloud sessions.

The rules that apply are the ones on `main`. If the checked-out branch is behind `main`, read `git show origin/main:CLAUDE.md` and `git show origin/main:.claude/commands/review.md` and follow those where they differ from your copy.

**All feedback goes on the PR as comments.** Every finding, whatever its severity, must be its own comment on the PR, never only in a summary, the review body or the Linear ticket. The implementing session's auto-fix acts on PR comments, so a finding that isn't a comment won't get fixed.

Ticket requested: "$ARGUMENTS"

0. **Choose and claim the PR**
   - If a ticket key was given, find the open PR whose title starts with that key.
   - Otherwise list open, non-draft PRs labelled `ready-for-review` and take the oldest (by when the label was added, or creation date).
   - Claim it: re-check its labels; if it no longer has `ready-for-review`, another reviewer took it, so pick again. Otherwise remove `ready-for-review` and add `in-review`.
   - If no PR is waiting, say so and stop.

1. **Load context**
   - Fetch the Linear ticket named in the PR title (description, acceptance criteria, out of scope, comments) and the tickets it depends on.
   - Read the full diff, the PR description, existing review threads (from earlier rounds) and CI results. Check out the branch locally.
   - If this is a re-review, check each earlier comment was addressed as well as the new changes.

2. **Verify yourself**
   - Run lint, tests and any evals the ticket names; don't rely on the PR's claims.
   - Check the branch is up to date with `main` and mergeable.

3. **Check**
   - **Acceptance criteria:** each met, with evidence? Anything marked "Needs Dan to verify" genuinely needs hardware or a human?
   - **Scope:** nothing out of scope added; nothing required missing.
   - **Correctness:** logic errors, edge cases, error handling, race conditions, timezone handling (Europe/London, BST/GMT).
   - **Safety:** confirmation tiers, privacy flags, budget checks and the kill switch respected; no secrets committed.
   - **Tests:** meaningful, not just passing; failure paths covered.
   - **Shared memory:** CLAUDE.md and docs updated where the change affects them.
   - **Consistency:** follows existing patterns and CLAUDE.md conventions.

4. **Decide**

   **A. No feedback at all** (nothing to comment on, CI green, branch mergeable):
   - Squash-merge the PR (`PUT /repos/Breakwell92/Alfred/pulls/{n}/merge` with `merge_method: squash`, commit title `STU-xx: <title> (#n)`), then delete the branch.
   - Remove the `in-review` label.
   - Linear: if the PR has any "Needs Dan to verify" items, move the ticket to **QA** and comment listing exactly what Dan needs to check; otherwise move it to **Done**. Comment with the merge commit either way.

   **B. Any feedback** (even minor):
   - Post one review with event `COMMENT` (`POST /repos/Breakwell92/Alfred/pulls/{n}/reviews`). Put every finding as an **inline comment** on the exact file and line (`comments: [{path, line, side, body}]`). Start each body with **Must fix**, **Should fix** or **Nit**, and say what to change. Attach a finding to the closest relevant line if you can (e.g. a dependency issue on its line in `pyproject.toml`).
   - Findings that really can't attach to a line (something missing entirely, CI failing, branch needs a rebase, PR description wrong) each get their own PR conversation comment (`POST /repos/Breakwell92/Alfred/issues/{n}/comments`), also starting with the severity.
   - The review body is only a short summary: what you ran and the results, an acceptance-criteria checklist, and the count of comments by severity. Don't put a finding there that isn't also a comment.
   - Remove `in-review` and add `changes-requested`.
   - Move the Linear ticket to **In Development** and comment with a one-line verdict and the number of comments by severity.
   - The inline comments trigger auto-fix on the implementing session, which addresses them and re-labels the PR `ready-for-review` when done.

Never change the code yourself. Never merge if CI is failing, the branch isn't mergeable, or you left any comment.

Finish with a short summary: which PR, merged or not, and the comments left.
