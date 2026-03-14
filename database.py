"""
database.py — Database connection for SafeBank
- On Render (production): uses PostgreSQL via DATABASE_URL (using pg8000)
- On your laptop (local): uses SQLite
"""
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
USING_POSTGRES = DATABASE_URL is not None

if USING_POSTGRES:
    import pg8000.native
    print("[✓] Using PostgreSQL (Neon cloud database)")
else:
    print("[✓] Using SQLite (local development)")

DB_FILE = "safebank_web.db"


def get_connection():
    if USING_POSTGRES:
        # Parse the DATABASE_URL manually for pg8000
        # Format: postgresql://user:password@host/dbname?sslmode=require
        import urllib.parse as up
        url = up.urlparse(DATABASE_URL)
        conn = pg8000.native.Connection(
            user     = url.username,
            password = url.password,
            host     = url.hostname,
            port     = url.port or 5432,
            database = url.path[1:],  # remove leading /
            ssl_context = True        # Neon requires SSL
        )
        return conn
    else:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def fetchone_as_dict(cursor_or_conn, query, params=()):
    """Execute query and return one row as a dict."""
    if USING_POSTGRES:
        # pg8000 native uses conn directly, not cursor
        conn = cursor_or_conn
        rows = conn.run(query, **_params_to_kwargs(query, params))
        if not rows:
            return None
        cols = [col["name"] for col in conn.columns]
        return dict(zip(cols, rows[0]))
    else:
        cursor_or_conn.execute(query, params)
        row = cursor_or_conn.fetchone()
        return dict(row) if row else None


def fetchall_as_dict(cursor_or_conn, query, params=()):
    """Execute query and return all rows as list of dicts."""
    if USING_POSTGRES:
        conn = cursor_or_conn
        rows = conn.run(query, **_params_to_kwargs(query, params))
        if not rows:
            return []
        cols = [col["name"] for col in conn.columns]
        return [dict(zip(cols, row)) for row in rows]
    else:
        cursor_or_conn.execute(query, params)
        return [dict(row) for row in cursor_or_conn.fetchall()]


def execute_query(cursor_or_conn, query, params=()):
    """Execute INSERT/UPDATE/DELETE."""
    if USING_POSTGRES:
        cursor_or_conn.run(query, **_params_to_kwargs(query, params))
    else:
        cursor_or_conn.execute(query, params)


def _params_to_kwargs(query, params):
    """
    pg8000 native uses named params like :p1 :p2 instead of ? or %s.
    This converts a params tuple to a dict {p1: val1, p2: val2, ...}
    and replaces ? or %s in the query.
    But we handle this differently — we use positional :p1 style.
    """
    return {}  # handled in adapt_query


def adapt_query(query):
    """
    Convert SQLite ? placeholders to pg8000 :p1, :p2 style
    OR keep as-is for SQLite.
    """
    if not USING_POSTGRES:
        return query
    # Replace each ? with :p1, :p2, :p3 etc
    result = ""
    i = 1
    for ch in query:
        if ch == "?":
            result += f":p{i}"
            i += 1
        else:
            result += ch
    return result


def params_to_pg(params):
    """Convert a tuple of params to pg8000 named dict {p1:v, p2:v}."""
    return {f"p{i+1}": v for i, v in enumerate(params)}


def commit(conn):
    if USING_POSTGRES:
        pass  # pg8000 native auto-commits each run() call
    else:
        conn.commit()


def rollback(conn):
    if USING_POSTGRES:
        pass  # pg8000 native — nothing to rollback in native mode
    else:
        conn.rollback()


def close(conn):
    if USING_POSTGRES:
        conn.close()
    else:
        conn.close()


def initialize_database():
    if USING_POSTGRES:
        conn = get_connection()
        conn.run("""
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
        conn.run("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                balance     REAL DEFAULT 0.0,
                status      TEXT DEFAULT 'active'
            )
        """)
        conn.run("""
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
        conn.run("""
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
        conn.close()
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            phone TEXT, is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')))""")
        c.execute("""CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            balance REAL DEFAULT 0.0, status TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users(user_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            txn_type TEXT NOT NULL, amount REAL NOT NULL,
            balance_after REAL NOT NULL, description TEXT,
            merchant TEXT, location TEXT, fraud_score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES accounts(account_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS fraud_reports (
            report_id TEXT PRIMARY KEY, txn_id TEXT NOT NULL,
            account_id TEXT NOT NULL, user_id TEXT NOT NULL,
            reason TEXT NOT NULL, evidence TEXT,
            status TEXT DEFAULT 'pending', fraud_score INTEGER DEFAULT 0,
            submitted_at TEXT DEFAULT (datetime('now')),
            reviewed_at TEXT, reviewed_by TEXT,
            admin_notes TEXT, refund_txn_id TEXT)""")
        conn.commit()
        conn.close()
    print("[✓] Database ready.")


if __name__ == "__main__":
    initialize_database()
