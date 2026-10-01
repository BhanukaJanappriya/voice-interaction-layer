r"""Walks through the MCP server's security the way a client sees it, for a
live demonstration.

    venv\Scripts\python.exe scripts\demo_mcp_client.py CLIENT_ID CLIENT_SECRET [--base http://127.0.0.1:8000]

Create the client (and copy its secret, shown once) in the admin portal
under MCP clients first. Give it only knowledge:read to see the scope
check refuse ask_assistant, or both scopes to see everything succeed.
"""

import argparse
import asyncio
import json
import sys

import requests


def heading(text):
    print(f"\n=== {text}")


async def call_tools(base, token):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(f"{base}/mcp", http_client=http)) as client:
            heading("4. tools/list")
            for tool in (await client.list_tools()).tools:
                print(f"   {tool.name}")

            heading("5. search_knowledge (needs knowledge:read)")
            result = await client.call_tool("search_knowledge", {"query": "How do I activate one rate roaming?", "top_k": 2})
            data = getattr(result, "structured_content", None) or {}
            for hit in data.get("results", [])[:2]:
                print(f"   score {hit['score']:.2f}  {hit['source_url']}")
                print(f"   {hit['text'][:140]}...")

            heading("6. ask_assistant (needs assistant:ask)")
            try:
                result = await client.call_tool("ask_assistant", {"question": "What is the Mobitel hotline number?"})
                data = getattr(result, "structured_content", None) or {}
                print(f"   {data.get('reply')}  [{data.get('mode')}]")
            except Exception as exc:
                print(f"   refused: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client_id")
    parser.add_argument("client_secret")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    heading("1. Call /mcp with no token")
    response = requests.post(f"{args.base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=10)
    print(f"   HTTP {response.status_code}")
    print(f"   WWW-Authenticate: {response.headers.get('WWW-Authenticate')}")

    heading("2. Wrong secret at /oauth/token")
    response = requests.post(f"{args.base}/oauth/token", data={"grant_type": "client_credentials"},
                             auth=(args.client_id, "wrong-secret"), timeout=10)
    print(f"   HTTP {response.status_code} {json.dumps(response.json())}")

    heading("3. Real secret: short lived scoped token")
    response = requests.post(f"{args.base}/oauth/token", data={"grant_type": "client_credentials"},
                             auth=(args.client_id, args.client_secret), timeout=10)
    if response.status_code != 200:
        sys.exit(f"   token request failed: HTTP {response.status_code} {response.text}")
    token_data = response.json()
    print(f"   scopes {token_data['scope']!r}, valid {token_data['expires_in']} seconds")

    asyncio.run(call_tools(args.base, token_data["access_token"]))
    print("\nEvery token request and tool call above is now in the audit log (admin portal), with no question text.")


if __name__ == "__main__":
    main()
