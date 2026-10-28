"""`z-runner` entrypoint. Workstream B2 implements the subcommands."""

import sys


def main() -> int:
    print("z-runner: not built yet (BUILD phase B2)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
