---
description: Work a Linear ticket end to end (read, plan, implement, test, PR, update Linear)
argument-hint: STU-xx
---

Work Linear ticket **$ARGUMENTS** for the Alfred project, following the "Working on tickets" rules in CLAUDE.md.

1. **Load context**
   - Fetch $ARGUMENTS from Linear, including its relations and comments.
   - Fetch every ticket blocking it. If any blocker is not **Done**, stop and tell me which.
   - If a PR already exists for this ticket, you are resuming: read the PR, its review comments and the Linear comments, then continue from there instead of starting over.
   - Read CLAUDE.md and the docs it points to that are relevant to this ticket.

2. **Start**
   - Move the ticket to **In Development**.
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
   - Commit (conventional commits, referencing $ARGUMENTS), push, and open a PR titled `$ARGUMENTS: <ticket title>` with summary, testing, acceptance-criteria checklist and follow-ups.
   - Comment on the Linear ticket with the PR link, a short summary, anything needing my verification, and follow-ups. Move it to **Code Review**.
   - If you had to stop because something is missing, move it to **Blocked** and comment with exactly what's needed instead.

Finish with a short summary: what was done, what needs me, and the PR link.
