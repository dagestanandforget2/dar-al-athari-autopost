"""
Telegram bot commands: DM the bot a book name and it posts that book.

Messages arrive instantly through the Cloudflare Worker (cloudflare/worker.js),
which starts this script via a GitHub "repository_dispatch" with BOT_CHAT and BOT_TEXT.

  <book name>        e.g. "kitab tawheed" or "الداء والدواء" -> posts that book
  several lines      one book per line -> posts each
  /random            posts a random in-stock book
  /help              shows this

Only the Telegram user in TELEGRAM_OWNER_ID can use it. If that secret isn't set yet,
the bot replies to any DM with the sender's Telegram ID so it can be added.
"""

import html
import json
import os

import requests

import post

API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
OWNER = os.getenv("TELEGRAM_OWNER_ID", "").strip()
MATCH = 0.75

HELP = ("Send me a book name (English or Arabic) and I'll post it to the channel.\n\n"
        "• <code>kitab tawheed</code>\n• <code>ثلاثة الأصول</code>\n"
        "• One book per line to post several\n• /random – a random in-stock book\n\n"
        "⚡️ Flash sales:\n• <code>/sale kitab tawheed 20% 24h</code>\n• <code>/sale kitab tawheed $12 6h</code>\n"
        "• /sales – list running sales\n• <code>/endsale kitab tawheed</code> – end early\n\n"
        "Posts take about a minute.")


def tg(method, **data):
    return requests.post(f"{API}/{method}", data=data, timeout=60).json()


def reply(chat, text, keyboard=None):
    data = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    data["reply_markup"] = json.dumps(keyboard if keyboard else {"remove_keyboard": True})
    tg("sendMessage", **data)


def choice_keyboard(books):
    return {"keyboard": [[{"text": b["title"]}] for b in books],
            "one_time_keyboard": True, "resize_keyboard": True}


def handle_query(chat, text, catalog):
    in_stock = [b for b in catalog if b["in_stock"]]
    if text.lower().lstrip("/") == "random":
        book = post.pick_book(in_stock, post.load_state())
    else:
        scored = post.find_book(catalog, text)
        if not scored or scored[0][0] < MATCH:
            close = [b for sc, b in scored[:3] if sc >= 0.62]
            if close:
                reply(chat, f"Couldn't find “{html.escape(text)}”. Did you mean one of these? Tap it to post.",
                      choice_keyboard(close))
            else:
                reply(chat, f"Couldn't find any book matching “{html.escape(text)}”. Try another spelling or more of the title.")
            return
        top = scored[0][0]
        tied = [b for sc, b in scored[:5] if sc >= MATCH and top - sc < 0.05]
        if len(tied) > 1:
            reply(chat, "Which one? Tap it to post.", choice_keyboard(tied))
            return
        book = scored[0][1]
        if not book["in_stock"]:
            reply(chat, f"❌ <b>{html.escape(book['title'])}</b> is sold out, so I didn't post it.")
            return

    errors = post.publish(book)
    title = f"<b>{html.escape(book['title'])}</b> – {post.fmt_price(book['price'])}"
    if errors:
        reply(chat, f"⚠️ Problem posting {title}:\n<code>{html.escape(chr(10).join(errors))[:3000]}</code>")
    else:
        reply(chat, f"✅ Posted {title}")


def handle_message(chat, uid, text, catalog_cache):
    if not OWNER:
        reply(chat, f"Your Telegram ID is <code>{uid}</code>.\n\nAdd it in GitHub as the secret "
                    f"<b>TELEGRAM_OWNER_ID</b>, then send me a book name.")
        return
    if str(uid) != OWNER:
        reply(chat, "Sorry, this bot is private.")
        return
    cmd = text.split()[0].lower().split("@")[0]
    if cmd in ("/start", "/help"):
        reply(chat, HELP)
        return
    if cmd in ("/sale", "/endsale", "/sales"):
        import sale
        rest = text[len(text.split()[0]):].strip()
        say = lambda msg: reply(chat, msg)
        try:
            if cmd == "/sales":
                sale.list_sales(say)
            elif cmd == "/endsale":
                sale.end_sale_cmd(rest, say)
            else:
                if not catalog_cache:
                    catalog_cache.extend(post.fetch_books(include_sold_out=True))
                sale.start_sale(rest, catalog_cache, say)
        except Exception as e:
            reply(chat, f"⚠️ Sale problem: <code>{html.escape(str(e))[:800]}</code>")
        return
    if not catalog_cache:
        catalog_cache.extend(post.fetch_books(include_sold_out=True))
    for line in [l.strip() for l in text.splitlines() if l.strip()][:10]:
        try:
            handle_query(chat, line, catalog_cache)
        except Exception as e:
            reply(chat, f"⚠️ Something went wrong with “{html.escape(line)}”: <code>{html.escape(str(e))[:500]}</code>")


def main():
    # Instant mode: started by the Cloudflare Worker with one message
    if os.getenv("BOT_TEXT"):
        chat = os.environ["BOT_CHAT"]
        handle_message(chat, chat, os.environ["BOT_TEXT"].strip(), [])  # private chat id == user id
        return

    # Fallback polling mode (only works while no webhook is set)
    updates = tg("getUpdates", timeout=0).get("result", [])
    if not updates:
        return
    tg("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0)
    cache = []
    for u in updates:
        m = u.get("message")
        if m and m.get("chat", {}).get("type") == "private" and m.get("text"):
            handle_message(m["chat"]["id"], m["from"]["id"], m["text"].strip(), cache)


if __name__ == "__main__":
    main()
