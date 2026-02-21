from __future__ import annotations

import logging
import os

from agents.browser_agent import run_browser_agent
from agents.discover_crawl_agent import run_discover_crawl
from db.models import (
    complete_run,
    fail_run,
    save_browser_data,
    save_plan,
    save_results,
    save_token_usage,
    update_plan_step,
    update_run,
)
from scheduler import PipelineScheduler
from tools.kb_tools import get_knowledge

logger = logging.getLogger(__name__)


async def run_browser_pipeline(run_id: str, kb_key: str) -> None:
    """Standalone browser crawl — looks up KB for URL/creds, then runs browser agent."""
    try:
        # Synthetic 1-step plan
        save_plan(run_id, [
            {"step_order": 1, "step_name": "browser_crawl", "agent": "browser", "params": {}, "depends_on": []},
        ])

        # 1. Look up staging URL and credentials from KB
        kb_entry = get_knowledge("staging_urls", kb_key)
        if isinstance(kb_entry, dict) and "error" in kb_entry:
            fail_run(run_id, f"KB lookup failed: {kb_entry['error']}")
            return

        url = kb_entry["url"]
        creds = {k: v for k, v in kb_entry.items() if k != "url"}

        # 2. Build task string for browser agent
        creds_text = ""
        if creds:
            creds_text = "\n\nLogin credentials:\n" + "\n".join(
                f"  {k}: {v}" for k, v in creds.items()
            )

        task = (
            f"Explore the web application at {url} thoroughly.\n"
            f"Job ID: {run_id}\n"
            f"Output directory: outputs/{run_id}/\n"
            f"{creds_text}\n\n"
            f"## CRITICAL: URL Restriction\n"
            f"You MUST only navigate to {url} and pages within its origin. Do NOT navigate to:\n"
            f"- Figma links, design URLs, or any figma.com URLs\n"
            f"- Any external URLs found on pages (e.g. documentation, support links, third-party services)\n"
            f"Navigate within the app using clicks (click_element, click_by_text), NOT by calling navigate_to_url with new URLs. "
            f"The only exception is if you need to return to {url} after getting lost.\n\n"
            "Instructions:\n"
            "1. Navigate to the URL\n"
            "2. If there's a login page, use the provided credentials to log in\n"
            "3. Take a screenshot of every page you visit\n"
            "4. List interactive elements and click through ALL navigation links, tabs, and menu items\n"
            "5. Systematically visit every reachable page in the application\n"
            "6. Take a screenshot after each navigation\n"
            "7. When you've explored all pages, stop the recording\n"
            "8. Provide a summary of all pages and flows discovered"
        )

        # 3. Run browser agent
        update_plan_step(run_id, "browser_crawl", "running")
        update_run(run_id, "Crawling staging app...", 30)

        result = await run_browser_agent(task)

        # Save browser agent token usage
        usage = result.get("usage", {})
        if usage:
            save_token_usage(run_id, "browser", usage.get("model", ""), usage.get("input_tokens", 0), usage.get("output_tokens", 0), usage.get("cost_usd", 0))

        # 4. Collect outputs
        collected = {
            "feature_name": kb_key,
            "design_score": 0,
            "deviations": [],
            "summary": result["summary"],
            "release_notes": "",
            "video_path": None,
            "screenshots": [],
            "slack_sent": False,
        }

        screenshots_dir = f"outputs/{run_id}/screenshots"
        video_dir = f"outputs/{run_id}/video"
        if os.path.isdir(screenshots_dir):
            collected["screenshots"] = [
                f"{screenshots_dir}/{f}"
                for f in sorted(os.listdir(screenshots_dir))
                if f.endswith(".png")
            ]
        if os.path.isdir(video_dir):
            video_files = [f for f in os.listdir(video_dir) if f.endswith((".webm", ".mov"))]
            if video_files:
                collected["video_path"] = f"{video_dir}/{video_files[0]}"

        update_plan_step(run_id, "browser_crawl", "done", result_summary="Browser crawl completed")
        update_run(run_id, "Complete", 100)

        save_results(run_id, collected)
        complete_run(run_id)

    except Exception as e:
        logger.exception("Browser pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))


async def run_discover_crawl_pipeline(
    run_id: str, kb_key: str, figma_images_dir: str | None = None
) -> None:
    """Discover-crawl pipeline — login, discover nav, then full crawl."""
    try:
        # Synthetic 3-step plan
        save_plan(run_id, [
            {"step_order": 1, "step_name": "login", "agent": "discover_crawl", "params": {}, "depends_on": []},
            {"step_order": 2, "step_name": "nav_discovery", "agent": "discover_crawl", "params": {}, "depends_on": ["login"]},
            {"step_order": 3, "step_name": "browser_crawl", "agent": "discover_crawl", "params": {}, "depends_on": ["nav_discovery"]},
        ])

        # Phase 1: Login
        update_plan_step(run_id, "login", "running")
        update_run(run_id, "Logging in and capturing home page...", 10)

        # Phase 2: Nav discovery + Phase 3: Browser crawl
        result = await run_discover_crawl(run_id, kb_key, figma_images_dir)

        # Update step statuses
        update_plan_step(run_id, "login", "done", result_summary="Logged in")
        update_plan_step(run_id, "nav_discovery", "done", result_summary="Nav discovered")
        update_plan_step(run_id, "browser_crawl", "done", result_summary="Crawl completed")

        # Save token usage
        usage = result.get("usage", {})
        if usage:
            save_token_usage(
                run_id, "discover_crawl",
                usage.get("model", ""),
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cost_usd", 0),
            )

        # Save browser data from crawl phase
        crawl_data = result.get("data", {}).get("crawl", {}).get("data", {})
        if crawl_data:
            save_browser_data(run_id, crawl_data)

        # Collect outputs
        collected = {
            "feature_name": kb_key,
            "design_score": 0,
            "deviations": [],
            "summary": result.get("summary", ""),
            "release_notes": "",
            "video_path": None,
            "screenshots": [],
            "slack_sent": False,
        }

        screenshots_dir = "outputs/uat_screenshots/screenshots"
        video_dir = "outputs/uat_screenshots/video"
        if os.path.isdir(screenshots_dir):
            collected["screenshots"] = [
                f"{screenshots_dir}/{f}"
                for f in sorted(os.listdir(screenshots_dir))
                if f.endswith(".png")
            ]
        if os.path.isdir(video_dir):
            video_files = [f for f in os.listdir(video_dir) if f.endswith((".webm", ".mov"))]
            if video_files:
                collected["video_path"] = f"{video_dir}/{video_files[0]}"

        update_run(run_id, "Complete", 100)
        save_results(run_id, collected)
        complete_run(run_id)

    except Exception as e:
        logger.exception("Discover-crawl pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))


async def run_pipeline(run_id: str, ticket_id: str) -> None:
    """Main entry point — plans then executes via event-driven scheduler."""
    try:
        # Phase 1 + 2: Plan and execute via scheduler
        update_run(run_id, "Planning pipeline...", 2)
        scheduler = PipelineScheduler(run_id, ticket_id)
        await scheduler.start()

    except Exception as e:
        logger.exception("Pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))
