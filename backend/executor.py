from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import anthropic

from agent_runner import calc_cost
from agents.discover_crawl_agent import run_discover_crawl
from agents.figma_agent import run_figma_agent
from agents.jira_agent import run_jira_agent
from agents.slack_agent import run_slack_agent
from agents.synthesis_agent import generate_pm_summary
from agents.demo_video_agent import generate_demo_video
from agents.score_evaluator_agent import evaluate_scores
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
)
from tools.kb_tools import get_knowledge
from utils.adf_parser import adf_to_text
from utils.pdf_parser import extract_text

logger = logging.getLogger(__name__)

# Steps that abort the whole pipeline on failure
CRITICAL_STEPS = {"jira_fetch", "discover_crawl"}


class StepValidationError(Exception):
    """Raised when a critical step produces empty/invalid results."""


def _validate_jira_result(jira_data: dict) -> None:
    ticket = jira_data.get("ticket", {})
    if not ticket.get("title"):
        raise StepValidationError("Jira agent returned no ticket title")


# Progress percentages for each step
STEP_PROGRESS = {
    "jira_fetch": (5, 15),
    "prd_parse": (15, 20),
    "figma_export": (20, 28),
    "discover_crawl": (20, 60),
    "design_compare": (60, 72),
    "demo_video": (60, 72),
    "synthesis": (72, 88),
    "slack_delivery": (88, 98),
}


STEP_LABELS = {
    "jira_fetch": "Fetching Jira ticket...",
    "data_cleanup": "Cleaning up ticket data...",
    "prd_parse": "Parsing PRD...",
    "figma_export": "Exporting Figma designs...",
    "discover_crawl": "Discovering and crawling app...",
    "design_compare": "Comparing designs...",
    "demo_video": "Generating demo video...",
    "synthesis": "Generating summary...",
    "slack_delivery": "Delivering to Slack...",
}


_panel_client = anthropic.Anthropic(max_retries=3)


def _resolve_panel(run_id: str, context_texts: list[str]) -> str | None:
    """Use a lightweight Claude call to identify which KB panel the context refers to."""
    urls = get_knowledge("staging_urls")
    if not isinstance(urls, dict) or "error" in urls:
        return None
    kb_keys = list(urls.keys())
    if not kb_keys:
        return None

    context = "\n---\n".join(t[:500] for t in context_texts if t)
    if not context.strip():
        return None

    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    try:
        response = _panel_client.messages.create(
            model=model,
            max_tokens=50,
            temperature=0,
            messages=[{"role": "user", "content": (
                f"Available staging app keys: {', '.join(kb_keys)}\n\n"
                f"Context from a Jira ticket:\n{context}\n\n"
                "Which key does this context refer to? "
                "Return ONLY JSON: {\"key\": \"the-key\"} or {\"key\": null} if ambiguous."
            )}],
        )
    except Exception as e:
        logger.warning("[%s] panel resolver failed: %s", run_id, e)
        return None

    save_token_usage(
        run_id, "panel_resolver", model,
        response.usage.input_tokens, response.usage.output_tokens,
        calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
    )

    text = response.content[0].text.strip()
    try:
        parsed = json.loads(text.replace("```json", "").replace("```", "").strip())
        key = parsed.get("key")
        if key and key in kb_keys:
            return key
    except (json.JSONDecodeError, AttributeError):
        logger.warning("[%s] panel resolver returned unparseable: %s", run_id, text[:100])
    return None


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
    update_run(run_id, label, 0)  # progress updated by scheduler

    try:
        handler = _STEP_HANDLERS.get(step_name)
        if handler is None:
            logger.warning("No handler for step %s, skipping", step_name)
            update_plan_step(run_id, step_name, "skipped", result_summary="No handler")
            return "No handler"

        result_summary = await handler(run_id, ticket_id, params)

        update_plan_step(run_id, step_name, "done", result_summary=result_summary)

        # Update feature_name on run if jira provided it
        jira_out = get_step_output(run_id, "jira_fetch")
        if jira_out and jira_out.get("feature_name"):
            update_run(run_id, label, 0, feature_name=jira_out["feature_name"])

        return result_summary

    except Exception as e:
        error_msg = str(e)
        logger.exception("Step %s failed for run %s", step_name, run_id)
        update_plan_step(run_id, step_name, "failed", error=error_msg)

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
    logger.info("[%s] jira_fetch: starting for ticket %s", run_id, ticket_id)
    task = (
        f"Fetch all details for Jira ticket {ticket_id} including subtasks, "
        f"comments, and all attachments. Save attachments to outputs/{run_id}/prd/."
    )
    result = await run_jira_agent(task)
    _save_usage(run_id, "jira", result)

    jira_data = result["data"]
    _validate_jira_result(jira_data)
    ticket = jira_data.get("ticket", {})
    logger.info("[%s] jira_fetch: got ticket '%s', %d attachments, %d subtasks", run_id, ticket.get("title", ""), len(jira_data.get("attachments", [])), len(jira_data.get("subtasks", [])))

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
    desc_str = adf_to_text(str(ticket.get("description", "")))
    design_links.extend(re.findall(figma_pattern, desc_str))
    for comment in jira_data.get("comments", []):
        design_links.extend(re.findall(figma_pattern, comment.get("body", "")))
    design_links = list(set(design_links))

    # Abort if ticket has neither design links nor PRD
    if not design_links and not prd_text:
        raise StepValidationError(
            "Jira ticket has no design links (Figma) and no PRD attachments. "
            "Both are required to proceed."
        )

    # Compute subtask summary
    subtasks = jira_data.get("subtasks", [])
    done_statuses = {"done", "closed", "resolved"}
    completed = [s for s in subtasks if s.get("status", "").lower() in done_statuses]
    pending = [s for s in subtasks if s.get("status", "").lower() not in done_statuses]
    total = len(subtasks)
    completed_count = len(completed)
    task_summary = f"{completed_count}/{total} subtasks completed"
    if pending:
        pending_names = ", ".join(s.get("summary", s.get("key", "?")) for s in pending)
        task_summary += f" — pending: {pending_names}"

    save_jira_data(run_id, {
        "ticket_title": ticket.get("title", ""),
        "ticket_description": desc_str,
        "staging_url": ticket.get("staging_url", ""),
        "ticket_status": ticket.get("status", ""),
        "assignee": ticket.get("assignee", ""),
        "subtasks": subtasks,
        "attachments": jira_data.get("attachments", []),
        "comments": jira_data.get("comments", []),
        "design_links": design_links,
        "task_summary": task_summary,
        "pending_subtasks": pending,
    })

    # Resolve which staging panel this ticket refers to
    panel_texts = [desc_str, ticket.get("title", "")]
    panel_texts.extend(c.get("body", "") for c in jira_data.get("comments", []))
    detected_panel = _resolve_panel(run_id, panel_texts)

    # Fallback: try matching staging URL from the ticket against KB
    if not detected_panel:
        staging_url = ticket.get("staging_url", "")
        if staging_url:
            all_urls = get_knowledge("staging_urls")
            if isinstance(all_urls, dict) and "error" not in all_urls:
                for key, entry in all_urls.items():
                    if isinstance(entry, dict) and entry.get("url") == staging_url:
                        detected_panel = key
                        break

    if not detected_panel:
        raise StepValidationError(
            "Could not determine which staging panel to browse from ticket context, "
            "Figma designs, or knowledge base. Ensure the ticket has a staging URL "
            "or recognizable panel reference."
        )

    logger.info("[%s] jira_fetch: detected panel '%s'", run_id, detected_panel)

    feature_name = ticket.get("title", ticket_id)
    save_step_output(run_id, "jira_fetch", {
        "feature_name": feature_name,
        "prd_text": prd_text,
        "detected_panel": detected_panel,
    })

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
    jira_out = get_step_output(run_id, "jira_fetch")
    prd_text = jira_out.get("prd_text", "") if jira_out else ""

    if not prd_text:
        jira = get_jira_data(run_id)
        prd_text = jira.get("ticket_description", "") if jira else ""
        if prd_text:
            save_step_output(run_id, "prd_parse", {"prd_text": prd_text})
            return f"No PRD found, using ticket description ({len(prd_text)} chars)"
        save_step_output(run_id, "prd_parse", {"prd_text": ""})
        return "No PRD text or ticket description found"

    save_step_output(run_id, "prd_parse", {"prd_text": prd_text})
    return f"PRD text available ({len(prd_text)} chars)"


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

    all_exported: list[dict] = []
    all_errors: list[dict] = []
    primary_url = design_links[0]
    primary_file_info: dict = {}
    primary_node_info: dict = {}

    for figma_url in design_links:
        logger.info("[%s] figma_export: processing link %s", run_id, figma_url)
        task = (
            f"Extract the design from this Figma link: {figma_url}. "
            f"Save exported images to outputs/{run_id}/figma/."
        )
        result = await run_figma_agent(task)
        _save_usage(run_id, "figma", result)

        figma_data = result["data"]
        all_exported.extend(figma_data.get("exported", []))
        all_errors.extend(figma_data.get("errors", []))
        if figma_url == primary_url:
            primary_file_info = figma_data.get("file_info", {})
            primary_node_info = figma_data.get("node_info", {})

    logger.info("[%s] figma_export: %d images exported from %d links, %d errors",
                run_id, len(all_exported), len(design_links), len(all_errors))

    # Resolve which staging panel the Figma designs refer to
    figma_texts = [primary_file_info.get("name", ""), primary_node_info.get("name", "")]
    detected_panel = _resolve_panel(run_id, figma_texts)
    if detected_panel:
        logger.info("[%s] figma_export: detected panel '%s'", run_id, detected_panel)

    save_figma_data(run_id, {
        "figma_url": primary_url,
        "file_name": primary_file_info.get("name", ""),
        "file_last_modified": primary_file_info.get("last_modified", ""),
        "node_name": primary_node_info.get("name", ""),
        "exported_images": all_exported,
        "export_errors": all_errors,
    })

    save_step_output(run_id, "figma_export", {
        "detected_panel": detected_panel,
    })

    return f"Exported {len(all_exported)} images from {len(design_links)} Figma links"


async def _execute_discover_crawl(run_id: str, ticket_id: str, params: dict) -> str:
    logger.info("[%s] discover_crawl: starting", run_id)

    # Resolve kb_key from upstream steps
    kb_key: str | None = None

    # 1. Check detected_panel from jira_fetch
    jira_out = get_step_output(run_id, "jira_fetch")
    if jira_out:
        kb_key = jira_out.get("detected_panel")

    # 2. Check detected_panel from figma_export
    if not kb_key:
        figma_out = get_step_output(run_id, "figma_export")
        if figma_out:
            kb_key = figma_out.get("detected_panel")

    # 3. Try to find KB entry by matching staging URL from Jira data
    if not kb_key:
        jira = get_jira_data(run_id)
        staging_url = jira.get("staging_url", "") if jira else ""
        if staging_url:
            all_urls = get_knowledge("staging_urls")
            if isinstance(all_urls, dict) and "error" not in all_urls:
                for key, entry in all_urls.items():
                    if isinstance(entry, dict) and entry.get("url") == staging_url:
                        kb_key = key
                        break

    if not kb_key:
        raise StepValidationError(
            "No staging panel found — checked Jira ticket, Figma export, and knowledge base. "
            "Add a staging URL to the Jira ticket or knowledge base before running the pipeline."
        )

    logger.info("[%s] discover_crawl: resolved kb_key=%s", run_id, kb_key)

    # Check for Figma images
    figma_images_dir = f"outputs/{run_id}/figma"
    has_figma = os.path.isdir(figma_images_dir) and any(
        f.lower().endswith(".png") for f in os.listdir(figma_images_dir)
    )
    if not has_figma:
        figma_images_dir = None

    output_dir = f"outputs/{run_id}"

    result = await run_discover_crawl(run_id, kb_key, figma_images_dir, output_dir)
    _save_usage(run_id, "discover_crawl", result)

    # Extract crawl data from nested result
    crawl_data = result.get("data", {}).get("crawl", {}).get("data", {})
    screenshot_paths = crawl_data.get("screenshot_paths", [])
    video_path_raw = crawl_data.get("video_path", "")

    logger.info(
        "[%s] discover_crawl: %d screenshots, video=%s",
        run_id, len(screenshot_paths), bool(video_path_raw),
    )

    # Save browser data (same schema as old handler)
    save_browser_data(run_id, {
        "urls_visited": [],
        "page_titles": [],
        "screenshot_paths": screenshot_paths,
        "video_path": video_path_raw or "",
        "page_content": "",
        "interactive_elements": crawl_data.get("interactive_elements", []),
    })

    # Collect screenshots and video from filesystem
    screenshots: list[str] = []
    video_path = ""
    screenshots_dir = f"outputs/{run_id}/screenshots"
    video_dir = f"outputs/{run_id}/video"
    if os.path.isdir(screenshots_dir):
        screenshots = [
            f"{screenshots_dir}/{f}"
            for f in sorted(os.listdir(screenshots_dir))
            if f.endswith(".png")
        ]
    if os.path.isdir(video_dir):
        video_files = [f for f in os.listdir(video_dir) if f.endswith((".webm", ".mov"))]
        if video_files:
            video_path = f"{video_dir}/{video_files[0]}"

    save_step_output(run_id, "discover_crawl", {
        "screenshots": screenshots,
        "video_path": video_path,
    })

    return result.get("summary", "") if isinstance(result.get("summary"), str) else json.dumps(result.get("summary", ""))


async def _execute_score_evaluator(run_id: str, ticket_id: str, params: dict) -> str:
    logger.info("[%s] design_compare: starting (score_evaluator)", run_id)

    figma_dir = f"outputs/{run_id}/figma"
    screenshots_dir = f"outputs/{run_id}/screenshots"

    has_figma = os.path.isdir(figma_dir) and any(
        f.lower().endswith(".png") for f in os.listdir(figma_dir)
    )
    has_screenshots = os.path.isdir(screenshots_dir) and any(
        f.lower().endswith(".png") for f in os.listdir(screenshots_dir)
    )

    if not has_figma or not has_screenshots:
        save_step_output(run_id, "design_compare", {
            "overall_score": 0,
            "design_score": 0,
            "screen_coverage": {},
            "visual_comparison": {},
            "missing_screens": {},
            "deviations": [],
            "summary": "Skipped — no design files or no screenshots",
            "additional_analysis": {},
        })
        return "Skipped — no design files or no screenshots"

    try:
        result = evaluate_scores(screenshots_dir, figma_dir)
    except Exception:
        logger.exception("[%s] design_compare: score_evaluator failed", run_id)
        raise

    overall_score = result.get("overall_score", 0)
    top_deviations = (
        result.get("additional_analysis", {}).get("top_deviations", [])
    )

    logger.info(
        "[%s] design_compare: overall_score=%d, coverage=%d, visual=%d, %d top deviations",
        run_id,
        overall_score,
        result.get("screen_coverage", {}).get("score", 0),
        result.get("visual_comparison", {}).get("score", 0),
        len(top_deviations),
    )

    save_step_output(run_id, "design_compare", {
        "overall_score": overall_score,
        "design_score": overall_score,  # backward compat for synthesis/slack
        "screen_coverage": result.get("screen_coverage", {}),
        "visual_comparison": result.get("visual_comparison", {}),
        "missing_screens": result.get("missing_screens", {}),
        "deviations": top_deviations,
        "summary": result.get("summary", ""),
        "additional_analysis": result.get("additional_analysis", {}),
    })

    usage = result.get("usage", {})
    if usage:
        save_token_usage(
            run_id, "score_evaluator",
            usage.get("model", ""),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cost_usd", 0),
        )

    return f"Design score: {overall_score}/100, {len(top_deviations)} deviations ({usage.get('api_calls', 0)} API calls)"


async def _execute_demo_video(run_id: str, ticket_id: str, params: dict) -> str:
    logger.info("[%s] demo_video: starting", run_id)

    browser_out = get_step_output(run_id, "discover_crawl")
    video_path = browser_out.get("video_path", "") if browser_out else ""
    screenshots = browser_out.get("screenshots", []) if browser_out else []

    if not video_path or not os.path.isfile(video_path):
        save_step_output(run_id, "demo_video", {
            "demo_video_path": "",
            "processing_stats": {},
        })
        return "Skipped — no video recording available"

    # Load action log if saved by browser tools
    action_log: list[dict] = []
    action_log_path = f"outputs/{run_id}/video/action_log.json"
    if os.path.isfile(action_log_path):
        with open(action_log_path) as f:
            action_log = json.load(f)

    jira_out = get_step_output(run_id, "jira_fetch")
    feature_context = jira_out.get("feature_name", "") if jira_out else ""

    output_dir = f"outputs/{run_id}/demo_video"

    try:
        result = await generate_demo_video(
            video_path,
            action_log,
            screenshot_paths=screenshots or None,
            feature_context=feature_context,
            output_dir=output_dir,
        )
    except Exception:
        logger.exception("[%s] demo_video: generate_demo_video failed", run_id)
        raise

    demo_video_path = result.get("output_video_path", "")
    stats = result.get("processing_stats", {})

    logger.info("[%s] demo_video: output=%s, stats=%s", run_id, demo_video_path, stats)

    save_step_output(run_id, "demo_video", {
        "demo_video_path": demo_video_path,
        "processing_stats": stats,
    })

    usage = result.get("usage", {})
    if usage:
        save_token_usage(
            run_id, "demo_video",
            usage.get("model", ""),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cost_usd", 0),
        )

    deduped = stats.get("deduped_duration_s", 0)
    return f"Demo video generated ({deduped}s, {stats.get('click_animations', 0)} click animations)"


async def _execute_synthesis(run_id: str, ticket_id: str, params: dict) -> str:
    logger.info("[%s] synthesis: starting", run_id)
    # Read inputs from DB
    jira_out = get_step_output(run_id, "jira_fetch")
    feature_name = jira_out.get("feature_name", ticket_id) if jira_out else ticket_id
    prd_text = jira_out.get("prd_text", "") if jira_out else ""

    vision_out = get_step_output(run_id, "design_compare")
    design_result = {
        "score": vision_out.get("overall_score", vision_out.get("design_score", 0)) if vision_out else 0,
        "deviations": vision_out.get("deviations", []) if vision_out else [],
        "summary": vision_out.get("summary", "") if vision_out else "",
        "screen_coverage": vision_out.get("screen_coverage", {}) if vision_out else {},
        "visual_comparison": vision_out.get("visual_comparison", {}) if vision_out else {},
        "missing_screens": vision_out.get("missing_screens", {}) if vision_out else {},
        "additional_analysis": vision_out.get("additional_analysis", {}) if vision_out else {},
    }

    try:
        result = generate_pm_summary(feature_name, prd_text, design_result)
    except Exception:
        logger.exception("[%s] synthesis: agent failed", run_id)
        raise

    # Handle graceful error returns from synthesis agent
    if result.get("error_code"):
        logger.warning("[%s] synthesis: agent returned error: %s", run_id, result.get("summary"))
        save_step_output(run_id, "synthesis", {
            "summary": result.get("summary", "Synthesis failed"),
            "release_notes": "",
        })
        return f"Synthesis error: {result.get('summary', 'unknown')}"

    logger.info("[%s] synthesis: summary=%d chars, release_notes=%d chars", run_id, len(result.get("summary", "")), len(result.get("release_notes", "")))
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
    browser_out = get_step_output(run_id, "discover_crawl")
    vision_out = get_step_output(run_id, "design_compare")
    synthesis_out = get_step_output(run_id, "synthesis")
    demo_video_out = get_step_output(run_id, "demo_video")

    feature_name = jira_out.get("feature_name", ticket_id) if jira_out else ticket_id
    design_score = vision_out.get("overall_score", vision_out.get("design_score", 0)) if vision_out else 0
    deviations = vision_out.get("deviations", []) if vision_out else []
    summary = synthesis_out.get("summary", "") if synthesis_out else ""
    release_notes = synthesis_out.get("release_notes", "") if synthesis_out else ""

    # Prefer polished demo video over raw recording
    video_path = ""
    if demo_video_out and demo_video_out.get("demo_video_path"):
        video_path = demo_video_out["demo_video_path"]
    if not video_path and browser_out:
        video_path = browser_out.get("video_path", "")

    # Build briefing message
    if design_score >= 80:
        score_emoji = "🟢"
    elif design_score >= 60:
        score_emoji = "🟡"
    else:
        score_emoji = "🔴"

    parts = [
        f"*SkipTheDemo Briefing — {feature_name}*\n",
        f"*Design Score:* {score_emoji} {design_score}/100",
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
    "discover_crawl": _execute_discover_crawl,
    "design_compare": _execute_score_evaluator,
    "demo_video": _execute_demo_video,
    "synthesis": _execute_synthesis,
    "slack_delivery": _execute_slack,
}
