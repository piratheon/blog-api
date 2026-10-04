"""Database URL autodetection.

Vercel injects different variable names depending on how the database was
provisioned, so the app accepts all of them. These tests pin the real shapes.
"""

from __future__ import annotations

from fieldnotes.config import (
    Config,
    describe_available_db_vars,
    resolve_database_url,
)


def test_prefers_database_url():
    env = {
        "DATABASE_URL": "postgresql://u:p@db.main.neon.tech/neondb",
        "POSTGRES_URL": "postgresql://other@elsewhere/db",
    }
    url, source = resolve_database_url(env)
    assert source == "DATABASE_URL"
    assert url.startswith("postgresql://u:p@db.main.neon.tech/neondb")


def test_falls_back_to_postgres_url():
    env = {"POSTGRES_URL": "postgresql://u:p@db.zeeruh.app/verceldb"}
    url, source = resolve_database_url(env)
    assert source == "POSTGRES_URL"
    assert "verceldb" in url


def test_accepts_prisma_style_url():
    env = {"POSTGRES_URL_PRISMA": "postgresql://u:p@host/db?schema=public"}
    url, source = resolve_database_url(env)
    assert source == "POSTGRES_URL_PRISMA"
    assert "schema=public" in url


def test_assembles_from_discrete_pg_parts():
    env = {
        "PGHOST": "db.example.com",
        "PGPORT": "5432",
        "PGDATABASE": "appdb",
        "PGUSER": "appuser",
        "PGPASSWORD": "s3cret",
    }
    url, source = resolve_database_url(env)
    assert source == "PGHOST+PGPORT+PGDATABASE+PGUSER+PGPASSWORD"
    assert url == (
        "postgresql://appuser:s3cret@db.example.com:5432/appdb?sslmode=require"
    )


def test_pg_parts_require_host_and_database():
    assert resolve_database_url({"PGUSER": "u", "PGPASSWORD": "p"})[1] == "sqlite-fallback"
    assert resolve_database_url({"PGHOST": "h"})[1] == "sqlite-fallback"


def test_pg_parts_respect_pgsslmode():
    env = {
        "PGHOST": "h",
        "PGDATABASE": "d",
        "PGUSER": "u",
        "PGPASSWORD": "p",
        "PGSSLMODE": "disable",
    }
    assert "sslmode=disable" in resolve_database_url(env)[0]


def test_rewrites_legacy_postgres_scheme():
    """SQLAlchemy 2 rejects postgres://, and some providers still emit it."""
    url, _ = resolve_database_url({"DATABASE_URL": "postgres://u:p@host/db"})
    assert url.startswith("postgresql://")


def test_adds_sslmode_when_absent():
    url, _ = resolve_database_url({"DATABASE_URL": "postgresql://u:p@host/db"})
    assert "sslmode=require" in url


def test_preserves_existing_sslmode():
    url, _ = resolve_database_url({"DATABASE_URL": "postgresql://u:p@host/db?sslmode=no-verify"})
    assert "sslmode=require" not in url
    assert "sslmode=no-verify" in url


def test_password_with_reserved_characters_is_encoded():
    env = {"PGHOST": "h", "PGDATABASE": "d", "PGUSER": "u@corp", "PGPASSWORD": "p@ss/word"}
    url, _ = resolve_database_url(env)
    assert "u%40corp:p%40ss%2Fword@" in url


def test_blank_values_are_ignored():
    env = {"DATABASE_URL": "   ", "POSTGRES_URL": "postgresql://u:p@host/db"}
    assert resolve_database_url(env)[1] == "POSTGRES_URL"


def test_no_variables_falls_back_to_sqlite():
    url, source = resolve_database_url({})
    assert source == "sqlite-fallback"
    assert url.startswith("sqlite+pysqlite:///")


def test_describe_lists_what_was_found():
    """A diagnostic: it reports everything present, not just what won."""
    env = {"DATABASE_URL": "postgresql://u:p@h/d", "PGHOST": "ignored"}
    assert describe_available_db_vars(env) == ["DATABASE_URL", "PGHOST"]


def test_describe_lists_pg_parts():
    env = {"PGHOST": "h", "PGDATABASE": "d"}
    assert describe_available_db_vars(env) == ["PGHOST+PGDATABASE"]


def test_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db")
    config = Config()
    assert config.database_source == "DATABASE_URL"
    assert config.is_sqlite is False


def test_health_reports_the_detected_source(client, monkeypatch):
    body = client.get("/api/health").get_json()
    assert "database" in body
    assert "source" in body["database"]
    assert isinstance(body["database"]["detected"], list)

def test_postgres_driver_is_installed():
    """SQLAlchemy only fails at connect time without a driver, which on Vercel
    means a cold-start 500 rather than an obvious import error."""
    import importlib.util

    assert importlib.util.find_spec("psycopg") is not None, (
        "psycopg must be a dependency: a detected Postgres URL cannot connect without it"
    )


def test_engine_builds_for_a_detected_postgres_url(monkeypatch):
    """Creating the engine must not require reaching the database."""
    from fieldnotes.db import init_engine, reset_engine

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example.invalid:5432/appdb")
    reset_engine()
    try:
        engine = init_engine(Config())
        assert engine.dialect.name == "postgresql"
    finally:
        reset_engine()
