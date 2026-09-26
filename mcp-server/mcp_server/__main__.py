from __future__ import annotations

import logging

from .config import Config
from .server import build_server, build_transport_security


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config.from_env()
    server = build_server(config)
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=config.port,
        transport_security=build_transport_security(config),
    )


if __name__ == "__main__":
    main()
