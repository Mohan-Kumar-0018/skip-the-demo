from __future__ import annotations

from typing import Any

from agent_runner import run_agent_loop
from tools.browser_tools import (
    click_by_text,
    click_element,
    get_page_content,
    list_interactive_elements,
    navigate_to_url,
    press_key,
    scroll_page,
    start_recording,
    stop_recording,
    take_screenshot,
    type_text,
    wait_seconds,
)

SYSTEM_PROMPT = """You are an autonomous browser exploration agent. Your job is to systematically discover and document every functionality on a given web page by interacting with all UI elements and capturing screenshots of each distinct state.

## Exploration Protocol

When given a URL, job_id, and optionally a target section to focus on:

### Phase 0: Navigation & Login (NO recording, NO screenshots)
IMPORTANT: Complete ALL of Phase 0 before taking ANY screenshots or calling start_recording.
Each step below must be its own separate turn — do NOT combine steps into parallel tool calls.

1. Call navigate_to_url with the URL and job_id. Wait for the result before proceeding.
2. If login credentials are provided, complete the ENTIRE login flow:
   - Use list_interactive_elements and get_page_content to understand the login page
   - Enter phone/email using type_text, click continue/submit buttons
   - Wait for OTP/password screen, enter the code/password, click verify/submit
   - Wait for the dashboard/home page to fully load (use wait_seconds 3-5)
   - Do NOT take any screenshots or call start_recording during login
3. If a target section/page is specified, navigate to it (click on the matching navigation item, tab, or menu entry). Wait for it to load.
4. NOW call start_recording. This creates a clean video starting from the current page — login is excluded.

### Phase 1: Initial Survey (recording is now active)
5. Take a screenshot of the current page state (the landing page or target section).
6. Call list_interactive_elements to get a full inventory of all UI elements.
7. Call get_page_content to understand the page context and data displayed.
8. Mentally categorize the discovered elements into these groups:
   - NAVIGATION: tabs, sidebar links, bottom nav items, breadcrumbs
   - ACTIONS: buttons that trigger operations (add, edit, delete, export, etc.)
   - FILTERS: filter buttons, dropdowns, date pickers that narrow displayed data
   - SEARCH: search bars, search icons
   - PAGINATION: next/previous buttons, page numbers, "load more"
   - DATA ITEMS: list items, table rows, cards that can be clicked for detail views
   - TOGGLES: switches, checkboxes, radio buttons that change state
   - MODALS/POPUPS: elements that open overlays (confirmed by trying them)
   - SORT: column headers or sort controls

### Phase 2: Efficient Exploration
BE EFFICIENT — aim for 1-2 screenshots per screen, ~15-20 turns total. Do NOT exhaustively explore every interactive element.

**A. Navigate Each Main Screen/Tab**
- For each navigation tab or sidebar link:
  1. Click the tab/link
  2. Take exactly ONE screenshot of the view
  3. Move on to the next tab

**B. One Detail View Per Screen**
- On each main screen, click ONE representative data item (first list item, card, or table row):
  1. Take ONE screenshot of the detail view
  2. Navigate back to the list

**C. One Action Button**
- Click ONE primary action button (Add, Create, etc.) if present:
  1. Take ONE screenshot of the form/modal/dialog
  2. DISMISS without submitting (click Cancel, X, or press Escape)
  3. Do NOT create, delete, or modify any data

**SKIP the following** unless directly relevant to the target section:
- Exhaustive filter/sort/pagination exploration
- Search functionality testing
- Scrolling for below-the-fold content
- Icon button enumeration

### Phase 3: Completion
9. After systematically exploring all discovered functionalities, call stop_recording.
10. Provide a structured summary of everything discovered.

## Rules
- STRICTLY ONE screenshot per distinct visual state. Before taking a screenshot, check if you already captured this same view. NEVER take two screenshots of the same page — if you navigated back to a page you already screenshotted, do NOT screenshot it again.
- ALWAYS wait for content to load before taking a screenshot (use wait_seconds 2-3 seconds after clicks).
- NEVER submit forms, create records, or delete data. Only OPEN forms to capture their UI, then cancel.
- If clicking an element causes an error or unexpected navigation, use the browser back or navigate back to recover.
- If an element is not found by CSS selector, try click_by_text with the element's visible text. For icon buttons, use their aria-label selector shown by list_interactive_elements.
- Use scroll_page to reveal content hidden below the viewport.
- Use press_key for keyboard interactions (Escape to dismiss modals, Enter to submit search, Tab to navigate).
- To clear a text input, use type_text with an empty string "" — NEVER press Backspace repeatedly.
- Keep track of what you have already explored and screenshotted to avoid revisiting the same states.
- Be efficient with turns — aim to finish in 15-25 turns. Do NOT exhaustively explore every element.
- Prioritize breadth (visiting all main screens) over depth (exploring every button on one screen)."""

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
        "description": "Start video recording. Call this AFTER login and navigation to the target page. Creates a clean recording context — login screens are excluded from the video.",
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
    {
        "name": "scroll_page",
        "description": "Scroll the page up or down by a specified number of pixels. Use to reveal content below the fold or return to the top.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction: 'up' or 'down'",
                },
                "amount": {
                    "type": "integer",
                    "description": "Number of pixels to scroll (e.g. 500 for half a screen, 1000 for a full screen)",
                },
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["direction", "amount", "job_id"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key. Common keys: Enter, Escape, Tab, Backspace, ArrowDown, ArrowUp. Use Escape to dismiss modals/popups, Enter to submit search queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key to press (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown')",
                },
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["key", "job_id"],
        },
    },
    {
        "name": "click_by_text",
        "description": "Click an element by its visible text. More reliable than CSS selectors for dynamic apps (especially Flutter). Falls back to role-based matching if text match fails.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The visible text of the element to click",
                },
                "exact": {
                    "type": "boolean",
                    "description": "If true, match the text exactly. If false (default), match substring.",
                },
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["text", "job_id"],
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
    elif name == "scroll_page":
        return await scroll_page(input["direction"], input["amount"], input["job_id"])
    elif name == "press_key":
        return await press_key(input["key"], input["job_id"])
    elif name == "click_by_text":
        return await click_by_text(input["text"], input["job_id"], input.get("exact", False))
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
        "action_log": [],
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
            collected["action_log"] = result.get("action_log", [])
        return result

    result = await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_collecting_executor,
        user_message=task,
        max_turns=30,
    )
    return {"summary": result["text"], "data": collected, "usage": result["usage"]}
