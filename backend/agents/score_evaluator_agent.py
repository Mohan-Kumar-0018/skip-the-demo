"""Score Evaluator Agent — multi-phase design vs UAT comparison.

Compares a full set of UAT screenshots against Figma design exports across
multiple dimensions: screen coverage, visual fidelity, and missing screens.
Uses 6-7 sequential Claude Vision API calls with temperature=0 for
deterministic results.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
from typing import Any

import anthropic
from PIL import Image

from agent_runner import calc_cost

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)

MAX_IMAGES_PER_CALL = 20

# ─── Prompts ────────────────────────────────────────────────────

FIGMA_INVENTORY_PROMPT = """\
You are a design analysis agent. You are given Figma design export images for a web/mobile application.

Identify every distinct screen or state shown across all the images. Each Figma export may contain multiple screen frames.

For each screen found:
- Give it a short descriptive name (e.g. "Suppliers - List View", "Login Page", "Add Product Modal")
- Note which source image it appears in
- Write a 1-2 sentence semantic description of what the screen shows (layout, key UI elements, purpose)

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "screens": [
    {
      "id": "figma_1",
      "name": "Screen Name",
      "source_image": "filename.png",
      "description": "Semantic description of the screen content and purpose"
    }
  ],
  "total_screens": <int>
}"""

UAT_INVENTORY_PROMPT = """\
You are a QA analysis agent. You are given UAT (User Acceptance Testing) screenshots from a web/mobile application.

For each screenshot, identify:
- The page type (login, dashboard, list view, detail view, modal, form, etc.)
- The current state (empty, loaded, error, success, etc.)
- Key UI elements visible (navigation, tables, forms, buttons, etc.)
- A 1-2 sentence semantic description

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "screens": [
    {
      "id": "uat_1",
      "filename": "screen_1.png",
      "page_type": "list view",
      "state": "loaded with data",
      "description": "Semantic description of what this screenshot shows"
    }
  ],
  "total_screens": <int>
}"""

SCREEN_MATCHING_PROMPT = """\
You are a screen matching agent. You have two inventories:

1. Figma design screens (the intended design)
2. UAT screenshots (the actual built application)

Match each UAT screenshot to the Figma design screen it most closely represents.
Multiple UAT screenshots can match the same Figma screen (e.g. different states of the same page).
A Figma screen with no UAT match means that screen was not built or not tested.
A UAT screenshot with no Figma match means it shows something not in the designs.

Rules:
- Match by semantic similarity of descriptions, not by filename
- Only match if confidence >= 40 (out of 100)
- A single Figma screen can have multiple UAT matches
- Report unmatched screens on both sides

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "matches": [
    {
      "figma_id": "figma_1",
      "figma_name": "Screen Name",
      "uat_id": "uat_3",
      "uat_filename": "screen_3.png",
      "confidence": 85,
      "reasoning": "Both show the supplier list view with search and filter options"
    }
  ],
  "unmatched_figma": [
    {
      "figma_id": "figma_5",
      "figma_name": "Screen Name",
      "reason": "No UAT screenshot covers this screen"
    }
  ],
  "unmatched_uat": [
    {
      "uat_id": "uat_12",
      "uat_filename": "screen_12.png",
      "reason": "This screen is not represented in the Figma designs"
    }
  ]
}"""

VISUAL_COMPARE_PROMPT = """\
You are a visual QA agent. You are given pairs of images: a Figma design and the corresponding UAT screenshot.

For each pair, compare the actual implementation against the design and score these dimensions (0-100):
- layout_accuracy: How well does the spatial arrangement match? (positions, sizes, spacing)
- color_consistency: Do colors, gradients, and themes match the design?
- typography: Do fonts, sizes, weights, and text alignment match?
- component_completeness: Are all designed UI components present and correctly implemented?
- overall_fidelity: Overall visual match quality

Also list specific deviations found.

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "comparisons": [
    {
      "figma_id": "figma_1",
      "uat_id": "uat_3",
      "scores": {
        "layout_accuracy": 85,
        "color_consistency": 90,
        "typography": 80,
        "component_completeness": 75,
        "overall_fidelity": 82
      },
      "deviations": [
        {
          "type": "layout",
          "description": "Search bar is 20px narrower than design",
          "severity": "low"
        }
      ]
    }
  ]
}"""

SYNTHESIS_PROMPT = """\
You are a QA scoring synthesis agent. You have the results of a multi-phase design comparison:

1. Screen inventories (Figma designs and UAT screenshots)
2. Screen matching results (which UAT screens map to which Figma screens)
3. Visual comparison scores for matched pairs

Calculate these aggregate scores:

**Screen Coverage Score (0-100):**
- (matched Figma screens / total Figma screens) * 100
- Each Figma screen counts as matched if at least one UAT screenshot maps to it

**Visual Comparison Score (0-100):**
- Average of overall_fidelity scores across all compared pairs
- If no pairs were compared, score = 0

**Missing Screens Penalty Score (0-100):**
- 100 = no missing screens, 0 = all screens missing
- Formula: (1 - missing_count / total_figma_screens) * 100

**Overall Score (0-100):**
- Weighted: 25% coverage + 50% visual + 25% missing screens

Also identify:
- Top 3 strongest matches (highest fidelity)
- Top 3 weakest matches (lowest fidelity)
- Top 5 most impactful deviations across all pairs
- 3-5 actionable recommendations for improving design fidelity

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "screen_coverage": {
    "score": <int>,
    "matched_figma_screens": <int>,
    "total_figma_screens": <int>,
    "coverage_percentage": <float>
  },
  "visual_comparison": {
    "score": <int>,
    "avg_layout_accuracy": <int>,
    "avg_color_consistency": <int>,
    "avg_typography": <int>,
    "avg_component_completeness": <int>,
    "pairs_compared": <int>
  },
  "missing_screens": {
    "score": <int>,
    "missing": [{"figma_id": "...", "figma_name": "...", "description": "..."}],
    "missing_count": <int>
  },
  "additional_analysis": {
    "strongest_matches": [{"figma_name": "...", "uat_filename": "...", "score": <int>}],
    "weakest_matches": [{"figma_name": "...", "uat_filename": "...", "score": <int>}],
    "top_deviations": [{"pair": "...", "type": "...", "description": "...", "severity": "..."}],
    "recommendations": ["..."]
  },
  "overall_score": <int>,
  "summary": "One paragraph overall assessment"
}"""


# ─── Helpers ────────────────────────────────────────────────────

def _resize_if_needed(path: str, max_dim: int = 4000) -> bytes:
    """Read an image and downscale if either dimension exceeds max_dim."""
    with Image.open(path) as img:
        w, h = img.size
        if w > max_dim or h > max_dim:
            scale = min(max_dim / w, max_dim / h)
            new_size = (int(w * scale), int(h * scale))
            logger.info("Resizing %s from %dx%d to %dx%d", path, w, h, *new_size)
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _b64_image(path: str) -> str:
    """Resize if needed and return base64-encoded PNG."""
    img_bytes = _resize_if_needed(path)
    return base64.b64encode(img_bytes).decode()


def _natural_sort_key(filename: str):
    """Sort key that handles numeric parts naturally (screen_2 before screen_10)."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', filename)
    ]


def _load_image_set(directory: str) -> list[dict]:
    """Load all PNGs from a directory, sorted naturally.

    Returns list of dicts with 'path' and 'filename' keys.
    """
    if not os.path.isdir(directory):
        return []
    files = [f for f in os.listdir(directory) if f.lower().endswith(".png")]
    files.sort(key=_natural_sort_key)
    return [
        {"path": os.path.join(directory, f), "filename": f}
        for f in files
    ]


def _build_image_content(images: list[dict], label_prefix: str) -> list[dict]:
    """Build Claude API content blocks for a set of images.

    Each image gets an image block followed by a text label.
    """
    content: list[dict] = []
    for i, img in enumerate(images, 1):
        b64 = _b64_image(img["path"])
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
            "text": f"[{label_prefix} {i}: {img['filename']}]",
        })
    return content


def _recover_truncated_json(text: str) -> str:
    """Attempt to fix truncated JSON by closing open structures.

    Handles the common case where a Claude response is cut off at max_tokens
    mid-JSON, leaving unterminated strings, arrays, or objects.
    """
    # If inside an unterminated string, close it
    # Count unescaped quotes
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_string:
            i += 2  # skip escaped char
            continue
        if ch == '"':
            in_string = not in_string
        i += 1

    if in_string:
        text += '"'

    # Strip any trailing comma
    text = text.rstrip().rstrip(',')

    # Close open brackets/braces
    stack: list[str] = []
    in_str = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_str:
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()
        i += 1

    # Close in reverse order
    for opener in reversed(stack):
        text += ']' if opener == '[' else '}'

    return text


def _call_claude_vision(
    model: str,
    system_prompt: str,
    content: list[dict],
    max_tokens: int = 4096,
) -> tuple[dict, dict]:
    """Make a single Claude Vision API call with temperature=0.

    Returns (parsed_json, usage_dict).
    """
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    logger.debug("Raw response (%d chars, stop=%s):\n%s", len(text), response.stop_reason, text)
    clean = text.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        # Response may have been truncated at max_tokens — try to recover
        # by closing open strings/arrays/objects
        logger.warning("JSON parse failed (likely truncated at %d tokens), attempting recovery", max_tokens)
        recovered = _recover_truncated_json(clean)
        parsed = json.loads(recovered)

    usage = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
        "api_calls": 1,
    }
    return parsed, usage


def _deduplicate_matches(matches: list[dict]) -> list[dict]:
    """Pick the highest-confidence UAT match per Figma screen for Phase 3.

    When multiple UAT screenshots match the same Figma screen, we keep the
    best one for visual comparison. All matches are still reported in the
    matching results.
    """
    best: dict[str, dict] = {}
    for m in matches:
        fid = m["figma_id"]
        if fid not in best or m.get("confidence", 0) > best[fid].get("confidence", 0):
            best[fid] = m
    return list(best.values())


def _aggregate_usage(*usages: dict) -> dict:
    """Merge token counts and costs across multiple API calls."""
    model = ""
    total_input = 0
    total_output = 0
    total_cost = 0.0
    total_calls = 0

    for u in usages:
        if not u:
            continue
        if u.get("model"):
            model = u["model"]
        total_input += u.get("input_tokens", 0)
        total_output += u.get("output_tokens", 0)
        total_cost += u.get("cost_usd", 0.0)
        total_calls += u.get("api_calls", 0)

    return {
        "model": model,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": round(total_cost, 6),
        "api_calls": total_calls,
    }


def _save_matched_pairs(pairs: list[dict], matches: list[dict]) -> None:
    """Copy matched Figma/UAT pairs into outputs/matched/ for easy review.

    Creates one subdirectory per pair:
        outputs/matched/01_Dashboard_Home/design.png
        outputs/matched/01_Dashboard_Home/actual.png
    Also writes a matches.json manifest.
    """
    matched_dir = "outputs/matched"
    if os.path.isdir(matched_dir):
        shutil.rmtree(matched_dir)
    os.makedirs(matched_dir, exist_ok=True)

    manifest: list[dict] = []
    for i, pair in enumerate(pairs, 1):
        # Slugify the Figma screen name for the folder
        slug = re.sub(r'[^a-zA-Z0-9]+', '_', pair["figma_name"]).strip('_')
        pair_dir = os.path.join(matched_dir, f"{i:02d}_{slug}")
        os.makedirs(pair_dir, exist_ok=True)

        shutil.copy2(pair["figma_path"], os.path.join(pair_dir, "design.png"))
        shutil.copy2(pair["uat_path"], os.path.join(pair_dir, "actual.png"))

        # Find confidence from the match
        confidence = 0
        for m in matches:
            if m["figma_id"] == pair["figma_id"] and m["uat_id"] == pair["uat_id"]:
                confidence = m.get("confidence", 0)
                break

        manifest.append({
            "pair": i,
            "folder": f"{i:02d}_{slug}",
            "figma_id": pair["figma_id"],
            "figma_name": pair["figma_name"],
            "figma_source": os.path.basename(pair["figma_path"]),
            "uat_id": pair["uat_id"],
            "uat_source": pair["uat_filename"],
            "confidence": confidence,
        })

    with open(os.path.join(matched_dir, "matches.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Saved %d matched pairs to %s/", len(pairs), matched_dir)


def _empty_result(reason: str) -> dict:
    """Return a valid zero-score result when inputs are missing."""
    return {
        "inventory": {"figma_screens": [], "uat_screens": []},
        "matching": {"matches": [], "unmatched_figma": [], "unmatched_uat": []},
        "pair_comparisons": [],
        "screen_coverage": {
            "score": 0, "matched_figma_screens": 0,
            "total_figma_screens": 0, "coverage_percentage": 0.0,
        },
        "visual_comparison": {
            "score": 0, "avg_layout_accuracy": 0, "avg_color_consistency": 0,
            "avg_typography": 0, "avg_component_completeness": 0, "pairs_compared": 0,
        },
        "missing_screens": {"score": 0, "missing": [], "missing_count": 0},
        "additional_analysis": {
            "strongest_matches": [], "weakest_matches": [],
            "top_deviations": [], "recommendations": [],
        },
        "overall_score": 0,
        "summary": reason,
        "usage": {"model": "", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "api_calls": 0},
    }


# ─── Main entry point ──────────────────────────────────────────

def evaluate_scores(uat_dir: str, figma_dir: str) -> dict:
    """Run the 4-phase score evaluation pipeline.

    Args:
        uat_dir: Directory containing UAT screenshot PNGs.
        figma_dir: Directory containing Figma design export PNGs.

    Returns:
        Dict with inventory, matching, pair_comparisons, scores, and usage.
    """
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    all_usages: list[dict] = []

    # ── Load images ─────────────────────────────────────────
    figma_images = _load_image_set(figma_dir)
    uat_images = _load_image_set(uat_dir)

    logger.info("Score evaluator: %d Figma images, %d UAT images", len(figma_images), len(uat_images))

    if not figma_images:
        return _empty_result("No Figma design images found")
    if not uat_images:
        return _empty_result("No UAT screenshots found")

    # ── Phase 1A: Figma inventory ───────────────────────────
    logger.info("Phase 1A: Figma inventory (%d images)", len(figma_images))
    figma_screens: list[dict] = []

    figma_batches = _batch_images(figma_images, MAX_IMAGES_PER_CALL)
    for batch_idx, batch in enumerate(figma_batches):
        logger.info("  Figma batch %d/%d (%d images)", batch_idx + 1, len(figma_batches), len(batch))
        content = _build_image_content(batch, "Figma Design")
        content.append({"type": "text", "text": "Analyze all the Figma design images above and inventory every distinct screen."})
        try:
            parsed, usage = _call_claude_vision(model, FIGMA_INVENTORY_PROMPT, content)
            all_usages.append(usage)
            # Re-index screen IDs to avoid collisions across batches
            for s in parsed.get("screens", []):
                s["id"] = f"figma_{len(figma_screens) + 1}"
                figma_screens.append(s)
        except Exception:
            logger.exception("Phase 1A batch %d failed", batch_idx + 1)

    if not figma_screens:
        result = _empty_result("Failed to inventory Figma designs")
        result["usage"] = _aggregate_usage(*all_usages)
        return result

    # ── Phase 1B/1C: UAT inventory (batched) ────────────────
    logger.info("Phase 1B/1C: UAT inventory (%d images)", len(uat_images))
    uat_screens: list[dict] = []

    uat_batches = _batch_images(uat_images, MAX_IMAGES_PER_CALL)
    for batch_idx, batch in enumerate(uat_batches):
        logger.info("  UAT batch %d/%d (%d images)", batch_idx + 1, len(uat_batches), len(batch))
        content = _build_image_content(batch, "UAT Screenshot")
        content.append({"type": "text", "text": "Analyze all the UAT screenshots above and describe each one."})
        try:
            parsed, usage = _call_claude_vision(model, UAT_INVENTORY_PROMPT, content)
            all_usages.append(usage)
            for s in parsed.get("screens", []):
                s["id"] = f"uat_{len(uat_screens) + 1}"
                if "filename" not in s and batch_idx * MAX_IMAGES_PER_CALL + len(uat_screens) < len(uat_images):
                    s["filename"] = uat_images[len(uat_screens)]["filename"]
                uat_screens.append(s)
        except Exception:
            logger.exception("Phase 1B/C batch %d failed", batch_idx + 1)

    if not uat_screens:
        result = _empty_result("Failed to inventory UAT screenshots")
        result["inventory"] = {"figma_screens": figma_screens, "uat_screens": []}
        result["usage"] = _aggregate_usage(*all_usages)
        return result

    # ── Phase 2: Screen matching (text-only) ────────────────
    logger.info("Phase 2: Screen matching (%d Figma x %d UAT)", len(figma_screens), len(uat_screens))
    matching_content = [{
        "type": "text",
        "text": (
            "Figma design screens:\n"
            + json.dumps(figma_screens, indent=2)
            + "\n\nUAT screenshots:\n"
            + json.dumps(uat_screens, indent=2)
            + "\n\nMatch the UAT screenshots to Figma design screens based on their descriptions."
        ),
    }]

    matching_result = {"matches": [], "unmatched_figma": [], "unmatched_uat": []}
    try:
        parsed, usage = _call_claude_vision(model, SCREEN_MATCHING_PROMPT, matching_content, max_tokens=4096)
        all_usages.append(usage)
        matching_result = parsed
    except Exception:
        logger.exception("Phase 2 matching failed")

    matches = matching_result.get("matches", [])

    # ── Phase 3: Visual comparison of matched pairs ─────────
    best_matches = _deduplicate_matches(matches)
    logger.info("Phase 3: Visual comparison (%d unique pairs)", len(best_matches))

    pair_comparisons: list[dict] = []

    if best_matches:
        # Build lookup maps
        figma_by_id = {img["filename"]: img["path"] for img in figma_images}
        uat_by_filename = {img["filename"]: img["path"] for img in uat_images}
        figma_screen_source = {s["id"]: s.get("source_image", "") for s in figma_screens}
        uat_screen_filename = {s["id"]: s.get("filename", "") for s in uat_screens}

        # Build pairs with their image paths
        pairs_with_paths: list[dict] = []
        for m in best_matches:
            figma_source = figma_screen_source.get(m["figma_id"], "")
            uat_file = uat_screen_filename.get(m["uat_id"], m.get("uat_filename", ""))
            figma_path = figma_by_id.get(figma_source)
            uat_path = uat_by_filename.get(uat_file)
            if figma_path and uat_path:
                pairs_with_paths.append({
                    "figma_id": m["figma_id"],
                    "uat_id": m["uat_id"],
                    "figma_path": figma_path,
                    "uat_path": uat_path,
                    "figma_name": m.get("figma_name", ""),
                    "uat_filename": uat_file,
                })

        # Save matched pairs to outputs/matched/
        _save_matched_pairs(pairs_with_paths, best_matches)

        # Batch pairs: 10 pairs per call (= 20 images)
        PAIRS_PER_CALL = 10
        pair_batches = [
            pairs_with_paths[i:i + PAIRS_PER_CALL]
            for i in range(0, len(pairs_with_paths), PAIRS_PER_CALL)
        ]

        for batch_idx, batch in enumerate(pair_batches):
            logger.info("  Comparison batch %d/%d (%d pairs)", batch_idx + 1, len(pair_batches), len(batch))
            content: list[dict] = []
            for pair in batch:
                # Figma design image
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _b64_image(pair["figma_path"]),
                    },
                })
                content.append({
                    "type": "text",
                    "text": f"[DESIGN — {pair['figma_id']}: {pair['figma_name']}]",
                })
                # UAT screenshot
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _b64_image(pair["uat_path"]),
                    },
                })
                content.append({
                    "type": "text",
                    "text": f"[ACTUAL — {pair['uat_id']}: {pair['uat_filename']}]",
                })

            content.append({
                "type": "text",
                "text": f"Compare each design/actual pair above. There are {len(batch)} pairs to compare.",
            })

            try:
                parsed, usage = _call_claude_vision(model, VISUAL_COMPARE_PROMPT, content, max_tokens=4096)
                all_usages.append(usage)
                pair_comparisons.extend(parsed.get("comparisons", []))
            except Exception:
                logger.exception("Phase 3 batch %d failed", batch_idx + 1)

    # ── Phase 4: Synthesis (text-only) ──────────────────────
    logger.info("Phase 4: Score synthesis")
    synthesis_input = {
        "figma_screens": figma_screens,
        "uat_screens": uat_screens,
        "matching": matching_result,
        "pair_comparisons": pair_comparisons,
    }
    synthesis_content = [{
        "type": "text",
        "text": (
            "Here are the complete results from the design comparison pipeline:\n\n"
            + json.dumps(synthesis_input, indent=2)
            + "\n\nCalculate the aggregate scores and provide the final assessment."
        ),
    }]

    synthesis_result = {}
    try:
        parsed, usage = _call_claude_vision(model, SYNTHESIS_PROMPT, synthesis_content, max_tokens=4096)
        all_usages.append(usage)
        synthesis_result = parsed
    except Exception:
        logger.exception("Phase 4 synthesis failed")

    # ── Assemble final result ───────────────────────────────
    result = {
        "inventory": {
            "figma_screens": figma_screens,
            "uat_screens": uat_screens,
        },
        "matching": matching_result,
        "pair_comparisons": pair_comparisons,
        "screen_coverage": synthesis_result.get("screen_coverage", {
            "score": 0, "matched_figma_screens": 0,
            "total_figma_screens": len(figma_screens), "coverage_percentage": 0.0,
        }),
        "visual_comparison": synthesis_result.get("visual_comparison", {
            "score": 0, "avg_layout_accuracy": 0, "avg_color_consistency": 0,
            "avg_typography": 0, "avg_component_completeness": 0, "pairs_compared": 0,
        }),
        "missing_screens": synthesis_result.get("missing_screens", {
            "score": 0, "missing": [], "missing_count": 0,
        }),
        "additional_analysis": synthesis_result.get("additional_analysis", {
            "strongest_matches": [], "weakest_matches": [],
            "top_deviations": [], "recommendations": [],
        }),
        "overall_score": synthesis_result.get("overall_score", 0),
        "summary": synthesis_result.get("summary", "Synthesis phase did not complete"),
        "usage": _aggregate_usage(*all_usages),
    }

    logger.info(
        "Score evaluation complete: overall=%d, coverage=%d, visual=%d, missing=%d | %d API calls, $%.4f",
        result["overall_score"],
        result["screen_coverage"].get("score", 0),
        result["visual_comparison"].get("score", 0),
        result["missing_screens"].get("score", 0),
        result["usage"]["api_calls"],
        result["usage"]["cost_usd"],
    )

    return result


def _batch_images(images: list[dict], batch_size: int) -> list[list[dict]]:
    """Split a list of images into batches of at most batch_size."""
    return [images[i:i + batch_size] for i in range(0, len(images), batch_size)]
