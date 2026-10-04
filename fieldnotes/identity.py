"""Client identity.

There is no login, so the only identifier available is the request's IP
address. Two rules follow from that:

1. The raw address is never stored. It is combined with a server-side pepper and
   hashed, which is what actually gets written to the database. Rotating the
   pepper anonymises every row retroactively.
2. The address is taken from the platform's proxy header, never from anything
   a client can set to an arbitrary value in a query parameter.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from flask import Request

# Ordered by trustworthiness. X-Forwarded-For is a list, client first.
FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "fastly-client-ip")


def client_ip(request: Request) -> str:
    """Best-effort client address, or an empty string when unknown."""
    for header in FORWARD_HEADERS:
        raw = request.headers.get(header)
        if not raw:
            continue
        first = raw.split(",")[0].strip()
        if first:
            return first
    return (request.remote_addr or "").strip()


@dataclass(frozen=True)
class Identity:
    voter: str
    ip: str

    @property
    def is_known(self) -> bool:
        return bool(self.voter)


def identify(request: Request, pepper: str) -> Identity:
    ip = client_ip(request)
    if not ip:
        # Still produce a stable bucket so unidentifiable traffic cannot be
        # unlimited, but it collapses into one shared identity.
        return Identity(voter=_hash("unknown", pepper), ip="")
    return Identity(voter=_hash(ip, pepper), ip=ip)


def _hash(ip: str, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}:{ip}".encode()).hexdigest()