from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any

import anthropic
from PIL import Image

from agent_runner import calc_cost

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)

NAV_PLANNER_PROMPT = """\
You are a navigation planning agent. You are given Figma design screenshots for a web application feature. \
Identify every distinct screen shown in the designs. A browser automation agent will use this list to know \
which pages to visit — you only need to name them.

Rules:
- List every visually distinct screen frame in the Figma image (pages, modals, empty states, confirmations).
- Use short, descriptive names. Follow the pattern: "Section - View/State" (e.g. "Suppliers - List View").
- Include modals, dialogs, and confirmation states as separate screens.
- One entry per distinct screen — do not merge or skip any.
- Order screens in the logical user flow (main page first, then sub-pages, then modals/confirmations).

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "screens": [
    {"name": "Screen Name", "figma_source": "source_filename.png"}
  ],
  "summary": "One sentence describing what the designs show"
}

Example 1 — E-commerce product feature (from product_designs.png):
{
  "screens": [
    {"name": "Products - Grid View", "figma_source": "product_designs.png"},
    {"name": "Products - List View", "figma_source": "product_designs.png"},
    {"name": "Products - Search Results", "figma_source": "product_designs.png"},
    {"name": "Product Detail Page", "figma_source": "product_designs.png"},
    {"name": "Add Product Form", "figma_source": "product_designs.png"},
    {"name": "Delete Product Confirmation", "figma_source": "product_designs.png"},
    {"name": "Product Added Success", "figma_source": "product_designs.png"}
  ],
  "summary": "Product catalog feature with grid/list views, search, detail page, and CRUD flows."
}

Example 2 — User settings feature (from settings_v2.png):
{
  "screens": [
    {"name": "Settings - Profile", "figma_source": "settings_v2.png"},
    {"name": "Settings - Notifications", "figma_source": "settings_v2.png"},
    {"name": "Settings - Security", "figma_source": "settings_v2.png"},
    {"name": "Change Password Modal", "figma_source": "settings_v2.png"},
    {"name": "Password Changed Success", "figma_source": "settings_v2.png"},
    {"name": "Deactivate Account Modal", "figma_source": "settings_v2.png"}
  ],
  "summary": "User settings with profile, notifications, and security tabs plus account actions."
}

Example 3 — Supplier discovery feature (from figma_13_1134.png):
{
  "screens": [
    {"name": "Dashboard - Home", "figma_source": "figma_13_1134.png"},
    {"name": "Explore Suppliers - Category View", "figma_source": "figma_13_1134.png"},
    {"name": "Explore Suppliers - List View", "figma_source": "figma_13_1134.png"},
    {"name": "Explore Suppliers - Search Results", "figma_source": "figma_13_1134.png"},
    {"name": "Explore Suppliers - Recent Searches", "figma_source": "figma_13_1134.png"},
    {"name": "Explore Suppliers - Nearby Suppliers", "figma_source": "figma_13_1134.png"},
    {"name": "Explore Suppliers - Can't Find Supplier", "figma_source": "figma_13_1134.png"},
    {"name": "Saved Suppliers - Empty State", "figma_source": "figma_13_1134.png"},
    {"name": "Saved Suppliers - List View", "figma_source": "figma_13_1134.png"},
    {"name": "Supplier Detail Page", "figma_source": "figma_13_1134.png"},
    {"name": "Filter Modal - Supplier Category", "figma_source": "figma_13_1134.png"},
    {"name": "Filter Modal - Sub Category", "figma_source": "figma_13_1134.png"},
    {"name": "Nominate a Supplier Form", "figma_source": "figma_13_1134.png"},
    {"name": "Supplier Nominated Success", "figma_source": "figma_13_1134.png"},
    {"name": "Supplier Saved Confirmation", "figma_source": "figma_13_1134.png"},
    {"name": "Remove from Saved Suppliers Modal", "figma_source": "figma_13_1134.png"},
    {"name": "Contact Supplier Form", "figma_source": "figma_13_1134.png"},
    {"name": "Contact Request Sent Confirmation", "figma_source": "figma_13_1134.png"},
    {"name": "Voice Note Recording", "figma_source": "figma_13_1134.png"},
    {"name": "Notifications Panel", "figma_source": "figma_13_1134.png"}
  ],
  "summary": "Supplier discovery feature with explore, filter, save, nominate, and contact flows."
}"""


def _resize_if_needed(path: str) -> bytes:
    """Read an image and downscale if either dimension exceeds 8000px."""
    MAX_DIM = 8000
    with Image.open(path) as img:
        w, h = img.size
        if w > MAX_DIM or h > MAX_DIM:
            scale = min(MAX_DIM / w, MAX_DIM / h)
            new_size = (int(w * scale), int(h * scale))
            logger.info("Resizing %s from %dx%d to %dx%d", path, w, h, *new_size)
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def plan_navigation(
    figma_images: list[dict], prd_text: str
) -> dict[str, Any]:
    """Analyze Figma design screens and produce a navigation flow.

    Args:
        figma_images: List of dicts with 'path' and optional 'name' keys.
        prd_text: PRD text for additional context.

    Returns:
        Dict with screens, summary, usage stats.
    """
    if not figma_images:
        return {"screens": [], "usage": {}}

    # Build multi-image content blocks
    content: list[dict[str, Any]] = []
    for img in figma_images:
        path = img.get("path", "")
        name = img.get("name", os.path.basename(path))
        img_bytes = _resize_if_needed(path)
        b64 = base64.b64encode(img_bytes).decode()
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        })
        content.append({
            "type": "text",
            "text": f"[Screen: {name}]",
        })

    # Add the instruction text
    context_text = ""
    if prd_text:
        context_text = f"PRD excerpt: {prd_text[:1500]}\n\n"
    content.append({
        "type": "text",
        "text": (
            f"\n{context_text}"
            "Analyze all the design screens above and produce the navigation flow JSON."
        ),
    })

    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        temperature=0,
        system=NAV_PLANNER_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    logger.info("Nav planner response: %d chars", len(text))
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Nav planner returned invalid JSON: %s", clean[:300])
        raise ValueError(f"Nav planner returned invalid JSON: {clean[:200]}") from exc

    parsed["usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
    }
    return parsed
