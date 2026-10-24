---
description: Show what's free to pick up, what's waiting for review, and what's stuck in the Alfred project
---

Give me a status board for the Alfred project. Don't change any tickets or PRs.

1. **Free to pick up:** Linear project **Alfred** (team Studio) issues with status **Ready**, **no assignee**, and all blocked-by tickets **Done** or **QA**. Show a table: key, title, milestone, priority, labels (Server/Android), and where it must run (home server vs Android machine). Suggest which can run in parallel (no shared core modules: router, connector registry, protocol, budget, `RequestContext`) and flag overlaps.
2. **In progress:** tickets **In Development**, with their PR (if any) and its label (`changes-requested` means auto-fix is working on review feedback).
3. **Waiting for review:** open PRs labelled `ready-for-review` (oldest first) and `in-review`.
4. **Needs Dan:** tickets in **QA** (merged; list what to verify), **Blocked** (reason from the latest comment), and any PR whose latest comment is a question for Dan.
5. **Ready but assigned:** any Ready tickets with an assignee, since agents skip these. They may need unassigning.

Use the GitHub REST API (`gh api`) for PRs if GraphQL isn't available.
