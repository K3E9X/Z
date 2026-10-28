"""`z` entrypoint. Workstream B4 implements the subcommands."""

import sys


def main() -> int:
    print("z: not built yet (BUILD phase B4)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
