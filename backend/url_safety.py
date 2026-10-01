"""Outbound URL safety checks for calls this server makes on an admin's
behalf (external chat agents registered in the admin portal).

An admin types a URL and this server then sends requests to it, so a
careless or malicious entry could point the server at something only it
can reach: a database on localhost, a router's admin page on the LAN, or
the cloud metadata service that hands out instance credentials
(169.254.169.254). That is server side request forgery (SSRF). This module
refuses such targets:

    check_outbound_url(url, allow_private=config.EXTERNAL_AGENT_ALLOW_PRIVATE)
        Validates a URL up front (at save time and before every request):
        http or https only, no user:password@ in the URL, and every A and
        AAAA record the host resolves to must be a public address.

    safe_session(allow_private=...)
        A requests.Session that repeats the address check at the moment
        of connecting and then connects to exactly the address it checked
        (DNS pinning). Without this a host could resolve to a public
        address when checked and to 127.0.0.1 a moment later when the
        request is actually made (DNS rebinding). The session also ignores
        proxy environment variables and .netrc, and callers must pass
        allow_redirects=False, since a redirect is just another way to
        send the request somewhere that was never checked.

allow_private (the EXTERNAL_AGENT_ALLOW_PRIVATE development flag) lets
private, loopback and link local addresses through so a stub agent on the
same machine can be tested. Cloud metadata addresses stay refused even
then: no chat agent ever lives there.
"""

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.util import connection as urllib3_connection

import config


class UnsafeUrlError(ValueError):
    """The URL is malformed, uses a scheme other than http/https, carries
    credentials, cannot be resolved, or resolves to an address this server
    must not call. The message is plain enough to show an admin."""


_ALLOWED_SCHEMES = ("http", "https")

# Shared address space for carrier grade NAT (RFC 6598). Python's
# ipaddress does not count it as private, but it is not the public
# internet either.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
# NAT64 well known prefix: the last 32 bits are an IPv4 address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

# Instance metadata endpoints (AWS, GCP, Azure, OpenStack on IPv4; AWS on
# IPv6; Alibaba Cloud). Refused even when private addresses are allowed.
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def _embedded_ipv4(address: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    """The IPv4 address hidden inside an IPv6 one (IPv4 mapped
    ::ffff:a.b.c.d, 6to4, Teredo, NAT64), if any, so ::ffff:127.0.0.1 is
    judged as 127.0.0.1."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def address_refusal_reason(ip_text: str, allow_private: bool = False) -> Optional[str]:
    """Why this server must not connect to ip_text, or None if it may."""
    try:
        address = ipaddress.ip_address(ip_text.split("%", 1)[0])
    except ValueError:
        return f"'{ip_text}' is not an IP address"

    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            reason = address_refusal_reason(str(embedded), allow_private)
            return f"{reason} (inside IPv6 address {address})" if reason else None

    if address in _METADATA_ADDRESSES:
        return f"{address} is a cloud metadata address"
    if address.is_multicast:
        return f"{address} is a multicast address"
    if address.is_unspecified:
        return f"{address} is an unspecified address"
    if allow_private:
        return None
    if address.is_loopback:
        return f"{address} is a loopback address"
    if address.is_link_local:
        return f"{address} is a link local address"
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return f"{address} is a carrier grade NAT address"
    if address.is_private:
        return f"{address} is a private network address"
    if address.is_reserved:
        return f"{address} is a reserved address"
    if not address.is_global:
        return f"{address} is not a public internet address"
    return None


def _split_url(url: str):
    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("The URL is empty.")
    if any(ch in url for ch in "\r\n\t ") or any(ord(ch) < 32 for ch in url):
        raise UnsafeUrlError("The URL contains spaces or control characters.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise UnsafeUrlError("The URL is malformed (bad host or port).") from None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError("Only http and https URLs are allowed.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeUrlError("Credentials in the URL (user:password@) are not allowed; use the auth header instead.")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("The URL has no host name.")
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    return host, port


def resolve_addresses(host: str, port: int) -> list[str]:
    """Every address host resolves to (A and AAAA). Raises UnsafeUrlError
    when it does not resolve at all."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise UnsafeUrlError(f"The host name '{host}' could not be resolved.") from None
    addresses = []
    for info in infos:
        ip_text = info[4][0]
        if ip_text not in addresses:
            addresses.append(ip_text)
    if not addresses:
        raise UnsafeUrlError(f"The host name '{host}' has no addresses.")
    return addresses


def check_resolved(host: str, addresses: list[str], allow_private: bool) -> None:
    """Refuses the host if any one of its addresses is unsafe (not just the
    first: the connection may use any of them)."""
    for ip_text in addresses:
        reason = address_refusal_reason(ip_text, allow_private)
        if reason:
            raise UnsafeUrlError(f"Refused to call '{host}': {reason}.")


def check_outbound_url(url: str, allow_private: Optional[bool] = None) -> None:
    """Raises UnsafeUrlError unless url is an http(s) URL without
    credentials whose host resolves only to addresses this server may call.
    allow_private defaults to config.EXTERNAL_AGENT_ALLOW_PRIVATE."""
    if allow_private is None:
        allow_private = config.EXTERNAL_AGENT_ALLOW_PRIVATE
    host, port = _split_url(url)
    check_resolved(host, resolve_addresses(host, port), allow_private)


# DNS pinning at connect time

def _guarded_connection_class(base, allow_private: bool):
    class GuardedConnection(base):
        """Resolves the host itself, refuses it if any address is unsafe,
        then connects to one of exactly those checked addresses. TLS still
        verifies the certificate against the host name (urllib3 uses
        self.host for SNI and matching), so pinning changes nothing about
        HTTPS verification."""

        def _new_conn(self) -> socket.socket:
            try:
                infos = socket.getaddrinfo(self._dns_host, self.port, type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise NameResolutionError(self.host, self, exc) from exc
            addresses = []
            for info in infos:
                if info[4][0] not in addresses:
                    addresses.append(info[4][0])
            check_resolved(self.host, addresses, allow_private)

            last_error: Optional[Exception] = None
            for ip_text in addresses:
                try:
                    return urllib3_connection.create_connection(
                        (ip_text, self.port),
                        self.timeout,
                        source_address=self.source_address,
                        socket_options=self.socket_options,
                    )
                except socket.timeout as exc:
                    last_error = ConnectTimeoutError(
                        self, f"Connection to {self.host} timed out. (connect timeout={self.timeout})"
                    )
                    last_error.__cause__ = exc
                except OSError as exc:
                    last_error = NewConnectionError(self, f"Failed to establish a new connection: {exc}")
                    last_error.__cause__ = exc
            raise last_error

    return GuardedConnection


_POOL_CLASSES: dict[bool, dict] = {}


def _pool_classes(allow_private: bool) -> dict:
    if allow_private not in _POOL_CLASSES:
        http_conn = _guarded_connection_class(HTTPConnection, allow_private)
        https_conn = _guarded_connection_class(HTTPSConnection, allow_private)

        class GuardedHTTPPool(HTTPConnectionPool):
            ConnectionCls = http_conn

        class GuardedHTTPSPool(HTTPSConnectionPool):
            ConnectionCls = https_conn

        _POOL_CLASSES[allow_private] = {"http": GuardedHTTPPool, "https": GuardedHTTPSPool}
    return _POOL_CLASSES[allow_private]


class _GuardedAdapter(HTTPAdapter):
    def __init__(self, allow_private: bool):
        self._allow_private = allow_private
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = _pool_classes(self._allow_private)
        # key_fn_by_scheme is keyed the same way; the defaults still apply.

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        raise UnsafeUrlError("Outbound calls through a proxy are not supported for external agents.")


def safe_session(allow_private: Optional[bool] = None) -> requests.Session:
    """A requests.Session whose every connection is checked and pinned as
    described in the module docstring. Callers still pass
    allow_redirects=False on each request."""
    if allow_private is None:
        allow_private = config.EXTERNAL_AGENT_ALLOW_PRIVATE
    session = requests.Session()
    # No proxies from the environment and no .netrc credentials: the
    # request must go straight to the checked address with only the
    # headers the admin configured.
    session.trust_env = False
    adapter = _GuardedAdapter(allow_private)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


if __name__ == "__main__":
    import sys
    import time

    for candidate in sys.argv[1:] or ["https://www.sltmobitel.lk/", "http://127.0.0.1:8000/", "http://169.254.169.254/"]:
        start = time.perf_counter()
        try:
            check_outbound_url(candidate)
            verdict = "allowed"
        except UnsafeUrlError as exc:
            verdict = f"refused: {exc}"
        print(f"{candidate}: {verdict} ({(time.perf_counter() - start) * 1000:.0f} ms)")
