# 0014: Signed approvals from a paired app

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-109

## Context

ADR 0013 lets only `channel: "app_ui"` (the Alfred app's own confirmation screen) approve an `irreversible` tool, such as unlocking a door. But `channel` was whatever the caller sent. Every client (the phone, the web chat page, curl) holds the same `ALFRED_API_TOKEN`, so any token holder could claim `app_ui`. This has to be fixed before the first connector with an `irreversible` tool (Home Assistant, STU-62).

Options weighed:
- **A per-device secret**, issued at pairing and sent with approvals. It's simple, but the secret lives in app storage and crosses the wire with every approval.
- **A signed approval** (chosen). The app signs each answer with a Keystore key. Nothing secret is ever sent, and the private key can't be exported from the phone.

Either way, registering a credential can't be allowed on the API token alone, or the token holder could register their own and the hole stays open.

## Decision

**Key.** The app makes an EC P-256 key in the Android Keystore (`AndroidKeyStoreDeviceKeys`, alias `alfred_approval_key`, sign only, SHA-256). It's hardware-backed where the phone has it and can't be exported.

**Pairing needs a shell on the server.**
- `python -m alfred_server.devices pair --name pixel` prints a one-time code (`ABCD-EFGH`):
  - 8 Crockford base32 characters (about 40 bits)
  - valid for 10 minutes and single use
  - stored only as its SHA-256
- The app sends it with its public key (base64 X.509 SubjectPublicKeyInfo) to `POST /devices/pair`, which also needs the API token.
- The server accepts only P-256 keys. A bad key doesn't use up the code.
- Claiming a code is an atomic update, so it pairs one device even if two requests race.
- `devices list` and `devices revoke <id>` manage pairings.
- Tables: `paired_devices` and `pairing_codes` (`alfred_server/devices/store.py`).

**Signed approvals.**
- An `app_ui` answer to `POST /confirmations/{id}` carries `proof: {device_id, issued_at, signature}`.
- The signature is DER ECDSA (SHA-256) over the UTF-8 text `alfred-approval-v1\n{device_id}\n{confirmation_id}\n{true|false}\n{issued_at}`. The server builds it in `approval_message` and the app in `approvalMessage`, and both test suites assert the same string.
- The server checks that:
  - the device exists and isn't revoked
  - `issued_at` is within 5 minutes of its clock
  - the signature verifies

  It then records `last_used_at`.
- A signature therefore covers one answer (approve or deny) to one confirmation from one device. A confirmation can only be answered once, so a replayed proof has nothing left to approve.

**How the channel is decided** (`confirmations/api.py` `_verified_channel`):

| Request | Treated as |
| --- | --- |
| `app_ui` with a valid proof | `app_ui` |
| `app_ui` with no proof | `text` (so it can still approve a `write`, never an `irreversible`) |
| `app_ui` with a proof that fails | 403 |
| anything else | as sent |

Denials need no proof: saying no is always safe.

**Relaxing an `irreversible` tool needs the server.**
- Otherwise the token holder could `PUT /settings/permissions/{connector}/{tool}` `{"risk": "read"}` (or `write`, or reset an override) and then run the tool ungated or with a typed yes.
- So the API answers 403 to any change that takes a tool's effective tier from `irreversible` to lower (`ConfirmationService.set_override`, `IrreversibleRelaxError`).
- `python -m alfred_server.confirmations set <connector> <tool> <read|write|irreversible|reset>` can do it, and `list` shows every tier.
- Tightening, and relaxing anything below `irreversible`, still work over the API.
- A device-signed permission change was considered and not chosen. It needs replay protection (a change, unlike a confirmation, can be repeated), and the app has no permissions screen.

**Biometrics later.** The key has no user-authentication requirement yet. When the confirmation screen is built, it adds a BiometricPrompt and an auth-bound key, which means re-pairing.

## Consequences

- The shared API token can no longer approve an `irreversible` action, or relax one so it no longer needs approving. Approving needs the private key on a paired phone, and relaxing needs a shell on the server.
- **Root of trust is a shell on the server.** Anyone who can run the CLI there can pair a device, which is no worse than what they can already do on that box.
- The app pairs from Settings → Approvals with the saved server URL and token. Re-pairing registers the same key under a new device id. Revoke the old id with the CLI if it matters.
- **Clock skew.** A phone more than 5 minutes off the server's clock can't approve. Both normally use network time.
- **Theft.** A stolen, unlocked phone can approve until it's revoked (`devices revoke`), or until the biometric prompt lands.
- The web chat page still can't approve `irreversible` actions, which matches its label.
- The confirmation screen (its own ticket) calls `ApprovalSigner.sign` and `ServerClient.answerConfirmation`. Both exist and are tested, but nothing calls them yet.
