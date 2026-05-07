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
BRAND_TAG = os.getenv("BRAND_TAG", "@capofferte").strip()
DEFAULT_BADGE = os.getenv("DEFAULT_BADGE", "TOP DEAL").strip()
STORE_FILE = Path(os.getenv("BOT_STORE_FILE", "bot_store.json")).expanduser()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
AMAZON_HOSTS = {"amazon.it", "www.amazon.it", "amzn.to", "www.amzn.to"}
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product/)([A-Z0-9]{10})", re.IGNORECASE)
SHORT_TEXT_RE = re.compile(r"^/post(?:@\w+)?\s+(.+)$", re.DOTALL)
URL_RE = re.compile(r"https?://\S+")
OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
FIELD_RE = re.compile(r"^(titolo|prezzo|prima|originale|sconto|badge|categoria)\s*:\s*(.+)$", re.IGNORECASE)
NUMBER_RE = re.compile(r"(\d+(?:[\.,]\d+)?)")


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


def parse_structured_fields(text: str) -> dict:
    fields = {}
    extra_lines = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = FIELD_RE.match(line)
        if match:
            key = match.group(1).lower()
            value = match.group(2).strip()
            fields[key] = value
        else:
            extra_lines.append(line)

    if extra_lines and "titolo" not in fields:
        fields["titolo"] = " ".join(extra_lines).strip()

    return fields


def parse_price_value(value: str | None) -> float | None:
    if not value:
        return None
    match = NUMBER_RE.search(value.replace("€", "").replace(" ", ""))
    if not match:
        return None
    raw = match.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_discount_percent(value: str | None) -> int | None:
    if not value:
        return None
    match = NUMBER_RE.search(value)
    if not match:
        return None
    try:
        return int(float(match.group(1).replace(",", ".")))
    except ValueError:
        return None


def compute_discount_percent(discounted_price: str | None, original_price: str | None) -> int | None:
    discounted = parse_price_value(discounted_price)
    original = parse_price_value(original_price)
    if discounted is None or original is None or original <= 0 or discounted >= original:
        return None
    return round((1 - (discounted / original)) * 100)


def choose_badge_and_intro(
    explicit_badge: str | None,
    discounted_price: str | None,
    original_price: str | None,
    discount_label: str | None,
) -> tuple[str, str, str]:
    if explicit_badge:
        badge = explicit_badge.strip()
    else:
        percent = parse_discount_percent(discount_label)
        if percent is None:
            percent = compute_discount_percent(discounted_price, original_price)

        price_value = parse_price_value(discounted_price)

        if percent is not None and percent >= 70:
            badge = "ERRORE PREZZO"
        elif price_value is not None and price_value <= 10:
            badge = "SOTTOCOSTO"
        elif percent is not None and percent >= 45:
            badge = "TOP DEAL"
        else:
            badge = DEFAULT_BADGE

    badge_upper = badge.upper()

    if badge_upper == "ERRORE PREZZO":
        return badge_upper, "🚨", "PREZZO ASSURDO"
    if badge_upper == "SOTTOCOSTO":
        return badge_upper, "💥", "SOTTOCOSTO VERO"
    if badge_upper == "TOP DEAL":
        return badge_upper, "🔥", "OFFERTA TOP"
    return badge_upper, "⚡", badge_upper


def choose_category_emoji(category: str | None, title: str | None) -> str:
    haystack = f"{category or ''} {title or ''}".lower()
    mapping = [
        ("smart home", "🏠"),
        ("casa", "🏠"),
        ("tv", "📺"),
        ("monitor", "🖥"),
        ("pc", "💻"),
        ("notebook", "💻"),
        ("laptop", "💻"),
        ("tablet", "📱"),
        ("iphone", "📱"),
        ("smartphone", "📱"),
        ("telefono", "📱"),
        ("apple", "🍎"),
        ("cuffie", "🎧"),
        ("audio", "🎧"),
        ("gaming", "🎮"),
        ("console", "🎮"),
        ("videogioco", "🎮"),
        ("cucina", "🍳"),
        ("elettrodomestici", "🔌"),
        ("elettronica", "🔌"),
        ("bambini", "🧸"),
        ("giocattoli", "🧸"),
        ("sport", "🏃"),
        ("fitness", "🏃"),
        ("libri", "📚"),
        ("beauty", "💄"),
    ]
    for keyword, emoji in mapping:
        if keyword in haystack:
            return emoji
    return "📦"


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


def format_deal_message(
    title: str,
    discounted_price: str | None,
    original_price: str | None,
    link: str,
    badge: str | None = None,
    discount_label: str | None = None,
    category: str | None = None,
) -> str:
    final_badge, intro_emoji, intro_text = choose_badge_and_intro(
        badge,
        discounted_price,
        original_price,
        discount_label,
    )
    category_emoji = choose_category_emoji(category, title)

    safe_title = escape((title or "Nuova offerta Amazon").strip())
    safe_link = escape(link.strip())
    safe_discounted = escape((discounted_price or "Prezzo non specificato").strip())
    safe_original = escape(original_price.strip()) if original_price else None
    computed_discount = discount_label or (
        f"-{compute_discount_percent(discounted_price, original_price)}%"
        if compute_discount_percent(discounted_price, original_price) is not None
        else None
    )
    safe_discount_label = escape(computed_discount.strip()) if computed_discount else None
    safe_category = escape(category.strip()) if category else "Amazon"
    safe_brand = escape(BRAND_TAG)
    safe_disclosure = escape(DISCLOSURE)
    safe_badge = escape(final_badge)
    safe_intro_text = escape(intro_text)

    lines = [
        f"{intro_emoji} <b>{safe_intro_text}</b>",
        f"{category_emoji} <b>{safe_badge}</b>",
        "",
        f"<b>{safe_title}</b>",
        "",
        f"💸 <b>Prezzo:</b> {safe_discounted}",
    ]

    if safe_original:
        lines.append(f"🕵️ <b>Prima stava a:</b> <tg-spoiler>{safe_original}</tg-spoiler>")

    if safe_discount_label:
        lines.append(f"🏷 <b>Sconto:</b> {safe_discount_label}")

    lines.extend(
        [
            f"📦 <b>Categoria:</b> {safe_category}",
            "",
            f"👉 <a href=\"{safe_link}\">VAI ALL'OFFERTA</a>",
            "",
            f"{safe_brand} | #offerte #amazon #capofferte",
            safe_disclosure,
        ]
    )

    return "\n".join(lines)


def parse_submission_from_message(message: Message) -> dict:
    text = message.caption or message.text or ""
    url = extract_url(text)
    if not url:
        raise ValueError("Mandami un link Amazon valido nel testo o nella caption.")

    affiliate_url = normalize_amazon_url(url)
    text_without_url = text.replace(url, "", 1).strip(" -\n")
    fields = parse_structured_fields(text_without_url)

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

    title = fields.get("titolo") or scraped_title or "Nuova offerta Amazon"
    discounted_price = fields.get("prezzo")
    original_price = fields.get("prima") or fields.get("originale")
    discount_label = fields.get("sconto")
    badge = fields.get("badge")
    category = fields.get("categoria")

    caption = format_deal_message(
        title=title,
        discounted_price=discounted_price,
        original_price=original_price,
        link=affiliate_url,
        badge=badge,
        discount_label=discount_label,
        category=category,
    )

    return {
        "url": affiliate_url,
        "text": text_without_url,
        "caption": caption,
        "asin": extract_asin(affiliate_url),
        "photo_file_id": photo_file_id,
        "image_url": image_url,
        "scraped_title": scraped_title,
        "title": title,
        "discounted_price": discounted_price,
        "original_price": original_price,
        "discount_label": discount_label,
        "category": category,
        "badge": badge,
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
        "Mandami un link Amazon, oppure foto + caption con campi tipo:\n"
        "titolo: Echo Dot 5\n"
        "prezzo: 24,99€\n"
        "prima: 59,99€\n"
        "sconto: -58%\n"
        "categoria: Smart Home\n"
        "https://www.amazon.it/dp/ASIN\n\n"
        "Il badge e l'intro vengono scelti automaticamente se non li scrivi tu."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Formato supportato:\n\n"
        "titolo: Nome prodotto\n"
        "prezzo: 24,99€\n"
        "prima: 59,99€\n"
        "sconto: -58%\n"
        "badge: ERRORE PREZZO\n"
        "categoria: Elettronica\n"
        "https://www.amazon.it/dp/ASIN\n\n"
        "Badge automatici:\n"
        "- ERRORE PREZZO se sconto molto alto\n"
        "- SOTTOCOSTO se prezzo bassissimo\n"
        "- TOP DEAL negli altri casi forti"
    )


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    text = update.message.text or ""
    match = SHORT_TEXT_RE.match(text)
    if not match:
        await update.message.reply_text("Formato: /post LINK oppure /post testo strutturato con link")
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
    logger.info("Bot Amazon con template aggressivo dinamico avviato")
    application.run_polling(drop_pending_updates=True)
