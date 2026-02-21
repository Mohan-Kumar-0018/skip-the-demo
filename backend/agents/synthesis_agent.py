from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from agent_runner import calc_cost

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)


def generate_pm_summary(
    feature_name: str, prd_text: str, design_result: dict[str, Any]
) -> dict[str, Any]:
    logger.info("Synthesis agent: feature=%s, prd=%d chars, score=%s", feature_name, len(prd_text), design_result.get("score"))

    if not feature_name:
        return {"summary": "No feature name provided", "release_notes": "", "error_code": "NO_FEATURE_NAME", "usage": {}}

    deviations = "\n".join(
        f"- [{d['severity'].upper()}] {d['description']}"
        for d in design_result.get("deviations", [])
    )

    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are writing an automated PM briefing for a completed software feature.\n\n"
                        f"Feature name: {feature_name}\n"
                        f"Design accuracy score: {design_result['score']}/100\n"
                        "Deviations from design:\n"
                        f"{deviations if deviations else 'None — feature matches design perfectly.'}\n\n"
                        f"PRD (first 3000 chars):\n{prd_text[:3000]}\n\n"
                        "Write two things:\n\n"
                        "1. SUMMARY — 3-4 sentences in plain product language for a PM.\n"
                        "   Mention what was built, the design score, and highlight any significant deviations.\n"
                        "   Do NOT use engineering jargon.\n\n"
                        "2. RELEASE NOTES — Professional, user-facing release notes in markdown.\n"
                        "   Use ## heading with the feature name, then bullet points.\n"
                        "   Write for an end user, not an engineer. Keep it punchy and positive.\n\n"
                        "Return ONLY valid JSON — no markdown fences:\n"
                        '{\n  "summary": "...",\n  "release_notes": "..."\n}'
                    ),
                }
            ],
        )
    except anthropic.APIError as e:
        logger.error("Synthesis API call failed: %s", e)
        return {"summary": f"Synthesis failed: {e}", "release_notes": "", "error_code": "API_ERROR", "usage": {}}

    text = response.content[0].text
    logger.info("Synthesis agent response: %d chars", len(text))
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Synthesis agent returned invalid JSON: %s", clean[:300])
        raise ValueError(f"Synthesis agent returned invalid JSON: {clean[:200]}") from exc
    parsed["usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
    }
    return parsed
