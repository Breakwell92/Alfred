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

1. **Load context**
   - Fetch the ticket from Linear, including its relations and comments.
   - Fetch every ticket blocking it. If any blocker is not **Done** or **QA**, unassign yourself, move the ticket back to **Ready**, and tell me which blocker is outstanding.
   - If a PR already exists for this ticket, you are resuming: read the PR, its inline review comments and the Linear comments, then continue from there (see "Responding to review feedback" in CLAUDE.md) instead of starting over.
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

7. **Hand off for review**
   - Rebase on `main`, commit (conventional commits, referencing the ticket key), push, and open a PR titled `STU-xx: <ticket title>` with summary, testing, acceptance-criteria checklist (including any "Needs Dan to verify" items) and follow-ups. Use the REST API (`gh api`) if GraphQL isn't available.
   - Add the `ready-for-review` label to the PR. This is how `/review` agents find it.
   - **Turn on auto-fix for the PR**, so CI failures and inline review comments get picked up automatically:
     - In a cloud session (claude.ai/code, mobile, `claude --cloud`): start watching the PR with auto-fix now, i.e. subscribe to its CI checks and review comments and fix clear issues as they arrive.
     - In a local session: you can't enable it yourself, so end your summary with "Run `/autofix-pr` on this branch to turn on auto-fix."
   - Comment on the Linear ticket with the PR link, a short summary, anything needing my verification, and follow-ups. Move it to **Code Review** (keep it assigned).
   - If you had to stop because something is missing, move it to **Blocked** (keep it assigned) and comment with exactly what's needed instead.

8. **When review feedback arrives** (auto-fix events, or resuming later): follow "Responding to review feedback" in CLAUDE.md. Fix clear comments, push, reply on each thread, then swap `changes-requested` for `ready-for-review` and move the ticket back to **Code Review**.

Finish with a short summary: which ticket, what was done, what needs me, the PR link, and whether auto-fix is on.
