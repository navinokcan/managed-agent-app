# Scheduler Agent Telegram Bot

A private Telegram bot for a personal scheduling assistant built on a [Claude Managed Agent](https://docs.anthropic.com/). You talk to the agent from your phone, and every morning it sends you a plan for the day based on the tracker it keeps in its session.

## Features

- **Chat from Telegram.** Messages go to your Managed Agent. The agent reuses the same session across restarts, so it keeps its memory and tracker.
- **Daily morning plan.** At the time you choose, the bot sends your time blocks, your top 1–3 priorities and any deadlines due in the next 3 days.
- **Tool approvals.** When the agent wants to run a tool that needs permission, you get **Allow** / **Deny** buttons.
- **Owner only.** Anyone other than the user in `ALLOWED_USER_ID` is turned away.
- **Two terminal clients:** `main.py` is an interactive chat and `once.py` sends a single message.

## Commands

| Command | What it does |
|---|---|
| `/today` | Send today's plan now |
| `/morning HH:MM` | Set the morning plan time (24-hour), or `/morning off` to stop it |
| `/tz Zone` | Set your time zone, e.g. `/tz Asia/Singapore` |
| `/settings` | Show the current time, time zone and agent session |
| `/new` | Start a blank agent session (**this loses the tracker**) |

Any other text you send is passed to the agent.

## Setup

### 1. Prerequisites

- Python 3.13
- An [Anthropic API key](https://console.anthropic.com/)
- A Managed Agent and environment. Put their IDs in `AGENT_ID` and `ENVIRONMENT_ID` at the top of `bot.py`, `main.py` and `once.py`.
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 2. Install

```bash
git clone https://github.com/navinokcan/managed-agent-app.git
cd managed-agent-app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Then fill in `.env`:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Token from @BotFather |
| `ANTHROPIC_API_KEY` | yes | Your Anthropic API key |
| `ALLOWED_USER_ID` | yes | Your numeric Telegram user ID (see below) |
| `AGENT_SESSION_ID` | no | An existing session to resume on first run |
| `DATA_DIR` | no | Where `sessions.json` / `settings.json` are stored (defaults to the project folder) |
| `MORNING_TIME` | no | Default `09:00` |
| `TIMEZONE` | no | Default `Europe/London` |
| `MORNING_GREETING` | no | Line added before the morning plan. Default `Sivayanama` |

**Finding your Telegram user ID:** start the bot with any value in `ALLOWED_USER_ID` and message it. It will reply with your real ID. Put that number in `.env` and restart the bot.

### 4. Run

```bash
python bot.py                    # Telegram bot
python main.py                   # interactive terminal chat
python once.py "What's on today?"  # send one message and print the reply
```

## Deploying to Railway

The repo includes a `railway.json` that runs `python bot.py` and restarts it automatically.

1. Create a new Railway project from this GitHub repo.
2. Add the environment variables above in the service's **Variables** tab.
3. Attach a **Volume** (e.g. mounted at `/data`) and set `DATA_DIR=/data`. This keeps the session ID and settings across redeploys.

Run only **one** copy of the bot at a time. Telegram polling doesn't work with more than one instance.

## Project structure

```
bot.py           Telegram bot + morning scheduler
main.py          Interactive terminal chat with the agent
once.py          One-shot client: one message, streamed reply
railway.json     Railway deploy config
.env.example     Template for secrets
```

## Security

`.env`, session IDs and local state files are listed in `.gitignore`. Never commit your API key or bot token.
