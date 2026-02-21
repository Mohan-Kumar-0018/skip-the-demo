from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import anthropic

from agent_runner import calc_cost

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _b64_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode()


def compare_design_vs_reality(
    design_bytes: bytes, screenshots: list[str]
) -> dict[str, Any]:
    logger.info("Vision compare: %d design bytes, %d screenshots", len(design_bytes), len(screenshots))

    if not design_bytes:
        return {"score": 0, "deviations": [], "summary": "No design image provided", "error_code": "NO_DESIGN", "usage": {}}

    if not screenshots or not os.path.isfile(screenshots[0]):
        return {"score": 0, "deviations": [], "summary": "No screenshots available for comparison", "error_code": "NO_SCREENSHOTS", "usage": {}}

    design_b64 = _b64_bytes(design_bytes)
    actual_b64 = _b64(screenshots[0])

    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": design_b64,
                            },
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": actual_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "First image = original design. Second image = actual built feature.\n\n"
                                "Compare them carefully. Return ONLY valid JSON — no markdown, no explanation:\n"
                                "{\n"
                                '  "score": <integer 0-100, how closely built feature matches design>,\n'
                                '  "deviations": [\n'
                                "    {\n"
                                '      "type": "visual | flow | missing | new",\n'
                                '      "description": "specific human-readable difference",\n'
                                '      "severity": "low | medium | high"\n'
                                "    }\n"
                                "  ],\n"
                                '  "summary": "One sentence overall assessment."\n'
                                "}"
                            ),
                        },
                    ],
                }
            ],
        )
    except anthropic.APIError as e:
        logger.error("Vision API call failed: %s", e)
        return {"score": 0, "deviations": [], "summary": f"Vision API error: {e}", "error_code": "API_ERROR", "usage": {}}

    text = response.content[0].text
    logger.info("Vision agent response: %d chars", len(text))
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Vision agent returned invalid JSON: %s", clean[:300])
        raise ValueError(f"Vision agent returned invalid JSON: {clean[:200]}") from exc
    parsed["usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
    }
    return parsed
