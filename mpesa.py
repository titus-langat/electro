import base64
from datetime import datetime
import requests

from config import (MPESA_ENV, MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET,
                    MPESA_PASSKEY, DARAJA_SHORTCODE, BUSINESS)

BASE = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "live": "https://api.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}[MPESA_ENV]


def normalize_phone(phone: str) -> str:
    """07.. / 01.. / +254.. -> 2547|2541..."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("254"):
        return digits
    if digits.startswith("0"):
        return "254" + digits[1:]
    if len(digits) == 9:
        return "254" + digits
    return digits


class Daraja:
    def __init__(self):
        self._token = None

    def token(self):
        if self._token:
            return self._token
        r = requests.get(
            f"{BASE}/oauth/v1/generate?grant_type=client_credentials",
            auth=(MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET), timeout=30)
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def _headers(self):
        return {"Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json"}

    @staticmethod
    def _timestamp():
        return datetime.now().strftime("%Y%m%d%H%M%S")

    def _password(self, ts):
        raw = f"{DARAJA_SHORTCODE}{MPESA_PASSKEY}{ts}".encode()
        return base64.b64encode(raw).decode()

    def stk_push(self, amount, phone, account_ref, description, callback_url):
        """Sends the M-Pesa prompt to the customer's phone."""
        ts = self._timestamp()
        
        if MPESA_ENV == "sandbox":
            txn_type = "CustomerPayBillOnline"
            party_b = DARAJA_SHORTCODE
            if not callback_url or "YOUR-NGROK" in callback_url or "127.0.0.1" in callback_url or "localhost" in callback_url:
                callback_url = "https://mydomain.com/api/mpesa/stk/callback"
        else:
            txn_type = "CustomerBuyGoodsOnline"
            party_b = BUSINESS["till"]

        payload = {
            "BusinessShortCode": DARAJA_SHORTCODE,
            "Password": self._password(ts),
            "Timestamp": ts,
            "TransactionType": txn_type,
            "Amount": int(round(float(amount))),
            "PartyA": normalize_phone(phone),
            "PartyB": party_b,
            "PhoneNumber": normalize_phone(phone),
            "CallBackURL": callback_url,
            "AccountReference": account_ref[:12],
            "TransactionDesc": description[:13],
        }
        r = requests.post(f"{BASE}/mpesa/stkpush/v1/processrequest",
                          json=payload, headers=self._headers(), timeout=30)
        data = r.json()
        if r.status_code != 200 or data.get("ResponseCode") != "0":
            raise RuntimeError(data.get("errorMessage") or str(data))
        return data

    def stk_query(self, checkout_request_id):
        ts = self._timestamp()
        payload = {"BusinessShortCode": DARAJA_SHORTCODE,
                   "Password": self._password(ts), "Timestamp": ts,
                   "CheckoutRequestID": checkout_request_id}
        r = requests.post(f"{BASE}/mpesa/stkpushquery/v1/query",
                          json=payload, headers=self._headers(), timeout=30)
        return r.json()

    def register_c2b_urls(self, validation_url, confirmation_url):
        payload = {
            "ShortCode": BUSINESS["till"] if MPESA_ENV in ("production", "live") else DARAJA_SHORTCODE,
            "ResponseType": "Completed",
            "ConfirmationURL": confirmation_url,
            "ValidationURL": validation_url,
        }
        r = requests.post(f"{BASE}/mpesa/c2b/v1/registerurl",
                          json=payload, headers=self._headers(), timeout=30)
        return r.json()