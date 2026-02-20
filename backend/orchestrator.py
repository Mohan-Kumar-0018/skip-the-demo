from __future__ import annotations

import logging
import os

from agents.browser_agent import run_browser_agent
from db.models import (
    complete_run,
    fail_run,
    save_browser_data,
    save_results,
    save_token_usage,
    update_run,
    upsert_step,
)
from executor import execute_plan
from planner import create_plan
from tools.kb_tools import get_knowledge

logger = logging.getLogger(__name__)

STEPS = [
    "jira_fetch",
    "prd_parse",
    "figma_export",
    "browser_crawl",
    "design_compare",
    "synthesis",
    "slack_delivery",
]


async def run_browser_pipeline(run_id: str, kb_key: str) -> None:
    """Standalone browser crawl — looks up KB for URL/creds, then runs browser agent."""
    try:
        # Init steps
        upsert_step(run_id, "browser_crawl", "pending")

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
        upsert_step(run_id, "browser_crawl", "running")
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

        output_dir = f"outputs/{run_id}"
        if os.path.isdir(output_dir):
            collected["screenshots"] = [
                f"{output_dir}/{f}"
                for f in sorted(os.listdir(output_dir))
                if f.endswith(".png")
            ]
            video_files = [f for f in os.listdir(output_dir) if f.endswith(".webm")]
            if video_files:
                collected["video_path"] = f"{output_dir}/{video_files[0]}"

        upsert_step(run_id, "browser_crawl", "done")
        update_run(run_id, "Complete", 100)

        save_results(run_id, collected)
        complete_run(run_id)

    except Exception as e:
        logger.exception("Browser pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))


async def run_pipeline(run_id: str, ticket_id: str) -> None:
    """Main entry point — plans then executes the pipeline deterministically."""
    try:
        # Init all steps as pending
        for s in STEPS:
            upsert_step(run_id, s, "pending")

        # Phase 1: Plan (1 Claude call)
        update_run(run_id, "Planning pipeline...", 2)
        await create_plan(run_id, ticket_id)

        # Phase 2: Execute (deterministic Python)
        collected = await execute_plan(run_id, ticket_id)

        # Save results and complete
        save_results(run_id, collected)
        complete_run(run_id)

    except Exception as e:
        logger.exception("Pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))
