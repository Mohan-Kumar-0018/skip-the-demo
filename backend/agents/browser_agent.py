from __future__ import annotations

from typing import Any

from agent_runner import run_agent_loop
from tools.browser_tools import (
    click_element,
    get_page_content,
    list_interactive_elements,
    navigate_to_url,
    start_recording,
    stop_recording,
    take_screenshot,
    type_text,
    wait_seconds,
)

SYSTEM_PROMPT = """You are a browser agent. Your job is to explore a web application thoroughly.

When given a URL and job_id:
1. Navigate to the URL (this also starts video recording automatically)
2. If the task does NOT say to skip certain pages: take a screenshot of the initial page. If the task says to skip login or take screenshots only on specific sections (e.g. supplier pages), do NOT take any screenshots until you have reached those sections.
3. List interactive elements to understand what's available
4. Click through the flows the task asks for (e.g. login without capturing, then go to supplier section)
5. Take screenshots only when on the pages the task allows (e.g. only on supplier pages). Do not take_screenshot on login or other excluded pages.
6. When done exploring, stop the recording

If the task says "skip the login page" or "screenshots only on supplier pages" (or similar), you must: complete login without calling take_screenshot; enter the OTP/password when asked (e.g. 6666); then open the Suppliers section and capture exactly one screenshot per distinct view:

CRITICAL — One screenshot per screen only. Do NOT take multiple screenshots of the same page. Navigate to a new view, wait for it to load, then take exactly one screenshot. Each screenshot must show a different screen.

Supplier section — capture these 4 distinct views (one screenshot each):
1. **Main suppliers list** — On the suppliers list screen, take exactly one screenshot. Then leave this view.
2. **Starred suppliers** — Open the starred/favourites view (tab or "Starred" / "Favourites"). Wait for it to load. Take exactly one screenshot. Then go back or navigate away.
3. **Supplier detail** — From the list, click one supplier to open its detail page. Wait for the detail page to load. Take exactly one screenshot. Then go back to the list.
4. **Filter popup (place)** — Open the filter UI (Filter button/icon), then open or select the "Place" (location) filter so the popup with place options is visible. Take exactly one screenshot of the open filter popup.

After you have exactly four different screens (list, starred, detail, filter popup), call stop_recording. Do not take extra screenshots of the same or nearly same view."""

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
        "description": "Capture one full-page screenshot of the current view. Only call after navigating to a new, distinct screen (e.g. new tab, detail page, or open popup). Do not call again until the view has changed — one screenshot per distinct view.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into an input field identified by CSS selector. Use this to fill in forms, search boxes, login fields, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input field"},
                "text": {"type": "string", "description": "The text to type into the field"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["selector", "text", "job_id"],
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
        "name": "wait_seconds",
        "description": "Wait for a few seconds (e.g. 2–3) to let the page load. Use after clicking Continue/Next so the OTP screen appears before typing the code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Seconds to wait (1–10)"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["seconds", "job_id"],
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
    elif name == "type_text":
        return await type_text(input["selector"], input["text"], input["job_id"])
    elif name == "click_element":
        return await click_element(input["selector"], input["job_id"])
    elif name == "list_interactive_elements":
        return await list_interactive_elements(input["job_id"])
    elif name == "wait_seconds":
        return await wait_seconds(input["seconds"], input["job_id"])
    elif name == "get_page_content":
        return await get_page_content(input["job_id"])
    elif name == "start_recording":
        return await start_recording(input["job_id"])
    elif name == "stop_recording":
        return await stop_recording(input["job_id"])
    else:
        return {"error": f"Unknown tool: {name}"}


async def run_browser_agent(task: str) -> dict[str, Any]:
    """Run the browser agent. Returns {summary: str, data: dict} with collected structured data."""
    collected: dict[str, Any] = {
        "urls_visited": [],
        "page_titles": [],
        "screenshot_paths": [],
        "video_path": None,
        "page_content": "",
        "interactive_elements": [],
    }

    async def _collecting_executor(name: str, input: dict) -> str | dict | list:
        result = await _execute_tool(name, input)
        if name == "navigate_to_url" and isinstance(result, dict):
            collected["urls_visited"].append({
                "url": result.get("url", input.get("url", "")),
                "title": result.get("title", ""),
                "description": result.get("description", ""),
            })
            collected["page_titles"].append(result.get("title", ""))
        elif name == "take_screenshot" and isinstance(result, dict):
            collected["screenshot_paths"].append(result.get("path", ""))
        elif name == "click_element" and isinstance(result, dict):
            if result.get("status") == "ok":
                collected["page_titles"].append(result.get("title", ""))
        elif name == "get_page_content" and isinstance(result, dict):
            collected["page_content"] = result.get("text", "")
        elif name == "list_interactive_elements" and isinstance(result, list):
            collected["interactive_elements"] = result
        elif name == "stop_recording" and isinstance(result, dict):
            collected["video_path"] = result.get("video_path")
        return result

    summary = await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_collecting_executor,
        user_message=task,
        max_turns=60,
    )
    return {"summary": summary, "data": collected}
