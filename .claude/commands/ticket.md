---
description: Work a Linear ticket end to end. With no argument, picks up the next unblocked ticket.
argument-hint: "[STU-xx]"
---

Work a Linear ticket for the Alfred project, following the "Working on tickets" rules in CLAUDE.md.

Ticket requested: "$ARGUMENTS"

0. **Choose the ticket**
   - If a ticket key was given above, use it.
   - If it's empty, pick the next unblocked ticket yourself:
     1. List issues in Linear project **Alfred** (team Studio) with status **Ready**.
     2. Keep only tickets whose blocked-by tickets are all **Done**.
     3. Drop tickets that change the same core modules (router, connector registry, protocol, budget, `RequestContext`) as any ticket currently **In Development**, so parallel sessions don't collide.
     4. Prefer tickets that can be done in this environment: skip Android tickets if there's no Android SDK here, and skip tickets that need the GPU server if this isn't it (check with `nvidia-smi` / `docker` when relevant).
     5. Pick by priority (Urgent first), then milestone order (M1 before M2…), then lowest ticket number.
     6. Claim it immediately by moving it to **In Development**, so another session doesn't pick the same one. Then tell me which ticket you picked and why, in one line, and carry on.
   - If nothing qualifies, stop and show what's blocking progress (as `/next` would).

1. **Load context**
   - Fetch the ticket from Linear, including its relations and comments.
   - Fetch every ticket blocking it. If any blocker is not **Done**, stop and tell me which.
   - If a PR already exists for this ticket, you are resuming: read the PR, its review comments and the Linear comments, then continue from there instead of starting over.
   - Read CLAUDE.md and the docs it points to that are relevant to this ticket.

2. **Start**
   - Move the ticket to **In Development** (if step 0 didn't already).
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

7. **Hand off**
   - Commit (conventional commits, referencing the ticket key), push, and open a PR titled `STU-xx: <ticket title>` with summary, testing, acceptance-criteria checklist and follow-ups.
   - **Turn on auto-fix for the PR**, so CI failures and review comments get picked up automatically:
     - In a cloud session (claude.ai/code, mobile, `claude --cloud`): start watching the PR with auto-fix now, i.e. subscribe to its CI checks and review comments and fix clear issues as they arrive.
     - In a local session: you can't enable it yourself, so end your summary with "Run `/autofix-pr` on this branch to turn on auto-fix."
   - Comment on the Linear ticket with the PR link, a short summary, anything needing my verification, and follow-ups. Move it to **Code Review**.
   - If you had to stop because something is missing, move it to **Blocked** and comment with exactly what's needed instead.

Finish with a short summary: which ticket, what was done, what needs me, the PR link, and whether auto-fix is on.
