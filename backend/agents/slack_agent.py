from __future__ import annotations

import os

from agent_runner import run_agent_loop
from tools.slack_tools import post_slack_message, read_slack_messages, upload_slack_file

DEFAULT_CHANNEL = os.getenv("SLACK_CHANNEL", "#skip-the-demo")

SYSTEM_PROMPT = f"""You are a Slack agent. You can read and post messages to Slack channels, and upload files.

IMPORTANT: Always post to this exact channel: {DEFAULT_CHANNEL}
Do NOT guess or invent channel names. Use "{DEFAULT_CHANNEL}" for every tool call.

When posting PM briefings:
1. Post the briefing message to the channel.
2. Check the result — if "ok" is "False", report the error and stop. Do NOT retry.
3. If a video file path is provided, upload it to the THREAD of the message you just posted
   (use the "ts" value from the post result as thread_ts).
4. If the upload returns an error (e.g. file not found), report it but consider the briefing delivered.

Formatting guidelines:
- Use score emojis: 🟢 for score >= 80, 🟡 for score >= 60, 🔴 for score < 60
- Keep the message scannable — feature name, score, and key deviations up top
- Put full release notes in a separate thread reply if they exceed 10 lines"""

TOOLS = [
    {
        "name": "post_slack_message",
        "description": "Post a message to a Slack channel. Returns {ok, ts, channel}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "The Slack channel to post to"},
                "text": {"type": "string", "description": "The message text (supports Slack markdown)"},
            },
            "required": ["channel", "text"],
        },
    },
    {
        "name": "read_slack_messages",
        "description": "Read recent messages from a Slack channel. Returns list of {user, text, ts}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "The Slack channel to read from"},
                "limit": {"type": "integer", "description": "Number of recent messages to fetch (default 10)"},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "upload_slack_file",
        "description": "Upload a file to a Slack channel, optionally in a thread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "The Slack channel"},
                "file_path": {"type": "string", "description": "Path to the file to upload"},
                "title": {"type": "string", "description": "Title for the uploaded file"},
                "thread_ts": {"type": "string", "description": "Thread timestamp to upload into (optional)"},
            },
            "required": ["channel", "file_path", "title"],
        },
    },
]


async def _execute_tool(name: str, input: dict) -> str | dict | list:
    if name == "post_slack_message":
        return post_slack_message(input["channel"], input["text"])
    elif name == "read_slack_messages":
        return read_slack_messages(input["channel"], input.get("limit", 10))
    elif name == "upload_slack_file":
        return upload_slack_file(
            input["channel"],
            input["file_path"],
            input["title"],
            input.get("thread_ts"),
        )
    else:
        return {"error": f"Unknown tool: {name}"}


async def run_slack_agent(task: str) -> dict:
    """Run the Slack agent with the given task description. Returns {text, usage}."""
    return await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_execute_tool,
        user_message=task,
    )
