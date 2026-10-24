---
description: Project manager for Alfred. Gives a project update, then starts a /ticket session for each free ticket, up to 2 In Development at once.
argument-hint: "[status]"
---

Act as project manager for the Alfred project, following the "Working on tickets" rules in CLAUDE.md (section 0 has the status/assignee/label table).

Argument: "$ARGUMENTS". If it is `status`, do steps 1 and 2 only and start nothing.

**Never change tickets or PRs yourself.** Don't assign, move or comment on Linear tickets, and don't touch PR labels. The sessions you start do that. In particular, don't assign a ticket before starting its session: `/ticket` treats an assignee as "another session took it" and backs off.

1. **Project update** (Linear project **Alfred**, team Studio):
   - **Milestones:** for each milestone, the count of tickets Done / QA / in flight (In Development, Code Review) / not started, and the next milestone to finish.
   - **Since last update:** tickets that moved to Done or QA, and PRs merged, in the last 7 days.
   - **Outstanding:** what's left in the current milestone, and anything that will block the next one.
   - **Needs Dan:** QA tickets (with what to verify), Blocked tickets (reason from the latest comment), PRs whose latest comment is a question for Dan, and Ready tickets that are assigned (agents skip these).
   Keep it short: a few lines per heading.

2. **Status board:** produce the board described in `.claude/commands/next.md` (read it and follow it), without repeating anything already in the update.

3. **Dispatch** (skip if the argument is `status`):
   1. **Slots:** 2 minus the number of tickets currently **In Development** (including ones with a `changes-requested` PR). Also count any ticket you started a session for in this conversation that isn't In Development yet, since its session may not have claimed it. If no slots are left, say so and stop.
   2. **Candidates:** the "Free to pick up" tickets from the board (Ready, unassigned, all blockers Done or QA). Drop:
      - tickets that can't run on this machine: Android tickets if there's no Android SDK here, GPU/server/Docker tickets if this isn't the home server (check with `nvidia-smi` / `docker` / `ANDROID_HOME` when relevant);
      - tickets that change the same core modules (router, connector registry, protocol, budget, `RequestContext`) as a ticket In Development or as another candidate you're picking;
      - tickets you already started a session for in this conversation.
   3. **Pick** in `/ticket`'s order (priority, then milestone, then lowest number), up to the number of slots.
   4. **Start one session per pick.** Dan asks for this whenever he runs `/pm`. In the Claude desktop app, call `start_session` (`mcp__ccd_session__start_session`, load it via ToolSearch if it's deferred) with `initiation: "user_asked"`, `context: "fresh"`, `use_worktree: true`, title `STU-xx` and prompt `/ticket STU-xx`. Run them locally, never `target: "cloud"`. If the tool isn't available (CLI, cloud) or the app refuses a start, list the picks with the command to run (`/ticket STU-xx`) instead.
   5. Say in one line per ticket what you started and why it was picked; for each candidate you dropped, say why in a few words.

4. **Keep the pipeline full.** When a session you started ends a turn and you're continued with its note, check the board again (dispatch step only, no full update). If a slot has opened (its ticket moved to Code Review, QA, Done or Blocked) and a candidate qualifies, start the next one the same way. Report in a line or two. If the app refuses the start, tell Dan to run `/pm` again.

Use the GitHub REST API (`gh api repos/Breakwell92/Alfred/...`) for PRs if GraphQL isn't available.
