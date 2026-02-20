from __future__ import annotations

from agent_runner import run_agent_loop
from tools.jira_tools import (
    add_jira_comment,
    get_jira_attachments,
    get_jira_comments,
    get_jira_subtasks,
    get_jira_ticket,
)

SYSTEM_PROMPT = """You are a Jira agent. Given a task, decide what information to fetch from Jira — ticket details, subtasks, attachments, comments — and summarize what you find.

When fetching attachments, use the output_dir provided in the task. Categorize what you download (PRD documents, design files, etc).

Always return a clear, structured summary of what you found."""

TOOLS = [
    {
        "name": "get_jira_ticket",
        "description": "Fetch ticket details from Jira. Returns title, description, staging_url, status, assignee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The Jira ticket ID (e.g. PROJ-123)"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "get_jira_subtasks",
        "description": "Fetch subtasks for a Jira ticket. Returns list of subtask summaries with key, summary, status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The Jira ticket ID"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "get_jira_attachments",
        "description": "Download all attachments from a Jira ticket. Saves files to output_dir. Returns list of {filename, path, mime_type, category}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The Jira ticket ID"},
                "output_dir": {"type": "string", "description": "Directory to save downloaded attachments"},
            },
            "required": ["ticket_id", "output_dir"],
        },
    },
    {
        "name": "get_jira_comments",
        "description": "Fetch all comments on a Jira ticket. Returns list of {author, body, created}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The Jira ticket ID"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "add_jira_comment",
        "description": "Post a comment on a Jira ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The Jira ticket ID"},
                "text": {"type": "string", "description": "The comment text to post"},
            },
            "required": ["ticket_id", "text"],
        },
    },
]


async def _execute_tool(name: str, input: dict) -> str | dict | list:
    if name == "get_jira_ticket":
        return get_jira_ticket(input["ticket_id"])
    elif name == "get_jira_subtasks":
        return get_jira_subtasks(input["ticket_id"])
    elif name == "get_jira_attachments":
        return get_jira_attachments(input["ticket_id"], input["output_dir"])
    elif name == "get_jira_comments":
        return get_jira_comments(input["ticket_id"])
    elif name == "add_jira_comment":
        return add_jira_comment(input["ticket_id"], input["text"])
    else:
        return {"error": f"Unknown tool: {name}"}


async def run_jira_agent(task: str) -> str:
    """Run the Jira agent with the given task description."""
    return await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_execute_tool,
        user_message=task,
    )
