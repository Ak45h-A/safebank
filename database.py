"""
database.py — SQLite setup for SafeBank Web
"""
import sqlite3, os

DB_FILE = "safebank_web.db"

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def initialize_database():
    conn = get_connection()
    c = conn.cursor()

    # Users table — stores all bank customers + admin flag
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       TEXT PRIMARY KEY,
            full_name     TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            phone         TEXT,
            is_admin      INTEGER DEFAULT 0,   -- 1 = admin (you), 0 = customer
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    # Bank accounts — one per user
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id  TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            balance     REAL DEFAULT 0.0,
            status      TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # Every money movement recorded here permanently
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id        TEXT PRIMARY KEY,
            account_id    TEXT NOT NULL,
            txn_type      TEXT NOT NULL,      -- debit / credit / reversal
            amount        REAL NOT NULL,
            balance_after REAL NOT NULL,
            description   TEXT,
            merchant      TEXT,
            location      TEXT,
            fraud_score   INTEGER DEFAULT 0,
            status        TEXT DEFAULT 'success', -- success/flagged/blocked/reversed
            timestamp     TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES accounts(account_id)
        )
    """)

    # Fraud reports submitted by users — admin reviews these
    c.execute("""
        CREATE TABLE IF NOT EXISTS fraud_reports (
            report_id     TEXT PRIMARY KEY,
            txn_id        TEXT NOT NULL,
            account_id    TEXT NOT NULL,
            user_id       TEXT NOT NULL,
            reason        TEXT NOT NULL,
            evidence      TEXT,               -- extra details user provides
            status        TEXT DEFAULT 'pending', -- pending/approved/rejected
            fraud_score   INTEGER DEFAULT 0,
            submitted_at  TEXT DEFAULT (datetime('now')),
            reviewed_at   TEXT,
            reviewed_by   TEXT,               -- admin user_id who acted
            admin_notes   TEXT,
            refund_txn_id TEXT,               -- set when approved & refunded
            FOREIGN KEY (txn_id) REFERENCES transactions(txn_id)
        )
    """)

    conn.commit()
    conn.close()
    print("[✓] Database ready.")

if __name__ == "__main__":
    initialize_database()
    