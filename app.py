"""
app.py — SafeBank Web Application
Run: python app.py
Then open: http://localhost:5000
"""
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from database import initialize_database
from bank import (register_user, login_user, get_user_account, get_transactions,
                  make_payment, deposit_money, submit_fraud_report,
                  get_fraud_reports, admin_process_report, get_all_users)
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "safebank-local-dev-key-2024")

# ── RUNS ON EVERY STARTUP (local AND Render) ──────────────────────────────
# IMPORTANT: This must be outside if __name__ == "__main__"
# When Render runs "gunicorn app:app", it imports app.py but never
# reaches the __main__ block — so database init must happen at import time.
def startup():
    from bank import register_user as reg
    from database import get_connection
    initialize_database()
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE email='admin@safebank.com'")
    if not c.fetchone():
        reg("Bank Admin", "admin@safebank.com", "admin123", "9999999999", 999999, is_admin=1)
        print("Admin account created: admin@safebank.com / admin123")
    conn.close()
    print("SafeBank database ready.")

startup()

# ── HELPERS ───────────────────────────────────────────────────────────────────

def logged_in_user():
    """Returns current user dict from session, or None."""
    return session.get("user")

def require_login(f):
    """Decorator: redirect to USER login if not logged in."""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not logged_in_user():
            flash("Please login to your account first.", "warning")
            return redirect(url_for("user_login"))
        if logged_in_user().get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        return f(*args, **kwargs)
    return wrapper

def require_admin(f):
    """Decorator: only admins can access."""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = logged_in_user()
        if not user:
            flash("Admin login required.", "warning")
            return redirect(url_for("admin_login"))
        if not user.get("is_admin"):
            flash("You do not have admin access.", "error")
            return redirect(url_for("user_login"))
        return f(*args, **kwargs)
    return wrapper

# ── PUBLIC ROUTES ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    user = logged_in_user()
    if user:
        if user.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("user_login"))

# ── USER LOGIN / REGISTER ─────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def user_login():
    if logged_in_user():
        if logged_in_user().get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        result = login_user(request.form["email"], request.form["password"])
        if result["success"]:
            if result["is_admin"]:
                flash("This is the customer login page. Admins must use the Admin Portal.", "error")
                return redirect(url_for("admin_login"))
            session["user"] = {
                "user_id":    result["user_id"],
                "full_name":  result["full_name"],
                "email":      result["email"],
                "account_id": result["account_id"],
                "is_admin":   0,
            }
            return redirect(url_for("dashboard"))
        flash(result["error"], "error")

    return render_template("login_user.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if logged_in_user():
        if logged_in_user().get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        session.clear()

    if request.method == "POST":
        result = login_user(request.form["email"], request.form["password"])
        if result["success"]:
            if not result["is_admin"]:
                flash("Access denied. This portal is for bank administrators only.", "error")
                return render_template("login_admin.html")
            session["user"] = {
                "user_id":    result["user_id"],
                "full_name":  result["full_name"],
                "email":      result["email"],
                "account_id": result["account_id"],
                "is_admin":   1,
            }
            return redirect(url_for("admin_dashboard"))
        flash(result["error"], "error")

    return render_template("login_admin.html")


@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        result = register_user(
            request.form["full_name"],
            request.form["email"],
            request.form["password"],
            request.form.get("phone",""),
            request.form.get("initial_deposit", 1000),
            is_admin=0
        )
        if result["success"]:
            flash("Account created successfully! Please login.", "success")
            return redirect(url_for("user_login"))
        flash(result["error"], "error")
    return render_template("register.html")

@app.route("/logout")
def logout():
    was_admin = logged_in_user() and logged_in_user().get("is_admin")
    session.clear()
    flash("Logged out successfully.", "success")
    if was_admin:
        return redirect(url_for("admin_login"))
    return redirect(url_for("user_login"))

# ── USER ROUTES ───────────────────────────────────────────────────────────────

@app.route("/dashboard")
@require_login
def dashboard():
    user = logged_in_user()
    acc  = get_user_account(user["user_id"])
    txns = get_transactions(acc["account_id"], limit=5)
    my_reports = get_fraud_reports(account_id=acc["account_id"])
    pending = sum(1 for r in my_reports if r["status"] == "pending")
    return render_template("dashboard.html", acc=acc, txns=txns, pending_reports=pending)

@app.route("/transactions")
@require_login
def transactions():
    user = logged_in_user()
    acc  = get_user_account(user["user_id"])
    txns = get_transactions(acc["account_id"], limit=50)
    return render_template("transactions.html", acc=acc, txns=txns)

@app.route("/pay", methods=["GET","POST"])
@require_login
def pay():
    user = logged_in_user()
    acc  = get_user_account(user["user_id"])
    if request.method == "POST":
        result = make_payment(
            acc["account_id"],
            request.form["amount"],
            request.form["merchant"],
            request.form.get("location","India"),
            request.form.get("description","")
        )
        return render_template("pay.html", acc=acc, result=result, form=request.form)
    return render_template("pay.html", acc=acc, result=None, form=None)

@app.route("/deposit", methods=["GET","POST"])
@require_login
def deposit():
    user = logged_in_user()
    acc  = get_user_account(user["user_id"])
    if request.method == "POST":
        result = deposit_money(acc["account_id"], request.form["amount"])
        if result["success"]:
            flash(f"Deposited successfully!", "success")
            return redirect(url_for("dashboard"))
        flash(result["error"], "error")
    return render_template("deposit.html", acc=acc)

@app.route("/report-fraud", methods=["GET","POST"])
@require_login
def report_fraud():
    user = logged_in_user()
    acc  = get_user_account(user["user_id"])
    all_txns   = get_transactions(acc["account_id"], limit=50)
    refundable = [t for t in all_txns if t["txn_type"] == "debit" and t["status"] not in ("reversed",)]

    if request.method == "POST":
        result = submit_fraud_report(
            acc["account_id"],
            user["user_id"],
            request.form["txn_id"],
            request.form["reason"],
            request.form.get("evidence","")
        )
        if result["success"]:
            flash(result["message"], "success")
            return redirect(url_for("my_reports"))
        flash(result["error"], "error")

    return render_template("report_fraud.html", acc=acc, refundable=refundable)

@app.route("/my-reports")
@require_login
def my_reports():
    user    = logged_in_user()
    acc     = get_user_account(user["user_id"])
    reports = get_fraud_reports(account_id=acc["account_id"])
    return render_template("my_reports.html", acc=acc, reports=reports)

# ── ADMIN ROUTES ──────────────────────────────────────────────────────────────

@app.route("/admin")
@require_admin
def admin_dashboard():
    pending  = get_fraud_reports(status_filter="pending")
    approved = get_fraud_reports(status_filter="approved")
    rejected = get_fraud_reports(status_filter="rejected")
    users    = get_all_users()
    return render_template("admin_dashboard.html",
                           pending=pending, approved=approved,
                           rejected=rejected, users=users)

@app.route("/admin/reports")
@require_admin
def admin_reports():
    status  = request.args.get("status","pending")
    reports = get_fraud_reports(status_filter=status if status != "all" else None)
    return render_template("admin_reports.html", reports=reports, current_status=status)

@app.route("/admin/process/<report_id>", methods=["POST"])
@require_admin
def admin_process(report_id):
    user    = logged_in_user()
    approve = request.form.get("action") == "approve"
    notes   = request.form.get("admin_notes","")
    result  = admin_process_report(report_id, approve, user["user_id"], notes)
    if result["success"]:
        flash(result["message"], "success" if approve else "warning")
    else:
        flash(result["error"], "error")
    return redirect(url_for("admin_reports", status="pending"))

@app.route("/admin/users")
@require_admin
def admin_users():
    users = get_all_users()
    return render_template("admin_users.html", users=users)

# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/balance")
@require_login
def api_balance():
    user = logged_in_user()
    acc  = get_user_account(user["user_id"])
    return jsonify({"balance": acc["balance"]})

# ── LOCAL RUN ONLY ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("SafeBank running at http://localhost:5000")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
