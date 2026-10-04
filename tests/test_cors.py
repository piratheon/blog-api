"""CORS and baseline HTTP hardening.

The blog is on a different origin from the API, so CORS is the only thing
letting the browser read the response -- and the only thing that must NOT let a
random site do so.
"""

from __future__ import annotations

from conftest import ORIGIN, get_json, post_json

SLUG = "2026-10-04-hello-everyone"


def test_allowed_origin_is_echoed(client):
    r = get_json(client, f"/api/stats?post={SLUG}")
    assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Origin" in r.headers.get("Vary", "")


def test_foreign_origin_gets_no_cors_header(client):
    r = get_json(client, f"/api/stats?post={SLUG}", origin="https://evil.example")
    assert "Access-Control-Allow-Origin" not in r.headers


def test_trailing_slash_origin_matches(client):
    # The raw Origin is echoed back, which is what the browser compares against.
    r = get_json(client, f"/api/stats?post={SLUG}", origin="https://blog.example/")
    assert r.headers["Access-Control-Allow-Origin"] == "https://blog.example/"


def test_preflight_is_answered(client):
    r = client.options("/api/vote", headers={"Origin": ORIGIN})
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert "Content-Type" in r.headers["Access-Control-Allow-Headers"]


def test_no_origin_header_still_works_for_server_side_calls(client):
    r = client.get(f"/api/stats?post={SLUG}")
    assert r.status_code == 200


def test_nosniff_is_set(client):
    assert get_json(client, f"/api/stats?post={SLUG}").headers["X-Content-Type-Options"] == "nosniff"


def test_unknown_route_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not found"


def test_bad_json_body_rejected(client):
    r = client.post("/api/vote", data="not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_missing_post_field_rejected(client):
    assert post_json(client, "/api/vote", {"value": 1}).status_code == 400


def test_health_reports_config_problems(client):
    body = client.get("/api/health").get_json()
    assert body["ok"] is True
    assert isinstance(body["problems"], list)