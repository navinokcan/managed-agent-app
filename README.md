# Scheduler Agent Telegram Bot

A private Telegram bot for a personal scheduling assistant built on a [Claude Managed Agent](https://docs.anthropic.com/). I talk to the agent from my phone, and every morning it sends me a plan for the day based on the tracker it keeps in its session.

## Features

- **Chat from Telegram.** Messages go to your Managed Agent. The agent reuses the same session across restarts, so it keeps its memory and tracker.
- **Daily morning plan.** At the time I choose, the bot sends my time blocks, my top 1–3 priorities and any deadlines due in the next 3 days.
- **Tool approvals.** When the agent wants to run a tool that needs permission, I get **Allow** / **Deny** buttons.
- **Owner only.** Anyone other than the user in `ALLOWED_USER_ID` is turned away.
- **Two terminal clients:** `main.py` is an interactive chat and `once.py` sends a single message.

## Commands

| Command | What it does |
|---|---|
| `/today` | Send today's plan now |
| `/morning HH:MM` | Set the morning plan time (24-hour), or `/morning off` to stop it |
| `/tz Zone` | Set my time zone, e.g. `/tz Asia/Singapore` |
| `/settings` | Show the current time, time zone and agent session |
| `/new` | Start a blank agent session (**this loses the tracker**) |

Any other text I send is passed to the agent.
