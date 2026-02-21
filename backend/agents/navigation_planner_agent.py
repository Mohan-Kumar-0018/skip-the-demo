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
which pages to visit and HOW to reach each one.

Rules:
- List every visually distinct screen frame in the Figma image (pages, modals, empty states, confirmations).
- Use short, descriptive names. Follow the pattern: "Section - View/State" (e.g. "Suppliers - List View").
- Include modals, dialogs, and confirmation states as separate screens.
- One entry per distinct screen — do not merge or skip any.
- Order screens in the logical user flow (main page first, then sub-pages, then modals/confirmations).
- For each screen, describe how to navigate to it from its parent screen.
- Set "parent" to the screen name from which navigation starts (null for root/landing pages).
- Set "type" to one of: "page", "modal", "tab", "state", "confirmation".

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "screens": [
    {
      "name": "Screen Name",
      "figma_source": "source_filename.png",
      "how_to_reach": "Human-readable instruction on how to navigate here from parent",
      "parent": "Parent Screen Name or null",
      "type": "page|modal|tab|state|confirmation"
    }
  ],
  "summary": "One sentence describing what the designs show"
}

Example 1 — E-commerce product feature (from product_designs.png):
{
  "screens": [
    {"name": "Products - Grid View", "figma_source": "product_designs.png", "how_to_reach": "Click 'Products' in the sidebar navigation", "parent": null, "type": "page"},
    {"name": "Products - List View", "figma_source": "product_designs.png", "how_to_reach": "Click the list view toggle icon in the top-right", "parent": "Products - Grid View", "type": "tab"},
    {"name": "Products - Search Results", "figma_source": "product_designs.png", "how_to_reach": "Type a search query in the search bar and press Enter", "parent": "Products - Grid View", "type": "state"},
    {"name": "Product Detail Page", "figma_source": "product_designs.png", "how_to_reach": "Click on any product card in the grid or list", "parent": "Products - Grid View", "type": "page"},
    {"name": "Add Product Form", "figma_source": "product_designs.png", "how_to_reach": "Click the 'Add Product' button", "parent": "Products - Grid View", "type": "modal"},
    {"name": "Delete Product Confirmation", "figma_source": "product_designs.png", "how_to_reach": "Click the delete icon on a product, then confirm", "parent": "Product Detail Page", "type": "confirmation"},
    {"name": "Product Added Success", "figma_source": "product_designs.png", "how_to_reach": "Submit the Add Product form successfully", "parent": "Add Product Form", "type": "confirmation"}
  ],
  "summary": "Product catalog feature with grid/list views, search, detail page, and CRUD flows."
}

Example 2 — User settings feature (from settings_v2.png):
{
  "screens": [
    {"name": "Settings - Profile", "figma_source": "settings_v2.png", "how_to_reach": "Click 'Settings' in the sidebar or user menu", "parent": null, "type": "page"},
    {"name": "Settings - Notifications", "figma_source": "settings_v2.png", "how_to_reach": "Click the 'Notifications' tab", "parent": "Settings - Profile", "type": "tab"},
    {"name": "Settings - Security", "figma_source": "settings_v2.png", "how_to_reach": "Click the 'Security' tab", "parent": "Settings - Profile", "type": "tab"},
    {"name": "Change Password Modal", "figma_source": "settings_v2.png", "how_to_reach": "Click 'Change Password' button on Security tab", "parent": "Settings - Security", "type": "modal"},
    {"name": "Password Changed Success", "figma_source": "settings_v2.png", "how_to_reach": "Submit the change password form", "parent": "Change Password Modal", "type": "confirmation"},
    {"name": "Deactivate Account Modal", "figma_source": "settings_v2.png", "how_to_reach": "Click 'Deactivate Account' button on Security tab", "parent": "Settings - Security", "type": "modal"}
  ],
  "summary": "User settings with profile, notifications, and security tabs plus account actions."
}

Example 3 — Supplier discovery feature (from figma_13_1134.png):
{
  "screens": [
    {"name": "Dashboard - Home", "figma_source": "figma_13_1134.png", "how_to_reach": "Landing page after login", "parent": null, "type": "page"},
    {"name": "Explore Suppliers - Category View", "figma_source": "figma_13_1134.png", "how_to_reach": "Click 'Explore Suppliers' button on Dashboard", "parent": "Dashboard - Home", "type": "page"},
    {"name": "Explore Suppliers - List View", "figma_source": "figma_13_1134.png", "how_to_reach": "Click on a supplier category card", "parent": "Explore Suppliers - Category View", "type": "page"},
    {"name": "Explore Suppliers - Search Results", "figma_source": "figma_13_1134.png", "how_to_reach": "Type in the search bar and press Enter", "parent": "Explore Suppliers - Category View", "type": "state"},
    {"name": "Explore Suppliers - Recent Searches", "figma_source": "figma_13_1134.png", "how_to_reach": "Tap the search bar to see recent searches", "parent": "Explore Suppliers - Category View", "type": "state"},
    {"name": "Explore Suppliers - Nearby Suppliers", "figma_source": "figma_13_1134.png", "how_to_reach": "Click 'Nearby Suppliers' section or tab", "parent": "Explore Suppliers - Category View", "type": "tab"},
    {"name": "Explore Suppliers - Can't Find Supplier", "figma_source": "figma_13_1134.png", "how_to_reach": "Scroll down on Category View or click 'Can\\'t find your supplier?'", "parent": "Explore Suppliers - Category View", "type": "state"},
    {"name": "Saved Suppliers - Empty State", "figma_source": "figma_13_1134.png", "how_to_reach": "Click 'Saved Suppliers' tab when no suppliers are saved", "parent": "Explore Suppliers - Category View", "type": "state"},
    {"name": "Saved Suppliers - List View", "figma_source": "figma_13_1134.png", "how_to_reach": "Click 'Saved Suppliers' tab when suppliers are saved", "parent": "Explore Suppliers - Category View", "type": "tab"},
    {"name": "Supplier Detail Page", "figma_source": "figma_13_1134.png", "how_to_reach": "Click on a supplier card in the list view", "parent": "Explore Suppliers - List View", "type": "page"},
    {"name": "Filter Modal - Supplier Category", "figma_source": "figma_13_1134.png", "how_to_reach": "Click the filter icon on List View", "parent": "Explore Suppliers - List View", "type": "modal"},
    {"name": "Filter Modal - Sub Category", "figma_source": "figma_13_1134.png", "how_to_reach": "Select a category in the filter modal to see sub-categories", "parent": "Filter Modal - Supplier Category", "type": "modal"},
    {"name": "Nominate a Supplier Form", "figma_source": "figma_13_1134.png", "how_to_reach": "Click 'Nominate a Supplier' button", "parent": "Explore Suppliers - Can't Find Supplier", "type": "modal"},
    {"name": "Supplier Nominated Success", "figma_source": "figma_13_1134.png", "how_to_reach": "Submit the Nominate a Supplier form", "parent": "Nominate a Supplier Form", "type": "confirmation"},
    {"name": "Supplier Saved Confirmation", "figma_source": "figma_13_1134.png", "how_to_reach": "Click the save/bookmark icon on a supplier card", "parent": "Supplier Detail Page", "type": "confirmation"},
    {"name": "Remove from Saved Suppliers Modal", "figma_source": "figma_13_1134.png", "how_to_reach": "Click the remove/unsave icon on a saved supplier", "parent": "Saved Suppliers - List View", "type": "modal"},
    {"name": "Contact Supplier Form", "figma_source": "figma_13_1134.png", "how_to_reach": "Click 'Contact Supplier' button on Supplier Detail Page", "parent": "Supplier Detail Page", "type": "modal"},
    {"name": "Contact Request Sent Confirmation", "figma_source": "figma_13_1134.png", "how_to_reach": "Submit the Contact Supplier form", "parent": "Contact Supplier Form", "type": "confirmation"},
    {"name": "Voice Note Recording", "figma_source": "figma_13_1134.png", "how_to_reach": "Click the microphone icon in the Contact Supplier form", "parent": "Contact Supplier Form", "type": "state"},
    {"name": "Notifications Panel", "figma_source": "figma_13_1134.png", "how_to_reach": "Click the notifications bell icon in the header", "parent": "Dashboard - Home", "type": "page"}
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
    try:
        response = client.messages.create(
            model=model,
            max_tokens=3000,
            temperature=0,
            system=NAV_PLANNER_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        logger.error("Nav planner API call failed: %s", e)
        return {"screens": [], "summary": f"Nav planner API error: {e}", "error_code": "API_ERROR", "usage": {}}

    text = response.content[0].text
    logger.info("Nav planner response: %d chars", len(text))
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Nav planner returned invalid JSON: %s", clean[:300])
        return {"screens": [], "summary": f"Nav planner returned invalid JSON: {clean[:200]}", "error_code": "INVALID_JSON", "usage": {
            "model": model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
        }}

    parsed["usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
    }
    return parsed
