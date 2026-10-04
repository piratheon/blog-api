"""Anonymous threaded comments: threading, validation, and spam controls."""

from __future__ import annotations

from sqlalchemy import select

from conftest import get_json, post_json
from fieldnotes.db import session_scope
from fieldnotes.models import Comment

SLUG = "2026-10-04-hello-everyone"


def create(client, body="Nice post", parent_id=None, ip="203.0.113.5", **extra):
    payload = {"post": SLUG, "body": body}
    if parent_id is not None:
        payload["parentId"] = parent_id
    payload.update(extra)
    return post_json(client, "/api/comments", payload, ip=ip)


# -- basics ------------------------------------------------------------------


def test_create_and_list(client):
    r = create(client, "first!")
    assert r.status_code == 201
    assert r.get_json()["comment"]["body"] == "first!"

    listing = get_json(client, f"/api/comments?post={SLUG}").get_json()
    assert len(listing["comments"]) == 1
    assert listing["comments"][0]["body"] == "first!"


def test_body_is_preserved_exactly(client):
    text = "line one\n\nline two with  spaces   and\ttabs"
    create(client, text)
    got = get_json(client, f"/api/comments?post={SLUG}").get_json()["comments"][0]["body"]
    assert got == text


def test_comments_are_scoped_per_post(client):
    create(client, "on one")
    post_json(client, "/api/comments", {"post": "2026-08-20-other", "body": "on two"})
    assert len(get_json(client, f"/api/comments?post={SLUG}").get_json()["comments"]) == 1


def test_empty_body_rejected(client):
    assert create(client, "   ").status_code == 400


def test_overlong_body_rejected(client):
    assert create(client, "x" * 500).status_code == 400


def test_too_many_links_rejected(client):
    body = " ".join(f"https://spam{i}.example" for i in range(4))
    assert create(client, body).status_code == 400


def test_few_links_allowed(client):
    body = "see https://a.example and https://b.example thanks"
    assert create(client, body).status_code == 201


def test_html_is_stored_verbatim_not_interpreted(client):
    """The API returns text; the client must escape it when rendering."""
    payload = "<script>alert(1)</script>"
    create(client, payload)
    got = get_json(client, f"/api/comments?post={SLUG}").get_json()["comments"][0]["body"]
    assert got == payload


# -- threading ----------------------------------------------------------------


def test_reply_links_to_parent(client):
    parent = create(client, "parent").get_json()["comment"]
    child = create(client, "child", parent_id=parent["id"]).get_json()["comment"]
    assert child["parentId"] == parent["id"]


def test_reply_to_reply_is_flattened_one_level(client):
    a = create(client, "root comment").get_json()["comment"]
    b = create(client, "first reply", parent_id=a["id"]).get_json()["comment"]
    c = create(client, "second reply", parent_id=b["id"]).get_json()["comment"]
    assert c["parentId"] == a["id"], "threads stay one level deep"


def test_reply_to_missing_parent_rejected(client):
    assert create(client, "orphan", parent_id=9999).status_code == 400


def test_parent_from_another_post_rejected(client):
    other = post_json(
        client, "/api/comments", {"post": "2026-08-20-other", "body": "elsewhere"}
    ).get_json()["comment"]
    assert create(client, "x", parent_id=other["id"]).status_code == 400


def test_non_integer_parent_rejected(client):
    assert create(client, "x", parent_id="1").status_code == 400
    assert create(client, "x", parent_id=True).status_code == 400


# -- abuse controls -----------------------------------------------------------


def test_honeypot_silently_succeeds(client):
    r = create(client, "buy pills", website="http://spam.example")
    assert r.status_code == 201
    assert r.get_json()["comment"] is None, "bot should not learn it was filtered"
    assert get_json(client, f"/api/comments?post={SLUG}").get_json()["comments"] == []


def test_rate_limit_per_ip(client):
    for _ in range(3):
        assert create(client, "spam").status_code == 201
    assert create(client, "one too many").status_code == 400


def test_rate_limit_is_per_ip_not_global(client):
    for _ in range(3):
        create(client, "spam", ip="203.0.113.5")
    assert create(client, "other person", ip="203.0.113.6").status_code == 201


def test_minimum_interval_enforced(tmp_path):
    from fieldnotes.app import create_app
    from fieldnotes.config import Config
    from fieldnotes.db import reset_engine

    reset_engine()
    cfg = Config(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'rate.db'}",
        vote_pepper="p",
        allowed_origins=("https://blog.example",),
        comment_min_interval_seconds=3600,
        auto_create_schema=True,
    )
    app = create_app(cfg)
    with app.test_client() as c:
        assert create(c, "first").status_code == 201
        assert create(c, "immediately after").status_code == 400
    reset_engine()


def test_punctuation_soup_rejected(client):
    assert create(client, "!!!!!!!! ???? ....").status_code == 400


def test_unicode_is_preserved(client):
    create(client, "شكرا 🙏 — nice")
    got = get_json(client, f"/api/comments?post={SLUG}").get_json()["comments"][0]["body"]
    assert got == "شكرا 🙏 — nice"


# -- moderation ---------------------------------------------------------------


def test_moderation_holds_comment(tmp_path):
    from fieldnotes.app import create_app
    from fieldnotes.config import Config
    from fieldnotes.db import reset_engine

    reset_engine()
    cfg = Config(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'mod.db'}",
        vote_pepper="p",
        allowed_origins=("https://blog.example",),
        comments_require_moderation=True,
        comment_min_interval_seconds=0,
        auto_create_schema=True,
    )
    app = create_app(cfg)
    with app.test_client() as c:
        r = create(c, "needs review")
        assert r.get_json()["moderated"] is True
        assert r.get_json()["comment"] is None
        assert get_json(c, f"/api/comments?post={SLUG}").get_json()["comments"] == []

        with session_scope() as session:
            row = session.scalars(select(Comment)).one()
            assert row.status == "pending"
            row_id = row.id
            row.status = "published"

        assert len(get_json(c, f"/api/comments?post={SLUG}").get_json()["comments"]) == 1
    reset_engine()