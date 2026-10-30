from __future__ import annotations

import logging
import os

import uvicorn

from z_browser.app import create_app


def main() -> None:
    logging.basicConfig(level=os.environ.get("Z_LOG_LEVEL", "INFO"))
    uvicorn.run(
        create_app(),
        host=os.environ.get("Z_BROWSER_HOST", "0.0.0.0"),  # noqa: S104 - in a container, behind a NetworkPolicy
        port=int(os.environ.get("Z_BROWSER_PORT", "8090")),
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
