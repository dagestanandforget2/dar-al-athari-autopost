"""
Dar Al Athari autoposter.

Each run:
  1. Pulls in-stock books from the store's public catalog (no Shopify token needed)
  2. Picks the next book that hasn't been posted yet
  3. Builds a 1080x1920 story image (photo + title + price + site)
  4. Publishes it as an Instagram Story
  5. Posts the book photo + name + price + "Buy now" button to the Telegram channel
  6. Records it in state/posted.json so it isn't repeated until every book has had a turn

Env vars (set as GitHub secrets / workflow env):
  IG_ACCESS_TOKEN     Instagram long-lived token (Instagram Login)
  TELEGRAM_BOT_TOKEN  from @BotFather
  TELEGRAM_CHANNEL    e.g. @daralathari  (or the numeric -100... id)
  GITHUB_REPOSITORY   set automatically by GitHub Actions (owner/repo)
  SHOP_URL            default https://daralathari.com
  COLLECTION          default all-books
  BOOK="tawheed"       post this specific book instead of a random one
  DRY_RUN=1           build the image only, post nothing
  SKIP_IG=1 / SKIP_TELEGRAM=1   post to one platform only
"""

import io
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "posted.json"
STORY_DIR = ROOT / "stories"
FONT_DIR = ROOT / "fonts"

SHOP_URL = os.getenv("SHOP_URL", "https://daralathari.com").rstrip("/")
COLLECTION = os.getenv("COLLECTION", "all-books")
SITE_LABEL = os.getenv("SITE_LABEL", "daralathari.com")
IG_API = "https://graph.instagram.com/v23.0"

# Brand colours
BG = (17, 32, 26)          # deep green-black
GOLD = (201, 167, 90)
CREAM = (245, 239, 225)
MUTED = (170, 180, 172)

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
HEADERS = {"User-Agent": "DarAlAthari-Autopost/1.0"}


# ---------------------------------------------------------------- catalog
def fetch_books(include_sold_out=False):
    books, page = [], 1
    while True:
        url = f"{SHOP_URL}/collections/{COLLECTION}/products.json?limit=250&page={page}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        products = r.json().get("products", [])
        if not products:
            break
        for p in products:
            variants = p.get("variants") or []
            available = [v for v in variants if v.get("available")]
            if not p.get("images") or not variants or (not available and not include_sold_out):
                continue  # skip sold-out books and books with no photo
            price = min(float(v["price"]) for v in (available or variants))
            books.append({
                "handle": p["handle"],
                "title": p["title"].strip(),
                "vendor": (p.get("vendor") or "").strip(),
                "price": price,
                "image": p["images"][0]["src"],
                "url": f"{SHOP_URL}/products/{p['handle']}",
                "in_stock": bool(available),
            })
        page += 1
    return books


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posted": [], "history": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def pick_book(books, state):
    posted = set(state["posted"])
    remaining = [b for b in books if b["handle"] not in posted]
    if not remaining:  # every book has had a turn -> start a new cycle
        last = state["posted"][-1] if state["posted"] else None
        state["posted"] = []
        remaining = [b for b in books if b["handle"] != last] or books
    return random.choice(remaining)


def _norm(t):
    t = t.lower()
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)          # Arabic diacritics/tatweel
    t = t.replace("'", "").replace("’", "").replace("‘", "")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\b(al|ad|an|ar|as|ash|at|az|ul|us|ut)\s+", "", t)  # al-, ad-, ...
    return re.sub(r"\s+", " ", t).strip()


def find_book(books, query):
    """Best match for a typed name. Tolerates spelling (Tawheed/Tauhid), al-/ad- and Arabic."""
    from difflib import SequenceMatcher
    q = _norm(query)
    q_words = q.split()

    def word_score(w, words):
        return max((SequenceMatcher(None, w, x).ratio() for x in words), default=0)

    scored = []
    for b in books:
        hay = _norm(b["title"] + " " + b["vendor"] + " " + b["handle"].replace("-", " "))
        words = hay.split()
        if not words:
            continue
        coverage = sum(word_score(w, words) for w in q_words) / max(len(q_words), 1)
        whole = 1.0 if q in hay else 0.0
        scored.append((coverage + 0.5 * whole, b))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ---------------------------------------------------------------- text helpers
def fmt_price(p):
    return f"${p:,.2f}"


def split_title(title):
    """'English - Arabic - Arabic' -> ('English', 'Arabic - Arabic');
    'Main: Subtitle' -> main line + subtitle."""
    parts = [s.strip() for s in re.split(r"\s+[-–—]\s+", title) if s.strip()]
    latin = [s for s in parts if not ARABIC_RE.search(s)]
    arabic = [s for s in parts if ARABIC_RE.search(s)]
    main = " · ".join(latin) if latin else ""
    sub = ""
    if ":" in main:
        main, sub = [s.strip() for s in main.split(":", 1)]
    return main, sub, " - ".join(arabic)


def font(name, size):
    path = FONT_DIR / name
    if path.exists():
        engine = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
        return ImageFont.truetype(str(path), size, layout_engine=engine)
    for fallback in ("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"):
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size)
    return ImageFont.load_default(size)


def text_w(draw, text, f):
    return draw.textbbox((0, 0), text, font=f)[2]


def wrap(draw, text, f, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if text_w(draw, trial, f) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def fit_lines(draw, text, font_name, max_w, max_lines, start, minimum):
    """Largest font size where text fits in max_lines; truncates as a last resort."""
    size = start
    while size >= minimum:
        f = font(font_name, size)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = font(font_name, minimum)
    lines = wrap(draw, text, f, max_w)[:max_lines]
    lines[-1] = lines[-1].rstrip(".,;:") + "…"
    return f, lines


def draw_centered(draw, y, lines, f, fill, spacing=12, direction=None):
    for line in lines:
        kw = {"direction": direction} if direction and features.check("raqm") else {}
        bbox = draw.textbbox((0, 0), line, font=f, **kw)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((1080 - w) / 2 - bbox[0], y - bbox[1]), line, font=f, fill=fill, **kw)
        y += h + spacing
    return y


# ---------------------------------------------------------------- image
def download_image(url):
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def render_story(book, photo):
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), BG)

    # soft blurred photo behind everything
    blur = photo.copy()
    scale = max(W / blur.width, H / blur.height)
    blur = blur.resize((int(blur.width * scale) + 1, int(blur.height * scale) + 1))
    blur = blur.crop(((blur.width - W) // 2, (blur.height - H) // 2,
                      (blur.width - W) // 2 + W, (blur.height - H) // 2 + H))
    blur = blur.filter(ImageFilter.GaussianBlur(40))
    canvas = Image.blend(canvas, blur, 0.22)
    draw = ImageDraw.Draw(canvas)

    # brand line (below Instagram's top UI)
    brand = font("Amiri-Bold.ttf", 40)
    label = "D A R   A L   A T H A R I"
    draw.text(((W - text_w(draw, label, brand)) / 2, 250), label, font=brand, fill=GOLD)
    draw.line([(W / 2 - 60, 312), (W / 2 + 60, 312)], fill=GOLD, width=2)

    # book photo with shadow
    box_w, box_h = 700, 820
    p = photo.copy()
    p.thumbnail((box_w, box_h), Image.LANCZOS)
    px, py = (W - p.width) // 2, 360 + (box_h - p.height) // 2
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rectangle([px + 10, py + 22, px + p.width + 10, py + p.height + 22], fill=170)
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    canvas.paste(Image.new("RGB", (W, H), (0, 0, 0)), mask=shadow)
    canvas.paste(p, (px, py))
    draw = ImageDraw.Draw(canvas)

    # title block
    main, sub, arabic = split_title(book["title"])
    y = 360 + box_h + 56
    if main:
        f, lines = fit_lines(draw, main, "Amiri-Bold.ttf", 920, 3 if not arabic else 2, 64, 40)
        y = draw_centered(draw, y, lines, f, CREAM, spacing=10)
    if sub:
        f, lines = fit_lines(draw, sub, "Amiri-Regular.ttf", 900, 1, 38, 28)
        y = draw_centered(draw, y + 6, lines, f, MUTED)
    if arabic:
        f, lines = fit_lines(draw, arabic, "Amiri-Bold.ttf", 920, 2, 54, 36)
        y = draw_centered(draw, y + 14, lines, f, GOLD, spacing=8, direction="rtl")

    # price pill
    pf = font("Amiri-Bold.ttf", 66)
    price = fmt_price(book["price"])
    pw = text_w(draw, price, pf) + 100
    py0 = max(y + 34, 1500)
    py0 = min(py0, 1560)
    draw.rounded_rectangle([(W - pw) / 2, py0, (W + pw) / 2, py0 + 104], radius=52, fill=GOLD)
    bbox = draw.textbbox((0, 0), price, font=pf)
    draw.text(((W - (bbox[2] - bbox[0])) / 2 - bbox[0], py0 + 52 - (bbox[3] + bbox[1]) / 2),
              price, font=pf, fill=BG)

    # footer (above Instagram's bottom reply bar)
    ff = font("Amiri-Regular.ttf", 38)
    footer = f"Order at {SITE_LABEL}"
    draw.text(((W - text_w(draw, footer, ff)) / 2, 1690), footer, font=ff, fill=CREAM)
    return canvas


# ---------------------------------------------------------------- git (public image URL for Instagram)
def git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def push_files(paths, message):
    git("add", *[str(p) for p in paths])
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    git("commit", "-m", message)
    for attempt in range(3):
        try:
            git("pull", "--rebase", "-q")
            git("push", "-q")
            return
        except subprocess.CalledProcessError:
            time.sleep(5)
    raise RuntimeError("git push failed")


def prune_old_stories(keep=6):
    files = sorted(STORY_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    old = files[:-keep]
    for f in old:
        f.unlink()
    return old


# ---------------------------------------------------------------- Instagram
def ig_post_story(image_url):
    token = os.environ["IG_ACCESS_TOKEN"]
    me = requests.get(f"{IG_API}/me", params={"fields": "user_id,username", "access_token": token}, timeout=30)
    me.raise_for_status()
    ig_id = me.json()["user_id"]

    r = requests.post(f"{IG_API}/{ig_id}/media", data={
        "image_url": image_url, "media_type": "STORIES", "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Instagram container error: {r.text}")
    container = r.json()["id"]

    for _ in range(20):  # wait until Instagram has fetched the image
        s = requests.get(f"{IG_API}/{container}", params={"fields": "status_code", "access_token": token}, timeout=30).json()
        status = s.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Instagram processing error: {s}")
        time.sleep(5)

    r = requests.post(f"{IG_API}/{ig_id}/media_publish", data={
        "creation_id": container, "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Instagram publish error: {r.text}")
    return r.json()["id"]


# ---------------------------------------------------------------- Telegram
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def telegram_post(book, photo):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHANNEL"]
    lines = [f"📚 <b>{esc(book['title'])}</b>"]
    if book["vendor"] and book["vendor"].lower() != "dar al athari":
        lines.append(f"✍️ {esc(book['vendor'])}")
    lines.append(f"💵 <b>{fmt_price(book['price'])}</b>")
    lines.append(f"\n🛒 <a href=\"{book['url']}\">Order now on {SITE_LABEL}</a>")
    caption = "\n".join(lines)[:1024]

    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=92)
    buf.seek(0)
    keyboard = {"inline_keyboard": [[{"text": f"🛒 Buy now – {fmt_price(book['price'])}", "url": book["url"]}]]}
    r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data={
        "chat_id": chat, "caption": caption, "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard)}, files={"photo": ("book.jpg", buf, "image/jpeg")}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.text}")
    return r.json()["result"]["message_id"]


# ---------------------------------------------------------------- main
def record_posted(handle):
    """Mark a book as posted and push. If the bot and the schedule both posted at once,
    re-sync with GitHub and re-apply instead of failing on a merge conflict."""
    for attempt in range(4):
        state = load_state()
        if handle not in state["posted"]:
            state["posted"].append(handle)
        state["history"] = (state.get("history", []) + [
            {"handle": handle, "time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}])[-200:]
        save_state(state)
        try:
            push_files([STATE_FILE], f"posted: {handle}")
            return
        except (RuntimeError, subprocess.CalledProcessError):
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT)
            git("fetch", "-q", "origin")
            git("reset", "-q", "--hard", f"origin/{os.getenv('GITHUB_REF_NAME', 'main')}")
            time.sleep(3)
    print("Warning: could not save posted state")


def publish(book, dry=False, skip_ig=None, skip_tg=None):
    """Render the story and post one book everywhere that's configured. Returns a list of errors."""
    if skip_ig is None:
        skip_ig = os.getenv("SKIP_IG") == "1" or not os.getenv("IG_ACCESS_TOKEN")
    if skip_tg is None:
        skip_tg = os.getenv("SKIP_TELEGRAM") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN")
    if skip_ig:
        print("Instagram skipped (no IG_ACCESS_TOKEN yet or turned off for this run)")
    if skip_tg:
        print("Telegram skipped (no TELEGRAM_BOT_TOKEN yet or turned off for this run)")
    print(f"Posting: {book['title']} – {fmt_price(book['price'])}")

    photo = download_image(book["image"])
    STORY_DIR.mkdir(exist_ok=True)
    story_path = STORY_DIR / f"{book['handle'][:80]}-{int(time.time())}.jpg"
    render_story(book, photo).save(story_path, "JPEG", quality=92)
    print(f"Story image: {story_path.name}")

    if dry:
        print("DRY_RUN – nothing posted.")
        return []

    errors = []
    if not skip_ig:
        try:
            repo = os.environ["GITHUB_REPOSITORY"]
            branch = os.getenv("GITHUB_REF_NAME", "main")
            removed = prune_old_stories()
            push_files([STORY_DIR, *removed], f"story: {book['handle']}")
            image_url = f"https://raw.githubusercontent.com/{repo}/{branch}/stories/{story_path.name}"
            time.sleep(10)
            print("Instagram story id:", ig_post_story(image_url))
        except Exception as e:
            errors.append(f"Instagram: {e}")

    if not skip_tg:
        try:
            print("Telegram message id:", telegram_post(book, photo))
        except Exception as e:
            errors.append(f"Telegram: {e}")

    if len(errors) < (2 - skip_ig - skip_tg):  # at least one platform worked
        record_posted(book["handle"])
    return errors


def main():
    wanted = os.getenv("BOOK", "").strip()
    if wanted:
        scored = find_book(fetch_books(include_sold_out=True), wanted)
        print("Top matches:")
        for sc, b in scored[:5]:
            print(f"  {sc:.2f}  {b['title']}")
        if not scored or scored[0][0] < 0.75:
            sys.exit(f"No book found matching '{wanted}'. Try a different spelling or more of the title.")
        book = scored[0][1]
        if not book["in_stock"]:
            sys.exit(f"'{book['title']}' is sold out, so it wasn't posted.")
    else:
        books = fetch_books()
        if not books:
            sys.exit("No in-stock books with photos found.")
        book = pick_book(books, load_state())

    errors = publish(book, dry=os.getenv("DRY_RUN") == "1")
    if errors:
        sys.exit("\n".join(errors))


if __name__ == "__main__":
    main()
