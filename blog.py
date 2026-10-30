"""
Share a published Book Guides blog article to the Instagram Story and the Telegram channel.

Three ways to start it:
  1. Drop a JSON file in blog-queue/ and push it (this is what the blog-writing scheduled task does):
       {"article": "arabic-islamic-book-editions-explained", "telegram": "optional custom caption (HTML)"}
     The "Share a blog article" workflow runs, posts it, and moves the file to blog-queue/done/.
  2. Actions -> Share a blog article -> Run workflow -> paste the article URL or handle.
  3. DM the Telegram bot:  /blog arabic-islamic-book-editions-explained   (or the full URL)

The article must already be PUBLISHED on daralathari.com. Title, summary and image are read from
the live page, so nothing is posted that isn't on the site. Each article is shared once
(state/blog_posted.json); add "force": true to the queue file to share it again.

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL, IG_ACCESS_TOKEN (Instagram is skipped if missing),
     ARTICLE (for workflow runs), BLOG (default book-guides), FORCE=1, DRY_RUN=1
"""

import html as htmllib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter

import post

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "blog-queue"
DONE = QUEUE / "done"
STATE = ROOT / "state" / "blog_posted.json"
BLOG = os.getenv("BLOG", "book-guides")


# ---------------------------------------------------------------- article
def article_url(ref):
    ref = ref.strip()
    if ref.startswith("http"):
        return ref.split("?")[0].rstrip("/")
    ref = ref.strip("/").split("/")[-1]
    return f"{post.SHOP_URL}/blogs/{BLOG}/{ref}"


def _meta(page, prop):
    for pat in (rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]*)"',
                rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{prop}"'):
        m = re.search(pat, page, re.I)
        if m:
            return htmllib.unescape(m.group(1)).strip()
    return ""


def fetch_article(ref):
    url = article_url(ref)
    r = requests.get(url, headers=post.HEADERS, timeout=30)
    if r.status_code == 404:
        raise RuntimeError(f"Article not found at {url}. Is it published?")
    r.raise_for_status()
    page = r.text
    title = _meta(page, "og:title") or re.sub(r"\s+", " ", re.search(r"<title>(.*?)</title>", page, re.S).group(1))
    title = re.sub(r"\s*[|–-]\s*Dar Al Athari\s*$", "", title, flags=re.I).strip()
    image = _meta(page, "og:image")
    if image.startswith("//"):
        image = "https:" + image
    if image.startswith("http://"):
        image = "https://" + image[7:]
    return {
        "url": url,
        "handle": url.rsplit("/", 1)[-1],
        "title": title,
        "summary": _meta(page, "og:description"),
        "image": image,
    }


# ---------------------------------------------------------------- story image
def render_story(art, photo):
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), post.BG)
    if photo:
        blur = photo.copy()
        s = max(W / blur.width, H / blur.height)
        blur = blur.resize((int(blur.width * s) + 1, int(blur.height * s) + 1))
        blur = blur.crop(((blur.width - W) // 2, (blur.height - H) // 2,
                          (blur.width - W) // 2 + W, (blur.height - H) // 2 + H)).filter(ImageFilter.GaussianBlur(40))
        canvas = Image.blend(canvas, blur, 0.18)
    d = ImageDraw.Draw(canvas)

    # eyebrow below Instagram's top bar
    ef = post.font("Amiri-Bold.ttf", 40)
    label = "B O O K   G U I D E"
    d.text(((W - post.text_w(d, label, ef)) / 2, 250), label, font=ef, fill=post.GOLD)
    d.line([(W / 2 - 60, 312), (W / 2 + 60, 312)], fill=post.GOLD, width=2)

    # title
    f, lines = post.fit_lines(d, art["title"], "Amiri-Bold.ttf", 900, 4, 78, 48)
    y = post.draw_centered(d, 370, lines, f, post.CREAM, spacing=14)

    # image
    top = y + 50
    if photo:
        box_w, box_h = 640, max(380, 1360 - top)
        p = photo.copy()
        p.thumbnail((box_w, box_h), Image.LANCZOS)
        px, py = (W - p.width) // 2, top + (box_h - p.height) // 2
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rectangle([px + 10, py + 22, px + p.width + 10, py + p.height + 22], fill=170)
        canvas.paste(Image.new("RGB", (W, H), (0, 0, 0)), mask=sh.filter(ImageFilter.GaussianBlur(26)))
        canvas.paste(p, (px, py))
        d = ImageDraw.Draw(canvas)

    # call to action pill + footer (above Instagram's reply bar)
    pf = post.font("Amiri-Bold.ttf", 50)
    cta = "Read the full guide"
    pw = post.text_w(d, cta, pf) + 110
    py0 = 1460
    d.rounded_rectangle([(W - pw) / 2, py0, (W + pw) / 2, py0 + 96], radius=48, fill=post.GOLD)
    bb = d.textbbox((0, 0), cta, font=pf)
    d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], py0 + 48 - (bb[3] + bb[1]) / 2), cta, font=pf, fill=post.BG)
    ff = post.font("Amiri-Regular.ttf", 38)
    for text, yy, col in (("Link in bio", 1600, post.CREAM), (f"{post.SITE_LABEL}/blogs/{BLOG}", 1655, post.MUTED)):
        d.text(((W - post.text_w(d, text, ff)) / 2, yy), text, font=ff, fill=col)
    return canvas


# ---------------------------------------------------------------- Telegram
def telegram_caption(art):
    lines = [f"📖 <b>{post.esc(art['title'])}</b>"]
    if art["summary"]:
        lines.append(f"\n{post.esc(art['summary'])}")
    lines.append(f"\n👉 <a href=\"{art['url']}\">Read the full guide on {post.SITE_LABEL}</a>")
    return "\n".join(lines)


def telegram_send(art, photo, caption):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHANNEL"]
    keyboard = json.dumps({"inline_keyboard": [[{"text": "📖 Read the guide", "url": art["url"]}]]})
    if photo:
        buf = io.BytesIO()
        photo.save(buf, "JPEG", quality=92)
        buf.seek(0)
        r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data={
            "chat_id": chat, "caption": caption[:1024], "parse_mode": "HTML", "reply_markup": keyboard},
            files={"photo": ("guide.jpg", buf, "image/jpeg")}, timeout=60)
    else:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
            "chat_id": chat, "text": caption[:4096], "parse_mode": "HTML", "reply_markup": keyboard}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.text}")
    return r.json()["result"]["message_id"]


# ---------------------------------------------------------------- share
def already_shared(handle):
    if not STATE.exists():
        return False
    return handle in json.loads(STATE.read_text()).get("shared", [])


def record(handle):
    def mutate(s):
        s.setdefault("shared", [])
        if handle not in s["shared"]:
            s["shared"].append(handle)
        s["history"] = (s.get("history", []) + [
            {"handle": handle, "time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}])[-200:]
    post.commit_json(STATE, mutate, f"blog shared: {handle}", {"shared": [], "history": []})


def share(ref, telegram_text=None, force=False, dry=False, say=print):
    """Post one article everywhere configured. Returns (article, errors)."""
    art = fetch_article(ref)
    say(f"Article: {art['title']}\n{art['url']}")
    if already_shared(art["handle"]) and not force:
        say("Already shared earlier, so it was skipped. Use force to share it again.")
        return art, []

    photo = None
    if art["image"]:
        try:
            photo = post.download_image(art["image"])
        except Exception as e:
            say(f"Couldn't load the article image ({e}); posting without it.")

    post.STORY_DIR.mkdir(exist_ok=True)
    story_path = post.STORY_DIR / f"blog-{art['handle'][:70]}-{int(time.time())}.jpg"
    render_story(art, photo).save(story_path, "JPEG", quality=92)
    say(f"Story image: {story_path.name}")
    if dry:
        return art, []

    skip_ig = os.getenv("SKIP_IG") == "1" or not os.getenv("IG_ACCESS_TOKEN")
    skip_tg = os.getenv("SKIP_TELEGRAM") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN")
    errors, done = [], 0
    if skip_ig:
        say("Instagram skipped (no IG_ACCESS_TOKEN yet or turned off for this run)")
    else:
        try:
            repo = os.environ["GITHUB_REPOSITORY"]
            branch = os.getenv("GITHUB_REF_NAME", "main")
            removed = post.prune_old_stories()
            post.push_files([post.STORY_DIR, *removed], f"story: blog {art['handle']}")
            time.sleep(10)
            say(f"Instagram story id: {post.ig_post_story(f'https://raw.githubusercontent.com/{repo}/{branch}/stories/{story_path.name}')}")
            done += 1
        except Exception as e:
            errors.append(f"Instagram: {e}")
    if skip_tg:
        say("Telegram skipped (no TELEGRAM_BOT_TOKEN yet or turned off for this run)")
    else:
        try:
            say(f"Telegram message id: {telegram_send(art, photo, telegram_text or telegram_caption(art))}")
            done += 1
        except Exception as e:
            errors.append(f"Telegram: {e}")
    if done:
        record(art["handle"])
    return art, errors


# ---------------------------------------------------------------- entry points
def run_queue():
    files = sorted(QUEUE.glob("*.json"))
    if not files:
        print("Nothing in blog-queue/.")
        return []
    all_errors = []
    for f in files:
        job = json.loads(f.read_text())
        print(f"\n== {f.name}")
        try:
            _, errors = share(job["article"], job.get("telegram"), bool(job.get("force")), os.getenv("DRY_RUN") == "1")
        except Exception as e:
            errors = [str(e)]
        all_errors += [f"{f.name}: {e}" for e in errors]
        if os.getenv("DRY_RUN") != "1":
            DONE.mkdir(parents=True, exist_ok=True)
            job["result"] = errors or "shared"
            job["processed"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
            (DONE / f.name).write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n")
            f.unlink()
            post.push_files([QUEUE], f"blog queue: {f.stem} processed")
    return all_errors


def main():
    ref = os.getenv("ARTICLE", "").strip()
    if ref:
        _, errors = share(ref, force=os.getenv("FORCE") == "1", dry=os.getenv("DRY_RUN") == "1")
    else:
        errors = run_queue()
    if errors:
        sys.exit("\n".join(errors))


if __name__ == "__main__":
    main()
