from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse

import requests

FIGMA_TOKEN = os.getenv("FIGMA_ACCESS_TOKEN", "")
BASE_URL = "https://api.figma.com"


def _headers() -> dict[str, str]:
    return {"X-Figma-Token": FIGMA_TOKEN}


def parse_figma_url(url: str) -> dict[str, str]:
    """Extract file_key and node_id from a Figma URL.

    Handles URLs like:
      https://www.figma.com/design/<file_key>/Title?node-id=13-1134
      https://www.figma.com/file/<file_key>/Title?node-id=13-1134
    Node IDs use '-' in URLs but ':' in the API (13-1134 → 13:1134).
    """
    parsed = urlparse(url)
    # Path: /design/<file_key>/... or /file/<file_key>/...
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        return {"error": f"Cannot parse Figma URL: {url}"}

    file_key = parts[1]

    # Extract node-id from query params
    node_id = None
    if parsed.query:
        for param in parsed.query.split("&"):
            if param.startswith("node-id="):
                raw = unquote(param.split("=", 1)[1])
                # Convert dash to colon: 13-1134 → 13:1134
                node_id = raw.replace("-", ":")
                break

    result = {"file_key": file_key}
    if node_id:
        result["node_id"] = node_id
    return result


def get_figma_file_info(file_key: str) -> dict:
    """GET /v1/files/:key — returns file name, last modified, pages list."""
    res = requests.get(f"{BASE_URL}/v1/files/{file_key}?depth=1", headers=_headers())
    if not res.ok:
        return {"error": f"Figma API error {res.status_code}: {res.text[:200]}"}
    data = res.json()
    pages = []
    for page in data.get("document", {}).get("children", []):
        pages.append({"id": page["id"], "name": page["name"], "type": page["type"]})
    return {
        "name": data.get("name", ""),
        "last_modified": data.get("lastModified", ""),
        "pages": pages,
    }


def get_figma_node_info(file_key: str, node_id: str) -> dict:
    """GET /v1/files/:key/nodes?ids=X — returns node name, type, children."""
    res = requests.get(
        f"{BASE_URL}/v1/files/{file_key}/nodes",
        headers=_headers(),
        params={"ids": node_id},
    )
    if not res.ok:
        return {"error": f"Figma API error {res.status_code}: {res.text[:200]}"}
    data = res.json()
    nodes = data.get("nodes", {})
    node_data = nodes.get(node_id, {}).get("document")
    if not node_data:
        return {"error": f"Node {node_id} not found in file {file_key}"}
    children = []
    for child in node_data.get("children", []):
        children.append({"id": child["id"], "name": child["name"], "type": child["type"]})
    return {
        "id": node_data["id"],
        "name": node_data["name"],
        "type": node_data["type"],
        "children": children,
    }


def export_figma_node(
    file_key: str, node_id: str, output_dir: str, scale: int = 2, format: str = "png"
) -> dict:
    """GET /v1/images/:key?ids=X&format=png&scale=2 — downloads exported image.

    Returns {path, url} on success or {error} on failure.
    """
    os.makedirs(output_dir, exist_ok=True)

    res = requests.get(
        f"{BASE_URL}/v1/images/{file_key}",
        headers=_headers(),
        params={"ids": node_id, "format": format, "scale": scale},
    )
    if not res.ok:
        return {"error": f"Figma API error {res.status_code}: {res.text[:200]}"}

    data = res.json()
    images = data.get("images", {})
    image_url = images.get(node_id)
    if not image_url:
        return {"error": f"No image URL returned for node {node_id}. Response: {data}"}

    # Download the image
    img_res = requests.get(image_url)
    if not img_res.ok:
        return {"error": f"Failed to download image: {img_res.status_code}"}

    # Build filename from node_id: 13:1134 → 13_1134.png
    safe_id = node_id.replace(":", "_")
    filename = f"figma_{safe_id}.{format}"
    path = os.path.join(output_dir, filename)

    with open(path, "wb") as f:
        f.write(img_res.content)

    return {"path": path, "url": image_url}


def _sanitize_filename(name: str) -> str:
    """Convert a node name to a safe filename: lowercase, spaces to underscores, strip special chars."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s-]+", "_", name)
    return name


def export_figma_nodes(
    file_key: str,
    nodes: list[dict],
    output_dir: str,
    scale: int = 2,
    format: str = "png",
) -> dict:
    """Batch-export multiple Figma nodes in a single API call.

    Args:
        nodes: List of {"id": "13:1134", "name": "Dashboard"} dicts.

    Returns {exported: [{name, id, path}], errors: [...]}.
    """
    os.makedirs(output_dir, exist_ok=True)

    ids_str = ",".join(n["id"] for n in nodes)
    res = requests.get(
        f"{BASE_URL}/v1/images/{file_key}",
        headers=_headers(),
        params={"ids": ids_str, "format": format, "scale": scale},
    )
    if not res.ok:
        return {"error": f"Figma API error {res.status_code}: {res.text[:200]}"}

    images = res.json().get("images", {})

    # Build lookup: id → name
    name_map = {n["id"]: n.get("name", n["id"]) for n in nodes}

    exported = []
    errors = []
    for node_id, image_url in images.items():
        name = name_map.get(node_id, node_id)
        if not image_url:
            errors.append({"id": node_id, "name": name, "error": "No image URL returned"})
            continue

        img_res = requests.get(image_url)
        if not img_res.ok:
            errors.append({"id": node_id, "name": name, "error": f"Download failed: {img_res.status_code}"})
            continue

        safe_name = _sanitize_filename(name)
        filename = f"{safe_name}.{format}"
        path = os.path.join(output_dir, filename)

        with open(path, "wb") as f:
            f.write(img_res.content)

        exported.append({"name": name, "id": node_id, "path": path})

    return {"exported": exported, "errors": errors}
