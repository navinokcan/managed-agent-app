"""Interactive chat with a Claude Managed Agent.

Keeps one session across turns and across runs (the session ID is saved in
.session_id), so the agent remembers earlier conversation.

Commands:  /new  start a fresh session   /quit  exit (Ctrl-D also works)
Ctrl-C while the agent is replying interrupts it.
"""

import sys
from pathlib import Path

import anthropic

AGENT_ID = "agent_01VYB2Y6QgdMXvC63Edf6w46"
ENVIRONMENT_ID = "env_01QYwQfrzNwHYZqiLxNvZDmj"
SESSION_FILE = Path(__file__).with_name(".session_id")


def log(msg: str) -> None:
    print(f"[{msg}]", file=sys.stderr)


def get_session_id(client: anthropic.Anthropic, fresh: bool = False) -> str:
    """Resume the saved session if it's still usable, otherwise create one."""
    if not fresh and SESSION_FILE.exists():
        sid = SESSION_FILE.read_text().strip()
        try:
            s = client.beta.sessions.retrieve(sid)
            if s.status != "terminated" and s.archived_at is None:
                log(f"resumed session {sid}")
                return sid
        except anthropic.NotFoundError:
            pass
    s = client.beta.sessions.create(agent=AGENT_ID, environment_id=ENVIRONMENT_ID)
    SESSION_FILE.write_text(s.id)
    log(f"new session {s.id}")
    return s.id


def interrupt(client: anthropic.Anthropic, sid: str) -> None:
    client.beta.sessions.events.send(session_id=sid, events=[{"type": "user.interrupt"}])


def ask_yes_no(question: str) -> bool:
    try:
        return input(f"\n{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_turn(client: anthropic.Anthropic, sid: str, text: str) -> bool:
    """Send one message and stream the reply. Returns False if the session terminated."""
    started = False
    try:
        # Stream-first: open the stream, then send, so no events are missed.
        with client.beta.sessions.events.stream(session_id=sid) as stream:
            client.beta.sessions.events.send(
                session_id=sid,
                events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
            )
            for event in stream:
                if event.type == "agent.message":
                    if not started:
                        print("\nagent> ", end="")
                        started = True
                    for block in event.content:
                        if block.type == "text":
                            print(block.text, end="", flush=True)

                elif event.type in ("agent.tool_use", "agent.mcp_tool_use") and getattr(
                    event, "evaluated_permission", None
                ) == "ask":
                    allow = ask_yes_no(f"Agent wants to run tool '{event.name}'. Allow?")
                    client.beta.sessions.events.send(
                        session_id=sid,
                        events=[{
                            "type": "user.tool_confirmation",
                            "tool_use_id": event.id,
                            "result": "allow" if allow else "deny",
                        }],
                    )

                elif event.type == "agent.custom_tool_use":
                    # This app has no custom tool handlers; stop the agent instead of hanging.
                    log(f"agent called custom tool '{event.name}', which this app can't run; interrupting")
                    interrupt(client, sid)

                elif event.type == "session.error":
                    log(f"session error: {getattr(event, 'error', event)}")

                elif event.type == "session.status_idle":
                    reason = event.stop_reason.type
                    if reason == "requires_action":
                        continue  # waiting on a confirmation we've sent / are sending
                    print()
                    if reason != "end_turn":
                        log(f"agent stopped: {reason}")
                    return True

                elif event.type == "session.status_terminated":
                    print()
                    log("session terminated; a new one will start on your next message")
                    return False
    except KeyboardInterrupt:
        interrupt(client, sid)
        print()
        log("interrupted")
    return True


def main() -> int:
    client = anthropic.Anthropic()
    try:
        sid = get_session_id(client)
        print("Chat with your agent. /new = fresh session, /quit = exit.\n")
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not text:
                continue
            if text in ("/quit", "/exit"):
                return 0
            if text == "/new":
                sid = get_session_id(client, fresh=True)
                continue
            if not run_turn(client, sid, text):
                sid = get_session_id(client, fresh=True)
            print()
    except anthropic.AuthenticationError:
        print("Authentication failed: set ANTHROPIC_API_KEY or run `ant auth login`.", file=sys.stderr)
    except anthropic.NotFoundError as e:
        print(f"Not found (check agent/environment IDs): {e.message}", file=sys.stderr)
    except anthropic.RateLimitError as e:
        print(f"Rate limited: {e.message}", file=sys.stderr)
    except anthropic.APIStatusError as e:
        print(f"API error {e.status_code}: {e.message}", file=sys.stderr)
    except anthropic.APIConnectionError as e:
        print(f"Network error: {e}", file=sys.stderr)
    except TypeError as e:
        if "authentication" not in str(e):
            raise
        print("No API credentials found: export ANTHROPIC_API_KEY=... first.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
