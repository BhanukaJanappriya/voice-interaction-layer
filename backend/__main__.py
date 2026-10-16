"""The one start command for the whole app: python -m backend

Runs a single uvicorn process serving everything: the frontend, the
admin portal, /ws/voice, /chat, /auth, /admin/api, /mcp and /health.
The port is always 8000 (backend/app_port.py): there is no --port option, and if 8000 is
already taken the app says so and stops instead of picking another port. Only the host can
be changed with --host (default 127.0.0.1).
"""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Start the SLT Mobitel voice assistant (single process).")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    # Importing through the backend package puts backend/ on sys.path (see
    # backend/__init__.py), so server.py's own bare imports resolve.
    from backend import server
    from backend.app_port import APP_PORT, ensure_free

    ensure_free(args.host)
    uvicorn.run(server.app, host=args.host, port=APP_PORT)


if __name__ == "__main__":
    main()
