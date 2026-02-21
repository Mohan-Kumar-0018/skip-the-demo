from __future__ import annotations

import os
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_client: WebClient | None = None


def _get_client() -> WebClient:
    global _client
    if _client is None:
        _client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
    return _client


def post_slack_message(channel: str, text: str) -> dict[str, str]:
    """Post a message to a Slack channel. Returns {ok, ts, channel}."""
    client = _get_client()
    try:
        res = client.chat_postMessage(channel=channel, text=text)
        return {"ok": str(res["ok"]), "ts": res["ts"], "channel": res["channel"]}
    except SlackApiError as e:
        return {"ok": "False", "error": str(e.response["error"])}


def read_slack_messages(channel: str, limit: int = 10) -> list[dict[str, str]]:
    """Read recent messages from a Slack channel. Returns list of {user, text, ts}."""
    client = _get_client()
    try:
        res = client.conversations_history(channel=channel, limit=limit)
        return [
            {"user": m.get("user", "bot"), "text": m.get("text", ""), "ts": m["ts"]}
            for m in res.get("messages", [])
        ]
    except SlackApiError as e:
        return [{"error": str(e.response["error"])}]


def upload_slack_file(
    channel: str, file_path: str, title: str, thread_ts: str | None = None
) -> dict[str, str]:
    """Upload a file to a Slack channel, optionally in a thread."""
    if not os.path.isfile(file_path):
        return {"ok": "False", "error": f"File not found: {file_path}"}
    client = _get_client()
    kwargs: dict[str, Any] = {
        "channel": channel,
        "file": file_path,
        "title": title,
    }
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    try:
        res = client.files_upload_v2(**kwargs)
        return {"ok": str(res["ok"])}
    except SlackApiError as e:
        return {"ok": "False", "error": str(e.response["error"])}
