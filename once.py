"""Minimal one-shot client for a Claude Managed Agent: one session, one message, streamed reply."""

import sys

import anthropic

AGENT_ID = "agent_01VYB2Y6QgdMXvC63Edf6w46"
ENVIRONMENT_ID = "env_01QYwQfrzNwHYZqiLxNvZDmj"


def run(client: anthropic.Anthropic, prompt: str) -> int:
    session = client.beta.sessions.create(agent=AGENT_ID, environment_id=ENVIRONMENT_ID)
    print(f"[session {session.id}]", file=sys.stderr)

    had_error = False
    # Stream-first: open the stream before sending so no early events are missed.
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, end="", flush=True)
            elif event.type == "session.error":
                had_error = True  # errors arrive as events; the session then goes idle
                print(f"\n[session.error] {getattr(event, 'error', event)}", file=sys.stderr)
            elif event.type == "session.status_idle":
                reason = event.stop_reason.type
                print()
                if reason == "requires_action":
                    print("[agent is waiting on a tool approval/result this app doesn't handle]", file=sys.stderr)
                    return 1
                if reason != "end_turn":
                    print(f"[session idle: {reason}]", file=sys.stderr)
                    return 1
                return 1 if had_error else 0
            elif event.type == "session.status_terminated":
                print("\n[session terminated]", file=sys.stderr)
                return 1

    print("\n[stream closed before the session went idle]", file=sys.stderr)
    return 1


def main() -> int:
    prompt = " ".join(sys.argv[1:]) or "Hello! Briefly introduce yourself."
    try:
        return run(anthropic.Anthropic(), prompt)
    except KeyboardInterrupt:
        print("\n[interrupted]", file=sys.stderr)
        return 130
    except anthropic.AuthenticationError:
        print("Authentication failed: check ANTHROPIC_API_KEY.", file=sys.stderr)
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
