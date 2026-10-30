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

**Sale roundups:** when 2+ in-stock books are on sale (not counting anything in a Damaged or Clearance collection, or with those words in the title) (flash sales or a compare-at price set in Shopify),
one scheduled post a day becomes a roundup featuring 2–3 sale books (rotating), as a Telegram album and one IG story.
`/salepost` posts one now. `/sale` with one book per line starts them all and announces them in one roundup.
Change the frequency with `ROUNDUP_EVERY_HOURS` (default 20).

**Discount codes (Telegram bot):** `/code RAMADAN15 15%` creates a 15%-off-everything code (48h default) in Shopify
and announces it; `/code TAWHEED5 $5 kitab at-tauhid 3d` for one book; `/code Sharh` announces an existing code;
`/codes` lists active codes with uses and sales; `/endcode CODE` switches one off. Posts link to
daralathari.com/discount/CODE so the code applies automatically. Needs `read_discounts` + `write_discounts` on the Shopify app.

**New arrivals (Telegram bot):** `/newbooks` announces every in-stock book published in the last 24 hours
(`/newbooks 3d` to look further back) in ONE post: a short blurb, an album of up to 10 covers, each title with price
and link, plus an IG story with up to 4 covers. Already-announced books and Damaged/Clearance copies are skipped.

**Blog guides (Instagram Story + Telegram):** shares a *published* Book Guides article: title, summary and image
are read from the live page. Three ways to start it:
- the blog-writing scheduled task adds a file to `blog-queue/` after you approve a post (automatic);
- Actions → Share a blog article → Run workflow → paste the article link or handle;
- DM the bot `/blog <link or handle>` (add ` again` at the end to re-share).
Each article is shared once (`state/blog_posted.json`). Instagram is skipped until `IG_ACCESS_TOKEN` is set.
