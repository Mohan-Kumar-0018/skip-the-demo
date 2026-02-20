from __future__ import annotations

import logging

from agents.browser_agent import explore_and_record
from agents.jira_agent import add_comment, get_prd_and_design, get_ticket
from agents.slack_agent import send_pm_briefing
from agents.synthesis_agent import generate_pm_summary
from agents.vision_agent import compare_design_vs_reality
from db.models import complete_run, fail_run, save_results, update_run, upsert_step
from utils.pdf_parser import extract_text

logger = logging.getLogger(__name__)

STEPS = [
    "jira_fetch",
    "prd_parse",
    "browser_crawl",
    "design_compare",
    "synthesis",
    "slack_delivery",
]


def _step(
    run_id: str,
    label: str,
    progress: int,
    name: str,
    status: str,
    feature_name: str | None = None,
) -> None:
    update_run(run_id, label, progress, feature_name=feature_name)
    upsert_step(run_id, name, status)


async def run_pipeline(run_id: str, ticket_id: str) -> None:
    try:
        # Init all steps as pending
        for s in STEPS:
            upsert_step(run_id, s, "pending")

        # 1. Jira
        _step(run_id, "Fetching Jira ticket...", 10, "jira_fetch", "running")
        ticket = get_ticket(ticket_id)
        prd_bytes, design_bytes = get_prd_and_design(ticket["attachments"])
        _step(
            run_id,
            "Jira ticket fetched",
            20,
            "jira_fetch",
            "done",
            feature_name=ticket["title"],
        )

        # 2. PRD
        _step(run_id, "Reading PRD...", 25, "prd_parse", "running")
        prd_text = extract_text(prd_bytes) if prd_bytes else str(ticket["description"])
        _step(run_id, "PRD parsed", 30, "prd_parse", "done")

        # 3. Browser
        _step(run_id, "Exploring staging app...", 35, "browser_crawl", "running")
        screenshots, video_path = await explore_and_record(
            ticket["staging_url"], run_id
        )
        _step(run_id, "Staging app recorded", 55, "browser_crawl", "done")

        # 4. Vision
        _step(
            run_id, "Comparing design vs reality...", 60, "design_compare", "running"
        )
        if design_bytes:
            design_result = compare_design_vs_reality(design_bytes, screenshots)
        else:
            design_result = {
                "score": 0,
                "deviations": [],
                "summary": "No design file attached",
            }
        _step(run_id, "Design comparison complete", 70, "design_compare", "done")

        # 5. Synthesis
        _step(
            run_id,
            "Writing PM summary and release notes...",
            75,
            "synthesis",
            "running",
        )
        content = generate_pm_summary(ticket["title"], prd_text, design_result)
        _step(run_id, "Content generated", 85, "synthesis", "done")

        # 6. Slack
        _step(
            run_id, "Sending PM briefing to Slack...", 88, "slack_delivery", "running"
        )
        results = {
            "feature_name": ticket["title"],
            "design_score": design_result["score"],
            "deviations": design_result["deviations"],
            "summary": content["summary"],
            "release_notes": content["release_notes"],
            "video_path": video_path,
            "screenshots": screenshots,
            "slack_sent": False,
        }
        send_pm_briefing(results)
        results["slack_sent"] = True
        _step(run_id, "Slack briefing sent", 95, "slack_delivery", "done")

        # Save + close
        save_results(run_id, results)
        add_comment(
            ticket_id, "\u2705 SkipTheDemo briefing delivered to PM via Slack."
        )
        complete_run(run_id)

    except Exception as e:
        logger.exception("Pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))
