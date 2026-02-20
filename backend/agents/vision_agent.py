from __future__ import annotations

import base64
import json
from typing import Any

import anthropic

client = anthropic.Anthropic()


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _b64_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode()


def compare_design_vs_reality(
    design_bytes: bytes, screenshots: list[str]
) -> dict[str, Any]:
    design_b64 = _b64_bytes(design_bytes)
    actual_b64 = _b64(screenshots[0])

    response = client.messages.create(
        model="claude-sonnet-4-6",
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

    text = response.content[0].text
    clean = text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)
