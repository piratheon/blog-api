"""Configuration, read once from the environment.

Every value has a safe default so the service runs locally with no setup, but
the production-critical ones (DATABASE_URL, VOTE_PEPPER) have no default you
would want in production.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# In priority order. Which one Vercel injects depends on how the database was
# provisioned: Vercel Postgres/Neon set DATABASE_URL, older Vercel Postgres
# projects set POSTGRES_URL, and a raw PG* set is exported by several add-ons.
FULL_URL_VARS = (
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_URL_PRISMA",
    "POSTGRES_PRISMA_URL",
    "PG_URL",
)

# Discreet parts, assembled when no single URL variable is present.
PG_PART_VARS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")


def _normalise(url: str) -> str:
    """Make a provider URL usable by SQLAlchemy 2.x.

    Two things providers get wrong: the legacy ``postgres://`` scheme, which
    SQLAlchemy 2 rejects outright, and a missing ``sslmode``, which managed
    Postgres (Neon, Vercel, Supabase, RDS) requires to connect at all.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _from_parts(env) -> str | None:
    """Assemble a URL from PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD."""
    parts = {name: (env.get(name) or "").strip() for name in PG_PART_VARS}
    if not parts["PGHOST"] or not parts["PGDATABASE"]:
        return None

    user = quote(parts["PGUSER"], safe="") if parts["PGUSER"] else ""
    password = quote(parts["PGPASSWORD"], safe="") if parts["PGPASSWORD"] else ""
    credentials = ""
    if user and password:
        credentials = f"{user}:{password}@"
    elif user:
        credentials = f"{user}@"

    port = f":{parts['PGPORT']}" if parts["PGPORT"] else ""
    sslmode = (env.get("PGSSLMODE") or "").strip() or "require"
    query = urlencode({"sslmode": sslmode})
    return _normalise(
        f"postgresql://{credentials}{parts['PGHOST']}{port}/{parts['PGDATABASE']}?{query}"
    )


def resolve_database_url(env=None) -> tuple[str, str]:
    """Return ``(url, source)`` where source names the variable it came from.

    Falls back to a local SQLite file so the project runs with no setup; on
    Vercel that fallback is reported as a problem by ``warn_if_insecure``.
    """
    env = os.environ if env is None else env

    for name in FULL_URL_VARS:
        value = (env.get(name) or "").strip()
        if value:
            return _normalise(value), name

    assembled = _from_parts(env)
    if assembled:
        return assembled, "+".join(PG_PART_VARS)

    return "sqlite+pysqlite:///" + _default_sqlite_path(), "sqlite-fallback"


def describe_available_db_vars(env=None) -> list[str]:
    """Which recognised database variables are set, for diagnostics."""
    env = os.environ if env is None else env
    names = [n for n in FULL_URL_VARS if (env.get(n) or "").strip()]
    if any((env.get(n) or "").strip() for n in PG_PART_VARS):
        names.append("+".join(n for n in PG_PART_VARS if (env.get(n) or "").strip()))
    return names


@dataclass
class Config:
    # -- storage ------------------------------------------------------------
    # Detected from whichever database variable the platform injected. SQLite
    # locally; Postgres on Vercel, because the function filesystem is ephemeral.
    database_url: str = field(default_factory=lambda: resolve_database_url()[0])
    database_source: str = field(default_factory=lambda: resolve_database_url()[1])

    # -- identity -----------------------------------------------------------
    # Mixed into the voter hash. Rotating it anonymises everyone retroactively,
    # at the cost of resetting one-vote-per-person.
    vote_pepper: str = field(
        default_factory=lambda: os.environ.get("VOTE_PEPPER", "dev-only-insecure-pepper")
    )

    # -- CORS ---------------------------------------------------------------
    # Comma-separated list of allowed origins, e.g. https://piratheon.github.io
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip().rstrip("/")
            for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
            if o.strip()
        )
    )

    # -- abuse limits -------------------------------------------------------
    comment_rate_limit: int = field(default_factory=lambda: _int("COMMENT_RATE_LIMIT", 5))
    comment_rate_window_hours: int = field(
        default_factory=lambda: _int("COMMENT_RATE_WINDOW_HOURS", 24)
    )
    comment_min_interval_seconds: int = field(
        default_factory=lambda: _int("COMMENT_MIN_INTERVAL_SECONDS", 20)
    )
    comment_max_length: int = field(default_factory=lambda: _int("COMMENT_MAX_LENGTH", 5000))
    comment_max_links: int = field(default_factory=lambda: _int("COMMENT_MAX_LINKS", 3))
    comments_require_moderation: bool = field(
        default_factory=lambda: _bool("COMMENTS_REQUIRE_MODERATION", False)
    )

    max_content_length: int = field(default_factory=lambda: _int("MAX_CONTENT_LENGTH", 64 * 1024))

    # -- schema -------------------------------------------------------------
    auto_create_schema: bool = field(
        default_factory=lambda: _bool("AUTO_CREATE_SCHEMA", False)
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def warn_if_insecure(self) -> list[str]:
        """Problems worth refusing to start in production."""
        problems: list[str] = []
        if self.vote_pepper == "dev-only-insecure-pepper":
            problems.append("VOTE_PEPPER is unset; using the public dev default")
        if self.is_sqlite and os.environ.get("VERCEL"):
            problems.append(
                "no Postgres URL detected on Vercel (looked for "
                + ", ".join(FULL_URL_VARS)
                + " or "
                + "/".join(PG_PART_VARS)
                + "); SQLite is ephemeral here and every count will be lost"
            )
        if not self.allowed_origins:
            problems.append("ALLOWED_ORIGINS is empty; every browser request will be blocked")
        return problems


def _default_sqlite_path() -> str:
    import os as _os
    import tempfile

    base = _os.environ.get("FLNOTES_DATA_DIR") or _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data"
    )
    try:
        _os.makedirs(base, exist_ok=True)
    except OSError:
        base = tempfile.mkdtemp(prefix="flnotes-")
    return _os.path.join(base, "fieldnotes.db")