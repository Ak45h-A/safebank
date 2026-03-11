"""
fraud_detector.py — Rule-based fraud scoring engine
Scores every transaction 0-100. Higher = more suspicious.
"""
from database import get_connection
from datetime import datetime, timedelta

RISKY_MERCHANTS  = ["crypto","bitcoin","gambling","lottery","casino","wire transfer","anonymous","gift card","forex","darkweb"]
RISKY_LOCATIONS  = ["Unknown","Anonymous VPN","TOR Node","Foreign Server","North Korea","Offshore"]
SAFE_MERCHANTS   = ["amazon","flipkart","swiggy","zomato","irctc","bigbasket","phonepe","gpay","paytm","netflix","uber","ola"]

def calculate_fraud_score(account_id, amount, merchant, location):
    score   = 0
    reasons = []
    conn    = get_connection()
    c       = conn.cursor()

    # Rule 1 — Amount anomaly vs history
    c.execute("SELECT AVG(amount) as avg FROM transactions WHERE account_id=? AND txn_type='debit'", (account_id,))
    row = c.fetchone()
    if row["avg"]:
        avg = row["avg"]
        if amount > avg * 10: score += 35; reasons.append(f"Amount is {amount/avg:.0f}x your average spend")
        elif amount > avg * 5: score += 20; reasons.append("Amount is unusually high (5x average)")
        elif amount > avg * 3: score += 10; reasons.append("Amount is higher than usual")
    else:
        if amount > 50000: score += 20; reasons.append("Large amount on new account")

    # Rule 2 — Velocity: too many txns in 5 min
    since = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("SELECT COUNT(*) as n FROM transactions WHERE account_id=? AND timestamp>? AND txn_type='debit'", (account_id, since))
    n = c.fetchone()["n"]
    if n >= 3: score += 25; reasons.append(f"{n} transactions in last 5 minutes")

    # Rule 3 — Risky merchant
    ml = merchant.lower()
    for w in RISKY_MERCHANTS:
        if w in ml: score += 25; reasons.append(f"High-risk merchant: {merchant}"); break

    # Rule 4 — Risky location
    if location in RISKY_LOCATIONS: score += 30; reasons.append(f"Suspicious location: {location}")

    # Rule 5 — Night-time large transaction
    h = datetime.now().hour
    if h < 5 and amount > 10000: score += 15; reasons.append(f"Large transaction at {h}:00 AM")

    # Rule 6 — Round number
    if amount >= 10000 and amount % 1000 == 0: score += 5; reasons.append("Suspiciously round amount")

    # Rule 7 — Safe merchant (reduce score)
    for w in SAFE_MERCHANTS:
        if w in ml: score -= 10; break

    conn.close()
    return max(0, min(100, score)), reasons

def get_risk_level(score):
    if score >= 75: return "CRITICAL"
    if score >= 50: return "HIGH"
    if score >= 25: return "MEDIUM"
    return "LOW"
