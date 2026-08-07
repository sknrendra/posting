"""Test bootstrap: points DATABASE_URL at a fresh temp sqlite file and runs
`alembic upgrade head` against it BEFORE any `app.*` module is imported, since
app.config.settings is a module-level singleton read once at import time.
This must stay at module scope (not inside a fixture) so it runs during
conftest.py's own import, ahead of test module collection.
"""

import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="posting_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_DIR / 'test.db'}"

from alembic import command
from alembic.config import Config

_alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
_alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
command.upgrade(_alembic_cfg, "head")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal
from app.main import app
from app.services import account_service, auth_service, invoice_settings_service

# Mirrors entrypoint.sh's `alembic upgrade head && python -m app.seed` so tests
# see the same fully-seeded chart of accounts + invoice_settings mappings a
# real deployment would have.
with SessionLocal() as _seed_db:
    account_service.seed_default_chart_of_accounts(_seed_db)
    invoice_settings_service.backfill_default_mappings(_seed_db)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_slate(db):
    """Invoices/journal entries/users are test-created churn; accounts and the
    invoice_settings singleton are migration-seeded fixtures left in place."""
    yield
    # invoices <-> journal_entries have FKs in both directions (invoice_id, and
    # journal_entry_id/void_journal_entry_id), plus journal_entries.reverses_entry_id
    # self-references — null out every cross-reference before deleting either table.
    db.execute(text("UPDATE invoices SET journal_entry_id = NULL, void_journal_entry_id = NULL"))
    db.execute(text("UPDATE journal_entries SET reverses_entry_id = NULL"))
    db.execute(text("UPDATE journal_lines SET reconciliation_id = NULL"))
    db.execute(text("DELETE FROM reconciliations"))
    db.execute(text("DELETE FROM journal_lines"))
    db.execute(text("DELETE FROM journal_entries"))
    db.execute(text("DELETE FROM invoice_lines"))
    db.execute(text("DELETE FROM invoices"))
    db.execute(text("DELETE FROM invoice_number_counters"))
    db.execute(text("DELETE FROM sessions"))
    db.execute(text("DELETE FROM users"))
    db.commit()


@pytest.fixture()
def test_user(db):
    return auth_service.create_user(db, "test@example.com", "password123")


@pytest.fixture()
def admin_user(db):
    return auth_service.create_user(db, "admin@example.com", "password123", is_admin=True)


@pytest.fixture()
def client(db, test_user):
    token = auth_service.create_session(db, test_user)
    c = TestClient(app)
    c.cookies.set("posting_session", token)
    return c


@pytest.fixture()
def admin_client(db, admin_user):
    token = auth_service.create_session(db, admin_user)
    c = TestClient(app)
    c.cookies.set("posting_session", token)
    return c
