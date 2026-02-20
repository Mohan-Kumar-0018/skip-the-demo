from __future__ import annotations

import os
from typing import Any

from playwright.async_api import Browser, Page, async_playwright, Playwright

# Persistent browser sessions keyed by job_id
_sessions: dict[str, dict[str, Any]] = {}


async def _get_session(job_id: str) -> dict[str, Any]:
    """Get or raise for an existing browser session."""
    if job_id not in _sessions:
        raise RuntimeError(f"No browser session for job {job_id}. Call navigate_to_url first.")
    return _sessions[job_id]


async def navigate_to_url(url: str, job_id: str) -> dict[str, str]:
    """Open a URL in the browser. Creates a new session if needed. Returns page title and description."""
    output_dir = f"outputs/{job_id}"
    os.makedirs(output_dir, exist_ok=True)

    if job_id not in _sessions:
        pw: Playwright = await async_playwright().start()
        browser: Browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            record_video_dir=output_dir,
            record_video_size={"width": 1280, "height": 720},
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
    await page.wait_for_timeout(1500)  # let app (e.g. Flutter) settle
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
        await page.wait_for_timeout(500)  # let Flutter assign focus before typing
        await target.focus()
        await page.wait_for_timeout(300)
        # Try fill() on real input/textarea; otherwise type via keyboard (Flutter canvas/custom)
        tag = await target.evaluate("el => (el.tagName && el.tagName.toLowerCase()) || ''")
        if tag in ("input", "textarea"):
            await target.fill("")
            await target.fill(text)
        else:
            # Flutter/custom widget: focus is on the element, type with delay so OTP box receives keys
            await page.keyboard.type(text, delay=120)
        await page.wait_for_timeout(800)  # allow UI to update after typing
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "message": f"Typed '{text}' into {selector}"}


async def press_key(key: str, job_id: str) -> dict[str, str]:
    """Press a keyboard key (e.g. Enter, Tab, Backspace, ArrowDown)."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    try:
        await page.keyboard.press(key)
        await page.wait_for_timeout(500)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "message": f"Pressed {key}"}


async def click_element(selector: str, job_id: str) -> dict[str, str]:
    """Click an element by CSS selector. Returns new page state after click."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    el = await page.query_selector(selector)
    if not el:
        return {"status": "error", "message": f"Element not found: {selector}"}
    try:
        await el.scroll_into_view_if_needed()
        await el.click()
        await page.wait_for_timeout(1200)  # allow navigation/UI update (e.g. Flutter)
        await page.wait_for_load_state("networkidle")
    except Exception as e:
        return {"status": "error", "message": str(e)}
    title = await page.title()
    return {"status": "ok", "title": title, "url": page.url}


async def list_interactive_elements(job_id: str) -> list[dict[str, str]]:
    """List clickable elements on the current page with their selectors and text."""
    session = await _get_session(job_id)
    page: Page = session["page"]
    elements = await page.evaluate("""() => {
        const selectors = ['button', 'a[href]', 'input', 'textarea', 'select', 'input[type=submit]', '[role=button]', '[role=tab]', '[role=link]', '[role=textbox]'];
        const results = [];
        for (const sel of selectors) {
            for (const el of document.querySelectorAll(sel)) {
                const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().substring(0, 100);
                if (!text) continue;
                const tag = el.tagName.toLowerCase();
                const href = el.getAttribute('href') || '';
                // Build a usable selector
                let css = sel;
                if (el.id) css = '#' + el.id;
                else if (text.length < 50) css = `${tag}:has-text("${text.replace(/"/g, '\\\\"')}")`;
                results.push({tag, text, href, selector: css});
            }
        }
        return results.slice(0, 30);
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
    """Start video recording. Recording is already active from context creation, so this is a no-op acknowledgment."""
    if job_id not in _sessions:
        return {"status": "error", "message": "No browser session. Call navigate_to_url first."}
    return {"status": "recording", "message": "Video recording is active since session start."}


async def stop_recording(job_id: str) -> dict[str, str | None]:
    """Stop recording and close the browser session. Returns the video path."""
    if job_id not in _sessions:
        return {"status": "error", "video_path": None}

    session = _sessions.pop(job_id)
    page: Page = session["page"]
    video = page.video
    await session["context"].close()
    await session["browser"].close()
    await session["playwright"].stop()

    video_path = None
    if video:
        video_path = await video.path()
        video_path = str(video_path)

    # Fallback: find video file in output dir
    if not video_path:
        output_dir = session["output_dir"]
        video_files = [f for f in os.listdir(output_dir) if f.endswith(".webm")]
        if video_files:
            video_path = f"{output_dir}/{video_files[0]}"

    return {"status": "stopped", "video_path": video_path}
