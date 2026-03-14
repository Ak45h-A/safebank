"""
fraud_detector.py — Fraud scoring engine
Works with both PostgreSQL and SQLite
"""
from database import get_connection, adapt_query, params_to_pg, close, USING_POSTGRES
from datetime import datetime, timedelta

RISKY_MERCHANTS = ["crypto","bitcoin","gambling","lottery","casino",
                   "wire transfer","anonymous","gift card","forex","darkweb"]
RISKY_LOCATIONS = ["Unknown","Anonymous VPN","TOR Node","Foreign Server","North Korea","Offshore"]
SAFE_MERCHANTS  = ["amazon","flipkart","swiggy","zomato","irctc","bigbasket",
                   "phonepe","gpay","paytm","netflix","uber","ola"]

def _run(conn, query, params=()):
    q = adapt_query(query)
    if USING_POSTGRES:
        rows = conn.run(q, **params_to_pg(params)) if params else conn.run(q)
        if not rows: return None
        cols = [col["name"] for col in conn.columns]
        return [dict(zip(cols, row)) for row in rows]
    else:
        c = conn.cursor()
        c.execute(q, params)
        return [dict(row) for row in c.fetchall()]

def calculate_fraud_score(account_id, amount, merchant, location):
    score   = 0
    reasons = []
    conn    = get_connection()
    try:
        # Rule 1 — Amount anomaly
        rows = _run(conn, "SELECT AVG(amount) as avg FROM transactions WHERE account_id=? AND txn_type='debit'", (account_id,))
        avg  = rows[0]["avg"] if rows and rows[0]["avg"] else None
        if avg:
            if   amount > avg * 10: score += 35; reasons.append(f"Amount is {amount/avg:.0f}x your average")
            elif amount > avg * 5:  score += 20; reasons.append("Amount is unusually high (5x average)")
            elif amount > avg * 3:  score += 10; reasons.append("Amount is higher than usual")
        else:
            if amount > 50000: score += 20; reasons.append("Large amount on new account")

        # Rule 2 — Velocity
        since = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        rows2 = _run(conn, "SELECT COUNT(*) as n FROM transactions WHERE account_id=? AND timestamp>? AND txn_type='debit'", (account_id, since))
        n = int(rows2[0]["n"]) if rows2 and rows2[0]["n"] else 0
        if n >= 3: score += 25; reasons.append(f"{n} transactions in last 5 minutes")

        # Rule 3 — Risky merchant
        ml = merchant.lower()
        for w in RISKY_MERCHANTS:
            if w in ml: score += 25; reasons.append(f"High-risk merchant: {merchant}"); break

        # Rule 4 — Risky location
        if location in RISKY_LOCATIONS:
            score += 30; reasons.append(f"Suspicious location: {location}")

        # Rule 5 — Night-time
        h = datetime.now().hour
        if h < 5 and amount > 10000:
            score += 15; reasons.append(f"Large transaction at {h}:00 AM")

        # Rule 6 — Round number
        if amount >= 10000 and amount % 1000 == 0:
            score += 5; reasons.append("Round amount")

        # Rule 7 — Safe merchant
        for w in SAFE_MERCHANTS:
            if w in ml: score -= 10; break

    finally:
        close(conn)

    return max(0, min(100, score)), reasons

def get_risk_level(score):
    if score >= 75: return "CRITICAL"
    if score >= 50: return "HIGH"
    if score >= 25: return "MEDIUM"
    return "LOW"
