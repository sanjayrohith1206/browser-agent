"""Run the backend: `uv run python -m app`."""

from __future__ import annotations

import uvicorn

from app.config import get_settings
from app.main import create_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        ws_max_size=4 * 1024 * 1024,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        server_header=False,
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
