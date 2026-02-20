from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from agent_runner import calc_cost
from db.models import save_plan, save_token_usage
from tools.kb_tools import get_knowledge, search_knowledge

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

PLANNER_SYSTEM_PROMPT = """\
You are the SkipTheDemo pipeline planner. Given a Jira ticket ID and optional context \
(staging URLs, credentials, project info from the knowledge base), produce an execution plan \
as a JSON array of steps.

Available agents:
- jira: Fetches ticket info, PRD attachments, design files, subtasks, comments from Jira.
- internal: Internal processing step (e.g. PDF parsing, data extraction). No LLM call needed.
- figma: Exports design images from Figma links found in the ticket.
- browser: Explores a staging URL with Playwright, takes screenshots, records demo video.
- vision: Compares design images against screenshots using Claude Vision.
- synthesis: Generates PM summary and release notes using Claude.
- slack: Posts briefing message and uploads video to Slack.

Rules:
1. jira_fetch is ALWAYS the first step.
2. prd_parse depends on jira_fetch (extracts text from downloaded PDFs).
3. figma_export depends on jira_fetch (needs Figma URLs from ticket).
4. browser_crawl depends on jira_fetch (needs staging URL).
5. design_compare depends on browser_crawl and figma_export (needs screenshots + design).
6. synthesis depends on design_compare and prd_parse (needs scores + PRD text).
7. slack_delivery depends on synthesis (needs the complete briefing).

Output ONLY a JSON array. Each element must have:
- step_order (int, 1-based)
- step_name (string, one of: jira_fetch, prd_parse, figma_export, browser_crawl, design_compare, synthesis, slack_delivery)
- agent (string, one of: jira, internal, figma, browser, vision, synthesis, slack)
- params (object, any extra parameters for the step)
- depends_on (array of step_name strings this step waits for)

Do not include markdown fences or extra text — output raw JSON only."""


async def create_plan(run_id: str, ticket_id: str) -> list[dict[str, Any]]:
    """Single-shot Claude call to produce the execution plan, then save it to DB."""

    # Gather KB context to feed the planner
    kb_context_parts: list[str] = []

    staging_urls = get_knowledge("staging_urls")
    if isinstance(staging_urls, dict) and "error" not in staging_urls:
        kb_context_parts.append(f"Known staging URLs: {json.dumps(staging_urls)}")

    credentials = get_knowledge("credentials")
    if isinstance(credentials, dict) and "error" not in credentials:
        kb_context_parts.append(f"Known credentials: {json.dumps(credentials)}")

    project_hits = search_knowledge(ticket_id.split("-")[0] if "-" in ticket_id else ticket_id)
    if project_hits and "message" not in project_hits[0]:
        kb_context_parts.append(f"Project info: {json.dumps(project_hits)}")

    kb_context = "\n".join(kb_context_parts) if kb_context_parts else "No knowledge base context available."

    user_message = (
        f"Create an execution plan for Jira ticket {ticket_id}.\n"
        f"Run ID: {run_id}\n"
        f"Output directory: outputs/{run_id}/\n\n"
        f"Knowledge base context:\n{kb_context}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=PLANNER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # Track token usage
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = calc_cost(MODEL, input_tokens, output_tokens)
    save_token_usage(run_id, "planner", MODEL, input_tokens, output_tokens, cost)

    # Parse the plan from response
    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    steps = json.loads(text)

    # Save plan to DB
    save_plan(run_id, steps)
    logger.info("Plan created for run %s: %d steps", run_id, len(steps))

    return steps
