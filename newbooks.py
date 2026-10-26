"""
"New books in stock" announcement, from the Telegram bot.

  /newbooks          books added to the store in the last 24 hours
  /newbooks 3d       look back further (any length: 12h, 3d, ...)

Posts ONE message: a short blurb, an album of the new books (up to 10 photos, Telegram's max),
every title with its price and link, and an Instagram story with up to 4 covers.
Books already announced are skipped, so sending it twice won't repeat anything.
"""

import html
import json
import os
import time
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFilter

import post
import roundup
import sale

MAX_PHOTOS = 10
STORY_COVERS = 4
NEW_URL = f"{post.SHOP_URL}/collections/all-books?sort_by=created-descending"


def parse_window(rest):
    m = sale.DUR_RE.match(rest.strip()) if rest.strip() else None
    if not m:
        return 24.0
    n, unit = float(m.group(1)), m.group(2).lower()
    return n / 60 if unit.startswith("m") else n * 24 if unit.startswith("d") else n


def find_new(books, hours, state):
    cutoff = time.time() - hours * 3600
    announced = set(state.get("announced_new", []))
    out = []
    for b in books:
        ts = b.get("published_at")
        if not ts or b["handle"] in announced:
            continue
        if datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() < cutoff:
            continue
        if b["handle"] in roundup.excluded_handles() or any(w in b["title"].lower() for w in roundup.NOT_A_SALE):
            continue  # damaged / clearance copies aren't "new books"
        out.append(b)
    out.sort(key=lambda b: b["published_at"])
    return out


def short_title(title):
    """English part only for the list ("Zad al-Mustaqni' fi Ikhtisar al-Muqni' · al-Hajjawi"); Arabic if that's all."""
    main, _, arabic = post.split_title(title)
    return main or arabic or title


def caption_for(books, shown=None):
    n = len(books)
    shown = books if shown is None else shown
    head = (f"🆕 <b>New books just arrived!</b>\n\nWe've just stocked <b>{n} new title{'s' if n > 1 else ''}</b> "
            f"at Dar Al Athari, alhamdulillah:\n")
    lines = [f"📚 <a href=\"{b['url']}\">{post.esc(short_title(b['title']))}</a> – {post.fmt_price(b['price'])}"
             for b in shown]
    more = n - len(shown)
    tail = (f"\n➕ {more} more – " if more else "\n") + f"<a href=\"{NEW_URL}\">see all new arrivals</a>"
    cap = head + "\n".join(lines) + "\n" + tail
    if len(cap) > 1024 and len(shown) > 1:  # Telegram caption limit: list fewer, link the rest
        return caption_for(books, shown[:-1])
    return cap[:1024]


def render_story(books, photos, total):
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), post.BG)
    draw = ImageDraw.Draw(canvas)
    f = post.font

    bf = f("Amiri-Bold.ttf", 58)
    label = "NEW ARRIVALS"
    draw.rectangle([0, 240, W, 330], fill=(35, 95, 70))
    bb = draw.textbbox((0, 0), label, font=bf)
    draw.text(((W - (bb[2] - bb[0])) / 2 - bb[0], 285 - (bb[3] + bb[1]) / 2), label, font=bf, fill=(255, 245, 230))
    sub = f"{total} new book{'s' if total > 1 else ''} just stocked"
    sf = f("Amiri-Regular.ttf", 44)
    draw.text(((W - post.text_w(draw, sub, sf)) / 2, 360), sub, font=sf, fill=post.GOLD)

    n = len(books)
    cols = 1 if n == 1 else 2
    rows = (n + cols - 1) // cols
    top, bottom = 460, 1590
    cell_w = (W - 120) // cols
    cell_h = (bottom - top) // rows
    for i, (b, photo) in enumerate(zip(books, photos)):
        r, c = divmod(i, cols)
        if n == 3 and i == 2:  # center the odd one
            cx0 = 60 + cell_w // 2
        else:
            cx0 = 60 + c * cell_w
        y0 = top + r * cell_h
        p = photo.copy()
        p.thumbnail((cell_w - 60, cell_h - 150), Image.LANCZOS)
        px, py = cx0 + (cell_w - p.width) // 2, y0 + 10
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rectangle([px + 8, py + 14, px + p.width + 8, py + p.height + 14], fill=160)
        canvas.paste(Image.new("RGB", (W, H), (0, 0, 0)), mask=sh.filter(ImageFilter.GaussianBlur(16)))
        canvas.paste(p, (px, py))
        draw = ImageDraw.Draw(canvas)
        main, _, arabic = post.split_title(b["title"])
        tf, lines = post.fit_lines(draw, main or arabic, "Amiri-Bold.ttf", cell_w - 40, 2, 34, 26)
        ty = py + p.height + 22
        kw = {"direction": "rtl"} if not main and post.features.check("raqm") else {}
        for ln in lines:
            bbx = draw.textbbox((0, 0), ln, font=tf, **kw)
            draw.text((cx0 + (cell_w - (bbx[2] - bbx[0])) / 2 - bbx[0], ty - bbx[1]), ln, font=tf, fill=post.CREAM, **kw)
            ty += bbx[3] - bbx[1] + 6
        pf = f("Amiri-Bold.ttf", 36)
        pr = post.fmt_price(b["price"])
        draw.text((cx0 + (cell_w - post.text_w(draw, pr, pf)) / 2, ty + 6), pr, font=pf, fill=post.GOLD)

    more = total - n
    if more > 0:
        mf = f("Amiri-Bold.ttf", 42)
        m = f"+ {more} more new books"
        draw.text(((W - post.text_w(draw, m, mf)) / 2, 1625), m, font=mf, fill=post.GOLD)
    ff = f("Amiri-Regular.ttf", 38)
    footer = f"Order at {post.SITE_LABEL}"
    draw.text(((W - post.text_w(draw, footer, ff)) / 2, 1690), footer, font=ff, fill=post.CREAM)
    return canvas


def announce(books):
    """One Telegram album + one IG story. Returns errors."""
    import requests
    skip_ig = os.getenv("SKIP_IG") == "1" or not os.getenv("IG_ACCESS_TOKEN")
    photos = [post.download_image(b["image"]) for b in books[:MAX_PHOTOS]]
    errors = []

    post.STORY_DIR.mkdir(exist_ok=True)
    path = post.STORY_DIR / f"new-arrivals-{int(time.time())}.jpg"
    render_story(books[:STORY_COVERS], photos[:STORY_COVERS], len(books)).save(path, "JPEG", quality=92)
    if not skip_ig:
        try:
            repo, branch = os.environ["GITHUB_REPOSITORY"], os.getenv("GITHUB_REF_NAME", "main")
            removed = post.prune_old_stories()
            post.push_files([post.STORY_DIR, *removed], "story: new arrivals")
            time.sleep(10)
            post.ig_post_story(f"https://raw.githubusercontent.com/{repo}/{branch}/stories/{path.name}")
        except Exception as e:
            errors.append(f"Instagram: {e}")

    try:
        cap = caption_for(books)
        if len(photos) == 1:
            post.telegram_post(books[0], photos[0], cap)
        else:
            media, files = [], {}
            for i, ph in enumerate(photos):
                buf = post.io.BytesIO()
                ph.save(buf, "JPEG", quality=90)
                files[f"p{i}"] = (f"p{i}.jpg", buf.getvalue(), "image/jpeg")
                item = {"type": "photo", "media": f"attach://p{i}"}
                if i == 0:
                    item.update(caption=cap, parse_mode="HTML")
                media.append(item)
            r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMediaGroup",
                              data={"chat_id": os.environ["TELEGRAM_CHANNEL"], "media": json.dumps(media)},
                              files=files, timeout=120)
            if not r.ok:
                raise RuntimeError(r.text)
    except Exception as e:
        errors.append(f"Telegram: {e}")

    if len(errors) < (2 - skip_ig):
        handles = [b["handle"] for b in books]
        post.commit_json(post.STATE_FILE, lambda s: s.update(
            announced_new=(s.get("announced_new", []) + handles)[-1000:]),
            "announced new books", {"posted": [], "history": []})
    return errors


def newbooks_cmd(rest, reply):
    hours = parse_window(rest)
    books = post.fetch_books()  # in stock, with a photo
    new = find_new(books, hours, post.load_state())
    window = f"{hours:g} hours" if hours < 48 else f"{hours / 24:g} days"
    if not new:
        reply(f"No new in-stock books were added in the last {window} (that haven't been announced already).\n"
              f"Try a longer window, e.g. <code>/newbooks 3d</code>")
        return
    errors = announce(new)
    names = "\n".join(f"• {html.escape(b['title'])}" for b in new)
    reply(f"📣 Announced {len(new)} new book{'s' if len(new) > 1 else ''} in one post:\n{names}"
          + ("\n\n⚠️ " + html.escape("; ".join(errors)) if errors else ""))
