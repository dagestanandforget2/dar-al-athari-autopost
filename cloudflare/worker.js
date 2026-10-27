// Dar Al Athari – Telegram bot webhook (Cloudflare Worker)
// Receives your DMs instantly and starts the GitHub job that posts the book.
//
// Variables (Worker → Settings → Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  bot token from @BotFather            (Secret)
//   GH_TOKEN            GitHub fine-grained token, this repo, Contents: Read and write (Secret)
//   WEBHOOK_SECRET      any long random letters/numbers you make up   (Secret)
//   OWNER_ID            your Telegram ID (the bot tells you if it's missing)
//   GH_REPO             dagestanandforget2/dar-al-athari-autopost
//
// Schedule (Worker → Settings → Triggers → Cron Triggers): one trigger, "*/5 * * * *".
// Every 5 minutes it checks the New York time: at 12:20 PM and 7:15 PM it starts the
// daily book post, and any time a flash sale has run out it starts the job that ends it.

const HELP =
  "<b>Dar Al Athari bot</b>\n\n" +
  "📚 <b>Post a book</b>\n" +
  "Send a book name (English or Arabic), e.g. <code>kitab at-tauhid</code>\n" +
  "One per line to post several · /random for a random book\n\n" +
  "🆕 <b>New arrivals</b>\n" +
  "/newbooks – books added in the last 24h, in one post\n" +
  "<code>/newbooks 3d</code> – look back 3 days\n\n" +
  "⚡️ <b>Flash sales</b> (changes the price on the store)\n" +
  "<code>/sale kitab at-tauhid 20% 24h</code>\n" +
  "<code>/sale kitab at-tauhid $12 6h</code>\n" +
  "Several books: one per line after /sale\n" +
  "/sales – running sales · <code>/endsale kitab at-tauhid</code>\n" +
  "/salepost – post a roundup of books on sale\n\n" +
  "🎟️ <b>Discount codes</b>\n" +
  "<code>/code RAMADAN15 15%</code> – new code, everything, 48h\n" +
  "<code>/code TAWHEED5 $5 kitab at-tauhid 3d</code> – one book\n" +
  "<code>/code Sharh</code> – announce an existing code\n" +
  "/codes – active codes with uses and sales\n" +
  "<code>/endcode RAMADAN15</code> – switch a code off\n\n" +
  "Replies come back in about a minute.";

// The menu Telegram shows when you type "/"
const COMMANDS = [
  { command: "help", description: "All commands" },
  { command: "random", description: "Post a random in-stock book" },
  { command: "newbooks", description: "Announce books added in the last 24h" },
  { command: "sale", description: "Start a flash sale: /sale book 20% 24h" },
  { command: "sales", description: "List running sales" },
  { command: "endsale", description: "End a sale early: /endsale book" },
  { command: "salepost", description: "Post a roundup of books on sale" },
  { command: "code", description: "New or existing discount code: /code NAME 15%" },
  { command: "codes", description: "Active codes with uses and sales" },
  { command: "endcode", description: "Switch a code off: /endcode NAME" },
];

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

const reply = (env, chat, text) =>
  tg(env, "sendMessage", { chat_id: chat, text, parse_mode: "HTML", disable_web_page_preview: true });

// ---------------------------------------------------------------- schedule
const POST_TIMES = [[12, 20], [19, 15]]; // New York time: 12:20 PM and 7:15 PM

function nyTime(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23",
  }).formatToParts(date);
  const get = (t) => Number(parts.find((p) => p.type === t).value);
  return [get("hour"), get("minute")];
}

function dispatch(env, eventType, payload = {}) {
  return fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "daralathari-bot",
    },
    body: JSON.stringify({ event_type: eventType, client_payload: payload }),
  });
}

async function onSchedule(env, when) {
  const [h, m] = nyTime(when);
  // Fires every 5 minutes, so exactly one run lands in each 5-minute posting window
  if (POST_TIMES.some(([ph, pm]) => h === ph && m >= pm && m < pm + 5)) {
    await dispatch(env, "scheduled-post");
  }
  // End flash sales on time: only wake GitHub when one is actually due
  try {
    const r = await fetch(`https://raw.githubusercontent.com/${env.GH_REPO}/main/state/sales.json`,
                          { cf: { cacheTtl: 0 } });
    if (r.ok) {
      const sales = await r.json();
      const now = Date.now() / 1000;
      if (Object.values(sales).some((s) => s.ends_at <= now)) await dispatch(env, "end-sales");
    }
  } catch (e) { /* no sales file yet */ }
}

async function handle(env, chat, uid, text) {
  if (!env.OWNER_ID) {
    return reply(env, chat, `Your Telegram ID is <code>${uid}</code>.\n\nAdd it to the Worker as the variable <b>OWNER_ID</b>.`);
  }
  if (uid !== String(env.OWNER_ID).trim()) return reply(env, chat, "Sorry, this bot is private.");

  const cmd = text.split(/\s+/)[0].toLowerCase();
  if (cmd === "/start" || cmd === "/help") return reply(env, chat, HELP);

  const r = await dispatch(env, "telegram", { chat: String(chat), text: text.slice(0, 2000) });
  if (r.status === 204) return reply(env, chat, "⏳ On it… (about a minute)");
  return reply(env, chat, `⚠️ Couldn't start the job (GitHub said ${r.status}). Check the GH_TOKEN variable.`);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(onSchedule(env, new Date(event.scheduledTime)));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // One-time: open https://<your-worker>/setup?key=<WEBHOOK_SECRET> to connect the bot
    if (request.method === "GET" && url.pathname === "/setup") {
      if (url.searchParams.get("key") !== env.WEBHOOK_SECRET) return new Response("Wrong key", { status: 403 });
      await tg(env, "setMyCommands", { commands: COMMANDS });
      const res = await tg(env, "setWebhook", {
        url: `${url.origin}/telegram`,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message"],
        drop_pending_updates: true,
      });
      return new Response(res.ok ? "✅ Bot connected and command menu updated. Message it a book name." : `❌ ${JSON.stringify(res)}`, {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    if (request.method !== "POST" || url.pathname !== "/telegram") return new Response("OK");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    const update = await request.json().catch(() => ({}));
    const m = update.message;
    if (m && m.chat && m.chat.type === "private" && m.text) {
      ctx.waitUntil(handle(env, m.chat.id, String(m.from.id), m.text.trim()));
    }
    return new Response("OK"); // answer Telegram immediately
  },
};
