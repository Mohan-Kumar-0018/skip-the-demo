from __future__ import annotations

import os
from typing import Any

import requests

JIRA_HOST = os.getenv("JIRA_HOST")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN")


def _auth() -> tuple[str | None, str | None]:
    return (JIRA_EMAIL, JIRA_TOKEN)


def get_ticket(ticket_id: str) -> dict[str, Any]:
    url = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}"
    res = requests.get(url, auth=_auth()).json()
    f = res["fields"]
    return {
        "title": f["summary"],
        "description": f.get("description", ""),
        "staging_url": f.get(os.getenv("JIRA_STAGING_URL_FIELD", ""), ""),
        "attachments": f.get("attachment", []),
    }


def get_attachment_bytes(attachment: dict[str, Any]) -> bytes:
    return requests.get(attachment["content"], auth=_auth()).content


def get_prd_and_design(
    attachments: list[dict[str, Any]],
) -> tuple[bytes | None, bytes | None]:
    """Returns (prd_bytes, design_bytes) — either may be None."""
    prd: bytes | None = None
    design: bytes | None = None
    for att in attachments:
        name = att["filename"].lower()
        content = get_attachment_bytes(att)
        if name.endswith(".pdf"):
            if "design" in name:
                design = content
            else:
                prd = content
        elif name.endswith((".png", ".jpg", ".jpeg")):
            design = content
    return prd, design


def add_comment(ticket_id: str, text: str) -> None:
    url = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}/comment"
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }
    }
    requests.post(url, json=payload, auth=_auth())
