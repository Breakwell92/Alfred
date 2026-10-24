# 0011: Web chat page for testing the brain

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-59

## Context

Before voice exists, Dan needs to talk to Alfred by text and see, for each turn, which specialist and model answered and why (ADR 0009). The page has to work over Tailscale from any browser, with no build step, and without putting the API token in the page. Every server route except `/health` requires the bearer token, but a browser can't send a bearer header when it opens a page.

## Decision

- **Files.** `alfred_server/ui/` serves three static files: `GET /ui/chat` (HTML), `/ui/chat.js` and `/ui/chat.css`, read from `alfred_server/ui/static/`. Plain HTML, CSS and JS, no framework or build step.
- **The page shell is open; the API is not.** These three routes, `/health` and the OAuth callback (ADR 0010) are the only ones without the token. They hold no data and no secrets (a test checks the configured token never appears in them). The page asks for the token once, keeps it in `localStorage` (in memory if storage is blocked) and sends it as `Authorization: Bearer …` on every API call. A 401 clears it and asks again. The alternatives were worse: a token in the URL leaks into history and logs, and a cookie login would mean a second auth scheme.
- **Locked-down responses.** `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. No inline script or style, so the CSP needs no `unsafe-inline`. The script only ever sets model output as `textContent`, never HTML.
- **Talking to the brain.** The page posts to `POST /chat/stream` with `device_type: "text"` (so `RequestContext.device_type` is `text`) and a `session_id` it generates and keeps in `localStorage`. "New conversation" starts a new id.
- **Per-turn metadata.** The stream's `done` event now carries `turn`: the routing decision (`alias`, `tier`, `reason`, `budget_code`, `budget_tier`, `specialist`), the tools the final run called, its input/output tokens and `cost_gbp`. Tools and tokens come from the stored Agno run, because the stream's completion event doesn't carry tool calls. `cost_gbp` is `null` for local models. For cloud it is the proxy's price for the run's tokens (`BudgetService.estimate_cost`), so close to, but not exactly, the spend LiteLLM records. It is also `null` if the price can't be read. The page shows the metadata as one line under each reply.
- **Confirmations.** The page has a panel for pending confirmations, with Approve and Deny buttons. It uses the API that STU-57 defines (`GET /confirmations/pending`, `POST /confirmations/{id}` `{approve}`) and checks it on load and after every turn. Until that API exists the server answers 404 and the panel stays hidden. The server enforces which channel may approve which tier. The page only labels `irreversible` items "can only be approved in the app".

## Consequences

- A new open route must be static, data-free and added to the exception in `CLAUDE.md`. Anything that returns data stays on the protected router.
- The confirmations panel guesses STU-57's response shape: a list, or `{pending: [...]}`, of items with `id`, `summary`, `tier` and `expires_at`. STU-57 should match that or adjust `chat.js`.
- Every streamed turn now does one extra storage read (the stored run) before `done`. Before, that read happened only when the reply might be the low-confidence marker.
- A Telegram bot or the Android chat screen (STU-99) can reuse the same `turn` metadata.
