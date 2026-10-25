"""Pair, list and revoke the devices allowed to approve `irreversible` actions.

    python -m alfred_server.devices pair --name pixel   # prints a one-time code for the app
    python -m alfred_server.devices list
    python -m alfred_server.devices revoke <device id>

Uses `ALFRED_DATABASE_URL`, like the server. Running it is the root of trust for pairing, so
only someone with a shell on the server can let a new device approve.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

from alfred_server.devices.service import CODE_TTL, DeviceService, get_device_service


def _when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"


def main(argv: Sequence[str] | None = None, service: DeviceService | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alfred_server.devices")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair", help="mint a one-time pairing code")
    pair.add_argument("--name", required=True, help="what to call the device, e.g. pixel")
    pair.add_argument(
        "--minutes",
        type=int,
        default=int(CODE_TTL.total_seconds() // 60),
        help="how long the code is valid (default %(default)s)",
    )
    commands.add_parser("list", help="list paired devices")
    revoke = commands.add_parser("revoke", help="stop a device approving anything")
    revoke.add_argument("device_id")
    args = parser.parse_args(argv)

    try:
        devices = service or get_device_service()
        if args.command == "pair":
            name = args.name.strip()[:64]
            if not name or args.minutes < 1:
                parser.error("--name must not be empty and --minutes must be at least 1")
            code = devices.create_pairing_code(name, timedelta(minutes=args.minutes))
            print(f"Pairing code for {name!r}: {code}")
            print(f"Enter it in the Alfred app (Settings > Approvals) within {args.minutes} min.")
        elif args.command == "list":
            rows = devices.devices()
            if not rows:
                print("No paired devices.")
            for d in rows:
                state = f"revoked {_when(d.revoked_at)}" if d.revoked_at else "active"
                print(
                    f"{d.id}  {d.name}  paired {_when(d.created_at)}  "
                    f"last used {_when(d.last_used_at)}  {state}"
                )
        else:
            if not devices.revoke(args.device_id):
                print(f"No active device {args.device_id}", file=sys.stderr)
                return 1
            print(f"Revoked {args.device_id}")
    except Exception as exc:  # report storage failures as a clean CLI error
        print(f"devices: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
