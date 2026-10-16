"""The one port the app runs on: 8000.

It is a constant on purpose, not a setting: the page, the README, the admin portal and the
MCP callbacks all use http://127.0.0.1:8000, so a different port would silently break them. If
8000 is taken, the app does not move to another port; it says what to do and stops.
"""

import socket
import sys

APP_PORT = 8000


def ensure_free(host: str = "127.0.0.1", port: int = APP_PORT) -> None:
    """Exits with a clear message when something already listens on the port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No SO_REUSEADDR: on Windows it would let a second listener share the port.
        probe.bind(("0.0.0.0" if host in ("0.0.0.0", "") else "127.0.0.1", port))
    except OSError:
        print(
            f"Port {port} is already in use. This app runs only on port {port}: "
            f"stop the program that is using it (for example an older copy of this server) and start again.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    finally:
        probe.close()
