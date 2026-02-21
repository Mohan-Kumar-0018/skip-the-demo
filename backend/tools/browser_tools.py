from __future__ import annotations

import json
import os
import time
from typing import Any

from playwright.async_api import Browser, Page, async_playwright, Playwright

# Persistent browser sessions keyed by job_id
_sessions: dict[str, dict[str, Any]] = {}


async def _get_session(job_id: str) -> dict[str, Any]:
    """Get or raise for an existing browser session."""
    if job_id not in _sessions:
        raise RuntimeError(f"No browser session for job {job_id}. Call navigate_to_url first.")
    return _sessions[job_id]


def _log_action(
    session: dict[str, Any],
    action_type: str,
    description: str,
    x: float | None = None,
    y: float | None = None,
) -> None:
    """Record an action in the session's action journal (only if recording is active)."""
    if "action_log" not in session:
        return
    start = session.get("recording_start_time", 0)
    entry: dict[str, Any] = {
        "timestamp_ms": int((time.monotonic() - start) * 1000),
        "action_type": action_type,
        "description": description,
    }
    if x is not None and y is not None:
        entry["x"] = x
        entry["y"] = y
    session["action_log"].append(entry)


async def navigate_to_url(url: str, job_id: str) -> dict[str, str]:
    """Open a URL in the browser WITHOUT video recording. Call start_recording later to begin recording."""
    output_dir = f"outputs/{job_id}"
    os.makedirs(output_dir, exist_ok=True)

    if job_id not in _sessions:
        pw: Playwright = await async_playwright().start()
        browser: Browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        page: Page = await context.new_page()
        _sessions[job_id] = {
            "playwright": pw,
            "browser": browser,
            "context": context,
            "page": page,
            "output_dir": output_dir,
            "screenshot_count": 0,
        }
    else:
        page = _sessions[job_id]["page"]

    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(500)  # let app (e.g. Flutter) settle
    title = await page.title()
    meta_desc = await page.evaluate(
        "() => document.querySelector('meta[name=\"description\"]')?.content || ''"
    )
    return {"title": title, "description": meta_desc, "url": page.url}


async def take_screenshot(job_id: str) -> dict[str, str]:
    """Capture a screenshot of the current page. Returns the file path."""
    session = await _get_session(job_id)
    session["screenshot_count"] += 1
    path = f"{session['output_dir']}/screen_{session['screenshot_count']}.png"
    await session["page"].screenshot(path=path, full_page=True)
    return {"path": path}


async def type_text(selector: str, text: str, job_id: str) -> dict[str, str]:
    """Type text into an input field identified by CSS selector. For Flutter/OTP fields: focuses, waits, then types."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    el = await page.query_selector(selector)
    if not el:
        return {"status": "error", "message": f"Element not found: {selector}"}
    # Flutter often wraps the real input: prefer nested input/textarea for fill()
    input_el = await el.query_selector("input, textarea")
    target = input_el if input_el else el
    try:
        await target.scroll_into_view_if_needed()
        await target.click()
        await page.wait_for_timeout(200)  # let Flutter assign focus before typing
        await target.focus()
        await page.wait_for_timeout(100)
        # Try fill() on real input/textarea; otherwise type via keyboard (Flutter canvas/custom)
        tag = await target.evaluate("el => (el.tagName && el.tagName.toLowerCase()) || ''")
        if tag in ("input", "textarea"):
            await target.fill("")
            await target.fill(text)
        else:
            # Flutter/custom widget: focus is on the element, type with delay so OTP box receives keys
            await page.keyboard.type(text, delay=120)
        await page.wait_for_timeout(300)  # allow UI to update after typing
    except Exception as e:
        return {"status": "error", "message": str(e)}
    _log_action(session, "type", f"Typed '{text}' into {selector}")
    return {"status": "ok", "message": f"Typed '{text}' into {selector}"}


async def press_key(key: str, job_id: str) -> dict[str, str]:
    """Press a keyboard key (e.g. Enter, Tab, Backspace, ArrowDown)."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    try:
        await page.keyboard.press(key)
        await page.wait_for_timeout(200)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    _log_action(session, "key_press", f"Pressed {key}")
    return {"status": "ok", "message": f"Pressed {key}"}


async def click_element(selector: str, job_id: str) -> dict[str, str]:
    """Click an element by CSS selector. Returns new page state after click."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    el = await page.query_selector(selector)
    if not el:
        return {"status": "error", "message": f"Element not found: {selector}"}
    # Capture bounding box for action journal before click
    box = await el.bounding_box()
    try:
        await el.scroll_into_view_if_needed()
        await el.click()
        await page.wait_for_timeout(400)  # allow navigation/UI update (e.g. Flutter)
        await page.wait_for_load_state("domcontentloaded")
    except Exception as e:
        return {"status": "error", "message": str(e)}
    cx = box["x"] + box["width"] / 2 if box else None
    cy = box["y"] + box["height"] / 2 if box else None
    _log_action(session, "click", f"Clicked {selector}", cx, cy)
    title = await page.title()
    return {"status": "ok", "title": title, "url": page.url}


async def click_by_text(text: str, job_id: str, exact: bool = False) -> dict[str, str]:
    """Click an element by its visible text using Playwright's get_by_text locator.

    More reliable than CSS selectors for Flutter apps. Falls back to
    get_by_role if get_by_text finds nothing.
    """
    session = await _get_session(job_id)
    page: Page = session["page"]
    try:
        locator = page.get_by_text(text, exact=exact)
        count = await locator.count()
        if count == 0:
            # Fallback: try role-based locators
            for role in ("button", "tab", "link", "menuitem"):
                role_loc = page.get_by_role(role, name=text)
                if await role_loc.count() > 0:
                    locator = role_loc
                    count = await locator.count()
                    break
        if count == 0:
            return {"status": "error", "message": f"No element found with text: '{text}'"}
        # Click the first visible match
        target = locator.first
        box = await target.bounding_box()
        await target.scroll_into_view_if_needed()
        await target.click()
        await page.wait_for_timeout(500)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass  # Flutter apps may not trigger network activity
        cx = box["x"] + box["width"] / 2 if box else None
        cy = box["y"] + box["height"] / 2 if box else None
        _log_action(session, "click", f"Clicked text '{text}'", cx, cy)
        title = await page.title()
        return {"status": "ok", "title": title, "url": page.url, "matches": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def scroll_page(direction: str, amount: int, job_id: str) -> dict[str, str]:
    """Scroll the page using mouse wheel. direction: 'up' or 'down'. amount: pixels to scroll."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    try:
        delta_y = amount if direction == "down" else -amount
        await page.mouse.wheel(0, delta_y)
        await page.wait_for_timeout(300)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    _log_action(session, "scroll", f"Scrolled {direction} by {amount}px")
    return {"status": "ok", "message": f"Scrolled {direction} by {amount}px"}


async def list_interactive_elements(job_id: str) -> list[dict[str, str]]:
    """List clickable elements on the current page with their selectors and text.

    Includes Flutter-specific semantic elements (flt-semantics, role=menuitem, etc.).
    """
    session = await _get_session(job_id)
    page: Page = session["page"]
    elements = await page.evaluate("""() => {
        const selectors = [
            'button', 'a[href]', 'input', 'textarea', 'select',
            'input[type=submit]',
            '[role=button]', '[role=tab]', '[role=link]', '[role=textbox]',
            '[role=menuitem]', '[role=option]', '[role=listitem]',
            'flt-semantics', 'flt-semantics-container',
            '[aria-roledescription]',
            '[aria-label]', 'svg[role]', '[data-icon]'
        ];
        const seen = new Set();
        const results = [];
        for (const sel of selectors) {
            for (const el of document.querySelectorAll(sel)) {
                // Try multiple sources for descriptive text
                let text = (el.innerText || '').trim();
                if (!text) text = (el.value || '').trim();
                if (!text) text = (el.getAttribute('aria-label') || '').trim();
                if (!text) text = (el.getAttribute('aria-roledescription') || '').trim();
                if (!text) text = (el.getAttribute('title') || '').trim();
                // For icon-only elements, describe by class or tag
                if (!text) {
                    const svg = el.querySelector('svg');
                    const img = el.querySelector('img');
                    const cls = el.className ? String(el.className).substring(0, 60) : '';
                    if (svg) text = '[icon-button]';
                    else if (img) text = '[image-button: ' + (img.alt || 'no-alt') + ']';
                    else if (cls) text = '[' + cls.split(' ')[0] + ']';
                }
                if (!text) text = '[unnamed-' + el.tagName.toLowerCase() + ']';
                text = text.substring(0, 100);
                if (seen.has(text)) continue;
                seen.add(text);
                const tag = el.tagName.toLowerCase();
                const role = el.getAttribute('role') || '';
                const href = el.getAttribute('href') || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                // Build a usable selector
                let css = sel;
                if (el.id) css = '#' + el.id;
                else if (ariaLabel) css = `[aria-label="${ariaLabel.replace(/"/g, '\\\\"')}"]`;
                else if (text.length < 50 && !text.startsWith('[')) css = `${tag}:has-text("${text.replace(/"/g, '\\\\"')}")`;
                results.push({tag, text, role, href, selector: css, ariaLabel});
            }
        }
        return results.slice(0, 60);
    }""")
    return elements


async def wait_seconds(seconds: float, job_id: str) -> dict[str, str]:
    """Wait for a number of seconds. Use after navigation or click to let the page load (e.g. OTP screen)."""
    session = await _get_session(job_id)
    sec = max(0, min(10, float(seconds)))
    await session["page"].wait_for_timeout(int(sec * 1000))
    return {"status": "ok", "message": f"Waited {sec}s"}


async def get_page_content(job_id: str) -> dict[str, str]:
    """Get the visible text content of the current page."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    text = await page.evaluate("() => document.body.innerText")
    title = await page.title()
    return {"title": title, "url": page.url, "text": text[:5000]}


async def start_recording(job_id: str) -> dict[str, str]:
    """Start video recording by creating a new browser context with recording enabled.

    Preserves the current session (cookies, localStorage) and navigates to the
    current URL so the video begins clean — no login screens recorded.
    """
    if job_id not in _sessions:
        return {"status": "error", "message": "No browser session. Call navigate_to_url first."}

    session = _sessions[job_id]
    page: Page = session["page"]
    output_dir = session["output_dir"]

    # 1. Save current state and URL
    storage_state = await session["context"].storage_state()
    current_url = page.url

    # 2. Close old context (no video was recorded)
    await session["context"].close()

    # 3. Create new context WITH video recording, importing saved state
    new_context = await session["browser"].new_context(
        record_video_dir=output_dir,
        record_video_size={"width": 1280, "height": 720},
        viewport={"width": 1280, "height": 720},
        storage_state=storage_state,
    )
    new_page: Page = await new_context.new_page()
    await new_page.goto(current_url, wait_until="networkidle")
    await new_page.wait_for_timeout(500)

    # 4. Update session references
    session["context"] = new_context
    session["page"] = new_page

    # 5. Initialize action journal
    session["action_log"] = []
    session["recording_start_time"] = time.monotonic()

    return {"status": "recording", "message": f"Video recording started on {current_url}"}


async def stop_recording(job_id: str) -> dict[str, Any]:
    """Stop recording and close the browser session. Returns video path and action log."""
    if job_id not in _sessions:
        return {"status": "error", "video_path": None, "action_log": []}

    session = _sessions.pop(job_id)
    page: Page = session["page"]
    video = page.video
    action_log = session.get("action_log", [])
    output_dir = session["output_dir"]
    await session["context"].close()
    await session["browser"].close()
    await session["playwright"].stop()

    video_path = None
    if video:
        video_path = await video.path()
        video_path = str(video_path)

    # Fallback: find video file in output dir
    if not video_path:
        video_files = [f for f in os.listdir(output_dir) if f.endswith((".webm", ".mov"))]
        if video_files:
            video_path = f"{output_dir}/{video_files[0]}"

    # Save action log as JSON for standalone use
    if action_log:
        log_path = f"{output_dir}/action_log.json"
        with open(log_path, "w") as f:
            json.dump(action_log, f, indent=2)

    return {"status": "stopped", "video_path": video_path, "action_log": action_log}
