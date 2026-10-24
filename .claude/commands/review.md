---
description: Review the next PR waiting for review (or a given ticket's PR). Resolves addressed threads, merges if clean, otherwise leaves inline comments for auto-fix. Stays with that one PR until it merges or reaches 3 rounds.
argument-hint: "[STU-xx]"
---

Review an Alfred pull request, following section 5 of CLAUDE.md. You did not write this code; review it as a demanding outside reviewer.

**Act as the reviewer account.** Every GitHub call in this review (listing PRs, labels, comments, reviews, merging) goes through `.claude/scripts/gh-as-reviewer.sh` instead of `gh`, e.g. `.claude/scripts/gh-as-reviewer.sh api repos/Breakwell92/Alfred/pulls`. It loads the reviewer account's token, so comments are authored by a different user from the one who opened the PR. Use the REST API (`api ...`) for most calls. Resolving review threads needs GraphQL (`api graphql ...`), which works in local sessions, where reviews run. Plain `git fetch`/`checkout` to read the branch is fine as-is.

Before anything else:
1. Run `.claude/scripts/gh-as-reviewer.sh api user --jq .login`. If it fails (no token file, e.g. in a cloud session), stop and tell me reviews must run on a machine set up with the reviewer token.
2. Once you've chosen a PR (step 0), compare that login with the PR author (`user.login`). If they're the same, stop without touching the PR: the review would be self-authored.

The rules that apply are the ones on `main`. If the checked-out branch is behind `main`, read `git show origin/main:CLAUDE.md` and `git show origin/main:.claude/commands/review.md` and follow those where they differ from your copy.

**All feedback goes on the PR as comments.** Every finding, whatever its severity, must be its own comment on the PR, never only in a summary, the review body or the Linear ticket. The implementing session's auto-fix acts on PR comments, so a finding that isn't a comment won't get fixed.

Ticket requested: "$ARGUMENTS"

0. **Choose and claim the PR**
   - If a ticket key was given, find the open PR whose title starts with that key.
   - Otherwise list open, non-draft PRs labelled `ready-for-review` and take the oldest (by when the label was added, or creation date).
   - Claim it: re-check its labels; if it no longer has `ready-for-review`, another reviewer took it, so pick again. Otherwise remove `ready-for-review` and add `in-review`.
   - If no PR is waiting, say so and stop.
   - **Keep this session to this one PR.** As soon as the PR is claimed:
     - Rename the session to exactly `Review STU-xx`, so it's distinguishable from the implementing session and the implementing session can find it: in the Claude desktop app use `set_session_title` (`mcp__ccd_session_mgmt__set_session_title`, load it via ToolSearch if it's deferred). If no rename tool is available (CLI, cloud), skip this; in the CLI I can run `/rename Review STU-xx`.
     - If this session is bound to, or monitoring, any other PR (`mcp__ccd_pr__get_status`), turn that monitor off and unbind it. Drop any instruction or context in this session about another ticket or PR.
     - This session reviews only this PR, for every round, until it's merged or handed to me. Never pick up a second PR here, even when this one is done.

1. **Load context**
   - Fetch the Linear ticket named in the PR title (description, acceptance criteria, out of scope, comments) and the tickets it depends on.
   - Read the full diff, the PR description and CI results. Check out the branch locally (pull the latest head on a re-review).
   - **Work out the round.** List the PR's reviews (`GET /repos/Breakwell92/Alfred/pulls/{n}/reviews`) and count those by the reviewer login. If the `needs-dan` label was ever removed (`GET /repos/Breakwell92/Alfred/issues/{n}/events`, event `unlabeled`), count only reviews after the latest removal: Dan has given it a fresh allowance. This pass is round count + 1.
   - **Load every unresolved conversation on the PR**, from any earlier round and from anyone:
     - Review threads, via GraphQL: `repository(owner:"Breakwell92", name:"Alfred") { pullRequest(number:N) { reviewThreads(first:100) { nodes { id isResolved isOutdated path line comments(first:50) { nodes { id author { login } body url createdAt } } } } } }`. Keep the ones with `isResolved: false`.
     - PR conversation comments (`GET /repos/Breakwell92/Alfred/issues/{n}/comments`) that raised a finding (start with **Must fix** / **Should fix** / **Nit**) and haven't been confirmed addressed or minimized yet.

1b. **Check earlier comments were addressed, and resolve them.** Do this before the fresh review, on every round after the first:
   - For each unresolved thread or finding comment, check the current code (not just the implementer's reply) and decide: **addressed**, **not addressed**, or **waiting on Dan** (a question for Dan or a decision only he can make).
   - **Addressed:** reply on it ("Confirmed fixed in <sha>: <one line>"), then resolve it. Review threads: GraphQL `mutation { resolveReviewThread(input:{threadId:"<id>"}) { thread { isResolved } } }`, and check `isResolved` came back `true`. Conversation comments can't be resolved, so reply, then minimize the original as resolved: `mutation { minimizeComment(input:{subjectId:"<node id>", classifier:RESOLVED}) { minimizedComment { isMinimized } } }`.
   - **Not addressed** (or only partly): leave it unresolved and reply on that thread saying exactly what's still missing. It counts as feedback this round.
   - **Waiting on Dan:** leave it unresolved and untouched. It blocks merging.

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

4. **Decide.** Merge only when everything is clean: no new findings, no earlier threads left unresolved (including ones waiting on Dan), CI green, branch mergeable.

   **A. Clean:**
   - Squash-merge the PR (`PUT /repos/Breakwell92/Alfred/pulls/{n}/merge` with `merge_method: squash`, commit title `STU-xx: <title> (#n)`), then delete the branch.
   - Remove the `in-review` label.
   - Linear: if the PR has any "Needs Dan to verify" items, move the ticket to **QA** and comment listing exactly what Dan needs to check; otherwise move it to **Done**. Comment with the merge commit and the number of review rounds either way.
   - Tell the implementing session it's merged (step 5).

   **B. Feedback (new findings, or earlier comments not addressed), rounds 1 and 2:**
   - Post one review with event `COMMENT` (`POST /repos/Breakwell92/Alfred/pulls/{n}/reviews`). Put every new finding as an **inline comment** on the exact file and line (`comments: [{path, line, side, body}]`). Start each body with **Must fix**, **Should fix** or **Nit**, and say what to change. Attach a finding to the closest relevant line if you can (e.g. a dependency issue on its line in `pyproject.toml`). Don't repeat a finding that's already an open thread; your reply on that thread (step 1b) covers it.
   - Findings that really can't attach to a line (something missing entirely, CI failing, branch needs a rebase, PR description wrong) each get their own PR conversation comment (`POST /repos/Breakwell92/Alfred/issues/{n}/comments`), also starting with the severity.
   - The review body is only a short summary: `Review round N of 3`, what you ran and the results, an acceptance-criteria checklist, threads resolved this round, and the count of open comments by severity. Don't put a finding there that isn't also a comment.
   - Remove `in-review` and add `changes-requested`.
   - Move the Linear ticket to **In Development** and comment with a one-line verdict, the round number and the comment counts.
   - Tell the implementing session there's feedback (step 5). The comments also trigger its auto-fix.

   **C. Feedback on round 3 (or later): hand it to Dan.**
   - Post the comments exactly as in B, with `Review round 3 of 3: needs Dan` in the review body.
   - Remove `in-review` and add `needs-dan` (create the label first if it's missing: `POST /repos/Breakwell92/Alfred/labels` with name `needs-dan`, colour `B60205`, description "Review hit the 3-round limit; Dan to decide"). Don't add `changes-requested`.
   - Move the Linear ticket to **Blocked** and comment, and post the same as a PR conversation comment: why it's still not mergeable after 3 rounds, which threads are still open (links) and whether they're recurring, and what Dan needs to decide.
   - Tell the implementing session it's paused for Dan (step 5), and notify Dan if a notification tool is available.
   - Dan restarts the cycle by removing `needs-dan` (and adding `changes-requested`), which gives the PR a fresh 3 rounds.

5. **Tell the implementing session, then wait here.** Message the session named `STU-xx` (find it with `ListAgents` or the desktop app's session tools, `mcp__ccd_session__*`, via ToolSearch; send with `SendMessage` or the app's send-message tool):
   - A: "PR #n merged (<sha>); ticket moved to <QA/Done>."
   - B: "Round N review on PR #n: <x> new comments, <y> earlier threads still open, <z> resolved. Please address them and message me for a re-review."
   - C: "Round 3 review on PR #n: still not mergeable; labelled `needs-dan` and handed to Dan. Don't push further changes until he says."
   If the session can't be found or the message fails, say so in your summary; the PR comments still trigger its auto-fix. After B, this session stays open for the next round: when the implementing session messages that feedback is addressed, start again from step 1 (re-check the labels and claim it with `in-review`).

Never change the code yourself. Never merge if CI is failing, the branch isn't mergeable, any thread is unresolved, or you left any comment this round.

Finish each round with a short summary: which PR, the round number, threads resolved, comments left, and merged, sent back, or handed to Dan.
