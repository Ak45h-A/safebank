"""
database.py — Database connection for SafeBank
- On Render (production): uses PostgreSQL via DATABASE_URL environment variable
- On your laptop (local): uses SQLite as before
"""
import os
import sqlite3

# Check if we have a PostgreSQL URL set (Render sets this automatically)
DATABASE_URL = os.environ.get("DATABASE_URL")
USING_POSTGRES = DATABASE_URL is not None

if USING_POSTGRES:
    import psycopg2
    import psycopg2.extras
    print("[✓] Using PostgreSQL (Neon cloud database)")
else:
    print("[✓] Using SQLite (local development)")

DB_FILE = "safebank_web.db"  # Only used for SQLite


def get_connection():
    """
    Returns a database connection.
    Automatically uses PostgreSQL on Render, SQLite locally.
    """
    if USING_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def fetchone_as_dict(cursor, query, params=()):
    """Execute query and return one row as a dict (works for both DBs)."""
    cursor.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        return None
    if USING_POSTGRES:
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    else:
        return dict(row)


def fetchall_as_dict(cursor, query, params=()):
    """Execute query and return all rows as list of dicts (works for both DBs)."""
    cursor.execute(query, params)
    rows = cursor.fetchall()
    if USING_POSTGRES:
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    else:
        return [dict(row) for row in rows]


def placeholder():
    """
    SQL placeholder differs between databases:
    SQLite uses ?  →  INSERT INTO t VALUES (?, ?)
    PostgreSQL uses %s  →  INSERT INTO t VALUES (%s, %s)
    """
    return "%s" if USING_POSTGRES else "?"


def adapt_query(query):
    """Convert SQLite ? placeholders to PostgreSQL %s placeholders."""
    if USING_POSTGRES:
        return query.replace("?", "%s")
    return query


def initialize_database():
    conn = get_connection()
    c    = conn.cursor()

    if USING_POSTGRES:
        # PostgreSQL syntax — uses SERIAL or TEXT for IDs
        # TEXT DEFAULT now() becomes DEFAULT NOW()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       TEXT PRIMARY KEY,
                full_name     TEXT NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                phone         TEXT,
                is_admin      INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                balance     REAL DEFAULT 0.0,
                status      TEXT DEFAULT 'active'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                txn_id        TEXT PRIMARY KEY,
                account_id    TEXT NOT NULL,
                txn_type      TEXT NOT NULL,
                amount        REAL NOT NULL,
                balance_after REAL NOT NULL,
                description   TEXT,
                merchant      TEXT,
                location      TEXT,
                fraud_score   INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'success',
                timestamp     TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS fraud_reports (
                report_id     TEXT PRIMARY KEY,
                txn_id        TEXT NOT NULL,
                account_id    TEXT NOT NULL,
                user_id       TEXT NOT NULL,
                reason        TEXT NOT NULL,
                evidence      TEXT,
                status        TEXT DEFAULT 'pending',
                fraud_score   INTEGER DEFAULT 0,
                submitted_at  TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
                reviewed_at   TEXT,
                reviewed_by   TEXT,
                admin_notes   TEXT,
                refund_txn_id TEXT
            )
        """)
    else:
        # SQLite syntax (local development)
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       TEXT PRIMARY KEY,
                full_name     TEXT NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                phone         TEXT,
                is_admin      INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                balance     REAL DEFAULT 0.0,
                status      TEXT DEFAULT 'active',
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                txn_id        TEXT PRIMARY KEY,
                account_id    TEXT NOT NULL,
                txn_type      TEXT NOT NULL,
                amount        REAL NOT NULL,
                balance_after REAL NOT NULL,
                description   TEXT,
                merchant      TEXT,
                location      TEXT,
                fraud_score   INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'success',
                timestamp     TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(account_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS fraud_reports (
                report_id     TEXT PRIMARY KEY,
                txn_id        TEXT NOT NULL,
                account_id    TEXT NOT NULL,
                user_id       TEXT NOT NULL,
                reason        TEXT NOT NULL,
                evidence      TEXT,
                status        TEXT DEFAULT 'pending',
                fraud_score   INTEGER DEFAULT 0,
                submitted_at  TEXT DEFAULT (datetime('now')),
                reviewed_at   TEXT,
                reviewed_by   TEXT,
                admin_notes   TEXT,
                refund_txn_id TEXT
            )
        """)

    conn.commit()
    conn.close()
    print("[✓] Database ready.")


if __name__ == "__main__":
    initialize_database()
