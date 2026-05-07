import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_TARGET_CHANNEL = os.getenv("TELEGRAM_TARGET_CHANNEL", "@capofferte").strip()
TELEGRAM_ADMIN_IDS = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
AMAZON_PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "").strip()
POST_DISCLOSURE = os.getenv("POST_DISCLOSURE", "Questo post contiene link affiliati Amazon.").strip()
BRAND_TAG = os.getenv("BRAND_TAG", "@capofferte").strip()
DEFAULT_BADGE = os.getenv("DEFAULT_BADGE", "TOP DEAL").strip()
BOT_STORE_FILE = os.getenv("BOT_STORE_FILE", "bot_store.json").strip()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
