"""Schema.

The uniqueness constraints do the real work here: `votes` is keyed by
(post, voter) so a second vote from the same person overwrites the first
instead of inflating the tally, and `views` is keyed by (post, day, voter) so
refreshing a page cannot inflate the count. No read-modify-write races.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        CheckConstraint("value IN (-1, 1)", name="vote_value_valid"),
        Index("ix_votes_post", "post"),
    )

    post: Mapped[str] = mapped_column(String(200), primary_key=True)
    voter: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class View(Base):
    __tablename__ = "views"
    __table_args__ = (Index("ix_views_post_day", "post", "day"),)

    post: Mapped[str] = mapped_column(String(200), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    voter: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )
    voter: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    children: Mapped[list["Comment"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    parent: Mapped["Comment | None"] = relationship(
        back_populates="children", remote_side="Comment.id"
    )


class BlockedVoter(Base):
    """Manual moderation: a voter hash that may not vote or comment."""

    __tablename__ = "blocked_voters"

    voter: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)