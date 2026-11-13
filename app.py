"""
=============================================================
  TT ELECTRONICS AND ELECTRONICS  |  Till 9232378  |  Bomet CBD
  0723337879/0718930255
=============================================================
"""

import base64
import csv
import io
import json
import os
import random
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

import qrcode
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, send_from_directory, session,
                   url_for)
from sqlalchemy import create_engine, text
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import (ADMIN_PASSWORD, BUSINESS, DATABASE_URL, MAIL_PASSWORD,
                    MAIL_PORT, MAIL_SERVER, MAIL_USERNAME, PUBLIC_BASE_URL,
                    SECRET_KEY, SIMULATE_PAYMENTS)
from etims import Etims
from mpesa import Daraja, normalize_phone

try:
    from zoneinfo import ZoneInfo
    NAIROBI = ZoneInfo("Africa/Nairobi")
except Exception:
    NAIROBI = timezone(timedelta(hours=3))

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["UPLOAD_FOLDER"] = UPLOADS
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def now_dt():
    return datetime.now(NAIROBI)


def iso_now():
    return now_dt().isoformat(timespec="seconds")


def today():
    return now_dt().strftime("%Y-%m-%d")


def db():
    return engine.connect()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        VARCHAR(255) NOT NULL,
  description TEXT DEFAULT '',
  price       REAL NOT NULL,
  category    VARCHAR(100) DEFAULT 'Electronics',
  stock       INTEGER DEFAULT 0,
  image       VARCHAR(255) DEFAULT '',
  created_at  VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS users(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        VARCHAR(255) DEFAULT '',
  identifier  VARCHAR(100) UNIQUE NOT NULL,
  gmail       VARCHAR(255) NOT NULL,
  password    VARCHAR(255) NOT NULL,
  verified    INTEGER DEFAULT 0,
  created_at  VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS otps(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  gmail       VARCHAR(255) NOT NULL,
  otp_code    VARCHAR(20) NOT NULL,
  expires_at  VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS admin_auth(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  password_hash VARCHAR(255) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no            VARCHAR(100) UNIQUE,
  customer_name       VARCHAR(255),
  customer_phone      VARCHAR(50),
  delivery            VARCHAR(255) DEFAULT 'Pickup - Bomet CBD shop',
  total               REAL,
  vat                 REAL,
  status              VARCHAR(50) DEFAULT 'pending',
  payment_method      VARCHAR(50) DEFAULT 'mpesa',
  mpesa_receipt       VARCHAR(100),
  checkout_request_id VARCHAR(100),
  etims_rcpt_no       VARCHAR(100),
  etims_cu_no         VARCHAR(100),
  etims_qr            TEXT,
  etims_status        VARCHAR(50) DEFAULT 'PENDING',
  created_at          VARCHAR(50),
  paid_at             VARCHAR(50)
);
CREATE TABLE IF NOT EXISTS order_items(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   INTEGER REFERENCES orders(id),
  product_id INTEGER,
  name       VARCHAR(255),
  price      REAL,
  qty        INTEGER
);
CREATE TABLE IF NOT EXISTS transactions(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  trans_type    VARCHAR(50), 
  trans_id      VARCHAR(100), 
  trans_time    VARCHAR(50),
  phone         VARCHAR(50), 
  amount        REAL, 
  till          VARCHAR(50),
  mpesa_receipt VARCHAR(100), 
  order_no      VARCHAR(100), 
  raw           TEXT, 
  created_at    VARCHAR(50)
);
"""


def init_db():
    with db() as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            if stmt.strip():
                query_str = stmt.strip()
                if "postgresql" in DATABASE_URL:
                    query_str = query_str.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                conn.execute(text(query_str))
        conn.commit()

        # Safely migrate older databases to include user names
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN name VARCHAR(255) DEFAULT ''"))
            conn.commit()
        except Exception:
            pass

        res = conn.execute(text("SELECT COUNT(*) FROM products")).fetchone()
        if res[0] == 0:
            seed = [
                ('32" Smart Android TV', "Frameless Android smart TV, Netflix & YouTube.", 18500, "TVs", 8),
                ("Hisense 2.1 Home Theatre", "300W sub-woofer home theatre with Bluetooth.", 12500, "Sound", 5),
                ("Ramtons 4-Plate Gas Cooker", "Full-ignition 4-plate cooker with oven.", 21500, "Kitchen", 4),
                ("Solar Panel 200W", "Monocrystalline panel, 5-year warranty.", 7800, "Solar", 12),
                ("Wireless Earbuds Pro", "ENC earbuds, 30hrs playtime, fast charge.", 2200, "Accessories", 30),
                ("Electric Kettle 1.8L", "Fast-boil stainless steel kettle.", 1450, "Kitchen", 20),
            ]
            for n, d, p, cat, s in seed:
                conn.execute(text("INSERT INTO products(name,description,price,category,stock,created_at) VALUES(:n,:d,:p,:cat,:s,:c)"),
                             {"n": n, "d": d, "p": p, "cat": cat, "s": s, "c": iso_now()})
            conn.commit()

        admin_row = conn.execute(text("SELECT * FROM admin_auth LIMIT 1")).fetchone()
        if not admin_row:
            conn.execute(text("INSERT INTO admin_auth(password_hash) VALUES(:p)"), {"p": generate_password_hash(ADMIN_PASSWORD)})
            conn.commit()


init_db()


@app.template_filter("kes")
def kes(v):
    return f"KSh {float(v or 0):,.2f}"


CATEGORY_EMOJI = {"TVs": "📺", "Sound": "🔊", "Kitchen": "🍳", "Solar": "☀️",
                  "Accessories": "🎧", "Phones": "📱", "Electronics": "🔌"}


@app.template_filter("emoji")
def emoji(category):
    return CATEGORY_EMOJI.get(category, "🔌")


@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    return dict(BUSINESS=BUSINESS, cart_count=sum(cart.values()),
                year=now_dt().year, sim=SIMULATE_PAYMENTS, user=session.get("user"), admin=session.get("admin"))


def cart_items():
    cart = session.get("cart", {})
    items, total, count = [], 0.0, 0
    if cart:
        with db() as conn:
            keys = [int(i) for i in cart.keys()]
            placeholders = ",".join(f":id_{i}" for i in range(len(keys)))
            params = {f"id_{i}": val for i, val in enumerate(keys)}
            
            query = text(f"SELECT * FROM products WHERE id IN ({placeholders})")
            rows = conn.execute(query, params).mappings().fetchall()
            
        for r in rows:
            qty = int(cart[str(r["id"])])
            items.append({"id": r["id"], "name": r["name"], "price": r["price"],
                          "image": r["image"], "qty": qty, "line": r["price"] * qty})
            total += r["price"] * qty
            count += qty
    return items, total, count


def load_order(order_no):
    with db() as conn:
        o = conn.execute(text("SELECT * FROM orders WHERE order_no=:o"), {"o": order_no}).mappings().fetchone()
        if not o:
            return None, []
        items = conn.execute(text("SELECT * FROM order_items WHERE order_id=:oid"), {"oid": o["id"]}).mappings().fetchall()
    return o, items


def finalize_payment(order_no, mpesa_receipt, method):
    with db() as conn:
        o = conn.execute(text("SELECT * FROM orders WHERE order_no=:o"), {"o": order_no}).mappings().fetchone()
        if not o or o["status"] == "paid":
            return
        seq_res = conn.execute(text("SELECT COUNT(*) FROM orders WHERE status='paid' AND SUBSTR(paid_at,1,10)=:t"), {"t": today()}).fetchone()
        seq = (seq_res[0] if seq_res else 0) + 1
        stamp = Etims().stamp(order_no, o["total"], o["created_at"], seq, buyer_name=o["customer_name"])
        conn.execute(text("""UPDATE orders SET status='paid', paid_at=:paid_at, mpesa_receipt=:receipt,
                     payment_method=:method, etims_rcpt_no=:rcpt, etims_cu_no=:cu, etims_qr=:qr,
                     etims_status=:status WHERE order_no=:order_no"""),
                     {"paid_at": iso_now(), "receipt": mpesa_receipt, "method": method,
                      "rcpt": stamp["rcpt"], "cu": stamp["cu"], "qr": stamp["qr"],
                      "status": stamp["status"], "order_no": order_no})
        conn.commit()


def send_otp_email(to_email, otp_code):
    if not MAIL_USERNAME or MAIL_USERNAME in ("your_actual_gmail@gmail.com", "****@gmail.com"):
        print(f"[SECURE LOG] SMTP not configured. Target: {to_email} | OTP: {otp_code}")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = MAIL_USERNAME
        msg["To"] = to_email
        msg["Subject"] = "Your TT Electronics Verification Code"

        body = f"""
        Hello,

        Your verification code for TT Electronics & Electronics is: {otp_code}

        This code expires in 15 minutes. If you did not request this, please ignore this email.

        Regards,
        TT Electronics Bomet CBD (0718930255/0751754591)
        """
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.sendmail(MAIL_USERNAME, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"SMTP Dispatch Error (non-fatal): {e}")
        return False


# --- STOREFRONT ROUTES ---

@app.route("/account-setup", methods=["GET", "POST"])
def account_setup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        identifier = normalize_phone(request.form.get("identifier", "").strip())
        gmail = request.form.get("gmail", "").strip()
        password = request.form.get("password", "").strip()

        # Enforce name, valid phone, email, and strong password (min 8 chars)
        if not name or len(identifier) < 12 or not gmail or len(password) < 8:
            flash("Full Name, valid Phone Number, Gmail, and a Strong Password (minimum 8 characters) are required.")
            return redirect(url_for("account_setup"))

        otp = str(random.randint(100000, 999999))
        hashed_pwd = generate_password_hash(password)

        with db() as conn:
            conn.execute(text("DELETE FROM users WHERE identifier=:id"), {"id": identifier})
            conn.execute(text("""INSERT INTO users(name, identifier, gmail, password, verified, created_at)
                         VALUES(:name, :id, :g, :p, 0, :c)"""),
                         {"name": name, "id": identifier, "g": gmail, "p": hashed_pwd, "c": iso_now()})
            conn.execute(text("DELETE FROM otps WHERE gmail=:g"), {"g": gmail})
            conn.execute(text("INSERT INTO otps(gmail, otp_code, expires_at) VALUES(:g, :o, :e)"),
                         {"g": gmail, "o": otp, "e": (now_dt() + timedelta(minutes=15)).isoformat()})
            conn.commit()

        sent = send_otp_email(gmail, otp)
        session["pending_gmail"] = gmail
        session["pending_user"] = identifier
        session["pending_name"] = name

        if sent:
            flash(f"Verification code successfully sent via email to {gmail}. Please check your inbox.")
        else:
            flash("Verification request processed. Please check your terminal or email inbox for the code.")

        return redirect(url_for("verify_otp"))

    return render_template("account_setup.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    gmail = session.get("pending_gmail")
    if not gmail:
        return redirect(url_for("account_setup"))

    if request.method == "POST":
        otp_input = request.form.get("otp", "").strip()
        with db() as conn:
            record = conn.execute(text("SELECT * FROM otps WHERE gmail=:g AND otp_code=:o"), {"g": gmail, "o": otp_input}).mappings().fetchone()
            if record:
                user_id = session.get("pending_user")
                conn.execute(text("UPDATE users SET verified=1 WHERE identifier=:u"), {"u": user_id})
                conn.commit()

                session["user"] = user_id
                session["phone"] = user_id
                session["customer_name"] = session.get("pending_name", "")
                
                session.pop("pending_gmail", None)
                session.pop("pending_user", None)
                session.pop("pending_name", None)
                
                flash("Account verified successfully! Proceeding to checkout.")
                return redirect(url_for("checkout"))
            else:
                flash("Invalid OTP code. Please check your email and try again.")

    return render_template("verify_otp.html", gmail=gmail)


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    sql, args = "SELECT * FROM products WHERE 1=1", {}
    if q:
        sql += " AND (name LIKE :q OR description LIKE :q)"
        args["q"] = f"%{q}%"
    if cat:
        sql += " AND category = :cat"
        args["cat"] = cat
    sql += " ORDER BY id DESC"
    with db() as conn:
        products = conn.execute(text(sql), args).mappings().fetchall()
        cats_res = conn.execute(text("SELECT DISTINCT category FROM products ORDER BY category")).fetchall()
        cats = [r[0] for r in cats_res]
    return render_template("index.html", products=products, cats=cats, q=q, cat=cat)


@app.route("/product/<int:pid>")
def product(pid):
    with db() as conn:
        p = conn.execute(text("SELECT * FROM products WHERE id=:pid"), {"pid": pid}).mappings().fetchone()
    if not p:
        abort(404)
    return render_template("product.html", p=p)


@app.route("/uploads/<path:fn>")
def uploads(fn):
    return send_from_directory(app.config["UPLOAD_FOLDER"], fn)


@app.route("/add-to-cart", methods=["POST"])
def add_to_cart():
    pid = str(request.form.get("pid", "")).strip()
    if not pid:
        return redirect(url_for("index"))
    try:
        qty = max(1, int(request.form.get("qty", 1)))
    except ValueError:
        qty = 1
    cart = session.setdefault("cart", {})
    cart[pid] = cart.get(pid, 0) + qty
    session.modified = True
    flash("Added to cart")
    return redirect(request.referrer or url_for("index"))


@app.route("/cart")
def cart_page():
    items, total, count = cart_items()
    return render_template("cart.html", items=items, total=total)


@app.route("/update-cart", methods=["POST"])
def update_cart():
    pid = str(request.form.get("pid", ""))
    try:
        qty = int(request.form.get("qty", 0))
    except ValueError:
        qty = 0
    cart = session.setdefault("cart", {})
    if qty <= 0:
        cart.pop(pid, None)
    else:
        cart[pid] = qty
    session.modified = True
    return redirect(url_for("cart_page"))


@app.route("/clear-cart", methods=["POST"])
def clear_cart():
    session.pop("cart", None)
    return redirect(url_for("cart_page"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    if not session.get("user") or not session.get("phone"):
        flash("Security Requirement: Verify your phone number and email before proceeding to payment.")
        return redirect(url_for("account_setup"))

    items, total, count = cart_items()
    if not items:
        flash("Your cart is empty")
        return redirect(url_for("index"))

    verified_phone = session.get("phone")
    customer_name = session.get("customer_name", "")
    if not customer_name:
        with db() as conn:
            u_row = conn.execute(text("SELECT name FROM users WHERE identifier=:id"), {"id": verified_phone}).mappings().fetchone()
            if u_row:
                customer_name = u_row["name"] or ""
                session["customer_name"] = customer_name

    if request.method == "POST":
        name = request.form.get("name", "").strip() or customer_name
        delivery = request.form.get("delivery", "Pickup - Bomet CBD shop").strip()
        phone = normalize_phone(verified_phone)

        if not name or len(phone) < 12:
            flash("Please enter your full name. Your phone number is securely locked from verification.")
            return render_template("checkout.html", items=items, total=total, verified_phone=verified_phone, customer_name=customer_name)
        
        order_no = f"TT-{now_dt().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        vat = round(total * 16 / 116, 2)
        with db() as conn:
            conn.execute(text("""INSERT INTO orders(order_no, customer_name,
                customer_phone, delivery, total, vat, created_at)
                VALUES(:o, :n, :p, :d, :t, :v, :c)"""),
                {"o": order_no, "n": name, "p": phone, "d": delivery, "t": total, "v": vat, "c": iso_now()})
            
            oid_res = conn.execute(text("SELECT id FROM orders WHERE order_no=:o"), {"o": order_no}).fetchone()
            oid = oid_res[0] if oid_res else None

            for it in items:
                conn.execute(text("""INSERT INTO order_items(order_id, product_id, name, price, qty)
                             VALUES(:oid, :pid, :n, :p, :q)"""),
                          {"oid": oid, "pid": it["id"], "n": it["name"], "p": it["price"], "q": it["qty"]})
                conn.execute(text("UPDATE products SET stock = MAX(stock - :q, 0) WHERE id=:pid"),
                          {"q": it["qty"], "pid": it["id"]})
            conn.commit()

        session.pop("cart", None)
        return redirect(url_for("pay_page", order_no=order_no))
        
    return render_template("checkout.html", items=items, total=total, verified_phone=verified_phone, customer_name=customer_name)


@app.route("/pay/<order_no>")
def pay_page(order_no):
    o, items = load_order(order_no)
    if not o:
        abort(404)
    if o["status"] == "paid":
        return redirect(url_for("receipt", order_no=order_no))
    return render_template("pay.html", o=o, items=items)


@app.route("/api/pay/<order_no>", methods=["POST"])
def api_pay(order_no):
    o, _ = load_order(order_no)
    if not o:
        return jsonify({"ok": False, "msg": "Order not found"})
    if o["status"] == "paid":
        return jsonify({"ok": True, "msg": "Already paid"})
    try:
        resp = Daraja().stk_push(o["total"], o["customer_phone"], order_no,
                                 "TT Electronics",
                                 f"{PUBLIC_BASE_URL}/api/mpesa/stk/callback")
        with db() as conn:
            conn.execute(text("UPDATE orders SET checkout_request_id=:req WHERE order_no=:o"),
                      {"req": resp.get("CheckoutRequestID"), "o": order_no})
            conn.commit()
        return jsonify({"ok": True, "msg": "M-Pesa prompt sent. Enter your PIN on your phone."})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route("/api/simulate/<order_no>", methods=["POST"])
def api_simulate_pay(order_no):
    o, _ = load_order(order_no)
    if not o:
        return jsonify({"ok": False, "msg": "Order not found"})
    
    fake_receipt = f"TST{random.randint(100000000, 999999999)}"
    
    with db() as conn:
        conn.execute(text("""INSERT INTO transactions(trans_type, trans_id, trans_time, phone, amount, till, mpesa_receipt, order_no, created_at)
                             VALUES(:t_type, :t_id, :t_time, :phone, :amount, :till, :receipt, :order_no, :c)"""),
                     {
                         "t_type": "C2B_SIMULATION",
                         "t_id": fake_receipt,
                         "t_time": iso_now(),
                         "phone": o["customer_phone"],
                         "amount": o["total"],
                         "till": "9232378",
                         "receipt": fake_receipt,
                         "order_no": order_no,
                         "c": iso_now()
                     })
        conn.commit()

    finalize_payment(order_no, fake_receipt, "mpesa_simulation")
    return jsonify({"ok": True, "receipt": fake_receipt})


@app.route("/api/order-status/<order_no>")
def order_status(order_no):
    o, _ = load_order(order_no)
    if not o:
        return jsonify({"status": "not_found"})
    return jsonify({"status": o["status"], "mpesa_receipt": o["mpesa_receipt"]})


@app.route("/api/mpesa/stk/callback", methods=["POST"])
def mpesa_stk_callback():
    data = request.get_json(silent=True) or {}
    try:
        body = data.get("Body", {}).get("stkCallback", {})
        result_code = body.get("ResultCode")
        checkout_req_id = body.get("CheckoutRequestID")
        
        if result_code == 0:
            metadata = body.get("CallbackMetadata", {}).get("Item", [])
            receipt_code = ""
            phone_val = ""
            amount_val = 0.0
            for item in metadata:
                if item.get("Name") == "MpesaReceiptNumber":
                    receipt_code = str(item.get("Value", ""))
                if item.get("Name") == "PhoneNumber":
                    phone_val = str(item.get("Value", ""))
                if item.get("Name") == "Amount":
                    amount_val = float(item.get("Value", 0.0))
            
            with db() as conn:
                o = conn.execute(text("SELECT order_no, customer_phone, total FROM orders WHERE checkout_request_id=:req"), {"req": checkout_req_id}).fetchone()
                if o:
                    order_no = o[0]
                    phone = phone_val or o[1]
                    amount = amount_val or o[2]
                    
                    conn.execute(text("""INSERT INTO transactions(trans_type, trans_id, trans_time, phone, amount, till, mpesa_receipt, order_no, created_at)
                                         VALUES(:t_type, :t_id, :t_time, :phone, :amount, :till, :receipt, :order_no, :c)"""),
                                 {
                                     "t_type": "STK_CALLBACK",
                                     "t_id": receipt_code,
                                     "t_time": iso_now(),
                                     "phone": phone,
                                     "amount": amount,
                                     "till": "9232378",
                                     "receipt": receipt_code,
                                     "order_no": order_no,
                                     "c": iso_now()
                                 })
                    conn.commit()
                    finalize_payment(order_no, receipt_code, "mpesa")
        return jsonify({"ResultCode": 0, "ResultDesc": "Success"})
    except Exception as e:
        return jsonify({"ResultCode": 1, "ResultDesc": str(e)})


@app.route("/receipt/<order_no>")
def receipt(order_no):
    o, items = load_order(order_no)
    if not o:
        abort(404)
    
    qr_uri = None
    if o.get("etims_qr"):
        img = qrcode.make(o["etims_qr"])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    return render_template("receipt.html", o=o, items=items, qr_uri=qr_uri)


# --- ADMIN PORTAL ROUTES ---

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        with db() as conn:
            row = conn.execute(text("SELECT password_hash FROM admin_auth LIMIT 1")).fetchone()
            if row and check_password_hash(row[0], pwd):
                session["admin"] = True
                flash("Admin access granted.")
                return redirect(url_for("admin_dashboard"))
        flash("Invalid admin password.")
    return render_template("admin/admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        flash("Admin access required.")
        return redirect(url_for("admin_login"))
    with db() as conn:
        orders = conn.execute(text("SELECT * FROM orders ORDER BY id DESC")).mappings().fetchall()
        products = conn.execute(text("SELECT * FROM products ORDER BY id DESC")).mappings().fetchall()
        transactions = conn.execute(text("SELECT * FROM transactions ORDER BY id DESC")).mappings().fetchall()
        
        today_str = today()
        today_paid = [o for o in orders if o["status"] == "paid" and str(o.get("paid_at", "")).startswith(today_str)]
        total_amt = sum(o["total"] for o in today_paid)
        total_vat = sum(o["vat"] for o in today_paid)
        
        sales = {
            "amt": total_amt,
            "n": len(today_paid),
            "vat": total_vat
        }
        
        pending = sum(1 for o in orders if o["status"] == "pending")
        recent = orders[:5]
        low = [p for p in products if p["stock"] <= 10]
        
    return render_template("admin/admin_dashboard.html",
                           orders=orders,
                           products=products,
                           transactions=transactions,
                           sales=sales,
                           pending=pending,
                           recent=recent,
                           low=low)


@app.route("/admin/products")
def admin_products():
    if not session.get("admin"):
        flash("Admin access required.")
        return redirect(url_for("admin_login"))
        
    edit_id = request.args.get("edit")
    editing = None
    
    with db() as conn:
        if edit_id:
            editing = conn.execute(
                text("SELECT * FROM products WHERE id=:id"), 
                {"id": edit_id}
            ).mappings().fetchone()
        products = conn.execute(
            text("SELECT * FROM products ORDER BY id DESC")
        ).mappings().fetchall()
        
    return render_template("admin/admin_products.html", products=products, editing=editing)


@app.route("/admin/product/save", methods=["POST"])
def admin_save_product():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
        
    action = request.form.get("action", "add")
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()
    price = float(request.form.get("price", 0))
    category = request.form.get("category", "Electronics").strip()
    stock_input = int(request.form.get("stock", 0))
    
    file = request.files.get("image")
    filename = request.form.get("old_image", "")
    if file and file.filename:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    with db() as conn:
        if action == "edit":
            pid = request.form.get("pid")
            conn.execute(text("""
                UPDATE products 
                SET name=:n, description=:d, price=:p, category=:cat, 
                    stock = stock + :s, image=:img 
                WHERE id=:pid
            """), {"n": name, "d": desc, "p": price, "cat": category, "s": stock_input, "img": filename, "pid": pid})
            flash("Product details updated and incoming stock added successfully.")
        else:
            conn.execute(text("""
                INSERT INTO products(name, description, price, category, stock, image, created_at)
                VALUES(:n, :d, :p, :cat, :s, :img, :c)
            """), {"n": name, "d": desc, "p": price, "cat": category, "s": stock_input, "img": filename, "c": iso_now()})
            flash("New product created successfully.")
        conn.commit()
    return redirect(url_for("admin_products"))


@app.route("/admin/product/delete/<int:pid>", methods=["POST"])
def admin_delete_product(pid):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
        
    admin_pin = request.form.get("admin_pin", "").strip()
    
    with db() as conn:
        row = conn.execute(text("SELECT password_hash FROM admin_auth LIMIT 1")).fetchone()
        if not row or not check_password_hash(row[0], admin_pin):
            flash("Security check failed: Incorrect Admin PIN/Password. Deletion aborted.")
            return redirect(url_for("admin_products"))
            
        conn.execute(text("DELETE FROM products WHERE id=:pid"), {"pid": pid})
        conn.commit()
        
    flash("Product permanently removed from catalog.")
    return redirect(url_for("admin_products"))


@app.route("/admin/orders")
def admin_orders():
    if not session.get("admin"):
        flash("Admin access required.")
        return redirect(url_for("admin_login"))
    with db() as conn:
        orders = []
        rows = conn.execute(text("SELECT * FROM orders ORDER BY id DESC")).mappings().fetchall()
        for r in rows:
            o_dict = dict(r)
            item_count_res = conn.execute(text("SELECT SUM(qty) FROM order_items WHERE order_id=:oid"), {"oid": r["id"]}).fetchone()
            o_dict["n_items"] = int(item_count_res[0] if item_count_res and item_count_res[0] else 0)
            orders.append(o_dict)
    return render_template("admin/admin_orders.html", orders=orders)


@app.route("/admin/transactions")
def admin_transactions():
    if not session.get("admin"):
        flash("Admin access required.")
        return redirect(url_for("admin_login"))
    with db() as conn:
        transactions = conn.execute(text("SELECT * FROM transactions ORDER BY id DESC")).mappings().fetchall()
    return render_template("admin/admin_transactions.html", txns=transactions)


@app.route("/admin/report")
def admin_report():
    if not session.get("admin"):
        flash("Admin access required.")
        return redirect(url_for("admin_login"))
        
    start_date = request.args.get("start_date", (now_dt() - timedelta(days=30)).strftime("%Y-%m-%d"))
    end_date = request.args.get("end_date", today())
    
    with db() as conn:
        orders_query = text("""
            SELECT * FROM orders 
            WHERE status='paid' AND SUBSTR(COALESCE(paid_at, created_at), 1, 10) >= :start 
            AND SUBSTR(COALESCE(paid_at, created_at), 1, 10) <= :end 
            ORDER BY id DESC
        """)
        paid_orders = conn.execute(orders_query, {"start": start_date, "end": end_date}).mappings().fetchall()
        
        gross_revenue = sum(o["total"] for o in paid_orders)
        total_vat = sum(o["vat"] for o in paid_orders)
        net_revenue = gross_revenue - total_vat
        
        try:
            d1 = datetime.strptime(start_date, "%Y-%m-%d")
            d2 = datetime.strptime(end_date, "%Y-%m-%d")
            num_days = max(1, (d2 - d1).days + 1)
        except Exception:
            num_days = 1
            
        order_ids = [o["id"] for o in paid_orders]
        by_product = []
        total_items_sold = 0
        
        if order_ids:
            placeholders = ",".join(f":oid_{i}" for i in range(len(order_ids)))
            params = {f"oid_{i}": val for i, val in enumerate(order_ids)}
            prod_query = text(f"""
                SELECT name, SUM(qty) as qty, SUM(price * qty) as amount 
                FROM order_items WHERE order_id IN ({placeholders}) 
                GROUP BY name ORDER BY qty DESC
            """)
            prod_rows = conn.execute(prod_query, params).mappings().fetchall()
            for pr in prod_rows:
                by_product.append(dict(pr))
                total_items_sold += int(pr["qty"] or 0)
                
        txn_query = text("""
            SELECT COUNT(*) as n, SUM(amount) as amt FROM transactions 
            WHERE SUBSTR(created_at, 1, 10) >= :start AND SUBSTR(created_at, 1, 10) <= :end
        """)
        txn_res = conn.execute(txn_query, {"start": start_date, "end": end_date}).fetchone()
        till_txn = {
            "n": int(txn_res[0] if txn_res and txn_res[0] else 0),
            "amt": float(txn_res[1] if txn_res and txn_res[1] else 0.0)
        }
        
        totals = {
            "gross": gross_revenue,
            "net": net_revenue,
            "vat": total_vat,
            "orders": len(paid_orders),
            "total_items_sold": total_items_sold,
            "num_days": num_days,
            "avg_daily_sales": gross_revenue / num_days if num_days > 0 else gross_revenue
        }
        
    return render_template("admin/admin_report.html",
                           orders=paid_orders,
                           by_product=by_product,
                           totals=totals,
                           till_txn=till_txn,
                           start_date=start_date,
                           end_date=end_date)


@app.route("/admin/setup")
def admin_setup():
    if not session.get("admin"):
        flash("Admin access required.")
        return redirect(url_for("admin_login"))
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/order/mark-paid/<order_no>", methods=["POST"])
def admin_mark_paid(order_no):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    fake_receipt = f"MANUAL{random.randint(100000000, 999999999)}"
    
    with db() as conn:
        o = conn.execute(text("SELECT customer_phone, total FROM orders WHERE order_no=:o"), {"o": order_no}).mappings().fetchone()
        if o:
            conn.execute(text("""INSERT INTO transactions(trans_type, trans_id, trans_time, phone, amount, till, mpesa_receipt, order_no, created_at)
                                 VALUES(:t_type, :t_id, :t_time, :phone, :amount, :till, :receipt, :order_no, :c)"""),
                         {
                             "t_type": "MANUAL_MARK_PAID",
                             "t_id": fake_receipt,
                             "t_time": iso_now(),
                             "phone": o["customer_phone"],
                             "amount": o["total"],
                             "till": "9232378",
                             "receipt": fake_receipt,
                             "order_no": order_no,
                             "c": iso_now()
                         })
            conn.commit()

    finalize_payment(order_no, fake_receipt, "manual_admin")
    flash(f"Order {order_no} marked as paid successfully.")
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/register-c2b", methods=["POST"])
def admin_register_c2b():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    try:
        res = Daraja().register_c2b_urls(
            f"{PUBLIC_BASE_URL}/api/mpesa/c2b/validation",
            f"{PUBLIC_BASE_URL}/api/mpesa/c2b/confirmation"
        )
        flash(f"C2B URLs registered successfully: {res.get('ResponseDescription', 'Success')}")
    except Exception as e:
        flash(f"Failed to register C2B URLs: {e}")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/api/chart-data")
def admin_chart_data():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    with db() as conn:
        labels = []
        amounts = []
        for i in range(6, -1, -1):
            d = now_dt() - timedelta(days=i)
            day_str = d.strftime("%Y-%m-%d")
            labels.append(d.strftime("%b %d"))
            
            res = conn.execute(text("SELECT SUM(total) FROM orders WHERE status='paid' AND SUBSTR(paid_at,1,10)=:t"), {"t": day_str}).fetchone()
            amounts.append(float(res[0] or 0))
            
        methods = [
            {"name": "M-Pesa", "amount": sum(amounts)},
            {"name": "Cash", "amount": 0.0},
            {"name": "Card", "amount": 0.0}
        ]
    return jsonify({"labels": labels, "amounts": amounts, "methods": methods})


@app.route("/admin/order/status/<order_no>", methods=["POST"])
def admin_update_order(order_no):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    status = request.form.get("status", "pending")
    with db() as conn:
        conn.execute(text("UPDATE orders SET status=:s WHERE order_no=:o"), {"s": status, "o": order_no})
        conn.commit()
    flash("Order status updated.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    flash("Logged out from admin portal.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5002)