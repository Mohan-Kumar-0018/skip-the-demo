"""Demo Video Generator Agent — 3-phase post-processing pipeline.

Takes raw .webm/.mov browser recordings and produces polished demo videos with:
- Frame deduplication (removes static pauses)
- Click ripple animations (purple #7C3AED)
- AI-generated speech narration (edge-tts)
- Subtitle overlays

Pattern follows score_evaluator_agent.py (multi-phase, no agent loop).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
from typing import Any, Callable

import anthropic
import numpy as np
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
)
from PIL import Image, ImageDraw, ImageFont

from agent_runner import calc_cost

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)

# ─── Constants ──────────────────────────────────────────────────

ACCENT_COLOR = (124, 58, 237)  # #7C3AED purple
RIPPLE_DURATION = 0.5  # seconds
RIPPLE_MAX_RADIUS = 40
FRAME_SAMPLE_FPS = 10
STATIC_THRESHOLD = 4.0  # seconds of static before trimming
STATIC_KEEP = 1.5  # seconds to keep from static segments
FRAME_DIFF_THRESHOLD = 0.02  # fraction of pixels that must differ
CURSOR_DURATION = 0.8  # cursor + ripple animation length
PATH_DRAW_DURATION = 0.4  # max time for line-draw between clicks
CLICK_LEAD_TIME = 0.15  # click animation starts this many seconds before the visual transition
TRANSITION_DIFF_THRESHOLD = 0.08  # fraction of pixels that must change for a transition
TRANSITION_MIN_GAP = 0.8  # minimum seconds between detected transitions
TRANSITION_STABILITY_DELAY = 0.3  # seconds to wait and re-check that change persisted
ACTION_SUBTITLE_MAX_DURATION = 2.5  # max subtitle display time
ACTION_SUBTITLE_MIN_GAP = 0.3  # gap between consecutive subtitles


# ─── Phase 1: Narration Script Generation ───────────────────────

NARRATION_PROMPT = """\
You are a product demo narration writer. You are given:
1. An action log from a browser recording session (timestamps + actions), OR detected visual transition timestamps if no action log is available
2. Optionally, screenshots of the application
3. Optionally, a feature description for context

Write a narration script for a polished product demo video. The narration should:
- Sound natural and professional, like a product manager giving a walkthrough
- Explain WHAT the user is doing and WHY at each step
- Keep each segment concise (1-2 sentences, 5-15 words per segment)
- Cover key interactions: navigation, clicking, typing, viewing data
- Skip trivial waits and repeated actions
- Use present tense ("Here we navigate to...", "The dashboard shows...")

For segments that describe a click or tap interaction, also include:
- "click_x_pct": estimated X position of the click as a percentage (0-100) of viewport width
- "click_y_pct": estimated Y position of the click as a percentage (0-100) of viewport height
Use the screenshots to estimate where the user clicked (e.g. a nav tab at the top-center
would be roughly click_x_pct=50, click_y_pct=5). Only include these fields for actual
click/tap actions, not for passive viewing segments.

WHEN NO ACTION LOG IS AVAILABLE (transition-based mode):
You will receive detected visual transition timestamps — moments where the screen changed
significantly, indicating a user interaction (click, navigation, data load). Each transition
represents a real visual change in the video.

For transition-based narration:
- Create one narration segment per transition. The segment should describe what changed.
- Set each segment's "start_ms" to approximately 200ms BEFORE the corresponding transition
  timestamp (so narration begins just as the user would be clicking).
- Use the DEDUPED video duration (provided) for all timing — NOT the original duration.
- Look at sequential screenshot pairs to understand what changed at each transition.
- For click positions: examine the screenshot BEFORE the transition to estimate where the
  user likely clicked to trigger the visual change (e.g. a button, tab, menu item).
- Add an opening segment (start_ms=0) describing the initial screen state.

Return ONLY valid JSON — no markdown fences, no extra text:
{
  "segments": [
    {
      "start_ms": 0,
      "text": "Welcome to the application dashboard.",
      "action_context": "initial view"
    },
    {
      "start_ms": 2500,
      "text": "Let's navigate to the orders section.",
      "action_context": "click on Orders tab",
      "click_x_pct": 65,
      "click_y_pct": 5
    }
  ]
}"""


def _generate_narration_script(
    action_log: list[dict],
    screenshot_paths: list[str] | None = None,
    feature_context: str = "",
    video_duration_s: float | None = None,
    transitions: list[float] | None = None,
    deduped_duration_s: float | None = None,
) -> tuple[list[dict], dict]:
    """Phase 1: Generate narration script from action log via Claude.

    When no action log is available, uses pre-scanned transition timestamps
    so Claude can anchor narration to real visual changes.
    """
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    content: list[dict[str, Any]] = []

    # Add screenshots (up to 10) for visual context
    if screenshot_paths:
        for path in screenshot_paths[:10]:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                })
                content.append({
                    "type": "text",
                    "text": f"[Screenshot: {os.path.basename(path)}]",
                })

    # Add action log / transitions and context
    if action_log:
        text_parts = [f"Action log:\n{json.dumps(action_log, indent=2)}"]
    elif transitions:
        # No action log — use detected transitions instead
        transition_strs = [f"{t:.1f}s" for t in transitions]
        effective_duration = deduped_duration_s or video_duration_s or 0
        text_parts = [
            "Action log: [] (empty — no action log available)",
            "",
            f"Detected visual transitions at: [{', '.join(transition_strs)}]",
            "Each transition is a moment where the screen changed significantly, "
            "indicating a user interaction (click, navigation, page load).",
            "",
            "IMPORTANT — Transition-based mode:",
            "- Create one narration segment per transition describing what changed.",
            "- Set each segment's start_ms to ~200ms BEFORE the transition timestamp "
            "(so narration starts just as the click happens).",
            "- Also add an opening segment at start_ms=0 for the initial screen.",
            "- Use the screenshots (in order) to see what changed at each transition.",
            "- For click positions: look at the screenshot BEFORE each transition to "
            "estimate where the user clicked to cause the change.",
            "",
            f"Deduped video duration: {effective_duration:.1f} seconds. "
            f"All start_ms values MUST be between 0 and {int(effective_duration * 1000)}.",
            "- Since no action log is available, estimate click positions (click_x_pct, click_y_pct) "
            "for each interaction segment by examining the screenshots.",
        ]
    else:
        text_parts = [
            "Action log: [] (empty — no action log available)",
            "Since no action log is available, please use the screenshots to infer "
            "the user's click interactions and estimate click positions (click_x_pct, click_y_pct) "
            "for each navigation/interaction segment.",
        ]
    if video_duration_s is not None and action_log:
        # Only add generic duration constraint when using action log
        text_parts.append(
            f"\nVideo duration: {video_duration_s:.1f} seconds. "
            "All segment start_ms values MUST be between 0 and "
            f"{int(video_duration_s * 1000)}. Space segments evenly across the video."
        )
    elif video_duration_s is not None and not transitions:
        # Fallback: no action log AND no transitions
        text_parts.append(
            f"\nVideo duration: {video_duration_s:.1f} seconds. "
            "All segment start_ms values MUST be between 0 and "
            f"{int(video_duration_s * 1000)}. Space segments evenly across the video."
        )
    if feature_context:
        text_parts.append(f"\nFeature context: {feature_context}")
    text_parts.append("\nWrite a narration script for this demo video.")
    content.append({"type": "text", "text": "\n".join(text_parts)})

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        temperature=0,
        system=NARRATION_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    clean = text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(clean)

    usage = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": calc_cost(model, response.usage.input_tokens, response.usage.output_tokens),
        "api_calls": 1,
    }

    segments = parsed.get("segments", [])
    logger.info("Phase 1: Generated %d narration segments", len(segments))
    return segments, usage


# ─── Phase 2: TTS Generation ────────────────────────────────────

async def _generate_tts(
    segments: list[dict],
    work_dir: str,
) -> list[dict]:
    """Phase 2: Convert narration segments to speech audio via edge-tts.

    Enriches each segment with audio_path and duration_ms.
    """
    import edge_tts

    voice = "en-US-AriaNeural"
    enriched = []

    for i, seg in enumerate(segments):
        text = seg.get("text", "")
        if not text.strip():
            continue

        audio_path = os.path.join(work_dir, f"narration_{i:03d}.mp3")
        subtitle_data: list[dict] = []

        communicate = edge_tts.Communicate(text, voice)

        # Collect timestamps for subtitle sync (WordBoundary in v6, SentenceBoundary in v7)
        audio_chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                subtitle_data.append({
                    "offset_ms": chunk["offset"] // 10_000,  # 100ns units to ms
                    "duration_ms": chunk["duration"] // 10_000,
                    "text": chunk["text"],
                })

        # Write audio file
        with open(audio_path, "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

        # Get duration from the audio file
        try:
            clip = AudioFileClip(audio_path)
            duration_ms = int(clip.duration * 1000)
            clip.close()
        except Exception:
            duration_ms = 3000  # fallback

        enriched.append({
            **seg,
            "audio_path": audio_path,
            "duration_ms": duration_ms,
            "subtitle_words": subtitle_data,
        })

    logger.info("Phase 2: Generated %d TTS audio files", len(enriched))
    return enriched


# ─── Phase 3: Video Processing ───────────────────────────────────

def _create_ripple_frame(width: int, height: int, cx: int, cy: int, radius: int) -> np.ndarray:
    """Create a transparent frame with a purple ripple circle at (cx, cy)."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Outer ring
    alpha = max(20, 180 - int(radius * 3.5))
    r, g, b = ACCENT_COLOR
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=(r, g, b, alpha),
        width=3,
    )
    # Inner filled circle (smaller, more transparent)
    inner_r = max(4, radius // 3)
    inner_alpha = max(10, 100 - int(radius * 2))
    draw.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=(r, g, b, inner_alpha),
    )
    return np.array(img)


def _draw_cursor(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int = 24, alpha: int = 230) -> None:
    """Draw a classic mouse pointer (white arrow with black outline) at (cx, cy)."""
    # Arrow polygon relative to tip at (0, 0)
    points = [
        (0, 0),
        (0, size),
        (size * 0.35, size * 0.7),
        (size * 0.55, size),
        (size * 0.7, size * 0.9),
        (size * 0.45, size * 0.6),
        (size * 0.75, size * 0.55),
    ]
    # Offset to click position
    polygon = [(cx + int(px), cy + int(py)) for px, py in points]
    # Black outline
    draw.polygon(polygon, outline=(0, 0, 0, alpha), fill=None)
    # White fill (drawn slightly smaller via same polygon)
    draw.polygon(polygon, fill=(255, 255, 255, alpha))
    # Redraw outline on top
    draw.polygon(polygon, outline=(0, 0, 0, alpha), fill=None)


def _create_cursor_frame(
    width: int, height: int, cx: int, cy: int,
    cursor_alpha: int, ripple_radius: int | None = None,
) -> np.ndarray:
    """Render one RGBA frame with a cursor pointer and optional ripple."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw cursor
    if cursor_alpha > 0:
        _draw_cursor(draw, cx, cy, size=24, alpha=cursor_alpha)
    # Draw ripple ring if active
    if ripple_radius is not None and ripple_radius > 0:
        r, g, b = ACCENT_COLOR
        ring_alpha = max(20, 180 - int(ripple_radius * 3.5))
        draw.ellipse(
            [cx - ripple_radius, cy - ripple_radius, cx + ripple_radius, cy + ripple_radius],
            outline=(r, g, b, ring_alpha),
            width=3,
        )
    return np.array(img)


def _create_path_frame(
    width: int, height: int,
    x1: int, y1: int, x2: int, y2: int,
    progress: float,
) -> np.ndarray:
    """Render one RGBA frame with a dashed line drawn from (x1,y1) toward (x2,y2) and cursor at the tip."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = ACCENT_COLOR

    # Current tip position
    tip_x = int(x1 + (x2 - x1) * progress)
    tip_y = int(y1 + (y2 - y1) * progress)

    # Draw dashed line from start to current tip
    total_dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    drawn_dist = total_dist * progress
    if drawn_dist > 2 and total_dist > 0:
        dash_on, dash_off = 10, 6
        d = 0.0
        while d < drawn_dist:
            seg_s = d / total_dist
            seg_e = min((d + dash_on) / total_dist, progress)
            sx = int(x1 + (x2 - x1) * seg_s)
            sy = int(y1 + (y2 - y1) * seg_s)
            ex = int(x1 + (x2 - x1) * seg_e)
            ey = int(y1 + (y2 - y1) * seg_e)
            draw.line([(sx, sy), (ex, ey)], fill=(r, g, b, 160), width=2)
            d += dash_on + dash_off

    # Small origin dot
    dot_r = 5
    draw.ellipse(
        [x1 - dot_r, y1 - dot_r, x1 + dot_r, y1 + dot_r],
        fill=(r, g, b, 140),
    )

    # Cursor at tip
    _draw_cursor(draw, tip_x, tip_y, size=24, alpha=220)

    return np.array(img)


def _build_animated_ripple(frames: list[np.ndarray], duration: float, fps: int) -> CompositeVideoClip:
    """Build an animated ripple from pre-rendered RGBA frames."""
    clips = []
    frame_dur = 1.0 / fps
    for i, frame in enumerate(frames):
        # Split RGBA into RGB + mask
        rgb = frame[:, :, :3]
        alpha = frame[:, :, 3].astype(float) / 255.0
        clip = (
            ImageClip(rgb, duration=frame_dur)
            .set_start(i * frame_dur)
            .set_mask(ImageClip(alpha, ismask=True, duration=frame_dur))
        )
        clips.append(clip)

    return CompositeVideoClip(clips, use_bgclip=False).set_duration(duration)


def _has_nearby_action(action_log: list[dict], start_s: float, end_s: float, margin_s: float = 1.0) -> bool:
    """Return True if any action falls within [start_s - margin, end_s + margin]."""
    for action in action_log:
        t = action.get("timestamp_ms", 0) / 1000.0
        if (start_s - margin_s) <= t <= (end_s + margin_s):
            return True
    return False


def _detect_transitions(video_clip: VideoFileClip) -> list[float]:
    """Detect visual transitions (screen changes from clicks/navigation) in the video.

    Samples frames and returns timestamps (seconds) where consecutive frames
    differ significantly — indicating a user interaction changed the screen.
    """
    duration = video_clip.duration
    sample_fps = 8  # 8 fps sampling for transition detection
    n_samples = int(duration * sample_fps)

    if n_samples < 2:
        return []

    transitions: list[float] = []
    prev_frame = None
    last_t = -TRANSITION_MIN_GAP

    for i in range(n_samples):
        t = min(i / sample_fps, duration - 0.01)
        frame = video_clip.get_frame(t)

        if prev_frame is not None:
            diff = np.mean(np.abs(frame.astype(float) - prev_frame.astype(float))) / 255.0
            if diff > TRANSITION_DIFF_THRESHOLD and (t - last_t) >= TRANSITION_MIN_GAP:
                # Stability check: verify the change persists (not a transient overlay/spinner)
                check_t = min(t + TRANSITION_STABILITY_DELAY, duration - 0.01)
                if check_t > t + 0.05:
                    stable_frame = video_clip.get_frame(check_t)
                    revert_diff = np.mean(np.abs(stable_frame.astype(float) - prev_frame.astype(float))) / 255.0
                    if revert_diff < TRANSITION_DIFF_THRESHOLD:
                        # Change reverted — transient animation, skip
                        prev_frame = frame
                        continue
                transitions.append(t)
                last_t = t

        prev_frame = frame

    logger.info("Detected %d visual transitions in %.1fs video", len(transitions), duration)
    return transitions


def _deduplicate_frames(
    video: VideoFileClip, action_log: list[dict] | None = None,
) -> tuple[VideoFileClip, list[tuple[float, float]]]:
    """Remove long static segments from the video.

    Compares frames sampled at FRAME_SAMPLE_FPS. If consecutive frames are
    identical for longer than STATIC_THRESHOLD, trims the static segment
    down to STATIC_KEEP seconds. Skips trimming if user actions occur nearby
    (intentional viewing pauses).

    Returns (deduped_clip, keep_segments) where keep_segments maps original
    timeline ranges that were kept.
    """
    duration = video.duration
    sample_interval = 1.0 / FRAME_SAMPLE_FPS
    n_samples = int(duration * FRAME_SAMPLE_FPS)

    if n_samples < 2:
        return video, [(0.0, duration)]

    # Sample frames and find static segments
    prev_frame = None
    static_start = None
    keep_segments: list[tuple[float, float]] = []  # (start, end) pairs
    last_end = 0.0

    for i in range(n_samples):
        t = i * sample_interval
        frame = video.get_frame(min(t, duration - 0.01))

        if prev_frame is not None:
            # Compare frames: fraction of pixels that differ
            diff = np.mean(np.abs(frame.astype(float) - prev_frame.astype(float))) / 255.0
            is_static = diff < FRAME_DIFF_THRESHOLD

            if is_static and static_start is None:
                static_start = t - sample_interval
            elif not is_static and static_start is not None:
                static_end = t
                static_dur = static_end - static_start
                if static_dur > STATIC_THRESHOLD:
                    # Skip trim if user actions occur near this static segment
                    if action_log and _has_nearby_action(action_log, static_start, static_end):
                        pass  # intentional viewing pause — keep it
                    else:
                        keep_segments.append((last_end, static_start + STATIC_KEEP))
                        last_end = static_end
                static_start = None

        prev_frame = frame

    # Handle trailing static
    if static_start is not None:
        static_dur = duration - static_start
        if static_dur > STATIC_THRESHOLD and not (action_log and _has_nearby_action(action_log, static_start, duration)):
            keep_segments.append((last_end, static_start + STATIC_KEEP))
            last_end = duration
        else:
            keep_segments.append((last_end, duration))
    else:
        keep_segments.append((last_end, duration))

    if not keep_segments:
        return video, [(0.0, duration)]

    # If no trimming needed, return original
    total_kept = sum(end - start for start, end in keep_segments)
    if abs(total_kept - duration) < 0.1:
        return video, keep_segments

    logger.info(
        "Frame dedup: %.1fs → %.1fs (removed %.1fs of static)",
        duration, total_kept, duration - total_kept,
    )

    # Build subclips
    subclips = []
    for start, end in keep_segments:
        if end > start + 0.05:  # minimum clip length
            subclips.append(video.subclip(start, min(end, duration)))

    if not subclips:
        return video, [(0.0, duration)]

    return concatenate_videoclips(subclips, method="compose"), keep_segments


def _pre_scan_video(video_path: str) -> dict:
    """Quick pre-scan: detect transitions and estimate deduped duration.

    Runs BEFORE Phase 1 so Claude can anchor narration to real visual changes.
    """
    video = VideoFileClip(video_path)
    original_duration = video.duration
    transitions = _detect_transitions(video)

    # Run dedup to get keep_segments and deduped duration
    deduped, keep_segments = _deduplicate_frames(video)
    deduped_duration = deduped.duration

    # Close clips (deduped may be the same object as video if no trimming)
    if deduped is not video:
        deduped.close()
    video.close()

    logger.info(
        "Pre-scan: %d transitions, deduped %.1fs → %.1fs",
        len(transitions), original_duration, deduped_duration,
    )
    return {
        "transitions": transitions,
        "keep_segments": keep_segments,
        "deduped_duration_s": deduped_duration,
        "original_duration_s": original_duration,
    }


def _build_time_remap(
    keep_segments: list[tuple[float, float]],
) -> Callable[[float], float]:
    """Build a function that maps original-timeline timestamps to deduped-timeline timestamps.

    Uses keep_segments (original timeline ranges that were preserved) to compute
    where each original timestamp lands in the concatenated output.
    """
    if not keep_segments:
        return lambda t: t

    # Pre-compute cumulative start offsets in the deduped timeline
    cumulative: list[float] = []
    running = 0.0
    for start, end in keep_segments:
        cumulative.append(running)
        running += (end - start)
    total = running

    def remap(orig_t: float) -> float:
        if orig_t <= keep_segments[0][0]:
            return 0.0
        if orig_t >= keep_segments[-1][1]:
            return total
        for i, (s, e) in enumerate(keep_segments):
            if orig_t <= e:
                if orig_t >= s:
                    return cumulative[i] + (orig_t - s)
                else:
                    return cumulative[i]
        return total

    return remap


def _get_font(size: int = 28) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a font for subtitle rendering, trying common system paths."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _make_subtitle_clip(
    text: str,
    duration: float,
    video_width: int,
    video_height: int,
) -> ImageClip:
    """Create a subtitle overlay with semi-transparent background using Pillow."""
    font = _get_font(28)
    max_text_width = video_width - 100
    padding = 10

    # Word-wrap text to fit within max_text_width
    words = text.split()
    lines: list[str] = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = font.getbbox(test_line)
        if bbox[2] - bbox[0] > max_text_width and current_line:
            lines.append(current_line)
            current_line = word
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)

    # Calculate text block height
    line_height = font.getbbox("Ay")[3] - font.getbbox("Ay")[1] + 4
    text_height = line_height * len(lines)
    bar_height = text_height + padding * 2

    # Render RGBA image: semi-transparent black bar + white text
    img = Image.new("RGBA", (video_width, bar_height), (0, 0, 0, 180))
    draw = ImageDraw.Draw(img)
    y = padding
    for line in lines:
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x = (video_width - line_w) // 2
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
        y += line_height

    arr = np.array(img)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3].astype(float) / 255.0

    clip = ImageClip(rgb, duration=duration)
    mask = ImageClip(alpha, ismask=True, duration=duration)
    clip = clip.set_mask(mask)
    clip = clip.set_position(("center", video_height - bar_height - 20))
    return clip


def _clean_action_description(description: str, action_type: str) -> str:
    """Clean raw action descriptions into user-friendly subtitle text."""
    desc = description.strip()
    if action_type == "click":
        # "Clicked text 'Suppliers'" → "Click on Suppliers"
        m = re.match(r"Clicked\s+(?:text|button|link|element)\s+'(.+?)'", desc, re.IGNORECASE)
        if m:
            return f"Click on {m.group(1)}"
        m = re.match(r"Clicked\s+'(.+?)'", desc, re.IGNORECASE)
        if m:
            return f"Click on {m.group(1)}"
        m = re.match(r"Clicked\s+(.+)", desc, re.IGNORECASE)
        if m:
            return f"Click on {m.group(1)}"
        return "Click"
    elif action_type == "type":
        # "Typed 'hello' into input" → "Type 'hello'"
        m = re.match(r"Typed\s+'(.+?)'", desc, re.IGNORECASE)
        if m:
            return f"Type '{m.group(1)}'"
        return desc
    elif action_type == "key_press":
        # "Pressed Enter" → "Press Enter"
        m = re.match(r"Pressed\s+(.+)", desc, re.IGNORECASE)
        if m:
            return f"Press {m.group(1)}"
        return desc
    return desc


def _build_action_subtitles(action_log: list[dict], video_duration: float) -> list[dict]:
    """Build subtitle segments from action_log entries.

    Returns list of {start_s, duration_s, text} dicts.
    Skips scroll actions. Each subtitle lasts until the next action or
    ACTION_SUBTITLE_MAX_DURATION, whichever is shorter.
    """
    # Filter to displayable actions
    displayable = []
    for action in action_log:
        atype = action.get("action_type", "")
        if atype in ("click", "type", "key_press"):
            desc = action.get("description", "")
            if desc:
                displayable.append({
                    "time_s": action.get("timestamp_ms", 0) / 1000.0,
                    "text": _clean_action_description(desc, atype),
                })

    if not displayable:
        return []

    subtitles = []
    for i, entry in enumerate(displayable):
        start = entry["time_s"]
        if start >= video_duration:
            continue
        # Duration: until next action (minus gap) or max duration
        if i + 1 < len(displayable):
            next_start = displayable[i + 1]["time_s"]
            dur = min(next_start - start - ACTION_SUBTITLE_MIN_GAP, ACTION_SUBTITLE_MAX_DURATION)
        else:
            dur = ACTION_SUBTITLE_MAX_DURATION
        dur = max(0.3, min(dur, video_duration - start))
        subtitles.append({"start_s": start, "duration_s": dur, "text": entry["text"]})

    return subtitles


def _process_video(
    video_path: str,
    action_log: list[dict],
    narration_segments: list[dict],
    output_path: str,
    pre_scan: dict | None = None,
) -> dict[str, Any]:
    """Phase 3: Process video with dedup, click animations, subtitles, and audio.

    Args:
        pre_scan: Optional pre-scan data from _pre_scan_video() containing
            transitions, keep_segments, and deduped_duration_s.
    """
    original_action_log = bool(action_log)
    video = VideoFileClip(video_path)
    original_duration = video.duration
    w, h = video.size

    # Step 1: Frame deduplication (action-aware)
    video, keep_segments = _deduplicate_frames(video, action_log)
    deduped_duration = video.duration
    remap = _build_time_remap(keep_segments)

    # Remap narration segment timestamps from original timeline to deduped timeline
    for seg in narration_segments:
        orig_ms = seg["start_ms"]
        seg["start_ms"] = int(remap(orig_ms / 1000.0) * 1000)

    # Synthesize action entries from pre-scanned transitions when no action log
    if not action_log:
        # Use pre-scanned transitions (already on original timeline), remap to deduped
        if pre_scan and pre_scan.get("transitions"):
            transitions = [remap(t) for t in pre_scan["transitions"]]
        else:
            # Fallback: detect on the deduped video (already in deduped timeline)
            transitions = _detect_transitions(video)

        # Collect narration segments that have click position estimates
        click_segments = [
            seg for seg in narration_segments
            if seg.get("click_x_pct") is not None
            and seg.get("click_y_pct") is not None
            and isinstance(seg.get("click_x_pct"), (int, float))
            and isinstance(seg.get("click_y_pct"), (int, float))
        ]

        # Pair each detected transition with a click position estimate (in order)
        synthetic = []
        for i, t_s in enumerate(transitions):
            if i >= len(click_segments):
                break  # no more position estimates
            seg = click_segments[i]
            # Place click animation slightly before the transition (click precedes screen change)
            click_t = max(0, t_s - CLICK_LEAD_TIME)
            synthetic.append({
                "action_type": "click",
                "timestamp_ms": int(click_t * 1000),
                "x": max(0, min(seg["click_x_pct"] / 100.0 * w, w - 1)),
                "y": max(0, min(seg["click_y_pct"] / 100.0 * h, h - 1)),
                "description": seg.get("action_context", seg.get("text", "")),
            })
        if synthetic:
            action_log = synthetic
            logger.info(
                "Synthesized %d click actions from %d transitions + %d position estimates",
                len(synthetic), len(transitions), len(click_segments),
            )

    # Step 2: Build overlay clips (click ripples + subtitles)
    overlays = []

    # Click path + ripple animations
    # Sort clicks by time; draw a guiding line between consecutive clicks
    anim_fps = 30
    n_ripple_frames = int(CURSOR_DURATION * anim_fps)
    click_actions = sorted(
        [a for a in action_log if a.get("action_type") == "click" and a.get("x") is not None],
        key=lambda a: a["timestamp_ms"],
    )
    for i, action in enumerate(click_actions):
        x, y = int(action["x"]), int(action["y"])
        t = action["timestamp_ms"] / 1000.0
        if t >= deduped_duration:
            continue

        # --- Path line from previous click to this one ---
        if i > 0:
            prev = click_actions[i - 1]
            px, py = int(prev["x"]), int(prev["y"])
            prev_t = prev["timestamp_ms"] / 1000.0
            gap = t - prev_t
            # Adaptive duration: use up to PATH_DRAW_DURATION but leave room for prev ripple
            path_dur = min(PATH_DRAW_DURATION, gap * 0.5)
            path_start = t - path_dur
            # Don't overlap with previous ripple
            earliest = prev_t + CURSOR_DURATION
            if path_start < earliest:
                path_start = earliest
                path_dur = t - path_start
            if path_dur > 0.08:
                n_path_frames = max(2, int(path_dur * anim_fps))
                path_frames = []
                for fi in range(n_path_frames):
                    p = fi / max(1, n_path_frames - 1)
                    path_frames.append(_create_path_frame(w, h, px, py, x, y, p))
                path_clip = _build_animated_ripple(path_frames, path_dur, anim_fps).set_start(path_start)
                overlays.append(path_clip)

        # --- Ripple + cursor at click point ---
        ripple_frames = []
        for fi in range(n_ripple_frames):
            progress = fi / max(1, n_ripple_frames - 1)
            if progress < 0.2:
                cursor_alpha = int(230 * (progress / 0.2))
                ripple_r = None
            elif progress < 0.7:
                cursor_alpha = 230
                ripple_progress = (progress - 0.2) / 0.5
                ripple_r = max(1, int(ripple_progress * RIPPLE_MAX_RADIUS))
            else:
                fade = 1.0 - (progress - 0.7) / 0.3
                cursor_alpha = int(230 * fade)
                ripple_r = max(1, int(RIPPLE_MAX_RADIUS * fade))
            ripple_frames.append(_create_cursor_frame(w, h, x, y, cursor_alpha, ripple_r))
        anim_clip = _build_animated_ripple(ripple_frames, CURSOR_DURATION, anim_fps).set_start(t)
        overlays.append(anim_clip)

    # Subtitle overlays: prefer action-derived subtitles, fall back to narration
    action_subs = _build_action_subtitles(action_log, deduped_duration) if action_log else []
    if action_subs:
        for asub in action_subs:
            start_s = asub["start_s"]
            dur = asub["duration_s"]
            if start_s < deduped_duration and dur > 0.1:
                sub = _make_subtitle_clip(asub["text"], dur, w, h).set_start(start_s)
                overlays.append(sub)
    else:
        for seg in narration_segments:
            text = seg.get("text", "")
            if not text:
                continue
            start_s = seg["start_ms"] / 1000.0
            dur = seg.get("duration_ms", 3000) / 1000.0
            if start_s < deduped_duration:
                dur = min(dur, deduped_duration - start_s)
                if dur > 0.1:
                    sub = _make_subtitle_clip(text, dur, w, h).set_start(start_s)
                    overlays.append(sub)

    # Compose video with overlays
    if overlays:
        final_video = CompositeVideoClip([video] + overlays, size=(w, h))
    else:
        final_video = video

    # Step 3: Audio — merge narration audio tracks
    audio_clips = []

    # Keep original audio at low volume if present
    if video.audio is not None:
        audio_clips.append(video.audio.volumex(0.15))

    # Add narration audio
    for seg in narration_segments:
        audio_path = seg.get("audio_path")
        if not audio_path or not os.path.exists(audio_path):
            continue
        start_s = seg["start_ms"] / 1000.0
        if start_s < deduped_duration:
            try:
                narr_clip = AudioFileClip(audio_path).set_start(start_s)
                audio_clips.append(narr_clip)
            except Exception as e:
                logger.warning("Failed to load narration audio %s: %s", audio_path, e)

    if audio_clips:
        final_audio = CompositeAudioClip(audio_clips)
        final_video = final_video.set_audio(final_audio)

    # Step 4: Export
    final_video = final_video.set_duration(deduped_duration)
    final_video.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        fps=30,
        preset="medium",
        bitrate="2500k",
        logger=None,  # suppress moviepy progress bar
    )

    # Cleanup
    video.close()
    if final_video != video:
        try:
            final_video.close()
        except Exception:
            pass

    stats = {
        "original_duration_s": round(original_duration, 1),
        "deduped_duration_s": round(deduped_duration, 1),
        "frames_removed_s": round(original_duration - deduped_duration, 1),
        "click_animations": sum(
            1 for a in action_log
            if a.get("action_type") == "click" and a.get("x") is not None
            and a["timestamp_ms"] / 1000.0 < deduped_duration
        ),
        "subtitle_segments": len([s for s in narration_segments if s.get("text")]),
        "narration_segments": len(narration_segments),
        "click_source": "action_log" if original_action_log else "vision_estimate",
    }
    logger.info("Phase 3: Video processed — %s", stats)
    return stats


# ─── Top-level entry point ──────────────────────────────────────

async def generate_demo_video(
    video_path: str,
    action_log: list[dict],
    screenshot_paths: list[str] | None = None,
    feature_context: str = "",
    output_dir: str = "outputs/demo_videos",
) -> dict[str, Any]:
    """Generate a polished demo video from raw browser recording.

    Three-phase pipeline:
      1. Narration script generation (Claude API)
      2. TTS audio generation (edge-tts)
      3. Video processing (moviepy: dedup + ripples + subtitles + audio)

    Args:
        video_path: Path to raw .webm or .mov recording.
        action_log: List of action journal entries from browser tools.
        screenshot_paths: Optional screenshot PNGs for narration context.
        feature_context: Optional feature description for narration.
        output_dir: Output directory for the demo video.

    Returns:
        Dict with output_video_path, processing_stats, narration_segments, usage.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    os.makedirs(output_dir, exist_ok=True)

    # Output filename based on input
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_demo.mp4")

    # Pre-scan: detect transitions and estimate deduped duration BEFORE narration
    # This gives Claude real visual-change timestamps to anchor narration to.
    pre_scan = None
    if not action_log:
        logger.info("Pre-scan: Analyzing video for transitions and dedup estimate")
        pre_scan = _pre_scan_video(video_path)

    # Get video duration for narration constraints
    video_duration_s = pre_scan["original_duration_s"] if pre_scan else None
    if video_duration_s is None:
        probe_clip = VideoFileClip(video_path)
        video_duration_s = probe_clip.duration
        probe_clip.close()

    # Work directory for temporary TTS files
    work_dir = tempfile.mkdtemp(prefix="demo_video_")

    try:
        # Phase 1: Generate narration script
        logger.info("Phase 1: Generating narration script")
        narration_segments, usage = _generate_narration_script(
            action_log,
            screenshot_paths,
            feature_context,
            video_duration_s,
            transitions=pre_scan["transitions"] if pre_scan else None,
            deduped_duration_s=pre_scan["deduped_duration_s"] if pre_scan else None,
        )

        # Phase 2: Generate TTS audio
        logger.info("Phase 2: Generating TTS audio")
        enriched_segments = await _generate_tts(narration_segments, work_dir)

        # Phase 3: Process video
        logger.info("Phase 3: Processing video")
        stats = _process_video(
            video_path, action_log, enriched_segments, output_path,
            pre_scan=pre_scan,
        )

    finally:
        # Cleanup temp files
        import shutil
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass

    return {
        "output_video_path": output_path,
        "processing_stats": stats,
        "narration_segments": [
            {"start_ms": s["start_ms"], "text": s["text"], "duration_ms": s.get("duration_ms", 0)}
            for s in enriched_segments
        ],
        "usage": usage,
    }
