"""X-Forwarded-For is honoured only from a configured trusted proxy (issue #155)."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from src.api.client_ip import client_ip
from src.core.config import Settings, settings


def _request(peer: str | None, *xff: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", v.encode()) for v in xff],
        "client": (peer, 1234) if peer else None,
    }
    return Request(scope)


@pytest.fixture
def trust(monkeypatch: pytest.MonkeyPatch):
    def _set(*entries: str) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXIES", list(entries))

    return _set


def test_default_is_empty_and_uses_the_peer(trust) -> None:
    assert Settings().TRUSTED_PROXIES == []
    trust()
    assert client_ip(_request("203.0.113.9", "198.51.100.1")) == "203.0.113.9"


def test_spoofed_header_from_an_untrusted_peer_is_ignored(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_trusted_peer_yields_the_forwarded_client(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request("10.1.1.1", "198.51.100.7")) == "198.51.100.7"


def test_client_supplied_prefix_cannot_override_the_proxy_appended_hop(trust) -> None:
    """The proxy appends the real peer; a forged leftmost entry is skipped."""
    trust("10.0.0.0/8")
    assert client_ip(_request("10.1.1.1", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"


def test_chain_of_trusted_proxies_is_walked_right_to_left(trust) -> None:
    trust("10.0.0.0/8", "192.0.2.5")
    assert (
        client_ip(_request("10.1.1.1", "9.9.9.9, 198.51.100.7, 192.0.2.5, 10.2.2.2"))
        == "198.51.100.7"
    )


def test_repeated_headers_are_joined(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request("10.1.1.1", "6.6.6.6", "198.51.100.7")) == "198.51.100.7"


def test_trusted_peer_without_header_uses_the_peer(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request("10.1.1.1")) == "10.1.1.1"


def test_malformed_forwarded_entry_falls_back_to_the_peer(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request("10.1.1.1", "not-an-ip")) == "10.1.1.1"


def test_missing_peer_is_unknown(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request(None, "1.2.3.4")) == "unknown"


def test_invalid_trusted_proxy_entry_is_rejected_at_startup() -> None:
    with pytest.raises(ValueError):
        Settings(TRUSTED_PROXIES=["not-a-network"])


def test_ipv4_mapped_ipv6_peer_matches_an_ipv4_trusted_network(trust) -> None:
    trust("10.0.0.0/8")
    assert client_ip(_request("::ffff:10.1.1.1", "198.51.100.7")) == "198.51.100.7"
