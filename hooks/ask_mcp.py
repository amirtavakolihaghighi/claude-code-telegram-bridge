"""A tool Claude can call to ask you something, answered with Telegram buttons.

Claude Code has no way to ask a question when nobody is at the keyboard: its own
question tool is unavailable outside an interactive session. So the bridge hands
it one.

This is a minimal MCP server. Claude Code starts it, asks what tools it offers,
and can then call `ask_user`. The call is forwarded to the running bridge, which
posts the question into the Telegram topic and waits for you to tap an answer.
Whatever you choose comes back as the tool's result, and Claude carries on.

Standard library only: it runs as its own small process, spoken to over stdin
and stdout in line-delimited JSON-RPC.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

FALLBACK_PROTOCOL = "2024-11-05"


def reply_timeout() -> float:
    """Outlive the bridge's own wait by a margin.

    If both sides gave up at the same moment they would race, and the caller
    would see a bare connection timeout instead of the bridge's explanation that
    nobody answered.
    """
    try:
        return float(os.environ.get("BRIDGE_QUESTION_WAIT", "300")) + 60
    except ValueError:
        return 360.0

TOOL = {
    "name": "ask_user",
    "description": (
        "Ask the user a question and wait for their answer. Use this whenever "
        "you would otherwise have to guess, or would normally stop and ask: "
        "choosing between approaches, confirming a destructive step, or "
        "resolving an ambiguous request. The user answers by tapping a button "
        "on their phone, so give clear, short options. Returns the option or "
        "options they chose. Prefer this over asking in prose, which may not be "
        "seen for hours."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question, in one or two plain sentences.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Two to ten short answers to choose between.",
            },
            "allow_multiple": {
                "type": "boolean",
                "description": "True if several options may be chosen at once.",
            },
        },
        "required": ["question", "options"],
    },
}


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def reply(request_id, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id, code: int, text: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id,
          "error": {"code": code, "message": text}})


def text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def ask_the_bridge(arguments: dict) -> dict:
    url = os.environ.get("BRIDGE_QUESTION_URL")
    token = os.environ.get("BRIDGE_APPROVAL_TOKEN")
    if not url or not token:
        return text_result("The Telegram bridge is not reachable, so the user "
                           "cannot be asked. Decide without them, or ask in "
                           "your reply instead.", is_error=True)

    question = str(arguments.get("question") or "").strip()
    options = [str(option).strip() for option in arguments.get("options") or []
               if str(option).strip()]
    if not question or len(options) < 2:
        return text_result("A question and at least two options are needed.",
                           is_error=True)

    payload = json.dumps({
        "session_id": os.environ.get("BRIDGE_SESSION_ID", ""),
        "question": question,
        "options": options[:10],
        "allow_multiple": bool(arguments.get("allow_multiple")),
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Bridge-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=reply_timeout()) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return text_result(f"Could not reach the user: {exc}", is_error=True)

    chosen = answer.get("chosen") or []
    if not chosen:
        return text_result(
            f"The user did not answer: {answer.get('reason', 'no reason given')}. "
            f"Carry on without their input, and say that you did.",
            is_error=False)
    if len(chosen) == 1:
        return text_result(f"The user chose: {chosen[0]}")
    return text_result("The user chose: " + "; ".join(chosen))


def handle(message: dict) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": asked or FALLBACK_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "telegram-bridge", "version": "1.0.0"}}

    if method in ("tools/list", "tools/list_changed"):
        return {"tools": [TOOL]}

    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != TOOL["name"]:
            return text_result(f"No such tool: {params.get('name')}",
                               is_error=True)
        return ask_the_bridge(params.get("arguments") or {})

    if method in ("ping",):
        return {}

    if request_id is None:
        return None                     # a notification; nothing to answer
    raise LookupError(method or "unknown method")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        request_id = message.get("id")
        try:
            result = handle(message)
        except LookupError as exc:
            if request_id is not None:
                fail(request_id, -32601, f"Method not found: {exc}")
            continue
        except Exception as exc:                        # never take the CLI down
            if request_id is not None:
                fail(request_id, -32603, str(exc))
            continue

        if result is not None and request_id is not None:
            reply(request_id, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
