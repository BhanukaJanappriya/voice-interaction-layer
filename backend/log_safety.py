"""Keeps customer data out of the logs.

The MCP SDK and the HTTP libraries under it log whole request and response
bodies at DEBUG, including the telephone_no of a tool call, the typed one time
code and the data a customer tool returns. Nothing in this repository logs those,
but a DEBUG level on those third party loggers would. cap_sensitive_loggers()
raises them to INFO, whatever the root level is, so account data never reaches a
log file even if someone turns on debug logging for the rest of the app.
"""

import logging

# The http libraries are installed under their own names here (httpx2, httpcore2).
SENSITIVE_LOGGERS = (
    "mcp",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "sse_starlette",
    "h11",
    "anyio",
)


# The HTTP clients log the hub's transport address ("POST .../messages/?session_id=...")
# at INFO on every call. That id is a handle to a customer's open hub connection, so
# these stay quiet below WARNING.
QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")


def cap_sensitive_loggers(level: int = logging.INFO) -> None:
    for name in SENSITIVE_LOGGERS:
        logger = logging.getLogger(name)
        wanted = max(level, logging.WARNING) if name in QUIET_LOGGERS else level
        if logger.level == logging.NOTSET or logger.level < wanted:
            logger.setLevel(wanted)
