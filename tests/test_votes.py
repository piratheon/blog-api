"""Votes: one per IP per post, re-votable, and never storing the raw address."""

from __future__ import annotations

from sqlalchemy import select

from conftest import fake_request, get_json, post_json
from fieldnotes.db import session_scope
from fieldnotes.models import Vote

SLUG = "2026-10-04-hello-everyone"


def test_vote_records(client):
    r = post_json(client, "/api/vote", {"post": SLUG, "value": 1})
    assert r.status_code == 200
    body = r.get_json()
    assert body["up"] == 1 and body["down"] == 0 and body["myVote"] == 1


def test_same_ip_cannot_inflate(client):
    for _ in range(5):
        post_json(client, "/api/vote", {"post": SLUG, "value": 1})
    assert get_json(client, f"/api/stats?post={SLUG}").get_json()["up"] == 1


def test_second_ip_counts_separately(client):
    post_json(client, "/api/vote", {"post": SLUG, "value": 1}, ip="203.0.113.5")
    post_json(client, "/api/vote", {"post": SLUG, "value": 1}, ip="203.0.113.6")
    assert get_json(client, f"/api/stats?post={SLUG}").get_json()["up"] == 2


def test_revote_switches_direction(client):
    post_json(client, "/api/vote", {"post": SLUG, "value": 1}, ip="203.0.113.5")
    body = post_json(client, "/api/vote", {"post": SLUG, "value": -1}, ip="203.0.113.5").get_json()
    assert body["up"] == 0 and body["down"] == 1, "vote should have flipped, not added"


def test_vote_zero_clears(client):
    post_json(client, "/api/vote", {"post": SLUG, "value": 1})
    body = post_json(client, "/api/vote", {"post": SLUG, "value": 0}).get_json()
    assert body["up"] == 0 and body["down"] == 0


def test_invalid_value_rejected(client):
    assert post_json(client, "/api/vote", {"post": SLUG, "value": 5}).status_code == 400


def test_invalid_slug_rejected(client):
    assert post_json(client, "/api/vote", {"post": "../etc/passwd", "value": 1}).status_code == 400


def test_stats_reflects_my_vote_per_ip(client):
    post_json(client, "/api/vote", {"post": SLUG, "value": 1}, ip="203.0.113.5")
    assert get_json(client, f"/api/stats?post={SLUG}", ip="203.0.113.5").get_json()["myVote"] == 1
    assert get_json(client, f"/api/stats?post={SLUG}", ip="203.0.113.9").get_json()["myVote"] == 0


def test_raw_ip_is_never_stored(client):
    post_json(client, "/api/vote", {"post": SLUG, "value": 1}, ip="198.51.100.77")
    with session_scope() as session:
        stored = session.scalars(select(Vote)).all()
    assert stored, "expected a vote row"
    blob = " ".join([stored[0].voter, stored[0].post])
    assert "198.51.100.77" not in blob
    assert len(stored[0].voter) == 64, "expected a sha256 hex digest"


def test_different_pepper_yields_different_hash():
    from fieldnotes.identity import identify

    req = fake_request("203.0.113.5")
    a = identify(req, "pepper-one").voter
    b = identify(req, "pepper-two").voter
    assert a != b


def test_forwarded_header_takes_first_entry(client):
    """A proxy chain must use the client address, not the last hop."""
    from fieldnotes.identity import client_ip

    req = fake_request("203.0.113.5, 70.41.3.18, 150.172.238.178", remote="10.0.0.1")
    assert client_ip(req) == "203.0.113.5"


def test_blocked_voter_cannot_vote(client):
    from fieldnotes.identity import identify
    from fieldnotes.models import BlockedVoter

    voter = identify(fake_request("203.0.113.66"), "test-pepper").voter
    with session_scope() as session:
        session.add(BlockedVoter(voter=voter, reason="test"))

    assert post_json(client, "/api/vote", {"post": SLUG, "value": 1}, ip="203.0.113.66").status_code == 400