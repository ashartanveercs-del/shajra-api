"""Validation for bearer-authenticated Upstash REST endpoints."""

import re
from urllib.parse import urlsplit


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _is_canonical_dns_hostname(hostname: str) -> bool:
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError:
        return False
    labels = hostname.split(".")
    return (
        len(hostname) <= 253
        and len(labels) >= 2
        and all(_DNS_LABEL.fullmatch(label) for label in labels)
    )


def require_canonical_upstash_url(value: str) -> str:
    """Return an exact HTTPS origin or fail without exposing the supplied URL."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Invalid UPSTASH_REDIS_REST_URL") from None
    if (
        type(value) is not str
        or not hostname
        or not _is_canonical_dns_hostname(hostname)
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or value != f"https://{hostname}"
    ):
        raise ValueError("Invalid UPSTASH_REDIS_REST_URL")
    return value
