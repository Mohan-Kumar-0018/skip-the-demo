from __future__ import annotations

import os
from typing import Any

import requests

JIRA_HOST = os.getenv("JIRA_HOST")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN")


def _auth() -> tuple[str | None, str | None]:
    return (JIRA_EMAIL, JIRA_TOKEN)


def get_jira_ticket(ticket_id: str) -> dict[str, Any]:
    """Fetch ticket details from Jira. Returns title, description, staging_url, status, assignee."""
    url = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}"
    res = requests.get(url, auth=_auth()).json()
    f = res["fields"]
    assignee = f.get("assignee") or {}
    return {
        "title": f["summary"],
        "description": f.get("description", ""),
        "staging_url": f.get(os.getenv("JIRA_STAGING_URL_FIELD", ""), ""),
        "status": f.get("status", {}).get("name", ""),
        "assignee": assignee.get("displayName", "Unassigned"),
    }


def get_jira_subtasks(ticket_id: str) -> list[dict[str, str]]:
    """Fetch subtasks for a Jira ticket. Returns list of {key, summary, status}."""
    url = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}"
    res = requests.get(url, auth=_auth()).json()
    subtasks = res["fields"].get("subtasks", [])
    return [
        {
            "key": st["key"],
            "summary": st["fields"]["summary"],
            "status": st["fields"]["status"]["name"],
        }
        for st in subtasks
    ]


def get_jira_attachments(ticket_id: str, output_dir: str) -> list[dict[str, str]]:
    """Download all attachments from a ticket. Saves files to output_dir. Returns list of {filename, path, mime_type, category}."""
    os.makedirs(output_dir, exist_ok=True)
    url = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}"
    res = requests.get(url, auth=_auth()).json()
    attachments = res["fields"].get("attachment", [])

    results = []
    for att in attachments:
        name = att["filename"]
        content = requests.get(att["content"], auth=_auth()).content
        path = os.path.join(output_dir, name)
        with open(path, "wb") as f:
            f.write(content)

        name_lower = name.lower()
        if name_lower.endswith(".pdf") and "design" not in name_lower:
            category = "prd"
        elif name_lower.endswith(".pdf") and "design" in name_lower:
            category = "design"
        elif name_lower.endswith((".png", ".jpg", ".jpeg", ".svg", ".fig")):
            category = "design"
        else:
            category = "other"

        results.append({
            "filename": name,
            "path": path,
            "mime_type": att.get("mimeType", ""),
            "category": category,
        })
    return results


def get_jira_comments(ticket_id: str) -> list[dict[str, str]]:
    """Fetch all comments on a Jira ticket. Returns list of {author, body, created}."""
    url = f"https://{JIRA_HOST}/rest/api/3/issue/{ticket_id}/comment"
    res = requests.get(url, auth=_auth()).json()
    comments = res.get("comments", [])
    results = []
    for c in comments:
        # Extract plain text from ADF body
        body_parts = []
        for block in c.get("body", {}).get("content", []):
            for inline in block.get("content", []):
                if inline.get("type") == "text":
                    body_parts.append(inline["text"])
        results.append({
            "author": c.get("author", {}).get("displayName", "Unknown"),
            "body": " ".join(body_parts),
            "created": c.get("created", ""),
        })
    return results


def add_jira_comment(ticket_id: str, text: str) -> dict[str, str]:
    """Post an ADF-formatted comment on a Jira ticket."""
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
    res = requests.post(url, json=payload, auth=_auth())
    return {"status": "ok" if res.ok else "error", "code": str(res.status_code)}
