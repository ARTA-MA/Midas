"""Midas Engine entrypoint.

Runs the local FastAPI server that the Flutter shell talks to.
Usage: midas-engine.exe [--port 0] [--no-watchdog]
"""
import argparse
import socket
import sys

import uvicorn

from midas_engine.api.app import create_app


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(prog="midas-engine")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = auto)")
    parser.add_argument("--no-watchdog", action="store_true",
                        help="Disable heartbeat watchdog (dev mode)")
    args = parser.parse_args()

    port = args.port or find_free_port()
    app = create_app(watchdog=not args.no_watchdog)

    # The Flutter shell reads this line from stdout to discover the port.
    print(f"MIDAS_ENGINE_PORT={port}", flush=True)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
