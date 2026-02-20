from __future__ import annotations

from typing import Any

from agent_runner import run_agent_loop
from tools.figma_tools import (
    export_figma_node,
    export_figma_nodes,
    get_figma_file_info,
    get_figma_node_info,
    parse_figma_url,
)

SYSTEM_PROMPT = """You are a Figma agent. Given a Figma link, your job is to extract design screens as PNG images.

Steps:
1. Parse the Figma URL to extract the file key and node ID.
2. Get the node info to see its type and children.
3. If the node is a container (SECTION, FRAME, PAGE) with FRAME children, use export_children to batch-export all child frames as individual PNGs. Filter to only FRAME-type children (skip COMPONENT_SET, COMPONENT, INSTANCE types — those are reusable elements, not screens).
4. Also export the parent node itself as a single overview image using export_node_as_image.
5. Report what was downloaded — list all file paths and screen names.

If the URL has no node-id, get the file info first and export the first page."""

TOOLS = [
    {
        "name": "parse_figma_url",
        "description": "Parse a Figma URL to extract the file_key and node_id. Node IDs are converted from URL format (13-1134) to API format (13:1134).",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The Figma URL to parse"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_file_info",
        "description": "Get Figma file metadata — name, last modified, list of pages. Useful to understand what's in the file before exporting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string", "description": "The Figma file key"},
            },
            "required": ["file_key"],
        },
    },
    {
        "name": "get_node_info",
        "description": "Get info about a specific node in a Figma file — name, type, children. Useful to understand the node structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string", "description": "The Figma file key"},
                "node_id": {"type": "string", "description": "The node ID (e.g. '13:1134')"},
            },
            "required": ["file_key", "node_id"],
        },
    },
    {
        "name": "export_children",
        "description": "Batch-export multiple Figma nodes as individual PNG images in a single API call. Pass the list of children (with id and name) from get_node_info. Returns {exported: [{name, id, path}], errors: [...]}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string", "description": "The Figma file key"},
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Node ID (e.g. '13:1135')"},
                            "name": {"type": "string", "description": "Node name (used for filename)"},
                        },
                        "required": ["id", "name"],
                    },
                    "description": "List of nodes to export, each with id and name",
                },
                "output_dir": {"type": "string", "description": "Directory to save the exported images"},
                "scale": {"type": "integer", "description": "Export scale (default 2)", "default": 2},
                "format": {"type": "string", "description": "Image format (default png)", "default": "png"},
            },
            "required": ["file_key", "nodes", "output_dir"],
        },
    },
    {
        "name": "export_node_as_image",
        "description": "Export a single Figma node as a PNG image. Downloads and saves to output_dir. Returns {path, url}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {"type": "string", "description": "The Figma file key"},
                "node_id": {"type": "string", "description": "The node ID to export (e.g. '13:1134')"},
                "output_dir": {"type": "string", "description": "Directory to save the exported image"},
                "scale": {"type": "integer", "description": "Export scale (default 2)", "default": 2},
                "format": {"type": "string", "description": "Image format: png, jpg, svg, pdf (default png)", "default": "png"},
            },
            "required": ["file_key", "node_id", "output_dir"],
        },
    },
]


async def _execute_tool(name: str, input: dict) -> str | dict | list:
    if name == "parse_figma_url":
        return parse_figma_url(input["url"])
    elif name == "get_file_info":
        return get_figma_file_info(input["file_key"])
    elif name == "get_node_info":
        return get_figma_node_info(input["file_key"], input["node_id"])
    elif name == "export_children":
        return export_figma_nodes(
            file_key=input["file_key"],
            nodes=input["nodes"],
            output_dir=input["output_dir"],
            scale=input.get("scale", 2),
            format=input.get("format", "png"),
        )
    elif name == "export_node_as_image":
        return export_figma_node(
            file_key=input["file_key"],
            node_id=input["node_id"],
            output_dir=input["output_dir"],
            scale=input.get("scale", 2),
            format=input.get("format", "png"),
        )
    else:
        return {"error": f"Unknown tool: {name}"}


async def run_figma_agent(task: str) -> dict[str, Any]:
    """Run the Figma agent. Returns {summary: str, data: dict} with collected structured data."""
    collected: dict[str, Any] = {
        "parsed_url": {},
        "file_info": {},
        "node_info": {},
        "exported": [],
        "errors": [],
    }

    async def _collecting_executor(name: str, input: dict) -> str | dict | list:
        result = await _execute_tool(name, input)
        if name == "parse_figma_url":
            collected["parsed_url"] = result
        elif name == "get_file_info":
            collected["file_info"] = result
        elif name == "get_node_info":
            collected["node_info"] = result
        elif name == "export_children":
            collected["exported"].extend(result.get("exported", []))
            collected["errors"].extend(result.get("errors", []))
        elif name == "export_node_as_image":
            if isinstance(result, dict) and "path" in result:
                collected["exported"].append({
                    "name": input.get("node_id", ""),
                    "id": input.get("node_id", ""),
                    "path": result["path"],
                })
        return result

    result = await run_agent_loop(
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_executor=_collecting_executor,
        user_message=task,
    )
    return {"summary": result["text"], "data": collected, "usage": result["usage"]}
