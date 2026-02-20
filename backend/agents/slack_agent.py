from __future__ import annotations

import os

from agent_runner import run_agent_loop
from tools.slack_tools import post_slack_message, read_slack_messages, upload_slack_file

DEFAULT_CHANNEL = os.getenv("SLACK_CHANNEL", "#skipdemo-pm")

SYSTEM_PROMPT = f"""You are a Slack agent. You can read and post messages to Slack channels, and upload files.

Default channel: {DEFAULT_CHANNEL}

When posting PM briefings, format messages clearly with:
- Feature name and design accuracy score with emoji indicators (green >= 90, yellow >= 75, red < 75)
- Deviations from design
- Summary and release notes
- Note about video in thread

After posting a message, if there's a video file to upload, upload it to the thread."""

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


async def run_slack_agent(task: str) -> str:
    """Run the Slack agent with the given task description."""
    return await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_execute_tool,
        user_message=task,
    )
