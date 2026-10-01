"""url_safety.check_outbound_url: SSRF refusal for external agent URLs."""

import socket

import pytest

from conftest import require_module


@pytest.fixture(scope="module")
def url_safety():
    return require_module("url_safety")


@pytest.fixture
def fake_dns(monkeypatch):
    """Maps hostnames to addresses without touching the network."""
    table = {}
    real = socket.getaddrinfo

    def _getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host in table:
            results = []
            for address in table[host]:
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                sockaddr = (address, port or 0, 0, 0) if family == socket.AF_INET6 else (address, port or 0)
                results.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
            return results
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    return table


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/chat",
        "http://127.1.2.3/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://100.64.0.1/",
        "http://100.127.255.254/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
    ],
)
def test_private_and_special_addresses_refused(url_safety, url):
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url(url, allow_private=False)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://93.184.216.34/file",
        "file:///etc/passwd",
        "gopher://93.184.216.34/",
        "javascript:alert(1)",
        "//93.184.216.34/",
        "not a url",
        "",
    ],
)
def test_non_http_schemes_refused(url_safety, url):
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url(url, allow_private=False)


@pytest.mark.parametrize(
    "url",
    ["http://user:pass@93.184.216.34/", "https://user@93.184.216.34/", "http://:secret@93.184.216.34/"],
)
def test_credentials_in_url_refused(url_safety, url):
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url(url, allow_private=False)


def test_hostname_resolving_to_private_address_refused(url_safety, fake_dns):
    fake_dns["internal.example.com"] = ["10.1.2.3"]
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("https://internal.example.com/api", allow_private=False)


def test_hostname_with_any_private_record_refused(url_safety, fake_dns):
    fake_dns["mixed.example.com"] = ["93.184.216.34", "127.0.0.1"]
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("https://mixed.example.com/", allow_private=False)


def test_hostname_resolving_to_mapped_loopback_refused(url_safety, fake_dns):
    fake_dns["sneaky.example.com"] = ["::ffff:127.0.0.1"]
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("https://sneaky.example.com/", allow_private=False)


def test_unresolvable_hostname_refused(url_safety, monkeypatch):
    def _fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("https://nowhere.invalid/", allow_private=False)


def test_public_ip_allowed(url_safety):
    url_safety.check_outbound_url("https://93.184.216.34/chat", allow_private=False)
    url_safety.check_outbound_url("http://8.8.8.8:8080/", allow_private=False)


def test_public_hostname_allowed(url_safety, fake_dns):
    fake_dns["agent.example.com"] = ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
    url_safety.check_outbound_url("https://agent.example.com/v1/chat", allow_private=False)


def test_allow_private_flag_permits_loopback(url_safety):
    url_safety.check_outbound_url("http://127.0.0.1:9999/", allow_private=True)


def test_allow_private_still_refuses_bad_scheme_and_credentials(url_safety):
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("file:///etc/passwd", allow_private=True)
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("http://u:p@127.0.0.1/", allow_private=True)


def test_default_follows_config_flag_at_call_time(url_safety, monkeypatch):
    """The admin can only reach a local test agent when the development
    flag is on; the default must read config when called, not when the
    module was imported."""
    import config

    monkeypatch.setattr(config, "EXTERNAL_AGENT_ALLOW_PRIVATE", False)
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.check_outbound_url("http://127.0.0.1:9999/")
    monkeypatch.setattr(config, "EXTERNAL_AGENT_ALLOW_PRIVATE", True)
    url_safety.check_outbound_url("http://127.0.0.1:9999/")


def test_unsafe_url_error_is_an_exception(url_safety):
    assert issubclass(url_safety.UnsafeUrlError, Exception)
