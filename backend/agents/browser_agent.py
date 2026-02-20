from __future__ import annotations

from agent_runner import run_agent_loop
from tools.browser_tools import (
    click_element,
    get_page_content,
    list_interactive_elements,
    navigate_to_url,
    start_recording,
    stop_recording,
    take_screenshot,
)

SYSTEM_PROMPT = """You are a browser agent. Your job is to explore a web application thoroughly.

When given a URL and job_id:
1. Navigate to the URL (this also starts video recording automatically)
2. Take a screenshot of the initial page
3. List interactive elements to understand what's available
4. Click through important UI flows — buttons, navigation links, tabs, forms
5. Take screenshots at each significant step
6. When done exploring, stop the recording

Be systematic. Explore the main flows a PM would care about. Describe what you see at each step.
After exploring, provide a clear summary of what the app does and what flows you recorded."""

TOOLS = [
    {
        "name": "navigate_to_url",
        "description": "Open a URL in the browser. Creates a new session if needed. Returns page title and description.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to"},
                "job_id": {"type": "string", "description": "The job ID for session management"},
            },
            "required": ["url", "job_id"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Capture a full-page screenshot of the current page. Returns the file path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "click_element",
        "description": "Click an element by CSS selector. Returns new page state after click.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector or Playwright selector for the element to click"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["selector", "job_id"],
        },
    },
    {
        "name": "list_interactive_elements",
        "description": "List all clickable elements on the current page with their selectors and text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "get_page_content",
        "description": "Get the visible text content of the current page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "start_recording",
        "description": "Acknowledge that video recording is active (it starts automatically with the browser session).",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "stop_recording",
        "description": "Stop recording and close the browser session. Returns the video file path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["job_id"],
        },
    },
]


async def _execute_tool(name: str, input: dict) -> str | dict | list:
    if name == "navigate_to_url":
        return await navigate_to_url(input["url"], input["job_id"])
    elif name == "take_screenshot":
        return await take_screenshot(input["job_id"])
    elif name == "click_element":
        return await click_element(input["selector"], input["job_id"])
    elif name == "list_interactive_elements":
        return await list_interactive_elements(input["job_id"])
    elif name == "get_page_content":
        return await get_page_content(input["job_id"])
    elif name == "start_recording":
        return await start_recording(input["job_id"])
    elif name == "stop_recording":
        return await stop_recording(input["job_id"])
    else:
        return {"error": f"Unknown tool: {name}"}


async def run_browser_agent(task: str) -> str:
    """Run the browser agent with the given task description."""
    return await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_execute_tool,
        user_message=task,
    )
