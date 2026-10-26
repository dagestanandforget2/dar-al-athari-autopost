"""
Discount-code announcements, controlled from the Telegram bot.

  /code RAMADAN15 15%                       new code: 15% off everything, 48 hours (default)
  /code TAWHEED5 $5 kitab at-tauhid 3d      new code: $5 off one book, 3 days
  /code Sharh                               announce a code that already exists
  /codes                                    active codes with uses and sales
  /endcode RAMADAN15                        switch a code off now

New codes are created in Shopify with an end date, so they expire on their own.
Posts include a link that applies the code automatically (daralathari.com/discount/CODE).
"""

import html
import io
import os
import re
import time
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFilter

import post
import sale

DEFAULT_HOURS = 48
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
RED = (176, 32, 38)
STORE_WIDE = {"", "all", "everything", "store", "storewide", "store-wide", "sitewide"}


def money(v):
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


# ---------------------------------------------------------------- Shopify
def lookup(code):
    d = sale.gql("""query($c: String!) { codeDiscountNodeByCode(code: $c) { id
        codeDiscount { __typename
          ... on DiscountCodeBasic { title summary status endsAt asyncUsageCount totalSales { amount }
                customerGets { value { __typename ... on DiscountPercentage { percentage }
                                       ... on DiscountAmount { amount { amount } } }
                               items { __typename ... on DiscountProducts { products(first: 1) { nodes { handle } } } } } }
          ... on DiscountCodeFreeShipping { title summary status endsAt asyncUsageCount totalSales { amount } }
          ... on DiscountCodeBxgy { title summary status endsAt asyncUsageCount totalSales { amount } } } } }""",
                 {"c": code})
    return d["codeDiscountNodeByCode"]


def create(code, kind, value, product_id, hours):
    gets = {"value": {"percentage": value / 100} if kind == "pct"
            else {"discountAmount": {"amount": f"{value:.2f}", "appliesOnEachItem": False}},
            "items": {"products": {"productsToAdd": [product_id]}} if product_id else {"all": True}}
    now = datetime.now(timezone.utc)
    ends = datetime.fromtimestamp(time.time() + hours * 3600, timezone.utc)
    d = sale.gql("""mutation($d: DiscountCodeBasicInput!) { discountCodeBasicCreate(basicCodeDiscount: $d) {
                      codeDiscountNode { id } userErrors { field message } } }""",
                 {"d": {"title": code, "code": code, "startsAt": now.isoformat(), "endsAt": ends.isoformat(),
                        "context": {"all": "ALL"}, "customerGets": gets, "appliesOncePerCustomer": False,
                        "combinesWith": {"productDiscounts": False, "orderDiscounts": False, "shippingDiscounts": True},
                        "tags": ["telegram-bot"]}})
    errs = d["discountCodeBasicCreate"]["userErrors"]
    if errs:
        raise RuntimeError("Shopify refused the code: " + "; ".join(e["message"] for e in errs))
    return ends.timestamp()


def deactivate(node_id):
    d = sale.gql("""mutation($id: ID!) { discountCodeDeactivate(id: $id) { userErrors { field message } } }""",
                 {"id": node_id})
    errs = d["discountCodeDeactivate"]["userErrors"]
    if errs:
        raise RuntimeError("; ".join(e["message"] for e in errs))


# ---------------------------------------------------------------- parsing
def parse(rest):
    rest = re.sub(r"(\d)\s+%", r"\1%", rest)
    rest = re.sub(r"\$\s+(\d)", r"$\1", rest)
    tokens = rest.split()
    if not tokens or not CODE_RE.match(tokens[0]):
        raise ValueError("start with the code, e.g. <code>/code RAMADAN15 15%</code>")
    code, tokens = tokens[0], tokens[1:]
    hours = None
    if len(tokens) >= 2 and sale.DUR_RE.match(" ".join(tokens[-2:])) and not tokens[-1][0].isdigit():
        m, tokens = sale.DUR_RE.match(" ".join(tokens[-2:])), tokens[:-2]
    elif tokens and sale.DUR_RE.match(tokens[-1]):
        m, tokens = sale.DUR_RE.match(tokens[-1]), tokens[:-1]
    else:
        m = None
    if m:
        n, unit = float(m.group(1)), m.group(2).lower()
        hours = n / 60 if unit.startswith("m") else n * 24 if unit.startswith("d") else n
    amount = None
    if tokens:
        a = sale.AMT_RE.match(tokens[0])
        if a and ("%" in tokens[0] or "$" in tokens[0]):
            amount = ("pct", float(a.group(1) or a.group(2))) if (a.group(1) or a.group(2)) \
                else ("amt", float(a.group(3)))
            tokens = tokens[1:]
        else:
            raise ValueError("put the amount right after the code, like 15% or $5")
    return code, amount, " ".join(tokens), hours


# ---------------------------------------------------------------- image
def render_card(headline, target, code, ends, photo=None):
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), post.BG)
    if photo is not None:  # soft blurred book photo behind
        blur = photo.copy()
        sc = max(W / blur.width, H / blur.height)
        blur = blur.resize((int(blur.width * sc) + 1, int(blur.height * sc) + 1))
        blur = blur.crop(((blur.width - W) // 2, (blur.height - H) // 2,
                          (blur.width - W) // 2 + W, (blur.height - H) // 2 + H)).filter(ImageFilter.GaussianBlur(40))
        canvas = Image.blend(canvas, blur, 0.2)
    draw = ImageDraw.Draw(canvas)
    f = post.font

    def center(text, y, fnt, fill):
        bb = draw.textbbox((0, 0), text, font=fnt)
        draw.text(((W - (bb[2] - bb[0])) / 2 - bb[0], y - bb[1]), text, font=fnt, fill=fill)
        return y + bb[3] - bb[1]

    bf = f("Amiri-Bold.ttf", 56)
    draw.rectangle([0, 240, W, 330], fill=RED)
    bb = draw.textbbox((0, 0), "DISCOUNT CODE", font=bf)
    draw.text(((W - (bb[2] - bb[0])) / 2 - bb[0], 285 - (bb[3] + bb[1]) / 2), "DISCOUNT CODE", font=bf,
              fill=(255, 245, 230))

    y = 400
    if photo is not None:
        p = photo.copy()
        p.thumbnail((520, 600), Image.LANCZOS)
        px = (W - p.width) // 2
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rectangle([px + 10, y + 20, px + p.width + 10, y + p.height + 20], fill=170)
        canvas.paste(Image.new("RGB", (W, H), (0, 0, 0)), mask=sh.filter(ImageFilter.GaussianBlur(24)))
        canvas.paste(p, (px, y))
        draw = ImageDraw.Draw(canvas)
        y += p.height + 50
        big = 130
    else:
        y = 700
        big = 210

    y = center(headline, y, f("Amiri-Bold.ttf", big), post.GOLD) + 30
    tf, lines = post.fit_lines(draw, target, "Amiri-Bold.ttf", 920, 2, 54, 36)
    for ln in lines:
        y = center(ln, y, tf, post.CREAM) + 10
    y += 60 if photo is not None else 120

    # code in a dashed box
    cf = f("Amiri-Bold.ttf", 96)
    cb = draw.textbbox((0, 0), code, font=cf)
    cw, ch = cb[2] - cb[0], cb[3] - cb[1]
    bx0, bx1 = (W - cw) / 2 - 60, (W + cw) / 2 + 60
    by0, by1 = y, y + ch + 80
    for x in range(int(bx0), int(bx1), 36):
        draw.line([(x, by0), (min(x + 20, bx1), by0)], fill=post.GOLD, width=5)
        draw.line([(x, by1), (min(x + 20, bx1), by1)], fill=post.GOLD, width=5)
    for yy in range(int(by0), int(by1), 36):
        draw.line([(bx0, yy), (bx0, min(yy + 20, by1))], fill=post.GOLD, width=5)
        draw.line([(bx1, yy), (bx1, min(yy + 20, by1))], fill=post.GOLD, width=5)
    draw.text(((W - cw) / 2 - cb[0], by0 + 40 - cb[1]), code, font=cf, fill=(255, 255, 255))
    y = by1 + 30
    center("Enter at checkout", y, f("Amiri-Regular.ttf", 40), post.MUTED)

    if ends:
        center(f"Ends {ends}", 1632, f("Amiri-Bold.ttf", 40), post.GOLD)
    center(f"Order at {post.SITE_LABEL}", 1690, f("Amiri-Regular.ttf", 38), post.CREAM)
    return canvas


# ---------------------------------------------------------------- announce
def describe(node):
    """(headline like '15% OFF', product handle or None) from an existing Shopify code."""
    d = node["codeDiscount"]
    headline, handle = None, None
    gets = d.get("customerGets") or {}
    v = (gets.get("value") or {})
    if v.get("__typename") == "DiscountPercentage":
        headline = f"{round(v['percentage'] * 100)}% OFF"
    elif v.get("__typename") == "DiscountAmount":
        headline = f"{money(float(v['amount']['amount']))} OFF"
    items = gets.get("items") or {}
    if items.get("__typename") == "DiscountProducts" and items["products"]["nodes"]:
        handle = items["products"]["nodes"][0]["handle"]
    if d["__typename"] == "DiscountCodeFreeShipping":
        headline = "FREE SHIPPING"
    return headline or d["summary"].split("•")[0].strip().upper(), handle


def announce(code, headline, summary, book, ends_ts):
    ends = sale.ends_label(ends_ts) if ends_ts else None
    photo = post.download_image(book["image"]) if book else None
    target = book["title"] if book else "EVERYTHING IN THE STORE"
    if book:
        main, _, arabic = post.split_title(book["title"])
        target = main or arabic
    card = render_card(headline, target, code.upper(), ends, photo)

    redirect = f"/products/{book['handle']}" if book else "/collections/all-books"
    link = f"{post.SHOP_URL}/discount/{code}?redirect={redirect}"
    on = f"<a href=\"{post.SHOP_URL}/products/{book['handle']}\"><b>{html.escape(book['title'])}</b></a>" if book \
        else "<b>everything in the store</b>"
    extras = [x.strip() for x in (summary or "").split("•")[1:] if x.strip()]  # e.g. "One use per customer"
    caption = (f"🎟️ <b>DISCOUNT CODE</b>\n\n"
               f"Use code <code>{html.escape(code)}</code> for <b>{html.escape(headline.lower())}</b> "
               + ("" if headline == "FREE SHIPPING" else ("on " if book else "")) + on
               + "".join(f"\n<i>{html.escape(x)}</i>" for x in extras)
               + (f"\n⏰ Ends {ends}" if ends else "")
               + f"\n\n👉 Tap the button and the code is applied automatically"
               + f"\n(or tap the code above to copy it)")

    errors = []
    skip_ig = os.getenv("SKIP_IG") == "1" or not os.getenv("IG_ACCESS_TOKEN")
    post.STORY_DIR.mkdir(exist_ok=True)
    path = post.STORY_DIR / f"code-{code.lower()}-{int(time.time())}.jpg"
    card.save(path, "JPEG", quality=92)
    if not skip_ig:
        try:
            repo, branch = os.environ["GITHUB_REPOSITORY"], os.getenv("GITHUB_REF_NAME", "main")
            removed = post.prune_old_stories()
            post.push_files([post.STORY_DIR, *removed], f"story: code {code}")
            time.sleep(10)
            post.ig_post_story(f"https://raw.githubusercontent.com/{repo}/{branch}/stories/{path.name}")
        except Exception as e:
            errors.append(f"Instagram: {e}")
    try:
        import json
        import requests
        buf = io.BytesIO()
        card.save(buf, "JPEG", quality=90)
        kb = {"inline_keyboard": [[{"text": f"🛒 Shop with {code}", "url": link}]]}
        r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendPhoto",
                          data={"chat_id": os.environ["TELEGRAM_CHANNEL"], "caption": caption[:1024],
                                "parse_mode": "HTML", "reply_markup": json.dumps(kb)},
                          files={"photo": ("code.jpg", buf.getvalue(), "image/jpeg")}, timeout=60)
        if not r.ok:
            raise RuntimeError(r.text)
    except Exception as e:
        errors.append(f"Telegram: {e}")
    return errors, ends


# ---------------------------------------------------------------- commands
def code_cmd(rest, catalog, reply):
    try:
        code, amount, target, hours = parse(rest)
    except ValueError as e:
        reply(f"Couldn't read that: {e}.\n\nExamples:\n<code>/code RAMADAN15 15%</code>\n"
              f"<code>/code TAWHEED5 $5 kitab at-tauhid 3d</code>\n<code>/code Sharh</code> (announce an existing code)")
        return

    node = lookup(code)
    book = None

    if amount is None:  # announce an existing code
        if not node:
            reply(f"There's no code <b>{html.escape(code)}</b> in Shopify. To create it, add an amount: "
                  f"<code>/code {html.escape(code)} 15%</code>")
            return
        d = node["codeDiscount"]
        if d.get("status") != "ACTIVE":
            reply(f"<b>{html.escape(code)}</b> exists but isn't active ({d.get('status', '').lower()}), "
                  f"so I didn't announce it.")
            return
        headline, handle = describe(node)
        if handle:
            book = next((b for b in catalog if b["handle"] == handle), None)
        ends_ts = datetime.fromisoformat(d["endsAt"].replace("Z", "+00:00")).timestamp() if d.get("endsAt") else None
        errors, ends = announce(code, headline, d["summary"], book, ends_ts)
        reply(f"📣 Announced <code>{html.escape(code)}</code> ({html.escape(d['summary'])})"
              + (f", ends {ends}" if ends else ", no end date")
              + ("\n⚠️ " + html.escape("; ".join(errors)) if errors else ""))
        return

    if node:
        reply(f"<b>{html.escape(code)}</b> already exists ({html.escape(node['codeDiscount']['summary'])}). "
              f"Send <code>/code {html.escape(code)}</code> to announce it, or pick a new code name.")
        return

    kind, value = amount
    if kind == "pct" and not 0 < value < 100:
        reply("The percentage must be between 1 and 99.")
        return
    product_id = None
    if target.lower() not in STORE_WIDE:
        scored = post.find_book([b for b in catalog if b["in_stock"]], target)
        if not scored or scored[0][0] < 0.75:
            reply(f"Couldn't find an in-stock book matching “{html.escape(target)}”.")
            return
        top = scored[0][0]
        tied = [b for sc, b in scored[:5] if sc >= 0.75 and top - sc < 0.05]
        if len(tied) > 1:
            reply("More than one book matches. Use more of the title:\n"
                  + "\n".join(f"• {html.escape(b['title'])}" for b in tied))
            return
        book = scored[0][1]
        product_id = sale.get_product(book["handle"])["id"]
        if kind == "amt" and value >= book["price"]:
            reply(f"${value:.2f} off is more than the book costs ({post.fmt_price(book['price'])}).")
            return

    hours = hours or DEFAULT_HOURS
    ends_ts = create(code, kind, value, product_id, hours)
    headline = f"{value:g}% OFF" if kind == "pct" else f"{money(value)} OFF"
    summary = None if book else ("entire order" if kind == "pct" else "off your order")
    errors, ends = announce(code, headline, summary, book, ends_ts)
    reply(f"✅ Created and announced <code>{html.escape(code)}</code>: {headline.lower()} on "
          + (html.escape(book["title"]) if book else "everything")
          + f", ends {ends}. It switches off by itself."
          + ("\n⚠️ " + html.escape("; ".join(errors)) if errors else ""))


def codes_cmd(reply):
    d = sale.gql("""{ discountNodes(first: 50, query: "status:active", sortKey: CREATED_AT, reverse: true) { nodes {
        discount { __typename
          ... on DiscountCodeBasic { summary endsAt asyncUsageCount totalSales { amount } codes(first: 1) { nodes { code } } }
          ... on DiscountCodeFreeShipping { summary endsAt asyncUsageCount totalSales { amount } codes(first: 1) { nodes { code } } }
          ... on DiscountCodeBxgy { summary endsAt asyncUsageCount totalSales { amount } codes(first: 1) { nodes { code } } } } } } }""")
    rows = []
    for n in d["discountNodes"]["nodes"]:
        x = n["discount"]
        if "codes" not in x or not x["codes"]["nodes"]:
            continue  # automatic discounts
        sales_amt = float((x.get("totalSales") or {}).get("amount") or 0)
        ends = ""
        if x.get("endsAt"):
            ends = ", ends " + sale.ends_label(datetime.fromisoformat(x["endsAt"].replace("Z", "+00:00")).timestamp())
        rows.append((x["asyncUsageCount"], sales_amt,
                     f"• <code>{html.escape(x['codes']['nodes'][0]['code'])}</code>: {html.escape(x['summary'].split('•')[0].strip())}"
                     f" | {x['asyncUsageCount']} use{'s' if x['asyncUsageCount'] != 1 else ''}, "
                     f"{post.fmt_price(sales_amt)} sales{ends}"))
    if not rows:
        reply("No active discount codes.")
        return
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    reply("🎟️ <b>Active codes</b> (most used first)\n\n" + "\n".join(r[2] for r in rows[:30]))


def endcode_cmd(rest, reply):
    code = rest.split()[0] if rest.split() else ""
    if not code:
        reply("Which code? e.g. <code>/endcode RAMADAN15</code>")
        return
    node = lookup(code)
    if not node:
        reply(f"No code <b>{html.escape(code)}</b> found.")
        return
    deactivate(node["id"])
    reply(f"✅ <code>{html.escape(code)}</code> is switched off. (It's kept in Shopify, so you can turn it back on there.)")
