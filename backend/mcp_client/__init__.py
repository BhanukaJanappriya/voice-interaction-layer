"""This assistant as a client of the SLT MCP hub (plan R4).

The package is imported by bare name like every other backend package
(`from mcp_client.tools import hub_context_for`), with backend/ on
sys.path. Nothing is imported here so that importing the package stays
free of the MCP SDK until the hub is actually used.
"""
