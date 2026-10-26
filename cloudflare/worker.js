// Dar Al Athari – Telegram bot webhook (Cloudflare Worker)
// Receives your DMs instantly and starts the GitHub job that posts the book.
//
// Variables (Worker → Settings → Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  bot token from @BotFather            (Secret)
//   GH_TOKEN            GitHub fine-grained token, this repo, Contents: Read and write (Secret)
//   WEBHOOK_SECRET      any long random letters/numbers you make up   (Secret)
//   OWNER_ID            your Telegram ID (the bot tells you if it's missing)
//   GH_REPO             dagestanandforget2/dar-al-athari-autopost

const HELP =
  "Send me a book name (English or Arabic) and I'll post it to the channel.\n\n" +
  "• <code>kitab tawheed</code>\n• <code>ثلاثة الأصول</code>\n" +
  "• One book per line to post several\n• /random – a random in-stock book";

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

async function handle(env, chat, uid, text) {
  if (!env.OWNER_ID) {
    return reply(env, chat, `Your Telegram ID is <code>${uid}</code>.\n\nAdd it to the Worker as the variable <b>OWNER_ID</b>.`);
  }
  if (uid !== String(env.OWNER_ID).trim()) return reply(env, chat, "Sorry, this bot is private.");

  const cmd = text.split(/\s+/)[0].toLowerCase();
  if (cmd === "/start" || cmd === "/help") return reply(env, chat, HELP);

  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "daralathari-bot",
    },
    body: JSON.stringify({ event_type: "telegram", client_payload: { chat: String(chat), text: text.slice(0, 2000) } }),
  });
  if (r.status === 204) return reply(env, chat, "⏳ Posting… (about a minute)");
  return reply(env, chat, `⚠️ Couldn't start the job (GitHub said ${r.status}). Check the GH_TOKEN variable.`);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // One-time: open https://<your-worker>/setup?key=<WEBHOOK_SECRET> to connect the bot
    if (request.method === "GET" && url.pathname === "/setup") {
      if (url.searchParams.get("key") !== env.WEBHOOK_SECRET) return new Response("Wrong key", { status: 403 });
      const res = await tg(env, "setWebhook", {
        url: `${url.origin}/telegram`,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message"],
        drop_pending_updates: true,
      });
      return new Response(res.ok ? "✅ Bot connected. Message it a book name." : `❌ ${JSON.stringify(res)}`, {
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
