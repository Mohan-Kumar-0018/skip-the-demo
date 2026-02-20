from __future__ import annotations

import json
import os
from typing import Any

import anthropic

client = anthropic.Anthropic()


def generate_pm_summary(
    feature_name: str, prd_text: str, design_result: dict[str, Any]
) -> dict[str, str]:
    deviations = "\n".join(
        f"- [{d['severity'].upper()}] {d['description']}"
        for d in design_result.get("deviations", [])
    )

    response = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
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

    text = response.content[0].text
    clean = text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)
