# 0002: Android client stack and server auth

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-51

## Context

The Android app is a thin, sideloaded, single-user client that talks to the home server over Tailscale. It needs DI, settings storage, HTTP/WebSocket and a way to prove to the server that requests come from Dan's phone. The server holds OAuth tokens and home controls, so every route except a health check must be authenticated.

## Decision

- **Toolchain:** latest stable at the time: AGP 9.4 (built-in Kotlin, so no `kotlin-android` plugin is applied), Kotlin 2.4 with the Compose compiler plugin, Gradle 9.8 wrapper, JDK 21. compileSdk/targetSdk 37, minSdk 29. Versions live in `android/gradle/libs.versions.toml`.
- **DI: Koin**, not Hilt. Koin needs no annotation processing, so there's no KSP or Hilt Gradle plugin to keep in step with AGP 9 and Kotlin releases. For an app this size, compile-time graph checking isn't worth that churn.
- **Architecture:** single activity, Compose navigation (string routes), ViewModels exposing `StateFlow`, OkHttp for HTTP (and WebSocket later), Preferences DataStore for settings.
- **Transport:** plain HTTP to the server's Tailscale MagicDNS name (default `http://glados:8000`). Tailscale already encrypts and authenticates the link with WireGuard, and MagicDNS names have no public TLS certificate. Cleartext is allowed in `network_security_config.xml`.
- **Auth:** one shared bearer token, `ALFRED_API_TOKEN`. The server checks it on every route except `/health` (protected `APIRouter` plus the `require_api_token` dependency, compared in constant time). It **fails closed**: with no token configured, protected routes return 503. FastAPI's `/docs`, `/redoc` and `/openapi.json` are disabled so that `/health` is the only open route.
- **Token storage on the phone:** Preferences DataStore in app-private storage, unencrypted. Backups and device transfer are excluded (`data_extraction_rules.xml`).

## Consequences

- New server routes must go on the protected router in `main.py` (or depend on `require_api_token`), otherwise they're unauthenticated.
- Losing the phone means rotating `ALFRED_API_TOKEN`. Per-device tokens or Tailscale identity headers can replace the shared token if more clients appear.
- If the server ever leaves the tailnet (public exposure), TLS becomes mandatory and the cleartext config must go.
- Encrypting the token at rest (Android Keystore) is a possible follow-up if the threat model changes.
- To get the interactive API docs back for development, they'd need to sit behind the same token.
