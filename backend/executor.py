from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from agents.browser_agent import run_browser_agent
from agents.figma_agent import run_figma_agent
from agents.jira_agent import run_jira_agent
from agents.navigation_planner_agent import plan_navigation
from agents.slack_agent import run_slack_agent
from agents.synthesis_agent import generate_pm_summary
from agents.vision_agent import compare_design_vs_reality
from db.models import (
    get_browser_data,
    get_figma_data,
    get_jira_data,
    get_step_output,
    save_browser_data,
    save_figma_data,
    save_jira_data,
    save_step_output,
    save_token_usage,
    update_plan_step,
    update_run,
    upsert_step,
)
from tools.kb_tools import get_knowledge, search_knowledge
from utils.adf_parser import adf_to_text
from utils.pdf_parser import extract_text

logger = logging.getLogger(__name__)

# Steps that abort the whole pipeline on failure
CRITICAL_STEPS = {"jira_fetch", "browser_crawl"}

# Progress percentages for each step
STEP_PROGRESS = {
    "jira_fetch": (5, 15),
    "prd_parse": (15, 20),
    "figma_export": (20, 28),
    "nav_plan": (28, 33),
    "browser_crawl": (33, 60),
    "design_compare": (60, 75),
    "synthesis": (75, 90),
    "slack_delivery": (90, 98),
}


STEP_LABELS = {
    "jira_fetch": "Fetching Jira ticket...",
    "data_cleanup": "Cleaning up ticket data...",
    "prd_parse": "Parsing PRD...",
    "figma_export": "Exporting Figma designs...",
    "nav_plan": "Planning navigation flow...",
    "browser_crawl": "Crawling staging app...",
    "design_compare": "Comparing designs...",
    "synthesis": "Generating summary...",
    "slack_delivery": "Delivering to Slack...",
}


async def run_step(run_id: str, ticket_id: str, step: dict[str, Any]) -> str:
    """Execute a single plan step: mark running, call handler, mark done/failed.

    Returns the result summary string.
    Raises on critical step failure.
    """
    step_name = step["step_name"]
    params = step.get("params") or {}
    label = STEP_LABELS.get(step_name, f"Running {step_name}...")

    # Mark step running
    update_plan_step(run_id, step_name, "running")
    upsert_step(run_id, step_name, "running")
    update_run(run_id, label, 0)  # progress updated by scheduler

    try:
        handler = _STEP_HANDLERS.get(step_name)
        if handler is None:
            logger.warning("No handler for step %s, skipping", step_name)
            update_plan_step(run_id, step_name, "skipped", result_summary="No handler")
            upsert_step(run_id, step_name, "done")
            return "No handler"

        result_summary = await handler(run_id, ticket_id, params)

        update_plan_step(run_id, step_name, "done", result_summary=result_summary)
        upsert_step(run_id, step_name, "done")

        # Update feature_name on run if jira provided it
        jira_out = get_step_output(run_id, "jira_fetch")
        if jira_out and jira_out.get("feature_name"):
            update_run(run_id, label, 0, feature_name=jira_out["feature_name"])

        return result_summary

    except Exception as e:
        error_msg = str(e)
        logger.exception("Step %s failed for run %s", step_name, run_id)
        update_plan_step(run_id, step_name, "failed", error=error_msg)
        upsert_step(run_id, step_name, "failed", error=error_msg)

        if step_name in CRITICAL_STEPS:
            raise
        # Non-critical: log and continue
        logger.warning("Non-critical step %s failed, continuing: %s", step_name, error_msg)
        return f"Failed: {error_msg}"


# ── Step handlers ──────────────────────────────


def _save_usage(run_id: str, agent_name: str, result: dict[str, Any]) -> None:
    usage = result.get("usage", {})
    if usage:
        save_token_usage(
            run_id,
            agent_name,
            usage.get("model", ""),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cost_usd", 0),
        )


async def _execute_jira(run_id: str, ticket_id: str, params: dict) -> str:
    task = (
        f"Fetch all details for Jira ticket {ticket_id} including subtasks, "
        f"comments, and all attachments. Save attachments to outputs/{run_id}/."
    )
    result = await run_jira_agent(task)
    _save_usage(run_id, "jira", result)

    jira_data = result["data"]
    ticket = jira_data.get("ticket", {})

    # Extract PRD text from PDF attachments
    prd_text = ""
    for att in jira_data.get("attachments", []):
        if att.get("category") == "prd" and att.get("path", "").endswith(".pdf"):
            if os.path.isfile(att["path"]):
                with open(att["path"], "rb") as f:
                    prd_text = extract_text(f.read())
                break

    # Extract Figma URLs from description and comments
    figma_pattern = r'https?://(?:www\.)?figma\.com/(?:design|file)/[^\s\)\]\"\'>]+'
    design_links: list[str] = []
    desc_str = str(ticket.get("description", ""))
    design_links.extend(re.findall(figma_pattern, desc_str))
    for comment in jira_data.get("comments", []):
        design_links.extend(re.findall(figma_pattern, comment.get("body", "")))
    design_links = list(set(design_links))

    save_jira_data(run_id, {
        "ticket_title": ticket.get("title", ""),
        "ticket_description": desc_str,
        "staging_url": ticket.get("staging_url", ""),
        "ticket_status": ticket.get("status", ""),
        "assignee": ticket.get("assignee", ""),
        "subtasks": jira_data.get("subtasks", []),
        "attachments": jira_data.get("attachments", []),
        "comments": jira_data.get("comments", []),
        "prd_text": prd_text,
        "design_links": design_links,
    })

    feature_name = ticket.get("title", ticket_id)
    save_step_output(run_id, "jira_fetch", {"feature_name": feature_name})

    return result["summary"]


async def _execute_data_cleanup(run_id: str, ticket_id: str, params: dict) -> str:
    jira = get_jira_data(run_id)
    if not jira:
        return "No Jira data to clean"

    raw_desc = jira.get("ticket_description", "")
    if not raw_desc:
        return "No ticket description to clean"

    cleaned = adf_to_text(raw_desc)

    # Write back only the cleaned description
    jira["ticket_description"] = cleaned
    save_jira_data(run_id, jira)

    return f"Cleaned ticket description ({len(cleaned)} chars)"


async def _execute_prd_parse(run_id: str, ticket_id: str, params: dict) -> str:
    jira = get_jira_data(run_id)
    if jira and jira.get("prd_text"):
        return f"PRD text available ({len(jira['prd_text'])} chars)"
    return "No PRD text found (will use ticket description)"


async def _execute_figma(run_id: str, ticket_id: str, params: dict) -> str:
    jira = get_jira_data(run_id)
    design_links = []
    if jira:
        raw = jira.get("design_links", [])
        if isinstance(raw, str):
            design_links = json.loads(raw)
        else:
            design_links = raw

    if not design_links:
        update_plan_step(run_id, "figma_export", "skipped", result_summary="No Figma links found")
        return "Skipped — no Figma links"

    figma_url = design_links[0]
    task = (
        f"Extract the design from this Figma link: {figma_url}. "
        f"Save exported images to outputs/{run_id}/."
    )
    result = await run_figma_agent(task)
    _save_usage(run_id, "figma", result)

    figma_data = result["data"]
    parsed = figma_data.get("parsed_url", {})
    file_info = figma_data.get("file_info", {})
    node_info = figma_data.get("node_info", {})

    save_figma_data(run_id, {
        "figma_url": figma_url,
        "file_name": file_info.get("name", ""),
        "file_last_modified": file_info.get("last_modified", ""),
        "node_name": node_info.get("name", ""),
        "exported_images": figma_data.get("exported", []),
        "export_errors": figma_data.get("errors", []),
    })

    return result["summary"]


async def _execute_nav_plan(run_id: str, ticket_id: str, params: dict) -> str:
    figma = get_figma_data(run_id)
    figma_images: list[dict] = []

    if figma:
        exported = figma.get("exported_images", [])
        if isinstance(exported, str):
            exported = json.loads(exported)
        for img in exported:
            path = img.get("path", "") if isinstance(img, dict) else str(img)
            basename = os.path.basename(path) if path else ""
            if path and basename.startswith("figma") and basename.endswith(".png") and os.path.isfile(path):
                name = img.get("name", basename) if isinstance(img, dict) else basename
                figma_images.append({"path": path, "name": name})

    if not figma_images:
        save_step_output(run_id, "nav_plan", {"nav_screens": []})
        return "Skipped — no Figma design images available"

    # Get context from Jira data
    jira = get_jira_data(run_id)
    prd_text = ""
    if jira:
        prd_text = jira.get("prd_text", "") or jira.get("ticket_description", "")

    result = plan_navigation(figma_images, prd_text)
    _save_usage(run_id, "nav_planner", result)

    screens = result.get("screens") or []
    save_step_output(run_id, "nav_plan", {"nav_screens": screens})
    return f"Navigation plan created: {len(screens)} screens identified"


async def _execute_browser(run_id: str, ticket_id: str, params: dict) -> str:
    # Get staging URL from params, Jira data, or KB
    staging_url = params.get("staging_url", "")

    if not staging_url:
        jira = get_jira_data(run_id)
        if jira:
            staging_url = jira.get("staging_url", "")

    if not staging_url:
        # Try KB lookup
        project_key = ticket_id.split("-")[0].lower() if "-" in ticket_id else ""
        if project_key:
            kb_results = search_knowledge(project_key)
            for hit in kb_results:
                if hit.get("category") == "staging_urls":
                    data = hit.get("data", {})
                    if isinstance(data, dict) and "url" in data:
                        staging_url = data["url"]
                        break

    # Build credentials text from KB
    creds_text = ""
    creds = get_knowledge("credentials")
    if isinstance(creds, dict) and "error" not in creds:
        staging_creds = creds.get("staging")
        if isinstance(staging_creds, dict):
            creds_text = "\n\nLogin credentials:\n" + "\n".join(
                f"  {k}: {v}" for k, v in staging_creds.items()
            )

    # Build navigation guidance from nav_plan if available
    nav_guidance = ""
    nav_plan_out = get_step_output(run_id, "nav_plan")
    nav_screens = nav_plan_out.get("nav_screens", []) if nav_plan_out else []
    if nav_screens:
        screen_names = [s.get("name", "Unknown") for s in nav_screens]
        nav_guidance = (
            f"\n\nPages to visit (from design analysis): {', '.join(screen_names)}\n"
            "Make sure to navigate to each of these pages and capture screenshots."
        )

    task = (
        f"Explore the web application at {staging_url} thoroughly.\n"
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
        f"{nav_guidance}"
    )

    result = await run_browser_agent(task)
    _save_usage(run_id, "browser", result)

    browser_data = result["data"]
    save_browser_data(run_id, {
        "urls_visited": browser_data.get("urls_visited", []),
        "page_titles": browser_data.get("page_titles", []),
        "screenshot_paths": browser_data.get("screenshot_paths", []),
        "video_path": browser_data.get("video_path", ""),
        "page_content": browser_data.get("page_content", ""),
        "interactive_elements": browser_data.get("interactive_elements", []),
    })

    # Collect screenshots and video for step outputs
    screenshots: list[str] = []
    video_path = ""
    output_dir = f"outputs/{run_id}"
    if os.path.isdir(output_dir):
        screenshots = [
            f"{output_dir}/{f}"
            for f in sorted(os.listdir(output_dir))
            if f.endswith(".png")
        ]
        video_files = [f for f in os.listdir(output_dir) if f.endswith(".webm")]
        if video_files:
            video_path = f"{output_dir}/{video_files[0]}"

    save_step_output(run_id, "browser_crawl", {
        "screenshots": screenshots,
        "video_path": video_path,
    })

    return result["summary"]


async def _execute_vision(run_id: str, ticket_id: str, params: dict) -> str:
    # Find design image: prefer Figma export, fallback to Jira attachment
    design_path = None

    figma = get_figma_data(run_id)
    if figma:
        exported = figma.get("exported_images", [])
        if isinstance(exported, str):
            exported = json.loads(exported)
        for img in exported:
            path = img.get("path", "") if isinstance(img, dict) else str(img)
            if path and os.path.isfile(path):
                design_path = path
                break

    if not design_path:
        jira = get_jira_data(run_id)
        if jira:
            attachments = jira.get("attachments", [])
            if isinstance(attachments, str):
                attachments = json.loads(attachments)
            for att in attachments:
                if att.get("category") == "design" and os.path.isfile(att.get("path", "")):
                    design_path = att["path"]
                    break

    # Read screenshots from browser step output
    browser_out = get_step_output(run_id, "browser_crawl")
    screenshots = browser_out.get("screenshots", []) if browser_out else []

    if not design_path or not screenshots:
        save_step_output(run_id, "design_compare", {
            "design_score": 0,
            "deviations": [],
        })
        return "Skipped — no design file or no screenshots"

    with open(design_path, "rb") as f:
        design_bytes = f.read()

    result = compare_design_vs_reality(design_bytes, screenshots)

    save_step_output(run_id, "design_compare", {
        "design_score": result["score"],
        "deviations": result["deviations"],
    })

    usage = result.pop("usage", {})
    if usage:
        save_token_usage(
            run_id, "vision",
            usage.get("model", ""),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cost_usd", 0),
        )

    return f"Design score: {result['score']}/100, {len(result['deviations'])} deviations"


async def _execute_synthesis(run_id: str, ticket_id: str, params: dict) -> str:
    # Read inputs from DB
    jira_out = get_step_output(run_id, "jira_fetch")
    feature_name = jira_out.get("feature_name", ticket_id) if jira_out else ticket_id

    jira = get_jira_data(run_id)
    prd_text = ""
    if jira:
        prd_text = jira.get("prd_text", "") or jira.get("ticket_description", "")

    vision_out = get_step_output(run_id, "design_compare")
    design_result = {
        "score": vision_out.get("design_score", 0) if vision_out else 0,
        "deviations": vision_out.get("deviations", []) if vision_out else [],
        "summary": "",
    }

    result = generate_pm_summary(feature_name, prd_text, design_result)

    save_step_output(run_id, "synthesis", {
        "summary": result["summary"],
        "release_notes": result["release_notes"],
    })

    usage = result.pop("usage", {})
    if usage:
        save_token_usage(
            run_id, "synthesis",
            usage.get("model", ""),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cost_usd", 0),
        )

    return f"Summary generated ({len(result['summary'])} chars)"


async def _execute_slack(run_id: str, ticket_id: str, params: dict) -> str:
    # Read all upstream outputs from DB
    jira_out = get_step_output(run_id, "jira_fetch")
    browser_out = get_step_output(run_id, "browser_crawl")
    vision_out = get_step_output(run_id, "design_compare")
    synthesis_out = get_step_output(run_id, "synthesis")

    feature_name = jira_out.get("feature_name", ticket_id) if jira_out else ticket_id
    design_score = vision_out.get("design_score", 0) if vision_out else 0
    deviations = vision_out.get("deviations", []) if vision_out else []
    summary = synthesis_out.get("summary", "") if synthesis_out else ""
    release_notes = synthesis_out.get("release_notes", "") if synthesis_out else ""
    video_path = browser_out.get("video_path", "") if browser_out else ""

    # Build briefing message
    parts = [
        f"*SkipTheDemo Briefing — {feature_name}*\n",
        f"*Design Score:* {design_score}/100",
    ]

    if deviations:
        parts.append(f"*Deviations:* {len(deviations)} found")

    if summary:
        parts.append(f"\n*Summary:*\n{summary}")

    if release_notes:
        parts.append(f"\n*Release Notes:*\n{release_notes}")

    briefing = "\n".join(parts)

    upload_instruction = ""
    if video_path and os.path.isfile(video_path):
        upload_instruction = f" Also upload the demo video at {video_path}."

    task = (
        f"Post the following PM briefing to the #skipdemo-pm Slack channel:\n\n"
        f"{briefing}\n\n{upload_instruction}"
    )

    result = await run_slack_agent(task)
    _save_usage(run_id, "slack", result)

    save_step_output(run_id, "slack_delivery", {"slack_sent": True})

    return "Slack message delivered"


# ── Handler registry ───────────────────────────

_STEP_HANDLERS = {
    "jira_fetch": _execute_jira,
    "data_cleanup": _execute_data_cleanup,
    "prd_parse": _execute_prd_parse,
    "figma_export": _execute_figma,
    "nav_plan": _execute_nav_plan,
    "browser_crawl": _execute_browser,
    "design_compare": _execute_vision,
    "synthesis": _execute_synthesis,
    "slack_delivery": _execute_slack,
}
