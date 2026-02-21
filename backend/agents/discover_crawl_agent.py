from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any

import anthropic
from PIL import Image

from agent_runner import calc_cost, run_agent_loop
from agents.navigation_planner_agent import plan_navigation
from tools.browser_tools import (
    click_by_text,
    click_element,
    get_page_content,
    list_interactive_elements,
    navigate_to_url,
    press_key,
    scroll_page,
    stop_recording,
    take_screenshot,
    type_text,
    wait_seconds,
)
from tools.kb_tools import get_knowledge

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)

# ── Phase 1: Deterministic Login ──────────────────────────────

def _is_logged_in(page_content: dict | str) -> bool:
    """Check if page content indicates we're already logged in (not on a login page).

    Returns False if:
    - Page has login indicators (login form present)
    - Page has very little content (still loading/splash screen)
    """
    text = page_content.get("text", "") if isinstance(page_content, dict) else str(page_content)
    text_lower = text.lower().strip()

    # If page has very little content, it's likely still loading — not logged in
    if len(text_lower) < 50:
        return False

    login_indicators = ["log in", "login", "sign in", "get otp", "enter your phone",
                        "enter your email", "phone number", "password", "forgot password"]
    return not any(indicator in text_lower for indicator in login_indicators)


async def _login_and_capture_home(
    job_id: str, url: str, creds: dict
) -> dict[str, Any]:
    """Phase 1: Deterministic login — no Claude calls. Calls browser tools directly."""
    logger.info("Navigating to %s", url)
    await navigate_to_url(url, job_id)
    await wait_seconds(5, job_id)  # Wait for SPA/Flutter apps to fully load

    # Check if already logged in
    login_verified = True
    page_content = await get_page_content(job_id)
    if _is_logged_in(page_content):
        logger.info("Already logged in, skipping login flow")
    else:
        phone = creds.get("phone")
        email = creds.get("email")
        password = creds.get("password", "")

        if phone:
            # Phone + OTP/password login flow
            phone_str = str(phone)
            local_phone = phone_str[3:] if phone_str.startswith("966") else phone_str

            logger.info("Entering phone number: %s", local_phone)
            # Click the phone label to focus the field, then type
            await click_by_text("Phone Number", job_id)
            await wait_seconds(1, job_id)
            await type_text("input", local_phone, job_id)

            # Submit phone
            await click_by_text("Get OTP", job_id)
            await wait_seconds(3, job_id)

            # Enter OTP/password on second screen
            logger.info("Entering OTP/password")
            # Find the OTP input — scan elements for the right selector
            otp_typed = False
            elements = await list_interactive_elements(job_id)
            if isinstance(elements, list):
                for el in elements:
                    selector = el.get("selector", "") if isinstance(el, dict) else str(el)
                    sel_lower = selector.lower()
                    if "one-time-code" in sel_lower or "otp" in sel_lower or "verification" in sel_lower:
                        result = await type_text(selector, password, job_id)
                        if isinstance(result, dict) and result.get("status") != "error":
                            otp_typed = True
                            break
            if not otp_typed:
                # Fallback: type digits via keyboard (focus should be on OTP field)
                for digit in password:
                    await press_key(digit, job_id)

            # Submit OTP
            try:
                await click_by_text("Verify", job_id)
            except Exception:
                await click_by_text("Submit", job_id)

        elif email:
            # Email + password login flow
            logger.info("Entering email and password")
            await type_text("input[type='email']", str(email), job_id)
            await type_text("input[type='password']", password, job_id)
            try:
                await click_by_text("Log in", job_id)
            except Exception:
                try:
                    await click_by_text("Sign in", job_id)
                except Exception:
                    await click_by_text("Submit", job_id)

        # Wait for dashboard to load
        logger.info("Waiting for dashboard to load")
        await wait_seconds(5, job_id)

        # Verify login succeeded
        page_content = await get_page_content(job_id)
        login_verified = _is_logged_in(page_content)
        if not login_verified:
            logger.warning("Login may have failed — still on login page")

    # Take home page screenshot (session stays alive for Phase 3)
    home_screenshot = None
    screenshot_result = await take_screenshot(job_id)
    if isinstance(screenshot_result, dict):
        home_screenshot = screenshot_result.get("path")

    return {
        "summary": f"Logged in to {url} successfully",
        "home_screenshot": home_screenshot,
        "login_verified": login_verified,
        "usage": {},
    }


# ── Phase 2: Navigation Discovery ────────────────────────────

def _resize_if_needed(path: str) -> bytes:
    """Read an image and downscale if either dimension exceeds 8000px."""
    MAX_DIM = 8000
    with Image.open(path) as img:
        w, h = img.size
        if w > MAX_DIM or h > MAX_DIM:
            scale = min(MAX_DIM / w, MAX_DIM / h)
            new_size = (int(w * scale), int(h * scale))
            logger.info("Resizing %s from %dx%d to %dx%d", path, w, h, *new_size)
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


NAV_DISCOVER_PROMPT = """\
You are a navigation discovery agent. You analyze screenshots of a web application's home/dashboard page \
to identify all visible navigation elements that lead to different sections of the app.

Rules:
- Identify ALL navigation items: sidebar links, top nav tabs, bottom nav items, menu entries, section cards
- For each item, describe what to click (e.g. "sidebar link labeled 'Orders'", "tab labeled 'Analytics'")
- Order items in logical navigation order (main sections first)
- Include the visible text of each navigation element

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "flows": [
    {"name": "Section Name", "nav_element": "description of what to click", "nav_text": "visible text"}
  ],
  "summary": "One sentence describing the app's navigation structure"
}"""

NAV_VALIDATE_PROMPT = """\
You are a navigation validation agent. You are given:
1. A screenshot of a web application's home/dashboard page
2. A list of screens identified from Figma designs

Your job is to match Figma screens to actual navigation elements visible in the live UI screenshot. \
Only include screens that have a corresponding clickable navigation element in the UI.

Rules:
- For each Figma screen that has a matching nav element in the screenshot, include it
- Describe what to click to reach that screen (e.g. "sidebar link labeled 'Orders'")
- Skip Figma screens that are modals, confirmations, or sub-states (not directly navigable)
- Only include top-level navigable sections

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "flows": [
    {"name": "Screen Name", "nav_element": "description of what to click", "nav_text": "visible text"}
  ],
  "summary": "One sentence describing which Figma screens map to live nav elements"
}"""


def _call_claude_nav(
    model: str,
    system_prompt: str,
    content: list[dict[str, Any]],
) -> dict[str, Any]:
    """Call Claude for navigation discovery/validation and parse the JSON response."""
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    logger.info("Claude nav response (first 500 chars): %s", text[:500])
    clean = text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(clean)

    parsed["usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
    }
    return parsed


def _discover_navigation(
    home_screenshot_path: str | None,
    figma_images_dir: str | None = None,
) -> dict[str, Any]:
    """Phase 2: Discover navigation structure from home screenshot, optionally validated against Figma."""
    if not home_screenshot_path or not os.path.exists(home_screenshot_path):
        logger.warning(
            "Early return: screenshot path=%s exists=%s",
            home_screenshot_path,
            os.path.exists(home_screenshot_path) if home_screenshot_path else "N/A",
        )
        return {"flows": [], "summary": "No home screenshot available", "usage": {}}

    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    screenshot_bytes = _resize_if_needed(home_screenshot_path)
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

    # Base content: always include the home screenshot
    base_content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64},
        },
        {"type": "text", "text": "[Home page screenshot]"},
    ]

    discover_text = {"type": "text", "text": "\nIdentify all navigation elements visible in this home page screenshot."}

    # Determine mode: validate (Figma + screenshot) or discover (screenshot only)
    use_validate = False
    if figma_images_dir and os.path.isdir(figma_images_dir):
        figma_images = [
            {"path": os.path.join(figma_images_dir, f), "name": f}
            for f in sorted(os.listdir(figma_images_dir))
            if f.startswith("figma") and f.endswith(".png")
        ]
        if figma_images:
            nav_plan = plan_navigation(figma_images, "")
            screens = nav_plan.get("screens", [])
            if screens:
                use_validate = True
                logger.info("Using VALIDATE mode: %d Figma screens from plan_navigation", len(screens))
                screens_text = json.dumps(screens, indent=2)
                validate_content = base_content + [{
                    "type": "text",
                    "text": (
                        f"\nFigma design screens:\n{screens_text}\n\n"
                        "Match these Figma screens to navigation elements visible in the home page screenshot."
                    ),
                }]
            else:
                logger.warning("plan_navigation returned 0 screens, falling back to discover mode")
        else:
            logger.info("No figma*.png files in %s, using discover mode", figma_images_dir)
    else:
        logger.info("No figma_images_dir provided, using discover mode")

    if not use_validate:
        logger.info("Using DISCOVER mode: screenshot-only navigation discovery")

    # --- Attempt 1: validate or discover ---
    try:
        if use_validate:
            parsed = _call_claude_nav(model, NAV_VALIDATE_PROMPT, validate_content)
        else:
            parsed = _call_claude_nav(model, NAV_DISCOVER_PROMPT, base_content + [discover_text])

        flows = parsed.get("flows", [])
        logger.info("Parsed %d navigation flows (mode=%s)", len(flows), "validate" if use_validate else "discover")

        # Fallback: validate returned 0 flows → retry with discover
        if use_validate and not flows:
            logger.warning("Validate mode returned 0 flows, retrying with discover mode")
            validate_usage = parsed.get("usage", {})
            parsed = _call_claude_nav(model, NAV_DISCOVER_PROMPT, base_content + [discover_text])
            flows = parsed.get("flows", [])
            logger.info("Discover fallback returned %d flows", len(flows))
            # Merge usage from both calls
            if validate_usage:
                usage = parsed.get("usage", {})
                usage["input_tokens"] = usage.get("input_tokens", 0) + validate_usage.get("input_tokens", 0)
                usage["output_tokens"] = usage.get("output_tokens", 0) + validate_usage.get("output_tokens", 0)
                usage["cost_usd"] = usage.get("cost_usd", 0) + validate_usage.get("cost_usd", 0)

        return parsed

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("Failed to parse Claude nav response: %s", e, exc_info=True)
        return {"flows": [], "summary": f"Navigation discovery failed: {e}", "usage": {}}
    except anthropic.APIError as e:
        logger.error("Claude API error in nav discovery: %s", e, exc_info=True)
        return {"flows": [], "summary": f"Navigation discovery API error: {e}", "usage": {}}


# ── Phase 3: Browser Crawl with Discovered Flows ─────────────

CRAWL_SYSTEM_PROMPT = """\
You are a browser crawl agent. You are ALREADY logged in. A browser session is active. \
Do NOT navigate to any URL or log in.

Your job: visit ONLY the pages listed below, explore each page thoroughly, then stop.

## Steps for EACH page in the list
1. Click the nav element to reach the page (click_by_text with the nav_text)
2. wait_seconds 2
3. take_screenshot of the page
4. list_interactive_elements to discover all UI elements on this page
5. Explore sub-sections within this page:
   - Click tabs, sub-tabs, filters, or view toggles visible on the page
   - wait_seconds 2 after each click
   - take_screenshot of each distinct sub-view
   - Open ONE detail item (first card/row) if present, screenshot it, then go back
   - Open ONE action button (Add/Create) if present, screenshot the form/modal, then dismiss (Escape/Cancel)
6. Move to the next page in the list

## Overall flow
1. start_recording
2. take_screenshot of the home page
3. Explore each listed page using the steps above
4. stop_recording
5. Respond with ONLY valid JSON (no markdown fences):

{
  "pages": [
    {
      "name": "Page Name",
      "nav_text": "text clicked to reach this page",
      "screenshots": ["screen_1.png", "screen_2.png"],
      "interactive_elements_count": 15,
      "sub_sections": ["Tab 1", "Tab 2"],
      "detail_view_opened": true,
      "action_form_opened": false
    }
  ],
  "total_screenshots": 8,
  "total_pages_visited": 3,
  "summary": "One sentence describing what was found"
}

## Rules
- ONLY visit pages in the provided list — do NOT navigate to other sections
- Do NOT submit forms, create records, or modify data — only OPEN forms then dismiss
- Use click_by_text with nav_text — if it fails, try list_interactive_elements to find the selector
- Always respond with the exact JSON format above — no other format"""

CRAWL_TOOLS = [
    {
        "name": "take_screenshot",
        "description": "Capture a screenshot of the current page.",
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
        "description": "Click an element by CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the element"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["selector", "job_id"],
        },
    },
    {
        "name": "click_by_text",
        "description": "Click an element by its visible text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The visible text of the element"},
                "exact": {"type": "boolean", "description": "If true, match exactly. Default false."},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["text", "job_id"],
        },
    },
    {
        "name": "list_interactive_elements",
        "description": "List all clickable elements on the current page.",
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
        "name": "wait_seconds",
        "description": "Wait for a few seconds to let the page load.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Seconds to wait (1-10)"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["seconds", "job_id"],
        },
    },
    {
        "name": "scroll_page",
        "description": "Scroll the page up or down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction"},
                "amount": {"type": "integer", "description": "Pixels to scroll"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["direction", "amount", "job_id"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key (Enter, Escape, Tab, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The key to press"},
                "job_id": {"type": "string", "description": "The job ID"},
            },
            "required": ["key", "job_id"],
        },
    },
    {
        "name": "start_recording",
        "description": "Start video recording of the browser session.",
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


async def _crawl_execute_tool(name: str, input: dict) -> str | dict | list:
    """Execute browser tools for the crawl phase."""
    if name == "take_screenshot":
        return await take_screenshot(input["job_id"])
    elif name == "click_element":
        return await click_element(input["selector"], input["job_id"])
    elif name == "click_by_text":
        return await click_by_text(input["text"], input["job_id"], input.get("exact", False))
    elif name == "list_interactive_elements":
        return await list_interactive_elements(input["job_id"])
    elif name == "get_page_content":
        return await get_page_content(input["job_id"])
    elif name == "wait_seconds":
        return await wait_seconds(input["seconds"], input["job_id"])
    elif name == "scroll_page":
        return await scroll_page(input["direction"], input["amount"], input["job_id"])
    elif name == "press_key":
        return await press_key(input["key"], input["job_id"])
    elif name == "start_recording":
        from tools.browser_tools import start_recording
        return await start_recording(input["job_id"])
    elif name == "stop_recording":
        return await stop_recording(input["job_id"])
    else:
        return {"error": f"Unknown tool: {name}"}


async def _crawl_with_flows(
    job_id: str, flows: list[dict]
) -> dict[str, Any]:
    """Phase 3: Crawl using the existing browser session (already logged in)."""
    # Redirect output to outputs/uat_screenshots
    from tools.browser_tools import _sessions
    output_dir = "outputs/uat_screenshots"
    os.makedirs(output_dir, exist_ok=True)
    if job_id in _sessions:
        _sessions[job_id]["output_dir"] = output_dir
        _sessions[job_id]["screenshot_count"] = 0

    collected: dict[str, Any] = {
        "screenshot_paths": [],
        "video_path": None,
        "interactive_elements": [],
        "action_log": [],
    }

    async def _collecting_executor(name: str, input: dict) -> str | dict | list:
        result = await _crawl_execute_tool(name, input)
        if name == "take_screenshot" and isinstance(result, dict):
            collected["screenshot_paths"].append(result.get("path", ""))
        elif name == "list_interactive_elements" and isinstance(result, list):
            collected["interactive_elements"] = result
        elif name == "stop_recording" and isinstance(result, dict):
            collected["video_path"] = result.get("video_path")
            collected["action_log"] = result.get("action_log", [])
        return result

    # Build page list with exploration instructions
    pages_list = ""
    if flows:
        pages_list = "\n".join(
            f"  {i+1}. {f['name']} — click_by_text(\"{f.get('nav_text', '')}\")"
            for i, f in enumerate(flows)
        )

    task = (
        f"You are already logged in and on the home page.\n"
        f"Job ID: {job_id}\n\n"
        f"Pages to explore (ONLY these, nothing else):\n{pages_list}\n\n"
        "For each page: navigate to it, list_interactive_elements, explore sub-sections/tabs, "
        "open one detail view and one action form if available. Take screenshots of each distinct view."
    )

    # max_turns: start_recording(1) + home screenshot(1) + per flow ~8 turns (deep explore) + stop(1) + summary(1)
    max_turns = 4 + len(flows) * 8

    result = await run_agent_loop(
        system_prompt=CRAWL_SYSTEM_PROMPT,
        tools=CRAWL_TOOLS,
        tool_executor=_collecting_executor,
        user_message=task,
        max_turns=max_turns,
    )

    # Parse structured JSON from agent response, fallback to raw text
    response_text = result["text"]
    try:
        clean = response_text.replace("```json", "").replace("```", "").strip()
        structured = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        structured = {
            "pages": [],
            "total_screenshots": len(collected["screenshot_paths"]),
            "total_pages_visited": len(flows),
            "summary": response_text,
        }

    return {
        "summary": structured,
        "data": collected,
        "usage": result["usage"],
    }


# ── Top-level orchestrator ────────────────────────────────────

def _aggregate_usage(*usages: dict) -> dict:
    """Merge multiple usage dicts into a single total."""
    total_input = 0
    total_output = 0
    total_cost = 0.0
    model = ""
    for u in usages:
        if not u:
            continue
        total_input += u.get("input_tokens", 0)
        total_output += u.get("output_tokens", 0)
        total_cost += u.get("cost_usd", 0)
        model = u.get("model", model)
    return {
        "model": model,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": total_cost,
    }


async def run_discover_crawl(
    job_id: str,
    kb_key: str,
    figma_images_dir: str | None = None,
) -> dict[str, Any]:
    """Run the 3-phase discover-crawl pipeline.

    Phase 1: Login and capture home page screenshot
    Phase 2: Discover navigation structure (from screenshot +/- Figma)
    Phase 3: Full browser crawl with discovered flows as guidance

    Returns:
        Dict with summary, data (login/navigation/crawl results), and aggregated usage.
    """
    # KB lookup
    kb_entry = get_knowledge("staging_urls", kb_key)
    if isinstance(kb_entry, dict) and "error" in kb_entry:
        raise ValueError(f"KB lookup failed: {kb_entry['error']}")

    url = kb_entry["url"]
    creds = {k: v for k, v in kb_entry.items() if k != "url"}

    # Phase 1: Login + home screenshot
    logger.info("Phase 1: Login and capture home page for %s", kb_key)
    login_result = await _login_and_capture_home(job_id, url, creds)

    if not login_result.get("login_verified", True):
        logger.warning("Phase 1 login not verified — home screenshot may be a login page")

    # Phase 2: Discover navigation
    logger.info("Phase 2: Discover navigation from home screenshot")
    nav_result = _discover_navigation(
        login_result.get("home_screenshot"),
        figma_images_dir,
    )
    flows = nav_result.get("flows", [])
    logger.info("Discovered %d navigation flows", len(flows))

    # Phase 3: Full crawl with flows (reuses existing browser session — already logged in)
    logger.info("Phase 3: Browser crawl with %d discovered flows", len(flows))
    crawl_result = await _crawl_with_flows(job_id, flows)

    # Aggregate usage
    usage = _aggregate_usage(
        login_result.get("usage", {}),
        nav_result.get("usage", {}),
        crawl_result.get("usage", {}),
    )

    return {
        "summary": crawl_result.get("summary", ""),
        "data": {
            "login": login_result,
            "navigation": nav_result,
            "crawl": crawl_result,
        },
        "usage": usage,
    }
