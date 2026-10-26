"""
Sale roundups: when 2+ in-stock books are on sale (flash sales from the bot, or a
"compare at" price set in Shopify), post one alert featuring 2-3 of them.

  - Scheduled posts: at most one roundup a day (ROUNDUP_EVERY_HOURS), the other
    slot stays a normal book post. Featured books rotate through the sale pool.
  - Bot: /salepost posts a roundup right away.
  - Bot: /sale with several lines starts every sale and announces them in one roundup.
"""

import html
import json
import os
import random
import time

import requests
from PIL import Image, ImageDraw, ImageFilter

import post

ROUNDUP_EVERY_HOURS = float(os.getenv("ROUNDUP_EVERY_HOURS", "20"))
SALES_FILE = post.ROOT / "state" / "sales.json"
RED = (176, 32, 38)


def sale_pool(books):
    return [b for b in books if b.get("compare_at") and b.get("in_stock", True)]


def pct_off(b):
    return round((1 - b["price"] / b["compare_at"]) * 100)


def sale_ends():
    try:
        sales = json.loads(SALES_FILE.read_text())
    except Exception:
        return {}
    import sale
    return {h: sale.ends_label(s["ends_at"]) for h, s in sales.items()}


def roundup_due(state):
    return time.time() - state.get("last_roundup", 0) >= ROUNDUP_EVERY_HOURS * 3600


def pick(pool, state, k=3, first=None):
    """Up to k books: `first` ones, then sale books not featured recently."""
    picks = list(first or [])[:k]
    chosen = {b["handle"] for b in picks}
    featured = set(state.get("sale_featured", []))
    rest = [b for b in pool if b["handle"] not in chosen]
    fresh = [b for b in rest if b["handle"] not in featured]
    random.shuffle(fresh)
    stale = [b for b in rest if b["handle"] in featured]
    random.shuffle(stale)
    picks += (fresh + stale)[:k - len(picks)]
    return picks


# ---------------------------------------------------------------- image
def render_roundup(books, photos, total):
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), post.BG)
    draw = ImageDraw.Draw(canvas)
    f = post.font

    # banner (kept below Instagram's top bar)
    bf = f("Amiri-Bold.ttf", 56)
    label = f"SALE  ·  {total} BOOKS ON SALE" if total > 1 else "SALE ON NOW"
    draw.rectangle([0, 240, W, 330], fill=RED)
    bb = draw.textbbox((0, 0), label, font=bf)
    draw.text(((W - (bb[2] - bb[0])) / 2 - bb[0], 285 - (bb[3] + bb[1]) / 2), label, font=bf, fill=(255, 245, 230))

    top, bottom = 380, 1590
    row_h = (bottom - top) // len(books)
    thumb_h = min(row_h - 50, 440)
    thumb_w = int(thumb_h * 0.74)
    ends = sale_ends()

    for i, (b, photo) in enumerate(zip(books, photos)):
        y0 = top + i * row_h
        cy = y0 + row_h // 2
        # photo with shadow
        p = photo.copy()
        p.thumbnail((thumb_w, thumb_h), Image.LANCZOS)
        px, py = 70 + (thumb_w - p.width) // 2, cy - p.height // 2
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rectangle([px + 8, py + 14, px + p.width + 8, py + p.height + 14], fill=160)
        canvas.paste(Image.new("RGB", (W, H), (0, 0, 0)), mask=sh.filter(ImageFilter.GaussianBlur(16)))
        canvas.paste(p, (px, py))
        draw = ImageDraw.Draw(canvas)

        # text block
        x = 70 + thumb_w + 50
        maxw = W - x - 60
        main, sub, arabic = post.split_title(b["title"])
        title = main or arabic
        tf, lines = post.fit_lines(draw, title, "Amiri-Bold.ttf", maxw, 3 if row_h > 450 else 2, 46, 32)
        kw = {"direction": "rtl"} if not main and post.features.check("raqm") else {}
        block = []
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=tf, **kw)
            block.append((ln, bb))
        title_h = sum(bb[3] - bb[1] + 8 for _, bb in block)
        price_h = 120 + (40 if b["handle"] in ends else 0)
        ty = cy - (title_h + price_h) // 2
        for ln, bb in block:
            draw.text((x - bb[0], ty - bb[1]), ln, font=tf, fill=post.CREAM, **kw)
            ty += bb[3] - bb[1] + 8

        ty += 18
        of = f("Amiri-Regular.ttf", 38)
        old = post.fmt_price(b["compare_at"])
        ob = draw.textbbox((0, 0), old, font=of)
        draw.text((x - ob[0], ty - ob[1]), old, font=of, fill=post.MUTED)
        mid = ty + (ob[3] - ob[1]) / 2
        draw.line([(x - 4, mid), (x + ob[2] - ob[0] + 4, mid)], fill=(200, 70, 70), width=3)
        tag = f"-{pct_off(b)}%"
        tgf = f("Amiri-Bold.ttf", 34)
        tx = x + ob[2] - ob[0] + 24
        tb = draw.textbbox((0, 0), tag, font=tgf)
        draw.rounded_rectangle([tx, ty - 4, tx + tb[2] - tb[0] + 24, ty + 44], radius=10, fill=RED)
        draw.text((tx + 12 - tb[0], ty + 20 - (tb[3] + tb[1]) / 2), tag, font=tgf, fill=(255, 245, 230))

        ty += 56
        nf = f("Amiri-Bold.ttf", 60)
        new = post.fmt_price(b["price"])
        nb = draw.textbbox((0, 0), new, font=nf)
        draw.text((x - nb[0], ty - nb[1]), new, font=nf, fill=post.GOLD)
        if b["handle"] in ends:
            ef = f("Amiri-Regular.ttf", 30)
            draw.text((x, ty + nb[3] - nb[1] + 16), f"Ends {ends[b['handle']]}", font=ef, fill=post.MUTED)

        if i < len(books) - 1:
            draw.line([(70, y0 + row_h), (W - 70, y0 + row_h)], fill=(60, 75, 68), width=2)

    ff = f("Amiri-Regular.ttf", 38)
    more = total - len(books)
    if more > 0:
        mf = f("Amiri-Bold.ttf", 42)
        m = f"+ {more} more on sale"
        draw.text(((W - post.text_w(draw, m, mf)) / 2, 1625), m, font=mf, fill=post.GOLD)
    footer = f"Order at {post.SITE_LABEL}"
    draw.text(((W - post.text_w(draw, footer, ff)) / 2, 1690), footer, font=ff, fill=post.CREAM)
    return canvas


# ---------------------------------------------------------------- Telegram
def caption_for(books, total, intro=None):
    ends = sale_ends()
    head = intro or (f"🔥 <b>SALE ON NOW – {total} books discounted</b>" if total > 1 else "🔥 <b>SALE ON NOW</b>")
    lines = [head, ""]
    for b in books:
        lines.append(f"📚 <a href=\"{b['url']}\"><b>{post.esc(b['title'])}</b></a>")
        line = f"      <s>{post.fmt_price(b['compare_at'])}</s> → <b>{post.fmt_price(b['price'])}</b>  (-{pct_off(b)}%)"
        if b["handle"] in ends:
            line += f"\n      ⏰ ends {ends[b['handle']]}"
        lines.append(line)
    more = total - len(books)
    if more > 0:
        lines.append(f"\n➕ {more} more book{'s' if more > 1 else ''} on sale at {post.SITE_LABEL}")
    lines.append("\n🛒 Tap a title to order")
    caption = "\n".join(lines)
    if len(caption) > 1024 and len(books) > 1:  # Telegram caption limit: list fewer titles
        return caption_for(books[:-1], total, intro)
    return caption[:1024]


def telegram_album(books, photos, caption):
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"]
    media, files = [], {}
    for i, photo in enumerate(photos):
        buf = post.io.BytesIO()
        photo.save(buf, "JPEG", quality=92)
        files[f"p{i}"] = (f"p{i}.jpg", buf.getvalue(), "image/jpeg")
        item = {"type": "photo", "media": f"attach://p{i}"}
        if i == 0:
            item.update(caption=caption, parse_mode="HTML")
        media.append(item)
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMediaGroup",
                      data={"chat_id": chat, "media": json.dumps(media)}, files=files, timeout=90)
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.text}")
    return r.json()["result"][0]["message_id"]


# ---------------------------------------------------------------- publish
def publish_roundup(pool, first=None, intro=None, dry=False):
    """Post a roundup of up to 3 sale books. Returns (featured books, errors)."""
    skip_ig = os.getenv("SKIP_IG") == "1" or not os.getenv("IG_ACCESS_TOKEN")
    skip_tg = os.getenv("SKIP_TELEGRAM") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN")
    state = post.load_state()
    books = pick(pool, state, 3, first)
    total = len({b["handle"] for b in pool} | {b["handle"] for b in books})
    print(f"Sale roundup ({total} on sale): " + " | ".join(b["title"] for b in books))
    photos = [post.download_image(b["image"]) for b in books]

    post.STORY_DIR.mkdir(exist_ok=True)
    story_path = post.STORY_DIR / f"sale-roundup-{int(time.time())}.jpg"
    render_roundup(books, photos, total).save(story_path, "JPEG", quality=92)
    print(f"Story image: {story_path.name}")
    if dry:
        return books, []

    errors = []
    if not skip_ig:
        try:
            repo, branch = os.environ["GITHUB_REPOSITORY"], os.getenv("GITHUB_REF_NAME", "main")
            removed = post.prune_old_stories()
            post.push_files([post.STORY_DIR, *removed], "story: sale roundup")
            time.sleep(10)
            print("Instagram story id:", post.ig_post_story(
                f"https://raw.githubusercontent.com/{repo}/{branch}/stories/{story_path.name}"))
        except Exception as e:
            errors.append(f"Instagram: {e}")
    if not skip_tg:
        try:
            cap = caption_for(books, total, intro)
            if len(books) == 1:
                post.telegram_post(books[0], photos[0], cap)
            else:
                telegram_album(books, photos, cap)
        except Exception as e:
            errors.append(f"Telegram: {e}")

    if len(errors) < (2 - skip_ig - skip_tg):
        handles = [b["handle"] for b in books]

        def mutate(s):
            pool_handles = {b["handle"] for b in pool}
            feat = [h for h in s.get("sale_featured", []) if h in pool_handles] + handles
            if pool_handles and pool_handles <= set(feat):  # everyone featured -> new cycle
                feat = handles
            s["sale_featured"] = feat
            s["last_roundup"] = time.time()
            s.setdefault("posted", [])
            s["posted"] += [h for h in handles if h not in s["posted"]]
        post.commit_json(post.STATE_FILE, mutate, "posted: sale roundup", {"posted": [], "history": []})
    return books, errors
