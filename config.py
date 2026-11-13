import os
import secrets
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

BUSINESS = {
    "name": os.environ.get("BUSINESS_NAME", "TT ELECTRONICS AND ELECTRONICS"),
    "till": os.environ.get("MPESA_TILL", "9232378"),
    "kra_pin": os.environ.get("KRA_PIN", "A009779057U"),
}

PUBLIC_BASE_URL = os.environ.get("https://ttelectronicsandgeneralshop.co.ke/", "http://127.0.0.1:5002").rstrip("/")
SIMULATE_PAYMENTS = os.environ.get("SIMULATE_PAYMENTS", "true").lower() in ("true", "1", "yes")
VAT_RATE = float(os.environ.get("VAT_RATE", "16.0"))

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///electro.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

MPESA_ENV = os.environ.get("MPESA_ENV", "production").lower()
MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET", "")
MPESA_PASSKEY = os.environ.get("MPESA_PASSKEY", "")
DARAJA_SHORTCODE = os.environ.get("DARAJA_SHORTCODE", "")

ETIMS_MODE = os.environ.get("ETIMS_MODE", "offline").lower()
ETIMS_API_URL = os.environ.get("ETIMS_API_URL", "")
ETIMS_USERNAME = os.environ.get("ETIMS_USERNAME", "")
ETIMS_PASSWORD = os.environ.get("ETIMS_PASSWORD", "")

MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")