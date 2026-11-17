"""SQLite storage for companies, contacts, proposals, and settings."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "outreach.db"

FROM_EMAIL = "hr@talentcrafters.co.za"
FROM_NAME = "Talent Crafters Recruitment"

DEFAULT_ADVANTAGES = "\n".join(
    [
        "A vetted candidate network, with screening before anyone reaches your team",
        "First shortlist within 10 working days of engagement",
        "Contingency fee of 10% to 15% of annual cost-to-company, payable only when someone is placed",
        "Replacement guarantee if a placed candidate leaves within 30 to 90 days, on the terms in the proposal",
        "Retained search for executive roles, and custom pricing for volume hiring",
        "Recruitment in South Africa and internationally",
    ]
)

DEFAULTS = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": "587",
    "smtp_user": FROM_EMAIL,
    "smtp_password": "",
    "imap_host": "imap.gmail.com",
    "imap_port": "993",
    "postal_address": "Leeuwpoort Street & Kaap St, CBD, Boksburg, 1460",
    "daily_cap": "12",
    "advantages": DEFAULT_ADVANTAGES,
    "confirmed": "0",
    "paused": "0",
    "hunter_api_key": "",
    "hunter_auto_send": "0",
    "adzuna_app_id": "",
    "adzuna_app_key": "",
    "public_base_url": "",
    "smtp_error": "",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL UNIQUE,
  website TEXT,
  market TEXT NOT NULL DEFAULT 'international',
  contact_checked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  market TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  discovered_at TEXT NOT NULL,
  UNIQUE(company_id, title, location, source)
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  auto_send INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(company_id, email)
);

CREATE TABLE IF NOT EXISTS outreach (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  role_id INTEGER REFERENCES roles(id) ON DELETE SET NULL,
  to_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body_text TEXT NOT NULL,
  body_html TEXT NOT NULL,
  status TEXT NOT NULL,
  unsub_token TEXT NOT NULL UNIQUE,
  error TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  UNIQUE(company_id, to_email)
);

CREATE TABLE IF NOT EXISTS suppressions (
  email TEXT PRIMARY KEY,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_roles_company ON roles(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_activity_id ON activity(id DESC);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


@contextmanager
def db():
    conn = _connection()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.execute(
            "DELETE FROM contacts WHERE lower(email) LIKE '%@w3.org' OR lower(email) LIKE '%@schema.org'"
        )
        conn.execute(
            "UPDATE companies SET website='' "
            "WHERE lower(website) LIKE '%schema.org%' OR lower(website) LIKE '%w3.org%'"
        )
        conn.execute(
            "DELETE FROM contacts WHERE instr(lower(email), 'u003e') > 0 "
            "OR lower(email) LIKE 'errors@%' OR lower(email) LIKE 'error@%'"
        )
        conn.execute(
            "UPDATE contacts SET auto_send=1 "
            "WHERE auto_send=0 AND source IN ('company_site', 'job_post', 'manual')"
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(outreach)")}
        if "edited" not in columns:
            conn.execute("ALTER TABLE outreach ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    found = {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM settings")
    }
    merged = dict(DEFAULTS)
    merged.update(found)
    return merged


def set_settings(conn: sqlite3.Connection, updates: dict[str, str]) -> None:
    for key, value in updates.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def log(conn: sqlite3.Connection, level: str, message: str) -> None:
    conn.execute(
        "INSERT INTO activity (level, message, created_at) VALUES (?, ?, ?)",
        (level, message[:500], now_iso()),
    )
    conn.execute(
        "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 300)"
    )
