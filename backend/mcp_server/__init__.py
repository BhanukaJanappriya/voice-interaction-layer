"""MCP server for this assistant (requirement R3): OAuth client
credentials token endpoint, discovery metadata, three scoped tools, and an
audit trail. Named mcp_server, never mcp, so it cannot shadow the SDK.
server.py calls mount.mount_mcp(); the admin portal calls clients.py."""
