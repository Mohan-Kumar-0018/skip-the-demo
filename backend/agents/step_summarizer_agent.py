from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

from agent_runner import calc_cost
from db.models import save_token_usage

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=3)

SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "You summarize pipeline step results for product managers and non-technical stakeholders. "
    "Write 1-2 sentences (under 280 characters). Be specific — mention ticket names, counts, "
    "scores, and durations where available. Use plain language, no jargon.\n\n"
    "Tone guidelines:\n"
    "- done: confident, factual (e.g. 'Fetched ticket SG-238 with 3 attachments and 2 Figma links.')\n"
    "- failed: brief explanation of what went wrong (e.g. 'Could not export Figma designs — API returned an auth error.')\n"
    "- skipped: explain why it was skipped (e.g. 'Skipped design comparison — no Figma files were attached to the ticket.')\n\n"
    "Return plain text only. No JSON, no markdown, no bullet points."
)


def generate_step_summary(
    run_id: str,
    step_name: str,
    display_name: str,
    status: str,
    result_summary: str | None,
    error: str | None,
    context: dict[str, Any] | None = None,
) -> str:
    """Single-shot Claude Haiku call to generate a PM-readable step summary."""
    parts = [
        f"Step: {display_name} ({step_name})",
        f"Status: {status}",
    ]
    if result_summary:
        parts.append(f"Result: {result_summary}")
    if error:
        parts.append(f"Error: {error}")
    if context:
        parts.append(f"Context: {context}")

    user_msg = "\n".join(parts)

    response = client.messages.create(
        model=SUMMARIZER_MODEL,
        max_tokens=150,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    save_token_usage(
        run_id,
        "step_summarizer",
        SUMMARIZER_MODEL,
        response.usage.input_tokens,
        response.usage.output_tokens,
        calc_cost(SUMMARIZER_MODEL, response.usage.input_tokens, response.usage.output_tokens),
    )

    return response.content[0].text.strip()
