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
from tools.kb_tools import get_knowledge, search_knowledge
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
- lookup_knowledge_base: Look up staging URLs, login credentials, and project info from the knowledge base. ALWAYS check this first for staging URLs and credentials before asking the Jira ticket.
- call_jira_agent: Fetches ticket info, PRD, design files, subtasks from Jira
- call_browser_agent: Explores staging apps, takes screenshots, records demo videos
- call_slack_agent: Reads and posts Slack messages
- analyze_design: Compares design files against screenshots for accuracy scoring
- generate_content: Writes PM summary and release notes
- update_progress: Reports progress to the dashboard

Given a ticket ID and run ID, plan and execute the full pipeline:
1. First, use lookup_knowledge_base to find staging URLs and credentials for the project. Then update progress to "jira_fetch/running". Call the Jira agent to fetch the ticket, its attachments (save to outputs/<run_id>/), and subtasks. After, update progress to "jira_fetch/done".
2. Update progress to "browser_crawl/running". Call the browser agent to explore the staging URL (from knowledge base or Jira ticket), navigate flows, take screenshots, and record a video. Include login credentials from the knowledge base if available. The task must include the staging URL and job_id. After, update to "browser_crawl/done".
3. Update progress to "design_compare/running". If a design file was found, call analyze_design with the design file path and screenshot paths. If no design file, skip. Update to "design_compare/done".
4. Update progress to "synthesis/running". Call generate_content with the feature name, PRD text (or ticket description), and design result. Update to "synthesis/done".
5. Update progress to "slack_delivery/running". Call the Slack agent to post the complete PM briefing with all results, and upload the video. Update to "slack_delivery/done".

Be autonomous. Make decisions based on what you find. If there's no staging URL, note it. If there's no design file, score is 0. Always call update_progress at each stage transition.

IMPORTANT: When calling sub-agents, provide detailed task descriptions with all the context they need (URLs, file paths, job IDs, etc)."""

ORCHESTRATOR_TOOLS = [
    {
        "name": "lookup_knowledge_base",
        "description": "Look up information from the knowledge base — staging URLs, login credentials, project config. Use 'get' mode for direct lookup by category/key, or 'search' mode to find entries matching a query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["get", "search"],
                    "description": "'get' for direct category/key lookup, 'search' for keyword search across all entries.",
                },
                "category": {
                    "type": "string",
                    "description": "Category to look up (for 'get' mode). Options: staging_urls, credentials, projects.",
                },
                "key": {
                    "type": "string",
                    "description": "Specific key within the category (optional for 'get' mode).",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for 'search' mode). Matches against keys, values, descriptions.",
                },
            },
            "required": ["mode"],
        },
    },
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
        if name == "lookup_knowledge_base":
            logger.info("Orchestrator calling: lookup_knowledge_base")
            if input["mode"] == "search":
                return search_knowledge(input.get("query", ""))
            return get_knowledge(input.get("category", ""), input.get("key"))

        elif name == "call_jira_agent":
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

        # 4. Collect outputs
        collected = {
            "feature_name": kb_key,
            "design_score": 0,
            "deviations": [],
            "summary": result,
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
