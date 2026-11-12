import os
import sqlite3
import secrets
import csv
import io
import requests

from datetime import datetime
from functools import wraps

from dotenv import load_dotenv

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    Response
)

from openpyxl import Workbook


# =========================================================
# CONFIGURATION
# =========================================================

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET",
    secrets.token_hex(32)
)

DB = "digital_aegis.db"

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    "admin123"
)

HTTPSMS_API_KEY = os.getenv("HTTPSMS_API_KEY")
SMS_FROM = os.getenv("SMS_FROM")

SMS_URL = "https://api.httpsms.com/v1/messages/send"

# PAYMENT AMOUNT IS FIXED
FIXED_AMOUNT = 1000.00

# SMS IS FIXED
FIXED_SMS = """Digital AEGIS


₹10000 has been debited from your account.

If you did not authorize this transaction, contact customer support immediately.
"""



# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sms_status TEXT DEFAULT 'NOT_ATTEMPTED'
        )
    """)

    # Add sms_status if an older database doesn't have it
    columns = conn.execute(
        "PRAGMA table_info(payments)"
    ).fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

    if "sms_status" not in column_names:
        conn.execute("""
            ALTER TABLE payments
            ADD COLUMN sms_status TEXT
            DEFAULT 'NOT_ATTEMPTED'
        """)

    conn.commit()
    conn.close()


# =========================================================
# HELPERS
# =========================================================

def generate_transaction_id():
    return "DA-" + secrets.token_hex(4).upper()


def normalize_phone(phone):
    phone = str(phone).strip()
    phone = phone.replace(" ", "")
    phone = phone.replace("-", "")

    if phone.startswith("0"):
        phone = "+91" + phone[1:]

    elif phone.startswith("91") and not phone.startswith("+"):
        phone = "+" + phone

    elif not phone.startswith("+"):
        phone = "+91" + phone

    return phone


# =========================================================
# SMS
# =========================================================

def send_sms(phone):

    print("\n======================================")
    print("          HTTP SMS REQUEST")
    print("======================================")

    print("API KEY LOADED:", bool(HTTPSMS_API_KEY))
    print("FROM:", SMS_FROM)
    print("TO:", phone)

    print("MESSAGE:")
    print(FIXED_SMS)

    if not HTTPSMS_API_KEY:
        print("ERROR: HTTPSMS_API_KEY is missing.")
        print("======================================")
        return False, "HTTPSMS_API_KEY is missing"

    if not SMS_FROM:
        print("ERROR: SMS_FROM is missing.")
        print("======================================")
        return False, "SMS_FROM is missing"

    headers = {
        "x-api-key": HTTPSMS_API_KEY,
        "Content-Type": "application/json"
    }

    data = {
        "from": SMS_FROM,
        "to": phone,
        "content": FIXED_SMS
    }

    try:

        response = requests.post(
            SMS_URL,
            headers=headers,
            json=data,
            timeout=20
        )

        print("HTTP STATUS:", response.status_code)
        print("RESPONSE:")
        print(response.text)

        print("======================================\n")

        if response.ok:
            return True, response.text

        return False, response.text

    except requests.RequestException as e:

        print("SMS REQUEST ERROR:")
        print(repr(e))

        print("======================================\n")

        return False, str(e)


# =========================================================
# MAIN PAGE
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


# =========================================================
# PAYMENT
# =========================================================

@app.route("/pay", methods=["POST"])
def pay():

    try:

        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "success": False,
                "error": "Invalid request."
            }), 400

        # ONLY PHONE NUMBER IS ACCEPTED
        phone = str(
            data.get("phone", "")
        ).strip()

        if not phone:

            return jsonify({
                "success": False,
                "error": "Phone number is required."
            }), 400

        phone = normalize_phone(phone)

        # =================================================
        # IMPORTANT:
        # AMOUNT IS ALWAYS ₹1000
        # ANY AMOUNT SENT BY THE FRONTEND IS IGNORED
        # =================================================

        amount = FIXED_AMOUNT

        transaction_id = generate_transaction_id()

        created_at = datetime.now().strftime(
            "%d-%m-%Y %H:%M:%S"
        )

        print("\n======================================")
        print("           NEW PAYMENT")
        print("======================================")

        print("PHONE:", phone)
        print("FIXED AMOUNT: Rs.1000")
        print("TRANSACTION:", transaction_id)

        # -------------------------------------------------
        # SAVE PAYMENT
        # -------------------------------------------------

        conn = get_db()

        conn.execute("""
            INSERT INTO payments (
                transaction_id,
                phone,
                amount,
                status,
                created_at,
                sms_status
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            transaction_id,
            phone,
            amount,
            "SUCCESS",
            created_at,
            "SENDING"
        ))

        conn.commit()
        conn.close()

        # -------------------------------------------------
        # SEND FIXED SMS
        # -------------------------------------------------

        sms_sent, sms_response = send_sms(phone)

        sms_status = (
            "SENT"
            if sms_sent
            else "FAILED"
        )

        # -------------------------------------------------
        # UPDATE SMS STATUS
        # -------------------------------------------------

        conn = get_db()

        conn.execute("""
            UPDATE payments
            SET sms_status = ?
            WHERE transaction_id = ?
        """, (
            sms_status,
            transaction_id
        ))

        conn.commit()
        conn.close()

        print("SMS STATUS:", sms_status)

        print("======================================\n")

        return jsonify({
            "success": True,
            "transaction_id": transaction_id,
            "amount": "1000.00",
            "time": created_at,
            "sms_sent": sms_sent,
            "sms_status": sms_status,
            "message": "Simulated payment of Rs.1000 successful."
        })

    except Exception as e:

        print("\n======================================")
        print("PAYMENT ERROR:")
        print(repr(e))
        print("======================================\n")

        return jsonify({
            "success": False,
            "error": "Server error while processing payment."
        }), 500


# =========================================================
# ADMIN LOGIN
# =========================================================

@app.route("/admin", methods=["GET", "POST"])
def admin():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if password == ADMIN_PASSWORD:

            session["admin"] = True

            return redirect(
                url_for("admin_dashboard")
            )

        return render_template(
            "admin.html",
            error="Incorrect password."
        )

    return render_template("admin.html")


# =========================================================
# ADMIN AUTH
# =========================================================

def admin_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if not session.get("admin"):
            return redirect(
                url_for("admin")
            )

        return func(*args, **kwargs)

    return wrapper


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():

    conn = get_db()

    payments = conn.execute("""
        SELECT *
        FROM payments
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        payments=payments
    )


# =========================================================
# ADMIN LOGOUT
# =========================================================

@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin")
    )


# =========================================================
# CSV EXPORT
# =========================================================

@app.route("/admin/export/csv")
@admin_required
def export_csv():

    conn = get_db()

    payments = conn.execute("""
        SELECT *
        FROM payments
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow([
        "Transaction ID",
        "Phone",
        "Amount",
        "Status",
        "SMS Status",
        "Date & Time"
    ])

    for payment in payments:

        writer.writerow([
            payment["transaction_id"],
            payment["phone"],
            payment["amount"],
            payment["status"],
            payment["sms_status"],
            payment["created_at"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=payments.csv"
        }
    )


# =========================================================
# EXCEL EXPORT
# =========================================================

@app.route("/admin/export/excel")
@admin_required
def export_excel():

    conn = get_db()

    payments = conn.execute("""
        SELECT *
        FROM payments
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "Payments"

    sheet.append([
        "Transaction ID",
        "Phone",
        "Amount",
        "Status",
        "SMS Status",
        "Date & Time"
    ])

    for payment in payments:

        sheet.append([
            payment["transaction_id"],
            payment["phone"],
            payment["amount"],
            payment["status"],
            payment["sms_status"],
            payment["created_at"]
        ])

    output = io.BytesIO()

    workbook.save(output)

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype=(
            "application/vnd.openxmlformats."
            "officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                "attachment; filename=payments.xlsx"
        }
    )


# =========================================================
# START SERVER
# =========================================================

# Initialize database when the application starts
init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
