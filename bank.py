"""
bank.py — Core banking operations for SafeBank Web
"""
import hashlib, uuid
from datetime import datetime
from database import get_connection
from fraud_detector import calculate_fraud_score, get_risk_level

def gen_id(prefix):
    date  = datetime.now().strftime("%Y%m%d")
    rand  = uuid.uuid4().hex[:6].upper()
    return f"{prefix}-{date}-{rand}"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def fmt(n):
    return f"₹{float(n):,.2f}"

# ── USER ─────────────────────────────────────────────────────────────────────

def register_user(full_name, email, password, phone, initial_deposit, is_admin=0):
    conn = get_connection()
    c    = conn.cursor()
    try:
        c.execute("SELECT user_id FROM users WHERE email=?", (email,))
        if c.fetchone():
            return {"success": False, "error": "Email already registered."}
        if float(initial_deposit) < 500:
            return {"success": False, "error": "Minimum opening deposit is ₹500."}

        uid = gen_id("USR")
        c.execute("INSERT INTO users (user_id,full_name,email,password_hash,phone,is_admin) VALUES(?,?,?,?,?,?)",
                  (uid, full_name, email, hash_pw(password), phone, is_admin))

        aid = gen_id("ACC")
        c.execute("INSERT INTO accounts (account_id,user_id,balance) VALUES(?,?,?)",
                  (aid, uid, float(initial_deposit)))

        tid = gen_id("TXN")
        c.execute("INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location) VALUES(?,?,'credit',?,?,'Initial Deposit','SafeBank','Branch')",
                  (tid, aid, float(initial_deposit), float(initial_deposit)))

        conn.commit()
        return {"success": True, "user_id": uid, "account_id": aid}
    except Exception as e:
        conn.rollback(); return {"success": False, "error": str(e)}
    finally:
        conn.close()

def login_user(email, password):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""SELECT u.*,a.account_id,a.balance,a.status as acc_status
                 FROM users u JOIN accounts a ON u.user_id=a.user_id
                 WHERE u.email=?""", (email,))
    row = c.fetchone()
    conn.close()
    if not row:                              return {"success": False, "error": "Email not found."}
    if row["password_hash"] != hash_pw(password): return {"success": False, "error": "Wrong password."}
    if row["acc_status"] != "active":        return {"success": False, "error": "Account is not active."}
    return {"success": True, **dict(row)}

def get_user_account(user_id):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT a.*,u.full_name,u.email,u.phone,u.is_admin FROM accounts a JOIN users u ON a.user_id=u.user_id WHERE a.user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# ── TRANSACTIONS ─────────────────────────────────────────────────────────────

def get_transactions(account_id, limit=50):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY timestamp DESC LIMIT ?", (account_id, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def make_payment(account_id, amount, merchant, location="India", description=""):
    conn = get_connection()
    c    = conn.cursor()
    try:
        amount = float(amount)
        if amount <= 0: return {"success": False, "error": "Amount must be > 0"}

        c.execute("SELECT balance,status FROM accounts WHERE account_id=?", (account_id,))
        acc = c.fetchone()
        if not acc:                   return {"success": False, "error": "Account not found"}
        if acc["status"] != "active": return {"success": False, "error": "Account not active"}
        if acc["balance"] < amount:   return {"success": False, "error": f"Insufficient balance. Available: {fmt(acc['balance'])}"}

        score, reasons = calculate_fraud_score(account_id, amount, merchant, location)
        risk = get_risk_level(score)

        if score >= 75:   # BLOCK
            tid = gen_id("TXN")
            c.execute("INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location,fraud_score,status) VALUES(?,?,'blocked',?,?,?,?,?,?,'blocked')",
                      (tid, account_id, amount, acc["balance"], description or f"Payment to {merchant}", merchant, location, score))
            conn.commit()
            return {"success": False, "blocked": True, "txn_id": tid, "fraud_score": score, "risk_level": risk, "reasons": reasons,
                    "error": "Transaction BLOCKED — high fraud risk detected. Your money is safe."}

        new_bal = acc["balance"] - amount
        status  = "flagged" if score >= 25 else "success"
        c.execute("UPDATE accounts SET balance=? WHERE account_id=?", (new_bal, account_id))

        tid = gen_id("TXN")
        c.execute("INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location,fraud_score,status) VALUES(?,?,'debit',?,?,?,?,?,?,?)",
                  (tid, account_id, amount, new_bal, description or f"Payment to {merchant}", merchant, location, score, status))
        conn.commit()
        return {"success": True, "txn_id": tid, "amount": amount, "new_balance": new_bal,
                "fraud_score": score, "risk_level": risk, "flagged": score >= 25, "reasons": reasons}
    except Exception as e:
        conn.rollback(); return {"success": False, "error": str(e)}
    finally:
        conn.close()

def deposit_money(account_id, amount):
    conn = get_connection()
    c    = conn.cursor()
    try:
        amount = float(amount)
        if amount <= 0: return {"success": False, "error": "Amount must be > 0"}
        c.execute("SELECT balance FROM accounts WHERE account_id=?", (account_id,))
        acc     = c.fetchone()
        new_bal = acc["balance"] + amount
        c.execute("UPDATE accounts SET balance=? WHERE account_id=?", (new_bal, account_id))
        tid = gen_id("TXN")
        c.execute("INSERT INTO transactions (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location) VALUES(?,?,'credit',?,?,'Cash Deposit','ATM/Branch','India')",
                  (tid, account_id, amount, new_bal))
        conn.commit()
        return {"success": True, "new_balance": new_bal}
    except Exception as e:
        conn.rollback(); return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ── FRAUD REPORTS ─────────────────────────────────────────────────────────────

def submit_fraud_report(account_id, user_id, txn_id, reason, evidence=""):
    """
    User submits a fraud report. Status = 'pending'.
    Money is NOT refunded yet — admin must verify first.
    """
    conn = get_connection()
    c    = conn.cursor()
    try:
        c.execute("SELECT * FROM transactions WHERE txn_id=? AND account_id=?", (txn_id, account_id))
        txn = c.fetchone()
        if not txn:                               return {"success": False, "error": "Transaction not found on your account."}
        if dict(txn)["txn_type"] not in ("debit","blocked"): return {"success": False, "error": "Only payment transactions can be reported."}
        if dict(txn)["status"] == "reversed":     return {"success": False, "error": "This transaction has already been refunded."}

        # Check no active report exists
        c.execute("SELECT report_id,status FROM fraud_reports WHERE txn_id=? AND status IN ('pending','approved')", (txn_id,))
        ex = c.fetchone()
        if ex:
            if dict(ex)["status"] == "approved":  return {"success": False, "error": "This transaction was already refunded."}
            return {"success": False, "error": "A fraud report for this transaction is already under review."}

        rid = gen_id("RPT")
        c.execute("""INSERT INTO fraud_reports
                     (report_id,txn_id,account_id,user_id,reason,evidence,fraud_score)
                     VALUES(?,?,?,?,?,?,?)""",
                  (rid, txn_id, account_id, user_id, reason, evidence, dict(txn)["fraud_score"]))
        conn.commit()
        return {"success": True, "report_id": rid,
                "message": "Fraud report submitted! The admin will review it shortly. You will see the refund in your account once approved."}
    except Exception as e:
        conn.rollback(); return {"success": False, "error": str(e)}
    finally:
        conn.close()

def get_fraud_reports(account_id=None, status_filter=None):
    """Get fraud reports — if account_id given, only that user's reports."""
    conn = get_connection()
    c    = conn.cursor()
    query = """
        SELECT r.*,
               t.amount, t.merchant, t.timestamp as txn_date, t.location,
               u.full_name, u.email
        FROM fraud_reports r
        JOIN transactions t ON r.txn_id = t.txn_id
        JOIN users u        ON r.user_id = u.user_id
    """
    params = []
    filters = []
    if account_id:
        filters.append("r.account_id=?"); params.append(account_id)
    if status_filter:
        filters.append("r.status=?"); params.append(status_filter)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY r.submitted_at DESC"
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def admin_process_report(report_id, approve, admin_user_id, admin_notes=""):
    """
    Admin approves or rejects a fraud report.
    If approved → money instantly credited back.
    """
    conn = get_connection()
    c    = conn.cursor()
    try:
        c.execute("""SELECT r.*,t.amount,t.account_id as acc_id,t.txn_type
                     FROM fraud_reports r JOIN transactions t ON r.txn_id=t.txn_id
                     WHERE r.report_id=?""", (report_id,))
        rep = c.fetchone()
        if not rep:                         return {"success": False, "error": "Report not found."}
        rep = dict(rep)
        if rep["status"] != "pending":      return {"success": False, "error": f"Report already {rep['status']}."}

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if approve:
            # ── Credit money back instantly ───────────────────
            acc_id = rep["account_id"]
            amount = rep["amount"]

            c.execute("SELECT balance FROM accounts WHERE account_id=?", (acc_id,))
            cur_bal = c.fetchone()["balance"]
            new_bal = cur_bal + amount

            c.execute("UPDATE accounts SET balance=? WHERE account_id=?", (new_bal, acc_id))

            ref_tid = gen_id("TXN")
            c.execute("""INSERT INTO transactions
                         (txn_id,account_id,txn_type,amount,balance_after,description,merchant,location,fraud_score,status)
                         VALUES(?,?,'reversal',?,?,?,?,?,0,'success')""",
                      (ref_tid, acc_id, amount, new_bal,
                       f"REFUND approved by admin for {rep['txn_id']}",
                       "SafeBank Fraud Team", "HQ"))

            c.execute("UPDATE transactions SET status='reversed' WHERE txn_id=?", (rep["txn_id"],))
            c.execute("""UPDATE fraud_reports
                         SET status='approved',reviewed_at=?,reviewed_by=?,admin_notes=?,refund_txn_id=?
                         WHERE report_id=?""",
                      (now, admin_user_id, admin_notes or "Verified as fraud. Refund processed.", ref_tid, report_id))
            conn.commit()
            return {"success": True, "approved": True, "refund_amount": amount,
                    "new_balance": new_bal, "refund_txn_id": ref_tid,
                    "message": f"{fmt(amount)} refunded instantly to the customer."}
        else:
            c.execute("""UPDATE fraud_reports
                         SET status='rejected',reviewed_at=?,reviewed_by=?,admin_notes=?
                         WHERE report_id=?""",
                      (now, admin_user_id, admin_notes or "Report rejected after review.", report_id))
            conn.commit()
            return {"success": True, "approved": False,
                    "message": "Report rejected. Customer notified."}
    except Exception as e:
        conn.rollback(); return {"success": False, "error": str(e)}
    finally:
        conn.close()

def get_all_users():
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""SELECT u.*,a.account_id,a.balance,a.status as acc_status
                 FROM users u JOIN accounts a ON u.user_id=a.user_id
                 ORDER BY u.created_at DESC""")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
