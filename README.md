# Dar Al Athari Autopost

Posts one in-stock book from daralathari.com twice a day:
- **Instagram Story**: branded image with title, price and site
- **Telegram channel**: book photo, title, price and a "Buy now" button

Books rotate randomly and none repeats until every book has been posted.
Sold-out books are skipped automatically. New books are picked up automatically.

**Run it now:** Actions → Post a book → Run workflow.
**Change times:** edit the `cron` lines in `.github/workflows/autopost.yml` (times are UTC).

**Post a specific book:** Actions → Post a book → Run workflow → type part of the title in "Book name"
(e.g. `tawheed`, `three fundamental principles`, `ثلاثة الأصول`). Works from the GitHub phone app too.

**Post by messaging the bot:** DM your Telegram bot a book name (English or Arabic, one per line for several),
or `/random`. It replies ⏳ instantly and ✅ about a minute later when posted. Only the ID in `TELEGRAM_OWNER_ID` can use it.
Messages reach GitHub through the Cloudflare Worker in `cloudflare/worker.js`.

**Flash sales (Telegram bot):** `/sale kitab tawheed 20% 24h` or `/sale kitab tawheed $12 6h` (default 24h),
`/sales` to list, `/endsale kitab tawheed` to end early. Lowers the price on the store (old price crossed out),
announces it, and a job every 10 minutes restores the price when time is up.
Needs secrets `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` (Shopify Dev Dashboard app with product read/write access).
