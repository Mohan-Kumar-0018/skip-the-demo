from __future__ import annotations

import json
import os
from typing import Any

KB_PATH = os.path.join(os.path.dirname(__file__), "..", "knowledge_base.json")


def _load_kb() -> dict[str, Any]:
    with open(KB_PATH) as f:
        return json.load(f)


def get_knowledge(category: str, key: str | None = None) -> Any:
    """Retrieve a category or a specific key within a category.

    Examples:
        get_knowledge("staging_urls")                -> all staging URLs
        get_knowledge("staging_urls", "supplier-discovery") -> single entry
        get_knowledge("credentials", "staging")      -> staging login creds
    """
    kb = _load_kb()
    section = kb.get(category)
    if section is None:
        return {"error": f"Unknown category: {category}. Available: {list(kb.keys())}"}
    if key is None:
        return section
    entry = section.get(key)
    if entry is None:
        return {"error": f"Key '{key}' not found in '{category}'. Available: {list(section.keys())}"}
    return entry


def search_knowledge(query: str) -> list[dict[str, Any]]:
    """Search across the entire knowledge base for entries matching a query string.

    Does a case-insensitive match against keys, values, and descriptions.
    """
    kb = _load_kb()
    query_lower = query.lower()
    results = []

    for category, entries in kb.items():
        if isinstance(entries, dict):
            for key, value in entries.items():
                searchable = json.dumps({key: value}).lower()
                if query_lower in searchable:
                    results.append({"category": category, "key": key, "data": value})

    return results if results else [{"message": f"No results found for '{query}'."}]
