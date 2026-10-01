"""The one start command for the whole app: python -m backend

Runs a single uvicorn process serving everything: the frontend, the
admin portal, /ws/voice, /chat, /auth, /admin/api, /mcp and /health.
Host and port can be changed with --host and --port; by default it
listens on 127.0.0.1:8000, the address the frontend and README use.
"""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Start the SLT Mobitel voice assistant (single process).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    # Importing through the backend package puts backend/ on sys.path (see
    # backend/__init__.py), so server.py's own bare imports resolve.
    from backend import server

    uvicorn.run(server.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
