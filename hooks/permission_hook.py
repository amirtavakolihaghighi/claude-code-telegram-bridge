"""Claude Code runs this whenever it needs permission during a Telegram turn.

It forwards the question to the bridge, which asks you on Telegram, and prints
back the answer. Standard library only - it runs as its own little process.

If the bridge is not reachable, it prints nothing, which leaves Claude Code to
behave exactly as it normally would.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REPLY_TIMEOUT = 360     # seconds; longer than the bridge's own wait


def answer(decision: dict) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }))


def main() -> int:
    url = os.environ.get("BRIDGE_APPROVAL_URL")
    token = os.environ.get("BRIDGE_APPROVAL_TOKEN")
    if not url or not token:
        return 0        # not a Telegram-driven run; stay out of the way

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}

    if os.environ.get("BRIDGE_HOOK_DEBUG"):
        try:
            with open(os.environ["BRIDGE_HOOK_DEBUG"], "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload)[:4000] + "\n")
        except OSError:
            pass

    question = json.dumps({
        "session_id": payload.get("session_id") or payload.get("sessionId") or "",
        "tool_name": payload.get("tool_name") or payload.get("toolName") or "a tool",
        "tool_input": payload.get("tool_input") or payload.get("toolInput") or {},
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=question, method="POST",
        headers={"Content-Type": "application/json", "X-Bridge-Token": token},
    )

    try:
        with urllib.request.urlopen(request, timeout=REPLY_TIMEOUT) as response:
            decision = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        answer({"behavior": "deny", "message": f"could not reach the bridge: {exc}"})
        return 0

    if not isinstance(decision, dict) or "behavior" not in decision:
        answer({"behavior": "deny", "message": "the bridge gave no clear answer"})
        return 0

    answer(decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
