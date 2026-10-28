"""`z-server` entrypoint. Workstream B1 implements the subcommands."""

import sys


def main() -> int:
    print("z-server: not built yet (BUILD phase B1)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
