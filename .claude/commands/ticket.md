---
description: Work a Linear ticket end to end. With no argument, picks up the next free (Ready, unassigned) ticket.
argument-hint: "[STU-xx]"
---

Work a Linear ticket for the Alfred project, following the "Working on tickets" rules in CLAUDE.md (section 0 has the status/assignee/label table).

Ticket requested: "$ARGUMENTS"

0. **Choose and claim the ticket**
   - If a ticket key was given above, use it.
   - If it's empty, pick the next free ticket:
     1. List issues in Linear project **Alfred** (team Studio) with status **Ready** and **no assignee**. Ignore assigned tickets entirely, even if they are Ready.
     2. Keep only tickets whose blocked-by tickets are all **Done** or **QA**.
     3. Drop tickets that change the same core modules (router, connector registry, protocol, budget, `RequestContext`) as any ticket currently **In Development**, so parallel sessions don't collide.
     4. Prefer tickets that can be done in this environment: skip Android tickets if there's no Android SDK here, and skip tickets that need the GPU server if this isn't it (check with `nvidia-smi` / `docker` when relevant).
     5. Pick by priority (Urgent first), then milestone order (M1 before M2…), then lowest ticket number.
   - **Claim it before anything else:** re-fetch the ticket; if it now has an assignee, another session took it, so pick again. Otherwise assign it to `me` and move it to **In Development** in one update, then comment "Picked up by a Claude Code session". Tell me in one line which ticket you picked and why, and carry on.
   - If nothing qualifies, stop and show what's blocking progress (as `/next` would).
   - **Keep this session to this one ticket.** As soon as the ticket is chosen (including when resuming):
     - Rename the session to exactly the ticket key (e.g. `STU-48`): in the Claude desktop app use `set_session_title` (`mcp__ccd_session_mgmt__set_session_title`, load it via ToolSearch if it's deferred). If no rename tool is available (CLI, cloud), skip this; in the CLI I can run `/rename STU-xx`.
     - Call `mcp__ccd_pr__get_status` (load the `ccd_pr` tools via ToolSearch if they're deferred). If this session is bound to, or monitoring, any PR that isn't this ticket's, turn its monitor off (`set_monitor` with `auto_fix: false`, `address_comments: false` for that PR) and unbind it (the `ccd_pr` unbind tool if there is one; otherwise binding this ticket's PR in step 7 replaces it). Check `get_status` again: only this ticket's PR, or none yet, may be listed.
     - Work only in this ticket's branch and worktree. Drop any instruction, context or todo in this session that refers to another ticket; don't comment on, move or message anything for another ticket. New work you discover becomes a new Linear ticket (CLAUDE.md section 1), nothing more.

1. **Load context**
   - Fetch the ticket from Linear, including its relations and comments.
   - Fetch every ticket blocking it. If any blocker is not **Done** or **QA**, unassign yourself, move the ticket back to **Ready**, and tell me which blocker is outstanding.
   - If a PR already exists for this ticket, you are resuming: read the PR, its inline review comments and the Linear comments, then continue from there (see "Responding to review feedback" in CLAUDE.md) instead of starting over. Turn auto-fix on for that PR straight away and verify it (step 7b), since it's per session and the session that opened the PR may be gone.
   - Read CLAUDE.md and the docs it points to that are relevant to this ticket.

2. **Start**
   - Check out the ticket's Linear `gitBranchName` from up-to-date `main` (create it if needed). If I've asked for parallel work, use a dedicated worktree.

3. **Plan**
   - For anything beyond a trivial change, enter plan mode and present the plan: files to add/change, interfaces, tests, and how each acceptance criterion will be met or verified.
   - Flag anything in the ticket that is ambiguous, contradicts the code, or needs a decision from me. If it's too big for one PR, propose sub-issues and wait.

4. **Implement**
   - Stay within the ticket's scope and its "Out of scope" section.
   - Write tests alongside the code. Follow existing patterns.

5. **Verify**
   - Run lint, unit tests, and any evals the ticket names. Fix failures properly.
   - Go through each acceptance criterion: mark it met (with evidence), or "Needs Dan to verify" with exact steps if it needs hardware, credentials or a human.

6. **Record**
   - Update CLAUDE.md, `docs/decisions/`, `docs/protocol.md`, `docs/connectors/` or `infra/.env.example` wherever this change affects them.

7. **Hand off for review.** Do these in order; each depends on the one before.

   a. **Open the PR.** Rebase on `main`, commit (conventional commits, referencing the ticket key), push, and open a PR titled `STU-xx: <ticket title>` with summary, testing, acceptance-criteria checklist (including any "Needs Dan to verify" items) and follow-ups. Use the REST API (`gh api`) if GraphQL isn't available.

   b. **Turn on auto-fix and prove it's on.** I want it on for every `/ticket` PR, without asking, before any review starts:
      - Claude desktop app: call `mcp__ccd_pr__get_status`. If it doesn't report this PR, bind it with `mcp__ccd_pr__bind_pr` and the PR URL. Call `mcp__ccd_pr__set_monitor` with the PR URL, `auto_fix: true` and `address_comments: true`. Then call `get_status` again and **check the result shows this PR with auto-fix and address-comments on.** If it doesn't, repeat bind + set_monitor + check, up to 3 attempts. Don't tell me to run `/autofix-pr`: it isn't available in the desktop app.
      - Cloud session (claude.ai/code, mobile, `claude --cloud`): subscribe to the PR's CI checks and review comments now, and confirm the subscription is active.
      - **If auto-fix still isn't confirmed on, stop here.** Don't add `ready-for-review` and don't start a review: review comments would go unanswered. Move the ticket to **Blocked**, comment on the Linear ticket and the PR "Auto-fix could not be enabled for this session", and tell me plainly at the top of your summary.

   c. **Mark it ready.** Add the `ready-for-review` label. Comment on the Linear ticket with the PR link, a short summary, anything needing my verification, and follow-ups. Move it to **Code Review** (keep it assigned).

   d. **Start the review session yourself, every time.** Don't leave it to the scheduled `/review` task. In the Claude desktop app call `start_session` (`mcp__ccd_session__start_session`, load it via ToolSearch if it's deferred) with `initiation: "user_asked"`, `context: "fresh"`, `use_worktree: true`, title `Review STU-xx` and prompt `/review STU-xx`. Always local (the reviewer token lives on this machine), never `target: "cloud"`. If it can't be started (no tool, the app refuses), retry once, then say at the top of your summary: "Review NOT started: run `/review STU-xx`".

   If you had to stop before this step because something is missing, move the ticket to **Blocked** (keep it assigned) and comment with exactly what's needed instead.

8. **Review rounds: keep going until the PR is merged.** Feedback reaches this session through auto-fix (PR comments) and through a message from the `Review STU-xx` session. Each time:
   - Re-check the PR's labels first. If it's labelled `needs-dan`, the review has hit the round limit: don't change code, push or request another review. Reply on the PR that you're waiting for Dan, and stop until I say otherwise. If it's merged, go to step 9.
   - Otherwise follow "Responding to review feedback" in CLAUDE.md: fix every clear comment, run lint and tests, push, and reply on each thread saying what changed (don't resolve threads yourself; the reviewer does that once it has checked). Ambiguous or architectural comments: ask me on the thread and leave labels as they are.
   - Before asking for a re-review, check auto-fix is still on for this PR (`get_status`) and turn it back on if not (step 7b).
   - When every comment is addressed: swap `changes-requested` for `ready-for-review`, move the ticket to **Code Review**, then **ask the review session for another review** directly. Don't rely on the scheduled `/review` task:
     1. Message the existing `Review STU-xx` session: find it with `ListAgents` (or the desktop app's session tools, `mcp__ccd_session__*`, via ToolSearch) and send it, with `SendMessage` or the app's send-message tool: "STU-xx: feedback addressed in <commit sha>. Please re-review PR #n: check every unresolved thread, resolve the ones that are fixed, and merge if you're happy."
     2. If that session no longer exists or the message can't be delivered, start a fresh one as in step 7d.
     3. If neither works (the app may not let an auto-fix turn start sessions), comment on the PR and Linear ticket "Re-review needed" and tell me plainly: "Re-review NOT triggered: run `/review STU-xx`".

9. **When the PR is merged** (the review session tells you, or you see it): turn the monitor off for the PR (`set_monitor` with `auto_fix: false`), check the Linear ticket is in **QA** or **Done**, and finish.

Finish each turn with a short summary: which ticket, what was done, what needs me, the PR link, the review round (1–3), whether auto-fix is confirmed on, and whether the review session was reached.
