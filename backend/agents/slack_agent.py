from __future__ import annotations

import os
from typing import Any

from slack_sdk import WebClient

client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
CHANNEL = os.getenv("SLACK_CHANNEL", "#skipdemo-pm")


def send_pm_briefing(results: dict[str, Any]) -> None:
    score = results["design_score"]
    if score >= 90:
        emoji = "\U0001f7e2"  # green circle
    elif score >= 75:
        emoji = "\U0001f7e1"  # yellow circle
    else:
        emoji = "\U0001f534"  # red circle

    devs = "\n".join(
        f"  \u26a0\ufe0f {d['description']}" for d in results["deviations"]
    ) or "  \u2705 No deviations \u2014 feature matches design."

    message = (
        "\U0001f680 *SkipTheDemo \u2014 PM Briefing Ready*\n\n"
        f"*Feature:* {results['feature_name']}\n\n"
        f"{emoji} *Design Accuracy: {score}/100*\n"
        f"{devs}\n\n"
        f"\U0001f4cb *Summary*\n{results['summary']}\n\n"
        f"\U0001f4dd *Release Notes*\n{results['release_notes']}\n\n"
        "\U0001f4f9 Demo recording attached in thread."
    )

    res = client.chat_postMessage(channel=CHANNEL, text=message)
    ts = res["ts"]

    if results.get("video_path"):
        client.files_upload_v2(
            channel=CHANNEL,
            thread_ts=ts,
            file=results["video_path"],
            title=f"{results['feature_name']} \u2014 Demo Recording",
        )
