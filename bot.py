import json
import logging
import os
import re
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
STORE_FILE = Path(os.getenv("BOT_STORE_FILE", "bot_store.json")).expanduser()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
AMAZON_HOSTS = {"amazon.it", "www.amazon.it", "amzn.to", "www.amzn.to"}
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product/)([A-Z0-9]{10})", re.IGNORECASE)
SHORT_TEXT_RE = re.compile(r"^/post(?:@\w+)?\s+(.+)$", re.DOTALL)
URL_RE = re.compile(r"https?://\S+")
OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def load_store() -> dict:
    if not STORE_FILE.exists():
        return {"drafts": {}, "published": []}
    try:
        with STORE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("drafts", {})
        data.setdefault("published", [])
        return data
    except Exception:
        logger.exception("Impossibile leggere %s, ne creo uno nuovo", STORE_FILE)
        return {"drafts": {}, "published": []}


def save_store(store: dict) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STORE_FILE.open("w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


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

    return add_affiliate_tag(raw_url)


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def extract_asin(url: str) -> str | None:
    match = ASIN_RE.search(url)
    if match:
        return match.group(1).upper()
    return None


def fetch_page_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    return response.text


def extract_og_image(html: str) -> str | None:
    match = OG_IMAGE_RE.search(html)
    if match:
        return match.group(1).replace("&amp;", "&")
    return None


def extract_page_title(html: str) -> str | None:
    match = TITLE_RE.search(html)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    title = title.replace("Amazon.it: ", "").replace(": Amazon.it", "")
    return title[:220] if title else None


def draft_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Pubblica", callback_data=f"publish:{draft_id}"),
            InlineKeyboardButton("❌ Annulla", callback_data=f"cancel:{draft_id}"),
        ]]
    )


def published_key(url: str) -> str:
    asin = extract_asin(url)
    return f"asin:{asin}" if asin else f"url:{url}"


def build_caption(url: str, original_text: str, scraped_title: str | None = None) -> str:
    asin = extract_asin(url)
    lines = []
    cleaned_text = (original_text or "").strip()

    if cleaned_text:
        lines.append(escape(cleaned_text))
    elif scraped_title:
        lines.append(escape(scraped_title))
    else:
        lines.append("Nuova offerta Amazon")

    if asin:
        lines.append(f"ASIN: <code>{escape(asin)}</code>")

    lines.append(f"<a href=\"{escape(url)}\">Apri offerta</a>")
    lines.append(escape(DISCLOSURE))
    return "\n".join(lines)


def parse_submission_from_message(message: Message) -> dict:
    text = message.caption or message.text or ""
    url = extract_url(text)
    if not url:
        raise ValueError("Mandami un link Amazon valido nel testo o nella caption.")

    affiliate_url = normalize_amazon_url(url)
    custom_text = text.replace(url, "", 1).strip(" -\n")
    photo_file_id = None
    image_url = None
    scraped_title = None

    if message.photo:
        photo_file_id = message.photo[-1].file_id
    else:
        try:
            html = fetch_page_html(affiliate_url)
            image_url = extract_og_image(html)
            scraped_title = extract_page_title(html)
        except Exception as exc:
            logger.warning("Immagine automatica non trovata per %s: %s", affiliate_url, exc)

    caption = build_caption(affiliate_url, custom_text, scraped_title)

    return {
        "url": affiliate_url,
        "text": custom_text,
        "caption": caption,
        "asin": extract_asin(affiliate_url),
        "photo_file_id": photo_file_id,
        "image_url": image_url,
        "scraped_title": scraped_title,
    }


def save_draft(user_id: int, draft: dict) -> str:
    store = load_store()
    draft_id = f"{user_id}_{len(store['drafts']) + 1}"
    store["drafts"][draft_id] = draft
    save_store(store)
    return draft_id


def get_draft(draft_id: str) -> dict | None:
    store = load_store()
    return store.get("drafts", {}).get(draft_id)


def delete_draft(draft_id: str) -> None:
    store = load_store()
    store.get("drafts", {}).pop(draft_id, None)
    save_store(store)


def is_duplicate(url: str) -> bool:
    key = published_key(url)
    store = load_store()
    return key in store.get("published", [])


def mark_published(url: str) -> None:
    key = published_key(url)
    store = load_store()
    published = store.get("published", [])
    if key not in published:
        published.append(key)
    store["published"] = published[-500:]
    save_store(store)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Mandami un link Amazon, oppure foto + caption con link Amazon. Ti preparo una preview privata con immagine quando disponibile."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Uso rapido:\n"
        "1) Invia un link Amazon in privato\n"
        "2) Oppure invia foto + caption con link Amazon\n"
        "3) Ricevi anteprima privata con immagine\n"
        "4) Premi Pubblica o Annulla\n\n"
        "Comando supportato:\n"
        "/post https://www.amazon.it/dp/ASIN Testo opzionale"
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
    original_text = update.message.text
    update.message.text = payload
    try:
        await create_preview_from_message(update, context, update.message)
    finally:
        update.message.text = original_text


async def send_preview(message: Message, draft_id: str, draft: dict) -> None:
    preview_header = "📝 Anteprima privata\n\n"
    if draft.get("photo_file_id"):
        await message.reply_photo(
            photo=draft["photo_file_id"],
            caption=preview_header + draft["caption"],
            parse_mode=ParseMode.HTML,
            reply_markup=draft_keyboard(draft_id),
        )
        return

    if draft.get("image_url"):
        try:
            await message.reply_photo(
                photo=draft["image_url"],
                caption=preview_header + draft["caption"],
                parse_mode=ParseMode.HTML,
                reply_markup=draft_keyboard(draft_id),
            )
            return
        except Exception as exc:
            logger.warning("Preview foto da URL fallita, fallback testo: %s", exc)

    await message.reply_text(
        preview_header + draft["caption"],
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False,
        reply_markup=draft_keyboard(draft_id),
    )


async def create_preview_from_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source_message: Message | None = None,
) -> None:
    message = source_message or update.message

    try:
        draft = parse_submission_from_message(message)
        if is_duplicate(draft["url"]):
            await message.reply_text("Questo prodotto sembra già pubblicato sul canale.")
            return

        draft_id = save_draft(update.effective_user.id, draft)
        await send_preview(message, draft_id, draft)
    except Exception as exc:
        logger.exception("Errore creazione preview: %s", exc)
        await message.reply_text(f"Errore: {exc}")


async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await create_preview_from_message(update, context)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    if not is_allowed(update):
        return

    data = query.data or ""
    if ":" not in data:
        return

    action, draft_id = data.split(":", 1)
    draft = get_draft(draft_id)

    if not draft:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Bozza non trovata o già usata.")
        return

    if action == "cancel":
        delete_draft(draft_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Bozza annullata.")
        return

    if action == "publish":
        try:
            if is_duplicate(draft["url"]):
                delete_draft(draft_id)
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("Prodotto già pubblicato in precedenza.")
                return

            sent = False
            if draft.get("photo_file_id"):
                await context.bot.send_photo(
                    chat_id=TARGET_CHANNEL,
                    photo=draft["photo_file_id"],
                    caption=draft["caption"],
                    parse_mode=ParseMode.HTML,
                )
                sent = True
            elif draft.get("image_url"):
                try:
                    await context.bot.send_photo(
                        chat_id=TARGET_CHANNEL,
                        photo=draft["image_url"],
                        caption=draft["caption"],
                        parse_mode=ParseMode.HTML,
                    )
                    sent = True
                except Exception as exc:
                    logger.warning("Invio foto da URL fallito, fallback testo: %s", exc)

            if not sent:
                await context.bot.send_message(
                    chat_id=TARGET_CHANNEL,
                    text=draft["caption"],
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )

            mark_published(draft["url"])
            delete_draft(draft_id)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Pubblicato su canale.")
        except Exception as exc:
            logger.exception("Errore pubblicazione bozza: %s", exc)
            await query.message.reply_text(f"Errore pubblicazione: {exc}")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    store = load_store()
    store["drafts"] = {}
    save_store(store)
    await update.message.reply_text("Bozze cancellate.")


async def reset_published_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    store = load_store()
    store["published"] = []
    save_store(store)
    await update.message.reply_text("Storico pubblicazioni azzerato.")


def run() -> None:
    _check_config()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("post", post_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("resetpublished", reset_published_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
            private_message_handler,
        )
    )
    logger.info("Bot Amazon con immagini automatiche avviato")
    application.run_polling(drop_pending_updates=True)
