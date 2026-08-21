"""Entry point: `oracled`."""

from __future__ import annotations

import uvicorn

from oracle.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "oracle.api.app:create_app",
        factory=True,
        host=s.host,
        port=s.port,
        log_config=None,  # structlog owns logging (docs/LOGGING.md)
    )


if __name__ == "__main__":
    main()
