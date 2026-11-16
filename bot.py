"""Telegram bot that chats with a Claude Managed Agent, plus a daily morning plan.

Only the Telegram user in ALLOWED_USER_ID can use it. The agent session ID is
kept in DATA_DIR/sessions.json (falls back to the AGENT_SESSION_ID env var),
so the agent remembers the conversation and the tracker.

Environment (in .env locally, or as variables on a cloud host):
  TELEGRAM_BOT_TOKEN   from @BotFather
  ANTHROPIC_API_KEY
  ALLOWED_USER_ID      your numeric Telegram user ID
  AGENT_SESSION_ID     optional: session to use if sessions.json has none
  DATA_DIR             optional: where state files live (default: this folder)
  MORNING_TIME         optional: default 09:00
  TIMEZONE             optional: default Europe/London
  MORNING_GREETING     optional: default "Sivayanama"

Commands:
  /today           send today's plan now
  /morning HH:MM   change the morning time  (/morning off to stop it)
  /tz Zone         change time zone, e.g. /tz Asia/Singapore
  /settings        show current settings
  /new             start a blank agent session (loses the tracker!)
"""

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anthropic
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

AGENT_ID = "agent_01VYB2Y6QgdMXvC63Edf6w46"
ENVIRONMENT_ID = "env_01QYwQfrzNwHYZqiLxNvZDmj"
DATA_DIR = Path(os.environ.get("DATA_DIR") or HERE)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_FILE = DATA_DIR / "sessions.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
OWNER = os.environ.get("ALLOWED_USER_ID", "")
GREETING = os.environ.get("MORNING_GREETING", "Sivayanama")
TG_LIMIT = 4000
MORNING_JOB = "morning"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

client = anthropic.AsyncAnthropic()
chat_locks: dict[int, asyncio.Lock] = {}
pending_approvals: dict[str, asyncio.Future] = {}


# ---------- persisted state ----------

def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def settings() -> dict:
    s = {"morning_time": os.environ.get("MORNING_TIME", "09:00"),
         "timezone": os.environ.get("TIMEZONE", "Europe/London")}
    s.update(read_json(SETTINGS_FILE))
    return s


def save_setting(key: str, value) -> None:
    data = read_json(SETTINGS_FILE)
    data[key] = value
    write_json(SETTINGS_FILE, data)


def save_session(chat_id: int, sid: str | None) -> None:
    data = read_json(SESSIONS_FILE)
    if sid is None:
        data[str(chat_id)] = None  # explicit "start fresh", overrides AGENT_SESSION_ID
    else:
        data[str(chat_id)] = sid
    write_json(SESSIONS_FILE, data)


async def get_session_id(chat_id: int) -> str:
    data = read_json(SESSIONS_FILE)
    if str(chat_id) in data:
        sid = data[str(chat_id)]
    else:
        sid = os.environ.get("AGENT_SESSION_ID") if str(chat_id) == OWNER else None
    if sid:
        try:
            s = await client.beta.sessions.retrieve(sid)
            if s.status != "terminated" and s.archived_at is None:
                if data.get(str(chat_id)) != sid:
                    save_session(chat_id, sid)
                return sid
            log.warning("session %s is %s/archived; starting a new one", sid, s.status)
        except anthropic.NotFoundError:
            log.warning("session %s not found; starting a new one", sid)
    s = await client.beta.sessions.create(agent=AGENT_ID, environment_id=ENVIRONMENT_ID)
    save_session(chat_id, s.id)
    log.info("chat %s -> new session %s", chat_id, s.id)
    return s.id


# ---------- sending helpers ----------

async def send_long(bot: Bot, chat_id: int, text: str) -> None:
    text = text.strip()
    while text:
        chunk, text = text[:TG_LIMIT], text[TG_LIMIT:]
        await bot.send_message(chat_id, chunk)


async def keep_typing(bot: Bot, chat_id: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4)
        except asyncio.TimeoutError:
            pass


async def ask_approval(bot: Bot, chat_id: int, event) -> bool:
    fut = asyncio.get_running_loop().create_future()
    pending_approvals[event.id] = fut
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("Allow", callback_data=f"allow:{event.id}"),
        InlineKeyboardButton("Deny", callback_data=f"deny:{event.id}"),
    ]])
    await bot.send_message(chat_id, f"The agent wants to run the tool '{event.name}'. Allow it?", reply_markup=buttons)
    try:
        return await asyncio.wait_for(fut, timeout=600)
    except asyncio.TimeoutError:
        await bot.send_message(chat_id, "No answer in 10 minutes, so I denied it.")
        return False
    finally:
        pending_approvals.pop(event.id, None)


# ---------- one agent turn ----------

async def run_turn(bot: Bot, chat_id: int, sid: str, text: str, prefix: str = "") -> None:
    """Send one message to the agent and relay its replies. `prefix` goes before the first reply."""
    async with await client.beta.sessions.events.stream(session_id=sid) as stream:
        await client.beta.sessions.events.send(
            session_id=sid,
            events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
        )
        async for event in stream:
            if event.type == "agent.message":
                reply = "".join(b.text for b in event.content if b.type == "text").strip()
                if reply:
                    if prefix:
                        reply, prefix = f"{prefix}\n\n{reply}", ""
                    await send_long(bot, chat_id, reply)

            elif event.type in ("agent.tool_use", "agent.mcp_tool_use") and getattr(
                event, "evaluated_permission", None
            ) == "ask":
                ok = await ask_approval(bot, chat_id, event)
                await client.beta.sessions.events.send(
                    session_id=sid,
                    events=[{"type": "user.tool_confirmation", "tool_use_id": event.id,
                             "result": "allow" if ok else "deny"}],
                )

            elif event.type == "agent.custom_tool_use":
                await bot.send_message(chat_id, f"The agent tried a custom tool ('{event.name}') this bot can't run, so I stopped it.")
                await client.beta.sessions.events.send(session_id=sid, events=[{"type": "user.interrupt"}])

            elif event.type == "session.error":
                log.error("session %s error: %s", sid, getattr(event, "error", event))
                await bot.send_message(chat_id, "The agent hit an error (details in the bot's log).")

            elif event.type == "session.status_idle":
                reason = event.stop_reason.type
                if reason == "requires_action":
                    continue
                if reason != "end_turn":
                    await bot.send_message(chat_id, f"The agent stopped early ({reason}).")
                return

            elif event.type == "session.status_terminated":
                await bot.send_message(chat_id, "That agent session ended unexpectedly. Ask Claude Code to help restore your tracker before sending more.")
                return


async def talk(bot: Bot, chat_id: int, text: str, prefix: str = "") -> None:
    """Run one turn with locking, typing indicator and error handling."""
    lock = chat_locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        await bot.send_message(chat_id, "Still working on the previous message; this one is next.")
    async with lock:
        stop = asyncio.Event()
        typing = asyncio.create_task(keep_typing(bot, chat_id, stop))
        try:
            sid = await get_session_id(chat_id)
            await run_turn(bot, chat_id, sid, text, prefix)
        except anthropic.AuthenticationError:
            log.error("Anthropic authentication failed; check ANTHROPIC_API_KEY")
            await bot.send_message(chat_id, "The bot's Anthropic API key isn't working.")
        except anthropic.RateLimitError:
            await bot.send_message(chat_id, "Rate limited. Try again in a minute.")
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            log.exception("Anthropic API error")
            await bot.send_message(chat_id, f"Couldn't reach the agent ({type(e).__name__}). Try again shortly.")
        finally:
            stop.set()
            await typing


# ---------- morning plan ----------

def morning_prompt() -> str:
    tz = settings()["timezone"]
    now = dt.datetime.now(ZoneInfo(tz))
    return (
        f"[Automatic morning check-in] It's {now:%A %d %B %Y}, {now:%H:%M} ({tz}). "
        "Using the tracker, give me today's plan: the time blocks for today, the top 1-3 priorities, "
        "and any hard deadline in the next 3 days. If yesterday's tasks may have slipped, mention it briefly "
        "and ask what got done. Keep it short and phone-friendly, no tables."
    )


async def send_morning(bot: Bot) -> None:
    if OWNER:
        await talk(bot, int(OWNER), morning_prompt(), prefix=GREETING)


async def morning_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("sending morning plan")
    await send_morning(ctx.bot)


def schedule_morning(app: Application) -> str:
    for job in app.job_queue.get_jobs_by_name(MORNING_JOB):
        job.schedule_removal()
    s = settings()
    if s["morning_time"] == "off":
        return "Morning plan is off."
    hh, mm = map(int, s["morning_time"].split(":"))
    tz = ZoneInfo(s["timezone"])
    app.job_queue.run_daily(morning_job, time=dt.time(hh, mm, tzinfo=tz), name=MORNING_JOB)
    return f"Morning plan at {s['morning_time']} ({s['timezone']})."


# ---------- Telegram handlers ----------

def allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and str(user.id) == OWNER


async def reject(update: Update) -> None:
    uid = update.effective_user.id if update.effective_user else "unknown"
    log.warning("rejected user %s", uid)
    if update.effective_message:
        await update.effective_message.reply_text(
            f"This bot is private. Your Telegram user ID is {uid}.\n"
            "If this is your bot, set ALLOWED_USER_ID to that number and restart it."
        )


def owner_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not allowed(update):
            return await reject(update)
        return await fn(update, ctx)
    return wrapper


@owner_only
async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hi! Message me and I'll pass it to your agent.\n" + schedule_morning(ctx.application))


@owner_only
async def on_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await send_morning(ctx.bot)


@owner_only
async def on_morning(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    arg = (ctx.args[0] if ctx.args else "").lower()
    if arg == "off":
        save_setting("morning_time", "off")
    else:
        try:
            t = dt.datetime.strptime(arg, "%H:%M")
        except ValueError:
            return await update.message.reply_text("Use /morning HH:MM (24-hour, e.g. /morning 08:45) or /morning off.")
        save_setting("morning_time", f"{t:%H:%M}")
    await update.message.reply_text(schedule_morning(ctx.application))


@owner_only
async def on_tz(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    arg = ctx.args[0] if ctx.args else ""
    try:
        ZoneInfo(arg)
    except (ZoneInfoNotFoundError, ValueError):
        return await update.message.reply_text("Use a zone name like /tz Europe/London or /tz Asia/Singapore.")
    save_setting("timezone", arg)
    await update.message.reply_text(schedule_morning(ctx.application))


@owner_only
async def on_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    s = settings()
    sid = read_json(SESSIONS_FILE).get(OWNER) or os.environ.get("AGENT_SESSION_ID") or "(none yet)"
    await update.message.reply_text(
        f"Morning plan: {s['morning_time']}\nTime zone: {s['timezone']}\nGreeting: {GREETING}\nAgent session: {sid}"
    )


@owner_only
async def on_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    save_session(update.effective_chat.id, None)
    await update.message.reply_text("OK, your next message starts a blank session (no tracker).")


@owner_only
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await talk(ctx.bot, update.effective_chat.id, update.message.text)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not allowed(update):
        return await query.answer("Not allowed.")
    choice, _, event_id = query.data.partition(":")
    fut = pending_approvals.get(event_id)
    if fut is None or fut.done():
        return await query.answer("This request has expired.")
    fut.set_result(choice == "allow")
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n{'Allowed' if choice == 'allow' else 'Denied'}.")


async def post_init(app: Application) -> None:
    log.info(schedule_morning(app))


def main() -> None:
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "ALLOWED_USER_ID") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing settings: {', '.join(missing)}")
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_BOT_TOKEN"])
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("today", on_today))
    app.add_handler(CommandHandler("morning", on_morning))
    app.add_handler(CommandHandler("tz", on_tz))
    app.add_handler(CommandHandler("settings", on_settings))
    app.add_handler(CommandHandler("new", on_new))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Bot running. Press Ctrl-C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
