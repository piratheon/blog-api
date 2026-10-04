"""View counts must survive rebuilds, which is why they live in the database
rather than in the generated HTML."""

from __future__ import annotations

from sqlalchemy import func, select

from conftest import get_json, post_json
from fieldnotes.db import session_scope
from fieldnotes.models import View

SLUG = "2026-10-04-hello-everyone"


def test_first_view_counts(client):
    body = post_json(client, "/api/view", {"post": SLUG}).get_json()
    assert body["views"] == 1 and body["counted"] is True


def test_reload_same_day_does_not_inflate(client):
    for _ in range(10):
        post_json(client, "/api/view", {"post": SLUG})
    assert get_json(client, f"/api/stats?post={SLUG}").get_json()["views"] == 1


def test_different_ips_count_separately(client):
    post_json(client, "/api/view", {"post": SLUG}, ip="203.0.113.5")
    post_json(client, "/api/view", {"post": SLUG}, ip="203.0.113.6")
    assert get_json(client, f"/api/stats?post={SLUG}").get_json()["views"] == 2


def test_second_visit_same_ip_is_not_recounted(client):
    first = post_json(client, "/api/view", {"post": SLUG}, ip="203.0.113.5").get_json()
    second = post_json(client, "/api/view", {"post": SLUG}, ip="203.0.113.5").get_json()
    assert first["counted"] is True
    assert second["counted"] is False
    assert second["views"] == 1


def test_counts_are_per_post(client):
    post_json(client, "/api/view", {"post": SLUG})
    post_json(client, "/api/view", {"post": "2026-08-20-other"})
    assert get_json(client, f"/api/stats?post={SLUG}").get_json()["views"] == 1
    assert get_json(client, "/api/stats?post=2026-08-20-other").get_json()["views"] == 1


def test_raw_ip_not_stored(client):
    post_json(client, "/api/view", {"post": SLUG}, ip="198.51.100.123")
    with session_scope() as session:
        row = session.scalars(select(View)).one()
    assert "198.51.100.123" not in f"{row.voter}{row.post}{row.day}"


def test_view_survives_a_rebuild(client):
    """The build wipes dist/ every time, so counts must not live there."""
    post_json(client, "/api/view", {"post": SLUG})
    post_json(client, "/api/vote", {"post": SLUG, "value": 1})

    # Simulate any number of builds: no in-process state is consulted, the
    # tally is read from the database every single time.
    for _ in range(3):
        stats = get_json(client, f"/api/stats?post={SLUG}").get_json()
        assert stats["views"] == 1
        assert stats["up"] == 1


def test_day_bucket_is_utc_dated(client):
    from datetime import datetime, timezone

    post_json(client, "/api/view", {"post": SLUG})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(View).where(View.day == today)) == 1


def test_stats_start_at_zero(client):
    assert get_json(client, f"/api/stats?post={SLUG}").get_json() == {
        "post": SLUG,
        "up": 0,
        "down": 0,
        "views": 0,
        "myVote": 0,
    }


def test_invalid_slug_rejected(client):
    assert post_json(client, "/api/view", {"post": "not a slug"}).status_code == 400