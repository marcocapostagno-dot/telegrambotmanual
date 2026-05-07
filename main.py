from pathlib import Path
code = Path('output/bot.py').read_text()
old = '''def fetch_page_html(url: str) -> str:
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
'''
new = '''def fetch_page_html(url: str) -> str:
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


def extract_price_from_html(html: str) -> str | None:
    patterns = [
        r'id="corePrice_feature_div".*?a-offscreen">([^<]+)<',
        r'id="priceblock_ourprice"[^>]*>([^<]+)<',
        r'id="priceblock_dealprice"[^>]*>([^<]+)<',
        r'"priceToPay":"([^"]+)"',
        r'"displayPrice":"([^"]+)"',
        r'"price":"([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.S)
        if m:
            price = re.sub(r"\\s+", " ", m.group(1)).strip()
            price = price.replace("&nbsp;", " ")
            return price
    return None
'''
code = code.replace(old, new)
old2 = '''    if message.photo:
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
'''
new2 = '''    html = None
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
    if not discounted_price and html:
        discounted_price = extract_price_from_html(html)
    original_price = fields.get("prima") or fields.get("originale")
    discount_label = fields.get("sconto")
'''
code = code.replace(old2, new2)
Path('output/bot.py').write_text(code)
print('patched')
