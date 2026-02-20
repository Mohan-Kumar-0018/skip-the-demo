from __future__ import annotations

import json
import logging
import os
from typing import Any

from agent_runner import run_agent_loop
from agents.browser_agent import run_browser_agent
from agents.jira_agent import run_jira_agent
from agents.slack_agent import run_slack_agent
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

ORCHESTRATOR_SYSTEM_PROMPT = """You are the SkipTheDemo orchestrator. You control a team of agents to automate demo video creation, design accuracy scoring, and release notes generation from Jira tickets.

Your tools:
- call_jira_agent: Fetches ticket info, PRD, design files, subtasks from Jira
- call_browser_agent: Explores staging apps, takes screenshots, records demo videos
- call_slack_agent: Reads and posts Slack messages
- analyze_design: Compares design files against screenshots for accuracy scoring
- generate_content: Writes PM summary and release notes
- update_progress: Reports progress to the dashboard

Given a ticket ID and run ID, plan and execute the full pipeline:
1. First, update progress to "jira_fetch/running". Then call the Jira agent to fetch the ticket, its attachments (save to outputs/<run_id>/), and subtasks. After, update progress to "jira_fetch/done".
2. Update progress to "browser_crawl/running". Call the browser agent to explore the staging URL, navigate flows, take screenshots, and record a video. The task must include the staging URL and job_id. After, update to "browser_crawl/done".
3. Update progress to "design_compare/running". If a design file was found, call analyze_design with the design file path and screenshot paths. If no design file, skip. Update to "design_compare/done".
4. Update progress to "synthesis/running". Call generate_content with the feature name, PRD text (or ticket description), and design result. Update to "synthesis/done".
5. Update progress to "slack_delivery/running". Call the Slack agent to post the complete PM briefing with all results, and upload the video. Update to "slack_delivery/done".

Be autonomous. Make decisions based on what you find. If there's no staging URL, note it. If there's no design file, score is 0. Always call update_progress at each stage transition.

IMPORTANT: When calling sub-agents, provide detailed task descriptions with all the context they need (URLs, file paths, job IDs, etc)."""

ORCHESTRATOR_TOOLS = [
    {
        "name": "call_jira_agent",
        "description": "Delegate a task to the Jira agent. It can fetch tickets, subtasks, attachments, and post comments. Returns the agent's summary of what it found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Detailed task description for the Jira agent, including ticket ID and output directory for attachments."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "call_browser_agent",
        "description": "Delegate a task to the Browser agent. It explores web apps, takes screenshots, and records videos. Returns description of what was explored.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Detailed task description including the URL to visit and the job_id for session management."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "call_slack_agent",
        "description": "Delegate a task to the Slack agent. It can read and post messages, upload files. Returns confirmation of actions taken.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Detailed task description including what to post, which channel, file paths for uploads, etc."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "analyze_design",
        "description": "Compare a design file against screenshots using Claude Vision. Returns {score, deviations, summary}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "design_path": {"type": "string", "description": "Path to the design file (PNG/JPG)"},
                "screenshot_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of screenshot file paths to compare against the design",
                },
            },
            "required": ["design_path", "screenshot_paths"],
        },
    },
    {
        "name": "generate_content",
        "description": "Generate PM summary and release notes using Claude. Returns {summary, release_notes}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "feature_name": {"type": "string", "description": "Name of the feature"},
                "prd_text": {"type": "string", "description": "PRD text or ticket description"},
                "design_score": {"type": "integer", "description": "Design accuracy score (0-100)"},
                "deviations": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of design deviations",
                },
                "design_summary": {"type": "string", "description": "One-sentence design comparison summary"},
            },
            "required": ["feature_name", "prd_text", "design_score", "deviations", "design_summary"],
        },
    },
    {
        "name": "update_progress",
        "description": "Update the pipeline progress in the dashboard. Call this at each stage transition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "description": "Current stage label shown to user (e.g. 'Fetching Jira ticket...')"},
                "step_name": {
                    "type": "string",
                    "enum": ["jira_fetch", "prd_parse", "browser_crawl", "design_compare", "synthesis", "slack_delivery"],
                    "description": "The pipeline step name",
                },
                "step_status": {
                    "type": "string",
                    "enum": ["running", "done"],
                    "description": "Status of the step",
                },
                "progress": {"type": "integer", "description": "Progress percentage (0-100)"},
                "feature_name": {"type": "string", "description": "Feature name to display (optional, set when known)"},
            },
            "required": ["stage", "step_name", "step_status", "progress"],
        },
    },
]


def _build_orchestrator_executor(run_id: str, ticket_id: str):
    """Build a tool executor closure that captures run_id and ticket_id."""

    # Mutable state to collect results for saving at the end
    collected: dict[str, Any] = {
        "feature_name": "",
        "design_score": 0,
        "deviations": [],
        "summary": "",
        "release_notes": "",
        "video_path": None,
        "screenshots": [],
        "slack_sent": False,
    }

    async def executor(name: str, input: dict) -> Any:
        if name == "call_jira_agent":
            logger.info("Orchestrator calling: call_jira_agent")
            return await run_jira_agent(input["task"])

        elif name == "call_browser_agent":
            logger.info("Orchestrator calling: call_browser_agent")
            result = await run_browser_agent(input["task"])
            # Collect screenshot and video paths from outputs dir
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
            return result

        elif name == "call_slack_agent":
            logger.info("Orchestrator calling: call_slack_agent")
            result = await run_slack_agent(input["task"])
            collected["slack_sent"] = True
            return result

        elif name == "analyze_design":
            logger.info("Orchestrator calling: analyze_design")
            design_path = input["design_path"]
            screenshot_paths = input["screenshot_paths"]
            with open(design_path, "rb") as f:
                design_bytes = f.read()
            result = compare_design_vs_reality(design_bytes, screenshot_paths)
            collected["design_score"] = result["score"]
            collected["deviations"] = result["deviations"]
            return result

        elif name == "generate_content":
            logger.info("Orchestrator calling: generate_content")
            design_result = {
                "score": input["design_score"],
                "deviations": input["deviations"],
                "summary": input["design_summary"],
            }
            # Try to extract PRD text from file if it looks like a path
            prd_text = input["prd_text"]
            if prd_text.endswith(".pdf") and os.path.isfile(prd_text):
                with open(prd_text, "rb") as f:
                    prd_text = extract_text(f.read())

            result = generate_pm_summary(input["feature_name"], prd_text, design_result)
            collected["feature_name"] = input["feature_name"]
            collected["summary"] = result["summary"]
            collected["release_notes"] = result["release_notes"]
            return result

        elif name == "update_progress":
            logger.info(
                "Orchestrator progress: %s — %s (%d%%)",
                input["step_name"],
                input["step_status"],
                input["progress"],
            )
            feature = input.get("feature_name")
            if feature:
                collected["feature_name"] = feature
            update_run(run_id, input["stage"], input["progress"], feature_name=feature)
            upsert_step(run_id, input["step_name"], input["step_status"])
            return {"status": "ok"}

        else:
            return {"error": f"Unknown tool: {name}"}

    return executor, collected


async def run_pipeline(run_id: str, ticket_id: str) -> None:
    """Main entry point — runs the orchestrator agentic loop."""
    try:
        # Init all steps as pending
        for s in STEPS:
            upsert_step(run_id, s, "pending")

        executor, collected = _build_orchestrator_executor(run_id, ticket_id)

        user_message = (
            f"Run the full SkipTheDemo pipeline for Jira ticket {ticket_id}.\n"
            f"Run ID: {run_id}\n"
            f"Output directory for attachments and screenshots: outputs/{run_id}/\n\n"
            "Execute all steps: fetch from Jira, explore staging app, compare design, "
            "generate content, and deliver to Slack."
        )

        await run_agent_loop(
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            tools=ORCHESTRATOR_TOOLS,
            tool_executor=executor,
            user_message=user_message,
            max_turns=25,
        )

        # Save collected results and mark complete
        save_results(run_id, collected)
        complete_run(run_id)

    except Exception as e:
        logger.exception("Pipeline failed for run %s", run_id)
        fail_run(run_id, str(e))
