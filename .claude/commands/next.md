---
description: List Alfred tickets that are ready to start now, and which can safely run in parallel
---

Find Alfred tickets I can start now.

1. List issues in Linear project **Alfred** (team Studio) with status **Ready**.
2. For each, fetch its blocked-by relations. Keep only tickets whose blockers are all **Done**.
3. Show them as a table: key, title, milestone, priority, labels (Server/Android), and where it must run (home server vs Android machine).
4. Suggest which can run in parallel in separate worktrees: group tickets that don't touch the same core modules (router, connector registry, protocol, budget, `RequestContext`). Flag overlaps.
5. Also list tickets in **Code Review** (waiting for review/merge) and **Blocked** (with the reason from their latest comment), so I can see what's stuck.

Don't change any tickets.
