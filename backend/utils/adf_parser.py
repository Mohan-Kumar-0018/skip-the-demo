"""Convert Atlassian Document Format (ADF) JSON to plain text."""

from __future__ import annotations

import ast
import json
from typing import Any


def adf_to_text(adf: dict | str) -> str:
    """Recursively walk an ADF document and return plain text.

    Accepts either a parsed dict, a JSON string, or a Python repr string
    (single-quoted dicts from ``str(dict)``).  Falls back to returning
    the input unchanged when it isn't valid ADF.
    """
    if isinstance(adf, str):
        # Try JSON first (double quotes), then Python literal (single quotes)
        for parser in (json.loads, ast.literal_eval):
            try:
                adf = parser(adf)
                break
            except (json.JSONDecodeError, TypeError, ValueError, SyntaxError):
                continue
        else:
            return adf  # already plain text or unparseable

    if not isinstance(adf, dict) or "type" not in adf:
        return str(adf)

    return _walk(adf).strip()


def _walk(node: dict[str, Any]) -> str:
    ntype = node.get("type", "")

    # Leaf: plain text
    if ntype == "text":
        return node.get("text", "")

    # Leaf: hard break
    if ntype == "hardBreak":
        return "\n"

    # Leaf: inline card (Jira smart link)
    if ntype == "inlineCard":
        attrs = node.get("attrs", {})
        return attrs.get("url", "")

    # Leaf: media / attachment placeholder
    if ntype in ("media", "mediaInline", "mediaGroup", "mediaSingle"):
        return "[attachment]"

    # Leaf: emoji
    if ntype == "emoji":
        attrs = node.get("attrs", {})
        return attrs.get("text", attrs.get("shortName", ""))

    # Leaf: mention
    if ntype == "mention":
        attrs = node.get("attrs", {})
        return f"@{attrs.get('text', '')}"

    # Block: heading
    if ntype == "heading":
        children_text = _walk_children(node)
        return f"\n{children_text}\n"

    # Block: paragraph
    if ntype == "paragraph":
        return _walk_children(node) + "\n"

    # Block: bullet / ordered list
    if ntype in ("bulletList", "orderedList"):
        items = []
        for i, child in enumerate(node.get("content", []), 1):
            prefix = "- " if ntype == "bulletList" else f"{i}. "
            item_text = _walk_children(child).strip()
            items.append(f"{prefix}{item_text}")
        return "\n".join(items) + "\n"

    # Block: list item (handled inline by list parent, but support standalone)
    if ntype == "listItem":
        return _walk_children(node)

    # Block: code block
    if ntype == "codeBlock":
        return _walk_children(node) + "\n"

    # Block: blockquote
    if ntype == "blockquote":
        inner = _walk_children(node).strip()
        lines = [f"> {line}" for line in inner.split("\n")]
        return "\n".join(lines) + "\n"

    # Block: table
    if ntype == "table":
        return _walk_children(node)

    if ntype == "tableRow":
        cells = []
        for child in node.get("content", []):
            cells.append(_walk_children(child).strip())
        return " | ".join(cells) + "\n"

    if ntype in ("tableCell", "tableHeader"):
        return _walk_children(node)

    # Block: rule / divider
    if ntype == "rule":
        return "\n---\n"

    # Default: recurse into children
    return _walk_children(node)


def _walk_children(node: dict[str, Any]) -> str:
    parts = []
    for child in node.get("content", []):
        parts.append(_walk(child))
    return "".join(parts)
