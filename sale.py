"""
Flash sales, controlled from the Telegram bot.

  /sale kitab tawheed 20%          20% off for 24 hours (default)
  /sale kitab tawheed $12 6h       sale price $12 for 6 hours
  /sale ad-da wad-dawa 15% 3d      15% off for 3 days
  /endsale kitab tawheed           end a sale early
  /sales                           list running sales

Starting a sale lowers the price on the store (old price shown crossed out),
announces it on Telegram (and the Instagram story once that's connected),
and saves the original prices in state/sales.json.
`python sale.py expire` (run every 10 min by GitHub) puts prices back when sales end.

Env: SHOPIFY_STORE (xxx.myshopify.com), SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET
"""

import html
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import post

SALES_FILE = post.ROOT / "state" / "sales.json"
STORE = os.getenv("SHOPIFY_STORE", "z11fv0-ap.myshopify.com")
API_VERSION = "2026-10"
TZ = ZoneInfo("America/New_York")
DEFAULT_HOURS = 24

_token = None


# ---------------------------------------------------------------- Shopify
def shopify_token():
    """Fresh Admin API token from the Dev Dashboard app (client credentials, valid 24h)."""
    global _token
    if _token:
        return _token
    cid, secret = os.getenv("SHOPIFY_CLIENT_ID"), os.getenv("SHOPIFY_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("Shopify isn't connected yet (add SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET in GitHub secrets).")
    r = requests.post(f"https://{STORE}/admin/oauth/access_token", json={
        "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Shopify login failed ({r.status_code}): {r.text[:300]}")
    _token = r.json()["access_token"]
    return _token


def gql(query, variables=None):
    r = requests.post(f"https://{STORE}/admin/api/{API_VERSION}/graphql.json",
                      json={"query": query, "variables": variables or {}},
                      headers={"X-Shopify-Access-Token": shopify_token()}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"Shopify error: {data['errors']}")
    return data["data"]


def get_product(handle):
    d = gql("""query($h: String!) { productByIdentifier(identifier: {handle: $h}) {
                id title variants(first: 100) { nodes { id price compareAtPrice } } } }""", {"h": handle})
    p = d["productByIdentifier"]
    if not p:
        raise RuntimeError(f"Product '{handle}' not found in Shopify")
    return p


def set_prices(product_id, variants):
    d = gql("""mutation($pid: ID!, $v: [ProductVariantsBulkInput!]!) {
                 productVariantsBulkUpdate(productId: $pid, variants: $v) {
                   productVariants { id price compareAtPrice } userErrors { field message } } }""",
            {"pid": product_id, "v": variants})
    errs = d["productVariantsBulkUpdate"]["userErrors"]
    if errs:
        raise RuntimeError(f"Shopify refused the price change: {errs}")


# ---------------------------------------------------------------- parsing
DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$", re.I)
AMT_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*%|%\s*(\d+(?:\.\d+)?)|\$?\s*(\d+(?:\.\d+)?)\s*\$?)$")


def parse_sale(text):
    """'kitab tawheed 20% 6h' -> ('kitab tawheed', ('pct', 20.0), 6.0 hours)"""
    text = re.sub(r"(\d)\s+%", r"\1%", text)
    text = re.sub(r"\$\s+(\d)", r"$\1", text)
    tokens = text.split()
    hours = DEFAULT_HOURS
    # duration may be written "6h" or "6 hours"
    if len(tokens) >= 2 and DUR_RE.match(" ".join(tokens[-2:])) and not tokens[-1][0].isdigit():
        m, tokens = DUR_RE.match(" ".join(tokens[-2:])), tokens[:-2]
    elif tokens and DUR_RE.match(tokens[-1]):
        m, tokens = DUR_RE.match(tokens[-1]), tokens[:-1]
    else:
        m = None
    if m:
        n, unit = float(m.group(1)), m.group(2).lower()
        hours = n / 60 if unit.startswith("m") else n * 24 if unit.startswith("d") else n
    if not tokens:
        raise ValueError("missing book name and amount")
    a = AMT_RE.match(tokens[-1])
    if not a or len(tokens) < 2:
        raise ValueError("missing the sale amount (like 20% or $12)")
    if a.group(1) or a.group(2):
        amount = ("pct", float(a.group(1) or a.group(2)))
    else:
        amount = ("price", float(a.group(3)))
    return " ".join(tokens[:-1]), amount, hours


def ends_label(ts):
    dt = datetime.fromtimestamp(ts, TZ)
    now = datetime.now(TZ)
    day = "today" if dt.date() == now.date() else "tomorrow" if dt.date() == (now + timedelta(days=1)).date() \
        else dt.strftime("%a %b %-d")
    return f"{day} at {dt.strftime('%-I:%M %p').replace(':00 ', ' ')} ET"


def load_sales():
    import json
    return json.loads(SALES_FILE.read_text()) if SALES_FILE.exists() else {}


# ---------------------------------------------------------------- actions
def start_sale(text, catalog, reply, announce=True):
    try:
        query, (kind, value), hours = parse_sale(text)
    except ValueError as e:
        reply(f"Couldn't read that ({e}).\nExample: <code>/sale kitab tawheed 20% 24h</code>")
        return
    if hours <= 0 or hours > 24 * 30:
        reply("Sale length must be between a few minutes and 30 days.")
        return

    scored = post.find_book([b for b in catalog if b["in_stock"]], query)
    if not scored or scored[0][0] < 0.75:
        reply(f"Couldn't find an in-stock book matching “{html.escape(query)}”.")
        return
    top = scored[0][0]
    tied = [b for sc, b in scored[:5] if sc >= 0.75 and top - sc < 0.05]
    if len(tied) > 1:
        names = "\n".join(f"• {html.escape(b['title'])}" for b in tied)
        reply(f"More than one book matches. Use more of the title:\n{names}")
        return
    book = dict(scored[0][1])
    handle = book["handle"]

    if handle in load_sales():
        reply(f"<b>{html.escape(book['title'])}</b> is already on sale. Send <code>/endsale {html.escape(query)}</code> first.")
        return

    product = get_product(handle)
    originals = [{"id": v["id"], "price": v["price"], "compareAtPrice": v["compareAtPrice"]}
                 for v in product["variants"]["nodes"]]
    new_variants = []
    for v in originals:
        base = float(v["price"])
        new = round(base * (1 - value / 100), 2) if kind == "pct" else value
        if not (0 < new < base):
            reply(f"The sale price must be lower than the current price ({post.fmt_price(base)}).")
            return
        new_variants.append({"id": v["id"], "price": f"{new:.2f}", "compareAtPrice": f"{base:.2f}"})

    set_prices(product["id"], new_variants)
    ends = time.time() + hours * 3600
    old_price = book["price"]
    sale_price = min(float(v["price"]) for v in new_variants)
    pct_off = round((1 - sale_price / old_price) * 100)

    post.commit_json(SALES_FILE, lambda s: s.update({handle: {
        "title": book["title"], "product_id": product["id"], "original_variants": originals,
        "sale_price": sale_price, "ends_at": ends,
        "started": datetime.now(timezone.utc).isoformat(timespec="minutes")}}),
        f"sale start: {handle}", {})

    label = ends_label(ends)
    if not announce:  # part of a multi-book sale: one roundup is posted afterwards
        book.update(price=sale_price, compare_at=old_price)
        reply(f"✅ <b>{html.escape(book['title'])}</b>: {post.fmt_price(old_price)} → "
              f"<b>{post.fmt_price(sale_price)}</b> ({pct_off}% off), ends {label}")
        return book

    # announce
    book.update(price=sale_price, compare_at=old_price, sale_label=f"FLASH SALE  ·  {pct_off}% OFF",
                sale_ends=label)
    caption = (f"⚡️ <b>FLASH SALE – {pct_off}% OFF</b> ⚡️\n\n"
               f"📚 <b>{html.escape(book['title'])}</b>\n"
               f"💵 <s>{post.fmt_price(old_price)}</s>  →  <b>{post.fmt_price(sale_price)}</b>\n"
               f"⏰ Ends {label}\n\n"
               f"🛒 <a href=\"{book['url']}\">Order now on {post.SITE_LABEL}</a>")
    errors = post.publish(book, caption=caption, record=False)
    msg = (f"✅ Sale started: <b>{html.escape(book['title'])}</b>\n"
           f"{post.fmt_price(old_price)} → <b>{post.fmt_price(sale_price)}</b> ({pct_off}% off)\n"
           f"Ends {label}. Prices go back automatically.")
    if errors:
        msg += "\n\n⚠️ Announcement problem:\n<code>" + html.escape("\n".join(errors))[:1500] + "</code>"
    reply(msg)
    return book


def end_sale(handle, sale):
    """Restore original prices. Returns the title."""
    set_prices(sale["product_id"], [{"id": v["id"], "price": v["price"], "compareAtPrice": v["compareAtPrice"]}
                                    for v in sale["original_variants"]])
    post.commit_json(SALES_FILE, lambda s: s.pop(handle, None), f"sale end: {handle}", {})
    return sale["title"]


def end_sale_cmd(text, reply):
    sales = load_sales()
    if not sales:
        reply("There are no sales running.")
        return
    items = [{"handle": h, "title": s["title"], "vendor": "", "in_stock": True} for h, s in sales.items()]
    scored = post.find_book(items, text) if text else []
    if not text and len(sales) == 1:
        handle = next(iter(sales))
    elif scored and scored[0][0] >= 0.75:
        handle = scored[0][1]["handle"]
    else:
        reply("Which sale? Running now:\n" + "\n".join(f"• {html.escape(s['title'])}" for s in sales.values()))
        return
    sale = sales[handle]
    title = end_sale(handle, sale)
    price = sale["original_variants"][0]["price"]
    reply(f"✅ Sale ended: <b>{html.escape(title)}</b> is back to {post.fmt_price(float(price))}.")


def list_sales(reply):
    sales = load_sales()
    if not sales:
        reply("There are no sales running.")
        return
    lines = [f"• <b>{html.escape(s['title'])}</b>: {post.fmt_price(s['sale_price'])}, ends {ends_label(s['ends_at'])}"
             for s in sales.values()]
    reply("⚡️ Running sales:\n" + "\n".join(lines))


def expire(notify):
    """Called every 10 minutes: end any sale whose time is up."""
    now = time.time()
    for handle, sale in load_sales().items():
        if sale["ends_at"] <= now:
            try:
                title = end_sale(handle, sale)
                notify(f"⏰ Flash sale over: <b>{html.escape(title)}</b> is back to "
                       f"{post.fmt_price(float(sale['original_variants'][0]['price']))}.")
            except Exception as e:
                notify(f"⚠️ Couldn't end the sale on <b>{html.escape(sale['title'])}</b>: "
                       f"<code>{html.escape(str(e))[:500]}</code>\nPlease fix the price in Shopify.")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "expire":
    owner = os.getenv("TELEGRAM_OWNER_ID")

    def notify(msg):
        print(msg)
        if owner and os.getenv("TELEGRAM_BOT_TOKEN"):
            requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                          data={"chat_id": owner, "text": msg, "parse_mode": "HTML"}, timeout=30)

    expire(notify)
