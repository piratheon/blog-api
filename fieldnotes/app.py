"""Flask application: read counts, votes, and anonymous threaded comments.

No accounts. A voter is identified by a peppered hash of their IP address, which
the schema's uniqueness constraints turn into one-vote-per-post and
one-view-per-day without any read-modify-write logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import click
from flask import Flask, Response, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.exceptions import HTTPException

from .config import Config, describe_available_db_vars
from .db import create_schema, init_engine, session_scope
from .identity import identify
from .models import BlockedVoter, Comment, Vote, View, utcnow
from .validation import Invalid, clean_body, clean_slug, is_spammy

log = logging.getLogger("fieldnotes")


def create_app(config: Config | None = None) -> Flask:
    config = config or Config()
    app = Flask(__name__)
    app.config["FLNOTES"] = config
    app.config["MAX_CONTENT_LENGTH"] = config.max_content_length

    init_engine(config)
    if config.auto_create_schema:
        create_schema()

    for problem in config.warn_if_insecure():
        log.warning("configuration: %s", problem)

    # ---------------------------------------------------------------- plumbing

    @app.after_request
    def _cors(response: Response) -> Response:
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") in config.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Max-Cent"] = "600"
            response.headers.add("Vary", "Origin")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.before_request
    def _preflight() -> tuple[str, int] | None:
        # Short-circuit preflight before routing, so an OPTIONS catch-all rule
        # cannot shadow the real endpoints or turn unknown paths into 405s.
        # after_request still runs, so CORS headers are attached.
        if request.method == "OPTIONS":
            return ("", 204)
        return None

    @app.errorhandler(Invalid)
    def _invalid(err: Invalid) -> tuple[Response, int]:
        return jsonify({"error": str(err)}), 400

    @app.errorhandler(Exception)
    def _boom(err: Exception) -> tuple[Response, int]:
        # Werkzeug's HTTPException already knows its status code; do not let a
        # blanket handler turn a 404 or 405 into a 500.
        if isinstance(err, HTTPException):
            # err.name is the short label ("Not Found"), not the long
            # description Werkzeug generates for HTML responses.
            return jsonify({"error": (err.name or "error").lower()}), err.code or 500
        log.exception("unhandled error: %s", err)
        return jsonify({"error": "internal error"}), 500

    def _body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise Invalid("expected a JSON object")
        return data

    def _identity():
        return identify(request, config.vote_pepper)

    def _blocked(session: Session, voter: str) -> bool:
        return session.get(BlockedVoter, voter) is not None

    def _tally(session: Session, post: str) -> dict:
        up = session.scalar(
            select(func.count()).select_from(Vote).where(Vote.post == post, Vote.value == 1)
        )
        down = session.scalar(
            select(func.count()).select_from(Vote).where(Vote.post == post, Vote.value == -1)
        )
        views = session.scalar(select(func.count()).select_from(View).where(View.post == post))
        return {"post": post, "up": up or 0, "down": down or 0, "views": views or 0}

    # ----------------------------------------------------------------- routes

    @app.get("/api/health")
    def health() -> Response:
        return jsonify(
            {
                "ok": True,
                "database": {
                    "sqlite": config.is_sqlite,
                    # Which variable was used, so a misconfigured deploy is
                    # obvious from the outside without reading Vercel logs.
                    "source": config.database_source,
                    "detected": describe_available_db_vars(),
                },
                "problems": config.warn_if_insecure(),
            }
        )

    @app.get("/api/stats")
    def stats() -> Response:
        post = clean_slug(request.args.get("post"))
        with session_scope() as session:
            mine = session.get(Vote, (post, _identity().voter))
            payload = _tally(session, post)
        payload["myVote"] = mine.value if mine else 0
        return jsonify(payload)

    @app.post("/api/view")
    def record_view() -> Response:
        post = clean_slug(_body().get("post"))
        who = _identity()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with session_scope() as session:
            if _blocked(session, who.voter):
                raise Invalid("blocked")
            already = session.get(View, (post, today, who.voter))
            counted = already is not None
            if not counted:
                try:
                    session.add(View(post=post, day=today, voter=who.voter))
                    session.flush()
                except IntegrityError:
                    # Concurrent duplicate from the same IP: the PK did its job.
                    session.rollback()
                    counted = True
            payload = _tally(session, post)

        payload["counted"] = not counted
        return jsonify(payload)

    @app.post("/api/vote")
    def vote() -> Response:
        post = clean_slug(_body().get("post"))
        raw = _body().get("value", 0)
        if raw not in (1, -1, 0):
            raise Invalid("value must be 1, -1 or 0")
        value = int(raw)
        who = _identity()

        with session_scope() as session:
            if _blocked(session, who.voter):
                raise Invalid("blocked")

            existing = session.get(Vote, (post, who.voter))
            if existing is None:
                if value != 0:
                    try:
                        session.add(Vote(post=post, voter=who.voter, value=value))
                        session.flush()
                    except IntegrityError:
                        # A concurrent vote from the same IP won the race; the
                        # primary key did its job, so treat this as an update.
                        session.rollback()
                        existing = session.get(Vote, (post, who.voter))
                        if existing is not None:
                            existing.value = value
            elif value == 0:
                session.delete(existing)  # 0 clears an existing vote
            else:
                existing.value = value
                session.flush()

            payload = _tally(session, post)
        payload["myVote"] = value
        return jsonify(payload)

    @app.get("/api/comments")
    def list_comments() -> Response:
        post = clean_slug(request.args.get("post"))
        with session_scope() as session:
            rows = session.scalars(
                select(Comment)
                .where(Comment.post == post, Comment.status == "published")
                .order_by(Comment.created_at.asc())
            ).all()
            payload = [serialize_comment(row) for row in rows]
        return jsonify({"post": post, "comments": payload})

    @app.post("/api/comments")
    def create_comment() -> Response:
        data = _body()
        post = clean_slug(data.get("post"))
        who = _identity()

        # Honeypot: a field only a bot fills in. Answer as if it worked.
        if data.get("website"):
            return jsonify({"ok": True, "comment": None, "moderated": True}), 201

        text = clean_body(
            data.get("body"),
            max_length=config.comment_max_length,
            max_links=config.comment_max_links,
        )
        reason = is_spammy(text)
        if reason:
            raise Invalid(reason)

        parent_id = data.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, int) or isinstance(parent_id, bool):
                raise Invalid("parentId must be an integer or null")

        status = "pending" if config.comments_require_moderation else "published"

        with session_scope() as session:
            if _blocked(session, who.voter):
                raise Invalid("blocked")

            _enforce_rate_limit(session, who.voter, now=utcnow())

            if parent_id is not None:
                parent = session.get(Comment, parent_id)
                if parent is None or parent.post != post:
                    raise Invalid("parent comment not found on this post")
                if parent.parent_id is not None:
                    # Keep threads exactly one level deep; replying to a reply
                    # attaches to the same parent instead of nesting forever.
                    parent_id = parent.parent_id

            comment = Comment(
                post=post,
                parent_id=parent_id,
                voter=who.voter,
                body=text,
                status=status,
            )
            session.add(comment)
            session.flush()
            result = serialize_comment(comment)

        return (
            jsonify({"ok": True, "comment": result if status == "published" else None,
                     "moderated": status == "pending"}),
            201,
        )

    def _enforce_rate_limit(session: Session, voter: str, *, now: datetime) -> None:
        window = timedelta(hours=config.comment_rate_window_hours)
        recent = session.scalar(
            select(func.count())
            .select_from(Comment)
            .where(
                Comment.voter == voter,
                Comment.created_at > now - window,
                Comment.status != "rejected",
            )
        )
        if (recent or 0) >= config.comment_rate_limit:
            raise Invalid(
                f"comment limit reached; try again in {config.comment_rate_window_hours}h"
            )

        last = session.scalar(
            select(func.max(Comment.created_at))
            .select_from(Comment)
            .where(Comment.voter == voter)
        )
        if last is not None:
            gap = (now - last).total_seconds()
            if gap < config.comment_min_interval_seconds:
                raise Invalid("posting too quickly")

    # ------------------------------------------------------------------- cli

    @app.cli.command("init-db")
    def init_db() -> None:
        """Create any missing tables."""
        create_schema()
        click.echo(f"schema ready on {'sqlite' if config.is_sqlite else 'postgres'}")

    @app.cli.command("block")
    @click.argument("voter_hash")
    @click.option("--reason", default="")
    def block(voter_hash: str, reason: str) -> None:
        """Block a voter hash from voting and commenting."""
        with session_scope() as session:
            session.add(BlockedVoter(voter=voter_hash.lower(), reason=reason))
        click.echo(f"blocked {voter_hash}")

    @app.cli.command("unblock")
    @click.argument("voter_hash")
    def unblock(voter_hash: str) -> None:
        with session_scope() as session:
            row = session.get(BlockedVoter, voter_hash.lower())
            if row is None:
                click.echo("not blocked")
                return
            session.delete(row)
        click.echo(f"unblocked {voter_hash}")

    @app.cli.command("approve")
    @click.argument("comment_id", type=int)
    def approve(comment_id: int) -> None:
        """Publish a comment held for moderation."""
        with session_scope() as session:
            row = session.get(Comment, comment_id)
            if row is None:
                click.echo("no such comment")
                return
            row.status = "published"
        click.echo(f"published comment {comment_id}")

    @app.cli.command("pending")
    def pending() -> None:
        """List comments awaiting moderation."""
        with session_scope() as session:
            rows = session.scalars(
                select(Comment).where(Comment.status == "pending").order_by(Comment.id)
            ).all()
        for row in rows:
            click.echo(f"{row.id}\t{row.post}\t{row.body[:70]!r}")

    return app


def serialize_comment(row: Comment) -> dict:
    return {
        "id": row.id,
        "post": row.post,
        "parentId": row.parent_id,
        "body": row.body,
        "createdAt": row.created_at.isoformat() + "Z" if row.created_at else None,
        "status": row.status,
    }