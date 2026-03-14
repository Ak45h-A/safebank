"""
bank.py — Core banking operations
Works with PostgreSQL (Render/Neon) and SQLite (local)
"""
import hashlib, uuid
from datetime import datetime
from database import (get_connection, fetchone_as_dict, fetchall_as_dict,
                      execute_query, adapt_query, params_to_pg,
                      commit, rollback, close, USING_POSTGRES)

def gen_id(prefix):
    date = datetime.now().strftime("%Y%m%d")
    rand = uuid.uuid4().hex[:6].upper()
    return f"{prefix}-{date}-{rand}"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def fmt(n):
    return f"₹{float(n):,.2f}"

def run(conn, query, params=()):
    """Universal query runner — handles both PostgreSQL and SQLite."""
    q = adapt_query(query)
    if USING_POSTGRES:
        if params:
            return conn.run(q, **params_to_pg(params))
        return conn.run(q)
    else:
        conn.execute(q, params)

# ── USER ──────────────────────────────────────────────────────────────────────

def register_user(full_name, email, password, phone, initial_deposit, is_admin=0):
    conn = get_connection()
    try:
        existing = fetchone_as_dict(conn if USING_POSTGRES else conn.cursor(),
            adapt_query("SELECT user_id FROM users WHERE email=?"), (email,)) \
            if not USING_POSTGRES else _pg_fetchone(conn, "SELECT user_id FROM users WHERE email=?", (email,))

        if existing:
            return {"success": False, "error": "Email already registered."}
        if float(initial_deposit) < 500:
            return {"success": False, "error": "Minimum opening deposit is ₹500."}

        uid = gen_id("USR")
        aid = gen_id("ACC")
        tid = gen_id("TXN")
        dep = float(initial_deposit)

        run(conn, "INSERT INTO users (user_id,full_name,email,password_hash,phone,is_admin) VALUES(?,?,?,?,?,?)",
            (uid, full_name, email, hash_pw(password), phone, is_admin))
        run(conn, "INSERT INTO accounts (account_id,user_id,balance) VALUES(?,?,?)",
            (aid, uid, dep))
        run(conn, "INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location) VALUES(?,?,'credit',?,?,'Initial Deposit','SafeBank','Branch')",
            (tid, aid, dep, dep))

        commit(conn)
        return {"success": True, "user_id": uid, "account_id": aid}
    except Exception as e:
        rollback(conn)
        return {"success": False, "error": str(e)}
    finally:
        close(conn)


def _pg_fetchone(conn, query, params=()):
    q = adapt_query(query)
    if USING_POSTGRES:
        rows = conn.run(q, **params_to_pg(params)) if params else conn.run(q)
        if not rows: return None
        cols = [col["name"] for col in conn.columns]
        return dict(zip(cols, rows[0]))
    else:
        c = conn.cursor()
        c.execute(q, params)
        row = c.fetchone()
        return dict(row) if row else None

def _pg_fetchall(conn, query, params=()):
    q = adapt_query(query)
    if USING_POSTGRES:
        rows = conn.run(q, **params_to_pg(params)) if params else conn.run(q)
        if not rows: return []
        cols = [col["name"] for col in conn.columns]
        return [dict(zip(cols, row)) for row in rows]
    else:
        c = conn.cursor()
        c.execute(q, params)
        return [dict(row) for row in c.fetchall()]


def login_user(email, password):
    conn = get_connection()
    try:
        row = _pg_fetchone(conn, """
            SELECT u.user_id, u.full_name, u.email, u.password_hash, u.is_admin,
                   a.account_id, a.balance, a.status as acc_status
            FROM users u JOIN accounts a ON u.user_id = a.user_id
            WHERE u.email=?""", (email,))
        if not row:               return {"success": False, "error": "Email not found."}
        if row["password_hash"] != hash_pw(password): return {"success": False, "error": "Wrong password."}
        if row["acc_status"] != "active":             return {"success": False, "error": "Account is not active."}
        return {"success": True, **row}
    finally:
        close(conn)


def get_user_account(user_id):
    conn = get_connection()
    try:
        return _pg_fetchone(conn, """
            SELECT a.account_id, a.user_id, a.balance, a.status,
                   u.full_name, u.email, u.phone, u.is_admin
            FROM accounts a JOIN users u ON a.user_id = u.user_id
            WHERE a.user_id=?""", (user_id,))
    finally:
        close(conn)


def get_transactions(account_id, limit=50):
    conn = get_connection()
    try:
        return _pg_fetchall(conn,
            "SELECT * FROM transactions WHERE account_id=? ORDER BY timestamp DESC LIMIT ?",
            (account_id, limit))
    finally:
        close(conn)


def make_payment(account_id, amount, merchant, location="India", description=""):
    conn = get_connection()
    try:
        amount = float(amount)
        if amount <= 0: return {"success": False, "error": "Amount must be > 0"}

        acc = _pg_fetchone(conn, "SELECT balance, status FROM accounts WHERE account_id=?", (account_id,))
        if not acc:                   return {"success": False, "error": "Account not found"}
        if acc["status"] != "active": return {"success": False, "error": "Account not active"}
        if acc["balance"] < amount:   return {"success": False, "error": f"Insufficient balance. Available: {fmt(acc['balance'])}"}

        from fraud_detector import calculate_fraud_score, get_risk_level
        score, reasons = calculate_fraud_score(account_id, amount, merchant, location)
        risk = get_risk_level(score)

        if score >= 75:
            tid = gen_id("TXN")
            run(conn, "INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location,fraud_score,status) VALUES(?,?,'blocked',?,?,?,?,?,?,'blocked')",
                (tid, account_id, amount, acc["balance"], description or f"Payment to {merchant}", merchant, location, score))
            commit(conn)
            return {"success": False, "blocked": True, "txn_id": tid,
                    "fraud_score": score, "risk_level": risk, "reasons": reasons,
                    "error": "Transaction BLOCKED — high fraud risk. Your money is safe."}

        new_bal = acc["balance"] - amount
        status  = "flagged" if score >= 25 else "success"
        run(conn, "UPDATE accounts SET balance=? WHERE account_id=?", (new_bal, account_id))
        tid = gen_id("TXN")
        run(conn, "INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location,fraud_score,status) VALUES(?,?,'debit',?,?,?,?,?,?,?)",
            (tid, account_id, amount, new_bal, description or f"Payment to {merchant}", merchant, location, score, status))
        commit(conn)
        return {"success": True, "txn_id": tid, "amount": amount, "new_balance": new_bal,
                "fraud_score": score, "risk_level": risk, "flagged": score >= 25, "reasons": reasons}
    except Exception as e:
        rollback(conn); return {"success": False, "error": str(e)}
    finally:
        close(conn)


def deposit_money(account_id, amount):
    conn = get_connection()
    try:
        amount  = float(amount)
        acc     = _pg_fetchone(conn, "SELECT balance FROM accounts WHERE account_id=?", (account_id,))
        new_bal = acc["balance"] + amount
        run(conn, "UPDATE accounts SET balance=? WHERE account_id=?", (new_bal, account_id))
        tid = gen_id("TXN")
        run(conn, "INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location) VALUES(?,?,'credit',?,?,'Cash Deposit','ATM/Branch','India')",
            (tid, account_id, amount, new_bal))
        commit(conn)
        return {"success": True, "new_balance": new_bal}
    except Exception as e:
        rollback(conn); return {"success": False, "error": str(e)}
    finally:
        close(conn)


def submit_fraud_report(account_id, user_id, txn_id, reason, evidence=""):
    conn = get_connection()
    try:
        txn = _pg_fetchone(conn, "SELECT * FROM transactions WHERE txn_id=? AND account_id=?", (txn_id, account_id))
        if not txn: return {"success": False, "error": "Transaction not found."}
        if txn["txn_type"] not in ("debit","blocked"): return {"success": False, "error": "Only payments can be reported."}
        if txn["status"] == "reversed": return {"success": False, "error": "Already refunded."}

        ex = _pg_fetchone(conn, "SELECT report_id, status FROM fraud_reports WHERE txn_id=? AND status IN ('pending','approved')", (txn_id,))
        if ex:
            if ex["status"] == "approved": return {"success": False, "error": "Already refunded."}
            return {"success": False, "error": "Report already under review."}

        rid = gen_id("RPT")
        run(conn, "INSERT INTO fraud_reports (report_id,txn_id,account_id,user_id,reason,evidence,fraud_score) VALUES(?,?,?,?,?,?,?)",
            (rid, txn_id, account_id, user_id, reason, evidence, txn["fraud_score"]))
        commit(conn)
        return {"success": True, "report_id": rid,
                "message": "Fraud report submitted! Admin will review it shortly."}
    except Exception as e:
        rollback(conn); return {"success": False, "error": str(e)}
    finally:
        close(conn)


def get_fraud_reports(account_id=None, status_filter=None):
    conn    = get_connection()
    try:
        query   = """SELECT r.report_id, r.txn_id, r.account_id, r.user_id, r.reason,
                            r.evidence, r.status, r.fraud_score, r.submitted_at,
                            r.reviewed_at, r.reviewed_by, r.admin_notes, r.refund_txn_id,
                            t.amount, t.merchant, t.timestamp as txn_date, t.location,
                            u.full_name, u.email
                     FROM fraud_reports r
                     JOIN transactions t ON r.txn_id  = t.txn_id
                     JOIN users        u ON r.user_id = u.user_id"""
        params  = []
        filters = []
        if account_id:    filters.append("r.account_id=?"); params.append(account_id)
        if status_filter: filters.append("r.status=?");     params.append(status_filter)
        if filters: query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY r.submitted_at DESC"
        return _pg_fetchall(conn, query, params)
    finally:
        close(conn)


def admin_process_report(report_id, approve, admin_user_id, admin_notes=""):
    conn = get_connection()
    try:
        rep = _pg_fetchone(conn, """
            SELECT r.report_id, r.txn_id, r.account_id, r.status,
                   t.amount
            FROM fraud_reports r JOIN transactions t ON r.txn_id=t.txn_id
            WHERE r.report_id=?""", (report_id,))
        if not rep:                    return {"success": False, "error": "Report not found."}
        if rep["status"] != "pending": return {"success": False, "error": f"Report already {rep['status']}."}

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if approve:
            acc     = _pg_fetchone(conn, "SELECT balance FROM accounts WHERE account_id=?", (rep["account_id"],))
            new_bal = acc["balance"] + rep["amount"]
            run(conn, "UPDATE accounts SET balance=? WHERE account_id=?", (new_bal, rep["account_id"]))
            ref_tid = gen_id("TXN")
            run(conn, "INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location,fraud_score,status) VALUES(?,?,'reversal',?,?,?,?,?,0,'success')",
                (ref_tid, rep["account_id"], rep["amount"], new_bal,
                 f"REFUND for {rep['txn_id']}", "SafeBank Fraud Team", "HQ"))
            run(conn, "UPDATE transactions SET status='reversed' WHERE txn_id=?", (rep["txn_id"],))
            run(conn, "UPDATE fraud_reports SET status='approved',reviewed_at=?,reviewed_by=?,admin_notes=?,refund_txn_id=? WHERE report_id=?",
                (now, admin_user_id, admin_notes or "Verified fraud. Refund processed.", ref_tid, report_id))
            commit(conn)
            return {"success": True, "approved": True, "refund_amount": rep["amount"],
                    "new_balance": new_bal, "refund_txn_id": ref_tid,
                    "message": f"{fmt(rep['amount'])} refunded instantly."}
        else:
            run(conn, "UPDATE fraud_reports SET status='rejected',reviewed_at=?,reviewed_by=?,admin_notes=? WHERE report_id=?",
                (now, admin_user_id, admin_notes or "Rejected after review.", report_id))
            commit(conn)
            return {"success": True, "approved": False, "message": "Report rejected."}
    except Exception as e:
        rollback(conn); return {"success": False, "error": str(e)}
    finally:
        close(conn)


def get_all_users():
    conn = get_connection()
    try:
        return _pg_fetchall(conn, """
            SELECT u.user_id, u.full_name, u.email, u.phone, u.is_admin, u.created_at,
                   a.account_id, a.balance, a.status as acc_status
            FROM users u JOIN accounts a ON u.user_id = a.user_id
            ORDER BY u.created_at DESC""", ())
    finally:
        close(conn)


def delete_user_history(account_id):
    """
    Admin function: deletes ALL transactions for a user.
    Also deletes related fraud reports.
    Account and balance are kept — only history is wiped.
    """
    conn = get_connection()
    try:
        # Count first so we can report how many were deleted
        rows = _pg_fetchone(conn,
            "SELECT COUNT(*) as n FROM transactions WHERE account_id=?",
            (account_id,))
        count = int(rows["n"]) if rows and rows["n"] else 0

        # Delete fraud reports linked to this account's transactions first
        run(conn, "DELETE FROM fraud_reports WHERE account_id=?", (account_id,))

        # Delete all transactions
        run(conn, "DELETE FROM transactions WHERE account_id=?", (account_id,))

        commit(conn)
        return {"success": True,
                "message": f"Deleted {count} transaction(s) and all fraud reports for this account."}
    except Exception as e:
        rollback(conn)
        return {"success": False, "error": str(e)}
    finally:
        close(conn)
