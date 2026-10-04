from __future__ import annotations

import sys
from pathlib import Path

import pytest
from werkzeug.datastructures import Headers

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fieldnotes.app import create_app  # noqa: E402
from fieldnotes.config import Config  # noqa: E402
from fieldnotes.db import reset_engine  # noqa: E402

ORIGIN = "https://blog.example"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        vote_pepper="test-pepper",
        allowed_origins=(ORIGIN,),
        comment_rate_limit=3,
        comment_rate_window_hours=24,
        comment_min_interval_seconds=0,
        comment_max_length=200,
        comment_max_links=2,
        comments_require_moderation=False,
        auto_create_schema=True,
    )


@pytest.fixture
def client(config: Config):
    reset_engine()
    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    reset_engine()


def headers(ip: str, origin: str | None = ORIGIN) -> dict:
    """Request headers pinning the apparent client address."""
    out = {"X-Forwarded-For": ip}
    if origin is not None:
        out["Origin"] = origin
    return out


def post_json(client, url: str, payload: dict, ip: str = "203.0.113.5", origin: str | None = ORIGIN):
    return client.post(url, json=payload, headers=headers(ip, origin))


def get_json(client, url: str, ip: str = "203.0.113.5", origin: str | None = ORIGIN):
    return client.get(url, headers=headers(ip, origin))


def fake_request(ip: str, remote: str | None = None):
    """A request-like object with case-insensitive headers, as Flask/Werkzeug has."""
    hdrs = Headers({"X-Forwarded-For": ip})

    class FakeRequest:
        def __init__(self):
            self.headers = hdrs
            self.remote_addr = remote

    return FakeRequest()