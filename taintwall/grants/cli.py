"""`taintwall-grant create`: mint a grant. The token goes to stdout once; only its hash is kept.

Not safe for concurrent writers to the same file. The gateway reads the file at startup (M1).
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError

from taintwall.grants.store import GrantsFileError, add_grant, utc_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taintwall-grant", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a grant and print its token once")
    create.add_argument("--grants-file", type=Path, required=True)
    create.add_argument("--agent-id", required=True)
    create.add_argument("--tools", nargs="+", required=True, help="namespaced tool names, or '*'")
    create.add_argument("--ttl-hours", type=float, required=True)
    create.add_argument("--grant-id", help="default: generated")
    args = parser.parse_args(argv)

    if not math.isfinite(args.ttl_hours) or args.ttl_hours <= 0:
        print("error: --ttl-hours must be a positive number", file=sys.stderr)
        return 2
    try:
        expires_at = utc_now() + timedelta(hours=args.ttl_hours)
        token, grant = add_grant(
            args.grants_file,
            agent_id=args.agent_id,
            tools=args.tools,
            expires_at=expires_at,
            grant_id=args.grant_id,
        )
    except (ValidationError, GrantsFileError, ValueError, OverflowError, OSError) as exc:
        detail = "invalid grant fields" if isinstance(exc, ValidationError) else str(exc)
        print(f"error: {detail}", file=sys.stderr)
        return 2
    print(f"created {grant.grant_id}, expires {grant.expires_at.isoformat()}", file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
