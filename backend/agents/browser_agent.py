from __future__ import annotations

import os

from playwright.async_api import async_playwright


async def explore_and_record(
    staging_url: str, job_id: str
) -> tuple[list[str], str | None]:
    output_dir = f"outputs/{job_id}"
    os.makedirs(output_dir, exist_ok=True)
    screenshots: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            record_video_dir=output_dir,
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()
        await page.goto(staging_url, wait_until="networkidle")

        # Screenshot: initial state
        s1 = f"{output_dir}/screen_1.png"
        await page.screenshot(path=s1, full_page=True)
        screenshots.append(s1)

        # Click through interactive elements
        selectors = ["button", "a[href]", "input[type=submit]", "[role=button]"]
        clicked = 0
        for selector in selectors:
            elements = await page.query_selector_all(selector)
            for el in elements:
                if clicked >= 8:
                    break
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_load_state("networkidle")
                    path = f"{output_dir}/screen_{clicked + 2}.png"
                    await page.screenshot(path=path, full_page=True)
                    screenshots.append(path)
                    clicked += 1
                    await page.go_back()
                    await page.wait_for_load_state("networkidle")
                except Exception:
                    continue

        await context.close()
        await browser.close()

    # Find recorded video
    video_files = [f for f in os.listdir(output_dir) if f.endswith(".webm")]
    video_path = f"{output_dir}/{video_files[0]}" if video_files else None

    return screenshots, video_path
