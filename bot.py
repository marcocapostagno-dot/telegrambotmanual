import logging
import os
import re
from html import escape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TARGET_CHANNEL = os.getenv("TELEGRAM_TARGET_CHANNEL", "@capofferte").strip()
AMAZON_PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "").strip()
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DISCLOSURE = os.getenv(
    "POST_DISCLOSURE",
    "Questo post contiene link affiliati Amazon.",
).strip()

AMAZON_HOSTS = {
    "amazon.it",
    "www.amazon.it",
    "amzn.to",
    "www.amzn.to",
}

ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product/)([A-Z0-9]{10})", re.IGNORECASE)
SHORT_TEXT_RE = re.compile(r"^/post(?:@\w+)?\s+(.+)$", re.DOTALL)


def _check_config() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not TARGET_CHANNEL:
        raise RuntimeError("Missing TELEGRAM_TARGET_CHANNEL")
    if not AMAZON_PARTNER_TAG:
        raise RuntimeError("Missing AMAZON_PARTNER_TAG")
    if not ADMIN_USER_IDS:
        logger.warning("TELEGRAM_ADMIN_IDS non impostato: il bot accetterà messaggi da chiunque gli scriva in privato.")


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False
    if chat.type != "private":
        return False
    if not ADMIN_USER_IDS:
        return True
    return user.id in ADMIN_USER_IDS


def add_affiliate_tag(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["tag"] = AMAZON_PARTNER_TAG
    clean = parsed._replace(query=urlencode(query, doseq=True), fragment="")
    return urlunparse(clean)


def normalize_amazon_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if not raw_url.startswith(("http://", "https://")):
        raise ValueError("Il link deve iniziare con http:// o https://")

    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    if host not in AMAZON_HOSTS and "amazon.it" not in host and "amzn.to" not in host:
        raise ValueError("Il link non sembra un URL Amazon valido")

    clean_url = add_affiliate_tag(raw_url)
    return clean_url


def extract_url(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else None


def extract_asin(url: str) -> str | None:
    match = ASIN_RE.search(url)
    if match:
        return match.group(1).upper()
    return None


def build_caption(url: str, original_text: str) -> str:
    asin = extract_asin(url)
    lines = []

    cleaned_text = original_text.strip()
    if cleaned_text:
        lines.append(escape(cleaned_text))
    else:
        lines.append("Nuova offerta Amazon")

    if asin:
        lines.append(f"ASIN: <code>{escape(asin)}</code>")

    lines.append(f"<a href=\"{escape(url)}\">Apri offerta</a>")
    lines.append(escape(DISCLOSURE))
    return "\n".join(lines)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Mandami un link Amazon oppure usa /post link testo opzionale."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Uso rapido:\n"
        "/post https://www.amazon.it/dp/ASIN Testo opzionale\n\n"
        "Oppure incolla semplicemente un link Amazon in chat privata e il bot lo pubblicherà su canale."
    )


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    text = update.message.text or ""
    match = SHORT_TEXT_RE.match(text)
    if not match:
        await update.message.reply_text("Formato: /post LINK testo opzionale")
        return

    payload = match.group(1).strip()
    url = extract_url(payload)
    if not url:
        await update.message.reply_text("Non ho trovato un link nel comando.")
        return

    custom_text = payload.replace(url, "", 1).strip(" -\n")

    try:
        affiliate_url = normalize_amazon_url(url)
        caption = build_caption(affiliate_url, custom_text)
        await context.bot.send_message(
            chat_id=TARGET_CHANNEL,
            text=caption,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        await update.message.reply_text("Pubblicato su canale.")
    except Exception as exc:
        logger.exception("Errore pubblicazione comando /post: %s", exc)
        await update.message.reply_text(f"Errore: {exc}")


async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    text = update.message.text or ""
    url = extract_url(text)
    if not url:
        await update.message.reply_text("Mandami un link Amazon valido.")
        return

    custom_text = text.replace(url, "", 1).strip(" -\n")

    try:
        affiliate_url = normalize_amazon_url(url)
        caption = build_caption(affiliate_url, custom_text)
        await context.bot.send_message(
            chat_id=TARGET_CHANNEL,
            text=caption,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        await update.message.reply_text("Pubblicato su canale.")
    except Exception as exc:
        logger.exception("Errore pubblicazione da messaggio privato: %s", exc)
        await update.message.reply_text(f"Errore: {exc}")


def run() -> None:
    _check_config()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("post", post_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, private_message_handler))
    logger.info("Bot posting Amazon avviato")
    application.run_polling(drop_pending_updates=True)
