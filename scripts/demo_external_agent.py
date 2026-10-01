r"""A tiny stand-in for "another chat agent", for demonstrating the admin
portal's external agents. Standard library only.

    venv\Scripts\python.exe scripts\demo_external_agent.py [port]

Listens on 127.0.0.1:9000 (or the given port). POST a JSON body with a
"message" field and it answers {"reply": "..."}; with "stream": true in the
body it answers as Server Sent Events instead, one word at a time, so the
streaming option in the admin portal can be shown too.

Register it in the admin portal with URL http://127.0.0.1:9000/chat, method
POST, body template {"message": "{text}"} and response path "reply". The
app refuses local addresses by default (protection against server side
request forgery), so for this demo start it with
EXTERNAL_AGENT_ALLOW_PRIVATE=true.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _answer(message: str) -> str:
    return f"This is the partner agent speaking. You asked: {message}. I am a separate service, not the SLT Mobitel assistant."


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        reply = _answer(str(body.get("message", "")))
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for word in reply.split():
                self.wfile.write(f"data: {json.dumps({'reply': word + ' '})}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.15)
            self.wfile.write(b"data: [DONE]\n\n")
            return
        payload = json.dumps({"reply": reply}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        print("partner agent:", fmt % args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    print(f"Partner agent on http://127.0.0.1:{port}/chat (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
