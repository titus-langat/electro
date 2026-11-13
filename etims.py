"""KRA eTIMS Bridge — Stamps receipts with cryptographic signatures and QR codes."""
import hashlib
import hmac
from datetime import datetime
import requests

from config import (BUSINESS, SECRET_KEY, VAT_RATE, ETIMS_MODE,
                    ETIMS_API_URL, ETIMS_USERNAME, ETIMS_PASSWORD)


def vat_split(total):
    """Kenyan retail prices are VAT-inclusive; extract the 16% tax component."""
    total = float(total)
    vat = round(total * VAT_RATE / (100 + VAT_RATE), 2)
    return round(total - vat, 2), vat


def _as_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def _rcv_code(method):
    m = (method or "").lower()
    if "mpesa" in m:
        return "MPESA"
    if "cash" in m:
        return "CASH"
    return (method or "OTHER").upper()[:12]


class Etims:
    def stamp(self, order_no, total, when, seq, buyer_name="", buyer_pin="", method="mpesa"):
        when = _as_dt(when)
        seq = int(seq)
        taxable, vat = vat_split(total)

        rcpt_no = f"{when.strftime('%y%m%d')}/{seq:04d}"
        cu_no = f"KE{BUSINESS['kra_pin']}{when.strftime('%Y%m%d')}{seq:06d}"

        fields = [
            ("AdrInfo", BUSINESS["name"]),
            ("Top", "NS"), ("Ty", "0102"), ("Tty", "SALE"),
            ("No", order_no),
            ("Tt", f"{taxable:.2f}"), ("Txbl", f"{taxable:.2f}"),
            ("TaxR", f"{VAT_RATE:.2f}"), ("Tax", f"{vat:.2f}"),
            ("Totp", f"{float(total):.2f}"),
            ("Dt", when.strftime("%Y%m%d%H%M%S")),
            ("Rcv", _rcv_code(method)),
            ("RCPTNO", rcpt_no), ("CUINNO", cu_no),
            ("PIN", BUSINESS["kra_pin"]), ("Till", BUSINESS["till"]),
            ("Bp", buyer_pin or ""),
        ]

        if not SECRET_KEY:
            raise ValueError("CRITICAL ERROR: SECRET_KEY is not defined. Cannot sign receipts.")

        secret = SECRET_KEY.encode()
        payload = ";".join(f"{k}={v}" for k, v in fields)
        sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:32].upper()
        qr = f"{payload};RSl={sig}"

        status = "STAMPED-LOCAL (offline mode)"
        if ETIMS_MODE == "live":
            ok, detail = self._transmit(order_no, total, when, buyer_name, buyer_pin)
            status = "SENT-OK" if ok else f"PENDING-RETRY ({detail})"

        return {
            "rcpt": rcpt_no, "cu": cu_no, "qr": qr, "status": status,
            "vat": vat, "taxable": taxable
        }

    def _transmit(self, order_no, total, when, buyer_name, buyer_pin):
        taxable, vat = vat_split(total)
        body = {
            "invoiceNumber": order_no,
            "dateTime": when.isoformat(),
            "traderSystemInvoiceNumber": order_no,
            "receiptType": "NS", "invoiceType": "0102",
            "taxRate": VAT_RATE, "taxableAmount": taxable,
            "taxAmount": vat, "totalAmount": float(total),
            "buyerName": buyer_name, "buyerPin": buyer_pin, "items": [],
        }
        try:
            r = requests.post(
                ETIMS_API_URL, json=body,
                auth=(ETIMS_USERNAME, ETIMS_PASSWORD), timeout=30
            )
            return r.status_code in (200, 201), f"HTTP {r.status_code}"
        except Exception as e:
            return False, e.__class__.__name__