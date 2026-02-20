"""Record a ~10-second 1080p video of pingops-matrix-v2.html using Playwright."""

import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parent
    html_file = root / "pingops-matrix-v2.html"
    output_dir = root / "outputs" / "pingops-video"
    os.makedirs(output_dir, exist_ok=True)

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        record_video_dir=str(output_dir),
        record_video_size={"width": 1920, "height": 1080},
        viewport={"width": 1920, "height": 1080},
    )
    page = await context.new_page()

    await page.goto(f"file://{html_file}", wait_until="networkidle")
    print("Recording ~10 seconds of animation...")
    await page.wait_for_timeout(10000)

    video_path = await page.video.path()
    await context.close()
    await browser.close()
    await pw.stop()

    print(f"Video saved to: {video_path}")


if __name__ == "__main__":
    asyncio.run(main())
