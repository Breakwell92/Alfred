"""List tool permissions and change them, including relaxing an `irreversible` tool.

    python -m alfred_server.confirmations list
    python -m alfred_server.confirmations set <connector> <tool> <read|write|irreversible|reset>

Uses `ALFRED_DATABASE_URL`, like the server. The API (`PUT /settings/permissions/...`) can
tighten any tool and relax any tool that isn't `irreversible`; taking a tool out of
`irreversible` is only possible here, so the shared API token can't get round signed `app_ui`
approvals (ADR 0016).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import cast

from alfred_server.confirmations.models import Tier
from alfred_server.confirmations.service import ConfirmationService, get_confirmation_service

CHOICES = ["read", "write", "irreversible", "reset"]


def main(argv: Sequence[str] | None = None, service: ConfirmationService | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alfred_server.confirmations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every declared tool with its declared and effective tier")
    change = commands.add_parser("set", help="override one tool's tier, or reset it")
    change.add_argument("connector_id")
    change.add_argument("tool")
    change.add_argument("risk", choices=CHOICES)
    args = parser.parse_args(argv)

    try:
        confirmations = service or get_confirmation_service()
        overrides = confirmations.store.overrides()
        if args.command == "list":
            for connector in confirmations.registry.all():
                for meta in connector.tools:
                    override = overrides.get((connector.id, meta.name))
                    effective = override or meta.tier
                    note = f" (override; default {meta.tier})" if override else ""
                    print(f"{connector.id}/{meta.name}  {effective}{note}")
            return 0
        risk = None if args.risk == "reset" else cast(Tier, args.risk)
        try:
            confirmations.set_override(
                args.connector_id, args.tool, risk, allow_relaxing_irreversible=True
            )
        except KeyError:
            print(f"No declared tool {args.connector_id}/{args.tool}", file=sys.stderr)
            return 1
        print(f"{args.connector_id}/{args.tool}: {args.risk}")
    except Exception as exc:  # report storage failures as a clean CLI error
        print(f"confirmations: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
